"""NOAA NCEP GFS adapter (S-11) -- the wind FORECAST source.

Why this exists (F-25): every other wind source ORCA can reach is an
observation product. CMEMS wind is L4 NRT with no forecast horizon, and the
INCOIS ASCAT archive ends in 2023, so a question about tomorrow -- the ordinary
question a fisher asks -- had no usable wind at all, and SAFETY could never
issue a verdict. GFS runs +384 h, which covers it.

The adapter reads a single grid node per request over ERDDAP griddap. It emits
COMPONENTS; speed and direction are derived by the geospatial kernel with a
recorded method (D-8), never here.
"""
from __future__ import annotations

import csv
import hashlib
import math
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np

from ...geospatial.geometry import haversine_km
from ...schemas.core import Provenance, QualityMetadata, SpatialRef, TemporalRef, utcnow
from ...schemas.data import Forecast
from ...schemas.enums import Freshness, QualityFlag, ValueKind
from ...schemas.errors import ErrorCode
from .bindings import BINDINGS, GfsBinding
from .client import (
    ACCESS_METHOD, DISTRIBUTOR, ORGANISATION, SOURCE_ID, SOURCE_NAME,
    ErddapError, GfsClient,
)

ATTRIBUTION = (f"{SOURCE_NAME} (NCEP GFS, US Government public domain), "
               f"distributed by {DISTRIBUTOR}")

#: Grid spacing published by the dataset, used only to report node distance.
GRID_DEG = 0.5

#: Plausibility bounds per canonical unit. A value outside these is not
#: discarded -- it is flagged SUSPECT and carried with the flag, because the
#: assessment engine excludes suspect values itself.
_PLAUSIBLE: dict[str, tuple[float, float]] = {
    "m s-1": (-120.0, 120.0),      # wind components; > 120 m/s is not physical
    "K": (150.0, 350.0),
    "Pa": (80_000.0, 110_000.0),
}


@dataclass(slots=True)
class GfsPointResult:
    observations: list[Forecast]
    provenance: list[Provenance]
    codes: list[ErrorCode]
    notes: list[str]
    dataset_id: str | None = None


class GfsError(ErddapError):
    """Kept distinct so a caller can tell a GFS failure from another host's."""


