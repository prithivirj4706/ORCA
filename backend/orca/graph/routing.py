"""Conditional edge functions (07_LANGGRAPH_WORKFLOW_SPEC.md section 5.1).

Routing is deterministic and reads only state, so a run's path is reproducible
from its checkpoints.
"""
from __future__ import annotations

from langgraph.types import Send

from ..schemas.enums import Domain
from .runtime import runtime_from
from .state import OrcaGraphState


def route_after_intent(state: OrcaGraphState) -> str:
    if state.get("errors_fatal"):
        return "error"
    if state.get("intent") == "smalltalk_or_out_of_scope":
        return "out_of_scope"
    return "plan"


def route_after_plan(state: OrcaGraphState):
    """Fan out one `tool_exec` per plan step (07 section 6).

    A plan that asks a clarifying question, or that has no executable step
    because every capability it needs is unavailable, must still reach the
    validate gate -- the answer has to say what could not be checked.
    """
    if state.get("errors_fatal"):
        return "error_handler"
    plan = state.get("plan")
    if plan is None:
        return "error_handler"
    if plan.clarification_needed:
        return "clarify"
    if not plan.steps:
        return "validate"

    rt = runtime_from(None)
    carry = {k: state.get(k) for k in
             ("run_id", "resolved_location", "resolved_time_window")}
    return [Send("tool_exec", {**carry, "step": step}) for step in plan.steps]


def route_after_validation(state: OrcaGraphState) -> str:
    report = state.get("validation_report")
    if report is None:
        return "total_failure"
    if report.all_steps_failed:
        return "total_failure"
    attempts = state.get("attempts", 0)
    # Only an ACTIONABLE gap justifies another attempt. A required input with no
    # reachable source degrades the domain to INSUFFICIENT_EVIDENCE instead of
    # spinning the loop (06 section 3.8).
    if report.actionable_gaps and attempts < _max_replans(state):
        return "replan"
    return "proceed"


def _max_replans(state: OrcaGraphState) -> int:
    from .runtime import MAX_REPLANS
    return MAX_REPLANS


def route_after_replan(state: OrcaGraphState):
    """Re-dispatch the new plan, or give up and assess what we have."""
    plan = state.get("plan")
    if plan is None or not plan.steps:
        return "geo_reason"
    carry = {k: state.get(k) for k in
             ("run_id", "resolved_location", "resolved_time_window")}
    return [Send("tool_exec", {**carry, "step": step}) for step in plan.steps]


def fan_out_assessments(state: OrcaGraphState):
    """One branch per REQUESTED domain. Only what the plan asked for runs."""
    plan = state.get("plan")
    domains = list(getattr(plan, "domains_required", []) or [])
    if not domains:
        return "evidence_assemble"
    # The whole state is carried so each branch can rebuild the evidence pool
    # independently; branches only ever append, so they cannot interfere.
    return [Send("assess_domain", {**dict(state), "domain": Domain(d).value})
            for d in domains]


def route_review(state: OrcaGraphState) -> str:
    return state.get("disposition") or "AUTO_RELEASE"
