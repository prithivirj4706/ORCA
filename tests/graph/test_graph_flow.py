"""Graph orchestration (07_LANGGRAPH_WORKFLOW_SPEC.md section 15).

Every test here is OFFLINE and uses no LLM: the deterministic path is a
first-class supported mode, so the whole graph must run without either.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.orca.graph.build import build_graph
from backend.orca.graph.runtime import OrcaRuntime
from backend.orca.schemas.core import (
    Provenance, QualityMetadata, SpatialRef, TemporalRef,
)
from backend.orca.schemas.data import Forecast
from backend.orca.schemas.enums import (
    Domain, EnvelopeStatus, QualityFlag, Representativeness as R, ValueKind, Verdict,
)
from backend.orca.schemas.envelope import OrcaEnvelope
from backend.orca.schemas.errors import ErrorCode
from backend.orca.tools.registry import ToolRegistry

UTC = timezone.utc

def _tomorrow_morning_utc() -> datetime:
    """The window "tomorrow morning" actually resolves to, computed the same way.

    These fixtures used to hardcode a date, which meant the suite passed only
    until that date arrived and then failed for reasons that had nothing to do
    with the code. Deriving it from `now` keeps the fixture and the query saying
    the same thing forever.
    """
    base = datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=1)
    return base.replace(hour=6, minute=0, second=0,
                        microsecond=0).astimezone(UTC)


WIN_START = _tomorrow_morning_utc()
WIN = {"start_time": WIN_START.isoformat(),
       "end_time": (WIN_START + timedelta(hours=4)).isoformat()}
LOC = {"lat": 9.93, "lon": 76.26, "label": "near Kochi"}


def env_with(tool, values, *, valid_time=None):
    valid_time = valid_time or (WIN_START + timedelta(hours=2))
    data, prov = [], []
    for i, (param, (val, unit)) in enumerate(values.items()):
        pid = f"pv-{tool}-{i}"
        temporal = TemporalRef(valid_time=valid_time, representativeness=R.INSTANTANEOUS)
        spatial = SpatialRef.point(LOC["lat"], LOC["lon"])
        quality = QualityMetadata(flag=QualityFlag.NOMINAL, basis="source-provided")
        data.append(Forecast(parameter=param, value=val, unit=unit, spatial=spatial,
                             temporal=temporal, quality=quality, provenance_id=pid))
        prov.append(Provenance(provenance_id=pid, parameter=param,
                               value_kind=ValueKind.FORECAST, unit=unit,
                               spatial=spatial, temporal=temporal, source="TEST",
                               source_id="S-TEST", dataset="test_ds",
                               quality=quality))
    return OrcaEnvelope(status=EnvelopeStatus.SUCCESS, tool=tool, data=data,
                        provenance=prov)


def warning_env():
    """A genuine NO_ACTIVE_WARNING -- a result, not a failure."""
    env = OrcaEnvelope.empty("get_marine_warnings", ErrorCode.NO_ACTIVE_WARNING,
                             "no warning intersecting the query", "marine_warning")
    env.provenance.append(Provenance(
        provenance_id="pv-warn-0", parameter="marine_warning_status",
        value_kind=ValueKind.OBSERVED, source="TEST", source_id="S-TEST"))
    return env


def make_registry(*, wind=True, warnings=True, waves=True, raise_on=()):
    r = ToolRegistry()

    def bound(tool, values):
        def fn(**kw):
            if tool in raise_on:
                raise RuntimeError(f"{tool} exploded")
            return env_with(tool, values)
        return fn

    if waves:
        r.bind("get_wave_conditions", bound("get_wave_conditions",
                                            {"significant_wave_height": (1.2, "m"),
                                             "swell_height": (0.9, "m")}))
    else:
        r.mark_unavailable("get_wave_conditions", "no source")
    if wind:
        r.bind("get_weather", bound("get_weather", {"wind_speed": (6.0, "m s-1")}))
    else:
        r.mark_unavailable("get_weather", "no source")
    if warnings:
        r.bind("get_marine_warnings", lambda **kw: warning_env())
    else:
        r.mark_unavailable("get_marine_warnings", "IMD credentials not granted")

    r.bind("get_sst", bound("get_sst", {"sst": (28.4, "degC")}))
    r.bind("get_chlorophyll", bound("get_chlorophyll",
                                    {"chlorophyll_ratio_to_local_median":
                                     (1.6, "ratio")}))
    for name in ("get_currents", "get_ocean_observations", "get_lightning",
                 "get_cyclone_track", "get_pfz", "get_maritime_boundaries"):
        r.mark_unavailable(name, "not part of this fixture")
    return r


def run(registry, query="is it safe to go out near Kochi tomorrow morning?",
        **overrides):
    rt = OrcaRuntime(registry=registry)
    graph = build_graph()
    # `client_*` are the CALLER's channels. `resolved_*` are what the graph
    # writes and a checkpoint restores, so seeding those would test a path no
    # caller can take -- and would hide the staleness bug F-73 fixed.
    state = {"query_text": query, "client_location": LOC,
             "client_time_window": WIN, **overrides}
    return graph.invoke(state, config={"configurable": rt.configurable()})


def domain(final, d):
    return next((a for a in final["assessments"] if a.domain is d), None)


def nodes_run(final):
    return [e["node"] for e in final["node_events"]]


class TestHappyPath:
    def test_all_nodes_execute_and_a_recommendation_is_produced(self):
        final = run(make_registry())
        assert final["recommendation"] is not None
        for node in ("ingest", "intent_context", "plan", "tool_exec", "validate",
                     "geo_reason", "review_gate", "finalize"):
            assert node in nodes_run(final), f"{node} did not run"

    def test_safety_gets_a_verdict_when_its_required_inputs_arrive(self):
        final = run(make_registry())
        safety = domain(final, Domain.SAFETY)
        assert safety is not None
        assert safety.verdict is not Verdict.INSUFFICIENT_EVIDENCE

    def test_no_chain_of_thought_reaches_state(self):
        final = run(make_registry())
        blob = str(final["node_events"])
        for leak in ("system:", "you are", "let me think", "step 1:"):
            assert leak not in blob.lower()


class TestPartialFailure:
    def test_one_dead_tool_does_not_kill_the_run(self):
        """A raising tool becomes a recorded failure; the fan-in still occurs."""
        final = run(make_registry(raise_on=("get_wave_conditions",)))
        assert final["recommendation"] is not None
        outcomes = {r.tool: r.outcome for r in final["step_results"]}
        assert outcomes["get_wave_conditions"] == "failed"
        assert outcomes["get_weather"] == "satisfied"

    def test_missing_required_evidence_yields_no_safety_verdict(self):
        """Absence of evidence is not evidence of safety."""
        final = run(make_registry(wind=False, warnings=False))
        safety = domain(final, Domain.SAFETY)
        assert safety.verdict is Verdict.INSUFFICIENT_EVIDENCE
        assert safety.missing_required

    def test_unavailable_capability_is_reported_as_a_gap(self):
        final = run(make_registry(warnings=False))
        gaps = {u["tool"] for u in final["unavailable_capabilities"]}
        assert "get_marine_warnings" in gaps


class TestReplanIsBounded:
    def test_replan_fires_at_most_twice_and_the_run_completes(self):
        final = run(make_registry(wind=False))
        assert final["attempts"] <= 2
        assert nodes_run(final).count("replan") <= 2
        assert final["recommendation"] is not None

    def test_an_unfillable_gap_does_not_provoke_a_replan(self):
        """A required input with no reachable source degrades, it does not loop.

        Re-planning would re-issue an identical request to a tool that has no
        source, or that already answered with everything it had.
        """
        final = run(make_registry(warnings=False))
        report = final["validation_report"]
        assert "official_warning_status" in report.required_gaps
        assert report.actionable_gaps == []
        assert "replan" not in nodes_run(final)

    def test_a_tool_is_not_called_twice_for_the_same_run(self):
        final = run(make_registry(warnings=False))
        called = [r.tool for r in final["step_results"]]
        assert len(called) == len(set(called)), f"duplicate tool calls: {called}"

    def test_blocked_still_explains_itself_without_issuing_a_verdict(self):
        """The degradation ladder ends in an honest refusal, not in silence."""
        final = run(make_registry(wind=False, warnings=False, waves=False))
        rec = final["recommendation"]
        assert final["disposition"] == "BLOCKED"
        assert rec is not None and rec.narrative
        safety = domain(final, Domain.SAFETY)
        assert safety.verdict is Verdict.INSUFFICIENT_EVIDENCE
        assert "not an official advisory" in rec.narrative


class TestDomainFanOut:
    def test_only_requested_domains_are_assessed(self):
        final = run(make_registry(), query="am I inside the Indian EEZ?")
        assert {a.domain for a in final["assessments"]} == {Domain.REGULATORY}

    def test_join_is_order_independent_and_complete(self):
        final = run(make_registry(),
                    query="is it good for fishing near Kochi tomorrow?")
        domains = {a.domain for a in final["assessments"]}
        assert domains == {Domain.SAFETY, Domain.FISHING_SUITABILITY,
                           Domain.REGULATORY}

    def test_domains_are_never_merged_into_one_score(self):
        final = run(make_registry(),
                    query="is it good for fishing near Kochi tomorrow?")
        rec = final["recommendation"]
        assert len(rec.assessments) == len(final["assessments"])
        assert not hasattr(rec, "overall_score")


class TestClarificationPath:
    def test_unresolved_location_stops_before_retrieval(self):
        final = run(make_registry(), query="is it safe out there?",
                    client_location=None, client_time_window=None)
        assert final.get("clarification_needed") == "location"
        assert "tool_exec" not in nodes_run(final)


class TestDeterminism:
    def test_same_fixtures_give_the_same_verdicts(self):
        a = run(make_registry())
        b = run(make_registry())
        assert ([(x.domain, x.verdict) for x in a["assessments"]]
                == [(x.domain, x.verdict) for x in b["assessments"]])


class TestNoLLMRequired:
    def test_graph_completes_with_no_model_configured(self):
        rt = OrcaRuntime(registry=make_registry())
        assert rt.llm.available is False
        final = run(make_registry())
        assert final["recommendation"].narrative
        assert "not an official advisory" in final["recommendation"].narrative
