"""Route planning over navigable water (problem statement, capability 9).

A* on a lat/lon grid. Two properties matter more than optimality:

  * **A route never crosses land.** Navigability is a REQUIRED input, not an
    optional refinement. Without it A* returns the straight line between two
    ports, which for Kochi to Chennai runs over the Western Ghats. A plausible
    line on a map is the most dangerous failure this system can produce, so
    `is_navigable` has no permissive default -- omit it and routing refuses.
  * **Failure is explicit.** No path within the node budget returns an empty
    path, which the caller reports as a gap. It never returns a partial or
    straightened path.

Field penalties (wave height, wind) steer the route within navigable water.
They are refinements; the mask is the safety property.
"""
from __future__ import annotations

import heapq
import math
from typing import Callable, Iterable

from ..schemas.data import OceanField

#: Expanded-node ceiling. A long coastal route around Sri Lanka is a few
#: thousand nodes; this bounds the worst case rather than hanging.
MAX_NODES = 60_000

_NEIGHBOURS = [(-1, -1), (-1, 0), (-1, 1), (0, -1),
               (0, 1), (1, -1), (1, 0), (1, 1)]


class RoutingError(Exception):
    """Raised when routing is asked for without the inputs that make it safe."""


def extract_field_values(lon: float, lat: float,
                         fields: Iterable[OceanField]) -> dict[str, float]:
    """Nearest-cell lookup into any inline gridded fields supplied."""
    out: dict[str, float] = {}
    for f in fields or ():
        if not (getattr(f, "values_inline", None) and getattr(f, "spatial", None)
                and getattr(f.spatial, "bbox", None)):
            continue
        bbox = f.spatial.bbox
        rows = f.values_inline
        n_lat = len(rows)
        if n_lat == 0 or not rows[0]:
            continue
        n_lon = len(rows[0])
        dlat = (bbox.max_lat - bbox.min_lat) / n_lat
        dlon = (bbox.max_lon - bbox.min_lon) / n_lon
        if dlat == 0 or dlon == 0:
            continue
        ilat = int((lat - bbox.min_lat) / dlat)
        ilon = int((lon - bbox.min_lon) / dlon)
        if 0 <= ilat < n_lat and 0 <= ilon < n_lon:
            val = rows[ilat][ilon]
            if val is not None:
                out[f.parameter] = val
    return out


def cost_function(lon: float, lat: float, fields: Iterable[OceanField]) -> float:
    """Traversal penalty in kilometre-equivalents, from `small_craft_v0.1`.

    Rough water is expensive, not forbidden: a route may cross a marginal patch
    if the detour is worse. Thresholds mirror the safety threshold set and carry
    the same SCIENTIFIC_VALIDATION_REQUIRED status.
    """
    vals = extract_field_values(lon, lat, fields)
    penalty = 0.0
    hs = vals.get("significant_wave_height")
    wind = vals.get("wind_speed")
    if hs is not None and hs > 1.5:
        penalty += 50.0 * (hs - 1.5)
    if wind is not None and wind > 12.0:
        penalty += 20.0 * (wind - 12.0)
    return penalty


def _km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    kx = 111.320 * math.cos(math.radians((lat1 + lat2) / 2.0))
    ky = 110.574
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * ky)


#: How far an endpoint may be moved to reach water. A harbour sits a few
#: kilometres from navigable water; anything further is a different place, and
#: silently routing to it would answer a question nobody asked.
MAX_SNAP_KM = 30.0


def _snap_to_water(lon: float, lat: float, is_navigable, step: float,
                   rings: int = 25,
                   max_snap_km: float = MAX_SNAP_KM) -> tuple[float, float] | None:
    """Ports are on land. Move the endpoint to the nearest navigable cell.

    Bounded: beyond `max_snap_km` the endpoint is not a harbour approach, it is
    somewhere else, and routing refuses rather than relocating the request.
    """
    if is_navigable(lon, lat):
        return lon, lat
    for r in range(1, rings + 1):
        best = None
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                clon, clat = lon + dx * step, lat + dy * step
                if is_navigable(clon, clat):
                    d = _km(lon, lat, clon, clat)
                    if best is None or d < best[0]:
                        best = (d, clon, clat)
        if best:
            return (best[1], best[2]) if best[0] <= max_snap_km else None
    return None


def a_star_route(start_lon: float, start_lat: float,
                 end_lon: float, end_lat: float,
                 fields: Iterable[OceanField] | None = None,
                 *,
                 is_navigable: Callable[[float, float], bool] | None = None,
                 resolution_deg: float = 0.1,
                 max_nodes: int = MAX_NODES) -> list[list[float]]:
    """A navigable path as [[lon, lat], ...], or [] if none was found.

    `is_navigable` is REQUIRED. Routing without a land mask produced a straight
    line across the Indian peninsula (F-43), so there is no permissive default.
    """
    if is_navigable is None:
        raise RoutingError(
            "a_star_route requires is_navigable: without it a route will cross "
            "land. Supply a sea mask or do not offer routing.")

    step = resolution_deg
    fields = list(fields or ())

    start = _snap_to_water(start_lon, start_lat, is_navigable, step)
    goal = _snap_to_water(end_lon, end_lat, is_navigable, step)
    if start is None or goal is None:
        return []

    def key(lon, lat):
        return (round(lon / step), round(lat / step))

    start_k, goal_k = key(*start), key(*goal)
    pos = {start_k: start, goal_k: goal}

    def h(k):
        a, b = pos.get(k, (k[0] * step, k[1] * step)), goal
        return _km(a[0], a[1], b[0], b[1])

    open_heap = [(h(start_k), 0.0, start_k)]
    came: dict = {}
    g = {start_k: 0.0}
    seen = set()
    expanded = 0

    while open_heap:
        _, gc, k = heapq.heappop(open_heap)
        if k in seen:
            continue
        seen.add(k)
        expanded += 1
        if expanded > max_nodes:
            return []                        # honest failure, never a shortcut
        if k == goal_k:
            path, cur = [], k
            while cur in came:
                path.append(list(pos[cur]))
                cur = came[cur]
            path.append(list(pos[start_k]))
            path.reverse()
            return path

        clon, clat = pos[k]
        for dx, dy in _NEIGHBOURS:
            nlon, nlat = clon + dx * step, clat + dy * step
            nk = key(nlon, nlat)
            if nk in seen or not is_navigable(nlon, nlat):
                continue
            cost = _km(clon, clat, nlon, nlat) + cost_function(nlon, nlat, fields)
            ng = gc + cost
            if ng < g.get(nk, float("inf")):
                g[nk] = ng
                came[nk] = k
                pos[nk] = (nlon, nlat)
                heapq.heappush(open_heap, (ng + h(nk), ng, nk))
    return []