class NoaaGfsAdapter:
    def __init__(self, client: GfsClient | None = None):
        self._client = client or GfsClient()
        self._owns = client is None
        self._coverage: tuple[datetime, datetime] | None = None

    def close(self) -> None:
        if self._owns:
            self._client.close()

    def __enter__(self) -> "NoaaGfsAdapter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- coverage ----------------------------------------------------------

    def coverage(self) -> tuple[datetime, datetime]:
        """The dataset's own advertised time range, read from its `.das`.

        Cached per adapter instance. A forecast product's horizon MOVES: it is
        read from the server rather than assumed, so a stale constant can never
        make ORCA claim coverage it does not have.
        """
        if self._coverage is not None:
            return self._coverage
        import re

        resp = self._client.get_text(f"griddap/{_dataset()}.das")
        m = re.search(r"time \{.*?actual_range ([0-9.eE+-]+), ([0-9.eE+-]+)",
                      resp.payload, re.S)
        if not m:
            raise GfsError(ErrorCode.ADAPTER_ERROR,
                           "could not read the time range from the dataset .das")
        lo = datetime.fromtimestamp(float(m.group(1)), timezone.utc)
        hi = datetime.fromtimestamp(float(m.group(2)), timezone.utc)
        self._coverage = (lo, hi)
        return self._coverage

    # -- retrieval ---------------------------------------------------------

    def fetch_point(self, parameter: str, lat: float, lon: float,
                    valid_time: datetime) -> GfsPointResult:
        bindings = BINDINGS.get(parameter)
        if not bindings:
            raise GfsError(ErrorCode.DATASET_UNAVAILABLE,
                           f"{parameter!r} has no GFS binding")
        binding = bindings[0]

        lo, hi = self.coverage()
        if not (lo <= valid_time <= hi):
            # The horizon is a fact about the product, not a failure to try
            # harder. Saying so is the whole point (M-12).
            raise GfsError(
                ErrorCode.INSUFFICIENT_COVERAGE,
                f"{valid_time:%Y-%m-%dT%H:%MZ} lies outside the GFS run "
                f"({lo:%Y-%m-%d %H:%M}Z..{hi:%Y-%m-%d %H:%M}Z)")

        return self._fetch_one(binding, lat, lon, valid_time)

    def fetch_grid(self, parameters: list[str], lat: float, lon: float,
                   valid_time: datetime, radius_km: float = 300.0):
        """A rectangular field for one or more parameters, with its axes.

        Requested as an ERDDAP range selector rather than cell by cell, so a
        whole map is one HTTP call. Returns (lats, lons, {param: block}, time).

        The grid is published on longitude 0..360 with latitude DECREASING, so
        the selector must be emitted in axis order or the server silently
        returns an empty or transposed block.
        """
        bindings = []
        for p in parameters:
            b = BINDINGS.get(p)
            if not b:
                raise GfsError(ErrorCode.DATASET_UNAVAILABLE,
                               f"{p!r} has no GFS binding")
            bindings.append(b[0])

        lo, hi = self.coverage()
        if not (lo <= valid_time <= hi):
            raise GfsError(
                ErrorCode.INSUFFICIENT_COVERAGE,
                f"{valid_time:%Y-%m-%dT%H:%MZ} lies outside the GFS run")

        dlat = radius_km / 111.32
        dlon = radius_km / max(111.32 * math.cos(math.radians(lat)), 1e-6)
        lat_hi, lat_lo = min(90.0, lat + dlat), max(-90.0, lat - dlat)
        lon_a = (lon - dlon) % 360.0
        lon_b = (lon + dlon) % 360.0
        if lon_a > lon_b:                      # antimeridian: clamp, do not wrap
            lon_a, lon_b = 0.0, 359.5

        t = _iso(valid_time)
        # latitude is stored decreasing, so high:low
        sel = ",".join(
            f"{b.variable}[({t})][({lat_hi}):({lat_lo})][({lon_a}):({lon_b})]"
            for b in bindings)
        resp = self._client.get_text(f"griddap/{bindings[0].dataset_id}.csv", sel)
        rows = _rows(resp.payload)
        if not rows:
            raise GfsError(ErrorCode.NO_DATA, "no GFS rows for the requested area")

        lats, lons = [], []
        for r in rows:
            la = float(r["values"]["latitude"])
            ln = float(r["values"]["longitude"])
            if la not in lats:
                lats.append(la)
            if ln not in lons:
                lons.append(ln)
        lat_ix = {v: i for i, v in enumerate(lats)}
        lon_ix = {v: i for i, v in enumerate(lons)}

        blocks = {b.parameter: np.full((len(lats), len(lons)), np.nan)
                  for b in bindings}
        actual = None
        for r in rows:
            y = lat_ix[float(r["values"]["latitude"])]
            x = lon_ix[float(r["values"]["longitude"])]
            actual = actual or _parse_time(r["values"]["time"])
            for b in bindings:
                raw = r["values"].get(b.variable)
                if raw not in (None, "", "NaN"):
                    blocks[b.parameter][y][x] = float(raw)
        # report longitudes in the -180..180 frame the client draws in
        out_lons = [((v + 180.0) % 360.0) - 180.0 for v in lons]
        return lats, out_lons, blocks, (actual or valid_time)

    def _fetch_one(self, binding: GfsBinding, lat: float, lon: float,
                   valid_time: datetime) -> GfsPointResult:
        # The dataset publishes longitude on 0..360, so a western longitude must
        # be shifted or ERDDAP silently clamps to the grid edge.
        query_lon = lon % 360.0
        selector = (f"{binding.variable}"
                    f"[({_iso(valid_time)})][({lat})][({query_lon})]")
        resp = self._client.get_text(f"griddap/{binding.dataset_id}.csv", selector)
        rows = _rows(resp.payload)
        if not rows:
            raise GfsError(ErrorCode.NO_DATA,
                           f"no GFS row for {binding.variable} at {lat},{lon}")

        row = rows[0]
        published_unit = row["units"].get(binding.variable) or ""
        raw = row["values"].get(binding.variable)
        if raw in (None, "", "NaN"):
            # GFS is a global field with no land mask on these variables, so an
            # empty value means the request missed, not that the sea is calm.
            raise GfsError(ErrorCode.NO_DATA,
                           f"{binding.variable} is empty at {lat},{lon}")
        value = float(raw)

        if published_unit and published_unit != binding.canonical_unit:
            # Fail loudly rather than put an unconverted number into a
            # threshold comparison (D-7). GFS has been stable here, but the
            # check is what makes that a fact rather than an assumption.
            raise GfsError(
                ErrorCode.SCHEMA_VALIDATION_FAILED,
                f"{binding.variable} published as {published_unit!r}, expected "
                f"{binding.canonical_unit!r}; refusing to use it unconverted")

        actual_time = _parse_time(row["values"]["time"])
        node_lat = float(row["values"]["latitude"])
        node_lon = float(row["values"]["longitude"])
        node_km = haversine_km(lat, lon, node_lat, ((node_lon + 180) % 360) - 180)

        now = utcnow()
        is_forecast = actual_time > now
        lead_h = (actual_time - now).total_seconds() / 3600.0

        flag = QualityFlag.NOMINAL
        codes: list[ErrorCode] = []
        notes: list[str] = []
        lo_p, hi_p = _PLAUSIBLE.get(binding.canonical_unit, (None, None))
        if lo_p is not None and not (lo_p <= value <= hi_p):
            flag = QualityFlag.SUSPECT
            notes.append(f"{value:g} {binding.canonical_unit} outside "
                         f"{lo_p:g}..{hi_p:g}")

        offset_h = abs((actual_time - valid_time).total_seconds()) / 3600.0
        if offset_h > 1.5:
            # The grid snapped to a different step than asked for; report it.
            codes.append(ErrorCode.STALE_DATA)
            notes.append(f"nearest step is {offset_h:.1f} h from the requested time")

        quality = QualityMetadata(
            flag=flag, basis="source-provided",
            nearest_node_distance_km=round(node_km, 2),
            lead_time_h=round(lead_h, 2),
            freshness=Freshness.FRESH if abs(lead_h) < 72 else Freshness.AGING,
            staleness_s=max(0.0, (now - actual_time).total_seconds()),
        )
        quality.add_check("unit_read_from_source", "pass",
                          f"published={published_unit!r} == "
                          f"{binding.canonical_unit!r}")
        quality.add_check("plausibility_range",
                          "pass" if flag is QualityFlag.NOMINAL else "fail",
                          f"{lo_p}..{hi_p}")
        quality.add_check("nearest_node", "pass", f"{node_km:.2f} km from request")

        spatial = SpatialRef.point(node_lat, ((node_lon + 180) % 360) - 180)
        temporal = TemporalRef(
            valid_time=actual_time,
            representativeness=binding.representativeness,
            temporal_resolution="PT1H",
            lead_time_h=round(lead_h, 2) if is_forecast else None,
            retrieved_at=now,
        )
        pid = "pv-" + hashlib.sha256(
            f"{binding.dataset_id}|{binding.variable}|{actual_time.isoformat()}"
            f"|{node_lat}|{node_lon}".encode()).hexdigest()[:10]

        prov = Provenance(
            provenance_id=pid, parameter=binding.parameter,
            value_kind=ValueKind.FORECAST if is_forecast else ValueKind.MODEL,
            unit=binding.canonical_unit, spatial=spatial, temporal=temporal,
            source=SOURCE_NAME, source_id=SOURCE_ID, organisation=ORGANISATION,
            dataset=binding.dataset_id, access_method=ACCESS_METHOD,
            external_source=True, retrieved_at=now,
            request_fingerprint="sha256:" + hashlib.sha256(
                resp.url.encode()).hexdigest()[:16],
            spatial_resolution=f"{GRID_DEG:g} deg",
            temporal_resolution="PT1H",
            quality=quality,
            notes=(f"{binding.note}; distributed by {DISTRIBUTOR}"
                   if binding.note else f"distributed by {DISTRIBUTOR}"),
            licence_reference=ATTRIBUTION,
        )
        obs = Forecast(
            parameter=binding.parameter, value=round(value, 4),
            unit=binding.canonical_unit,
            value_kind=ValueKind.FORECAST if is_forecast else ValueKind.MODEL,
            spatial=spatial, temporal=temporal, quality=quality,
            provenance_id=pid)
        return GfsPointResult([obs], [prov], codes, notes, binding.dataset_id)


# -- parsing ---------------------------------------------------------------

def _dataset() -> str:
    from .bindings import GFS_DATASET
    return GFS_DATASET


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _rows(text: str) -> list[dict[str, Any]]:
    """ERDDAP .csv is: header row, UNITS row, then data rows."""
    reader = list(csv.reader(io.StringIO(text)))
    if len(reader) < 3:
        return []
    header, units = reader[0], reader[1]
    unit_map = dict(zip(header, units))
    return [{"values": dict(zip(header, r)), "units": unit_map}
            for r in reader[2:] if any(c.strip() for c in r)]
