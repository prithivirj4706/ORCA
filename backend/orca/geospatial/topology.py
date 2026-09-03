"""Point-in-polygon and boundary-proximity predicates.

These are the deterministic core of the REGULATORY domain. They run against
FULL-PRECISION geometry: simplification is permitted for display only, never
for a containment test (11_GEOSPATIAL_REASONING_SPEC.md section 13).

Geometry is held flat -- one coordinate array per layer, with ring, polygon and
feature offsets into it -- so a query touches only the rings whose bounding box
can possibly contain the point. Nothing here knows where the geometry came
from; the adapter loads it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..schemas.core import haversine_km

#: Ray casting is exact for a simple ring, so the only tolerance that matters is
#: the one we report: how close to an edge a point may be before "inside" should
#: not be stated with full confidence.
DEFAULT_NEAR_BOUNDARY_KM = 5.0

#: Longitude span above which a ring is treated as crossing the antimeridian.
_ANTIMERIDIAN_SPAN_DEG = 180.0

_KY_KM_PER_DEG = 110.574          # metres per degree latitude, as km
_KX_KM_PER_DEG = 111.320          # at the equator; scaled by cos(lat)


def normalise_ring_longitudes(ring: np.ndarray) -> tuple[np.ndarray, bool]:
    """Shift a ring that crosses the antimeridian into a continuous 0..360 frame.

    A ring spanning Fiji or Kiribati has longitudes at both -179 and +179. Left
    alone, its bounding box spans the whole planet and every ray-casting test
    against it is wrong. Shifting the negative longitudes by +360 makes the ring
    contiguous; queries are shifted the same way (`query_longitude`).

    Returns (ring, shifted).
    """
    lons = ring[:, 0]
    if float(lons.max() - lons.min()) <= _ANTIMERIDIAN_SPAN_DEG:
        return ring, False
    out = ring.copy()
    out[:, 0] = np.where(out[:, 0] < 0.0, out[:, 0] + 360.0, out[:, 0])
    return out, True


def query_longitude(lon: float, ring_max_lon: float) -> float:
    """Express a query longitude in the same frame as a stored ring."""
    if ring_max_lon > 180.0 and lon < 0.0:
        return lon + 360.0
    return lon


def point_in_ring(lon: float, lat: float, ring: np.ndarray) -> bool:
    """Even-odd ray casting. `ring` is (N, 2) as [lon, lat].

    A point exactly on an edge is not given a defined answer by this test --
    which is precisely why `distance_to_ring_km` is reported alongside every
    containment result rather than left implicit.
    """
    if ring.shape[0] < 3:
        return False
    x1 = ring[:, 0]
    y1 = ring[:, 1]
    x2 = np.roll(x1, -1)
    y2 = np.roll(y1, -1)
    straddles = (y1 > lat) != (y2 > lat)
    if not straddles.any():
        return False
    # Evaluate the edge intersection only where the edge straddles the ray, so
    # horizontal edges never divide by zero.
    xa, ya, xb, yb = x1[straddles], y1[straddles], x2[straddles], y2[straddles]
    x_cross = xa + (lat - ya) * (xb - xa) / (yb - ya)
    return bool(np.count_nonzero(lon < x_cross) % 2 == 1)


def distance_to_ring_km(lon: float, lat: float, ring: np.ndarray) -> float:
    """Geodesic distance from a point to the nearest edge of a ring.

    The nearest point on each segment is found in a local equirectangular
    projection (valid over the few hundred kilometres that matter here); the
    distance finally reported is the great-circle distance to that point, so the
    number is geodesic even though the search is planar.
    """
    if ring.shape[0] < 2:
        return float("inf")
    kx = _KX_KM_PER_DEG * math.cos(math.radians(lat))
    ky = _KY_KM_PER_DEG

    px = (ring[:, 0] - lon) * kx
    py = (ring[:, 1] - lat) * ky
    qx = np.roll(px, -1)
    qy = np.roll(py, -1)

    dx = qx - px
    dy = qy - py
    seg_len2 = dx * dx + dy * dy
    with np.errstate(invalid="ignore", divide="ignore"):
        t = np.where(seg_len2 > 0.0, -(px * dx + py * dy) / seg_len2, 0.0)
    t = np.clip(np.nan_to_num(t), 0.0, 1.0)
    cx = px + t * dx
    cy = py + t * dy
    i = int(np.argmin(cx * cx + cy * cy))

    near_lon = lon + float(cx[i]) / kx if kx != 0 else lon
    near_lat = lat + float(cy[i]) / ky
    return haversine_km(lat, lon, near_lat, near_lon)


def distance_to_line_km(lon: float, lat: float, line: np.ndarray) -> float:
    """Geodesic distance from a point to an OPEN polyline.

    `distance_to_ring_km` closes the ring with `np.roll`, which is right for a
    polygon boundary and wrong for a line: it invents a segment from the last
    vertex back to the first. A PFZ advisory is an open line, and that phantom
    segment can run hundreds of kilometres across open sea, so a point near it
    would be reported as near an advisory that does not exist there.
    """
    if line.shape[0] < 2:
        return float("inf")
    kx = _KX_KM_PER_DEG * math.cos(math.radians(lat))
    ky = _KY_KM_PER_DEG

    px = (line[:-1, 0] - lon) * kx
    py = (line[:-1, 1] - lat) * ky
    qx = (line[1:, 0] - lon) * kx
    qy = (line[1:, 1] - lat) * ky

    dx = qx - px
    dy = qy - py
    seg_len2 = dx * dx + dy * dy
    with np.errstate(invalid="ignore", divide="ignore"):
        t = np.where(seg_len2 > 0.0, -(px * dx + py * dy) / seg_len2, 0.0)
    t = np.clip(np.nan_to_num(t), 0.0, 1.0)
    cx = px + t * dx
    cy = py + t * dy
    i = int(np.argmin(cx * cx + cy * cy))

    near_lon = lon + float(cx[i]) / kx if kx != 0 else lon
    near_lat = lat + float(cy[i]) / ky
    return haversine_km(lat, lon, near_lat, near_lon)


@dataclass(slots=True)
class FeatureIndex:
    """Flat, immutable geometry for one boundary layer.

    coords            (N, 2) float64, [lon, lat]
    ring_offsets      (R + 1,) int64 -- ring r is coords[ring_offsets[r]:...+1]
    ring_bbox         (R, 4) float64 -- [min_lon, min_lat, max_lon, max_lat]
    polygon_offsets   (P + 1,) int64 -- polygon p owns rings [off[p]:off[p+1]);
                                        the first is the exterior, the rest holes
    feature_offsets   (F + 1,) int64 -- feature f owns polygons [off[f]:off[f+1])
    """
    coords: np.ndarray
    ring_offsets: np.ndarray
    ring_bbox: np.ndarray
    polygon_offsets: np.ndarray
    feature_offsets: np.ndarray

    @property
    def feature_count(self) -> int:
        return int(self.feature_offsets.shape[0] - 1)

    def ring(self, r: int) -> np.ndarray:
        return self.coords[self.ring_offsets[r]:self.ring_offsets[r + 1]]

    def _ring_bbox_contains(self, r: int, lat: float, lon: float) -> bool:
        min_lon, min_lat, max_lon, max_lat = self.ring_bbox[r]
        x = query_longitude(lon, float(max_lon))
        return bool(min_lat <= lat <= max_lat and min_lon <= x <= max_lon)

    def contains(self, feature: int, lat: float, lon: float) -> bool:
        """True when the point lies inside the feature, holes excluded."""
        p0, p1 = self.feature_offsets[feature], self.feature_offsets[feature + 1]
        for p in range(int(p0), int(p1)):
            r0, r1 = int(self.polygon_offsets[p]), int(self.polygon_offsets[p + 1])
            if r1 <= r0 or not self._ring_bbox_contains(r0, lat, lon):
                continue
            shell = self.ring(r0)
            x = query_longitude(lon, float(self.ring_bbox[r0][2]))
            if not point_in_ring(x, lat, shell):
                continue
            in_hole = False
            for r in range(r0 + 1, r1):
                if not self._ring_bbox_contains(r, lat, lon):
                    continue
                xh = query_longitude(lon, float(self.ring_bbox[r][2]))
                if point_in_ring(xh, lat, self.ring(r)):
                    in_hole = True
                    break
            if not in_hole:
                return True
        return False

    def distance_to_boundary_km(self, feature: int, lat: float, lon: float,
                                *, search_km: float | None = None) -> float:
        """Distance to the nearest edge of a feature, in kilometres.

        Rings whose bounding box is already further away than the best distance
        found so far are skipped, which keeps a query against a 70,000-vertex
        national boundary cheap.
        """
        best = float("inf") if search_km is None else float(search_km)
        p0, p1 = self.feature_offsets[feature], self.feature_offsets[feature + 1]
        for p in range(int(p0), int(p1)):
            r0, r1 = int(self.polygon_offsets[p]), int(self.polygon_offsets[p + 1])
            for r in range(r0, r1):
                if self._bbox_distance_km(r, lat, lon) > best:
                    continue
                d = distance_to_ring_km(
                    query_longitude(lon, float(self.ring_bbox[r][2])), lat,
                    self.ring(r))
                best = min(best, d)
        return best

    def _bbox_distance_km(self, r: int, lat: float, lon: float) -> float:
        """Lower bound on the distance from a point to a ring's bounding box."""
        min_lon, min_lat, max_lon, max_lat = self.ring_bbox[r]
        x = query_longitude(lon, float(max_lon))
        dlat = max(float(min_lat) - lat, 0.0, lat - float(max_lat))
        dlon = max(float(min_lon) - x, 0.0, x - float(max_lon))
        kx = _KX_KM_PER_DEG * math.cos(math.radians(lat))
        return math.hypot(dlat * _KY_KM_PER_DEG, dlon * kx)

    def features_containing(self, lat: float, lon: float) -> list[int]:
        """Every feature containing the point. Overlaps are NOT adjudicated."""
        return [f for f in range(self.feature_count) if self.contains(f, lat, lon)]


