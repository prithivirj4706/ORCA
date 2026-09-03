"""Deterministic assessment engine.

Four domains, evaluated independently and never merged into a single score.
No LLM participates here: verdicts come from documented thresholds applied to
evidence that passed sufficiency, staleness and representativeness filters
(12_RISK_AND_RECOMMENDATION_SPEC.md).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable

from ..geospatial.temporal import Alignment, align
from ..schemas.assessment import Assessment, Driver, Evidence, NotEvaluated
from ..schemas.core import Provenance, SpatialRef, TemporalRef
from ..schemas.enums import (
    Confidence, Domain, QualityFlag, RegulatoryStatus, Representativeness, ValueKind,
    Verdict,
)
from ..schemas.envelope import OrcaEnvelope
from ..schemas.errors import ErrorCode
from . import thresholds as th
from .staleness import usable_age_days

#: Threshold factor  ->  (canonical parameter, transform)
#: Some factors are a function of a parameter rather than the parameter itself.
FACTOR_SOURCES: dict[str, tuple[str, Callable[[float], float]]] = {
    "significant_wave_height": ("significant_wave_height", lambda v: v),
    "wind_speed": ("wind_speed", lambda v: v),
    "wind_gust": ("wind_gust", lambda v: v),
    "swell_height": ("swell_height", lambda v: v),
    "current_speed": ("current_speed", lambda v: v),
    "cyclone_distance_km": ("cyclone_distance_km", lambda v: v),
    "sst_anomaly_abs": ("sst_anomaly", abs),
    "chlorophyll_ratio_to_local_median": ("chlorophyll_ratio_to_local_median", lambda v: v),
    "pfz_distance_km": ("pfz_distance_km", lambda v: v),
}

#: Factors that are presence-based rather than numeric.
NON_NUMERIC_FACTORS = frozenset({"official_warning_status", "pfz_advisory", "lightning"})

#: What the PRESENCE and the ABSENCE of each presence-based factor mean.
#:
#: The asymmetry is the point. A warning or a lightning strike in force is
#: adverse, and its confirmed absence is reassuring, so both directions band.
#: A PFZ advisory nearby is a positive signal, but its absence says nothing --
#: INCOIS issues advisories where conditions warrant, not everywhere every day,
#: so "no advisory here today" is not evidence that fishing is poor. Banding the
#: absence would invent an unfavourable finding out of an editorial decision.
PRESENCE_SEMANTICS: dict[str, dict[str, str | None]] = {
    "official_warning_status": {"present": "unsafe", "absent": "favourable"},
    "lightning": {"present": "unsafe", "absent": "favourable"},
    "pfz_advisory": {"present": "favourable", "absent": None},
}

DOMAIN_THRESHOLD_SET = {
    Domain.SAFETY: "small_craft_v0.1",
    Domain.FISHING_SUITABILITY: "fishing_v0.1",
}

_BAND_TO_VERDICT = {
    "favourable": Verdict.FAVOURABLE,
    "marginal": Verdict.MARGINAL,
    "unfavourable": Verdict.UNFAVOURABLE,
    "unsafe": Verdict.UNSAFE,
}
_VERDICT_SEVERITY = [Verdict.FAVOURABLE, Verdict.MARGINAL,
                     Verdict.UNFAVOURABLE, Verdict.UNSAFE]
_CONFIDENCE_LADDER = [Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]


@dataclass(slots=True)
class Candidate:
    """A retrieved value, before any domain has decided whether it may be used."""
    parameter: str
    value: float | None
    unit: str | None
    provenance_id: str
    valid_time: datetime
    representativeness: Representativeness
    value_kind: ValueKind
    quality_flag: QualityFlag
    source: str
    dataset: str | None
    node_distance_km: float | None = None
    lead_time_h: float | None = None


@dataclass(slots=True)
class EvidencePool:
    """Everything retrieved for a run, plus the failures that shaped it."""
    candidates: list[Candidate] = field(default_factory=list)
    gaps: list[NotEvaluated] = field(default_factory=list)
    #: Presence-based factors whose meaning is the tool OUTCOME, not a number.
    #: "no warning in force" is a genuine result and must be distinguishable
    #: from "we could not check", which is why it lives here rather than being
    #: encoded as a magic numeric value.
    status: dict[str, dict] = field(default_factory=dict)

    def ingest(self, env: OrcaEnvelope) -> None:
        self._ingest_status(env)
        prov = {p.provenance_id: p for p in env.provenance}
        for obj in env.data:
            pid = getattr(obj, "provenance_id", None)
            value = getattr(obj, "value", None)
            if pid is None or pid not in prov or not isinstance(value, (int, float)):
                continue
            p: Provenance = prov[pid]
            t = getattr(obj, "temporal", None)
            q = getattr(obj, "quality", None)
            self.candidates.append(Candidate(
                parameter=obj.parameter,
                value=float(value),
                unit=getattr(obj, "unit", None),
                provenance_id=pid,
                valid_time=t.valid_time,
                representativeness=t.representativeness,
                value_kind=getattr(obj, "value_kind", ValueKind.OBSERVED),
                quality_flag=q.flag if q else QualityFlag.UNKNOWN,
                source=p.source,
                dataset=p.dataset,
                node_distance_km=(q.nearest_node_distance_km if q else None),
                lead_time_h=(t.lead_time_h if t else None),
            ))
        for err in env.errors:
            if err.severity == "info" or err.code is ErrorCode.STALE_DATA:
                continue
            self.gaps.append(NotEvaluated(
                factor=err.subject or env.tool, reason=err.code.value,
                detail=err.detail[:160] or None, tool=env.tool))

    def _ingest_status(self, env: OrcaEnvelope) -> None:
        if env.tool == "get_pfz":
            self._ingest_pfz_status(env)
            return
        if env.tool != "get_marine_warnings":
            return
        codes = set(env.codes())
        warnings = [d for d in env.data if getattr(d, "type", None) == "MarineWarning"]
        if warnings:
            self.status["official_warning_status"] = {
                "active": True, "checked": True,
                "count": len(warnings),
                "severity": max(getattr(w, "severity", "") for w in warnings),
                "provenance_id": getattr(warnings[0], "provenance_id", None),
            }
        elif ErrorCode.NO_ACTIVE_WARNING in codes:
            self.status["official_warning_status"] = {
                "active": False, "checked": True,
                "provenance_id": (env.provenance[0].provenance_id
                                  if env.provenance else None),
            }
        # Any other outcome (AUTH_REQUIRED, SOURCE_UNAVAILABLE) leaves the status
        # unset, so the factor stays unsatisfied and no safety verdict is issued.

    def _ingest_pfz_status(self, env: OrcaEnvelope) -> None:
        """`pfz_advisory` is presence-based: its meaning is the tool OUTCOME.

        "checked, none in force nearby" is a result; "could not check" is not.
        Only the former sets the status, so an unreachable advisory service can
        never read as an absence of advisories (D-3).
        """
        quality = env.quality or {}
        if not quality.get("advisory_checked"):
            return
        self.status["pfz_advisory"] = {
            "active": bool(quality.get("advisory_present")),
            "checked": True,
            "issued": quality.get("issued"),
            "sector": quality.get("sector"),
            "provenance_id": (env.provenance[0].provenance_id
                              if env.provenance else None),
        }

    def add_gap(self, factor: str, reason: str, detail: str | None = None,
                tool: str | None = None) -> None:
        self.gaps.append(NotEvaluated(factor=factor, reason=reason,
                                      detail=detail, tool=tool))

    def find(self, parameter: str) -> Candidate | None:
        matches = [c for c in self.candidates if c.parameter == parameter]
        if not matches:
            return None
        # Prefer the most recent, then the closest node.
        return max(matches, key=lambda c: (c.valid_time,
                                           -(c.node_distance_km or 0.0)))


@dataclass(slots=True)
class DomainResult:
    assessment: Assessment
    evidence: list[Evidence]


def _presence_statement(factor: str, active: bool, status: dict) -> str:
    if factor == "official_warning_status":
        return ("an official marine warning is in force" if active else
                "no official marine warning is in force for this area and time")
    if factor == "pfz_advisory":
        if not active:
            return ("no INCOIS Potential Fishing Zone advisory is in force "
                    "within the search radius")
        issued = status.get("issued")
        return ("an INCOIS Potential Fishing Zone advisory is in force nearby"
                + (f", issued {issued}" if issued else ""))
    if factor == "lightning":
        return ("lightning activity was detected" if active else
                "no lightning activity was detected")
    return f"{factor} = {active}"


def _param_of(factor: str) -> str:
    return FACTOR_SOURCES.get(factor, (factor, None))[0]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def assess_domain(domain: Domain, pool: EvidencePool, *,
                  window_start: datetime, window_end: datetime,
                  spatial: SpatialRef | None = None,
                  threshold_set_id: str | None = None) -> DomainResult:
    """Evaluate one domain. Steps mirror 12_RISK_AND_RECOMMENDATION_SPEC.md section 3."""
    set_id = threshold_set_id or DOMAIN_THRESHOLD_SET[domain]
    tset = th.load(set_id)
    drivers: list[Driver] = []
    evidence: list[Evidence] = []
    # Only carry gaps this domain actually cares about. A missing wave forecast
    # is a SAFETY gap; listing it under FISHING_SUITABILITY is noise.
    relevant = (set(tset.required_factors) | set(tset.preferred_factors)
                | set(tset.optional_factors) | set(tset.factors))
    relevant |= {p for f in relevant for p, _ in [FACTOR_SOURCES.get(f, (f, None))]}
    not_evaluated: list[NotEvaluated] = [
        g for g in pool.gaps
        if g.factor in relevant or _param_of(g.factor) in relevant
    ]
    quality_penalties = 0
    warning_status: dict | None = None

    considered = (tuple(tset.required_factors) + tuple(tset.preferred_factors)
                  + tuple(tset.optional_factors)
                  + tuple(f for f in tset.factors if f not in tset.required_factors))
    seen: set[str] = set()
    usable_required: set[str] = set()
    usable_count = 0

    for factor in considered:
        if factor in seen:
            continue
        seen.add(factor)

        if factor in NON_NUMERIC_FACTORS:
            st = pool.status.get(factor)
            if st is None or not st.get("checked"):
                if factor not in {n.factor for n in not_evaluated}:
                    not_evaluated.append(NotEvaluated(
                        factor=factor, reason="NOT_RETRIEVED",
                        detail="this factor was not successfully checked in this run"))
                continue
            if factor == "official_warning_status":
                warning_status = st
            active = bool(st.get("active"))
            semantics = PRESENCE_SEMANTICS.get(factor, {})
            band = semantics.get("present" if active else "absent")

            eid = _new_id("ev")
            evidence.append(Evidence(
                evidence_id=eid, domain=domain,
                statement=_presence_statement(factor, active, st),
                parameter=factor, value=active, unit=None,
                value_kind=ValueKind.OBSERVED,
                provenance_id=st.get("provenance_id") or "pv-unknown",
                supports=[f"{set_id}:{factor}"],
                weight="primary" if band else "context"))

            if band is None:
                # Checked, but its absence carries no verdict weight. Recorded
                # as evidence so the answer can say it was checked.
                continue
            drivers.append(Driver(
                factor=factor, value=active, band=band,
                threshold_id=f"{set_id}:{factor}", evidence_id=eid,
                contribution="supporting"))
            if factor in tset.required_factors:
                usable_required.add(factor)
            usable_count += 1
            continue

        param, transform = FACTOR_SOURCES.get(factor, (factor, lambda v: v))
        cand = pool.find(param)
        if cand is None or cand.value is None:
            if factor not in {n.factor for n in not_evaluated}:
                not_evaluated.append(NotEvaluated(
                    factor=factor, reason="NOT_RETRIEVED",
                    detail=f"no value for {param!r} in this run"))
            continue

        # -- 1. FILTER: may this product class serve this domain, and does its
        #    validity reach the analysis window?
        decision = align(cand.valid_time, cand.representativeness,
                         window_start=window_start, window_end=window_end,
                         domain=domain,
                         usable_age_days=usable_age_days(factor))
        if not decision.usable_as_primary:
            not_evaluated.append(NotEvaluated(
                factor=factor,
                reason=("REPRESENTATIVENESS_MISMATCH"
                        if decision.alignment is Alignment.CONTEXT_ONLY
                        else "STALE_DATA"),
                detail=decision.reason,
                tool=None))
            continue
        if cand.quality_flag in (QualityFlag.INVALID, QualityFlag.SUSPECT):
            not_evaluated.append(NotEvaluated(
                factor=factor, reason="QUALITY_EXCLUDED",
                detail=f"quality flag={cand.quality_flag.value}"))
            continue
        if cand.quality_flag is QualityFlag.DEGRADED:
            quality_penalties += 1

        # -- 3. RULES
        spec = tset.factors.get(factor)
        if spec is None:
            continue
        val = transform(cand.value)
        band = spec.band_for(val)
        if band is None:
            not_evaluated.append(NotEvaluated(
                factor=factor, reason="NO_BAND",
                detail=f"value {val} falls outside every defined band"))
            continue

        eid = _new_id("ev")
        evidence.append(Evidence(
            evidence_id=eid, domain=domain,
            statement=f"{factor} is {val:g} {spec.unit or ''}".strip(),
            parameter=cand.parameter, value=val, unit=spec.unit,
            value_kind=cand.value_kind, provenance_id=cand.provenance_id,
            supports=[f"{set_id}:{factor}:{band}"],
            weight="primary" if factor in tset.required_factors else "supporting"))
        drivers.append(Driver(
            factor=factor, value=val, unit=spec.unit, band=band,
            threshold_id=f"{set_id}:{factor}", evidence_id=eid,
            # The edges travel WITH the driver so a gauge can place the value
            # on the real axis it was judged against rather than a drawn-to-fit
            # one. They are the same numbers the band decision used.
            bands={b: list(rng) for b, rng in spec.bands.items()},
            higher_is_worse=spec.higher_is_worse))
        usable_count += 1
        if factor in tset.required_factors:
            usable_required.add(factor)

    # A factor that produced a usable driver is not a gap, even if its envelope
    # also carried a quality caveat such as INSUFFICIENT_COVERAGE.
    driver_factors = {d.factor for d in drivers}
    driver_params = {_param_of(f) for f in driver_factors}
    not_evaluated = [n for n in not_evaluated
                     if n.factor not in driver_factors
                     and n.factor not in driver_params]

    # -- 2. SUFFICIENCY
    #
    # A missing required factor either BLOCKS the verdict or CAPS it (O-1). An
    # authority check is not a measurement: without wave height there is no sea
    # state to assess, but without a warning check there is -- what is missing
    # is the authority that would override it. So a capping factor yields a
    # ceilinged verdict, and everything else still refuses.
    missing_required = [f for f in tset.required_factors if f not in usable_required]
    capping = [f for f in missing_required if tset.cap_for(f)]
    blocking = [f for f in missing_required if f not in capping]
    insufficient = bool(blocking) or usable_count < tset.min_usable_factors

    if insufficient:
        reason = (f"required input(s) unavailable: {', '.join(blocking)}"
                  if blocking else
                  f"fewer than {tset.min_usable_factors} usable indicator(s)")
        return DomainResult(
            Assessment(
                assessment_id=_new_id("as"), domain=domain,
                verdict=Verdict.INSUFFICIENT_EVIDENCE, confidence=Confidence.LOW,
                spatial=spatial,
                temporal=TemporalRef(valid_time=window_start, valid_from=window_start,
                                     valid_to=window_end),
                drivers=drivers, not_evaluated=not_evaluated,
                missing_required=missing_required,
                threshold_set=set_id, threshold_set_status=tset.status,
                rationale=(f"No {domain.value} verdict issued: {reason}. "
                           f"Absence of evidence is not evidence of safety."
                           if domain is Domain.SAFETY else
                           f"No {domain.value} verdict issued: {reason}.")),
            evidence)

    # -- 4. COMBINE: worst band governs. Never averaged.
    worst = max(drivers, key=lambda d: _VERDICT_SEVERITY.index(_BAND_TO_VERDICT[d.band]))
    verdict = _BAND_TO_VERDICT[worst.band]
    for d in drivers:
        d.contribution = "limiting" if d is worst else "supporting"

    # -- 5. CONSTRAIN: an official warning outranks ORCA's own thresholds.
    if warning_status and warning_status.get("active"):
        # An official warning outranks ORCA's own threshold evaluation. ORCA does
        # not "disagree" with the issuing authority (12 section 4.2).
        verdict = Verdict.UNSAFE
        worst = next((d for d in drivers if d.factor == "official_warning_status"),
                     worst)
        for d in drivers:
            d.contribution = "limiting" if d is worst else "supporting"

    # -- 6. CONFIDENCE
    confidence = _confidence(tset, drivers, not_evaluated, quality_penalties)

    # -- 7. CAP: a required factor we could not check ceilings the verdict.
    rationale = (f"{verdict.value} for {domain.value}; the limiting factor is "
                 f"{worst.factor} at {worst.value:g} {worst.unit or ''}".strip()
                 + f" ({worst.band}).")
    capped_by: list[str] = []
    for factor in capping:
        cap_verdict = _BAND_TO_VERDICT[tset.cap_for(factor)]
        capped_by.append(factor)
        if _VERDICT_SEVERITY.index(verdict) < _VERDICT_SEVERITY.index(cap_verdict):
            verdict = cap_verdict
            # The ceiling, not a favourable driver, is what governs the answer.
            # No DRIVER is limiting in this case: the governing factor is a
            # check that could not be made, not a value that was measured.
            # Leaving the pre-cap driver marked "limiting" made the answer
            # contradict itself -- the card said wave height governed while the
            # headline said the missing warning check did.
            limiting = factor
            for d in drivers:
                d.contribution = "supporting"
        else:
            limiting = worst.factor
        worst_factor = limiting

    if capped_by:
        # Confidence can never be high on a verdict we were not able to check.
        if _CONFIDENCE_LADDER.index(confidence) > _CONFIDENCE_LADDER.index(
                Confidence.MEDIUM):
            confidence = Confidence.MEDIUM
        reasons = "; ".join(
            (tset.capping_factors[f].get("reason") or "").strip().rstrip(".")
            for f in capped_by if tset.capping_factors.get(f))
        rationale = (f"{verdict.value} for {domain.value}, capped because "
                     f"{', '.join(capped_by)} could not be checked. {reasons}. "
                     f"This is a ceiling, not a measurement: ORCA does not state "
                     f"that conditions are favourable when it could not check "
                     f"for an official warning.")
    else:
        worst_factor = worst.factor

    return DomainResult(
        Assessment(
            assessment_id=_new_id("as"), domain=domain, verdict=verdict,
            confidence=confidence, spatial=spatial,
            temporal=TemporalRef(valid_time=window_start, valid_from=window_start,
                                 valid_to=window_end),
            drivers=drivers, not_evaluated=not_evaluated,
            missing_required=[],
            verdict_capped_by=capped_by,
            limiting_factor=worst_factor,
            official_warning_status=warning_status,
            threshold_set=set_id, threshold_set_status=tset.status,
            rationale=rationale),
        evidence)


def _confidence(tset: th.ThresholdSet, drivers: list[Driver],
                not_evaluated: list[NotEvaluated], quality_penalties: int) -> Confidence:
    """Deterministic ladder. Confidence is qualitative; no false precision."""
    level = 2                                             # start at high
    missing = {n.factor for n in not_evaluated}
    if any(f in missing for f in tset.preferred_factors):
        level = min(level, 1)                             # cap at medium
    level -= quality_penalties
    if any(d.band in ("unfavourable", "unsafe") for d in drivers) and len(drivers) < 2:
        level = min(level, 1)      # a single adverse driver is thin ground
    return _CONFIDENCE_LADDER[max(0, min(2, level))]
