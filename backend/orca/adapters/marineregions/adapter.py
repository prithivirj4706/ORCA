"""MarineRegions source adapter (S-08).

Answers containment and proximity questions against a versioned local boundary
snapshot. Nothing above this layer knows that WFS, GeoServer or a .npz file
exist; nothing below the tool layer decides what a containment result MEANS.

Two rules from 04_ORCA_TOOL_CONTRACTS.md section 3.11 are enforced here rather
than described:

  * a boundary type with no configured authoritative source yields
    DATASET_UNAVAILABLE for that type -- it is never approximated from another
    type, because an EEZ polygon is not a fishing regulation zone;
  * overlapping claims are returned as multiple features. ORCA does not
    adjudicate between them.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from ...geospatial.methods import derivation
from ...schemas.core import (
    BBox, Provenance, QualityMetadata, SpatialRef, TemporalRef, utcnow,
)
from ...schemas.data import DerivedResult, VectorFeature
from ...schemas.enums import (
    Freshness, QualityFlag, Representation, Representativeness, ValueKind,
)
from ...schemas.errors import ErrorCode
from .bindings import BoundaryPolicy, load_policy
from .client import (
    ACCESS_METHOD, ATTRIBUTION, ORGANISATION, SOURCE_ID, SOURCE_NAME,
)
from .store import (
    BoundaryFeature, BoundarySnapshot, LayerSnapshot, SnapshotError,
    find_snapshot, load_snapshot,
)

log = logging.getLogger("orca.adapters.marineregions")

ROOT = pathlib.Path(__file__).resolve().parents[4]

#: How far to look for the nearest boundary of a type the point is NOT inside.
#: Beyond this the answer is "no boundary of this type within N km", which is
#: more useful than an unbounded search over a national coastline.
DEFAULT_SEARCH_KM = 250.0

#: The disclaimer that must accompany every boundary result.
DISCLAIMER_ID = "disc.boundary_advisory_only"
ADVISORY_NOTE = ("advisory context only -- not a legal determination and not "
                 "navigational authority")


class MarineRegionsError(Exception):
    def __init__(self, code: ErrorCode, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class BoundaryTestResult:
    features: list[VectorFeature] = field(default_factory=list)
    derived: list[DerivedResult] = field(default_factory=list)
    provenance: list[Provenance] = field(default_factory=list)
    codes: list[ErrorCode] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: (boundary_type, reason) for every type with no configured source.
    unavailable: list[tuple[str, str]] = field(default_factory=list)
    snapshot_version: str | None = None
    evaluated: list[str] = field(default_factory=list)


def _pid(*parts: str) -> str:
    return "pv-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:10]


class MarineRegionsAdapter:
    """Reads a versioned boundary snapshot. Never touches the network."""

    def __init__(self, snapshot: BoundarySnapshot | None = None,
                 policy: BoundaryPolicy | None = None,
                 snapshot_dir: str | pathlib.Path | None = None,
                 snapshot_version: str | None = None,
                 search_km: float = DEFAULT_SEARCH_KM):
        self.policy = policy or load_policy()
        self.search_km = search_km
        if snapshot is not None:
            self.snapshot: BoundarySnapshot | None = snapshot
            self.load_error: str | None = None
        else:
            root = pathlib.Path(snapshot_dir or (ROOT / self.policy.snapshot_dir))
            # 19_ENVIRONMENT_AND_CONFIGURATION_SPEC.md: the snapshot version is
            # pinned by environment so a deployment cannot drift onto newer
            # geometry without someone choosing to.
            pin = snapshot_version or os.getenv(
                "ORCA_MARINEREGIONS_SNAPSHOT_VERSION") or None
            path = find_snapshot(root, pin)
            if path is None and pin:
                self.snapshot = None
                self.load_error = (
                    f"ORCA_MARINEREGIONS_SNAPSHOT_VERSION pins snapshot {pin!r}, "
                    f"which is not present under {root}")
                return
            if path is None:
                self.snapshot = None
                self.load_error = (
                    f"no boundary snapshot under {root}; run "
                    f"`python -m scripts.capture_boundaries` to create one")
            else:
                try:
                    self.snapshot = load_snapshot(path)
                    self.load_error = None
                except SnapshotError as exc:
                    self.snapshot, self.load_error = None, str(exc)

    def close(self) -> None:            # symmetry with the network adapters
        return None

    def __enter__(self) -> "MarineRegionsAdapter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- public API -----------------------------------------------------------

    def test_point(self, lat: float, lon: float,
                   boundary_types: Sequence[str] | None = None
                   ) -> BoundaryTestResult:
        """Evaluate every requested boundary type at a point."""
        if self.snapshot is None:
            raise MarineRegionsError(ErrorCode.DATASET_UNAVAILABLE,
                                     self.load_error or "no snapshot loaded")
        snap = self.snapshot
        requested = list(boundary_types or self.policy.types)
        result = BoundaryTestResult(snapshot_version=snap.snapshot_version)

        unknown = [t for t in requested if t not in self.policy.types]
        for t in unknown:
            result.unavailable.append(
                (t, f"{t!r} is not a boundary type ORCA has a policy for"))

        if not snap.covers(lat, lon):
            r = snap.region
            raise MarineRegionsError(
                ErrorCode.INSUFFICIENT_COVERAGE,
                f"{lat:.3f}N {lon:.3f}E is outside the boundary snapshot region "
                f"(lat {r['min_lat']}..{r['max_lat']}, "
                f"lon {r['min_lon']}..{r['max_lon']}); no containment result is "
                f"issued, because 'not inside any boundary' would be false here")

        layers: dict[str, LayerSnapshot] = {}
        for name in requested:
            spec = self.policy.types.get(name)
            if spec is None:
                continue
            if not spec.configured:
                result.unavailable.append((name, spec.reason or "no source configured"))
                continue
            try:
                layer = snap.layer(spec.layer)
            except SnapshotError as exc:
                result.unavailable.append(
                    (name, f"configured layer {spec.layer!r} is not in snapshot "
                           f"{snap.snapshot_version}: {exc}"))
                continue
            self._evaluate_layer(result, name, layer, lat, lon)
            layers[name] = layer
            result.evaluated.append(name)

        if not result.evaluated:
            raise MarineRegionsError(
                ErrorCode.DATASET_UNAVAILABLE,
                "no requested boundary type has a configured source")
        self._check_jurisdiction_coverage(result, layers)
        return result

    def _check_jurisdiction_coverage(self, result: BoundaryTestResult,
                                     layers: dict[str, LayerSnapshot]) -> None:
        """Refuse "outside" for a layer that has no feature for this jurisdiction.

        MarineRegions publishes no internal-waters polygon for Sri Lanka. A
        query in Sri Lankan waters therefore falls outside every internal-waters
        feature in the snapshot -- which is a gap in the source, not a finding
        about the point. Reporting it as "not in internal waters" would be a
        false negative that under-states a restriction, so it is downgraded to
        "not evaluated for this jurisdiction".
        """
        eez = next((d for d in result.derived
                    if d.detail.get("boundary_type") == "EEZ"), None)
        if eez is None or not eez.detail.get("inside"):
            return
        isos = {f.get("iso_sov") for f in eez.detail["features"] if f.get("iso_sov")}
        if not isos:
            return
        for d in result.derived:
            name = d.detail["boundary_type"]
            if name == "EEZ" or d.detail.get("inside") or name not in layers:
                continue
            present = {f.iso_sov for f in layers[name].features if f.iso_sov}
            if isos & present:
                continue
            d.detail["jurisdiction_coverage"] = {
                "jurisdictions": sorted(isos), "present_in_layer": False,
                "layer": layers[name].layer}
            result.codes.append(ErrorCode.INSUFFICIENT_COVERAGE)
            result.notes.append(
                f"{name}: {layers[name].layer} publishes no feature for "
                f"{', '.join(sorted(isos))}, so this snapshot cannot state that "
                f"the point lies outside it; reported as not evaluated for this "
                f"jurisdiction rather than as unconstrained")

    # -- per-layer evaluation -------------------------------------------------

    def _evaluate_layer(self, result: BoundaryTestResult, boundary_type: str,
                        layer: LayerSnapshot, lat: float, lon: float) -> None:
        containing = layer.index.features_containing(lat, lon)
        feature_pids: list[str] = []
        inside_detail: list[dict] = []

        for i in containing:
            f = layer.features[i]
            km = layer.index.distance_to_boundary_km(i, lat, lon,
                                                     search_km=self.search_km)
            pid = self._emit_feature(result, layer, f, boundary_type, km)
            feature_pids.append(pid)
            inside_detail.append({
                "name": f.name, "territory": f.territory,
                "sovereign": f.sovereign, "iso_sov": f.iso_sov,
                "iso_ter": f.iso_ter,
                "mrgid": f.mrgid, "feature_id": f.feature_id,
                "distance_to_edge_km": round(km, 3) if km < float("inf") else None,
                "disputed": f.is_disputed,
                "provenance_id": pid,
            })

        nearest: dict | None = None
        if not containing:
            near = self._nearest_feature(layer, lat, lon)
            if near is not None:
                i, km = near
                f = layer.features[i]
                pid = self._emit_feature(result, layer, f, boundary_type, km,
                                         inside=False)
                feature_pids.append(pid)
                nearest = {"name": f.name, "sovereign": f.sovereign,
                           "iso_sov": f.iso_sov, "iso_ter": f.iso_ter,
                           "mrgid": f.mrgid, "distance_km": round(km, 3),
                           "provenance_id": pid}

        if len(containing) > 1:
            result.codes.append(ErrorCode.CONFLICTING_SOURCES)
            result.notes.append(
                f"{boundary_type}: the point falls inside "
                f"{len(containing)} overlapping features "
                f"({', '.join(str(d['name']) for d in inside_detail)}); "
                f"ORCA reports all claims and adjudicates none")

        self._emit_containment(result, boundary_type, layer, lat, lon,
                               bool(containing), inside_detail, nearest,
                               feature_pids)

    def _nearest_feature(self, layer: LayerSnapshot, lat: float,
                         lon: float) -> tuple[int, float] | None:
        best: tuple[int, float] | None = None
        limit = self.search_km
        for i in range(layer.index.feature_count):
            km = layer.index.distance_to_boundary_km(i, lat, lon, search_km=limit)
            if km < limit:
                best, limit = (i, km), km
        return best

    # -- canonical objects ----------------------------------------------------

    def _temporal(self, layer: LayerSnapshot) -> TemporalRef:
        """A boundary release has an effective date, not an observation time.

        The service publishes a release YEAR only, so the effective date is the
        start of that year and the imprecision is stated in provenance rather
        than hidden behind a fabricated timestamp.
        """
        return TemporalRef(
            valid_time=layer.effective_date, valid_from=layer.effective_date,
            representativeness=Representativeness.INSTANTANEOUS,
            temporal_resolution="static dataset release",
            retrieved_at=self.snapshot.captured_at if self.snapshot else utcnow())

    def _quality(self, distance_km: float | None) -> QualityMetadata:
        q = QualityMetadata(flag=QualityFlag.NOMINAL, basis="source-provided",
                            freshness=Freshness.FRESH)
        if distance_km is not None and distance_km < float("inf"):
            q.nearest_node_distance_km = round(distance_km, 3)
            q.add_check(
                "near_boundary", "pass"
                if distance_km >= self.policy.near_boundary_km else "warn",
                f"{distance_km:.2f} km from the boundary "
                f"(threshold {self.policy.near_boundary_km:g} km)")
        q.add_check("full_precision_geometry", "pass",
                    "containment tested against unsimplified source geometry")
        return q

    def _emit_feature(self, result: BoundaryTestResult, layer: LayerSnapshot,
                      f: BoundaryFeature, boundary_type: str,
                      distance_km: float, *, inside: bool = True) -> str:
        min_lon, min_lat, max_lon, max_lat = f.bbox
        bbox = None
        if min_lat < max_lat and min_lon < max_lon:
            bbox = BBox(min_lat=max(-90.0, min_lat), max_lat=min(90.0, max_lat),
                        min_lon=max(-180.0, min_lon), max_lon=min(180.0, max_lon))
        spatial = SpatialRef(kind="geometry", bbox=bbox,
                             area_description=f.name,
                             representation=Representation.VECTOR,
                             label=f.territory)
        temporal = self._temporal(layer)
        quality = self._quality(distance_km)
        pid = _pid("mr", layer.layer, str(f.mrgid or f.feature_id),
                   self.snapshot.snapshot_version if self.snapshot else "")

        result.provenance.append(Provenance(
            provenance_id=pid, parameter="maritime_boundary",
            value_kind=ValueKind.OBSERVED, unit=None,
            spatial=spatial, temporal=temporal,
            source=SOURCE_NAME, source_id=SOURCE_ID, organisation=ORGANISATION,
            dataset=layer.layer, dataset_version=layer.dataset_version,
            product_reference=layer.title, access_method=ACCESS_METHOD,
            external_source=True,
            retrieved_at=self.snapshot.captured_at if self.snapshot else utcnow(),
            request_fingerprint=layer.request_fingerprint,
            spatial_resolution="source polygon, unsimplified",
            temporal_resolution="static dataset release",
            quality=quality,
            notes=(f"{ADVISORY_NOTE}; snapshot "
                   f"{self.snapshot.snapshot_version if self.snapshot else '?'}, "
                   f"geometry {layer.geometry_sha256}; effective date recorded to "
                   f"year precision ({layer.effective_year}) because the service "
                   f"publishes only a release year"),
            licence_reference=ATTRIBUTION))

        result.features.append(VectorFeature(
            feature_id=f.feature_id, parameter="maritime_boundary",
            boundary_type=boundary_type, name=f.name,
            jurisdiction=f.sovereign or f.territory,
            attributes={"mrgid": f.mrgid, "territory": f.territory,
                        "sovereign": f.sovereign, "iso_ter": f.iso_ter,
                        "iso_sov": f.iso_sov, "pol_type": f.pol_type,
                        "area_km2": f.area_km2,
                        "contains_query_point": inside,
                        "distance_km": (round(distance_km, 3)
                                        if distance_km < float("inf") else None),
                        "disputed": f.is_disputed,
                        "layer_title": layer.title},
            geometry_ref=f"orca://geo/boundary/{layer.layer}/{f.mrgid or f.index}"
                         f"@{self.snapshot.snapshot_version if self.snapshot else '?'}",
            spatial=spatial, temporal=temporal,
            dataset_version=layer.dataset_version,
            advisory_only=True, provenance_id=pid))
        return pid

    def _emit_containment(self, result: BoundaryTestResult, boundary_type: str,
                          layer: LayerSnapshot, lat: float, lon: float,
                          inside: bool, inside_detail: list[dict],
                          nearest: dict | None, inputs: list[str]) -> None:
        distance = None
        if inside_detail:
            distance = min((d["distance_to_edge_km"] for d in inside_detail
                            if d["distance_to_edge_km"] is not None), default=None)
        elif nearest:
            distance = nearest["distance_km"]

        near_edge = (distance is not None
                     and distance < self.policy.near_boundary_km)
        if near_edge:
            result.notes.append(
                f"{boundary_type}: the point is {distance:.2f} km from the "
                f"boundary, within the {self.policy.near_boundary_km:g} km band "
                f"where source precision matters; containment is reported but "
                f"not with full confidence")

        d = derivation(
            "point_in_polygon", inputs,
            {"crs": "EPSG:4326", "algorithm": "ray_casting_even_odd",
             "boundary_type": boundary_type, "layer": layer.layer,
             "dataset_version": layer.dataset_version,
             "snapshot_version": (self.snapshot.snapshot_version
                                  if self.snapshot else None),
             "geometry_sha256": layer.geometry_sha256,
             "geometry_simplified": False,
             "point": [round(lon, 6), round(lat, 6)]},
            module="topology")
        pid = _pid("pip", boundary_type, layer.layer, f"{lat:.6f},{lon:.6f}",
                   self.snapshot.snapshot_version if self.snapshot else "")
        spatial = SpatialRef.point(lat, lon)
        temporal = self._temporal(layer)
        quality = self._quality(distance)

        result.provenance.append(Provenance(
            provenance_id=pid, parameter="point_in_boundary",
            value_kind=ValueKind.DERIVED, unit=None,
            spatial=spatial, temporal=temporal,
            source=SOURCE_NAME, source_id=SOURCE_ID, organisation=ORGANISATION,
            dataset=layer.layer, dataset_version=layer.dataset_version,
            product_reference=layer.title, access_method=ACCESS_METHOD,
            external_source=True, retrieved_at=utcnow(),
            quality=quality, derivation=d,
            notes=f"{ADVISORY_NOTE}; {boundary_type} containment",
            licence_reference=ATTRIBUTION))

        result.derived.append(DerivedResult(
            parameter="point_in_boundary", value=inside, unit=None,
            spatial=spatial, temporal=temporal, quality=quality,
            provenance_id=pid,
            detail={"boundary_type": boundary_type, "inside": inside,
                    "layer": layer.layer,
                    "dataset_version": layer.dataset_version,
                    "effective_year": layer.effective_year,
                    "snapshot_version": (self.snapshot.snapshot_version
                                         if self.snapshot else None),
                    "features": inside_detail, "nearest": nearest,
                    "distance_km": distance,
                    "near_boundary": near_edge,
                    "near_boundary_km": self.policy.near_boundary_km,
                    "search_km": self.search_km,
                    "advisory_only": True,
                    "disclaimer_id": DISCLAIMER_ID}))
