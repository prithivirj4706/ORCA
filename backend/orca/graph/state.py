"""OrcaGraphState and its reducers (07_LANGGRAPH_WORKFLOW_SPEC.md section 3).

Invariant (section 3.1): no node overwrites another node's output. Fields written
by parallel branches use `add`, which is commutative and loses nothing, so a
fan-in is order-independent. Correction is expressed as a new appended record,
never as a mutation, which is what keeps the audit trail complete.
"""
from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, TypedDict


#: Sentinel meaning "this is a new run: start the channel empty".
#:
#: `add` is right for parallel branches WITHIN one run, but a checkpointed
#: thread reuses its state across turns, so without this every follow-up
#: question inherited the previous turn's assessments, evidence and alerts --
#: turn three showed turn one's verdict and three duplicated geofence alerts.
RESET = ["__orca_reset__"]


def add_or_reset(a: list | None, b: Any) -> list:
    """Append, unless the writer asked for a fresh run.

    The sentinel is composable: `RESET + [x]` clears the channel and then
    writes `x`, so a node can reset a channel it also writes to in the same
    update. Without that, the later key in the dict silently wins and the
    reset never happens.
    """
    if isinstance(b, list) and b and b[0] == "__orca_reset__":
        return list(b[1:])
    return list(a or []) + list(b or [])


def last_write(a: Any, b: Any) -> Any:
    """Single-writer fields. A None write does not erase a real value."""
    return b if b is not None else a


def merge_dict(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


def accumulate_budget(a: dict | None, b: dict | None) -> dict:
    """Budget is shared across branches, so numeric fields accumulate."""
    out = dict(a or {})
    for key, value in (b or {}).items():
        if isinstance(value, (int, float)) and isinstance(out.get(key), (int, float)):
            out[key] = out[key] + value
        else:
            out[key] = value
    return out


class OrcaGraphState(TypedDict, total=False):
    # ---- identity -------------------------------------------------------
    run_id: str
    session_id: str
    user_id: str | None
    role: Literal["fisher", "operator", "officer", "analyst", "reviewer", "admin"]

    # ---- input ----------------------------------------------------------
    query_text: str
    language: str
    client_location: dict | None
    #: A window supplied by the CALLER for this turn. Separate from
    #: `resolved_time_window`, which the graph writes and a checkpoint restores:
    #: reading caller input off an output channel made every later turn reuse
    #: the first turn's window (F-73).
    client_time_window: dict | None
    session_context: dict

    # ---- resolved context (deterministic) -------------------------------
    intent: str
    intent_confidence: float
    resolved_location: dict | None
    resolved_time_window: dict | None
    resolution_notes: Annotated[list, add_or_reset]
    clarification_needed: str | None

    # ---- planning -------------------------------------------------------
    plan: Any
    plan_version: int
    attempts: int
    unavailable_capabilities: Annotated[list, add_or_reset]

    # ---- retrieval (fan-in) ---------------------------------------------
    tool_results: Annotated[list, add_or_reset]
    step_results: Annotated[list, add_or_reset]
    modifications: Annotated[list, add_or_reset]
    retrieval_report: Any
    fallbacks_used: Annotated[list, add_or_reset]

    # ---- validation -----------------------------------------------------
    validation_report: Any
    evidence_gaps: Annotated[list, add_or_reset]

    # ---- geospatial -----------------------------------------------------
    alignment_report: Any
    derived: Annotated[list, add_or_reset]
    layers: Annotated[list, add_or_reset]

    # ---- assessment (fan-in) --------------------------------------------
    assessments: Annotated[list, add_or_reset]
    conflicts: Annotated[list, add_or_reset]
    alerts: Annotated[list, add_or_reset]
    map_layers: Annotated[list, add_or_reset]
    not_evaluated: Annotated[list, add_or_reset]

    # ---- evidence & output ----------------------------------------------
    evidence: Annotated[list, add_or_reset]
    claims: Annotated[list, add_or_reset]
    recommendation: Any
    disposition: str | None
    review_reason: str | None
    human_review: dict | None

    # ---- provenance & observability -------------------------------------
    provenance: Annotated[list, add_or_reset]
    node_events: Annotated[list, add_or_reset]
    errors: Annotated[list, add_or_reset]
    errors_fatal: bool
    budget: Annotated[dict, accumulate_budget]
    trace_id: str
