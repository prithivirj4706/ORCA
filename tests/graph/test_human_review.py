"""Human review: durable interrupt and resume (07 sections 9, 10, 15).

The run is checkpointed at the interrupt, so the process may restart while a
reviewer decides. Nothing is delivered until a decision is recorded.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from backend.orca.graph.build import build_graph
from backend.orca.graph.runtime import OrcaRuntime
from backend.orca.schemas.core import (
    Provenance, QualityMetadata, SpatialRef, TemporalRef,
)
from backend.orca.schemas.data import Forecast, MarineWarning
from backend.orca.schemas.enums import (
    EnvelopeStatus, QualityFlag, Representativeness as R, ValueKind,
)
from backend.orca.schemas.envelope import OrcaEnvelope
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


def active_warning_env():
    """An official warning in force -- quoted, never computed."""
    pid = "pv-warn-1"
    warning = MarineWarning(
        warning_id="W-1", warning_type="rough_sea", severity="warning",
        issuing_office="TEST-IMD", issued_at=WIN_START,
        valid_from=WIN_START, valid_to=WIN_START + timedelta(hours=12),
        area_description="Kerala coast",
        text_verbatim="Rough sea conditions likely. Fishermen advised not to venture.",
        provenance_id=pid)
    return OrcaEnvelope(
        status=EnvelopeStatus.SUCCESS, tool="get_marine_warnings",
        data=[warning],
        provenance=[Provenance(provenance_id=pid, parameter="marine_warning_status",
                               value_kind=ValueKind.OBSERVED, source="TEST",
                               source_id="S-TEST")])


def env_with(tool, values):
    """Favourable model values, so the WARNING is what governs the verdict."""
    valid_time = WIN_START + timedelta(hours=2)
    data, prov = [], []
    for i, (param, (val, unit)) in enumerate(values.items()):
        pid = f"pv-{tool}-{i}"
        temporal = TemporalRef(valid_time=valid_time,
                               representativeness=R.INSTANTANEOUS)
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


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.bind("get_marine_warnings", lambda **kw: active_warning_env())
    r.bind("get_wave_conditions",
           lambda **kw: env_with("get_wave_conditions",
                                 {"significant_wave_height": (1.1, "m")}))
    r.bind("get_weather",
           lambda **kw: env_with("get_weather", {"wind_speed": (5.0, "m s-1")}))
    for name in ("get_currents", "get_sst", "get_chlorophyll",
                 "get_ocean_observations", "get_lightning", "get_cyclone_track",
                 "get_pfz", "get_maritime_boundaries"):
        r.mark_unavailable(name, "not part of this fixture")
    return r


def _config(registry, thread_id):
    rt = OrcaRuntime(registry=registry)
    return {"configurable": {**rt.configurable(), "thread_id": thread_id}}


class TestInterruptAndResume:
    def test_an_active_warning_holds_the_answer_for_review(self, registry):
        graph = build_graph(checkpointer=MemorySaver())
        config = _config(registry, "t-1")
        result = graph.invoke(
            {"query_text": "is it safe near Kochi tomorrow morning?",
             "client_location": LOC, "client_time_window": WIN}, config)

        assert "__interrupt__" in result, "the run should pause for review"
        payload = result["__interrupt__"][0].value
        assert payload["disposition"] == "REVIEW_REQUIRED"
        # Nothing has been delivered yet.
        assert result.get("recommendation") is None

    def test_resuming_with_a_decision_delivers_the_answer(self, registry):
        graph = build_graph(checkpointer=MemorySaver())
        config = _config(registry, "t-2")
        graph.invoke({"query_text": "is it safe near Kochi tomorrow morning?",
                      "client_location": LOC, "client_time_window": WIN}, config)

        final = graph.invoke(Command(resume={
            "reviewer_id": "u-9", "reviewer_role": "officer",
            "decision": "approved",
            "rationale": "Warning conveyed verbatim; ORCA adds context only.",
            "reviewed_at": "2026-09-02T12:00:00+00:00"}), config)

        assert final["recommendation"] is not None
        assert final["recommendation"].human_review["decision"] == "approved"
        assert final["recommendation"].human_review["reviewer_id"] == "u-9"

    def test_state_survives_a_rebuilt_graph_between_interrupt_and_resume(self,
                                                                        registry):
        """The process may restart while the interrupt is pending."""
        saver = MemorySaver()
        config = _config(registry, "t-3")
        build_graph(checkpointer=saver).invoke(
            {"query_text": "is it safe near Kochi tomorrow morning?",
             "client_location": LOC, "client_time_window": WIN}, config)

        # A brand-new graph object, same checkpointer and thread.
        final = build_graph(checkpointer=saver).invoke(
            Command(resume={"reviewer_id": "u-1", "reviewer_role": "reviewer",
                            "decision": "approved_with_edits", "rationale": "ok",
                            "reviewed_at": "2026-09-02T12:05:00+00:00"}), config)
        assert final["recommendation"].human_review["decision"] == "approved_with_edits"


class TestOfficialWarningGoverns:
    def test_the_answer_defers_to_the_official_warning(self, registry):
        graph = build_graph(checkpointer=MemorySaver())
        config = _config(registry, "t-4")
        graph.invoke({"query_text": "is it safe near Kochi tomorrow morning?",
                      "client_location": LOC, "client_time_window": WIN}, config)
        final = graph.invoke(Command(resume={
            "reviewer_id": "u-9", "reviewer_role": "officer", "decision": "approved",
            "rationale": "ok", "reviewed_at": "2026-09-02T12:00:00+00:00"}), config)
        assert final["recommendation"].category == "DEFER_TO_OFFICIAL"
