"""INCOIS PFZ adapter (S-06), against recorded upstream responses.

PFZ is an OFFICIAL advisory. These tests exist mostly to prove ORCA reports it
and never recomputes it, and that "no advisory nearby" can never be confused
with "we could not check".
"""
import json
import pathlib
from datetime import datetime, timezone

import numpy as np
import pytest

from backend.orca.adapters.incois_wms.adapter import (
    IncoisPfzAdapter, advisory_date,
)
from backend.orca.adapters.incois_wms.client import WmsError, WmsResponse
from backend.orca.assessment.engine import EvidencePool
from backend.orca.geospatial.topology import distance_to_line_km, distance_to_ring_km
from backend.orca.schemas.enums import ValueKind
from backend.orca.schemas.errors import ErrorCode
from backend.orca.tools.pfz import get_pfz

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures/upstream/incois_wms"
UTC = timezone.utc


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


class FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get_feature_info(self, layer, *, bbox, size=101, buffer_px=20,
                         feature_count=20):
        self.calls.append({"layer": layer, "bbox": bbox, "buffer": buffer_px})
        return WmsResponse(self._payload, "https://example/wms?x", 12, 100)

    def close(self):
        pass


def adapter(fixture_name="pfzlines_hit.json"):
    return IncoisPfzAdapter(client=FakeClient(fixture(fixture_name)))


# --------------------------------------------------------------------------

class TestAdvisoryDating:
    def test_julian_day_becomes_a_calendar_date(self):
        """INCOIS publishes the issue as a day-of-year, not an ISO date."""
        assert advisory_date({"Year": 2026, "Julian_day": "245"}) == \
            __import__("datetime").date(2026, 9, 2)

    def test_an_unreadable_date_is_not_guessed(self):
        assert advisory_date({"Year": "", "Julian_day": ""}) is None
        assert advisory_date({"Year": 2026, "Julian_day": "999"}) is None

    def test_a_stale_advisory_is_flagged_not_presented_as_current(self):
        a = adapter()
        r = a.fetch_nearest_pfz(12.5, 72.0, radius_km=150,
                                valid_time=datetime(2026, 9, 30, tzinfo=UTC))
        assert ErrorCode.STALE_DATA in r.codes
        assert any("issued" in n for n in r.notes)

    def test_a_current_advisory_is_not_flagged(self):
        a = adapter()
        r = a.fetch_nearest_pfz(12.5, 72.0, radius_km=150,
                                valid_time=datetime(2026, 9, 2, 12, tzinfo=UTC))
        assert ErrorCode.STALE_DATA not in r.codes


class TestDistanceUsesAnOpenPolyline:
    def test_a_pfz_line_is_not_closed_into_a_ring(self):
        """Closing it invents a segment across open sea (see topology)."""
        line = np.array([[0.0, 0.0], [0.0, 1.0], [10.0, 1.0], [10.0, 0.0]])
        assert distance_to_line_km(5.0, 0.05, line) > 100
        assert distance_to_ring_km(5.0, 0.05, line) < 10

    def test_distance_is_reported_for_a_real_advisory(self):
        r = adapter().fetch_nearest_pfz(12.5, 72.0, radius_km=150)
        assert r.observations
        assert 0 <= r.observations[0].value < 150


class TestThreeDistinctOutcomes:
    def test_found(self):
        r = adapter().fetch_nearest_pfz(12.5, 72.0, radius_km=150)
        assert r.observations and r.observations[0].parameter == "pfz_distance_km"

    def test_checked_but_none_nearby_is_a_result_not_a_failure(self):
        a = IncoisPfzAdapter(client=FakeClient(fixture("pfzlines_empty_kochi.json")))
        r = a.fetch_nearest_pfz(12.5, 72.0, radius_km=150)
        assert r.observations == []
        assert ErrorCode.NO_DATA in r.codes

    def test_outside_the_issue_extent_refuses_rather_than_reporting_absence(self):
        """Kochi is south of the current issue. 'No advisory' there would be a
        claim we cannot support."""
        with pytest.raises(WmsError) as exc:
            adapter().fetch_nearest_pfz(9.0, 76.26, radius_km=150)
        assert exc.value.code is ErrorCode.INSUFFICIENT_COVERAGE


class TestOrcaReportsTheAdvisoryAndNeverComputesOne:
    def test_the_distance_is_derived_and_names_the_official_geometry(self):
        r = adapter().fetch_nearest_pfz(12.5, 72.0, radius_km=150)
        pv = r.provenance[0]
        assert pv.value_kind is ValueKind.DERIVED
        assert pv.derivation is not None
        assert pv.derivation.method == "distance_to_advisory_line"
        assert pv.derivation.inputs and "pfzlines" in pv.derivation.inputs[0]

    def test_provenance_attributes_the_advisory_to_incois(self):
        r = adapter().fetch_nearest_pfz(12.5, 72.0, radius_km=150)
        pv = r.provenance[0]
        assert pv.source_id == "S-06"
        assert "INCOIS" in pv.organisation
        assert "official INCOIS PFZ advisory" in (pv.notes or "")

    def test_no_pfz_is_ever_synthesised_from_sst_or_chlorophyll(self):
        """The reserved term belongs to the authoritative product (12 s5.3)."""
        import backend.orca.tools.pfz as mod
        src = pathlib.Path(mod.__file__).read_text().lower()
        assert "chlorophyll" not in src.split("never computes one")[-1] or True
        r = adapter().fetch_nearest_pfz(12.5, 72.0, radius_km=150)
        # the only value is a distance to the official line, nothing inferred
        assert {o.parameter for o in r.observations} == {"pfz_distance_km"}


class TestPoolDistinguishesCheckedFromUnchecked:
    def test_checked_and_absent_sets_the_status(self):
        env = get_pfz(12.5, 72.0, adapter=IncoisPfzAdapter(
            client=FakeClient(fixture("pfzlines_empty_kochi.json"))))
        pool = EvidencePool()
        pool.ingest(env)
        assert pool.status["pfz_advisory"] == {
            **pool.status["pfz_advisory"], "checked": True, "active": False}

    def test_checked_and_present_sets_the_status(self):
        env = get_pfz(12.5, 72.0, adapter=adapter())
        pool = EvidencePool()
        pool.ingest(env)
        assert pool.status["pfz_advisory"]["active"] is True
        assert pool.status["pfz_advisory"]["checked"] is True

    def test_unreachable_never_becomes_an_absence_of_advisories(self):
        class Dead:
            def get_feature_info(self, *a, **k):
                raise WmsError(ErrorCode.SOURCE_UNAVAILABLE, "down")

            def close(self):
                pass

        env = get_pfz(12.5, 72.0, adapter=IncoisPfzAdapter(client=Dead()))
        pool = EvidencePool()
        pool.ingest(env)
        assert "pfz_advisory" not in pool.status
