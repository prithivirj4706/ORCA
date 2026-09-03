"""Registry of deterministic methods.

Every derived value records the method id and version that produced it, so the
number can be recomputed later (05_CANONICAL_DATA_SCHEMA.md section 16).
A method may not change behaviour without a version bump and a fixture update.
"""
from __future__ import annotations

from typing import Any

from ..schemas.core import Derivation

METHODS: dict[str, str] = {
    "haversine_distance": "1.0",
    "bbox_from_point_radius": "1.0",
    "bbox_area": "1.0",
    "nearest_node_extraction": "1.0",
    "vector_magnitude_direction": "1.0",
    "anomaly_vs_window_mean": "1.0",
    "field_area_statistics": "1.0",
    "ratio_to_local_median": "1.0",
    "temporal_alignment": "1.0",
    "point_in_polygon": "1.0",
    "distance_to_advisory_line": "1.0",
    "a_star_route": "1.0",
}

_CODE_REF = "orca.geospatial"


class UnknownMethod(KeyError):
    pass


def derivation(method: str, inputs: list[str], params: dict[str, Any] | None = None,
               module: str | None = None) -> Derivation:
    if method not in METHODS:
        raise UnknownMethod(f"method {method!r} is not registered")
    return Derivation(
        method=method,
        method_version=METHODS[method],
        inputs=list(inputs),
        params=params or {},
        code_reference=f"{_CODE_REF}.{module or method}:{method}",
    )
