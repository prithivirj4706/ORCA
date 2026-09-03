"""API layer (08_API_SPEC.md). Offline: no lifespan, no adapters, no network.

The API adds no reasoning, so what is tested here is the projection contract
the UI depends on and the one decision the API itself owns — per-turn language.
"""
import pytest

from backend.orca.api.main import ChatRequest, _dump, _initial_state, _project
from backend.orca.schemas.assessment import Assessment
from backend.orca.schemas.enums import Confidence, Domain, Verdict


class TestPerTurnLanguage:
    """A checkpoint restores the previous turn's language; the problem
    statement asks for the language of the query in hand."""

    def test_malayalam_query_is_detected(self):
        s = _initial_state(ChatRequest(query="കൊച്ചിയിൽ നാളെ മീൻപിടിക്കാമോ?"))
        assert s["language"] == "ml"

    def test_english_follow_up_is_not_stuck_on_the_thread_language(self):
        assert _initial_state(ChatRequest(query="is it safe?"))["language"] == "en"

    def test_an_explicit_language_overrides_detection(self):
        s = _initial_state(ChatRequest(query="is it safe?", language="ta"))
        assert s["language"] == "ta"

    def test_coordinates_are_passed_through_when_given(self):
        """On `client_location`, the caller's own channel.

        Not `resolved_location`: that is what the graph WRITES, and a
        checkpointed thread restores it, so caller input placed there would be
        indistinguishable from the previous turn's answer.
        """
        s = _initial_state(ChatRequest(query="x", lat=9.9, lon=76.2))
        assert s["client_location"]["lat"] == 9.9

    def test_no_location_key_when_not_given(self):
        s = _initial_state(ChatRequest(query="x"))
        assert "client_location" not in s
        assert "resolved_location" not in s


class TestProjection:
    def _final(self, **kw):
        base = {"language": "ml", "intent": "fishing_suitability",
                "assessments": [Assessment(assessment_id="as-1",
                                           domain=Domain.SAFETY,
                                           verdict=Verdict.MARGINAL,
                                           confidence=Confidence.MEDIUM)],
                "alerts": [{"kind": "approaching", "boundary_type": "EEZ"}],
                "node_events": [{"node": "plan", "status": "success"}]}
        base.update(kw)
        return base

    def test_every_key_the_ui_needs_is_present(self):
        p = _project(self._final(), "t-1")
        for key in ("thread_id", "language", "intent", "plan", "assessments",
                    "evidence", "alerts", "map_layers", "claims",
                    "recommendation", "trace", "disposition",
                    "resolution_notes", "clarification_needed"):
            assert key in p, f"missing {key}"

    def test_pydantic_models_are_serialised(self):
        p = _project(self._final(), "t-1")
        assert isinstance(p["assessments"][0], dict)
        assert p["assessments"][0]["verdict"] == "MARGINAL"

    def test_alerts_survive_the_projection(self):
        assert _project(self._final(), "t-1")["alerts"][0]["boundary_type"] == "EEZ"

    def test_a_missing_plan_does_not_crash(self):
        assert _project(self._final(plan=None), "t-1")["plan"] is None

    def test_the_thread_id_is_echoed(self):
        assert _project(self._final(), "t-9")["thread_id"] == "t-9"


class TestDumpIsTotal:
    """Whatever the graph puts in state has to reach the client as JSON."""

    @pytest.mark.parametrize("value", [None, 1, 1.5, "x", True,
                                       [1, 2], {"a": 1}, {"a": [{"b": 2}]}])
    def test_plain_values_round_trip(self, value):
        assert _dump(value) == value

    def test_a_pydantic_model_becomes_a_dict(self):
        a = Assessment(assessment_id="as-1", domain=Domain.SAFETY,
                       verdict=Verdict.MARGINAL, confidence=Confidence.LOW)
        assert _dump(a)["assessment_id"] == "as-1"
