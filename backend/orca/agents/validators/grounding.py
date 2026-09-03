"""Grounding validators (06_AGENT_SPEC.md section 7.7, 07 gate G3).

These are ENFORCED, not requested of the model. Generated text is parsed and
checked against the evidence set; a failure regenerates once and then falls back
to a deterministic template. The point is that ORCA's guarantees survive a model
that ignores its instructions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Reserved to quoted official bulletins. ORCA never issues an advisory, so
#: these words may not appear in ORCA's own synthesis (12 section 5.3).
OFFICIAL_TERMS = ("official warning", "advisory issued", "warning issued",
                  "officially advised", "we advise you", "issued an advisory")

#: Claiming safety is only permitted when safety was actually assessed. These
#: are the phrasings that assert it.
_SAFETY_CLAIMS = (
    r"\bit is safe\b", r"\bconditions are safe\b", r"\bsafe to (go|sail|venture|fish)\b",
    r"\bno danger\b", r"\bperfectly safe\b", r"\byou can safely\b",
)

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    detail: str


def _numbers_in(text: str) -> list[str]:
    return _NUMBER.findall(text or "")


def _canonical(values) -> set[str]:
    """Every rounding of an evidence number a narrative may legitimately use."""
    out: set[str] = set()
    for v in values:
        if v is None or isinstance(v, bool):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        out.add(f"{f:g}")
        out.add(f"{f:.0f}")
        out.add(f"{f:.1f}")
        out.add(f"{f:.2f}")
        if f.is_integer():
            out.add(str(int(f)))
    return out


def check_numeric_fidelity(text: str, evidence_values) -> list[ValidationIssue]:
    """Every number in the narrative must exist in the evidence.

    Numeric drift is the failure mode that would turn a 2.4 m sea into a 1.4 m
    one, so it fails validation rather than being rounded away.
    """
    allowed = _canonical(evidence_values)
    issues = []
    for token in _numbers_in(text):
        if token in allowed:
            continue
        try:
            if f"{float(token):g}" in allowed:
                continue
        except ValueError:
            pass
        issues.append(ValidationIssue(
            "NUMERIC_DRIFT", f"{token!r} does not appear in the evidence set"))
    return issues


def check_official_language(text: str) -> list[ValidationIssue]:
    low = (text or "").lower()
    return [ValidationIssue("OFFICIAL_LANGUAGE",
                            f"{term!r} is reserved for quoted official bulletins")
            for term in OFFICIAL_TERMS if term in low]


def check_absence_claims(text: str, safety_assessed: bool) -> list[ValidationIssue]:
    """"Conditions are safe" is forbidden when safety was not assessed.

    Absence of evidence is not evidence of safety (06 section 7.7 rule 4).
    """
    if safety_assessed:
        return []
    low = (text or "").lower()
    return [ValidationIssue("ABSENCE_AS_SAFETY",
                            f"text asserts safety ({pattern}) but safety was not "
                            f"assessed")
            for pattern in _SAFETY_CLAIMS if re.search(pattern, low)]


def validate_narrative(text: str, *, evidence_values, safety_assessed: bool
                       ) -> list[ValidationIssue]:
    return (check_numeric_fidelity(text, evidence_values)
            + check_official_language(text)
            + check_absence_claims(text, safety_assessed))
