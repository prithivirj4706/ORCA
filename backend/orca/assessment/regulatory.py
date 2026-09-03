"""REGULATORY domain assessment.

Entirely deterministic: point-in-polygon over versioned geometry, read through
a configured jurisdiction policy. No LLM participates, and no threshold set
applies -- the REGULATORY domain has its own vocabulary
(12_RISK_AND_RECOMMENDATION_SPEC.md section 7).

Three rules the code enforces rather than describes:

  * a boundary type with no configured source is listed as NOT EVALUATED. It is
    never silently omitted, because an answer that omitted marine protected
    areas and restricted zones would read as "you are clear";
  * the most constraining outcome governs. Outcomes are never averaged;
  * containment within `near_boundary_km` of an edge is reported WITH the
    distance and at reduced confidence, so a point 400 m inside a boundary is
    not presented as if it were 40 km inside.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from ..schemas.assessment import Assessment, Driver, Evidence, NotEvaluated
from ..schemas.core import SpatialRef, TemporalRef
from ..schemas.enums import Confidence, Domain, RegulatoryStatus, ValueKind
from ..schemas.envelope import OrcaEnvelope
from ..schemas.errors import ErrorCode
from .engine import DomainResult
from .jurisdiction import (
    JurisdictionPolicy, load_jurisdiction_policy, most_constraining,
)

THRESHOLD_SET_ID = "boundary_implications_v0.1"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _unknown(reason: str, *, spatial: SpatialRef | None,
             window_start: datetime, window_end: datetime,
             not_evaluated: list[NotEvaluated],
             policy: JurisdictionPolicy,
             missing: list[str] | None = None) -> DomainResult:
    return DomainResult(
        Assessment(
            assessment_id=_new_id("as"), domain=Domain.REGULATORY,
            verdict=RegulatoryStatus.UNKNOWN, confidence=Confidence.LOW,
            spatial=spatial,
            temporal=TemporalRef(valid_time=window_start, valid_from=window_start,
                                 valid_to=window_end),
            not_evaluated=not_evaluated, missing_required=missing or [],
            threshold_set=THRESHOLD_SET_ID, threshold_set_status=policy.status,
            rationale=(f"No REGULATORY status issued: {reason}. "
                       f"Not knowing where a boundary lies is not the same as "
                       f"there being none.")),
        [])


def assess_regulatory(env: OrcaEnvelope, *, window_start: datetime,
                      window_end: datetime, spatial: SpatialRef | None = None,
                      policy: JurisdictionPolicy | None = None) -> DomainResult:
    """Evaluate the REGULATORY domain from a get_maritime_boundaries envelope."""
    policy = policy or load_jurisdiction_policy()

    # Boundary types the run could not evaluate at all. These are carried into
    # the assessment whatever the outcome.
    not_evaluated: list[NotEvaluated] = [
        NotEvaluated(factor=e.subject or "maritime_boundary", reason=e.code.value,
                     detail=(e.detail or None), tool=env.tool)
        for e in env.errors
        if e.code is ErrorCode.DATASET_UNAVAILABLE and e.subject
    ]

    containments = [d for d in env.data
                    if getattr(d, "parameter", None) == "point_in_boundary"]
    if not env.ok or not containments:
        # Whatever stopped the tool, say WHICH thing stopped it. A boundary
        # query outside the snapshot region and a boundary service outage are
        # both "unknown", but they are not the same problem.
        blocking = next((e for e in env.errors if e.subject is None
                         or e.subject == "maritime_boundary"), None)
        reason = (f"{blocking.code.value.lower().replace('_', ' ')} — "
                  f"{blocking.detail}" if blocking and blocking.detail
                  else "no boundary containment result was returned")
        not_evaluated.append(NotEvaluated(
            factor="maritime_boundary",
            reason=(blocking.code.value if blocking else "NOT_RETRIEVED"),
            detail=(blocking.detail[:300] if blocking and blocking.detail else None),
            tool=env.tool))
        return _unknown(reason, spatial=spatial, window_start=window_start,
                        window_end=window_end, not_evaluated=not_evaluated,
                        policy=policy, missing=["maritime_boundary"])

    drivers: list[Driver] = []
    evidence: list[Evidence] = []
    outcomes: list[str] = []
    near_boundary = False
    disputed = False

    for c in containments:
        detail = c.detail
        boundary_type = detail.get("boundary_type", "unknown")
        implication = policy.implications.get(boundary_type)
        if implication is None:
            not_evaluated.append(NotEvaluated(
                factor=boundary_type, reason="NO_IMPLICATION_CONFIGURED",
                detail=(f"{boundary_type} geometry was evaluated but the policy "
                        f"does not say what containment implies"), tool=env.tool))
            continue

        # A layer that publishes nothing for this jurisdiction cannot support
        # "outside". The adapter flags it; the assessment refuses to read it.
        gap = detail.get("jurisdiction_coverage")
        if gap and not gap.get("present_in_layer"):
            not_evaluated.append(NotEvaluated(
                factor=boundary_type, reason="INSUFFICIENT_COVERAGE",
                detail=(f"{gap.get('layer')} publishes no feature for "
                        f"{', '.join(gap.get('jurisdictions') or [])}"),
                tool=env.tool))
            continue

        inside = bool(detail.get("inside"))
        features = detail.get("features") or []
        if inside and features:
            placements = [policy.placement(f.get("iso_sov"), f.get("iso_ter"),
                                           f.get("sovereign")) for f in features]
            candidate = [implication.outcome(p) for p in placements]
            outcome = most_constraining(candidate)
            placement = placements[candidate.index(outcome)]
            names = ", ".join(str(f.get("name")) for f in features)
            if len(features) > 1 or any(f.get("disputed") for f in features):
                disputed = True
        else:
            placement, outcome, names = "none", implication.outcome("none"), ""

        basis = implication.basis(placement) or boundary_type
        distance = detail.get("distance_km")
        if detail.get("near_boundary"):
            near_boundary = True

        statement = (f"the position is inside {names} ({basis})" if inside
                     else f"the position is outside every {boundary_type} "
                          f"feature in the snapshot")
        if distance is not None:
            statement += (f", {distance:g} km from the "
                          f"{'edge' if inside else 'nearest such boundary'}")
        # "Outside every EEZ" is equally true of the high seas and of a car park
        # in Kochi. Where the policy supplies a caveat for that, it is appended
        # rather than left for the reader to supply.
        caveat = None if inside else implication.basis("none")
        if caveat:
            statement += f". {caveat[0].upper()}{caveat[1:]}"

        eid = _new_id("ev")
        evidence.append(Evidence(
            evidence_id=eid, domain=Domain.REGULATORY, statement=statement,
            parameter="point_in_boundary", value=inside, unit=None,
            value_kind=ValueKind.DERIVED, provenance_id=c.provenance_id,
            supports=[f"{THRESHOLD_SET_ID}:{boundary_type}:{placement}"],
            weight="primary" if boundary_type == "EEZ" else "supporting"))
        drivers.append(Driver(
            factor=boundary_type, value=inside, unit=None,
            band=outcome.lower(),
            threshold_id=f"{THRESHOLD_SET_ID}:{boundary_type}",
            evidence_id=eid))
        outcomes.append(outcome)

    constraining = [o for o in outcomes if o != "NOT_CONSTRAINING"]
    if not constraining:
        return _unknown(
            "no evaluated boundary type carries a regulatory implication here",
            spatial=spatial, window_start=window_start, window_end=window_end,
            not_evaluated=not_evaluated, policy=policy)

    governing = most_constraining(constraining)
    verdict = policy.status_for(governing) or RegulatoryStatus.UNKNOWN
    limiting = next((d for d in drivers if d.band == governing.lower()), None)
    for d in drivers:
        d.contribution = ("limiting" if d is limiting
                          else "context" if d.band == "not_constraining"
                          else "supporting")

    confidence = _confidence(verdict, near_boundary, not_evaluated, disputed)
    rationale = _rationale(verdict, limiting, near_boundary, not_evaluated,
                           disputed, policy)

    return DomainResult(
        Assessment(
            assessment_id=_new_id("as"), domain=Domain.REGULATORY,
            verdict=verdict, confidence=confidence, spatial=spatial,
            temporal=TemporalRef(valid_time=window_start, valid_from=window_start,
                                 valid_to=window_end),
            drivers=drivers, not_evaluated=not_evaluated,
            limiting_factor=(limiting.factor if limiting else None),
            threshold_set=THRESHOLD_SET_ID, threshold_set_status=policy.status,
            rationale=rationale),
        evidence)


def _confidence(verdict: RegulatoryStatus, near_boundary: bool,
                not_evaluated: list[NotEvaluated], disputed: bool) -> Confidence:
    """Deterministic ladder, same shape as the numeric domains."""
    if verdict is RegulatoryStatus.UNKNOWN:
        return Confidence.LOW
    level = 2                                        # start at high
    if near_boundary:
        # Within the precision band of the source geometry.
        level = min(level, 1)
    if disputed:
        level = min(level, 1)
    if verdict is RegulatoryStatus.PERMITTED and not_evaluated:
        # PERMITTED is the outcome that unchecked restrictions could overturn:
        # an unevaluated naval exercise area or MPA can only make things worse,
        # never better. Deviates from 06_AGENT_SPEC.md section 476, which shows
        # PERMITTED at high confidence.
        level = min(level, 1)
    return [Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH][max(0, level)]


def _rationale(verdict: RegulatoryStatus, limiting: Driver | None,
               near_boundary: bool, not_evaluated: list[NotEvaluated],
               disputed: bool, policy: JurisdictionPolicy) -> str:
    parts = [f"{verdict.value} for REGULATORY"]
    if limiting is not None:
        parts[0] += (f"; the governing factor is {limiting.factor} "
                     f"({'inside' if limiting.value else 'outside'})")
    parts[0] += "."
    if near_boundary:
        parts.append(f"The position is within {policy.near_boundary_km:g} km of a "
                     f"boundary, where the precision of the source geometry "
                     f"matters; the containment is reported, not asserted.")
    if disputed:
        parts.append("Overlapping or disputed claims apply here and are reported "
                     "as separate features; ORCA does not adjudicate between them.")
    if not_evaluated:
        parts.append("Not evaluated: "
                     + ", ".join(sorted({n.factor for n in not_evaluated})) + ".")
    parts.append("Boundary geometry is advisory context, not a legal "
                 "determination and not navigational authority.")
    return " ".join(parts)
