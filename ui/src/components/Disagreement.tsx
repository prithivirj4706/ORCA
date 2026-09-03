/* Where the independent domains disagree.
 *
 * The four domains are assessed separately and are never merged into a score,
 * which is the point -- but it also means a reader can be shown FAVOURABLE
 * beside PROHIBITED with nothing drawing attention to the fact that those two
 * cards are in tension. Merging them would destroy the property; saying
 * nothing wastes it. So the divergence is NAMED, and the cards stay separate.
 *
 * Divergence is measured on a severity ladder shared by both vocabularies
 * (safety verdicts and regulatory statuses), because "PROHIBITED with
 * FAVOURABLE weather" is exactly the case worth surfacing.
 */
import type { ORCAAssessment } from '../types/api';

/* One ladder for both vocabularies. Index is severity, not a score: it is used
 * to find the SPREAD between domains and is never averaged into a verdict. */
const SEVERITY: Record<string, number> = {
  FAVOURABLE: 0, PERMITTED: 0,
  MARGINAL: 1, RESTRICTED: 1,
  UNFAVOURABLE: 2,
  UNSAFE: 3, PROHIBITED: 3
};
// Deliberately absent above: UNKNOWN and INSUFFICIENT_EVIDENCE. Not knowing is
// not a position on the ladder, so it cannot disagree with one -- it is a gap,
// and the verdict cards already say so.

const titleCase = (s: string) =>
  s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

export function Disagreement({ assessments }: { assessments: ORCAAssessment[] }) {
  const ranked = (assessments || [])
    .filter((a) => SEVERITY[a.verdict] !== undefined)
    .map((a) => ({ a, s: SEVERITY[a.verdict] }));
  if (ranked.length < 2) return null;

  const worst = ranked.reduce((m, r) => (r.s > m.s ? r : m));
  const best = ranked.reduce((m, r) => (r.s < m.s ? r : m));
  const spread = worst.s - best.s;
  if (spread < 2) return null;          // adjacent bands are not a disagreement

  return (
    <div className="disagree">
      <div className="dis-head">
        <i>⚠</i>
        <b>The domains disagree</b>
      </div>
      <p>
        {titleCase(worst.a.domain)} says <b>{titleCase(worst.a.verdict)}</b> while{' '}
        {titleCase(best.a.domain)} says <b>{titleCase(best.a.verdict)}</b>. These are
        not averaged and neither one overrides the other: they answer different
        questions about the same position.
      </p>
      <div className="dis-pair">
        {[worst, best].map(({ a }) => (
          <div key={a.domain} className="dis-cell">
            <span className="dis-dom">{titleCase(a.domain)}</span>
            <span className="dis-verdict">{titleCase(a.verdict)}</span>
            <span className="dis-why">
              {a.limiting_factor
                ? `governed by ${titleCase(a.limiting_factor)}`
                : a.rationale?.slice(0, 90)}
            </span>
          </div>
        ))}
      </div>
      <div className="cov">
        The stricter reading governs what you may do; the weather reading does
        not make it permitted.
      </div>
    </div>
  );
}
