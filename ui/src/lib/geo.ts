/* Geometry helpers for the map layers: range rings, field sampling and the
 * local-median contour.
 *
 * The rule from the backend carries through all three: a `null` cell is a HOLE.
 * Sampling refuses to interpolate across one, and the contour tracer skips any
 * cell with a masked corner rather than closing the ring through it. A contour
 * drawn across missing data would assert a boundary that was never observed.
 */
import type {
  Feature, FeatureCollection, LineString, MultiLineString, Polygon
} from 'geojson';
import type { ORCAField } from '../types/api';

const R_EARTH_KM = 6371.0088;

/** A geodesic circle as a GeoJSON polygon. Used for geofence range rings. */
export function ringPolygon(lat: number, lon: number, radiusKm: number,
                            steps = 128): Feature<Polygon> {
  const d = radiusKm / R_EARTH_KM;
  const φ1 = (lat * Math.PI) / 180;
  const λ1 = (lon * Math.PI) / 180;
  const ring: [number, number][] = [];
  for (let i = 0; i <= steps; i++) {
    const θ = (i / steps) * 2 * Math.PI;
    const φ2 = Math.asin(Math.sin(φ1) * Math.cos(d) +
                         Math.cos(φ1) * Math.sin(d) * Math.cos(θ));
    const λ2 = λ1 + Math.atan2(Math.sin(θ) * Math.sin(d) * Math.cos(φ1),
                               Math.cos(d) - Math.sin(φ1) * Math.sin(φ2));
    ring.push([(λ2 * 180) / Math.PI, (φ2 * 180) / Math.PI]);
  }
  return {
    type: 'Feature',
    properties: { radius_km: radiusKm },
    geometry: { type: 'Polygon', coordinates: [ring] }
  };
}

/** Axis index and interpolation fraction for a coordinate, or null if outside. */
function locate(axis: number[], v: number): [number, number] | null {
  const asc = axis[0] < axis[axis.length - 1];
  const lo = asc ? axis[0] : axis[axis.length - 1];
  const hi = asc ? axis[axis.length - 1] : axis[0];
  if (v < lo || v > hi || axis.length < 2) return null;
  const step = (axis[axis.length - 1] - axis[0]) / (axis.length - 1);
  const f = (v - axis[0]) / step;
  const i = Math.min(Math.max(Math.floor(f), 0), axis.length - 2);
  return [i, f - i];
}

/** Bilinear sample of a scalar field. Returns null over a masked cell. */
export function sampleScalar(field: ORCAField, lon: number, lat: number): number | null {
  const rows = field.values;
  if (!rows?.length) return null;
  const y = locate(field.lats, lat);
  const x = locate(field.lons, lon);
  if (!y || !x) return null;
  const [j, fy] = y;
  const [i, fx] = x;
  const q = [rows[j]?.[i], rows[j]?.[i + 1], rows[j + 1]?.[i], rows[j + 1]?.[i + 1]];
  // A hole stays a hole: refuse the sample rather than averaging around it.
  if (q.some((v) => v === null || v === undefined)) return null;
  const [a, b, c, d] = q as number[];
  return a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy;
}

/** The median of every unmasked cell — the field's own local baseline. */
export function fieldMedian(field: ORCAField): number | null {
  const vals: number[] = [];
  for (const row of field.values ?? []) {
    for (const v of row) if (v !== null && v !== undefined) vals.push(v);
  }
  if (!vals.length) return null;
  vals.sort((a, b) => a - b);
  const m = vals.length >> 1;
  return vals.length % 2 ? vals[m] : (vals[m - 1] + vals[m]) / 2;
}

/* Marching squares over the grid, at one level.
 *
 * Emits independent segments rather than stitched rings: the map draws them as
 * a MultiLineString and the visual result is identical, without the bookkeeping
 * (and the failure modes) of joining chains across masked cells. */
export function isoline(field: ORCAField, level: number): Feature<MultiLineString> | null {
  const rows = field.values;
  if (!rows?.length || rows.length < 2 || rows[0].length < 2) return null;
  const { lats, lons } = field;
  const segs: [number, number][][] = [];

  const pt = (i: number, j: number, i2: number, j2: number,
              v1: number, v2: number): [number, number] => {
    const t = (level - v1) / (v2 - v1 || 1);
    return [lons[i] + (lons[i2] - lons[i]) * t, lats[j] + (lats[j2] - lats[j]) * t];
  };

  for (let j = 0; j < rows.length - 1; j++) {
    for (let i = 0; i < rows[j].length - 1; i++) {
      const tl = rows[j][i], tr = rows[j][i + 1];
      const bl = rows[j + 1][i], br = rows[j + 1][i + 1];
      // Any masked corner and the cell is skipped: no contour is drawn through
      // data that was never collected.
      if ([tl, tr, bl, br].some((v) => v === null || v === undefined)) continue;
      const a = tl as number, b = tr as number, c = br as number, d = bl as number;

      const idx = (a > level ? 8 : 0) | (b > level ? 4 : 0) |
                  (c > level ? 2 : 0) | (d > level ? 1 : 0);
      if (idx === 0 || idx === 15) continue;

      const top = () => pt(i, j, i + 1, j, a, b);
      const right = () => pt(i + 1, j, i + 1, j + 1, b, c);
      const bottom = () => pt(i, j + 1, i + 1, j + 1, d, c);
      const left = () => pt(i, j, i, j + 1, a, d);

      const push = (p: [number, number], q: [number, number]) => segs.push([p, q]);
      switch (idx) {
        case 1: case 14: push(left(), bottom()); break;
        case 2: case 13: push(bottom(), right()); break;
        case 3: case 12: push(left(), right()); break;
        case 4: case 11: push(top(), right()); break;
        case 5: push(left(), top()); push(bottom(), right()); break;
        case 6: case 9: push(top(), bottom()); break;
        case 7: case 8: push(left(), top()); break;
        case 10: push(left(), bottom()); push(top(), right()); break;
      }
    }
  }
  if (!segs.length) return null;
  return {
    type: 'Feature',
    properties: { level },
    geometry: { type: 'MultiLineString', coordinates: segs }
  };
}

/* Split a route into segments, each carrying the field value sampled at its
 * midpoint. A segment over a hole carries null and is drawn unpainted rather
 * than being given the value of its neighbour. */
export function tintRoute(coords: [number, number][], field: ORCAField | null):
    FeatureCollection<LineString> {
  const features: Feature<LineString>[] = [];
  for (let i = 0; i < coords.length - 1; i++) {
    const [x1, y1] = coords[i];
    const [x2, y2] = coords[i + 1];
    const v = field ? sampleScalar(field, (x1 + x2) / 2, (y1 + y2) / 2) : null;
    features.push({
      type: 'Feature',
      properties: { v, known: v !== null },
      geometry: { type: 'LineString', coordinates: [coords[i], coords[i + 1]] }
    });
  }
  return { type: 'FeatureCollection', features };
}
