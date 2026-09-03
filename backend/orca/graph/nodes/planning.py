"""plan and replan (07 section 4).

`replan` is bounded by MAX_REPLANS and addresses only the gaps the validate gate
reported. When it cannot fill a required gap the run continues with a degraded
plan and the domain is marked INSUFFICIENT_EVIDENCE -- it never loops.
"""
from __future__ import annotations

import time

from ...agents.planner import PlannerAgent
from ..events import node_event
from ..runtime import runtime_from
from ..state import OrcaGraphState


def _planner(config):
    rt = runtime_from(config)
    return rt, PlannerAgent(llm=rt.llm, ledger=rt.ledger, budget=rt.budget)


def plan(state: OrcaGraphState, config=None) -> dict:
    started = time.perf_counter()
    rt, planner = _planner(config)
    result = planner.plan(
        query_text=state.get("query_text") or "",
        registry=rt.registry,
        resolved_location=state.get("resolved_location"),
        resolved_time_window=state.get("resolved_time_window"),
        intent=state.get("intent"))

    if not result.ok:
        return {"errors": [{"node": "plan", "code": result.failure.code,
                            "detail": result.failure.detail}],
                "errors_fatal": True,
                "node_events": [node_event("plan", "error", started=started,
                                           summary=result.failure.detail)]}

    p = result.value
    return {
        "plan": p,
        "plan_version": p.plan_version,
        "clarification_needed": p.clarification_needed,
        "unavailable_capabilities": list(p.unavailable_capabilities),
        "node_events": [node_event("plan", "success", started=started,
                                   summary=result.reasoning_summary,
                                   steps=len(p.steps),
                                   plan_version=p.plan_version)],
    }


def replan(state: OrcaGraphState, config=None) -> dict:
    started = time.perf_counter()
    rt, planner = _planner(config)
    previous = state.get("plan")
    report = state.get("validation_report")
    gaps = list(getattr(report, "required_gaps", []) or [])

    result = planner.plan(
        query_text=state.get("query_text") or "",
        registry=rt.registry,
        resolved_location=state.get("resolved_location"),
        resolved_time_window=state.get("resolved_time_window"),
        intent=state.get("intent"),
        previous=previous, required_gaps=gaps)

    attempts = state.get("attempts", 0) + 1
    if not result.ok:
        return {"attempts": attempts,
                "node_events": [node_event("replan", "error", started=started,
                                           summary=result.failure.detail)]}
    p = result.value
    return {
        "plan": p,
        "plan_version": p.plan_version,
        "attempts": attempts,
        "node_events": [node_event(
            "replan", "success", started=started, attempt=attempts,
            summary=f"addressing {', '.join(gaps) or 'no'} gap(s); "
                    f"{len(p.steps)} step(s)")],
    }
