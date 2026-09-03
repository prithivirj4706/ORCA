/* Twelve capabilities, grouped by the domain they serve.
 *
 * A flat list of twelve rows makes "8 of 12" a number to be taken on trust. The
 * constellation groups each capability under the domain it feeds and draws the
 * bound ones lit and the unbound ones dark but PRESENT, so the shape of what
 * ORCA cannot do is visible at a glance rather than read off a list.
 *
 * A capability with no source is declared, never hidden -- and never dropped
 * from the diagram, because an absent node would make the map look complete.
 */
import type { ORCASource } from '../types/api';

const DOMAIN_LABEL: Record<string, string> = {
  SAFETY: 'Safety',
  FISHING_SUITABILITY: 'Fishing',
  REGULATORY: 'Regulatory',
  NAVIGATION: 'Navigation',
  OTHER: 'Unassigned'
};

export function SourceHealth({ sources }: { sources: ORCASource[] }) {
  if (!sources.length) return <div className="empty">Loading…</div>;

  // A capability serving several domains appears under each: it genuinely does
  // serve each, and hiding the duplicate would understate what its loss costs.
  const groups = new Map<string, ORCASource[]>();
  for (const s of sources) {
    const domains = s.domains?.length ? s.domains : ['OTHER'];
    for (const d of domains) {
      if (!groups.has(d)) groups.set(d, []);
      groups.get(d)!.push(s);
    }
  }

  const available = sources.filter((s) => s.available).length;

  return (
    <>
      <div className="constellation-top">
        <b>{available}</b> of <b>{sources.length}</b> capabilities are bound.
      </div>

      {[...groups.entries()].map(([domain, list]) => (
        <div key={domain} className="constel">
          <div className="constel-head">
            <span>{DOMAIN_LABEL[domain] ?? domain}</span>
            <span className="constel-count">
              {list.filter((s) => s.available).length}/{list.length}
            </span>
          </div>
          <div className="constel-nodes">
            {list.map((s) => (
              <div
                key={`${domain}-${s.tool}`}
                className={`cnode${s.available ? ' on' : ' off'}`}
                title={s.available
                  ? `${s.tool} — ${s.description}`
                  : `${s.tool} — ${s.unavailable_reason || 'not bound'}`}
              >
                <span className="cnode-dot" />
                <span className="cnode-name">{s.tool.replace(/^get_/, '')}</span>
                {!s.available && (
                  <span className="cnode-why">
                    {s.unavailable_reason || 'not bound'}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}

      <div className="disclaimer">
        A capability with no source is declared, never hidden. Every answer names
        what it could not check.
      </div>
    </>
  );
}
