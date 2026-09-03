"""REGULATORY domain: tool contract, assessment and synthesis.

These check the domain's promises rather than its plumbing: an unknown boundary
never becomes a permitted one, an unevaluated restriction is always named, and
the most constraining outcome governs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.orca.adapters.marineregions.adapter import DISCLAIMER_ID
from backend.orca.assessment.jurisdiction import (
    IMPLICATION_ORDER, load_jurisdiction_policy, most_constraining,
)
from backend.orca.assessment.regulatory import assess_regulatory
from backend.orca.assessment.synthesis import synthesise
from backend.orca.schemas.assessment import Assessment
from backend.orca.schemas.core import Provenance, SpatialRef, TemporalRef
from backend.orca.schemas.data import DerivedResult
from backend.orca.schemas.enums import (
    Confidence, Disposition, Domain, EnvelopeStatus, RegulatoryStatus, ValueKind,
    Verdict,
)
from backend.orca.schemas.envelope import OrcaEnvelope
from backend.orca.schemas.errors import ErrorCode, OrcaError
from backend.orca.geospatial.methods import derivation

UTC = timezone.utc
W0 = datetime(2026, 9, 3, 0, 30, tzinfo=UTC)
W1 = W0 + timedelta(hours=4)
POINT = SpatialRef.point(9.93, 76.26)


def _containment(boundary_type: str, inside: bool, *, iso_sov: str = "IND",
                 name: str = "Indian Exclusive Economic Zone",
                 distance_km: float = 50.0, near: bool = False,
                 pid: str | None = None, coverage_gap: bool = False,
                 disputed: bool = False):
    pid = pid or f"pv-{boundary_type}"
    features = ([{"name": name, "iso_sov": iso_sov, "iso_ter": iso_sov,
                  "sovereign": name.split()[0], "disputed": disputed,
                  "distance_to_edge_km": distance_km, "provenance_id": "pv-f"}]
                if inside else [])
    detail = {"boundary_type": boundary_type, "inside": inside,
              "layer": f"MarineRegions:{boundary_type}", "dataset_version": "v12",
              "effective_year": "2023", "snapshot_version": "2026-09-02",
              "features": features, "nearest": None, "distance_km": distance_km,
              "near_boundary": near, "near_boundary_km": 5.0, "search_km": 250.0,
              "advisory_only": True, "disclaimer_id": DISCLAIMER_ID}
    if coverage_gap:
        detail["jurisdiction_coverage"] = {"jurisdictions": ["LKA"],
                                           "present_in_layer": False,
                                           "layer": "MarineRegions:internal"}
    data = DerivedResult(parameter="point_in_boundary", value=inside,
                         spatial=POINT, provenance_id=pid, detail=detail)
    prov = Provenance(
        provenance_id=pid, parameter="point_in_boundary",
        value_kind=ValueKind.DERIVED, source="MarineRegions", source_id="S-08",
        temporal=TemporalRef(valid_time=datetime(2023, 1, 1, tzinfo=UTC)),
        derivation=derivation("point_in_polygon", ["pv-f"], {"crs": "EPSG:4326"},
                              module="topology"))
    return data, prov


def _envelope(*containments, unavailable=("restricted_zone",),
              status=EnvelopeStatus.PARTIAL):
    data = [c[0] for c in containments]
    prov = [c[1] for c in containments]
    errors = [OrcaError(code=ErrorCode.DATASET_UNAVAILABLE, subject=name,
                        tool="get_maritime_boundaries", severity="warning",
                        detail="no source configured")
              for name in unavailable]
    return OrcaEnvelope(status=status, tool="get_maritime_boundaries", data=data,
                        provenance=prov, errors=errors,
                        quality={"advisory_only": True})


class TestJurisdictionPolicy:
    def test_the_shipped_policy_loads_and_declares_its_status(self):
        p = load_jurisdiction_policy()
        assert p.home_iso == "IND"
        assert p.status == "LEGAL_REVIEW_REQUIRED"
        assert not p.validated
        assert set(p.implications) >= {"EEZ", "territorial_sea"}

    def test_sovereignty_not_territory_decides_home(self):
        """The Andaman EEZ publishes no iso_ter but is sovereign Indian."""
        p = load_jurisdiction_policy()
        assert p.placement(iso_sov="IND", iso_ter=None) == "home"
        assert p.placement(iso_sov="LKA", iso_ter="LKA") == "foreign"
        assert p.placement(iso_sov=None, iso_ter=None, sovereign="India") == "home"

    def test_the_worst_outcome_governs_and_is_never_averaged(self):
        assert most_constraining(["PERMITTED", "RESTRICTED"]) == "RESTRICTED"
        assert most_constraining(["RESTRICTED", "PROHIBITED"]) == "PROHIBITED"
        assert most_constraining([]) == "UNKNOWN"
        assert IMPLICATION_ORDER[0] == "PROHIBITED"


class TestRegulatoryVerdicts:
    def test_home_eez_is_permitted(self):
        env = _envelope(_containment("EEZ", True, iso_sov="IND"))
        a = assess_regulatory(env, window_start=W0, window_end=W1,
                              spatial=POINT).assessment
        assert a.verdict is RegulatoryStatus.PERMITTED
        assert a.limiting_factor == "EEZ"

    def test_foreign_eez_is_restricted(self):
        env = _envelope(_containment("EEZ", True, iso_sov="LKA",
                                     name="Sri Lankan Exclusive Economic Zone"))
        a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
        assert a.verdict is RegulatoryStatus.RESTRICTED
        assert a.confidence is Confidence.HIGH

    def test_the_most_constraining_type_governs(self):
        """Inside a foreign territorial sea outranks a merely restricted EEZ."""
        env = _envelope(
            _containment("EEZ", True, iso_sov="LKA", pid="pv-eez"),
            _containment("territorial_sea", True, iso_sov="LKA",
                         name="Sri Lankan 12 NM", pid="pv-ts"))
        a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
        assert a.verdict is RegulatoryStatus.PROHIBITED
        assert a.limiting_factor == "territorial_sea"
        limiting = [d for d in a.drivers if d.contribution == "limiting"]
        assert [d.factor for d in limiting] == ["territorial_sea"]

    def test_beyond_every_eez_is_unknown_not_permitted(self):
        """High seas fishery regulation has no configured source."""
        env = _envelope(_containment("EEZ", False))
        a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
        assert a.verdict is RegulatoryStatus.UNKNOWN
        assert a.confidence is Confidence.LOW

    def test_a_failed_retrieval_names_the_reason(self):
        env = OrcaEnvelope(
            status=EnvelopeStatus.PARTIAL, tool="get_maritime_boundaries",
            errors=[OrcaError(code=ErrorCode.INSUFFICIENT_COVERAGE,
                              tool="get_maritime_boundaries",
                              detail="outside the boundary snapshot region")])
        a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
        assert a.verdict is RegulatoryStatus.UNKNOWN
        assert "snapshot region" in a.rationale
        assert "maritime_boundary" in a.missing_required
        assert any(n.reason == "INSUFFICIENT_COVERAGE" for n in a.not_evaluated)

    def test_a_jurisdiction_gap_is_not_evaluated_not_unconstrained(self):
        """A layer with no feature for this state cannot say "you are outside it"."""
        env = _envelope(
            _containment("EEZ", True, iso_sov="LKA", pid="pv-eez"),
            _containment("internal_waters", False, pid="pv-iw", coverage_gap=True))
        a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
        assert "internal_waters" not in {d.factor for d in a.drivers}
        gap = next(n for n in a.not_evaluated if n.factor == "internal_waters")
        assert gap.reason == "INSUFFICIENT_COVERAGE"


class TestHonesty:
    def test_unevaluated_restrictions_are_always_listed(self):
        env = _envelope(_containment("EEZ", True),
                        unavailable=("restricted_zone", "marine_protected_area"))
        a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
        assert {"restricted_zone", "marine_protected_area"} <= {
            n.factor for n in a.not_evaluated}

    def test_permitted_is_capped_at_medium_while_restrictions_are_unchecked(self):
        """An unchecked naval zone can only make things worse, never better."""
        env = _envelope(_containment("EEZ", True))
        a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
        assert a.verdict is RegulatoryStatus.PERMITTED
        assert a.confidence is Confidence.MEDIUM

    def test_near_boundary_containment_is_not_asserted_with_full_confidence(self):
        env = _envelope(_containment("EEZ", True, iso_sov="LKA", distance_km=0.4,
                                     near=True))
        a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
        assert a.confidence is Confidence.MEDIUM
        assert "precision of the source geometry" in a.rationale

    def test_the_threshold_set_status_is_surfaced(self):
        env = _envelope(_containment("EEZ", True))
        a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
        assert a.threshold_set_status == "LEGAL_REVIEW_REQUIRED"

    def test_the_advisory_disclaimer_is_in_every_rationale(self):
        for inside, iso in ((True, "IND"), (True, "LKA"), (False, "IND")):
            env = _envelope(_containment("EEZ", inside, iso_sov=iso))
            a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
            assert "not a legal determination" in a.rationale

    def test_overlapping_claims_are_reported_not_adjudicated(self):
        data, prov = _containment("EEZ", True, iso_sov="LKA", disputed=True)
        a = assess_regulatory(_envelope((data, prov)), window_start=W0,
                              window_end=W1).assessment
        assert "does not adjudicate" in a.rationale


class TestSynthesis:
    def _safety(self, verdict, **kw):
        return Assessment(assessment_id="as-s", domain=Domain.SAFETY,
                          verdict=verdict, confidence=Confidence.LOW, **kw)

    def _reg(self, verdict):
        return Assessment(assessment_id="as-r", domain=Domain.REGULATORY,
                          verdict=verdict, confidence=Confidence.HIGH,
                          limiting_factor="EEZ")

    def test_prohibited_outranks_everything(self):
        s = synthesise([self._safety(Verdict.FAVOURABLE),
                        self._reg(RegulatoryStatus.PROHIBITED)])
        assert s.category == "DO_NOT_PROCEED"
        assert s.limiting_domain is Domain.REGULATORY
        assert s.disposition is Disposition.REVIEW_REQUIRED

    def test_a_safety_refusal_does_not_hide_a_regulatory_constraint(self):
        s = synthesise([self._safety(Verdict.INSUFFICIENT_EVIDENCE),
                        self._reg(RegulatoryStatus.RESTRICTED)])
        assert s.limiting_domain is Domain.REGULATORY
        assert "requires authorisation" in s.headline
        assert "could not be assessed" in s.headline
        # ... and the safety block still governs the disposition.
        assert s.disposition is Disposition.BLOCKED

    def test_permitted_does_not_hijack_the_headline(self):
        s = synthesise([self._safety(Verdict.INSUFFICIENT_EVIDENCE),
                        self._reg(RegulatoryStatus.PERMITTED)])
        assert s.category == "CANNOT_ADVISE"
        assert s.limiting_domain is Domain.SAFETY


class TestSchemaGuards:
    def test_regulatory_may_not_borrow_the_safety_vocabulary(self):
        with pytest.raises(ValueError, match="PERMITTED/RESTRICTED"):
            Assessment(assessment_id="as-x", domain=Domain.REGULATORY,
                       verdict=Verdict.FAVOURABLE, confidence=Confidence.HIGH)

    def test_other_domains_may_not_borrow_the_regulatory_vocabulary(self):
        with pytest.raises(ValueError, match="may not use a regulatory status"):
            Assessment(assessment_id="as-y", domain=Domain.SAFETY,
                       verdict=RegulatoryStatus.PERMITTED,
                       confidence=Confidence.HIGH)


class TestOutsideEverything:
    """"Outside every EEZ" is true of the high seas and of dry land alike."""

    def test_the_statement_does_not_claim_high_seas(self):
        env = _envelope(_containment("EEZ", False))
        res = assess_regulatory(env, window_start=W0, window_end=W1)
        statement = res.evidence[0].statement
        assert "outside every EEZ" in statement
        assert "no land mask" in statement
        # The one mention of the high seas is the caveat that it cannot be
        # distinguished from land -- never a claim that the point is there.
        assert statement.count("high seas") == 1
        assert "cannot say whether that means the high seas" in statement

    def test_it_is_still_unknown_rather_than_permitted(self):
        env = _envelope(_containment("EEZ", False))
        a = assess_regulatory(env, window_start=W0, window_end=W1).assessment
        assert a.verdict is RegulatoryStatus.UNKNOWN
