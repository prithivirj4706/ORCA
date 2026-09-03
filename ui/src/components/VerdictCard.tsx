import { ThresholdGauge } from './ThresholdGauge';
import type { ORCAAssessment } from '../types/api';

const VERDICT_COLOUR: Record<string, string> = {
  FAVOURABLE: 'var(--favourable)', PERMITTED: 'var(--favourable)',
  MARGINAL: 'var(--marginal)', RESTRICTED: 'var(--marginal)',
  UNFAVOURABLE: 'var(--unfavourable)',
  UNSAFE: 'var(--unsafe)', PROHIBITED: 'var(--unsafe)',
  INSUFFICIENT_EVIDENCE: 'var(--unknown)', UNKNOWN: 'var(--unknown)'
};

/* Confidence as visual uncertainty rather than only a badge.
 *
 * A LOW-confidence verdict that is drawn exactly as crisply as a HIGH one asks
 * the reader to notice a three-letter label in order to discount everything
 * above it. Softening the card's edge and its verdict word makes the strength
 * of the claim legible before the label is read. It is deliberately subtle:
 * the numbers must stay exactly as readable, so only the verdict word and the
 * card's border carry it. */
const CONFIDENCE_STYLE: Record<string, { edge: number; blur: string; sub: string }> = {
  HIGH: { edge: 0.85, blur: '0', sub: 'stated with high confidence' },
  MEDIUM: { edge: 0.5, blur: '0.15px', sub: 'stated with medium confidence' },
  LOW: { edge: 0.28, blur: '0.35px', sub: 'low confidence — treat as indicative' }
};

const titleCase = (s: string) =>
  s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

export function VerdictCard({ assessment }: { assessment: ORCAAssessment }) {
  const colour = VERDICT_COLOUR[assessment.verdict] || 'var(--unknown)';
  const gaps = assessment.not_evaluated || [];
  const capped = (assessment.verdict_capped_by || []).length > 0;
  const conf = CONFIDENCE_STYLE[assessment.confidence] ?? CONFIDENCE_STYLE.LOW;

  return (
    <div
      className="verdict-card"
      style={{
        ['--c' as string]: colour,
        ['--edge' as string]: String(conf.edge),
        ['--vblur' as string]: conf.blur
      }}
    >
      <div className="vtop">
        <span className="vdom">{titleCase(assessment.domain)}</span>
        <span className="vverdict">{titleCase(assessment.verdict)}</span>
        <span className={`vconf c-${assessment.confidence?.toLowerCase()}`}
              title={conf.sub}>
          {assessment.confidence}
        </span>
      </div>

      {assessment.drivers?.map((d, i) => (
        <ThresholdGauge key={`${d.factor}-${i}`} driver={d} domain={assessment.domain} />
      ))}

      {capped && (
        <div className="ceiling">
          Ceiling, not a measurement — {assessment.verdict_capped_by.join(', ')}{' '}
          could not be checked.
        </div>
      )}

      {assessment.rationale && <div className="rationale">{assessment.rationale}</div>}

      {/* `not_evaluated` is first-class content, not an error state: an answer
          that hides what it could not check is the failure this system exists
          to avoid. Each gap carries its own reason. */}
      {gaps.length > 0 && (
        <details className="gaps">
          <summary>
            <b>Not checked</b> ({gaps.length}) —{' '}
            {gaps.slice(0, 3).map((g) => titleCase(g.factor)).join(', ')}
            {gaps.length > 3 ? ' …' : ''}
          </summary>
          <ul>
            {gaps.map((g, i) => (
              <li key={i}>
                <b>{titleCase(g.factor)}</b>
                <span className="gap-why">
                  {g.detail || g.reason.replace(/_/g, ' ').toLowerCase()}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
