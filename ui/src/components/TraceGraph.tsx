/* The agent trace as the graph it actually is.
 *
 * The timeline shows every node in the order it completed, which is the right
 * shape for reading WHAT happened. It cannot show the thing that makes ORCA
 * different from a dashboard: that the run has a SHAPE -- a plan that fans out
 * across seven tools at once, a validation gate that can send the run
 * backwards, a per-domain assessment fan-out that is deliberately never merged.
 *
 * So the topology is drawn from `backend/orca/graph/build.py` as a fixed
 * skeleton, and the run is lit over it. Two consequences are the point:
 *
 *   * a node that did NOT run stays visible, dim. The path not taken is
 *     information -- `clarify` dark means ORCA did not need to ask; `replan`
 *     dark means the first plan was sufficient; `human_review` dark means the
 *     answer was auto-released;
 *   * the fan-outs are drawn as fan-outs, so seven parallel tools read as
 *     seven parallel tools rather than seven consecutive lines (F-56).
 *
 * The skeleton is hand-maintained against build.py. It is a small, stable file
 * and a wrong picture of the graph would be worse than no picture, so the edges
 * that exist here are exactly the edges compiled there.
 */
import { useMemo } from 'react';
import type { ORCATraceEvent } from '../types/api';

/** The spine, in the order `build.py` wires it. */
const SPINE = [
  { id: 'ingest', label: 'Ingest' },
  { id: 'intent_context', label: 'Resolve intent, place, time' },
  { id: 'plan', label: 'Plan' },
  { id: 'tool_exec', label: 'Retrieve', fan: 'tool' as const },
  { id: 'validate', label: 'Validate evidence' },
  { id: 'geo_reason', label: 'Align and derive' },
  { id: 'assess_domain', label: 'Assess', fan: 'domain' as const },
  { id: 'conflict_resolve', label: 'Resolve conflicts' },
  { id: 'evidence_assemble', label: 'Assemble evidence' },
  { id: 'review_gate', label: 'Review gate' },
  { id: 'report', label: 'Compose answer' },
  { id: 'finalize', label: 'Finalise' }
];

/** Branches off the spine, each drawn at its source's layer. */
const BRANCHES = [
  { id: 'out_of_scope', label: 'Out of scope', from: 'intent_context',
    note: 'intent_context → out_of_scope → finalise; no retrieval, no verdict' },
  { id: 'clarify', label: 'Ask for clarification', from: 'plan',
    note: 'plan → clarify → finalise' },
  { id: 'replan', label: 'Re-plan', from: 'validate', loops: 'tool_exec',
    note: 'validate → replan → retrieve again, or forward' },
  { id: 'human_review', label: 'Human review', from: 'review_gate',
    note: 'review_gate → human_review → report' },
  { id: 'error_handler', label: 'Error', from: 'report',
    note: 'reachable from intent, plan and validate' }
];

const NODE_H = 24, CHIP_H = 15, GAP = 15;
const SPINE_X = 122, SPINE_W = 128;
const BRANCH_X = 8, BRANCH_W = 104;
const FAN_X = 262, FAN_W = 92;
const VB_W = 360;

type Status = 'idle' | 'ok' | 'warn' | 'err';

const STATUS_COLOUR: Record<Status, string> = {
  idle: 'var(--idle)', ok: 'var(--teal)', warn: 'var(--marginal)', err: 'var(--unsafe)'
};

function statusOf(events: ORCATraceEvent[]): Status {
  if (!events.length) return 'idle';
  if (events.some((e) => e.status === 'error' || e.status === 'failed')) return 'err';
  if (events.some((e) => e.status === 'degraded' || e.status === 'partial'
                      || e.fallback_used)) return 'warn';
  return 'ok';
}

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

interface Placed {
  id: string; label: string; y: number; status: Status;
  events: ORCATraceEvent[]; children: { key: string; label: string; status: Status;
                                        event: ORCATraceEvent }[];
}

