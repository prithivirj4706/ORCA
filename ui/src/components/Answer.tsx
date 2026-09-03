import { useMemo, useState } from 'react';
import { VerdictCard } from './VerdictCard';
import { TemporalStrip } from './TemporalStrip';
import { Disagreement } from './Disagreement';
import { FreshnessDot } from './Freshness';
import type { ORCAResponse, ORCARouteProps } from '../types/api';

const titleCase = (s: string) =>
  s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const ASK_HINT: Record<string, string> = {
  location: 'e.g. "near Kochi" or "9.93N 76.26E"',
  time_window: 'e.g. "tomorrow morning" or "tonight"',
  destination: 'e.g. "to Chennai"',
  intent: 'e.g. "is it safe?" or "how is the fishing?"'
};

export const askHintFor = (what?: string | null) =>
  (what && ASK_HINT[what]) || 'Ask a follow-up…';

const EVIDENCE_PAGE = 12;

interface Props {
  data: ORCAResponse;
  onEvidenceClick: (provenanceId: string) => void;
}

export function Answer({ data, onEvidenceClick }: Props) {
  const rec = data.recommendation;
  const [allEvidence, setAllEvidence] = useState(false);

  /* Evidence rows carry their own freshness, which lives on the temporal
   * entries rather than on the evidence itself. */
  const ageByProvenance = useMemo(() => {
    const m = new Map<string, number | null>();
    for (const e of data.temporal_alignment?.entries ?? []) {
      m.set(e.provenance_id, e.age_s);
    }
    return m;
  }, [data.temporal_alignment]);

  /* ORCA is ASKING, not answering. The question is the whole content: it gets
   * its own card, and the caller closes the trace panel so nothing sits on top
   * of it. A question the user cannot see is not a question (F-57). */
  if (data.clarification_needed) {
    return (
      <div className="clarify">
        <div className="qmark">?</div>
        <div>
          <div className="headline">{rec?.headline || 'I need one more detail.'}</div>
          <div className="sub">waiting on {data.clarification_needed}</div>
        </div>
      </div>
    );
  }

  const L = data.resolved_location;
  const where = L
    ? `${L.label || ''} ${L.lat?.toFixed(2)}N ${L.lon?.toFixed(2)}E`.trim()
    : '';

  /* `headline` is a short English summary; `narrative` is the COMPOSED answer,
   * and the only part of the payload that is written in the user's language.
   * Rendering the headline alone meant a Malayalam question was answered in
   * English on screen while its Malayalam answer sat unused in the response --
   * the verdict cards' domain and factor names are English too, so the
   * narrative is the whole of what a non-English reader can actually read.
   *
   * So the answer leads with the narrative's own first line, and the rest is
   * kept: collapsed in English, where the cards below say the same thing, and
   * open otherwise, where they do not. */
  const narrative = (rec?.narrative || '').trim();
  const [lead, ...restLines] = narrative
    ? narrative.split('\n')
    : [rec?.headline || '', ''];
  const rest = restLines.join('\n').trim();
  const localised = (data.language || 'en') !== 'en';
  /* Out of scope: the guidance IS the answer, so it is never collapsed, and
   * the intent/disposition line is suppressed -- "smalltalk_or_out_of_scope ·
   * out_of_scope" is internal vocabulary and tells the reader nothing. */
  const outOfScope = data.disposition === 'OUT_OF_SCOPE';
  /* The route's own account of itself. The backend records what steered the
   * search and what it could not reach; none of it was rendered, so the only
   * place that said "sea state was not considered" was a property nobody saw. */
  const routeProps: ORCARouteProps | null =
    (data.map_layers?.find((l) => l.id === 'optimized_route')
      ?.data?.properties as ORCARouteProps) ?? null;
  const steeredBy = routeProps?.steered_by ?? [];

  const evidence = data.evidence || [];
  const shownEvidence = allEvidence ? evidence : evidence.slice(0, EVIDENCE_PAGE);
  const unavailable = data.plan?.unavailable ?? [];
  const notEvaluated = data.not_evaluated ?? [];

  return (
    <>
      <div className="headline" lang={data.language || 'en'}>{lead}</div>
      {!outOfScope && (
        <div className="sub">
          {[data.intent, where, data.disposition?.toLowerCase()]
            .filter(Boolean).join(' · ')}
        </div>
      )}

      {/* How the question was READ. A wrong premise is the one error a correct
          pipeline cannot recover from, so the resolution is stated -- except
          out of scope, where "no location in the query" would reintroduce
          exactly the implication this path exists to remove. */}
      {!outOfScope && (data.resolution_notes?.length ?? 0) > 0 && (
        <div className="notes">
          {data.resolution_notes!.map((n, i) => <span key={i}>{n}</span>)}
        </div>
      )}

      {rest && (
        <details className="fullanswer" open={localised || outOfScope}>
          <summary>{localised ? 'Full answer' : 'Full answer in prose'}</summary>
          <p lang={data.language || 'en'}>{rest}</p>
        </details>
      )}

      {(data.alerts || []).map((a, i) => (
        <div key={i} className={`alert${a.severity === 'warning' ? ' warning' : ''}`}>
          <i>{a.kind === 'inside' ? '◉' : '◈'}</i>
          <div>
            <b>{titleCase(a.kind)} {titleCase(a.boundary_type)}</b>
            {a.name ? ` — ${a.name}` : ''}
            {/* Every boundary carries a dataset version and is advisory only.
                Showing them only when a distance exists dropped both on an
                `inside` alert, which is the one that most needs them. */}
            <div className="mono-xs">
              {a.distance_km != null ? `${a.distance_km} km · ` : ''}
              {a.dataset_version ? `${a.dataset_version} · ` : ''}
              advisory only
            </div>
          </div>
        </div>
      ))}

      {routeProps && (
        <div className={`routecard${steeredBy.length ? '' : ' plain'}`}>
          <div className="rc-top">
            <b>Route</b>
            <span className="rc-len">
              {routeProps.length_km} km · {routeProps.waypoints} waypoints
            </span>
          </div>
          <div className="rc-obj">
            {steeredBy.length
              ? <>Steered to avoid{' '}
                  {steeredBy.map((f) => titleCase(f)).join(' and ')}, over the
                  shortest navigable path.</>
              : <><b>Shortest navigable path only.</b> Sea state was not taken
                  into account — this is not a weather-optimised route.</>}
          </div>
          {(routeProps.fields_unavailable ?? []).length > 0 && (
            <div className="rc-gaps">
              Could not steer by:{' '}
              {routeProps.fields_unavailable!.map((f) =>
                `${titleCase(f.parameter)} (${f.reason.replace(/_/g, ' ').toLowerCase()})`
              ).join(', ')}
            </div>
          )}
          <div className="rc-note">
            {routeProps.navigability ? `Navigability: ${routeProps.navigability}. ` : ''}
            {routeProps.note}
          </div>
        </div>
      )}

      <Disagreement assessments={data.assessments || []} />

      {(data.assessments || []).length > 0 && (
        <>
          <div className="sec">Independent assessments</div>
          <div className="verdicts">
            {data.assessments!.map((a, i) => <VerdictCard key={i} assessment={a} />)}
          </div>
        </>
      )}

      <TemporalStrip data={data.temporal_alignment} />

      {/* Capabilities the plan asked for and could not fill. First-class
          content: an answer that quietly omits what it could not reach is
          indistinguishable from one that had everything. */}
      {unavailable.length > 0 && (
        <>
          <div className="sec">Planned for, not available ({unavailable.length})</div>
          {unavailable.map((u, i) => (
            <div key={i} className="gapline">
              <b>{titleCase(u.evidence || u.tool || 'capability')}</b>
              <span>{u.reason || 'no source bound'}</span>
            </div>
          ))}
        </>
      )}

      {notEvaluated.length > 0 && (
        <details className="notchecked">
          <summary><b>Not evaluated</b> ({notEvaluated.length})</summary>
          {notEvaluated.map((n, i) => (
            <div key={i} className="gapline">
              <b>{titleCase(n.factor)}</b>
              <span>{n.detail || n.reason.replace(/_/g, ' ').toLowerCase()}</span>
            </div>
          ))}
        </details>
      )}

      {evidence.length > 0 && (
        <>
          <div className="sec">Evidence ({evidence.length})</div>
          {shownEvidence.map((e) => (
            <div key={e.evidence_id} className="ev">
              <FreshnessDot ageSeconds={ageByProvenance.get(e.provenance_id)}
                            title={e.parameter} />
              {e.statement || e.parameter}
              <br />
              <button className="p" title="Show the provenance chain"
                      onClick={() => onEvidenceClick(e.provenance_id)}>
                {e.provenance_id}
              </button>
            </div>
          ))}
          {/* Never truncate silently: a hidden remainder reads as "that was all
              the evidence there was". */}
          {evidence.length > EVIDENCE_PAGE && (
            <button className="tmore" onClick={() => setAllEvidence(!allEvidence)}>
              {allEvidence
                ? 'show fewer'
                : `show ${evidence.length - EVIDENCE_PAGE} more evidence items`}
            </button>
          )}
        </>
      )}

      <div className="disclaimer">
        ORCA is not an official advisory service. It cites INCOIS and IMD;
        it does not replace them.
      </div>
    </>
  );
}
