"""Band edges travel with the driver (23_FRONTEND_REBUILD_BRIEF.md tier 2 #4).

A gauge can only place a value at its true position if it is told the axis it
was judged against. Before these fields existed the interface drew equal-width
bands with the pin at its band's CENTRE -- honest, but unable to distinguish a
value at the top of `favourable` from one at the bottom of it.

These tests pin the contract the renderer relies on: the edges are the same
numbers the band decision used, and they are absent where there is no axis.
"""
from datetime import datetime, timedelta, timezone

from backend.orca.assessment.engine import EvidencePool, assess_domain
from backend.orca.schemas.core import (
    Provenance, QualityMetadata, SpatialRef, TemporalRef,
)
from backend.orca.schemas.data import Forecast
from backend.orca.schemas.enums import (
    Domain, EnvelopeStatus, QualityFlag, Representativeness as R, ValueKind,
)
from backend.orca.schemas.envelope import OrcaEnvelope

UTC = timezone.utc
WIN_START = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
WIN_END = datetime(2026, 9, 3, 4, 0, tzinfo=UTC)


def env(tool, values):
    valid_time = WIN_START + timedelta(hours=2)
    data, prov = [], []
    for i, (param, (val, unit)) in enumerate(values.items()):
        pid = f"pv-{tool}-{i}"
        temporal = TemporalRef(valid_time=valid_time, representativeness=R.INSTANTANEOUS)
        spatial = SpatialRef.point(9.93, 76.26)
        quality = QualityMetadata(flag=QualityFlag.NOMINAL, basis="source-provided")
        data.append(Forecast(parameter=param, value=val, unit=unit, spatial=spatial,
                             temporal=temporal, quality=quality, provenance_id=pid))
        prov.append(Provenance(provenance_id=pid, parameter=param,
                               value_kind=ValueKind.FORECAST, unit=unit,
                               spatial=spatial, temporal=temporal, source="TEST",
                               source_id="S-TEST", dataset="test_ds", quality=quality))
    return OrcaEnvelope(status=EnvelopeStatus.SUCCESS, tool=tool, data=data,
                        provenance=prov)


def pool_of(**values):
    p = EvidencePool()
    p.ingest(env("t", dict(values)))
    return p


def safety(**values):
    return assess_domain(Domain.SAFETY, pool_of(**values),
                         window_start=WIN_START, window_end=WIN_END)


def driver(result, factor):
    return next((d for d in result.assessment.drivers if d.factor == factor), None)


class TestNumericDriversCarryTheirAxis:
    def test_edges_are_the_ones_the_verdict_used(self):
        d = driver(safety(significant_wave_height=(1.26, "m"),
                          wind_speed=(5.0, "m s-1")), "significant_wave_height")
        assert d is not None
        # The same numbers as config/thresholds/small_craft_v0.1.yaml.
        assert d.bands == {
            "favourable": [None, 1.5], "marginal": [1.5, 2.5],
            "unfavourable": [2.5, 3.5], "unsafe": [3.5, None],
        }

    def test_the_value_lies_inside_the_band_it_was_given(self):
        """The edges must agree with the band, or the pin lands outside it."""
        r = safety(significant_wave_height=(2.7, "m"), wind_speed=(5.0, "m s-1"))
        d = driver(r, "significant_wave_height")
        low, high = d.bands[d.band]
        assert (low is None or d.value >= low) and (high is None or d.value < high)

    def test_direction_of_harm_is_declared(self):
        d = driver(safety(significant_wave_height=(1.26, "m"),
                          wind_speed=(5.0, "m s-1")), "wind_speed")
        assert d.higher_is_worse is True


class TestFactorsWithNoAxis:
    def test_a_boolean_driver_carries_no_edges(self):
        """A presence/containment factor has no axis, so it must not invent one.

        The renderer falls back to equal-width bands when `bands` is None; a
        fabricated axis here would be drawn as though it were measured.
        """
        r = safety(significant_wave_height=(1.2, "m"), wind_speed=(5.0, "m s-1"))
        for d in r.assessment.drivers:
            if isinstance(d.value, bool):
                assert d.bands is None
