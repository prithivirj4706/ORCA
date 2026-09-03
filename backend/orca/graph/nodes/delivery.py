"""conflict_resolve, evidence_assemble, review_gate, human_review, report,
finalize and error_handler (07 sections 4, 9, 11).

`evidence_assemble` is the last point at which the answer's factual content is
fixed. `report` then renders language over that fixed set, which is what makes
"the Reporting Agent cannot add facts" a structural property rather than a
request.
"""
from __future__ import annotations

import time

from ...agents.reporting import ReportingAgent
from ...assessment.synthesis import synthesise
from ...schemas.enums import Confidence, Disposition, Domain, Verdict
from ..events import node_event
from ..runtime import runtime_from
from ..state import OrcaGraphState


def conflict_resolve(state: OrcaGraphState, config=None) -> dict:
    """Cross-source conflict policy.

    Nothing to resolve yet: the current tool layer selects one source per
    parameter and records the choice, so no two values for the same parameter
    reach this point. The node exists as the declared seam so that adding a
    cross-checking source later changes one place.
    """
    started = time.perf_counter()
    return {"node_events": [node_event("conflict_resolve", "success",
                                       started=started,
                                       summary="no cross-source conflicts")]}


def evidence_assemble(state: OrcaGraphState, config=None) -> dict:
    """Deduplicate evidence and compute the answer-level not-evaluated list."""
    started = time.perf_counter()
    seen: set[str] = set()
    unique = []
    for e in state.get("evidence") or []:
        if e.evidence_id in seen:
            continue
        seen.add(e.evidence_id)
        unique.append(e)

    gaps, gap_keys = [], set()
    for n in state.get("not_evaluated") or []:
        key = (n.factor, n.reason)
        if key in gap_keys:
            continue
        gap_keys.add(key)
        gaps.append(n)

    # Geofencing notifications are a policy over numbers the boundary run
    # already produced -- no new retrieval (problem statement, capability 8).
    from ...assessment.geofence import geofence_alerts
    boundary = next((e for e in (state.get("tool_results") or [])
                     if getattr(e, "tool", None) == "get_maritime_boundaries"), None)
    alerts = [a.as_dict() for a in geofence_alerts(boundary)]

    return {
        "alerts": alerts,
        "node_events": [node_event(
            "evidence_assemble", "success", started=started,
            summary=(f"{len(unique)} evidence item(s), {len(gaps)} not evaluated"
                     + (f", {len(alerts)} geofence alert(s)" if alerts else "")),
            evidence=len(unique), not_evaluated=len(gaps), alerts=len(alerts))],
        "budget": {"evidence_items": len(unique)},
    }


def review_gate(state: OrcaGraphState, config=None) -> dict:
    """Gate G2. Computes the disposition from the assessments (07 section 7)."""
    started = time.perf_counter()
    assessments = list(state.get("assessments") or [])
    evidence = list(state.get("evidence") or [])
    s = synthesise(assessments, evidence)

    reason = None
    if s.disposition is Disposition.REVIEW_REQUIRED:
        safety = next((a for a in assessments if a.domain is Domain.SAFETY), None)
        if safety is not None and safety.verdict is Verdict.UNSAFE:
            reason = "safety verdict is UNSAFE"
        elif safety is not None and safety.confidence is Confidence.LOW:
            reason = "low confidence on a safety verdict"
        else:
            reason = "policy requires review"
    elif s.disposition is Disposition.BLOCKED:
        reason = "no safety statement can be issued"

    return {
        "disposition": s.disposition.value,
        "review_reason": reason,
        "node_events": [node_event("review_gate", "success", started=started,
                                   summary=f"disposition={s.disposition.value}"
                                           + (f" ({reason})" if reason else ""))],
    }


def human_review(state: OrcaGraphState, config=None) -> dict:
    """Durable interrupt (07 section 9).

    `interrupt()` suspends the run at a checkpoint; the process may restart
    while the decision is pending. Resume with
    `Command(resume=decision)` on the same thread_id.
    """
    from langgraph.types import interrupt

    started = time.perf_counter()
    decision = interrupt({
        "run_id": state.get("run_id"),
        "reason": state.get("review_reason"),
        "assessments": [a.model_dump(mode="json")
                        for a in (state.get("assessments") or [])],
        "disposition": state.get("disposition"),
    })
    return {
        "human_review": {
            "reviewer_id": decision.get("reviewer_id"),
            "reviewer_role": decision.get("reviewer_role"),
            "decision": decision.get("decision"),
            "rationale": decision.get("rationale"),
            "reviewed_at": decision.get("reviewed_at"),
        },
        "node_events": [node_event("human_review", "success", started=started,
                                   summary=f"reviewer {decision.get('decision')}")],
    }


