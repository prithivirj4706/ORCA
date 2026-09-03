"""Route planning (problem statement, capability 9).

The property under test is not optimality. It is that a route never crosses
land, and that when one cannot be found ORCA says so instead of straightening
the line (F-43).
"""
import pytest

from backend.orca.geospatial.routing import (
    RoutingError, a_star_route, cost_function,
)


def open_water(lon, lat):
    return True


def sea_west_of_78(lon, lat):
    """A crude continent: everything east of 78E is land."""
    return lon < 78.0


class TestNavigabilityIsRequired:
    def test_routing_without_a_mask_refuses(self):
        """The default must not be permissive: unmasked routing crossed the
        Indian peninsula in a straight line."""
        with pytest.raises(RoutingError):
            a_star_route(76.0, 9.0, 80.0, 13.0)

    def test_no_waypoint_is_ever_on_land(self):
        path = a_star_route(76.0, 9.0, 77.5, 12.0, is_navigable=sea_west_of_78,
                            resolution_deg=0.25)
        assert path
        assert all(sea_west_of_78(p[0], p[1]) for p in path)

    def test_an_unreachable_destination_returns_no_path(self):
        """Never a partial or straightened path."""
        assert a_star_route(76.0, 9.0, 85.0, 13.0, is_navigable=sea_west_of_78,
                            resolution_deg=0.5) == []


class TestEndpoints:
    def test_a_port_just_inland_is_snapped_to_water(self):
        """Harbours sit on land; without snapping every route fails at step 0."""
        path = a_star_route(78.15, 9.0, 76.0, 9.0, is_navigable=sea_west_of_78,
                            resolution_deg=0.25)
        assert path
        assert path[0][0] < 78.0

    def test_an_endpoint_far_from_water_is_refused_not_relocated(self):
        """Snapping 100 km would answer a question nobody asked."""
        assert a_star_route(79.5, 9.0, 76.0, 9.0, is_navigable=sea_west_of_78,
                            resolution_deg=0.25) == []

    def test_a_start_with_no_water_anywhere_near_returns_nothing(self):
        assert a_star_route(120.0, 9.0, 121.0, 9.0,
                            is_navigable=sea_west_of_78, resolution_deg=0.25) == []


class TestCostSteersWithoutForbidding:
    def test_calm_water_costs_nothing_extra(self):
        assert cost_function(76.0, 9.0, []) == 0.0

    def test_the_node_budget_is_enforced(self):
        """A hopeless search stops rather than hanging."""
        assert a_star_route(76.0, 9.0, 77.9, 9.0, is_navigable=open_water,
                            resolution_deg=0.01, max_nodes=50) == []