export function TraceGraph({ trace, live, onSelect, selected }: {
  trace: ORCATraceEvent[]; live: boolean;
  onSelect: (id: string | null) => void; selected: string | null;
}) {
  const model = useMemo(() => {
    // `assess_domain` is one node in the graph but emits one event per domain,
    // named assess_<domain>; the fan-out is exactly that spread.
    const byNode = new Map<string, ORCATraceEvent[]>();
    for (const e of trace) {
      const key = e.node?.startsWith('assess_') && e.node !== 'assess_domain'
        ? 'assess_domain' : e.node;
      if (!key) continue;
      if (!byNode.has(key)) byNode.set(key, []);
      byNode.get(key)!.push(e);
    }

    let y = 10;
    const spine: Placed[] = SPINE.map((n) => {
      const events = byNode.get(n.id) ?? [];
      const children = n.fan
        ? events.map((e, i) => ({
            key: `${n.id}-${i}`,
            label: n.fan === 'tool'
              ? (e.tool ?? 'tool')
              : (e.node ?? '').replace(/^assess_/, ''),
            status: statusOf([e]),
            event: e
          }))
        : [];
      const h = Math.max(NODE_H, children.length * CHIP_H);
      const placed: Placed = {
        id: n.id, label: n.label, y: y + h / 2,
        status: statusOf(events), events, children
      };
      // children are laid out from the top of this layer
      (placed as Placed & { top: number }).top = y;
      y += h + GAP;
      return placed;
    });

    const branches = BRANCHES.map((b) => {
      const host = spine.find((s) => s.id === b.from)!;
      const events = byNode.get(b.id) ?? [];
      return { ...b, y: host.y, status: statusOf(events), events };
    });

    return { spine, branches, height: y };
  }, [trace]);

  const { spine, branches, height } = model;
  const liveId = live && trace.length
    ? (trace[trace.length - 1].node?.startsWith('assess_')
        ? 'assess_domain' : trace[trace.length - 1].node)
    : null;

  if (!trace.length) return <div className="empty">No trace yet.</div>;

  return (
    <svg className="dag" viewBox={`0 0 ${VB_W} ${height}`} role="img"
         aria-label="Agent graph: nodes that ran are lit, nodes that did not are dim">
      <defs>
        <marker id="ah" markerWidth="5" markerHeight="5" refX="4" refY="2.5"
                orient="auto">
          <path d="M0,0 L5,2.5 L0,5 z" fill="var(--line-2)" />
        </marker>
      </defs>

      {/* spine edges */}
      {spine.slice(0, -1).map((n, i) => {
        const next = spine[i + 1];
        const lit = n.status !== 'idle' && next.status !== 'idle';
        return (
          <line key={`e-${n.id}`} className={`dedge${lit ? ' lit' : ''}`}
                x1={SPINE_X + SPINE_W / 2} y1={n.y + NODE_H / 2}
                x2={SPINE_X + SPINE_W / 2} y2={next.y - NODE_H / 2}
                markerEnd="url(#ah)" />
        );
      })}

      {/* fan-out edges and chips */}
      {spine.filter((n) => n.children.length).map((n) => {
        const top = (n as Placed & { top: number }).top;
        return (
          <g key={`fan-${n.id}`}>
            {n.children.map((c, i) => {
              const cy = top + i * CHIP_H + CHIP_H / 2;
              return (
                <g key={c.key}>
                  <path className="dfan lit"
                        d={`M${SPINE_X + SPINE_W} ${n.y} C ${FAN_X - 18} ${n.y},
                            ${FAN_X - 18} ${cy}, ${FAN_X} ${cy}`} />
                  <rect className={`dchip s-${c.status}`} x={FAN_X} y={cy - CHIP_H / 2 + 1.5}
                        width={FAN_W} height={CHIP_H - 3} rx={5} />
                  <text className="dchip-t" x={FAN_X + 7} y={cy + 3}>
                    {clip(c.label.replace(/^get_/, ''), 15)}
                  </text>
                </g>
              );
            })}
          </g>
        );
      })}

      {/* branch stubs — drawn always, because a branch NOT taken is information */}
      {branches.map((b) => (
        <g key={b.id}>
          <line className={`dedge dashed${b.status !== 'idle' ? ' lit' : ''}`}
                x1={SPINE_X} y1={b.y} x2={BRANCH_X + BRANCH_W} y2={b.y} />
          {b.loops && b.status !== 'idle' && (
            // The re-plan loop: the one edge that goes BACKWARDS.
            <path className="dedge lit loop"
                  d={`M${BRANCH_X + 12} ${b.y - NODE_H / 2}
                      C ${BRANCH_X - 6} ${b.y - 40},
                        ${BRANCH_X - 6} ${spine.find((s) => s.id === b.loops)!.y + 30},
                        ${BRANCH_X + 12} ${spine.find((s) => s.id === b.loops)!.y}`}
                  markerEnd="url(#ah)" />
          )}
          <g className={`dnode branch s-${b.status}${selected === b.id ? ' sel' : ''}`}
             onClick={() => onSelect(selected === b.id ? null : b.id)}
             role="button" tabIndex={0}
             onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') {
               e.preventDefault(); onSelect(selected === b.id ? null : b.id); } }}>
            <title>{b.status === 'idle' ? `not taken — ${b.note}` : b.note}</title>
            <rect x={BRANCH_X} y={b.y - NODE_H / 2} width={BRANCH_W} height={NODE_H}
                  rx={7} />
            <text x={BRANCH_X + 9} y={b.y + 3.5}>{clip(b.label, 14)}</text>
          </g>
        </g>
      ))}

      {/* spine nodes last, so they sit over their edges */}
      {spine.map((n) => (
        <g key={n.id}
           className={`dnode s-${n.status}${liveId === n.id ? ' live' : ''}`
                      + `${selected === n.id ? ' sel' : ''}`}
           onClick={() => onSelect(selected === n.id ? null : n.id)}
           role="button" tabIndex={0}
           onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') {
             e.preventDefault(); onSelect(selected === n.id ? null : n.id); } }}>
          <title>
            {n.status === 'idle'
              ? `${n.label} — did not run`
              : `${n.label} — ${n.events.length} event(s)`}
          </title>
          <rect x={SPINE_X} y={n.y - NODE_H / 2} width={SPINE_W} height={NODE_H}
                rx={7} />
          <circle cx={SPINE_X + 11} cy={n.y} r={3}
                  fill={STATUS_COLOUR[n.status]} />
          <text x={SPINE_X + 21} y={n.y + 3.5}>{clip(n.label, 17)}</text>
          {n.children.length > 1 && (
            <text className="dcount" x={SPINE_X + SPINE_W - 8} y={n.y + 3.5}>
              ×{n.children.length}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}

/** Everything the graph cannot fit: codes, source, timing, the summary line. */
export function TraceDetail({ trace, nodeId }:
  { trace: ORCATraceEvent[]; nodeId: string }) {
  const events = trace.filter((e) => (
    e.node === nodeId ||
    (nodeId === 'assess_domain' && !!e.node?.startsWith('assess_'))
  ));
  const label = SPINE.find((n) => n.id === nodeId)?.label
    ?? BRANCHES.find((b) => b.id === nodeId)?.label ?? nodeId;

  if (!events.length) {
    const note = BRANCHES.find((b) => b.id === nodeId)?.note;
    return (
      <div className="dsel">
        <b>{label}</b>
        <div className="dsel-none">
          Did not run in this turn.{note ? ` ${note}.` : ''}
        </div>
      </div>
    );
  }

  return (
    <div className="dsel">
      <b>{label}</b>
      {events.map((e, i) => (
        <div key={i} className="dsel-row">
          <span className={`tdot${e.status === 'error' ? ' err'
            : e.status === 'degraded' || e.fallback_used ? ' warn' : ''}`} />
          <span className="dsel-name">
            {e.tool ?? e.node?.replace(/^assess_/, '') ?? '—'}
          </span>
          <span className="dsel-meta">
            {e.source ? `${e.source} · ` : ''}{e.duration_ms ?? 0} ms
          </span>
          {(e.codes?.length || e.fallback_used || e.summary) && (
            <div className="dsel-why">
              {[e.codes?.join(', '),
                e.fallback_used ? 'served by a fallback' : null,
                !e.tool ? e.summary : null].filter(Boolean).join(' — ')}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
