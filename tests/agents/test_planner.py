"""Planner behaviour (06_AGENT_SPEC.md sections 3, 10).

The Planner earns its place by planning LESS than the tool set allows.
"""
import pytest

from backend.orca.agents.planner import PlannerAgent, evidence_for
from backend.orca.schemas.enums import Domain
from backend.orca.tools.registry import ToolRegistry

LOC = {"lat": 9.93, "lon": 76.26, "label": "near Kochi"}
WIN = {"start_time": "2026-09-03T00:30:00+00:00",
       "end_time": "2026-09-03T04:30:00+00:00"}

BOUND = ("get_wave_conditions", "get_currents", "get_weather", "get_sst",
         "get_chlorophyll", "get_ocean_observations", "get_maritime_boundaries")
UNAVAILABLE = {"get_marine_warnings": "IMD credentials not granted",
               "get_lightning": "IMD credentials not granted",
               "get_cyclone_track": "IMD credentials not granted",
               "get_pfz": "INCOIS WMS pending verification"}


@pytest.fixture
def registry():
    r = ToolRegistry()
    for name in BOUND:
        r.bind(name, lambda **kw: None)
    for name, why in UNAVAILABLE.items():
        r.mark_unavailable(name, why)
    return r


def plan_for(registry, query, **kw):
    result = PlannerAgent().plan(query_text=query, registry=registry,
                                 resolved_location=kw.pop("location", LOC),
                                 resolved_time_window=kw.pop("window", WIN), **kw)
    assert result.ok, result.failure
    return result.value


class TestToolSelectionMinimality:
    def test_warning_lookup_does_not_plan_ocean_variables(self, registry):
        """Nine capabilities exist; a warning lookup needs one of them."""
        plan = plan_for(registry, "is there any warning in force right now?")
        assert plan.intent == "warning_lookup"
        planned = {s.tool for s in plan.steps}
        assert "get_sst" not in planned
        assert "get_chlorophyll" not in planned
        assert "get_wave_conditions" not in planned

    def test_unreachable_capability_is_declared_not_omitted(self, registry):
        plan = plan_for(registry, "is there any warning in force right now?")
        declared = {u["tool"] for u in plan.unavailable_capabilities}
        assert "get_marine_warnings" in declared
        reason = next(u["reason"] for u in plan.unavailable_capabilities
                      if u["tool"] == "get_marine_warnings")
        assert "credential" in reason.lower()

    def test_boundary_check_plans_only_boundaries(self, registry):
        plan = plan_for(registry, "am I inside the Indian EEZ?")
        assert [s.tool for s in plan.steps] == ["get_maritime_boundaries"]
        assert plan.domains_required == [Domain.REGULATORY]

    def test_fishing_question_plans_safety_and_regulatory_too(self, registry):
        plan = plan_for(registry, "is it good for fishing near Kochi tomorrow?")
        assert set(plan.domains_required) == {
            Domain.SAFETY, Domain.FISHING_SUITABILITY, Domain.REGULATORY}
        assert "get_maritime_boundaries" in {s.tool for s in plan.steps}


class TestEvidenceTablesComeFromConfig:
    def test_required_evidence_matches_the_threshold_set(self):
        required, preferred, optional = evidence_for(Domain.SAFETY)
        assert "official_warning_status" in required
        assert "significant_wave_height" in required
        assert "wind_speed" in required
        assert "swell_height" in preferred

    def test_regulatory_needs_boundaries(self):
        required, _, _ = evidence_for(Domain.REGULATORY)
        assert required == ("maritime_boundaries",)


class TestClarificationRatherThanGuessing:
    def test_unresolved_location_asks_and_plans_nothing(self, registry):
        plan = plan_for(registry, "is it safe?", location=None)
        assert plan.clarification_needed == "location"
        assert plan.steps == []

    def test_an_unclassifiable_but_MARINE_query_asks_for_the_topic(self, registry):
        """Marine words, no recognisable intent -> ask which topic.

        This is the case the clarifying question exists for. It must survive the
        out-of-scope test added alongside it, or every phrasing the keyword
        table happens not to carry would be refused instead of asked about.
        """
        plan = plan_for(registry, "what about the water there?")
        assert plan.clarification_needed == "intent"
        assert plan.steps == []

    def test_a_query_with_no_marine_content_is_out_of_scope(self, registry):
        """A greeting is not a marine question missing a detail.

        Asking "which topic?" about "hello there" asserts that it WAS one, so
        the exchange was untrue about itself even though it fabricated nothing.
        """
        plan = plan_for(registry, "hello there")
        assert plan.intent == "smalltalk_or_out_of_scope"
        assert plan.clarification_needed is None
        assert plan.steps == []

    def test_time_sensitive_intent_needs_a_window(self, registry):
        plan = plan_for(registry, "is it safe to go out near Kochi?", window=None)
        assert plan.clarification_needed == "time_window"
        assert plan.steps == []


class TestReplanIsBounded:
    def test_replan_addresses_only_the_reported_gaps(self, registry):
        first = plan_for(registry, "is it safe near Kochi tomorrow?")
        agent = PlannerAgent()
        second = agent.plan(query_text="is it safe near Kochi tomorrow?",
                            registry=registry, resolved_location=LOC,
                            resolved_time_window=WIN, previous=first,
                            required_gaps=["wind_speed"]).value
        assert second.plan_version == first.plan_version + 1
        assert second.required_evidence == ["wind_speed"]
        assert second.plan_id == first.plan_id      # same plan, new version


class TestNoProviderKnowledge:
    def test_plan_carries_no_urls_or_dataset_ids(self, registry):
        plan = plan_for(registry, "is it good for fishing near Kochi tomorrow?")
        blob = plan.model_dump_json()
        for leak in ("http", "erddap", "cmems", "zarr", "s3.", "api_key"):
            assert leak not in blob.lower(), f"planner leaked {leak!r}"


class TestRouteIntent:
    """Ordering bug: the problem statement's own route query classified as
    fishing, because '\\bfish' matched before the route pattern."""

    def test_the_problem_statements_route_query_is_a_route(self):
        q = ("What is the safest route for a fishing vessel considering "
             "weather and sea-state conditions?")
        assert PlannerAgent().classify(q) == "route_optimization"

    @pytest.mark.parametrize("q,expected", [
        ("safest route from kochi to mumbai", "route_optimization"),
        ("sail to Chennai", "route_optimization"),
        ("is it safe to venture out tomorrow?", "safety_check"),
        ("is it good for fishing near kochi?", "fishing_suitability"),
        ("is there a warning in force?", "warning_lookup"),
    ])
    def test_specificity_ordering_holds(self, q, expected):
        assert PlannerAgent().classify(q) == expected

    def test_a_route_without_a_destination_asks_rather_than_assessing(self, registry):
        """Silently assessing the origin would answer a question nobody asked."""
        plan = plan_for(registry, "plan a route", location=LOC)
        assert plan.clarification_needed in ("destination", "location")
        assert plan.steps == []

    def test_a_route_does_not_demand_a_time_window(self, registry):
        """'Plan a route to Chennai' means now."""
        loc = {**LOC, "dest_lat": 13.08, "dest_lon": 80.29}
        plan = plan_for(registry, "route from kochi to chennai",
                        location=loc, window=None)
        assert plan.clarification_needed is None
