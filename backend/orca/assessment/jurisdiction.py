"""Regulatory reading of a boundary containment result.

Geometry is a fact the source publishes. What it MEANS for a vessel is a legal
judgement, so it lives in configuration with a validation status that every
answer carries -- exactly as threshold sets do (`thresholds.py`).

This module reads only the `home_jurisdiction`, `implications` and
`near_boundary_km` sections of config/boundaries.yaml. The adapter reads the
source and snapshot sections. Neither imports the other.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from functools import lru_cache

import yaml

from ..schemas.enums import RegulatoryStatus

CONFIG = pathlib.Path(__file__).resolve().parents[3] / "config" / "boundaries.yaml"

#: Outcomes a containment result may imply, MOST CONSTRAINING FIRST. The worst
#: governs; outcomes are never averaged, exactly as bands are not
#: (12_RISK_AND_RECOMMENDATION_SPEC.md section 3).
IMPLICATION_ORDER = ("PROHIBITED", "RESTRICTED", "UNKNOWN", "PERMITTED",
                     "NOT_CONSTRAINING")

#: `NOT_CONSTRAINING` is not a verdict -- it means this boundary type imposes
#: nothing here, so it cannot govern on its own.
_TO_STATUS = {
    "PROHIBITED": RegulatoryStatus.PROHIBITED,
    "RESTRICTED": RegulatoryStatus.RESTRICTED,
    "PERMITTED": RegulatoryStatus.PERMITTED,
    "UNKNOWN": RegulatoryStatus.UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class Implication:
    boundary_type: str
    home: str
    foreign: str
    none: str
    basis_home: str | None = None
    basis_foreign: str | None = None
    basis_none: str | None = None

    def outcome(self, placement: str) -> str:
        return {"home": self.home, "foreign": self.foreign,
                "none": self.none}[placement]

    def basis(self, placement: str) -> str | None:
        return {"home": self.basis_home, "foreign": self.basis_foreign,
                "none": self.basis_none}[placement]


@dataclass(frozen=True, slots=True)
class JurisdictionPolicy:
    home_iso: str
    home_name: str
    status: str
    rationale: str
    near_boundary_km: float
    implications: dict[str, Implication]

    @property
    def validated(self) -> bool:
        return self.status.upper().startswith("VALIDATED")

    def placement(self, iso_sov: str | None, iso_ter: str | None = None,
                  sovereign: str | None = None) -> str:
        """`home` when a feature belongs to the configured home state.

        Sovereignty, not territory: the Andaman and Nicobar EEZ publishes no
        `iso_ter1` but is sovereign Indian territory, and a vessel there is in
        Indian waters.
        """
        for value in (iso_sov, iso_ter):
            if value and str(value).upper() == self.home_iso.upper():
                return "home"
        if sovereign and sovereign.strip().lower() == self.home_name.strip().lower():
            return "home"
        return "foreign"

    def status_for(self, outcome: str) -> RegulatoryStatus | None:
        return _TO_STATUS.get(outcome)


def most_constraining(outcomes: list[str]) -> str:
    """The worst outcome in a list. Never an average."""
    if not outcomes:
        return "UNKNOWN"
    return min(outcomes, key=IMPLICATION_ORDER.index)


@lru_cache(maxsize=2)
def load_jurisdiction_policy(path: str | None = None) -> JurisdictionPolicy:
    p = pathlib.Path(path or CONFIG)
    if not p.is_file():
        raise FileNotFoundError(f"boundary policy not found at {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    for key in ("home_jurisdiction", "implications"):
        if key not in raw:
            raise ValueError(f"{p.name}: missing required key {key!r}")
    home, imp = raw["home_jurisdiction"], raw["implications"]
    known = set(raw.get("boundary_types") or {})

    implications: dict[str, Implication] = {}
    for name, spec in (imp.get("types") or {}).items():
        if known and name not in known:
            raise ValueError(f"{p.name}: implication for unknown boundary type "
                             f"{name!r}")
        for key in ("home", "foreign", "none"):
            if spec.get(key) not in IMPLICATION_ORDER:
                raise ValueError(f"{p.name}: {name}.{key} must be one of "
                                 f"{IMPLICATION_ORDER}, got {spec.get(key)!r}")
        implications[name] = Implication(
            boundary_type=name, home=spec["home"], foreign=spec["foreign"],
            none=spec["none"],
            basis_home=(spec.get("basis_home") or "").strip() or None,
            basis_foreign=(spec.get("basis_foreign") or "").strip() or None,
            basis_none=(spec.get("basis_none") or "").strip() or None)

    return JurisdictionPolicy(
        home_iso=home["iso_ter"], home_name=home["name"],
        status=imp.get("status", "LEGAL_REVIEW_REQUIRED"),
        rationale=(imp.get("rationale") or "").strip(),
        near_boundary_km=float(raw.get("near_boundary_km", 5.0)),
        implications=implications)
