/* Animated vector-field particle layer (wind, currents).
 *
 * Particles are advected through the u/v grid the API returns and leave a
 * fading trail, which is what makes a static field read as motion.
 *
 * The one rule that matters: a cell the API returned as null is a HOLE. A
 * particle entering one is respawned rather than carried on a velocity of
 * zero -- drawing zero where there is no data would show a calm sea over
 * missing information, which is the thing the backend refuses to do.
 */
import type { Map as MapLibreMap } from 'maplibre-gl';
import type { ORCAField } from '../types/api';

type RGB = [number, number, number];

// Speed -> colour. Cool teal through cyan to warm amber/rose at gale force.
const RAMP: [number, RGB][] = [
  [0.0, [86, 216, 214]],
  [0.3, [56, 189, 248]],
  [0.5, [129, 140, 248]],
  [0.7, [251, 191, 36]],
  [1.0, [244, 63, 94]]
];

export function rampColour(t: number): RGB {
  const x = Math.max(0, Math.min(1, t));
  for (let i = 1; i < RAMP.length; i++) {
    if (x <= RAMP[i][0]) {
      const [t0, c0] = RAMP[i - 1];
      const [t1, c1] = RAMP[i];
      const f = (x - t0) / (t1 - t0 || 1);
      return [
        Math.round(c0[0] + (c1[0] - c0[0]) * f),
        Math.round(c0[1] + (c1[1] - c0[1]) * f),
        Math.round(c0[2] + (c1[2] - c0[2]) * f)
      ];
    }
  }
  return RAMP[RAMP.length - 1][1];
}

class VectorField {
  lats: number[];
  lons: number[];
  u: (number | null)[][];
  v: (number | null)[][];
  max: number;
  private lat0: number;
  private lon0: number;
  private dLat: number;
  private dLon: number;

  constructor(data: ORCAField) {
    this.lats = data.lats;
    this.lons = data.lons;
    this.u = data.u ?? [];
    this.v = data.v ?? [];
    this.max = Math.max(data.range.max, 0.1);
    // Latitude may be published ascending or descending; normalise the lookup
    // rather than assuming, or the field renders upside down.
    this.lat0 = this.lats[0];
    this.lon0 = this.lons[0];
    this.dLat = this.lats.length > 1 ? this.lats[1] - this.lats[0] : 1;
    this.dLon = this.lons.length > 1 ? this.lons[1] - this.lons[0] : 1;
  }

  bounds() {
    const la = this.lats, lo = this.lons;
    return {
      minLat: Math.min(la[0], la[la.length - 1]),
      maxLat: Math.max(la[0], la[la.length - 1]),
      minLon: Math.min(lo[0], lo[lo.length - 1]),
      maxLon: Math.max(lo[0], lo[lo.length - 1])
    };
  }

  /** Bilinear sample. `null` inside a hole or outside the grid — the caller
   *  must treat that as "no data", never as still air. */
  sample(lon: number, lat: number): [number, number] | null {
    const y = (lat - this.lat0) / this.dLat;
    const x = (lon - this.lon0) / this.dLon;
    if (!(y >= 0 && x >= 0 && y <= this.lats.length - 1 && x <= this.lons.length - 1)) {
      return null;
    }
    const y0 = Math.floor(y), x0 = Math.floor(x);
    const y1 = Math.min(y0 + 1, this.lats.length - 1);
    const x1 = Math.min(x0 + 1, this.lons.length - 1);
    const fy = y - y0, fx = x - x0;

    const uq = [this.u[y0]?.[x0], this.u[y0]?.[x1], this.u[y1]?.[x0], this.u[y1]?.[x1]];
    const vq = [this.v[y0]?.[x0], this.v[y0]?.[x1], this.v[y1]?.[x0], this.v[y1]?.[x1]];
    // Any corner missing => the edge of a hole. Respawn; do not fake.
    for (let i = 0; i < 4; i++) {
      if (uq[i] == null || vq[i] == null) return null;
    }
    const lerp = (q: (number | null | undefined)[]) =>
      (q[0] as number) * (1 - fx) * (1 - fy) + (q[1] as number) * fx * (1 - fy) +
      (q[2] as number) * (1 - fx) * fy + (q[3] as number) * fx * fy;
    return [lerp(uq), lerp(vq)];
  }
}

