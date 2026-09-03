"""Evidence, claims, assessments and recommendations.

Four domains are assessed independently and are NEVER merged into a single
score (12_RISK_AND_RECOMMENDATION_SPEC.md section 1).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .core import SpatialRef, TemporalRef, Uncertainty, utcnow
from .data import Conflict
from .enums import Confidence, Disposition, Domain, RegulatoryStatus, ValueKind, Verdict


class Evidence(BaseModel):
    """The assessment-facing view of a value."""
    type: Literal["Evidence"] = "Evidence"
    evidence_id: str
    domain: Domain
    statement: str
    parameter: str
    value: float | bool | None = None
    unit: str | None = None
    value_kind: ValueKind
    provenance_id: str
    supports: list[str] = Field(default_factory=list)   # threshold ids
    weight: Literal["primary", "supporting", "context"] = "supporting"


class Claim(BaseModel):
    """A sentence-level assertion, bound to the evidence that supports it."""
    type: Literal["Claim"] = "Claim"
    claim_id: str
    text: str
    claim_kind: Literal["observation", "forecast", "derived", "interpretation", "quote"]
    evidence_ids: list[str] = Field(default_factory=list)
    domain: Domain | None = None
    confidence: Confidence | None = None
    official_source: bool = False

    @model_validator(mode="after")
    def _material_claims_need_evidence(self) -> "Claim":
        if self.claim_kind != "quote" and not self.evidence_ids:
            raise ValueError(
                "SCHEMA_VALIDATION_FAILED: a material claim must reference at least "
                "one evidence_id"
            )
        return self


class Driver(BaseModel):
    """A factor that contributed to a verdict."""
    factor: str
    value: float | bool | None = None
    unit: str | None = None
    band: str | None = None                       # favourable | marginal | ...
    threshold_id: str | None = None
    contribution: Literal["limiting", "supporting", "context"] = "supporting"
    evidence_id: str | None = None
    #: The band EDGES this factor was judged against, as
    #: ``{band: [low, high]}`` with ``None`` for an open end. Carried so a
    #: renderer can place a value at its true position on a real axis instead
    #: of inventing one. A gauge whose axis is made up is a made-up fact, so
    #: the interface draws equal-width bands until these arrive.
    bands: dict[str, list[float | None]] | None = None
    #: True when a HIGHER value is worse, which tells a renderer which end of
    #: the axis is the bad one without it having to guess from the band order.
    higher_is_worse: bool | None = None


class NotEvaluated(BaseModel):
    factor: str
    reason: str                                   # canonical code or short phrase
    detail: str | None = None
    tool: str | None = None


class Assessment(BaseModel):
    type: Literal["Assessment"] = "Assessment"
    assessment_id: str
    domain: Domain
    verdict: Verdict | RegulatoryStatus
    confidence: Confidence
    spatial: SpatialRef | None = None
    temporal: TemporalRef | None = None
    drivers: list[Driver] = Field(default_factory=list)
    not_evaluated: list[NotEvaluated] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    #: Required factors whose absence CAPPED this verdict rather than blocking
    #: it. A non-empty list means ORCA could not check something that would have
    #: been allowed to override its own thresholds, so the verdict is a ceiling,
    #: not a measurement (O-1).
    verdict_capped_by: list[str] = Field(default_factory=list)
    limiting_factor: str | None = None
    official_warning_status: dict[str, Any] | None = None
    uncertainty: Uncertainty | None = None
    threshold_set: str | None = None
    threshold_set_status: str | None = None
    conflicts: list[str] = Field(default_factory=list)
    rationale: str = ""
    value_kind: ValueKind = ValueKind.INTERPRETATION
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _regulatory_uses_its_own_vocabulary(self) -> "Assessment":
        if self.domain is Domain.REGULATORY:
            if not isinstance(self.verdict, RegulatoryStatus):
                raise ValueError(
                    "SCHEMA_VALIDATION_FAILED: REGULATORY uses PERMITTED/RESTRICTED/"
                    "PROHIBITED/UNKNOWN"
                )
        elif isinstance(self.verdict, RegulatoryStatus):
            raise ValueError(
                f"SCHEMA_VALIDATION_FAILED: {self.domain.value} may not use a "
                f"regulatory status"
            )
        if (self.domain is not Domain.SAFETY
                and self.verdict is Verdict.UNSAFE):
            raise ValueError(
                "SCHEMA_VALIDATION_FAILED: only SAFETY may return UNSAFE"
            )
        return self


class Recommendation(BaseModel):
    """The composed result. It CONTAINS assessments; it does not replace them."""
    type: Literal["Recommendation"] = "Recommendation"
    recommendation_id: str
    run_id: str | None = None
    query_text: str | None = None
    language: str = "en"
    resolved_context: dict[str, Any] = Field(default_factory=dict)
    assessments: list[Assessment] = Field(default_factory=list)
    category: str
    headline: str
    limiting_domain: Domain | None = None
    limiting_factor: str | None = None
    narrative: str = ""
    claims: list[Claim] = Field(default_factory=list)
    reasoning_summary: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    not_evaluated: list[NotEvaluated] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW
    disposition: Disposition = Disposition.AUTO_RELEASE
    human_review: dict[str, Any] | None = None
    is_official_advisory: bool = False
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    map_layers: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer_id: str = "disc.not_official_advisory"
    generated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _never_official(self) -> "Recommendation":
        # ORCA output is never an official advisory. Enforced structurally, not
        # left to wording (12_RISK_AND_RECOMMENDATION_SPEC.md section 1).
        if self.is_official_advisory:
            raise ValueError(
                "SCHEMA_VALIDATION_FAILED: an ORCA recommendation may not be marked "
                "as an official advisory"
            )
        return self
