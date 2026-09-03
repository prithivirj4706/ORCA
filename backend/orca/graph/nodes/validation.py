"""validate -- gate G1 (07 section 7).

Deterministic. Decides whether the run has the evidence its plan declared
necessary, and therefore whether to re-plan, proceed, or stop. This is the gate
that turns "we could not reach the data" into an honest refusal rather than a
confident answer built on nothing.
"""
from __future__ import annotations

import time

from ...agents.contracts import ValidationReport
from ...agents.discovery import DiscoveryAgent
from ...assessment.engine import EvidencePool, _param_of
from ..events import node_event
from ..runtime import runtime_from
from ..state import OrcaGraphState

#: Factors whose meaning is a tool OUTCOME rather than a number. They are
#: satisfied only via EvidencePool.status, so "could not check" can never be
#: read as "nothing in force" (D-3).
_STATUS_FACTORS = ("official_warning_status",)


def build_pool(state: OrcaGraphState) -> EvidencePool:
    pool = EvidencePool()
    for env in state.get("tool_results") or []:
        pool.ingest(env)
    # Capabilities with no source in this environment are declared as gaps so
    # the answer states what it did not check.
    for gap in state.get("unavailable_capabilities") or []:
        for factor in str(gap.get("evidence", "")).split(", "):
            if factor and factor != "-":
                pool.add_gap(factor, "NOT_IMPLEMENTED", gap.get("reason"),
                             gap.get("tool"))
    return pool


def satisfied_evidence(state: OrcaGraphState, pool: EvidencePool) -> set[str]:
    """Which declared evidence keys the run actually obtained."""
    plan = state.get("plan")
    wanted = set(getattr(plan, "required_evidence", []) or []) | set(
        getattr(plan, "preferred_evidence", []) or [])
    got: set[str] = set()
    for factor in wanted:
        if factor in _STATUS_FACTORS:
            if pool.status.get(factor, {}).get("checked"):
                got.add(factor)
            continue
        if factor == "maritime_boundaries":
            if any(getattr(e, "tool", None) == "get_maritime_boundaries"
                   and e.data for e in (state.get("tool_results") or [])):
                got.add(factor)
            continue
        if pool.find(_param_of(factor)) is not None:
            got.add(factor)
    return got


def validate(state: OrcaGraphState, config=None) -> dict:
    started = time.perf_counter()
    rt = runtime_from(config)
    plan = state.get("plan")
    results = list(state.get("step_results") or [])

    pool = build_pool(state)
    got = satisfied_evidence(state, pool)
    required = list(getattr(plan, "required_evidence", []) or [])
    preferred = list(getattr(plan, "preferred_evidence", []) or [])
    required_gaps = [e for e in required if e not in got]

    # A plan with no executable steps is not a total failure -- it is a run that
    # honestly could not reach anything, which the answer must still explain.
    all_failed = bool(results) and all(r.outcome == "failed" for r in results)

    # A gap is only worth re-planning if some tool that yields it is available
    # AND has not already been tried. Re-issuing an identical request to a tool
    # that just answered is waste, not resilience.
    attempted = {r.tool for r in results}
    actionable = [
        gap for gap in required_gaps
        if any(rt.registry.is_available(tool) and tool not in attempted
               for tool in rt.registry.tools_yielding(gap))
    ]

    report = ValidationReport(
        valid_objects=len(pool.candidates),
        dropped_objects=0,
        required_gaps=required_gaps,
        actionable_gaps=actionable,
        preferred_gaps=[e for e in preferred if e not in got],
        all_steps_failed=all_failed,
        conflicts=[])

    discovery = DiscoveryAgent(llm=rt.llm, ledger=rt.ledger, budget=rt.budget)
    retrieval = discovery.report(
        plan, results, satisfied_evidence=got,
        modifications=list(state.get("modifications") or []),
        duration_ms=sum(r.duration_ms for r in results)) if plan else None

    return {
        "validation_report": report,
        "retrieval_report": retrieval.value if retrieval and retrieval.ok else None,
        "evidence_gaps": required_gaps,
        "node_events": [node_event(
            "validate", "success", started=started,
            summary=(f"{len(pool.candidates)} value(s); "
                     f"required gaps: {', '.join(required_gaps) or 'none'}"
                     + (f" (actionable: {', '.join(actionable)})" if actionable
                        else " (none actionable)" if required_gaps else "")),
            required_gaps=required_gaps, actionable_gaps=actionable,
            all_steps_failed=all_failed)],
    }
