"""assess_* -- the domain fan-out (07 sections 4, 5.2).

Each branch appends exactly one Assessment, including on failure, so the join
count always matches the dispatch count. Domains are assessed independently and
are never merged into a single score.
"""
from __future__ import annotations

import time
import uuid

from ...agents.risk import RiskAgent
from ...schemas.assessment import Assessment
from ...schemas.core import SpatialRef
from ...schemas.enums import Confidence, Domain, RegulatoryStatus, Verdict
from ..events import node_event
from ..runtime import runtime_from
from .analysis import _window
from .validation import build_pool


def _boundary_envelope(state):
    return next((e for e in (state.get("tool_results") or [])
                 if getattr(e, "tool", None) == "get_maritime_boundaries"), None)


def _fallback_assessment(domain: Domain, detail: str) -> Assessment:
    """A branch that failed hard still returns an assessment.

    A missing branch would stall the superstep; an INSUFFICIENT_EVIDENCE
    assessment is both truthful and joinable (07 section 5.2 guard).
    """
    verdict = (RegulatoryStatus.UNKNOWN if domain is Domain.REGULATORY
               else Verdict.INSUFFICIENT_EVIDENCE)
    return Assessment(
        assessment_id=f"as-{uuid.uuid4().hex[:10]}", domain=domain,
        verdict=verdict, confidence=Confidence.LOW,
        rationale=f"No assessment could be made for {domain.value}: {detail}")


def assess_domain_node(payload: dict, config=None) -> dict:
    started = time.perf_counter()
    rt = runtime_from(config)
    state = payload
    domain = Domain(payload["domain"])
    agent = RiskAgent(llm=rt.llm, ledger=rt.ledger, budget=rt.budget)

    loc = state.get("resolved_location") or {}
    spatial = SpatialRef.point(loc.get("lat"), loc.get("lon"),
                               label=loc.get("label"))
    start, end = _window(state)

    result = agent.assess(
        domain,
        pool=build_pool(state),
        boundary_env=_boundary_envelope(state),
        window_start=start, window_end=end, spatial=spatial)

    if not result.ok:
        a = _fallback_assessment(domain, result.failure.detail)
        return {"assessments": [a],
                "node_events": [node_event(f"assess_{domain.value.lower()}", "error",
                                           started=started,
                                           summary=result.failure.detail)]}
    return {
        "assessments": [result.value.assessment],
        "evidence": list(result.value.evidence),
        "not_evaluated": list(result.value.assessment.not_evaluated),
        "node_events": [node_event(f"assess_{domain.value.lower()}", "success",
                                   started=started, summary=result.reasoning_summary)],
    }
