"""Composition root for the live capability registry.

This is the ONE place that knows which adapter serves which capability. It is in
`tools/` because `tools/` is permitted to import `adapters/`; `agents/` and
`graph/` receive the bound registry and never learn what is behind it
(18_REPOSITORY_STRUCTURE.md section 1).

Capabilities with no source in this environment are registered as UNAVAILABLE
rather than omitted, so the Planner still plans for them and the answer states
what it could not check.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from .boundaries import get_maritime_boundaries
from .marine import get_currents, get_wave_conditions, get_weather
from .pfz import get_pfz
from .ocean import get_chlorophyll, get_ocean_observations, get_sst
from .registry import ToolRegistry

#: Capabilities whose source is not yet reachable, and why. Each becomes a
#: declared gap in every answer (03_DATA_SOURCE_MATRIX.md section 7).
UNAVAILABLE: dict[str, str] = {
    "get_marine_warnings": "IMD credentials not granted",
    "get_lightning": "IMD credentials not granted",
    "get_cyclone_track": "IMD credentials not granted",
    #: get_pfz is bound when an INCOIS WMS adapter is supplied; without one it
    #: is declared unavailable rather than omitted.
    "get_pfz": "INCOIS GeoServer adapter not supplied",
    #: Investigated 2026-09-03, no reachable source (F-31):
    #: UHSLC fast-delivery gauges are ~1 month behind, CMEMS publishes a tide
    #: product for the Arctic only, and the INCOIS TideGauges layer carries
    #: station LOCATIONS, not levels. ORCA will not compute a tide prediction
    #: of its own -- that would be an authoritative-looking invented number.
    #: NOTE: tidal CURRENTS are already covered, because the CMEMS total-current
    #: product includes the tidal component.
    "get_tides": ("no reachable tide-prediction source; UHSLC gauge data is "
                  "~1 month behind and CMEMS covers the Arctic only"),
}


def _when(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def build_live_registry(*, erddap, cmems, boundaries, gfs=None,
                        pfz=None) -> ToolRegistry:
    """Bind already-constructed adapters into a registry.

    Adapters are passed in rather than created here so their lifetime stays with
    the caller's `with` block -- a tool must not close a connection its caller
    is still using.
    """
    r = ToolRegistry()

    r.bind("get_wave_conditions",
           lambda lat, lon, valid_time, **_:
               get_wave_conditions(lat, lon, _when(valid_time), adapter=cmems))
    r.bind("get_currents",
           lambda lat, lon, valid_time, **_:
               get_currents(lat, lon, _when(valid_time), adapter=cmems))
    r.bind("get_weather",
           lambda lat, lon, valid_time, **_:
               get_weather(lat, lon, _when(valid_time), adapter=cmems, gfs=gfs))
    r.bind("get_ocean_observations",
           lambda lat, lon, valid_time, **_:
               get_ocean_observations(lat, lon, _when(valid_time), adapter=erddap))
    r.bind("get_sst",
           lambda lat, lon, valid_time, **_:
               get_sst(lat, lon, _when(valid_time), adapter=erddap, cmems=cmems))
    r.bind("get_chlorophyll",
           lambda lat, lon, valid_time, **_:
               get_chlorophyll(lat, lon, _when(valid_time), adapter=erddap,
                               cmems=cmems))
    if pfz is not None:
        r.bind("get_pfz",
               lambda lat, lon, valid_time=None, **_:
                   get_pfz(lat, lon, _when(valid_time) if valid_time else None,
                           adapter=pfz))
    r.bind("get_maritime_boundaries",
           lambda lat, lon, **_: get_maritime_boundaries(lat, lon,
                                                         adapter=boundaries))

    for name, reason in UNAVAILABLE.items():
        if not r.is_available(name):
            r.mark_unavailable(name, reason)
    return r

def bind_live_tools() -> ToolRegistry:
    from ..adapters.cmems.adapter import CmemsAdapter
    from ..adapters.incois_erddap.adapter import IncoisErddapAdapter
    from ..adapters.marineregions.adapter import MarineRegionsAdapter
    from ..adapters.incois_wms.adapter import IncoisPfzAdapter
    from ..adapters.noaa_gfs.adapter import NoaaGfsAdapter
    
    # Initialize without context managers for long-running API. 
    # Connection pooling will handle reuse.
    erddap = IncoisErddapAdapter()
    cmems = CmemsAdapter()
    boundaries = MarineRegionsAdapter()
    gfs = NoaaGfsAdapter()
    pfz = IncoisPfzAdapter()
    
    return build_live_registry(
        erddap=erddap, cmems=cmems, boundaries=boundaries, gfs=gfs, pfz=pfz
    )


def build_sea_mask(boundaries) -> "Callable[[float, float], bool]":
    """A navigability test for route planning, from the boundary snapshot.

    An EEZ polygon is a MARITIME zone: it runs from the baseline seaward and
    excludes land. So "inside some EEZ" is a serviceable test for "at sea",
    using geometry ORCA has already captured and versioned -- no new source.

    Limits, which the caller must state rather than hide:
      * outside the snapshot region, everything reads as not-navigable, so a
        route beyond it fails rather than inventing a path;
      * beyond 200 NM (the high seas) likewise reads as not-navigable;
      * it is a coastline test, not a bathymetry test. It says nothing about
        depth, traffic separation schemes or hazards to navigation.
    """
    snapshot = getattr(boundaries, "snapshot", None)
    if snapshot is None:
        return lambda lon, lat: False

    try:
        index = snapshot.layer("MarineRegions:eez").index
    except Exception:
        return lambda lon, lat: False

    cache: dict[tuple[int, int], bool] = {}

    def is_navigable(lon: float, lat: float) -> bool:
        # A* revisits cells constantly; the containment test dominates, so
        # memoise on the grid cell.
        k = (round(lon * 100), round(lat * 100))
        hit = cache.get(k)
        if hit is None:
            hit = bool(list(index.features_containing(lat, lon)))
            cache[k] = hit
        return hit

    return is_navigable
