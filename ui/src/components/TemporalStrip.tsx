/* Every retrieved value's validity, against the window being asked about.
 *
 * This is the least visible and most load-bearing judgement ORCA makes. The
 * fishing answer near Kochi rests on chlorophyll that is two days old and
 * REJECTS a sea-surface temperature from 2011 -- both decisions are correct,
 * both are invisible in a verdict card, and a reader who cannot see them has
 * to take the alignment on trust.
 *
 * So each source gets a bar on a shared time axis, the analysis window is drawn
 * behind them, and a row that was retrieved and then refused is drawn as such,
 * with the reason. A row that was USED is solid; a row that was not is hatched
 * and dimmed. Nothing here is inferred: `used`, `age_s` and `excluded_reason`
 * all come from the projection.
 */
import { useMemo, useState } from 'react';
import type { ORCATemporalAlignment, ORCATemporalEntry } from '../types/api';

const t = (s: string | null | undefined) => (s ? Date.parse(s) : NaN);

const human = (sec: number | null) => {
  if (sec === null || Number.isNaN(sec)) return '—';
  const a = Math.abs(sec);
  const suffix = sec < 0 ? ' ahead' : ' old';
  if (a < 5400) return `${Math.round(a / 60)} min${suffix}`;
  if (a < 172800) return `${Math.round(a / 3600)} h${suffix}`;
  const d = a / 86400;
  if (d < 400) return `${d.toFixed(d < 10 ? 1 : 0)} d${suffix}`;
  return `${(d / 365.25).toFixed(1)} yr${suffix}`;
};

const REASON_WORD: Record<string, string> = {
  STALE_DATA: 'too old for this window',
  REPRESENTATIVENESS_MISMATCH: 'wrong kind of value for this domain',
  QUALITY_EXCLUDED: 'excluded on quality',
  NO_BAND: 'outside every defined band',
  NOT_RETRIEVED: 'never retrieved'
};

/** One row's own extent. A value with no stated duration is an instant. */
function extent(e: ORCATemporalEntry): [number, number] | null {
  const v = t(e.valid_time);
  const from = t(e.valid_from);
  const to = t(e.valid_to);
  if (!Number.isNaN(from) && !Number.isNaN(to)) return [from, to];
  if (!Number.isNaN(v)) return [v, v];
  return null;
}

