"""Temporal alignment, staleness and freshness.

This module carries the rule that stops a 10-day analysis being presented as a
next-morning forecast (11_GEOSPATIAL_REASONING_SPEC.md section 8.2).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from ..schemas.core import utcnow
from ..schemas.enums import Domain, Freshness, Representativeness as R

#: Nominal cadence of each product class, in days. Freshness is judged relative
#: to a product's own cadence, so a monthly field is not "stale" at three weeks.
CADENCE_DAYS: dict[R, float] = {
    R.INSTANTANEOUS: 0.25,
    R.HOURLY_MEAN: 1 / 24,
    R.DAILY_COMPOSITE: 1.0,
    R.THREE_DAY_MEAN: 3.0,
    R.WEEKLY_MEAN: 7.0,
    R.TEN_DAY_MEAN: 10.0,
    R.MONTHLY_MEAN: 30.0,
    R.BULLETIN_PERIOD: 0.5,
}

#: Which product classes each domain will accept as PRIMARY evidence.
#: A class absent here may still be carried as background context, but it can
#: never drive a verdict for that domain.
DOMAIN_ACCEPTS: dict[Domain, frozenset[R]] = {
    Domain.SAFETY: frozenset({R.INSTANTANEOUS, R.HOURLY_MEAN, R.BULLETIN_PERIOD}),
    # BULLETIN_PERIOD is accepted here because the INCOIS PFZ advisory is a
    # dated bulletin and is the most authoritative fishing input ORCA has.
    # Excluding it downgraded the official product to "context only" while a
    # derived chlorophyll ratio drove the verdict, which is backwards.
    Domain.FISHING_SUITABILITY: frozenset({
        R.INSTANTANEOUS, R.HOURLY_MEAN, R.DAILY_COMPOSITE, R.THREE_DAY_MEAN,
        R.BULLETIN_PERIOD,
    }),
    Domain.ECOLOGICAL: frozenset({
        R.DAILY_COMPOSITE, R.THREE_DAY_MEAN, R.WEEKLY_MEAN,
        R.TEN_DAY_MEAN, R.MONTHLY_MEAN,
    }),
    Domain.REGULATORY: frozenset(R),        # boundaries are not time-varying evidence
}


class Alignment(StrEnum):
    ALIGNED = "aligned"            # usable as primary evidence for the domain
    CONTEXT_ONLY = "context_only"  # retained, but may not drive a verdict
    OUT_OF_WINDOW = "out_of_window"  # validity does not reach the analysis window


@dataclass(frozen=True, slots=True)
class AlignmentDecision:
    alignment: Alignment
    reason: str
    offset_days: float
    representativeness: R

    @property
    def usable_as_primary(self) -> bool:
        return self.alignment is Alignment.ALIGNED


def interval_intersects(a_from: datetime, a_to: datetime,
                        b_from: datetime, b_to: datetime) -> bool:
    return a_from < b_to and b_from < a_to


def align(valid_time: datetime, representativeness: R, *,
          window_start: datetime, window_end: datetime,
          domain: Domain,
          usable_age_days: float | None = None) -> AlignmentDecision:
    """Decide whether a value may serve as primary evidence for a domain.

    Two independent tests, both of which must pass:
      1. does the product class carry the right meaning for this domain?
      2. does its validity actually reach the analysis window?
    """
    cadence = CADENCE_DAYS.get(representativeness, 1.0)
    # A product informs a window over its own cadence, optionally widened by a
    # per-parameter policy (config/staleness.yaml). The widening is explicit
    # configuration, never an implicit consequence of cadence.
    # Ageing is ASYMMETRIC: a value remains informative for some time AFTER its
    # valid time, but says nothing about the period before it was measured.
    back = timedelta(days=cadence / 2)
    forward = timedelta(days=max(cadence / 2, usable_age_days or 0.0))
    half_days = forward.total_seconds() / 86400.0
    reaches = interval_intersects(valid_time - back, valid_time + forward,
                                  window_start, window_end)

    if valid_time < window_start:
        offset = (window_start - valid_time).total_seconds() / 86400.0
    elif valid_time > window_end:
        offset = -(valid_time - window_end).total_seconds() / 86400.0
    else:
        offset = 0.0

    if representativeness not in DOMAIN_ACCEPTS[domain]:
        return AlignmentDecision(
            Alignment.CONTEXT_ONLY,
            f"{representativeness.value} is not accepted as primary evidence for "
            f"{domain.value}; retained as background context",
            offset, representativeness,
        )
    if not reaches:
        return AlignmentDecision(
            Alignment.OUT_OF_WINDOW,
            f"validity (+/-{half_days:g} d around {valid_time.date()}) does not reach "
            f"the analysis window {window_start.date()}..{window_end.date()}"
            f"; {abs(offset):,.0f} days away",
            offset, representativeness,
        )
    return AlignmentDecision(
        Alignment.ALIGNED,
        f"{representativeness.value} intersects the analysis window",
        offset, representativeness,
    )


def staleness_seconds(valid_time: datetime, now: datetime | None = None) -> float:
    return ((now or utcnow()) - valid_time).total_seconds()


def freshness(valid_time: datetime, representativeness: R,
              now: datetime | None = None) -> Freshness:
    """Freshness relative to the product's own cadence."""
    cadence = CADENCE_DAYS.get(representativeness, 1.0)
    age_d = staleness_seconds(valid_time, now) / 86400.0
    if age_d <= cadence * 1.5:
        return Freshness.FRESH
    if age_d <= cadence * 3:
        return Freshness.AGING
    if age_d <= cadence * 30:
        return Freshness.STALE
    return Freshness.EXPIRED
