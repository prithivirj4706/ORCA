"""Multi-turn conversation state (problem statement, capability 3).

A checkpointed thread reuses graph state across turns, so every append-reduced
channel would otherwise accumulate: turn three showed turn one's verdict and
three duplicated geofence alerts. Each run must see only its own output, while
the RESOLVED CONTEXT still carries forward.
"""
from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver

from backend.orca.graph.build import build_graph
from backend.orca.graph.runtime import OrcaRuntime
from backend.orca.graph.state import RESET, add_or_reset
from backend.orca.tools.registry import ToolRegistry

UTC = timezone.utc


class TestReducer:
    def test_append_is_the_default(self):
        assert add_or_reset([1, 2], [3]) == [1, 2, 3]

    def test_the_sentinel_clears_the_channel(self):
        assert add_or_reset([1, 2, 3], RESET) == []

    def test_a_cleared_channel_accepts_new_writes(self):
        assert add_or_reset(add_or_reset([1], RESET), [9]) == [9]

    def test_none_is_tolerated(self):
        assert add_or_reset(None, [1]) == [1]
        assert add_or_reset([1], None) == [1]


@pytest.fixture
def rt():
    r = ToolRegistry()
    for t in ("get_wave_conditions", "get_weather", "get_maritime_boundaries",
              "get_marine_warnings", "get_sst", "get_chlorophyll", "get_currents",
              "get_pfz", "get_lightning", "get_cyclone_track",
              "get_ocean_observations", "get_tides"):
        r.mark_unavailable(t, "not part of this fixture")
    return OrcaRuntime(registry=r)


class TestTurnsDoNotAccumulate:
    def _thread(self, rt):
        graph = build_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "t-multi", **rt.configurable()}}
        return graph, cfg

    def test_a_second_turn_does_not_inherit_the_first_turns_verdicts(self, rt):
        graph, cfg = self._thread(rt)
        a = graph.invoke({"query_text": "am I inside the Indian EEZ near Kochi?"}, cfg)
        b = graph.invoke({"query_text": "is it safe near Kochi tomorrow morning?"}, cfg)
        # Turn two is a safety question; a REGULATORY card from turn one would
        # be a stale answer presented as current.
        domains = {x.domain.value for x in b["assessments"]}
        assert domains == {"SAFETY"}, f"leaked from the previous turn: {domains}"

    def test_alerts_are_not_duplicated_across_turns(self, rt):
        graph, cfg = self._thread(rt)
        a = graph.invoke({"query_text": "am I inside the Indian EEZ near Kochi?"}, cfg)
        b = graph.invoke({"query_text": "am I inside the Indian EEZ near Kochi?"}, cfg)
        assert len(b["alerts"]) == len(a["alerts"])

    def test_the_trace_shows_only_this_run(self, rt):
        graph, cfg = self._thread(rt)
        a = graph.invoke({"query_text": "am I inside the Indian EEZ near Kochi?"}, cfg)
        b = graph.invoke({"query_text": "am I inside the Indian EEZ near Kochi?"}, cfg)
        assert len(b["node_events"]) == len(a["node_events"])

    def test_each_turn_gets_its_own_run_id(self, rt):
        graph, cfg = self._thread(rt)
        a = graph.invoke({"query_text": "am I inside the Indian EEZ near Kochi?"}, cfg)
        b = graph.invoke({"query_text": "am I inside the Indian EEZ near Kochi?"}, cfg)
        assert a["run_id"] != b["run_id"]


class TestContextStillCarries:
    def test_the_location_survives_into_the_next_turn(self, rt):
        """Resetting per-run channels must not reset the CONVERSATION."""
        graph = build_graph(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "t-carry", **rt.configurable()}}
        graph.invoke({"query_text": "am I inside the Indian EEZ near Kochi?"}, cfg)
        # no place named in the follow-up
        b = graph.invoke({"query_text": "is it safe tomorrow morning?"}, cfg)
        assert b["resolved_location"]["lat"] == pytest.approx(9.93, abs=0.01)
        assert b.get("clarification_needed") is None