export function TemporalStrip({ data }: { data?: ORCATemporalAlignment }) {
  const [showAll, setShowAll] = useState(false);

  const model = useMemo(() => {
    const entries = data?.entries ?? [];
    if (!entries.length) return null;

    const ws = t(data?.window.start_time);
    const we = t(data?.window.end_time);
    const now = t(data?.generated_at);

    /* One boundary query returns a provenance record PER FEATURE, so six EEZ
     * polygons become six identical rows that push the interesting ones off the
     * panel. They are genuinely distinct records, but on a TIME axis they are
     * one fact, so they collapse to a single row carrying a count. */
    const byFact = new Map<string, { e: ORCATemporalEntry; ext: [number, number]; n: number }>();
    for (const e of entries) {
      const ext = extent(e);
      if (!ext) continue;
      const key = `${e.parameter}|${e.source}|${e.valid_time}|${e.used}|${e.excluded_reason}`;
      const hit = byFact.get(key);
      if (hit) hit.n += 1;
      else byFact.set(key, { e, ext, n: 1 });
    }
    const rows = [...byFact.values()];
    if (!rows.length) return null;

    /* A 2011 observation beside a forecast for tomorrow spans fifteen years, on
     * which every current source collapses to one pixel. So the axis is drawn
     * over the RECENT span -- the window plus a margin -- and anything older is
     * pinned to the left edge and labelled with its true age. The scale stays
     * honest because the outlier is never drawn as if it were in range. */
    const recent = rows.map((r) => r.ext[0]).filter((v) => !Number.isNaN(ws) ? v > ws - 12 * 86400e3 : true);
    const lo = Math.min(...(recent.length ? recent : rows.map((r) => r.ext[0])),
                        Number.isNaN(ws) ? Infinity : ws);
    const hi = Math.max(...rows.map((r) => r.ext[1]).filter((v) => v >= lo),
                        Number.isNaN(we) ? -Infinity : we,
                        Number.isNaN(now) ? -Infinity : now);
    const pad = Math.max((hi - lo) * 0.06, 36e5);
    const min = lo - pad;
    const max = hi + pad;
    const span = max - min || 1;
    const pct = (ms: number) => ((ms - min) / span) * 100;

    /* Oldest first, so the strip reads down the same axis the bars run along.
     * A 2011 observation therefore leads, which is exactly the row whose
     * rejection the panel exists to explain; today's forecasts follow. */
    rows.sort((a, b) => a.ext[0] - b.ext[0] || (a.e.parameter ?? '').localeCompare(b.e.parameter ?? ''));
    return { rows, ws, we, now, min, max, pct };
  }, [data]);

  if (!model) return null;
  const { rows, ws, we, now, min, pct } = model;

  const shown = showAll ? rows : rows.slice(0, 9);
  const hidden = rows.length - shown.length;

  return (
    <div className="tstrip">
      <div className="sec">
        Temporal alignment
        <span className="tstrip-sub">
          {rows.filter((r) => r.e.used).length} used · {rows.filter((r) => !r.e.used).length} not
        </span>
      </div>

      <div className="tstrip-axis">
        {!Number.isNaN(ws) && !Number.isNaN(we) && (
          <div className="twin" style={{ left: `${pct(ws)}%`, width: `${pct(we) - pct(ws)}%` }}>
            <span>analysis window</span>
          </div>
        )}
        {!Number.isNaN(now) && <div className="tnow" style={{ left: `${pct(now)}%` }} />}
      </div>

      {shown.map(({ e, ext, n }) => {
        const early = ext[0] < min;               // older than the drawn axis
        const left = early ? 0 : pct(ext[0]);
        const right = Math.max(pct(ext[1]), left + 0.8);
        return (
          <div key={e.provenance_id} className={`trow${e.used ? '' : ' unused'}`}>
            <div className="trow-head">
              <span className="trow-param" title={e.parameter ?? ''}>
                {e.parameter}
              </span>
              {n > 1 && <span className="trow-n" title={`${n} records`}>×{n}</span>}
              <span className="trow-src">{e.source}</span>
              <span className="trow-age">{human(e.age_s)}</span>
            </div>
            <div className="trow-track">
              <div
                className={`tbar${e.used ? '' : ' out'}${early ? ' clipped' : ''}`}
                style={{ left: `${left}%`, width: `${right - left}%` }}
                title={`${e.valid_time ?? ''}${e.dataset ? ` · ${e.dataset}` : ''}`}
              />
            </div>
            <div className="trow-why">
              {e.used ? (
                <>
                  used
                  {e.derived_via ? <> · derived by <code>{e.derived_via}</code></> : null}
                  {e.lead_time_h != null ? ` · +${e.lead_time_h} h lead` : ''}
                </>
              ) : (
                <span className="tno">
                  not used
                  {e.excluded_reason
                    ? ` — ${REASON_WORD[e.excluded_reason] ?? e.excluded_reason.toLowerCase()}`
                    : ' — not required by any factor in this plan'}
                </span>
              )}
            </div>
          </div>
        );
      })}

      {hidden > 0 && (
        <button className="tmore" onClick={() => setShowAll(true)}>
          show {hidden} more retrieved value{hidden === 1 ? '' : 's'}
        </button>
      )}
      {showAll && rows.length > 9 && (
        <button className="tmore" onClick={() => setShowAll(false)}>show fewer</button>
      )}

      <div className="cov">
        Bars are each value's own validity, not when it was fetched. A value
        older than the axis is pinned to the left edge and labelled with its
        true age, so a stale source is never drawn as if it were current.
      </div>
    </div>
  );
}