def build_index(geometries: list[dict]) -> tuple[FeatureIndex, int]:
    """Flatten GeoJSON Polygon/MultiPolygon geometries into a FeatureIndex.

    Returns (index, vertex_count). Used by the capture script; the runtime reads
    the flattened arrays back from the snapshot rather than re-parsing GeoJSON.
    """
    coords: list[np.ndarray] = []
    ring_offsets = [0]
    ring_bbox: list[list[float]] = []
    polygon_offsets = [0]
    feature_offsets = [0]
    n = 0

    for geom in geometries:
        polys: list[list[list[list[float]]]]
        if geom is None:
            polys = []
        elif geom["type"] == "Polygon":
            polys = [geom["coordinates"]]
        elif geom["type"] == "MultiPolygon":
            polys = geom["coordinates"]
        else:
            raise ValueError(f"unsupported boundary geometry type {geom['type']!r}")
        for poly in polys:
            for ring in poly:
                arr = np.asarray(ring, dtype=np.float64)[:, :2]
                arr, _ = normalise_ring_longitudes(arr)
                coords.append(arr)
                n += arr.shape[0]
                ring_offsets.append(n)
                ring_bbox.append([float(arr[:, 0].min()), float(arr[:, 1].min()),
                                  float(arr[:, 0].max()), float(arr[:, 1].max())])
            polygon_offsets.append(len(ring_offsets) - 1)
        feature_offsets.append(len(polygon_offsets) - 1)

    index = FeatureIndex(
        coords=(np.concatenate(coords) if coords
                else np.zeros((0, 2), dtype=np.float64)),
        ring_offsets=np.asarray(ring_offsets, dtype=np.int64),
        ring_bbox=(np.asarray(ring_bbox, dtype=np.float64) if ring_bbox
                   else np.zeros((0, 4), dtype=np.float64)),
        polygon_offsets=np.asarray(polygon_offsets, dtype=np.int64),
        feature_offsets=np.asarray(feature_offsets, dtype=np.int64),
    )
    return index, n
