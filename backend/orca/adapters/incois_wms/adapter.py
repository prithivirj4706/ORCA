"""INCOIS PFZ adapter (S-06).

Answers "where is the nearest Potential Fishing Zone advisory?" from the
official INCOIS product.

Two rules govern everything here.

**PFZ is an authority, not an indicator.** `12_RISK_AND_RECOMMENDATION_SPEC.md`
§5.3 reserves the term for the authoritative INCOIS product. ORCA reports the
advisory's geometry, its issue date and the distance to it. It never recomputes
one, never infers one from SST and chlorophyll, and never calls its own
productivity reasoning a PFZ.

**An advisory is dated, and the layer has no time dimension.** The server serves
whatever issue is current, so the issue date is read from each feature and
checked. A yesterday advisory presented as today's would be worse than no
advisory at all, so it is returned flagged STALE_DATA with the date stated.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np

from ...geospatial.geometry import bbox_from_point_radius
from ...geospatial.methods import derivation
from ...geospatial.topology import distance_to_line_km
from ...schemas.core import (
    Provenance, QualityMetadata, SpatialRef, TemporalRef, utcnow,
)
from ...schemas.data import DerivedResult
from ...schemas.enums import Freshness, QualityFlag, Representativeness, ValueKind
from ...schemas.errors import ErrorCode
from .bindings import PFZ_LINES, PFZ_SECTORS, WmsLayer
from .client import (
    ACCESS_METHOD, ATTRIBUTION, ORGANISATION, SOURCE_ID, SOURCE_NAME,
    IncoisWmsClient, WmsError,
)

#: A PFZ advisory is issued for a day. Beyond this it is history, not advice.
MAX_ADVISORY_AGE_DAYS = 3.0

#: GetFeatureInfo's BUFFER is in PIXELS, so the search radius depends on the
#: bbox we ask for. Using a fixed pixel grid makes the conversion exact.
_GRID_PX = 101


@dataclass(slots=True)
class PfzPointResult:
    observations: list[DerivedResult]
    provenance: list[Provenance]
    codes: list[ErrorCode]
    notes: list[str]
    dataset_id: str | None = None
    #: GeoJSON of the advisory lines found, for the map layer.
    features: list[dict] = None


def advisory_date(props: dict[str, Any]) -> date | None:
    """Issue date from the feature's Year + Julian_day attributes.

    INCOIS publishes the issue as a Julian day of year, not an ISO date, so it
    has to be converted before it can be compared with anything.
    """
    try:
        year = int(float(props.get("Year")))
        doy = int(float(props.get("Julian_day")))
    except (TypeError, ValueError):
        return None
    if not (1 <= doy <= 366):
        return None
    return date(year, 1, 1) + timedelta(days=doy - 1)


class IncoisPfzAdapter:
    def __init__(self, client: IncoisWmsClient | None = None):
        self._client = client or IncoisWmsClient()
        self._owns = client is None

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def __enter__(self) -> "IncoisPfzAdapter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- retrieval ---------------------------------------------------------

    def fetch_nearest_pfz(self, lat: float, lon: float, *,
                          radius_km: float = 100.0,
                          valid_time: datetime | None = None) -> PfzPointResult:
        """Nearest PFZ advisory line within `radius_km`."""
        valid_time = valid_time or utcnow()
        layer = PFZ_LINES

        if not _covers(layer, lat, lon):
            # Outside the advertised extent, "no advisory" is indistinguishable
            # from "not looked at". Say the second (D-15).
            raise WmsError(
                ErrorCode.INSUFFICIENT_COVERAGE,
                f"{lat:.2f},{lon:.2f} is outside the current PFZ layer extent "
                f"({layer.min_lat:.1f}..{layer.max_lat:.1f} N, "
                f"{layer.min_lon:.1f}..{layer.max_lon:.1f} E)")

        bbox = bbox_from_point_radius(lat, lon, radius_km)
        span_deg = max(bbox.max_lat - bbox.min_lat, bbox.max_lon - bbox.min_lon)
        # BUFFER is a pixel count; half the grid spans the requested radius.
        buffer_px = max(1, min(_GRID_PX // 2, int(_GRID_PX / 2)))
        resp = self._client.get_feature_info(
            layer.layer,
            bbox=(lon - span_deg / 2, lat - span_deg / 2,
                  lon + span_deg / 2, lat + span_deg / 2),
            size=_GRID_PX, buffer_px=buffer_px)

        features = (resp.payload or {}).get("features") or []
        now = utcnow()

        if not features:
            # A genuine result: the current issue has nothing near this point.
            # Distinct from "we could not check", which raises instead.
            return PfzPointResult(
                [], [], [ErrorCode.NO_DATA],
                [f"no PFZ advisory within {radius_km:g} km of "
                 f"{lat:.2f},{lon:.2f} in the current issue"],
                layer.layer, features=[])

        nearest = None
        for f in features:
            d_km = _feature_distance_km(lat, lon, f.get("geometry") or {})
            if d_km is None:
                continue
            if nearest is None or d_km < nearest[0]:
                nearest = (d_km, f)
        if nearest is None:
            raise WmsError(ErrorCode.ADAPTER_ERROR,
                           "PFZ features carried no usable line geometry")

        distance_km, feature = nearest
        props = feature.get("properties") or {}
        issued = advisory_date(props)

        codes: list[ErrorCode] = []
        notes: list[str] = []
        if issued is None:
            # Without a date we cannot say the advisory is current, and an
            # undated advisory must not be presented as today's.
            codes.append(ErrorCode.STALE_DATA)
            notes.append("the advisory carries no readable issue date")
            issued_dt = valid_time
            flag = QualityFlag.SUSPECT
        else:
            issued_dt = datetime(issued.year, issued.month, issued.day,
                                 tzinfo=timezone.utc)
            age_days = (valid_time - issued_dt).total_seconds() / 86400.0
            flag = QualityFlag.NOMINAL
            if age_days > MAX_ADVISORY_AGE_DAYS:
                codes.append(ErrorCode.STALE_DATA)
                flag = QualityFlag.DEGRADED
                notes.append(f"advisory issued {issued.isoformat()}, "
                             f"{age_days:.1f} days before the requested time")
            elif age_days < -1.0:
                notes.append(f"advisory issued {issued.isoformat()}, ahead of "
                             f"the requested time")

        if distance_km > radius_km:
            codes.append(ErrorCode.INSUFFICIENT_COVERAGE)
            notes.append(f"nearest advisory is {distance_km:.1f} km away, "
                         f"beyond the {radius_km:g} km search radius")

        sector = str(props.get("SECTORNAME") or "").strip() or None
        uid = str(props.get("UID") or props.get("Sno") or "")
        quality = QualityMetadata(
            flag=flag, basis="source-provided",
            nearest_node_distance_km=round(distance_km, 2),
            freshness=Freshness.FRESH if not codes else Freshness.AGING,
            staleness_s=max(0.0, (now - issued_dt).total_seconds()))
        quality.add_check("advisory_issue_date", "pass" if issued else "fail",
                          issued.isoformat() if issued else "unreadable")
        quality.add_check("representation", "pass",
                          "vector geometry via GetFeatureInfo (WFS is 403)")

        spatial = SpatialRef.point(lat, lon)
        temporal = TemporalRef(
            valid_time=issued_dt,
            representativeness=Representativeness.BULLETIN_PERIOD,
            retrieved_at=now)
        pid = "pv-" + hashlib.sha256(
            f"{layer.layer}|{uid}|{issued_dt.isoformat()}|{lat}|{lon}".encode()
        ).hexdigest()[:10]

        # ORCA computes the DISTANCE; INCOIS owns the advisory. The distance
        # is therefore a derived value and carries a derivation record that
        # names the official geometry it was measured against (D-10).
        d = derivation("distance_to_advisory_line", [f"{layer.layer}:{uid}"],
                       {"radius_km": radius_km,
                        "advisory_uid": uid,
                        "issued": issued.isoformat() if issued else None,
                        "geometry": "MultiLineString (open polyline)"},
                       module="topology")

        prov = Provenance(
            provenance_id=pid, parameter="pfz_distance_km",
            value_kind=ValueKind.DERIVED, unit="km",
            spatial=spatial, temporal=temporal,
            source=SOURCE_NAME, source_id=SOURCE_ID, organisation=ORGANISATION,
            dataset=layer.layer, access_method=ACCESS_METHOD,
            external_source=False, retrieved_at=now,
            request_fingerprint="sha256:" + hashlib.sha256(
                resp.url.encode()).hexdigest()[:16],
            quality=quality, derivation=d,
            notes=("official INCOIS PFZ advisory"
                   + (f", sector {sector}" if sector else "")
                   + (f", issued {issued.isoformat()}" if issued else "")),
            licence_reference=ATTRIBUTION)

        obs = DerivedResult(
            parameter="pfz_distance_km", value=round(distance_km, 2), unit="km",
            spatial=spatial, temporal=temporal,
            quality=quality, provenance_id=pid,
            detail={"sector": sector, "advisory_uid": uid,
                    "issued": issued.isoformat() if issued else None,
                    "advisory_count": len(features),
                    "representation": "vector"})
        return PfzPointResult([obs], [prov], codes, notes, layer.layer,
                              features=features)

    def fetch_sector(self, lat: float, lon: float) -> str | None:
        """The named PFZ sector containing a position, for a human-readable answer."""
        if not _covers(PFZ_SECTORS, lat, lon):
            return None
        try:
            resp = self._client.get_feature_info(
                PFZ_SECTORS.layer,
                bbox=(lon - 0.35, lat - 0.35, lon + 0.35, lat + 0.35),
                size=_GRID_PX, buffer_px=20, feature_count=5)
        except WmsError:
            return None
        for f in (resp.payload or {}).get("features") or []:
            name = str((f.get("properties") or {}).get("SECTORNAME") or "").strip()
            if name:
                return name
        return None


# -- helpers ---------------------------------------------------------------

def _covers(layer: WmsLayer, lat: float, lon: float) -> bool:
    return (layer.min_lat <= lat <= layer.max_lat
            and layer.min_lon <= lon <= layer.max_lon)


def _feature_distance_km(lat: float, lon: float, geometry: dict) -> float | None:
    """Distance to the nearest line in a (Multi)LineString.

    Uses the OPEN-polyline kernel: closing a PFZ advisory line would invent a
    segment across open sea and report an advisory where there is none.
    """
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None
    if gtype == "LineString":
        lines = [coords]
    elif gtype == "MultiLineString":
        lines = coords
    else:
        return None
    best = None
    for line in lines:
        arr = np.asarray(line, dtype=float)
        if arr.ndim != 2 or arr.shape[0] < 2:
            continue
        d = distance_to_line_km(lon, lat, arr[:, :2])
        best = d if best is None else min(best, d)
    return best
