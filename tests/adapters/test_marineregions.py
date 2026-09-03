"""MarineRegions adapter, against RECORDED upstream geometry.

The fixtures in tests/fixtures/upstream/marineregions/ are real WFS responses
(see CAPTURE.md). Everything here runs offline: the adapter reads a snapshot,
never the network.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import pytest

from backend.orca.adapters.marineregions.adapter import (
    MarineRegionsAdapter, MarineRegionsError,
)
from backend.orca.adapters.marineregions.client import parse_layer_version
from backend.orca.adapters.marineregions.store import (
    SNAPSHOT_FORMAT, SnapshotError, find_snapshot, load_snapshot, write_layer,
    write_manifest,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "upstream" \
    / "marineregions"

#: Positions verified against the recorded geometry, deepest-inside first.
INSIDE_PAKISTAN_12NM = (25.016, 64.076)
INSIDE_BANGLADESH_24NM = (21.168, 91.706)
OUTSIDE_NEARBY = (22.5, 67.0)          # ~133 km south of the Pakistani 12 NM
OUTSIDE_BEYOND_SEARCH = (18.0, 70.0)   # ~642 km away, past the search cap
OUTSIDE_THE_REGION = (5.0, 55.0)


def _collection(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def snapshot_dir(tmp_path_factory) -> pathlib.Path:
    """Build a snapshot from the recorded responses, via the real writer."""
    root = tmp_path_factory.mktemp("boundaries") / "2026-09-02"
    layers = [
        write_layer(root, "MarineRegions:eez_12nm", "territorial_sea",
                    _collection("wfs_eez_12nm_pakistan"),
                    title="Territorial Seas (12 NM) (v4, world, 2023)",
                    dataset_version="v4", effective_year="2023",
                    abstract="Version 4 of the Territorial Seas.",
                    request_url="https://geo.vliz.be/geoserver/MarineRegions/wfs?x=1"),
        write_layer(root, "MarineRegions:eez_24nm", "contiguous_zone",
                    _collection("wfs_eez_24nm_bangladesh"),
                    title="Contiguous Zones (24 NM) (v4, world, 2023)",
                    dataset_version="v4", effective_year="2023",
                    abstract="Version 4 of the Contiguous Zones.",
                    request_url="https://geo.vliz.be/geoserver/MarineRegions/wfs?x=2"),
    ]
    write_manifest(root, {
        "format": SNAPSHOT_FORMAT, "snapshot_version": "2026-09-02",
        "captured_at": datetime(2026, 9, 2, tzinfo=timezone.utc).isoformat(),
        "region": {"min_lat": 15.0, "min_lon": 60.0, "max_lat": 26.0,
                   "max_lon": 95.0},
        "source": {"source_id": "S-08", "name": "MarineRegions"},
        "layers": layers})
    return root.parent


@pytest.fixture
def adapter(snapshot_dir):
    return MarineRegionsAdapter(snapshot_dir=snapshot_dir)


class TestLayerVersion:
    def test_version_and_year_come_from_the_published_title(self):
        caps = (FIXTURES / "capabilities_featuretypes.xml").read_text()
        assert "Exclusive Economic Zones (200 NM) (v12, world, 2023)" in caps
        assert parse_layer_version(
            "Exclusive Economic Zones (200 NM) (v12, world, 2023)") == ("v12", "2023")

    def test_an_unparseable_title_yields_no_version(self):
        """The capture refuses to write a snapshot it cannot version."""
        assert parse_layer_version("Exclusive Economic Zones") == (None, None)


class TestSnapshot:
    def test_round_trip_preserves_source_attributes(self, snapshot_dir):
        snap = load_snapshot(find_snapshot(snapshot_dir))
        layer = snap.layer("MarineRegions:eez_12nm")
        assert layer.dataset_version == "v4"
        assert layer.effective_date.year == 2023
        assert [f.name for f in layer.features] == ["Pakistani 12 NM"]
        assert layer.features[0].iso_sov == "PAK"
        assert layer.features[0].mrgid == 49082
        assert layer.vertex_count == 874
        assert layer.geometry_sha256.startswith("sha256:")

    def test_missing_geometry_file_is_an_error_not_an_empty_answer(self, snapshot_dir):
        snap = load_snapshot(find_snapshot(snapshot_dir))
        (snap.root / "eez_24nm.npz").rename(snap.root / "eez_24nm.hidden")
        try:
            with pytest.raises(SnapshotError, match="missing"):
                snap.layer("MarineRegions:eez_24nm")
        finally:
            (snap.root / "eez_24nm.hidden").rename(snap.root / "eez_24nm.npz")


class TestContainment:
    def test_inside_real_geometry(self, adapter):
        res = adapter.test_point(*INSIDE_PAKISTAN_12NM,
                                 ["territorial_sea", "contiguous_zone"])
        by_type = {d.detail["boundary_type"]: d for d in res.derived}
        assert by_type["territorial_sea"].value is True
        assert by_type["contiguous_zone"].value is False
        feature = by_type["territorial_sea"].detail["features"][0]
        assert feature["name"] == "Pakistani 12 NM"
        assert feature["iso_sov"] == "PAK"

    def test_outside_reports_the_nearest_feature_and_its_distance(self, adapter):
        res = adapter.test_point(*OUTSIDE_NEARBY, ["territorial_sea"])
        detail = res.derived[0].detail
        assert detail["inside"] is False
        assert detail["nearest"]["name"] == "Pakistani 12 NM"
        assert detail["nearest"]["distance_km"] == pytest.approx(133.4, abs=1.0)

    def test_beyond_the_search_cap_no_nearest_feature_is_claimed(self, adapter):
        """"No boundary within 250 km" is an honest answer; a 642 km one is noise."""
        res = adapter.test_point(*OUTSIDE_BEYOND_SEARCH, ["territorial_sea"])
        detail = res.derived[0].detail
        assert detail["inside"] is False
        assert detail["nearest"] is None
        assert detail["search_km"] == 250.0

    def test_containment_carries_a_recomputable_derivation(self, adapter):
        res = adapter.test_point(*INSIDE_PAKISTAN_12NM, ["territorial_sea"])
        pid = res.derived[0].provenance_id
        prov = next(p for p in res.provenance if p.provenance_id == pid)
        d = prov.derivation
        assert d is not None
        assert d.method == "point_in_polygon"
        assert d.params["crs"] == "EPSG:4326"
        assert d.params["geometry_simplified"] is False
        assert d.params["dataset_version"] == "v4"
        assert d.params["snapshot_version"] == "2026-09-02"
        assert d.inputs                       # the feature it was tested against

    def test_every_feature_is_marked_advisory_only(self, adapter):
        res = adapter.test_point(*INSIDE_PAKISTAN_12NM)
        assert res.features
        assert all(f.advisory_only for f in res.features)
        assert all(d.detail["advisory_only"] for d in res.derived)

    def test_geometry_is_referenced_by_version_never_inlined(self, adapter):
        res = adapter.test_point(*INSIDE_PAKISTAN_12NM, ["territorial_sea"])
        f = res.features[0]
        assert f.geometry_inline is None
        assert f.geometry_ref.endswith("@2026-09-02")


class TestCoverageHonesty:
    def test_a_type_with_no_source_is_unavailable_never_proxied(self, adapter):
        res = adapter.test_point(*INSIDE_PAKISTAN_12NM,
                                 ["territorial_sea", "restricted_zone",
                                  "marine_protected_area"])
        unavailable = dict(res.unavailable)
        assert set(unavailable) == {"restricted_zone", "marine_protected_area"}
        assert all(d.detail["boundary_type"] == "territorial_sea"
                   for d in res.derived)

    def test_a_configured_layer_absent_from_the_snapshot_is_unavailable(self, adapter):
        """EEZ has a source in policy but is not in this snapshot."""
        res = adapter.test_point(*INSIDE_PAKISTAN_12NM, ["EEZ", "territorial_sea"])
        assert "EEZ" in dict(res.unavailable)

    def test_outside_the_region_refuses_rather_than_answering_no(self, adapter):
        with pytest.raises(MarineRegionsError) as exc:
            adapter.test_point(*OUTSIDE_THE_REGION)
        assert exc.value.code.value == "INSUFFICIENT_COVERAGE"
        assert "would be false" in exc.value.detail

    def test_no_configured_type_requested_is_an_error(self, adapter):
        with pytest.raises(MarineRegionsError) as exc:
            adapter.test_point(*INSIDE_PAKISTAN_12NM, ["restricted_zone"])
        assert exc.value.code.value == "DATASET_UNAVAILABLE"

    def test_no_snapshot_at_all_is_dataset_unavailable(self, tmp_path):
        a = MarineRegionsAdapter(snapshot_dir=tmp_path / "nothing")
        with pytest.raises(MarineRegionsError) as exc:
            a.test_point(20.0, 70.0)
        assert exc.value.code.value == "DATASET_UNAVAILABLE"
        assert "capture_boundaries" in exc.value.detail


class TestNearBoundary:
    def test_a_point_close_to_an_edge_is_flagged(self, adapter):
        """Containment 400 m inside must not read like containment 40 km inside."""
        res = adapter.test_point(24.0, 67.2, ["territorial_sea"])
        detail = res.derived[0].detail
        assert detail["distance_km"] is not None
        assert detail["near_boundary"] is (
            detail["distance_km"] < detail["near_boundary_km"])

    def test_distance_is_reported_even_when_inside(self, adapter):
        res = adapter.test_point(*INSIDE_PAKISTAN_12NM, ["territorial_sea"])
        assert res.derived[0].detail["distance_km"] > 0.0


class TestToolContract:
    """get_maritime_boundaries envelope promises (04 section 3.11)."""

    def test_advisory_only_and_the_disclaimer_are_always_present(self, adapter):
        from backend.orca.tools.boundaries import get_maritime_boundaries
        env = get_maritime_boundaries(*INSIDE_PAKISTAN_12NM, adapter=adapter)
        assert env.quality["advisory_only"] is True
        assert env.quality["disclaimer_id"] == "disc.boundary_advisory_only"
        assert env.quality["geometry_simplified"] is False
        assert env.quality["snapshot_version"] == "2026-09-02"

    def test_each_unconfigured_type_gets_its_own_warning(self, adapter):
        from backend.orca.schemas.errors import ErrorCode
        from backend.orca.tools.boundaries import get_maritime_boundaries
        env = get_maritime_boundaries(*INSIDE_PAKISTAN_12NM, adapter=adapter)
        subjects = {e.subject for e in env.errors
                    if e.code is ErrorCode.DATASET_UNAVAILABLE}
        assert {"restricted_zone", "marine_protected_area"} <= subjects
        assert all(e.severity == "warning" for e in env.errors)

    def test_every_data_object_resolves_to_a_provenance_record(self, adapter):
        from backend.orca.tools.boundaries import get_maritime_boundaries
        env = get_maritime_boundaries(*INSIDE_PAKISTAN_12NM, adapter=adapter)
        known = {p.provenance_id for p in env.provenance}
        assert env.data
        assert all(d.provenance_id in known for d in env.data)

    def test_an_invalid_position_fails_before_any_lookup(self, adapter):
        from backend.orca.schemas.errors import ErrorCode
        from backend.orca.tools.boundaries import get_maritime_boundaries
        env = get_maritime_boundaries(95.0, 200.0, adapter=adapter)
        assert env.codes() == [ErrorCode.INVALID_LOCATION]
        assert env.data == []

    def test_out_of_region_is_reported_not_answered(self, adapter):
        from backend.orca.schemas.errors import ErrorCode
        from backend.orca.tools.boundaries import get_maritime_boundaries
        env = get_maritime_boundaries(*OUTSIDE_THE_REGION, adapter=adapter)
        assert ErrorCode.INSUFFICIENT_COVERAGE in env.codes()
        assert env.data == []
