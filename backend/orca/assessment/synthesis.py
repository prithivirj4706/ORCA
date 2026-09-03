"""Cross-domain synthesis.

Domains are combined only in LANGUAGE, never in arithmetic
(12_RISK_AND_RECOMMENDATION_SPEC.md section 8).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..schemas.assessment import Assessment, Evidence
from ..schemas.enums import (
    Confidence, Disposition, Domain, RegulatoryStatus, Verdict,
)

#: Which outcome most constrains action, most constraining first.
_PRIORITY: list[tuple[Domain, object]] = [
    (Domain.REGULATORY, RegulatoryStatus.PROHIBITED),
    (Domain.SAFETY, Verdict.UNSAFE),
    (Domain.SAFETY, Verdict.UNFAVOURABLE),
    (Domain.SAFETY, Verdict.MARGINAL),
    (Domain.REGULATORY, RegulatoryStatus.RESTRICTED),
    (Domain.FISHING_SUITABILITY, Verdict.UNFAVOURABLE),
    (Domain.FISHING_SUITABILITY, Verdict.MARGINAL),
]

_CATEGORY = {
    RegulatoryStatus.PROHIBITED: "DO_NOT_PROCEED",
    Verdict.UNSAFE: "DO_NOT_PROCEED",
    Verdict.UNFAVOURABLE: "ADVISE_AGAINST",
    Verdict.MARGINAL: "PROCEED_WITH_CAUTION",
    # 12 section 11 does not name a category for REGULATORY RESTRICTED. Entering
    # another state's waters without authorisation is not a safety condition and
    # not a prohibition, but it is emphatically not "proceed with context".
    RegulatoryStatus.RESTRICTED: "PROCEED_WITH_CAUTION",
}

#: Regulatory outcomes that constrain action regardless of the weather, and
#: therefore outrank a safety refusal in the headline.
_CONSTRAINING_REGULATORY = (RegulatoryStatus.PROHIBITED, RegulatoryStatus.RESTRICTED)


@dataclass(slots=True)
class Synthesis:
    category: str
    headline: str
    limiting_domain: Domain | None
    limiting_factor: str | None
    confidence: Confidence
    disposition: Disposition


def _statement(a: Assessment, evidence: dict[str, Evidence]) -> str | None:
    """The evidence sentence behind an assessment's limiting driver.

    `Driver.evidence_id` exists for exactly this link, so the headline can name
    WHICH boundary or value drove the outcome without re-deriving it.
    """
    driver = next((d for d in a.drivers if d.contribution == "limiting"), None)
    if driver is None or driver.evidence_id is None:
        return None
    ev = evidence.get(driver.evidence_id)
    return ev.statement if ev else None


def synthesise(assessments: list[Assessment],
               evidence: list[Evidence] | None = None) -> Synthesis:
    by_domain = {a.domain: a for a in assessments}
    safety = by_domain.get(Domain.SAFETY)
    ev_by_id = {e.evidence_id: e for e in (evidence or [])}

    # A regulatory constraint holds whatever the weather does, so it is settled
    # before the safety branch. 12 section 8 puts REGULATORY(PROHIBITED) above
    # SAFETY(UNSAFE) in the priority order, and a safety refusal must not hide it.
    reg = by_domain.get(Domain.REGULATORY)
    if reg is not None and reg.verdict in _CONSTRAINING_REGULATORY:
        prohibited = reg.verdict is RegulatoryStatus.PROHIBITED
        detail = _statement(reg, ev_by_id)
        headline = ("Do not go. Operating at this location is not permitted "
                    if prohibited else
                    "This location is not freely open to you — operating here "
                    "requires authorisation from the state concerned ")
        headline += "(advisory information, not a legal determination)."
        if detail:
            headline += f" {detail[0].upper()}{detail[1:]}."
        disposition = (Disposition.REVIEW_REQUIRED if prohibited
                       else Disposition.AUTO_RELEASE)
        if safety is not None and safety.verdict is Verdict.INSUFFICIENT_EVIDENCE:
            headline += (" Sea conditions could not be assessed either, so this "
                         "is not a statement that conditions are otherwise fine.")
            # Naming the regulatory constraint does not answer the safety
            # question, so the safety block stands: the strictest disposition
            # governs (12 section 12).
            disposition = Disposition.BLOCKED
        return Synthesis(
            category=_CATEGORY[reg.verdict],
            headline=headline, limiting_domain=Domain.REGULATORY,
            limiting_factor=reg.limiting_factor, confidence=reg.confidence,
            disposition=disposition)

    # A safety question with no safety verdict is answered by refusing, not by
    # reporting the other domains as if they were the answer.
    if safety is not None and safety.verdict is Verdict.INSUFFICIENT_EVIDENCE:
        missing = ", ".join(safety.missing_required
                            or sorted({n.factor for n in safety.not_evaluated})[:3])
        return Synthesis(
            category="CANNOT_ADVISE",
            headline=("I cannot assess safety for this time and place, so I will not "
                      f"say whether it is safe to go. Missing: {missing}."),
            limiting_domain=Domain.SAFETY, limiting_factor=None,
            confidence=Confidence.LOW, disposition=Disposition.BLOCKED)

    if (safety is not None and safety.official_warning_status
            and safety.official_warning_status.get("active")):
        return Synthesis(
            category="DEFER_TO_OFFICIAL",
            headline=("An official marine warning is in force for this area. Follow it. "
                      "ORCA's role here is to convey and contextualise it."),
            limiting_domain=Domain.SAFETY,
            limiting_factor="official_warning_status",
            confidence=safety.confidence, disposition=Disposition.REVIEW_REQUIRED)

    for domain, verdict in _PRIORITY:
        a = by_domain.get(domain)
        if a is None or a.verdict is not verdict:
            continue
        others = [o for o in assessments
                  if o.domain is not domain
                  and o.verdict in (Verdict.FAVOURABLE, RegulatoryStatus.PERMITTED)]
        contrast = ""
        if others and domain is Domain.SAFETY:
            names = " and ".join(o.domain.value.replace("_", " ").lower()
                                 for o in others)
            contrast = (f" Conditions for {names} look favourable — the limiting "
                        f"factor is {a.limiting_factor}, not fish availability.")
        headline = {
            "DO_NOT_PROCEED": "Do not go. ",
            "ADVISE_AGAINST": "Going out is not advisable. ",
            "PROCEED_WITH_CAUTION": "Conditions are marginal. ",
        }.get(_CATEGORY.get(verdict, "PROCEED_WITH_CONTEXT"), "")
        headline += (f"{domain.value.replace('_', ' ').title()} is "
                     f"{str(getattr(verdict, 'value', verdict)).lower()}"
                     + (f" ({a.limiting_factor})." if a.limiting_factor else "."))
        return Synthesis(
            category=_CATEGORY.get(verdict, "PROCEED_WITH_CONTEXT"),
            headline=headline + contrast,
            limiting_domain=domain, limiting_factor=a.limiting_factor,
            confidence=a.confidence,
            disposition=(Disposition.REVIEW_REQUIRED
                         if verdict in (Verdict.UNSAFE, RegulatoryStatus.PROHIBITED)
                         else Disposition.AUTO_RELEASE))

    issued = [a for a in assessments if a.verdict is not Verdict.INSUFFICIENT_EVIDENCE]
    if not issued:
        return Synthesis("CANNOT_ADVISE",
                         "There is not enough evidence to assess any domain for this "
                         "time and place.", None, None, Confidence.LOW,
                         Disposition.BLOCKED)
    worst_conf = min((a.confidence for a in issued),
                     key=[Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH].index)
    return Synthesis("PROCEED_WITH_CONTEXT",
                     "No adverse conditions were identified in the domains that could "
                     "be assessed.", None, None, worst_conf, Disposition.AUTO_RELEASE)
