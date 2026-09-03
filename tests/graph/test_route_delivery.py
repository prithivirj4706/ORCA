"""Route planning through the graph, end to end (offline).

The route was computed correctly for several sessions while never reaching the
client: it was attached to the Recommendation via `model_copy` while the API
read `map_layers` from graph state. Computed-but-undelivered is indistinguishable
from broken, so the delivery path is asserted here, not just the algorithm.
"""
import pytest

from backend.orca.graph.build import build_graph
from backend.orca.graph.runtime import OrcaRuntime
from backend.orca.tools.registry import ToolRegistry


def sea_only(lon, lat):
    """A crude coastline: navigable west of 78E and south of 14N."""
    return lon < 78.0 or lat < 10.5


@pytest.fixture
def runtime():
    r = ToolRegistry()
    for t in ("get_wave_conditions", "get_weather", "get_maritime_boundaries"):
        r.mark_unavailable(t, "not part of this fixture")
    return OrcaRuntime(registry=r, navigable=sea_only)


def run(runtime, query):
    return build_graph().invoke({"query_text": query},
                                config={"configurable": runtime.configurable()})


class TestRouteReachesTheClient:
    def test_a_route_produces_a_geojson_layer_in_state(self, runtime):
        final = run(runtime, "safest route from kochi to mumbai")
        layers = final.get("map_layers") or []
        assert layers, "route computed but never delivered"
        geom = layers[0]["data"]["geometry"]
        assert geom["type"] == "LineString"
        assert len(geom["coordinates"]) >= 2

    def test_the_route_value_is_a_length_not_a_waypoint_count(self, runtime):
        final = run(runtime, "safest route from kochi to mumbai")
        route = next(d for env in final["tool_results"]
                     for d in getattr(env, "data", [])
                     if getattr(d, "parameter", None) == "optimized_route")
        assert route.unit == "km"
        assert route.value > 100          # Kochi->Mumbai is ~900 km by sea

    def test_the_route_carries_a_registered_derivation(self, runtime):
        """D-8: a derived value must be recomputable from its record."""
        final = run(runtime, "safest route from kochi to mumbai")
        prov = next(p for env in final["tool_results"]
                    for p in getattr(env, "provenance", [])
                    if p.parameter == "optimized_route")
        assert prov.derivation is not None
        assert prov.derivation.method == "a_star_route"
        assert prov.derivation.method_version


class TestRouteFailureIsVisible:
    def test_routing_without_a_mask_is_declared_not_silent(self):
        """A swallowed failure meant the user got a safety answer instead."""
        r = ToolRegistry()
        for t in ("get_wave_conditions", "get_weather", "get_maritime_boundaries"):
            r.mark_unavailable(t, "stub")
        final = build_graph().invoke(
            {"query_text": "safest route from kochi to mumbai"},
            config={"configurable": OrcaRuntime(registry=r).configurable()})
        gaps = [n.get("factor") if isinstance(n, dict) else n.factor
                for n in (final.get("not_evaluated") or [])]
        assert "optimized_route" in gaps
        assert not (final.get("map_layers") or [])
