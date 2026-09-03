"""Containment and proximity predicates.

Geometry here is synthetic on purpose: these tests check the ALGORITHM, not the
decoding of a provider's response. The adapter suite uses recorded upstream
geometry for that (tests/adapters/test_marineregions.py).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from backend.orca.geospatial.topology import (
    build_index, distance_to_ring_km, normalise_ring_longitudes, point_in_ring,
    query_longitude,
)

SQUARE = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
HOLE = [[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0], [4.0, 4.0]]


def _ring(points):
    return np.asarray(points, dtype=np.float64)


class TestRayCasting:
    def test_inside_and_outside(self):
        r = _ring(SQUARE)
        assert point_in_ring(5.0, 5.0, r) is True
        assert point_in_ring(15.0, 5.0, r) is False
        assert point_in_ring(-1.0, 5.0, r) is False
        assert point_in_ring(5.0, 15.0, r) is False

    def test_horizontal_edge_does_not_divide_by_zero(self):
        """A ray at the latitude of a horizontal edge must not raise or warn."""
        r = _ring(SQUARE)
        with np.errstate(all="raise"):
            assert point_in_ring(5.0, 0.0, r) in (True, False)
            assert point_in_ring(5.0, 10.0, r) in (True, False)

    def test_degenerate_ring_is_not_containment(self):
        assert point_in_ring(0.0, 0.0, _ring([[0.0, 0.0], [1.0, 1.0]])) is False


class TestHoles:
    def test_hole_is_excluded(self):
        index, n = build_index([{"type": "Polygon", "coordinates": [SQUARE, HOLE]}])
        assert n == 10
        assert index.contains(0, 2.0, 2.0) is True      # in the shell
        assert index.contains(0, 5.0, 5.0) is False     # in the hole
        assert index.contains(0, 20.0, 20.0) is False   # outside entirely

    def test_multipolygon_parts_are_independent(self):
        far = [[[100.0, 0.0], [101.0, 0.0], [101.0, 1.0], [100.0, 1.0],
                [100.0, 0.0]]]
        index, _ = build_index([{"type": "MultiPolygon",
                                 "coordinates": [[SQUARE], far]}])
        assert index.feature_count == 1
        assert index.contains(0, 5.0, 5.0) is True
        assert index.contains(0, 0.5, 100.5) is True
        assert index.contains(0, 0.5, 50.0) is False

    def test_unsupported_geometry_type_is_refused(self):
        with pytest.raises(ValueError, match="unsupported boundary geometry"):
            build_index([{"type": "LineString", "coordinates": [[0, 0], [1, 1]]}])


class TestAntimeridian:
    """A ring spanning +/-180 must not acquire a planet-wide bounding box."""

    RING = [[179.0, 0.0], [-179.0, 0.0], [-179.0, 2.0], [179.0, 2.0],
            [179.0, 0.0]]

    def test_longitudes_are_shifted_into_one_frame(self):
        shifted, changed = normalise_ring_longitudes(_ring(self.RING))
        assert changed is True
        assert shifted[:, 0].min() == 179.0
        assert shifted[:, 0].max() == 181.0

    def test_ordinary_ring_is_left_alone(self):
        _, changed = normalise_ring_longitudes(_ring(SQUARE))
        assert changed is False

    def test_query_is_shifted_to_match(self):
        assert query_longitude(-179.5, 181.0) == pytest.approx(180.5)
        assert query_longitude(75.0, 10.0) == 75.0

    def test_containment_across_the_antimeridian(self):
        index, _ = build_index([{"type": "Polygon", "coordinates": [self.RING]}])
        assert index.contains(0, 1.0, 179.5) is True
        assert index.contains(0, 1.0, -179.5) is True
        assert index.contains(0, 1.0, 0.0) is False


class TestDistance:
    def test_distance_is_geodesic_not_degrees(self):
        """Five degrees of longitude at the equator is ~556 km, never 5."""
        d = distance_to_ring_km(15.0, 0.0, _ring(SQUARE))
        assert d == pytest.approx(5 * 111.19, rel=0.01)

    def test_a_degree_of_longitude_shrinks_with_latitude(self):
        """The same degree offset is a shorter distance at 60 N than at 0 N."""
        ring = _ring([[0.0, 59.0], [10.0, 59.0], [10.0, 61.0], [0.0, 61.0],
                      [0.0, 59.0]])
        d = distance_to_ring_km(15.0, 60.0, ring)
        assert d < 5 * 111.19 * 0.55
        assert d == pytest.approx(5 * 111.19 * math.cos(math.radians(60.0)),
                                  rel=0.02)

    def test_distance_from_inside_is_to_the_nearest_edge(self):
        index, _ = build_index([{"type": "Polygon", "coordinates": [SQUARE]}])
        assert index.contains(0, 5.0, 1.0) is True
        assert index.distance_to_boundary_km(0, 5.0, 1.0) == pytest.approx(111.19,
                                                                           rel=0.01)

    def test_search_cap_bounds_the_answer(self):
        index, _ = build_index([{"type": "Polygon", "coordinates": [SQUARE]}])
        assert index.distance_to_boundary_km(0, 0.0, 80.0, search_km=50.0) == 50.0
