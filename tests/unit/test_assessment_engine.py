"""Assessment engine behaviour (12_RISK_AND_RECOMMENDATION_SPEC.md)."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.orca.assessment.engine import EvidencePool, assess_domain
from backend.orca.schemas.core import (
    Provenance, QualityMetadata, SpatialRef, TemporalRef,
)
from backend.orca.schemas.data import Forecast, Observation
from backend.orca.schemas.enums import (
    Confidence, Domain, EnvelopeStatus, QualityFlag, Representativeness as R,
    ValueKind, Verdict,
)
from backend.orca.schemas.envelope import OrcaEnvelope

UTC = timezone.utc
WIN_START = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
WIN_END = datetime(2026, 9, 3, 4, 0, tzinfo=UTC)


def make_env(tool, values, *, rep=R.INSTANTANEOUS, valid_time=None,
             kind=ValueKind.FORECAST, flag=QualityFlag.NOMINAL):
    """values: {parameter: (value, unit)}"""
    valid_time = valid_time or (WIN_START + timedelta(hours=2))
    data, prov = [], []
    for i, (param, (val, unit)) in enumerate(values.items()):
        pid = f"pv-{tool}-{i}"
        temporal = TemporalRef(valid_time=valid_time, representativeness=rep)
        spatial = SpatialRef.point(9.93, 76.26)
        quality = QualityMetadata(flag=flag, basis="source-provided")
        cls = Forecast if kind is ValueKind.FORECAST else Observation
        data.append(cls(parameter=param, value=val, unit=unit, spatial=spatial,
                        temporal=temporal, quality=quality, provenance_id=pid))
        prov.append(Provenance(provenance_id=pid, parameter=param, value_kind=kind,
                               unit=unit, spatial=spatial, temporal=temporal,
                               source="TEST", source_id="S-TEST", dataset="test_ds",
                               quality=quality))
    return OrcaEnvelope(status=EnvelopeStatus.SUCCESS, tool=tool, data=data,
                        provenance=prov)


def no_warning_env():
    """A genuine NO_ACTIVE_WARNING result -- a finding, not a failure."""
    from backend.orca.schemas.errors import ErrorCode
    env = OrcaEnvelope.empty("get_marine_warnings", ErrorCode.NO_ACTIVE_WARNING,
                             "no warning intersecting the query", "marine_warning")
    env.provenance.append(Provenance(
        provenance_id="pv-warn-0", parameter="marine_warning_status",
        value_kind=ValueKind.OBSERVED, source="TEST", source_id="S-TEST"))
    return env


def pool_with(*envs) -> EvidencePool:
    p = EvidencePool()
    for e in envs:
        p.ingest(e)
    return p


def safety(pool, **kw):
    return assess_domain(Domain.SAFETY, pool, window_start=WIN_START,
                         window_end=WIN_END, **kw).assessment


def fishing(pool, **kw):
    return assess_domain(Domain.FISHING_SUITABILITY, pool, window_start=WIN_START,
                         window_end=WIN_END, **kw).assessment


# --------------------------------------------------------------------------
# The central design rule: domains are independent and may disagree.
# --------------------------------------------------------------------------

class TestDomainIndependence:
    def test_good_fishing_and_unsafe_sea_are_reported_together(self):
        pool = pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (4.1, "m")}),
            make_env("get_weather", {"wind_speed": (9.0, "m s-1")}),
            no_warning_env(),
            make_env("get_sst", {"sst_anomaly": (0.3, "degC")},
                     rep=R.DAILY_COMPOSITE, kind=ValueKind.OBSERVED),
        )
        s, f = safety(pool), fishing(pool)
        assert s.verdict is Verdict.UNSAFE
        assert f.verdict is Verdict.FAVOURABLE
        # The whole point: they are not reconciled into one number.
        assert s.verdict != f.verdict
        assert s.limiting_factor == "significant_wave_height"

    def test_only_safety_may_return_unsafe(self):
        pool = pool_with(make_env("get_sst", {"sst_anomaly": (9.0, "degC")},
                                  rep=R.DAILY_COMPOSITE, kind=ValueKind.OBSERVED))
        assert fishing(pool).verdict is not Verdict.UNSAFE


# --------------------------------------------------------------------------
# Combination: the worst factor governs. Never averaged.
# --------------------------------------------------------------------------

class TestWorstFactorGoverns:
    def test_calm_wind_does_not_offset_dangerous_waves(self):
        pool = pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (3.9, "m")}),
            make_env("get_weather", {"wind_speed": (1.0, "m s-1")}),
            no_warning_env(),
        )
        a = safety(pool)
        assert a.verdict is Verdict.UNSAFE
        assert a.limiting_factor == "significant_wave_height"
        limiting = [d for d in a.drivers if d.contribution == "limiting"]
        assert len(limiting) == 1 and limiting[0].factor == "significant_wave_height"

    def test_all_favourable_gives_favourable(self):
        pool = pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (0.8, "m")}),
            make_env("get_weather", {"wind_speed": (4.0, "m s-1")}),
            no_warning_env(),
        )
        assert safety(pool).verdict is Verdict.FAVOURABLE


# --------------------------------------------------------------------------
# Evidence sufficiency: absence of evidence is never evidence of safety.
# --------------------------------------------------------------------------

class TestEvidenceSufficiency:
    def test_missing_required_input_blocks_a_safety_verdict(self):
        pool = pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (0.5, "m")}),
        )
        a = safety(pool)
        assert a.verdict is Verdict.INSUFFICIENT_EVIDENCE
        assert a.confidence is Confidence.LOW
        assert "wind_speed" in {n.factor for n in a.not_evaluated}
        assert "Absence of evidence is not evidence of safety" in a.rationale

    def test_empty_pool_yields_no_verdict_not_a_favourable_one(self):
        a = safety(EvidencePool())
        assert a.verdict is Verdict.INSUFFICIENT_EVIDENCE

    def test_gaps_from_failed_tools_are_carried_into_the_assessment(self):
        from backend.orca.schemas.errors import ErrorCode
        env = OrcaEnvelope.failure("get_lightning", ErrorCode.AUTH_REQUIRED,
                                   "IMD credentials not granted", subject="lightning")
        pool = pool_with(env)
        a = safety(pool)
        assert any(n.factor == "lightning" and n.reason == "AUTH_REQUIRED"
                   for n in a.not_evaluated)


# --------------------------------------------------------------------------
# Representativeness: a 10-day analysis cannot decide tomorrow morning.
# --------------------------------------------------------------------------

class TestRepresentativeness:
    def test_ten_day_analysis_is_refused_as_safety_evidence(self):
        pool = pool_with(make_env(
            "get_ocean_observations", {"wind_speed": (2.0, "m s-1")},
            rep=R.TEN_DAY_MEAN, valid_time=datetime(2026, 7, 30, tzinfo=UTC),
            kind=ValueKind.OBSERVED))
        a = safety(pool)
        assert a.verdict is Verdict.INSUFFICIENT_EVIDENCE
        gap = next(n for n in a.not_evaluated if n.factor == "wind_speed")
        assert gap.reason in ("REPRESENTATIVENESS_MISMATCH", "STALE_DATA")

    def test_archive_data_is_refused_as_fishing_evidence(self):
        pool = pool_with(make_env(
            "get_sst", {"sst_anomaly": (0.2, "degC")}, rep=R.DAILY_COMPOSITE,
            valid_time=datetime(2011, 10, 4, tzinfo=UTC), kind=ValueKind.OBSERVED))
        a = fishing(pool)
        assert a.verdict is Verdict.INSUFFICIENT_EVIDENCE
        assert any(n.reason == "STALE_DATA" for n in a.not_evaluated)


# --------------------------------------------------------------------------
# Provenance and threshold governance.
# --------------------------------------------------------------------------

class TestGovernance:
    def test_every_driver_is_bound_to_evidence_and_provenance(self):
        pool = pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (2.0, "m")}),
            make_env("get_weather", {"wind_speed": (9.0, "m s-1")}),
            no_warning_env(),
        )
        res = assess_domain(Domain.SAFETY, pool, window_start=WIN_START,
                            window_end=WIN_END)
        ev_ids = {e.evidence_id for e in res.evidence}
        assert res.assessment.drivers
        for d in res.assessment.drivers:
            assert d.evidence_id in ev_ids
        for e in res.evidence:
            assert e.provenance_id.startswith("pv-")

    def test_unvalidated_threshold_status_is_surfaced(self):
        pool = pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (2.0, "m")}),
            make_env("get_weather", {"wind_speed": (9.0, "m s-1")}),
            no_warning_env(),
        )
        a = safety(pool)
        assert a.threshold_set == "small_craft_v0.1"
        assert a.threshold_set_status == "SCIENTIFIC_VALIDATION_REQUIRED"


# --------------------------------------------------------------------------
# High-risk cases (15_EVALUATION_AND_TESTING_SPEC.md section 15.4).
# --------------------------------------------------------------------------

def active_warning_env(severity="WARNING"):
    from backend.orca.schemas.data import MarineWarning
    prov = Provenance(provenance_id="pv-warn-1", parameter="marine_warning_status",
                      value_kind=ValueKind.OBSERVED, source="IMD", source_id="S-05")
    w = MarineWarning(warning_id="B-1", warning_type="fishermen", severity=severity,
                      issuing_office="IMD", issued_at="2026-09-02T03:00:00Z",
                      text_verbatim="<bulletin text as issued>",
                      provenance_id="pv-warn-1")
    return OrcaEnvelope(status=EnvelopeStatus.SUCCESS, tool="get_marine_warnings",
                        data=[w], provenance=[prov])


class TestOfficialWarningGoverns:
    def test_active_warning_overrides_benign_model_values(self):
        """H-01: ORCA does not disagree with the issuing authority."""
        pool = pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (0.4, "m")}),
            make_env("get_weather", {"wind_speed": (2.0, "m s-1")}),
            active_warning_env(),
        )
        a = safety(pool)
        assert a.verdict is Verdict.UNSAFE
        assert a.limiting_factor == "official_warning_status"
        assert a.official_warning_status["active"] is True

    def _unchecked_warning_pool(self):
        """Benign model values, and a warning check that could not be made."""
        from backend.orca.schemas.errors import ErrorCode
        failed = OrcaEnvelope.failure("get_marine_warnings", ErrorCode.AUTH_REQUIRED,
                                      "IMD credentials not granted",
                                      subject="official_warning_status")
        return pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (0.4, "m")}),
            make_env("get_weather", {"wind_speed": (2.0, "m s-1")}),
            failed,
        )

    def test_unchecked_warnings_do_not_become_no_warning(self):
        """H-04: 'we could not check' must never read as 'nothing in force'.

        Under O-1 the verdict is now issued and CAPPED rather than refused, so
        the guarantee this test defends is no longer "refuses" -- it is that an
        unchecked authority never becomes evidence of its own absence. Nothing
        may claim the warning was checked.
        """
        a = safety(self._unchecked_warning_pool())
        assert "official_warning_status" in {n.factor for n in a.not_evaluated}
        assert "official_warning_status" not in {d.factor for d in a.drivers}
        assert a.official_warning_status is None

    def test_an_unchecked_warning_caps_the_verdict_at_marginal(self):
        """O-1: benign fields alone may never produce FAVOURABLE."""
        a = safety(self._unchecked_warning_pool())
        assert a.verdict is Verdict.MARGINAL
        assert a.verdict_capped_by == ["official_warning_status"]
        assert a.limiting_factor == "official_warning_status"
        assert "capped" in a.rationale.lower()

    def test_the_cap_is_a_ceiling_not_a_floor(self):
        """A worse-than-cap verdict is untouched by the cap."""
        from backend.orca.schemas.errors import ErrorCode
        failed = OrcaEnvelope.failure("get_marine_warnings", ErrorCode.AUTH_REQUIRED,
                                      "IMD credentials not granted",
                                      subject="official_warning_status")
        pool = pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (4.2, "m")}),
            make_env("get_weather", {"wind_speed": (2.0, "m s-1")}),
            failed,
        )
        a = safety(pool)
        assert a.verdict is Verdict.UNSAFE
        assert a.verdict_capped_by == ["official_warning_status"]

    def test_a_missing_measurement_still_refuses(self):
        """Capping applies to the authority check only, never to a measurement."""
        from backend.orca.schemas.errors import ErrorCode
        failed = OrcaEnvelope.failure("get_marine_warnings", ErrorCode.AUTH_REQUIRED,
                                      "IMD credentials not granted",
                                      subject="official_warning_status")
        pool = pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (0.4, "m")}),
            failed,
        )
        a = safety(pool)          # no wind at all
        assert a.verdict is Verdict.INSUFFICIENT_EVIDENCE
        assert "wind_speed" in a.missing_required

    def test_confidence_is_never_high_on_a_capped_verdict(self):
        a = safety(self._unchecked_warning_pool())
        assert a.confidence is not Confidence.HIGH


class TestPresenceBasedFactorsContribute:
    """Presence factors other than warnings were silently contributing nothing."""

    def _pool_with_pfz(self, active, issued="2026-09-02"):
        pool = pool_with(
            make_env("get_chlorophyll",
                     {"chlorophyll_ratio_to_local_median": (1.5, "ratio")},
                     rep=R.DAILY_COMPOSITE))
        pool.status["pfz_advisory"] = {"active": active, "checked": True,
                                       "issued": issued,
                                       "provenance_id": "pv-pfz-1"}
        return pool

    def test_an_advisory_in_force_is_a_favourable_driver(self):
        a = fishing(self._pool_with_pfz(True))
        driver = next(d for d in a.drivers if d.factor == "pfz_advisory")
        assert driver.band == "favourable"
        assert driver.value is True

    def test_the_absence_of_an_advisory_carries_no_verdict_weight(self):
        """INCOIS issues advisories where conditions warrant, not everywhere.
        'No advisory here today' is not evidence that fishing is poor."""
        a = fishing(self._pool_with_pfz(False))
        assert not any(d.factor == "pfz_advisory" for d in a.drivers)

    def test_a_checked_absence_is_still_recorded_as_evidence(self):
        res = assess_domain(Domain.FISHING_SUITABILITY, self._pool_with_pfz(False),
                            window_start=WIN_START, window_end=WIN_END)
        statements = [e.statement for e in res.evidence
                      if e.parameter == "pfz_advisory"]
        assert statements and "no INCOIS" in statements[0]

    def test_an_unchecked_advisory_is_not_evidence_of_absence(self):
        pool = pool_with(make_env("get_chlorophyll",
                                  {"chlorophyll_ratio_to_local_median": (1.5, "ratio")},
                                  rep=R.DAILY_COMPOSITE))
        a = fishing(pool)          # status never set
        assert not any(d.factor == "pfz_advisory" for d in a.drivers)
        assert "pfz_advisory" in {n.factor for n in a.not_evaluated}


class TestBulletinsArePrimaryEvidenceForFishing:
    def test_a_pfz_bulletin_is_not_downgraded_to_context(self):
        """The official advisory must not be outranked by a derived ratio."""
        from backend.orca.geospatial.temporal import DOMAIN_ACCEPTS
        assert R.BULLETIN_PERIOD in DOMAIN_ACCEPTS[Domain.FISHING_SUITABILITY]

    def test_pfz_distance_from_a_bulletin_reaches_the_drivers(self):
        pool = pool_with(make_env("get_pfz", {"pfz_distance_km": (0.5, "km")},
                                  rep=R.BULLETIN_PERIOD,
                                  kind=ValueKind.OBSERVED))
        a = fishing(pool)
        assert any(d.factor == "pfz_distance_km" for d in a.drivers)


class TestACappedVerdictDoesNotContradictItself:
    """The card said wave height governed while the headline said the missing
    warning check did. Only one of those can be true."""

    def _capped(self):
        from backend.orca.schemas.errors import ErrorCode
        failed = OrcaEnvelope.failure("get_marine_warnings", ErrorCode.AUTH_REQUIRED,
                                      "IMD credentials not granted",
                                      subject="official_warning_status")
        return pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (0.4, "m")}),
            make_env("get_weather", {"wind_speed": (2.0, "m s-1")}),
            failed)

    def test_no_driver_claims_to_be_limiting_when_a_cap_governs(self):
        a = safety(self._capped())
        assert a.verdict_capped_by == ["official_warning_status"]
        assert a.limiting_factor == "official_warning_status"
        assert not [d for d in a.drivers if d.contribution == "limiting"]

    def test_a_driver_still_leads_when_no_cap_applies(self):
        """The cap must not blank the limiting factor in the ordinary case."""
        pool = pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (4.2, "m")}),
            make_env("get_weather", {"wind_speed": (2.0, "m s-1")}),
            no_warning_env())
        a = safety(pool)
        assert a.verdict_capped_by == []
        assert [d.factor for d in a.drivers if d.contribution == "limiting"] \
            == ["significant_wave_height"]

    def test_a_cap_that_does_not_raise_the_verdict_leaves_the_driver_leading(self):
        """Seas already worse than the ceiling: the sea still governs."""
        a = safety(pool_with(
            make_env("get_wave_conditions", {"significant_wave_height": (4.2, "m")}),
            make_env("get_weather", {"wind_speed": (2.0, "m s-1")}),
            OrcaEnvelope.failure("get_marine_warnings",
                                 __import__("backend.orca.schemas.errors",
                                            fromlist=["ErrorCode"]).ErrorCode.AUTH_REQUIRED,
                                 "no credentials", subject="official_warning_status")))
        assert a.verdict is Verdict.UNSAFE
        assert [d.factor for d in a.drivers if d.contribution == "limiting"] \
            == ["significant_wave_height"]