interface Particle { lon: number; lat: number; age: number }

export class ParticleLayer {
  private ctx: CanvasRenderingContext2D;
  private field: VectorField | null = null;
  private particles: Particle[] = [];
  private raf: number | null = null;
  private running = false;
  private count = 2400;
  private maxAge = 90;
  private speedScale = 0.055;

  private canvas: HTMLCanvasElement;
  private map: MapLibreMap;

  constructor(canvas: HTMLCanvasElement, map: MapLibreMap) {
    this.canvas = canvas;
    this.map = map;
    this.ctx = canvas.getContext('2d')!;
    this.resize = this.resize.bind(this);
    this.frame = this.frame.bind(this);
    window.addEventListener('resize', this.resize);
    this.onMove = () => this.clear();
    map.on('move', this.onMove);
    this.resize();
  }

  private onMove: () => void;

  destroy() {
    this.stop();
    window.removeEventListener('resize', this.resize);
    try { this.map.off('move', this.onMove); } catch { /* map already gone */ }
  }

  private resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = window.innerWidth * dpr;
    this.canvas.height = window.innerHeight * dpr;
    this.canvas.style.width = `${window.innerWidth}px`;
    this.canvas.style.height = `${window.innerHeight}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  private clear() {
    this.ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  }

  set(data: ORCAField) {
    this.field = new VectorField(data);
    this.particles = [];
    for (let i = 0; i < Math.min(this.count, 3200); i++) {
      this.particles.push(this.spawn());
    }
    this.start();
  }

  private spawn(): Particle {
    const b = this.field!.bounds();
    return {
      lon: b.minLon + Math.random() * (b.maxLon - b.minLon),
      lat: b.minLat + Math.random() * (b.maxLat - b.minLat),
      age: Math.floor(Math.random() * this.maxAge)
    };
  }

  start() {
    if (this.running || !this.field) return;
    this.running = true;
    this.raf = requestAnimationFrame(this.frame);
  }

  stop() {
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = null;
    this.clear();
  }

  private frame() {
    if (!this.running || !this.field) return;
    const ctx = this.ctx;
    const W = window.innerWidth, H = window.innerHeight;

    // Fade the previous frame instead of clearing: this is the trail.
    ctx.globalCompositeOperation = 'destination-out';
    ctx.fillStyle = 'rgba(0,0,0,0.082)';
    ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = 'lighter';
    ctx.lineWidth = 1.15;
    ctx.lineCap = 'round';

    for (const p of this.particles) {
      const vel = this.field.sample(p.lon, p.lat);
      if (vel === null || p.age++ > this.maxAge) {
        Object.assign(p, this.spawn());
        continue;
      }
      const [u, v] = vel;
      const speed = Math.hypot(u, v);

      const before = this.map.project([p.lon, p.lat]);
      // Longitude degrees shrink with latitude; without this the flow skews.
      const coslat = Math.max(Math.cos((p.lat * Math.PI) / 180), 0.15);
      p.lon += ((u * this.speedScale) / (111.32 * coslat)) * 60;
      p.lat += ((v * this.speedScale) / 110.57) * 60;
      const after = this.map.project([p.lon, p.lat]);

      if (after.x < -60 || after.x > W + 60 || after.y < -60 || after.y > H + 60) continue;

      const c = rampColour(speed / this.field.max);
      ctx.strokeStyle = `rgba(${c[0]},${c[1]},${c[2]},0.82)`;
      ctx.beginPath();
      ctx.moveTo(before.x, before.y);
      ctx.lineTo(after.x, after.y);
      ctx.stroke();
    }
    this.raf = requestAnimationFrame(this.frame);
  }
}
