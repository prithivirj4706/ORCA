import { useState } from 'react';
import { TraceDetail, TraceGraph } from './TraceGraph';
import type { ORCATraceEvent } from '../types/api';

const NODE_LABEL: Record<string, string> = {
  ingest: 'Ingest', intent_context: 'Resolve intent, place and time',
  plan: 'Plan', tool_exec: 'Retrieve', validate: 'Validate evidence',
  replan: 'Re-plan', geo_reason: 'Align and derive',
  assess_safety: 'Assess safety', assess_fishing_suitability: 'Assess fishing',
  assess_regulatory: 'Assess regulatory', assess_navigation: 'Assess navigation',
  conflict_resolve: 'Resolve conflicts',
  evidence_assemble: 'Assemble evidence', review_gate: 'Review gate',
  human_review: 'Human review', report: 'Compose answer', finalize: 'Finalise',
  clarify: 'Ask for clarification', out_of_scope: 'Out of scope',
  error_handler: 'Error'
};

/* Two views over the same events, because they answer different questions.
 *
 * The GRAPH shows the run's shape -- the parallel fan-out, the branches not
 * taken, the backward edge -- which is the thing a dashboard cannot show. The
 * TIMELINE shows what happened in order, with the per-node codes and timings
 * the graph has no room for. Neither replaces the other, so both are kept and
 * the graph leads.
 *
 * Every node the graph emitted is present in both, including each parallel tool
 * in a fan-out: showing only the newest per superstep collapsed seven tools to
 * one line and hid the single thing this panel exists to show (F-56).
 */
export function AgentTrace({ trace, live }:
  { trace: ORCATraceEvent[]; live: boolean }) {
  const [view, setView] = useState<'graph' | 'timeline'>('graph');
  const [selected, setSelected] = useState<string | null>(null);

  if (!trace.length) return <div className="empty">No trace yet.</div>;

  return (
    <>
      <div className="tviews" role="tablist" aria-label="Trace view">
        {(['graph', 'timeline'] as const).map((v) => (
          <button key={v} role="tab" aria-selected={view === v}
                  className={`tview${view === v ? ' on' : ''}`}
                  onClick={() => setView(v)}>
            {v}
          </button>
        ))}
        <span className="tviews-n">{trace.length} events</span>
      </div>

      {view === 'graph' ? (
        <>
          <TraceGraph trace={trace} live={live}
                      selected={selected} onSelect={setSelected} />
          {selected
            ? <TraceDetail trace={trace} nodeId={selected} />
            : <div className="cov">
                Dim nodes did not run this turn — a branch not taken is
                information. Select any node for its codes, source and timing.
              </div>}
        </>
      ) : (
        <div className="trace">
          {trace.map((ev, i) => {
            const bad = ev.status === 'error' || ev.status === 'failed';
            const warn = ev.status === 'degraded' || ev.status === 'partial';
            const isLast = live && i === trace.length - 1;
            const bits: string[] = [];
            if (ev.codes?.length) bits.push(ev.codes.join(', '));
            if (ev.fallback_used) bits.push('served by a fallback');
            if (ev.summary && !ev.tool) bits.push(ev.summary);

            return (
              <div key={i}>
                {i > 0 && <div className="rail" />}
                <div className={`tnode${isLast ? ' live' : ''}`}>
                  <span className={`tdot${bad ? ' err' : warn ? ' warn' : ''}`} />
                  <span className="tname">
                    {ev.tool || NODE_LABEL[ev.node] || ev.node}
                  </span>
                  <span className="tmeta">
                    {ev.source ? `${ev.source} · ` : ''}
                    {ev.duration_ms ?? 0} ms
                  </span>
                </div>
                {bits.length > 0 && <div className="tsum">{bits.join(' — ')}</div>}
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
