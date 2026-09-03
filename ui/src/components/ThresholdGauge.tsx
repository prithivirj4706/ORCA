/* A driver drawn against the bands it was actually judged against.
 *
 * The API now returns each numeric factor's band EDGES, so the pin sits at its
 * true position on a real axis and the band widths are the real widths. Before
 * that the bands were drawn equal-width with the pin at its band's centre --
 * honest, because inventing an axis would be inventing a fact, but it could not
 * show that 1.26 m is at the very top of "favourable" while 1.49 m is about to
 * stop being favourable at all.
 *
 * The edges are still optional. A boolean has no axis, and a factor whose
 * thresholds are not numeric ranges has none either, so the equal-width
 * fallback stays rather than being replaced.
 */
import type { BandEdges, ORCADriver } from '../types/api';

const BAND_COLOUR: Record<string, string> = {
  favourable: '#34d399', marginal: '#fbbf24',
  unfavourable: '#fb923c', unsafe: '#f43f5e'
};
export const BAND_ORDER = ['favourable', 'marginal', 'unfavourable', 'unsafe'];

const titleCase = (s: string) =>
  s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

/* A boolean means CONTAINMENT in the regulatory domain and PRESENCE elsewhere.
 * "EEZ absent" reads as "there is no EEZ" rather than "you are outside it",
 * which is a different and false claim (F-59). */
export function booleanWord(value: boolean, domain: string) {
  return domain === 'REGULATORY'
    ? (value ? 'inside' : 'outside')
    : (value ? 'present' : 'absent');
}

interface Segment { band: string; from: number; to: number; open: 'low' | 'high' | null }

/* Turn `{favourable:[null,1.5], marginal:[1.5,2.5], ...}` into a drawable axis.
 *
 * An open end has no coordinate, so it is given ONE band's worth of runway --
 * enough to place a pin inside it, and never so much that the closed bands
 * collapse to slivers. The axis is therefore real everywhere it is bounded and
 * clearly notional only past the last edge, which is marked with a ‹ or ›. */
export function buildAxis(bands: Record<string, BandEdges>): {
  segments: Segment[]; min: number; max: number
} | null {
  const present = BAND_ORDER.filter((b) => bands[b]);
  if (!present.length) return null;

  const edges = present
    .flatMap((b) => bands[b])
    .filter((n): n is number => typeof n === 'number');
  if (!edges.length) return null;

  const lo = Math.min(...edges);
  const hi = Math.max(...edges);
  // A single-edge axis has no width of its own; give it a symmetric one.
  const span = hi - lo || Math.max(Math.abs(hi), 1);
  const runway = span / Math.max(present.length - 1, 1) || span;

  const min = lo - runway;
  const max = hi + runway;
  const segments: Segment[] = present.map((b) => {
    const [l, h] = bands[b];
    return {
      band: b,
      from: l ?? min,
      to: h ?? max,
      open: l === null ? 'low' : h === null ? 'high' : null
    };
  });
  segments.sort((a, b) => a.from - b.from);
  return { segments, min, max };
}

const fmt = (n: number) =>
  Math.abs(n) >= 100 ? n.toFixed(0) : Math.abs(n) >= 1 ? n.toFixed(1) : n.toFixed(2);

export function ThresholdGauge({ driver, domain }:
  { driver: ORCADriver; domain: string }) {
  const limiting = driver.contribution === 'limiting';
  const label = (
    <span className={`glabel${limiting ? ' limiting' : ''}`}>
      {limiting ? '▸ ' : ''}{titleCase(driver.factor)}
    </span>
  );

  if (typeof driver.value !== 'number') {
    const shown = typeof driver.value === 'boolean'
      ? booleanWord(driver.value, domain)
      : (driver.value ?? '—');
    return (
      <div className="grow">
        {label}
        <span className={`gval${typeof driver.value === 'boolean' ? ' bool' : ''}`}>
          {String(shown)}
        </span>
      </div>
    );
  }

  const axis = driver.bands ? buildAxis(driver.bands) : null;
  const value = driver.value;

  const head = (
    <div className="grow">
      {label}
      <span className="gval">
        {value}{driver.unit ? ` ${driver.unit}` : ''}
      </span>
    </div>
  );

  /* No edges: equal-width bands, pin at its band's centre. Marked `notional`
   * so the axis does not pretend to a precision it does not have. */
  if (!axis) {
    const idx = BAND_ORDER.indexOf(driver.band ?? '');
    const pin = idx < 0 ? 50 : ((idx + 0.5) / BAND_ORDER.length) * 100;
    return (
      <div className="gauge">
        {head}
        <div className="gbar notional" title="Band edges unavailable — bands drawn equal-width">
          {BAND_ORDER.map((b) => (
            <div key={b} className="gseg"
                 style={{ flex: 1, background: BAND_COLOUR[b], opacity: 0.55 }} />
          ))}
          <div className="gpin" style={{ left: `${pin}%` }} />
        </div>
      </div>
    );
  }

  const { segments, min, max } = axis;
  const span = max - min || 1;
  const pct = (n: number) => ((n - min) / span) * 100;
  const pin = Math.max(0.5, Math.min(99.5, pct(value)));

  return (
    <div className="gauge">
      {head}
      <div className="gbar">
        {segments.map((s) => (
          <div
            key={s.band}
            className={`gseg${s.band === driver.band ? ' in' : ''}`}
            style={{
              position: 'absolute', left: `${pct(s.from)}%`,
              width: `${pct(s.to) - pct(s.from)}%`,
              background: BAND_COLOUR[s.band],
              opacity: s.band === driver.band ? 0.85 : 0.4
            }}
            title={`${titleCase(s.band)} ${s.open === 'low' ? `< ${fmt(s.to)}`
              : s.open === 'high' ? `> ${fmt(s.from)}`
              : `${fmt(s.from)}–${fmt(s.to)}`}${driver.unit ? ` ${driver.unit}` : ''}`}
          />
        ))}
        {/* Tick every real edge, so the reader can see where the value sits
            relative to the next threshold rather than only which band it is in. */}
        {segments.slice(1).map((s) => (
          <div key={`t${s.from}`} className="gtick" style={{ left: `${pct(s.from)}%` }} />
        ))}
        <div className="gpin" style={{ left: `${pin}%` }} />
      </div>
      <div className="gscale">
        {segments.slice(1).map((s) => (
          <span key={`l${s.from}`} className="gedge" style={{ left: `${pct(s.from)}%` }}>
            {fmt(s.from)}
          </span>
        ))}
      </div>
    </div>
  );
}
