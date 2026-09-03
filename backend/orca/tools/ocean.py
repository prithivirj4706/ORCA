"""Ocean-variable capability tools.

Implements the P0 contracts get_ocean_observations, get_sst and get_chlorophyll
(04_ORCA_TOOL_CONTRACTS.md sections 3.6, 3.7, 3.10).

Source order reflects verified reality (03_DATA_SOURCE_MATRIX.md sections 14-15):
INCOIS is preferred as the Indian authority, but its SST and chlorophyll
holdings are archives (ending 2011 and 2020). CMEMS carries current equivalents.
The tool therefore tries INCOIS first and falls back to CMEMS when INCOIS cannot
serve the requested time -- and records that it did.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from ..adapters.cmems.adapter import CmemsAdapter
from ..adapters.cmems.client import SOURCE_ID as CMEMS_SOURCE
from ..adapters.incois_erddap.adapter import IncoisErddapAdapter
from ..schemas.envelope import OrcaEnvelope
from .base import collect_from_sources, collect_point_parameters

INCOIS_SOURCE = "S-01..S-04"


def get_ocean_observations(lat: float, lon: float, valid_time: datetime, *,
                           variables: Sequence[str] = ("temperature", "salinity"),
                           depth_m: float | None = 5.0,
                           adapter: IncoisErddapAdapter | None = None) -> OrcaEnvelope:
    """P0. Oceanographic observations/analysis fields for multi-variable reasoning.

    INCOIS-only: the Argo analysis products have no CMEMS equivalent that ORCA
    treats as interchangeable.
    """
    own = adapter is None
    adapter = adapter or IncoisErddapAdapter()
    try:
        return collect_point_parameters(
            "get_ocean_observations", variables, lat, lon, valid_time,
            lambda sid, p: adapter.fetch_point(p, lat, lon, valid_time,
                                               depth_m=depth_m),
            INCOIS_SOURCE)
    finally:
        if own:
            adapter.close()


def _dual_source(tool: str, variables: Sequence[str], lat: float, lon: float,
                 valid_time: datetime, erddap: IncoisErddapAdapter | None,
                 cmems: CmemsAdapter | None) -> OrcaEnvelope:
    own_e, own_c = erddap is None, cmems is None
    erddap = erddap or IncoisErddapAdapter()
    cmems = cmems or CmemsAdapter()
    try:
        return collect_from_sources(
            tool, variables, lat, lon, valid_time,
            [(INCOIS_SOURCE,
              lambda sid, p: erddap.fetch_point(p, lat, lon, valid_time)),
             (CMEMS_SOURCE,
              lambda sid, p: cmems.fetch_point(p, lat, lon, valid_time))])
    finally:
        if own_e:
            erddap.close()
        if own_c:
            cmems.close()


def get_sst(lat: float, lon: float, valid_time: datetime, *,
            include_anomaly: bool = True,
            adapter: IncoisErddapAdapter | None = None,
            cmems: CmemsAdapter | None = None) -> OrcaEnvelope:
    """P0. Sea-surface temperature.

    Primary INCOIS ERDDAP (`NOAA_AVHRR_AMSR_datasets`, coverage ends 2011-10-04);
    fallback CMEMS OSTIA (`METOFFICE-GLO-SST-L4-NRT-OBS-SST-V2`), which is
    current and publishes an analysis error. OSTIA reports kelvin; the adapter
    converts explicitly.
    """
    variables = ["sst", "sst_anomaly"] if include_anomaly else ["sst"]
    return _dual_source("get_sst", variables, lat, lon, valid_time, adapter, cmems)


def _append_local_ratio(env: OrcaEnvelope, cmems: CmemsAdapter, lat: float,
                        lon: float, valid_time: datetime,
                        radius_km: float) -> None:
    """Append chlorophyll expressed against the median of its own local field.

    12_RISK_AND_RECOMMENDATION_SPEC.md section 5.3 forbids "chlorophyll is
    high": ORCA has no validated absolute standard, so the assessment factor is
    comparative. The local field must come from the SAME source as the point
    value, or the ratio would compare two different instruments.

    Failure here is silent by design -- the point value is still a good result,
    and the ratio is simply absent from the evidence rather than faked.
    """
    from ..adapters.cmems.adapter import CmemsError
    from ..geospatial.derive import derive_ratio_to_local_median

    obs = next((d for d in env.data if getattr(d, "parameter", None)
                == "chlorophyll_a"), None)
    if obs is None:
        return
    prov = next((p for p in env.provenance
                 if p.provenance_id == obs.provenance_id), None)
    if prov is None or prov.source_id != CMEMS_SOURCE:
        return
    try:
        values, _, _, n_cells = cmems.fetch_local_field(
            "chlorophyll_a", lat, lon, valid_time, radius_km)
        result, rprov = derive_ratio_to_local_median(
            obs, prov, values, radius_km, n_cells)
    except (CmemsError, ValueError):
        return
    env.data.append(result)
    env.provenance.append(rprov)


def get_chlorophyll(lat: float, lon: float, valid_time: datetime, *,
                    variables: Sequence[str] = ("chlorophyll_a",),
                    adapter: IncoisErddapAdapter | None = None,
                    cmems: CmemsAdapter | None = None,
                    local_median_radius_km: float | None = 100.0) -> OrcaEnvelope:
    """P0. Chlorophyll-a for productivity reasoning.

    Primary INCOIS ERDDAP (`incois_oceansat2_datasets`, coverage ends
    2020-05-01); fallback CMEMS multi-sensor gap-free daily L4, which is current
    and publishes a per-pixel uncertainty percentage.

    When the point value is served by CMEMS the tool also derives
    `chlorophyll_ratio_to_local_median`, which is the factor the assessment
    engine actually consumes. The derivation lives here rather than in the
    caller so that every consumer of this capability gets the same evidence.
    """
    own_e, own_c = adapter is None, cmems is None
    erddap = adapter or IncoisErddapAdapter()
    cmems = cmems or CmemsAdapter()
    try:
        env = collect_from_sources(
            "get_chlorophyll", variables, lat, lon, valid_time,
            [(INCOIS_SOURCE,
              lambda sid, p: erddap.fetch_point(p, lat, lon, valid_time)),
             (CMEMS_SOURCE,
              lambda sid, p: cmems.fetch_point(p, lat, lon, valid_time))])
        if local_median_radius_km:
            _append_local_ratio(env, cmems, lat, lon, valid_time,
                                local_median_radius_km)
        return env
    finally:
        if own_e:
            erddap.close()
        if own_c:
            cmems.close()
