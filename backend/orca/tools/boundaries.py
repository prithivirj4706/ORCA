"""Maritime boundary capability tool.

Implements the P0 contract get_maritime_boundaries
(04_ORCA_TOOL_CONTRACTS.md section 3.11).

Every response carries `advisory_only` and a disclaimer id. That is a hard rule,
not a presentation choice: boundary geometry is context, never a legal
determination and never navigational authority.
"""
from __future__ import annotations

from typing import Sequence

from ..adapters.marineregions.adapter import (
    ADVISORY_NOTE, DISCLAIMER_ID, MarineRegionsAdapter, MarineRegionsError,
)
from ..adapters.marineregions.client import SOURCE_ID
from ..schemas.core import BBox
from ..schemas.enums import EnvelopeStatus
from ..schemas.envelope import OrcaEnvelope
from ..schemas.errors import ErrorCode, OrcaError
from .base import ToolInputError, ToolRun, validate_bbox, validate_point

TOOL = "get_maritime_boundaries"


def get_maritime_boundaries(lat: float, lon: float, *,
                            boundary_types: Sequence[str] | None = None,
                            bbox: BBox | None = None,
                            adapter: MarineRegionsAdapter | None = None
                            ) -> OrcaEnvelope:
    """P0. Evaluate maritime boundary containment at a point.

    `boundary_types` defaults to every type ORCA has a policy for -- including
    the ones with no configured source. That is deliberate: a boundary answer
    that silently omitted marine protected areas and restricted zones would read
    as "you are clear", which it is not.
    """
    own = adapter is None
    adapter = adapter or MarineRegionsAdapter()
    run = ToolRun(TOOL, primary_source=SOURCE_ID)
    try:
        try:
            validate_point(lat, lon)
            if bbox is not None:
                validate_bbox(bbox)
        except ToolInputError as exc:
            return run.failure(exc.code, exc.detail)

        try:
            res = adapter.test_point(lat, lon, boundary_types)
        except MarineRegionsError as exc:
            run.attempt(SOURCE_ID, exc.code.value, exc.detail[:160])
            return run.failure(exc.code, exc.detail, source_id=SOURCE_ID)

        run.attempt(SOURCE_ID, "success",
                    f"{len(res.evaluated)} boundary type(s) from snapshot "
                    f"{res.snapshot_version}")
        run.resolved(SOURCE_ID)

        errors = [
            OrcaError(code=ErrorCode.DATASET_UNAVAILABLE, subject=name, tool=TOOL,
                      source_id=SOURCE_ID, severity="warning", detail=reason)
            for name, reason in res.unavailable
        ]
        errors += [
            OrcaError(code=code, subject="maritime_boundary", tool=TOOL,
                      source_id=SOURCE_ID, severity="warning",
                      detail=note)
            for code, note in zip(res.codes, res.notes)
        ]
        warnings = [{"code": "BOUNDARY_NOTE", "subject": "maritime_boundary",
                     "detail": n} for n in res.notes]
        warnings.append({"code": "ADVISORY_ONLY", "subject": "maritime_boundary",
                         "detail": ADVISORY_NOTE})

        status = (EnvelopeStatus.PARTIAL
                  if (res.unavailable or res.codes) else EnvelopeStatus.SUCCESS)
        return run.envelope(
            status,
            data=[*res.features, *res.derived],
            provenance=res.provenance,
            errors=errors, warnings=warnings,
            quality={"advisory_only": True,
                     "disclaimer_id": DISCLAIMER_ID,
                     "snapshot_version": res.snapshot_version,
                     "boundary_types_evaluated": res.evaluated,
                     "boundary_types_unavailable": [n for n, _ in res.unavailable],
                     "near_boundary_km": adapter.policy.near_boundary_km,
                     "geometry_simplified": False})
    finally:
        if own:
            adapter.close()
