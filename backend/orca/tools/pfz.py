"""`get_pfz` -- the official INCOIS Potential Fishing Zone advisory (04 §3.5).

ORCA reports the advisory; it never computes one. `12` §5.3 reserves the term
"PFZ" for the authoritative INCOIS product, so ORCA's own SST/chlorophyll
reasoning is reported separately and never labelled a PFZ.

Three outcomes, deliberately distinct:

  * an advisory was found        -> its distance, sector and issue date
  * checked, none within range   -> EMPTY. A real answer, not a failure.
  * outside the issue's extent   -> INSUFFICIENT_COVERAGE. We did not look.

The middle and last cases must never collapse into each other: "no advisory
near you" and "we could not check" mean opposite things to someone deciding
whether to sail (D-3).
"""
from __future__ import annotations

from datetime import datetime

from ..adapters.incois_wms.adapter import IncoisPfzAdapter
from ..adapters.incois_wms.client import SOURCE_ID, WmsError
from ..schemas.enums import EnvelopeStatus
from ..schemas.envelope import OrcaEnvelope
from ..schemas.errors import ErrorCode, OrcaError
from .base import ToolInputError, ToolRun, validate_point

DEFAULT_RADIUS_KM = 100.0


def get_pfz(lat: float, lon: float, valid_time: datetime | None = None, *,
            radius_km: float = DEFAULT_RADIUS_KM,
            adapter: IncoisPfzAdapter | None = None) -> OrcaEnvelope:
    run = ToolRun("get_pfz", primary_source=SOURCE_ID)
    try:
        validate_point(lat, lon)
    except ToolInputError as exc:
        return run.failure(exc.code, exc.detail)

    own = adapter is None
    adapter = adapter or IncoisPfzAdapter()
    try:
        result = adapter.fetch_nearest_pfz(lat, lon, radius_km=radius_km,
                                           valid_time=valid_time)
    except WmsError as exc:
        run.attempt(SOURCE_ID, exc.code.value, exc.detail[:160])
        return run.failure(exc.code, exc.detail, subject="pfz_advisory",
                           source_id=SOURCE_ID)
    except Exception as exc:
        run.attempt(SOURCE_ID, "error", str(exc)[:160])
        return run.failure(ErrorCode.ADAPTER_ERROR, str(exc)[:300],
                           subject="pfz_advisory", source_id=SOURCE_ID)
    finally:
        if own:
            adapter.close()

    run.attempt(SOURCE_ID, "success" if result.observations else "empty",
                result.dataset_id)
    run.resolved(SOURCE_ID)

    warnings = [{"code": "SOURCE_NOTE", "subject": "pfz_advisory", "detail": n}
                for n in result.notes]

    if not result.observations:
        # Checked, and nothing is in force nearby. That is a finding.
        env = run.envelope(
            EnvelopeStatus.EMPTY,
            errors=[OrcaError(code=ErrorCode.NO_DATA, subject="pfz_advisory",
                              tool="get_pfz", source_id=SOURCE_ID,
                              severity="info",
                              detail=(result.notes[0] if result.notes
                                      else "no advisory within range"))],
            warnings=warnings,
            quality={"advisory_checked": True, "advisory_present": False,
                     "radius_km": radius_km, "layer": result.dataset_id})
        return env

    errors = [OrcaError(code=c, subject="pfz_advisory", tool="get_pfz",
                        source_id=SOURCE_ID, severity="warning",
                        detail=f"pfz served from {result.dataset_id}")
              for c in result.codes]
    detail = result.observations[0].detail or {}
    return run.envelope(
        EnvelopeStatus.PARTIAL if result.codes else EnvelopeStatus.SUCCESS,
        data=list(result.observations), provenance=list(result.provenance),
        errors=errors, warnings=warnings,
        quality={"advisory_checked": True, "advisory_present": True,
                 "radius_km": radius_km, "layer": result.dataset_id,
                 "issued": detail.get("issued"),
                 "sector": detail.get("sector"),
                 "representation": "vector",
                 "advisory_only": True})