def report(state: OrcaGraphState, config=None) -> dict:
    started = time.perf_counter()
    rt = runtime_from(config)
    agent = ReportingAgent(llm=rt.llm, ledger=rt.ledger, budget=rt.budget)

    result = agent.report(
        assessments=list(state.get("assessments") or []),
        evidence=list(state.get("evidence") or []),
        run_id=state.get("run_id"),
        query_text=state.get("query_text"),
        language=state.get("language") or "en",
        resolved_context={"location": state.get("resolved_location"),
                          "time_window": state.get("resolved_time_window")},
        not_evaluated=list(state.get("not_evaluated") or []))

    if not result.ok:
        return {"errors": [{"node": "report", "code": result.failure.code,
                            "detail": result.failure.detail}],
                "node_events": [node_event("report", "error", started=started,
                                           summary=result.failure.detail)]}
    rec = result.value
    review = state.get("human_review")
    
    # Geofence alerts are computed ONCE, at evidence_assemble, by
    # assessment/geofence.py. A second implementation here appended a rival set
    # through the `add` reducer, so every alert was emitted twice in two
    # different shapes, and that copy did not check whether a boundary type had
    # actually been evaluated.
    map_layers = []

    for env in state.get("tool_results") or []:
        for item in getattr(env, "data", []):
            # GeoJSON map layers from VectorFeatures
            if getattr(item, "type", None) == "VectorFeature":
                geom = getattr(item, "geometry_inline", None)
                if geom:
                    map_layers.append({
                        "id": getattr(item, "feature_id", "feature"),
                        "type": "geojson",
                        "name": getattr(item, "name") or getattr(item, "parameter", "feature"),
                        "data": {
                            "type": "Feature",
                            "geometry": geom,
                            "properties": getattr(item, "attributes", {})
                        }
                    })
            # Route optimization output from DerivedResult
            elif getattr(item, "type", None) == "DerivedResult" and getattr(item, "parameter", None) == "optimized_route":
                spatial = getattr(item, "spatial", None)
                if spatial and spatial.kind == "linestring" and spatial.coordinates:
                    map_layers.append({
                        "id": "optimized_route",
                        "type": "geojson",
                        "name": "Optimized Route",
                        "data": {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": spatial.coordinates
                            },
                            "properties": getattr(item, "detail", {})
                        }
                    })

    if review is not None or map_layers or state.get("alerts"):
        updates = {}
        if review is not None:
            updates["human_review"] = review
        # The Recommendation carries its own alerts so it is self-contained;
        # they are COMPUTED once, upstream, and only copied here.
        if state.get("alerts"):
            updates["alerts"] = list(state["alerts"])
        if map_layers:
            updates["map_layers"] = map_layers
        rec = rec.model_copy(update=updates)
        
    return {
        "recommendation": rec,
        "claims": list(rec.claims),
        "map_layers": map_layers,
        "node_events": [node_event("report", "success", started=started,
                                   summary=result.reasoning_summary,
                                   claims=len(rec.claims),
                                   map_layers=len(map_layers))],
    }


def error_handler(state: OrcaGraphState, config=None) -> dict:
    """An honest failure message. No verdict is produced (07 section 8)."""
    started = time.perf_counter()
    errors = list(state.get("errors") or [])
    reached = [r.tool for r in (state.get("step_results") or [])
               if r.outcome != "failed"]
    detail = "; ".join(str(e.get("detail", e)) for e in errors) or \
        "no data source could be reached"
    return {
        "recommendation": {
            "category": "CANNOT_ADVISE",
            "headline": ("ORCA could not reach the data needed to answer this. "
                         f"{detail}."),
            "reached": reached,
            "is_official_advisory": False,
        },
        "disposition": Disposition.BLOCKED.value,
        "node_events": [node_event("error_handler", "success", started=started,
                                   summary=detail[:160])],
    }


def finalize(state: OrcaGraphState, config=None) -> dict:
    """Persist, audit, emit. Terminal for every path."""
    started = time.perf_counter()
    rt = runtime_from(config)
    
    session_context = dict(state.get("session_context") or {})
    if state.get("resolved_location"):
        session_context["resolved_location"] = state.get("resolved_location")
    if state.get("resolved_time_window"):
        session_context["resolved_time_window"] = state.get("resolved_time_window")
    # Only a real topic is remembered. `session_context["intent"]` is what a
    # follow-up like "what about tomorrow?" inherits, and neither "out of
    # scope" nor "unclassified" is something a follow-up can be ABOUT --
    # persisting one made a single greeting poison every later turn in the
    # thread, which then answered out of scope too.
    intent = state.get("intent")
    if intent and intent not in ("unknown", "smalltalk_or_out_of_scope"):
        session_context["intent"] = intent
        
    return {
        "budget": {"wall_clock_ms": rt.budget.elapsed_ms()},
        "session_context": session_context,
        "node_events": [node_event(
            "finalize", "success", started=started,
            summary=(f"disposition={state.get('disposition') or 'n/a'}; "
                     f"{len(state.get('assessments') or [])} assessment(s)"),
            usage=rt.ledger.summary())],
    }
