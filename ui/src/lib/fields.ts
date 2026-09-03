/* Scalar field rasterisation for the map.
 *
 * A `null` cell is masked -- land, cloud, or simply not observed. It is
 * rasterised to a TRANSPARENT pixel so a hole stays a hole. Painting it as the
 * bottom of the colour ramp would show a calm, empty sea over data that was
 * never collected, which is the one way a pretty map can undo every guarantee
 * the backend makes.
 */
import type { ORCAField } from '../types/api';

export interface FieldSpec {
  name: string;
  label: string;
  vector: boolean;
  ramp?: string[];
  radiusKm: number;
}

export const FIELD_SPECS: FieldSpec[] = [
  { name: 'wind', label: 'Wind', vector: true, radiusKm: 400 },
  { name: 'current', label: 'Currents', vector: true, radiusKm: 400 },
  { name: 'chlorophyll', label: 'Chlorophyll', vector: false, radiusKm: 250,
    ramp: ['#082f49', '#0e7490', '#14b8a6', '#a3e635', '#fde047'] },
  { name: 'sst', label: 'Sea temp', vector: false, radiusKm: 250,
    ramp: ['#1e3a8a', '#0ea5e9', '#fbbf24', '#f97316', '#dc2626'] },
  { name: 'waves', label: 'Waves', vector: false, radiusKm: 250,
    ramp: ['#052e4a', '#0369a1', '#38bdf8', '#fbbf24', '#f43f5e'] }
];

const hexToRgb = (h: string): [number, number, number] =>
  [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];

export function rampAt(ramp: string[], t: number): [number, number, number] {
  const x = Math.max(0, Math.min(1, t));
  const n = ramp.length - 1;
  const i = Math.min(Math.floor(x * n), n - 1);
  const f = x * n - i;
  const a = hexToRgb(ramp[i]);
  const b = hexToRgb(ramp[i + 1]);
  return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * f)) as [number, number, number];
}

export interface RasterResult {
  url: string;
  /** MapLibre image-source corner order: TL, TR, BR, BL. */
  coordinates: [number, number][];
}

export function rasteriseScalar(spec: FieldSpec, data: ORCAField): RasterResult | null {
  const rows = data.values;
  if (!rows || !rows.length || !rows[0].length) return null;

  const H = rows.length, W = rows[0].length;
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d');
  if (!ctx) return null;

  const img = ctx.createImageData(W, H);
  const min = data.range.min;
  const span = (data.range.max - data.range.min) || 1;
  const ramp = spec.ramp ?? ['#0ea5e9', '#fde047'];

  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const v = rows[y][x];
      const i = (y * W + x) * 4;
      if (v === null || v === undefined) { img.data[i + 3] = 0; continue; }
      const c = rampAt(ramp, (v - min) / span);
      img.data[i] = c[0]; img.data[i + 1] = c[1]; img.data[i + 2] = c[2];
      img.data[i + 3] = 205;
    }
  }
  ctx.putImageData(img, 0, 0);

  // Row 0 of the array is the FIRST latitude in the axis, which may be the
  // south edge. Flip when latitude ascends or the field renders upside down.
  const latAsc = data.lats[0] < data.lats[data.lats.length - 1];
  const north = latAsc ? data.lats[data.lats.length - 1] : data.lats[0];
  const south = latAsc ? data.lats[0] : data.lats[data.lats.length - 1];
  const west = data.lons[0];
  const east = data.lons[data.lons.length - 1];

  return {
    url: latAsc ? flipVertical(cv) : cv.toDataURL(),
    coordinates: [[west, north], [east, north], [east, south], [west, south]]
  };
}

function flipVertical(cv: HTMLCanvasElement): string {
  const out = document.createElement('canvas');
  out.width = cv.width; out.height = cv.height;
  const c = out.getContext('2d')!;
  c.translate(0, cv.height);
  c.scale(1, -1);
  c.drawImage(cv, 0, 0);
  return out.toDataURL();
}

export function legendGradient(spec: FieldSpec): string {
  return spec.vector
    ? 'linear-gradient(90deg,#56d8d6,#38bdf8,#818cf8,#fbbf24,#f43f5e)'
    : `linear-gradient(90deg,${(spec.ramp ?? []).join(',')})`;
}
