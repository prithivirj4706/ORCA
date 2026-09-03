"""Wave and current capability tools, backed by CMEMS.

Implements the P0 contracts get_wave_conditions and get_currents
(04_ORCA_TOOL_CONTRACTS.md sections 3.8, 3.9).
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from ..adapters.cmems.adapter import CmemsAdapter
from ..adapters.cmems.client import SOURCE_ID
from ..schemas.envelope import OrcaEnvelope
from .base import collect_from_sources, collect_point_parameters

WAVE_VARIABLES = ("significant_wave_height", "peak_period", "swell_height",
                  "swell_period", "mean_wave_direction")
CURRENT_VARIABLES = ("current_u", "current_v")


def get_wave_conditions(lat: float, lon: float, valid_time: datetime, *,
                        variables: Sequence[str] = WAVE_VARIABLES,
                        adapter: CmemsAdapter | None = None) -> OrcaEnvelope:
    """P0. Wave and swell conditions for marine safety reasoning.

    Note: no other variable may substitute for wave height. If this tool cannot
    answer, the safety assessment records wave conditions as not evaluated and
    issues no safety verdict.
    """
    own = adapter is None
    adapter = adapter or CmemsAdapter()
    try:
        return collect_point_parameters(
            "get_wave_conditions", variables, lat, lon, valid_time,
            lambda sid, p: adapter.fetch_point(p, lat, lon, valid_time),
            SOURCE_ID)
    finally:
        if own:
            adapter.close()


def get_currents(lat: float, lon: float, valid_time: datetime, *,
                 variables: Sequence[str] = CURRENT_VARIABLES,
                 adapter: CmemsAdapter | None = None) -> OrcaEnvelope:
    """P0. Surface current velocity components.

    Speed and direction are DERIVED by the geospatial kernel, not by the
    adapter, so the derivation is traceable.
    """
    own = adapter is None
    adapter = adapter or CmemsAdapter()
    try:
        return collect_point_parameters(
            "get_currents", variables, lat, lon, valid_time,
            lambda sid, p: adapter.fetch_point(p, lat, lon, valid_time),
            SOURCE_ID)
    finally:
        if own:
            adapter.close()


def get_weather(lat: float, lon: float, valid_time: datetime, *,
                adapter: CmemsAdapter | None = None,
                gfs: "NoaaGfsAdapter | None" = None) -> OrcaEnvelope:
    """P0. Near-surface wind, as components.

    Neither source publishes a scalar wind speed, so this returns the eastward
    and northward components and the geospatial kernel derives speed and
    direction with a recorded method (D-8).

    Source order is TIME-AWARE, and the two sources answer different questions:

      * CMEMS L4 (S-07) is an OBSERVATION product with no forecast horizon. It
        is preferred when it covers the requested time, because an observation
        beats a model for a time that has already happened.
      * NOAA NCEP GFS (S-11) is a numerical FORECAST running +384 h. It is the
        only source that can answer "what will the wind be tomorrow", which is
        the ordinary question (F-25).

    `collect_from_sources` tries CMEMS first and moves to GFS when CMEMS cannot
    serve the requested time, recording the switch. So a past window is answered
    by observation, a future window by forecast, and the answer states which.

    04 section 3.1 names S-05 IMD as the primary and S-11 as its fallback; IMD
    has no credentials, so the chain begins at the first source ORCA can reach.
    """
    from ..adapters.noaa_gfs.adapter import NoaaGfsAdapter
    from ..adapters.noaa_gfs.client import SOURCE_ID as GFS_SOURCE

    own_c, own_g = adapter is None, gfs is None
    cmems = adapter or CmemsAdapter()
    gfs = gfs or NoaaGfsAdapter()
    try:
        return collect_from_sources(
            "get_weather", ("eastward_wind", "northward_wind"), lat, lon,
            valid_time,
            [(SOURCE_ID,
              lambda sid, p: cmems.fetch_point(p, lat, lon, valid_time)),
             (GFS_SOURCE,
              lambda sid, p: gfs.fetch_point(p, lat, lon, valid_time))])
    finally:
        if own_c:
            cmems.close()
        if own_g:
            gfs.close()
