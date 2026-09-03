"""Data Discovery Agent (06_AGENT_SPEC.md section 4).

Executes the plan and reports precisely what was and was not obtained. It knows
nothing about providers: it calls capability tools through the registry and
reads canonical envelopes back.

The one judgement it makes is whether to WIDEN an unsatisfied request. Widening
is table-driven and recorded as an explicit plan modification. Substituting a
different variable or source is never permitted, and `AUTH_REQUIRED` widens
nothing -- a credential problem is not fixed by asking a different question.
"""
from __future__ import annotations

import time
from typing import Any

from ..schemas.enums import EnvelopeStatus
from ..schemas.envelope import OrcaEnvelope
from ..schemas.errors import ErrorCode
from ..tools.registry import ToolRegistry
from .base import Agent, AgentResult
from .contracts import Modification, Plan, RetrievalReport, StepResult

#: Codes for which widening a request is defensible. A gridded ocean field that
#: returned nothing at this point may legitimately have data nearby.
WIDENABLE_CODES = frozenset({ErrorCode.NO_DATA, ErrorCode.INSUFFICIENT_COVERAGE})

#: Codes that must never provoke another request of any shape.
NEVER_WIDEN = frozenset({
    ErrorCode.AUTH_REQUIRED, ErrorCode.INVALID_LOCATION, ErrorCode.INVALID_BBOX,
    ErrorCode.INVALID_TIME_WINDOW, ErrorCode.SCHEMA_VALIDATION_FAILED,
})

#: Envelope statuses that count as a usable result for a step.
_SATISFIED = frozenset({EnvelopeStatus.SUCCESS})


def classify_outcome(env: OrcaEnvelope) -> str:
    """satisfied | degraded | empty | failed (06 section 4.7).

    EMPTY is a RESULT, not a failure: "no warning is in force" is an answer.
    """
    if env.status in _SATISFIED:
        return "satisfied"
    if env.status is EnvelopeStatus.PARTIAL:
        return "degraded"
    if env.status is EnvelopeStatus.EMPTY:
        return "empty"
    return "failed"


class DiscoveryAgent(Agent):
    name = "discovery"

    def execute_step(self, step, registry: ToolRegistry,
                     ) -> tuple[OrcaEnvelope | None, StepResult, Modification | None]:
        """Run one plan step. Always returns; never raises (07 section 6)."""
        started = time.perf_counter()
        try:
            self.budget.spend_call()
            env = registry.call(step.tool, **step.args)
        except Exception as exc:
            ms = int((time.perf_counter() - started) * 1000)
            return None, StepResult(step_id=step.step_id, tool=step.tool,
                                    outcome="failed",
                                    codes=[ErrorCode.ADAPTER_ERROR.value],
                                    duration_ms=ms), None

        modification = None
        outcome = classify_outcome(env)

        # One bounded widening attempt for a required step that came back thin.
        if outcome in {"degraded", "empty", "failed"} and step.necessity == "required":
            widened = self._widen(step, env, registry)
            if widened is not None:
                env2, modification = widened
                if classify_outcome(env2) in {"satisfied", "degraded"}:
                    env = env2
                    outcome = classify_outcome(env)

        ms = int((time.perf_counter() - started) * 1000)
        res = StepResult(
            step_id=step.step_id, tool=step.tool, outcome=outcome,
            codes=sorted({c.value for c in env.codes()}),
            envelope_ref=env.request_id,
            source=env.source_resolution.actual_source,
            fallback_used=bool(env.source_resolution.fallback_used),
            duration_ms=ms)
        return env, res, modification

    def _widen(self, step, env: OrcaEnvelope, registry: ToolRegistry):
        """Widen once, if the codes and the tool contract both permit it."""
        codes = set(env.codes())
        if codes & NEVER_WIDEN or not (codes & WIDENABLE_CODES):
            return None
        spec = registry.spec(step.tool)
        if "radius_km" not in spec.widenable:
            return None                            # e.g. a warning lookup: never
        current = float(step.args.get("radius_km") or 50.0)
        widened_args = dict(step.args, radius_km=current * 2)
        try:
            self.budget.spend_call()
            env2 = registry.call(step.tool, **widened_args)
        except Exception:
            return None
        return env2, Modification(
            step_id=step.step_id,
            change=f"radius_km {current:g} -> {current * 2:g}",
            reason=f"{'/'.join(sorted(c.value for c in codes & WIDENABLE_CODES))} "
                   f"at {current:g} km",
            applied_at=_now_iso())

    def report(self, plan: Plan, results: list[StepResult], *,
               satisfied_evidence: set[str],
               modifications: list[Modification] | None = None,
               duration_ms: int = 0) -> AgentResult[RetrievalReport]:
        """Assemble the RetrievalReport consumed by the validate gate."""
        try:
            coverage = {
                "required_satisfied": sorted(e for e in plan.required_evidence
                                             if e in satisfied_evidence),
                "required_missing": sorted(e for e in plan.required_evidence
                                           if e not in satisfied_evidence),
                "preferred_missing": sorted(e for e in plan.preferred_evidence
                                            if e not in satisfied_evidence),
            }
            report = RetrievalReport(
                plan_id=plan.plan_id, results=results,
                modifications=modifications or [],
                evidence_coverage=coverage,
                fallbacks_used=[{"step_id": r.step_id, "tool": r.tool,
                                 "actual": r.source}
                                for r in results if r.fallback_used],
                duration_ms=duration_ms)
            n_ok = sum(1 for r in results if r.outcome == "satisfied")
            summary = (f"{len(results)} step(s): {n_ok} satisfied, "
                       f"{sum(1 for r in results if r.outcome == 'degraded')} degraded, "
                       f"{sum(1 for r in results if r.outcome == 'empty')} empty, "
                       f"{sum(1 for r in results if r.outcome == 'failed')} failed.")
            if coverage["required_missing"]:
                summary += (" Missing required evidence: "
                            + ", ".join(coverage["required_missing"]) + ".")
            return AgentResult(agent=self.name, value=report,
                               reasoning_summary=summary)
        except Exception as exc:
            return self.failed("DISCOVERY_ERROR", f"{type(exc).__name__}: {exc}")


def _now_iso() -> str:
    from ..schemas.core import utcnow
    return utcnow().isoformat()
