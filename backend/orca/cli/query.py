"""ORCA vertical-slice CLI.

Retrieval -> canonical schema -> evidence pool -> independent domain
assessments -> cross-domain synthesis. Every number shown is traceable to a
provenance record, and no verdict is issued without sufficient evidence.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..adapters.cmems.adapter import CmemsAdapter
from ..adapters.incois_erddap.adapter import IncoisErddapAdapter
from ..adapters.marineregions.adapter import MarineRegionsAdapter
from ..adapters.incois_wms.adapter import IncoisPfzAdapter
from ..adapters.noaa_gfs.adapter import NoaaGfsAdapter
from ..assessment.engine import EvidencePool, assess_domain
from ..assessment.regulatory import assess_regulatory
from ..geospatial.derive import derive_from_envelope
from ..assessment.synthesis import synthesise
from ..schemas.core import SpatialRef
from ..schemas.enums import Domain, Verdict
from ..tools.boundaries import get_maritime_boundaries
from ..tools.marine import get_currents, get_wave_conditions, get_weather
from ..tools.ocean import get_chlorophyll, get_ocean_observations, get_sst
from ..tools.pfz import get_pfz

IST = ZoneInfo("Asia/Kolkata")
BAR = "=" * 78

#: Capabilities the vertical slice does not yet have a source for, and why.
#: These are declared so the answer can say what it did not check, rather than
#: silently omitting them.
UNBUILT = {
    "official_warning_status": ("get_marine_warnings", "IMD credentials not granted"),
    "lightning": ("get_lightning", "IMD credentials not granted"),
    "cyclone_distance_km": ("get_cyclone_track", "IMD credentials not granted"),
}


def run(lat: float, lon: float, label: str | None, when: datetime) -> int:
    window_start, window_end = when, when + timedelta(hours=4)
    spatial = SpatialRef.point(lat, lon, label=label)

    print(BAR)
    print("ORCA — Ocean Reasoning & Collaborative Agents   (vertical slice)")
    print(BAR)
    print(f"location    {label + ' ' if label else ''}({lat:.3f} N, {lon:.3f} E)")
    print(f"window      {window_start.astimezone(IST):%d %b %Y %H:%M}"
          f"–{window_end.astimezone(IST):%H:%M} IST")
    print()

    pool = EvidencePool()
    derived_note: list[str] = []
    print("RETRIEVAL")
    with MarineRegionsAdapter() as boundaries:
        boundary_env = get_maritime_boundaries(lat, lon, adapter=boundaries)
    codes = ",".join(sorted({c.value for c in boundary_env.codes()})) or "-"
    print(f"  {'get_maritime_boundaries':24} {boundary_env.status.value:8} "
          f"{boundary_env.timing.duration_ms:>5} ms  "
          f"{boundary_env.source_resolution.actual_source or '-'}  [{codes}]")

    with IncoisErddapAdapter() as erddap, CmemsAdapter() as cmems, \
            NoaaGfsAdapter() as gfs, IncoisPfzAdapter() as pfz:
        calls = [
            ("get_wave_conditions", lambda: get_wave_conditions(lat, lon, when,
                                                                adapter=cmems)),
            ("get_currents", lambda: get_currents(lat, lon, when, adapter=cmems)),
            ("get_weather", lambda: get_weather(lat, lon, when, adapter=cmems,
                                                gfs=gfs)),
            ("get_ocean_observations", lambda: get_ocean_observations(lat, lon, when,
                                                                      adapter=erddap)),
            ("get_sst", lambda: get_sst(lat, lon, when, adapter=erddap, cmems=cmems)),
            ("get_chlorophyll", lambda: get_chlorophyll(lat, lon, when, adapter=erddap,
                                                        cmems=cmems)),
            ("get_pfz", lambda: get_pfz(lat, lon, when, adapter=pfz)),
        ]
        for name, call in calls:
            env = call()
            # Speed/direction are derived by the kernel, never by an adapter.
            d_data, d_prov = derive_from_envelope(env)
            if d_data:
                env.data.extend(d_data)
                env.provenance.extend(d_prov)
                derived_note.append(
                    f"{', '.join(x.parameter for x in d_data)} derived from "
                    f"{name} components")
            # The ratio arrives already derived from the tool (kernel-computed,
            # with a full derivation record); report it alongside the others.
            for obs in env.data:
                if getattr(obs, "parameter", None) != "chlorophyll_ratio_to_local_median":
                    continue
                det = obs.detail or {}
                derived_note.append(
                    f"chlorophyll_ratio_to_local_median = {obs.value} "
                    f"(median {det.get('local_median', float('nan')):g} mg m-3 over "
                    f"{det.get('valid_cells')} valid cells within "
                    f"{det.get('radius_km', 0):g} km)")
            pool.ingest(env)
            src = env.source_resolution.actual_source or "-"
            fb = " fallback" if env.source_resolution.fallback_used else ""
            codes = ",".join(sorted({c.value for c in env.codes()})) or "-"
            print(f"  {name:24} {env.status.value:8} {env.timing.duration_ms:>5} ms  "
                  f"{src}{fb}  [{codes}]")
    for factor, (tool, why) in UNBUILT.items():
        pool.add_gap(factor, "NOT_IMPLEMENTED", why, tool)
        print(f"  {tool:24} {'skipped':8} {'':>5}      —  [{why}]")

    if derived_note:
        print("\nDERIVED (deterministic kernel, inputs recorded)")
        for n in derived_note:
            print(f"  • {n}")

    print("\nMARITIME BOUNDARIES   (versioned geometry; advisory context only)")
    if boundary_env.quality.get("snapshot_version"):
        print(f"  snapshot {boundary_env.quality['snapshot_version']}"
              f"   near-boundary band {boundary_env.quality['near_boundary_km']:g} km")
    for d in boundary_env.data:
        if getattr(d, "parameter", None) != "point_in_boundary":
            continue
        det = d.detail
        inside = "INSIDE " if d.value else "outside"
        who = (", ".join(str(f["name"]) for f in det["features"]) if det["features"]
               else (f"nearest {det['nearest']['name']}" if det.get("nearest")
                     else "no feature within range"))
        dist = (f"{det['distance_km']:g} km" if det.get("distance_km") is not None
                else "-")
        print(f"  {inside} {det['boundary_type']:18} {who}")
        print(f"          {det['layer']} {det['dataset_version']} "
              f"({det['effective_year']})   distance {dist}"
              + ("   NEAR BOUNDARY" if det.get("near_boundary") else ""))
    for name in boundary_env.quality.get("boundary_types_unavailable", []):
        print(f"  not evaluated: {name:22} DATASET_UNAVAILABLE")

    print("\nEVIDENCE RETRIEVED")
    if not pool.candidates:
        print("  (none)")
    for c in pool.candidates:
        print(f"  • {c.parameter} = {c.value:g} {c.unit or ''}".rstrip())
        print(f"      {c.source} / {c.dataset}   valid {c.valid_time:%Y-%m-%d}"
              f"  ({c.representativeness.value})")
        print(f"      provenance {c.provenance_id}"
              + (f"   nearest node {c.node_distance_km:g} km away"
                 if c.node_distance_km else ""))

    print("\nASSESSMENTS   (independent by design; never merged into one score)")
    assessments, evidence = [], []
    results = [assess_domain(domain, pool, window_start=window_start,
                             window_end=window_end, spatial=spatial)
               for domain in (Domain.SAFETY, Domain.FISHING_SUITABILITY)]
    results.append(assess_regulatory(boundary_env, window_start=window_start,
                                     window_end=window_end, spatial=spatial))
    for res in results:
        a = res.assessment
        assessments.append(a)
        evidence.extend(res.evidence)
        print(f"\n  {a.domain.value:22} {a.verdict.value:22} confidence={a.confidence.value}")
        print(f"      thresholds  {a.threshold_set}  [{a.threshold_set_status}]")
        for d in a.drivers:
            mark = ">>" if d.contribution == "limiting" else "  "
            if isinstance(d.value, bool):
                # Containment for a boundary, presence for an advisory.
                pair = (("inside", "outside") if "boundary" in d.factor
                        or d.factor.isupper() else ("present", "absent"))
                val = pair[0] if d.value else pair[1]
            elif d.value is not None:
                val = f"{d.value:g} {d.unit or ''}".strip()
            else:
                val = "-"
            print(f"      {mark} {d.factor:28} {val:14} {d.band or ''}")
        for n in a.not_evaluated:
            print(f"         not evaluated: {n.factor:24} {n.reason}")
        if a.rationale:
            print(f"      {a.rationale}")

    s = synthesise(assessments, evidence)
    print(f"\nANSWER   [{s.category}]")
    print(f"  {s.headline}")
    print(f"  disposition: {s.disposition.value}   confidence: {s.confidence.value}")

    print("\nORCA output is not an official advisory. Follow IMD and INCOIS bulletins.")
    print(BAR)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="orca-query", description="ORCA vertical slice")
    p.add_argument("--lat", type=float, default=9.93)
    p.add_argument("--lon", type=float, default=76.26)
    p.add_argument("--label", default="near Kochi")
    p.add_argument("--when", default=None, help="ISO-8601 UTC; default tomorrow 06:00 IST")
    a = p.parse_args(argv)
    when = (datetime.fromisoformat(a.when).replace(tzinfo=timezone.utc) if a.when
            else (datetime.now(IST) + timedelta(days=1)).replace(
                hour=6, minute=0, second=0, microsecond=0).astimezone(timezone.utc))
    return run(a.lat, a.lon, a.label, when)


if __name__ == "__main__":
    sys.exit(main())
