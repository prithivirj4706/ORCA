"""Gridded fields actually steer the route, and say so when they cannot.

The defect this guards against is specific and hard to see: the cost function
existed, was correct, and was called thousands of times per route — always with
an empty field list, because `tool_results` carries point values and never
grids. Every route was therefore a shortest path while looking like an optimised
one, and nothing in the answer distinguished the two.

So there are two properties here, and the second matters as much as the first:
fields must reach the router, and a route that had none must SAY it had none.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.orca.geospatial.routing import a_star_route, extract_field_values, _km
from backend.orca.graph.nodes.analysis import _corridor_radius_km
from backend.orca.tools.fields import as_ocean_field, route_fields

UTC = timezone.utc
NOW = datetime(2026, 9, 4, 0, 0, tzinfo=UTC)


def payload(lats, values, lons=(70.0, 71.0)):
    return {"lats": list(lats), "lons": list(lons), "values": values,
            "valid_time": NOW.isoformat(), "unit": "m", "source": "TEST",
            "source_id": "S-TEST", "dataset": "ds", "field": "waves",
            "cells": {}, "range": {}}


class TestLatitudeOrdering:
    """Row 0 must be the SOUTHERNMOST latitude.

    `extract_field_values` indexes from `bbox.min_lat`, so a grid published
    north-first must be flipped. Getting this wrong applies every penalty to the
    mirror image of the sea it was measured in — which is worse than no penalty,
    because the route still looks weather-aware.
    """

    NORTH_FIRST = payload([12.0, 11.0, 10.0], [[9.0, 9.0], [5.0, 5.0], [1.0, 1.0]])
    SOUTH_FIRST = payload([10.0, 11.0, 12.0], [[1.0, 1.0], [5.0, 5.0], [9.0, 9.0]])

    @pytest.mark.parametrize("name,p", [("north-first", NORTH_FIRST),
                                        ("south-first", SOUTH_FIRST)])
    def test_a_value_is_sampled_at_the_latitude_it_was_published_for(self, name, p):
        f, _ = as_ocean_field(p, "significant_wave_height", provenance_id="pv-t")
        north = extract_field_values(70.5, 11.9, [f])["significant_wave_height"]
        south = extract_field_values(70.5, 10.1, [f])["significant_wave_height"]
        assert (north, south) == (9.0, 1.0), f"{name} grid sampled upside down"

    def test_provenance_travels_with_the_grid(self):
        f, prov = as_ocean_field(self.SOUTH_FIRST, "significant_wave_height",
                                 provenance_id="pv-t")
        assert prov.provenance_id == f.provenance_id == "pv-t"
        assert prov.source_id == "S-TEST"


class TestFieldsChangeTheRoute:
    """A field that makes the direct line expensive must move the route."""

    @staticmethod
    def open_water(lon, lat):
        return 6.0 <= lat <= 16.0 and 74.0 <= lon <= 82.0

    def _rough_band(self):
        # 4 m seas across the direct corridor, calm elsewhere.
        nlat, nlon = 40, 40
        rows = [[0.5] * nlon for _ in range(nlat)]
        for j in range(nlat):
            lat = 6.0 + (j + 0.5) * (10.0 / nlat)
            if 9.5 < lat < 11.5:
                rows[j] = [4.0] * nlon
        f, _ = as_ocean_field(
            payload([6.0 + (j + 0.5) * (10.0 / nlat) for j in range(nlat)], rows,
                    lons=[74.0 + (i + 0.5) * (8.0 / nlon) for i in range(nlon)]),
            "significant_wave_height", provenance_id="pv-band")
        return f

    def test_a_rough_band_forces_a_detour(self):
        a = dict(start_lon=75.0, start_lat=10.5, end_lon=81.0, end_lat=10.5,
                 is_navigable=self.open_water)
        plain = a_star_route(**a, fields=[])
        steered = a_star_route(**a, fields=[self._rough_band()])
        assert plain and steered
        assert steered != plain, "the wave field did not change the route"
        length = lambda p: sum(_km(p[i][0], p[i][1], p[i + 1][0], p[i + 1][1])
                               for i in range(len(p) - 1))
        assert length(steered) > length(plain), "a detour must be longer"

    def test_the_detour_leaves_the_rough_water(self):
        band = self._rough_band()
        steered = a_star_route(start_lon=75.0, start_lat=10.5, end_lon=81.0,
                               end_lat=10.5, fields=[band],
                               is_navigable=self.open_water)
        rough = sum(1 for lon, lat in steered
                    if (extract_field_values(lon, lat, [band])
                        .get("significant_wave_height", 0) or 0) > 3.0)
        assert rough < len(steered) / 2, "the route stayed in the rough band"


class TestDegradationIsDeclared:
    def test_no_adapter_reports_each_field_with_a_reason(self):
        """Silence here is the bug: the route would be distance-only and mute."""
        fields, prov, gaps = route_fields(11.5, 78.2, NOW, radius_km=400,
                                          cmems=None, gfs=None)
        assert fields == [] and prov == []
        assert {g["parameter"] for g in gaps} == {"significant_wave_height",
                                                  "wind_speed"}
        assert all(g["reason"] and g["detail"] for g in gaps)

    def test_a_raising_adapter_is_reported_not_propagated(self):
        class Boom:
            def fetch_grid(self, *a, **k):
                raise RuntimeError("network gone")
        fields, _, gaps = route_fields(11.5, 78.2, NOW, radius_km=400,
                                       cmems=Boom(), gfs=Boom())
        assert fields == []
        assert all(g["reason"] == "ADAPTER_ERROR" for g in gaps)


class TestCorridorRadius:
    def test_it_covers_more_than_the_straight_line(self):
        """A field stopping at the straight line goes blind mid-detour."""
        half = _km(76.26, 9.93, 80.29, 13.08) / 2
        assert _corridor_radius_km(9.93, 76.26, 13.08, 80.29) > half

    def test_it_is_clamped_at_both_ends(self):
        assert _corridor_radius_km(9.9, 76.2, 9.91, 76.21) >= 150.0
        assert _corridor_radius_km(-40.0, 20.0, 40.0, 120.0) <= 800.0
