"""Typed agent I/O (06_AGENT_SPEC.md sections 3.5, 4.5, 5.5; 07 section 7).

These are the objects agents hand to each other through graph state. They are
not part of 05_CANONICAL_DATA_SCHEMA.md -- that document models *data*, these
model the *work*. Both are validated before they reach state, so a hand-off can
never be a bare dict of unknown shape.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..schemas.enums import Domain

Necessity = Literal["required", "preferred", "optional"]
StepOutcome = Literal["satisfied", "degraded", "empty", "failed"]


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    necessity: Necessity = "preferred"
    domain: Domain | None = None
    parallel_group: int = 1


class Plan(BaseModel):
    """What the question actually needs. Emitted by the Planner, never guessed
    at by the executor."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(default_factory=lambda: _id("pl"))
    intent: str
    domains_required: list[Domain] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    preferred_evidence: list[str] = Field(default_factory=list)
    analysis: dict[str, Any] = Field(default_factory=dict)
    clarification_needed: str | None = None
    #: Capabilities the plan wanted but the registry does not offer. Recorded so
    #: the answer can say what it could not check, rather than omitting it.
    unavailable_capabilities: list[dict[str, str]] = Field(default_factory=list)
    reasoning_summary: str = ""
    plan_version: int = 1
    planner: str = "deterministic"

    def step(self, step_id: str) -> PlanStep | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def groups(self) -> list[int]:
        return sorted({s.parallel_group for s in self.steps})


class StepResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    tool: str
    outcome: StepOutcome
    codes: list[str] = Field(default_factory=list)
    envelope_ref: str | None = None
    source: str | None = None
    fallback_used: bool = False
    duration_ms: int = 0


class Modification(BaseModel):
    """An explicit, recorded widening of a plan step. Widening is permitted;
    silent substitution of a different variable or source is not (06 s4.3)."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    change: str
    reason: str
    applied_at: str


class RetrievalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(default_factory=lambda: _id("rr"))
    plan_id: str
    results: list[StepResult] = Field(default_factory=list)
    modifications: list[Modification] = Field(default_factory=list)
    evidence_coverage: dict[str, list[str]] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)
    fallbacks_used: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: int = 0

    @property
    def all_steps_failed(self) -> bool:
        return bool(self.results) and all(r.outcome == "failed" for r in self.results)


class ValidationReport(BaseModel):
    """Gate G1 (07 section 7). Deterministic; decides whether to re-plan."""

    model_config = ConfigDict(extra="forbid")

    valid_objects: int = 0
    dropped_objects: int = 0
    required_gaps: list[str] = Field(default_factory=list)
    #: Gaps a re-plan could actually fill: some tool yielding them is available
    #: and has not been attempted yet. A gap whose only source is unavailable,
    #: or whose tool already ran and returned what it had, is NOT actionable --
    #: re-planning it would re-issue an identical request (06 section 3.8).
    actionable_gaps: list[str] = Field(default_factory=list)
    preferred_gaps: list[str] = Field(default_factory=list)
    all_steps_failed: bool = False
    conflicts: list[str] = Field(default_factory=list)
    drop_reasons: list[dict[str, str]] = Field(default_factory=list)


class AlignmentReport(BaseModel):
    """What was made comparable, and what could not be (06 section 5.5)."""

    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(default_factory=lambda: _id("al"))
    analysis_frame: dict[str, Any] = Field(default_factory=dict)
    aligned: list[dict[str, Any]] = Field(default_factory=list)
    not_aligned: list[dict[str, Any]] = Field(default_factory=list)
    derived: list[str] = Field(default_factory=list)
    geometry_results: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_operations: list[dict[str, Any]] = Field(default_factory=list)
    layers: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
