"""Output validators for LLM-authored text (06_AGENT_SPEC.md section 7.7)."""
from .grounding import (
    OFFICIAL_TERMS, ValidationIssue, check_absence_claims, check_numeric_fidelity,
    check_official_language, validate_narrative,
)

__all__ = ["OFFICIAL_TERMS", "ValidationIssue", "check_absence_claims",
           "check_numeric_fidelity", "check_official_language",
           "validate_narrative"]
