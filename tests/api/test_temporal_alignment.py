"""The temporal-alignment projection (23_FRONTEND_REBUILD_BRIEF.md tier 2 #5).

The strip answers "why was this value used and that one refused?". That makes
three properties load-bearing, and each is a way the panel could quietly lie:

  * it is built from PROVENANCE, so a value that was retrieved and then refused
    still appears -- a strip built from evidence alone shows only survivors and
    can never show a rejection;
  * a DERIVED value's inputs count as used, because the raw chlorophyll behind
    a ratio is the reason the ratio exists;
  * an exclusion carries its reason, joined even when the factor name extends
    the parameter name (`sst_anomaly_abs` from `sst_anomaly`).
"""
from datetime import datetime, timedelta, timezone

from backend.orca.api.main import _temporal_alignment
from backend.orca.schemas.assessment import Assessment, Evidence, NotEvaluated
from backend.orca.schemas.core import Derivation, Provenance, TemporalRef
from backend.orca.schemas.enums import Confidence, Domain, ValueKind, Verdict

UTC = timezone.utc
NOW = datetime.now(UTC)
WIN = {"start_time": (NOW + timedelta(hours=12)).isoformat(),
       "end_time": (NOW + timedelta(hours=16)).isoformat()}


class Env:
    """The shape `_temporal_alignment` reads off `state['tool_results']`."""
    def __init__(self, tool, provenance):
        self.tool = tool
        self.provenance = provenance


def prov(pid, parameter, *, valid, source="CMEMS", derivation=None):
    return Provenance(provenance_id=pid, parameter=parameter,
                      value_kind=ValueKind.FORECAST, source=source,
                      source_id="S-07", dataset="ds",
                      temporal=TemporalRef(valid_time=valid),
                      derivation=derivation)


def evidence(eid, pid, parameter):
    return Evidence(evidence_id=eid, domain=Domain.SAFETY,
                    statement=f"{parameter} recorded", parameter=parameter,
                    value=1.0, unit="m", value_kind=ValueKind.FORECAST,
                    provenance_id=pid)


def entry(out, parameter):
    return next(e for e in out["entries"] if e["parameter"] == parameter)


def build(**over):
    stale = NOW - timedelta(days=5400)
    fresh = NOW + timedelta(hours=14)
    records = [
        prov("pv-stale", "sst_anomaly", valid=stale, source="INCOIS ERDDAP"),
        prov("pv-raw", "chlorophyll_a", valid=NOW - timedelta(days=2)),
        prov("pv-ratio", "chlorophyll_ratio_to_local_median",
             valid=NOW - timedelta(days=2),
             derivation=Derivation(method="ratio_to_local_median",
                                   method_version="1", inputs=["pv-raw"])),
        prov("pv-wave", "significant_wave_height", valid=fresh),
    ]
    state = {
        "resolved_time_window": WIN,
        "tool_results": [Env("get_ocean", records)],
        "evidence": [evidence("ev-1", "pv-ratio", "chlorophyll_ratio_to_local_median"),
                     evidence("ev-2", "pv-wave", "significant_wave_height")],
        "assessments": [Assessment(
            assessment_id="a1", domain=Domain.SAFETY, verdict=Verdict.MARGINAL,
            confidence=Confidence.LOW,
            not_evaluated=[NotEvaluated(factor="sst_anomaly_abs",
                                        reason="STALE_DATA",
                                        detail="valid 2011, window is 2026")])],
    }
    state.update(over)
    return _temporal_alignment(state)


class TestRefusedValuesSurvive:
    def test_a_retrieved_but_unused_value_still_appears(self):
        assert entry(build(), "sst_anomaly")["used"] is False

    def test_it_carries_the_reason_it_was_refused(self):
        """Joined across the factor/parameter name difference, not dropped."""
        e = entry(build(), "sst_anomaly")
        assert e["excluded_reason"] == "STALE_DATA"
        assert "2011" in e["excluded_detail"]

    def test_its_true_age_is_reported(self):
        age_days = entry(build(), "sst_anomaly")["age_s"] / 86400
        assert age_days > 5000


class TestDerivationLineage:
    def test_a_derived_value_is_used(self):
        assert entry(build(), "chlorophyll_ratio_to_local_median")["used"] is True

    def test_its_raw_input_is_used_too(self):
        """The raw chlorophyll IS why the ratio exists; unused would be a lie."""
        assert entry(build(), "chlorophyll_a")["used"] is True

    def test_the_method_is_named(self):
        e = entry(build(), "chlorophyll_ratio_to_local_median")
        assert e["derived_via"] == "ratio_to_local_median"

    def test_a_cycle_in_the_chain_terminates(self):
        """A self-referential derivation must not hang the request."""
        loop = [prov("pv-a", "a", valid=NOW,
                     derivation=Derivation(method="m", method_version="1",
                                           inputs=["pv-b"])),
                prov("pv-b", "b", valid=NOW,
                     derivation=Derivation(method="m", method_version="1",
                                           inputs=["pv-a"]))]
        out = build(tool_results=[Env("t", loop)],
                    evidence=[evidence("ev-1", "pv-a", "a")], assessments=[])
        assert {e["parameter"] for e in out["entries"]} == {"a", "b"}
        assert all(e["used"] for e in out["entries"])


class TestWindowAndShape:
    def test_the_analysis_window_is_carried(self):
        assert build()["window"] == {"start_time": WIN["start_time"],
                                     "end_time": WIN["end_time"]}

    def test_every_retrieved_record_is_listed_once(self):
        ids = [e["provenance_id"] for e in build()["entries"]]
        assert len(ids) == len(set(ids)) == 4

    def test_a_forecast_reports_a_negative_age(self):
        """It is valid AHEAD of now; reporting it as old would invert the fact."""
        assert entry(build(), "significant_wave_height")["age_s"] < 0

    def test_no_tool_results_is_empty_not_an_error(self):
        out = build(tool_results=[], evidence=[], assessments=[])
        assert out["entries"] == []
