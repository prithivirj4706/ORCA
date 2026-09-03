/* A freshness dot that decays with age.
 *
 * Age is the property most likely to be skimmed past in text and the one most
 * likely to invalidate an answer, so it also gets a non-textual channel: a dot
 * that loses saturation and gains a ring as the value gets older. The text is
 * always present too -- the dot is a second channel, never the only one, since
 * colour alone is not an accessible carrier.
 */
const HOUR = 3600;

export interface FreshnessLevel {
  key: 'live' | 'recent' | 'aging' | 'stale' | 'ancient' | 'forecast';
  colour: string;
  label: string;
}

export function freshnessOf(ageSeconds: number | null | undefined): FreshnessLevel {
  if (ageSeconds === null || ageSeconds === undefined || Number.isNaN(ageSeconds))
    return { key: 'stale', colour: 'var(--unknown)', label: 'age unknown' };
  if (ageSeconds < -HOUR)
    return { key: 'forecast', colour: '#7dd3fc', label: 'forecast, ahead of now' };
  if (ageSeconds < 6 * HOUR) return { key: 'live', colour: '#34d399', label: 'current' };
  if (ageSeconds < 36 * HOUR) return { key: 'recent', colour: '#a3e635', label: 'recent' };
  if (ageSeconds < 7 * 24 * HOUR) return { key: 'aging', colour: '#fbbf24', label: 'ageing' };
  if (ageSeconds < 90 * 24 * HOUR) return { key: 'stale', colour: '#fb923c', label: 'stale' };
  return { key: 'ancient', colour: '#f43f5e', label: 'far outside any useful window' };
}

export function FreshnessDot({ ageSeconds, title }:
  { ageSeconds: number | null | undefined; title?: string }) {
  const f = freshnessOf(ageSeconds);
  return (
    <span
      className={`fdot f-${f.key}`}
      style={{ ['--f' as string]: f.colour }}
      title={title ? `${title} — ${f.label}` : f.label}
      aria-label={f.label}
    />
  );
}
