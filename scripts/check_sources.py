"""Live source health check.

Exercises every capability tool against a real position and reports what each
source actually did -- status, canonical codes, which source served it, and how
long it took. This is the ground truth behind `03_DATA_SOURCE_MATRIX.md`, and
the backing for the `/v1/health/sources` endpoint in `08_API_SPEC.md`.

Run it before believing any claim about what works.

    ./.venv/bin/python scripts/check_sources.py
    ./.venv/bin/python scripts/check_sources.py --lat 8.5 --lon 75.0 --offshore
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from backend.orca.adapters.cmems.adapter import CmemsAdapter
from backend.orca.adapters.incois_erddap.adapter import IncoisErddapAdapter
from backend.orca.adapters.incois_wms.adapter import IncoisPfzAdapter
from backend.orca.adapters.marineregions.adapter import MarineRegionsAdapter
from backend.orca.adapters.noaa_gfs.adapter import NoaaGfsAdapter
from backend.orca.tools.live import build_live_registry
from backend.orca.tools.registry import CATALOGUE

BAR = "=" * 96


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="check-sources")
    p.add_argument("--lat", type=float, default=9.93)
    p.add_argument("--lon", type=float, default=76.26)
    p.add_argument("--when", default=None, help="ISO-8601 UTC; default tomorrow 06:00")
    a = p.parse_args(argv)

    when = (datetime.fromisoformat(a.when).replace(tzinfo=timezone.utc) if a.when
            else (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                hour=6, minute=0, second=0, microsecond=0))

    print(BAR)
    print(f"ORCA source health   {a.lat:.3f} N, {a.lon:.3f} E   "
          f"valid {when:%Y-%m-%d %H:%M}Z")
    print(BAR)
    print(f"  {'capability':26} {'status':9} {'source':16} {'ms':>7}  "
          f"{'values':>6}  codes")
    print(f"  {'-'*26} {'-'*9} {'-'*16} {'-'*7}  {'-'*6}  {'-'*24}")

    ok = degraded = failed = unavailable = 0
    with IncoisErddapAdapter() as erddap, CmemsAdapter() as cmems, \
            MarineRegionsAdapter() as boundaries, NoaaGfsAdapter() as gfs, \
            IncoisPfzAdapter() as pfz:
        registry = build_live_registry(erddap=erddap, cmems=cmems,
                                       boundaries=boundaries, gfs=gfs, pfz=pfz)
        for spec in CATALOGUE:
            reason = registry.unavailable_reason(spec.name)
            if reason is not None or not registry.is_available(spec.name):
                unavailable += 1
                print(f"  {spec.name:26} {'--':9} {'':16} {'':>7}  {'':>6}  "
                      f"{reason or 'not bound'}")
                continue

            args = {"lat": a.lat, "lon": a.lon}
            if "valid_time" in spec.args_schema.get("properties", {}):
                args["valid_time"] = when
            t0 = time.perf_counter()
            try:
                env = registry.call(spec.name, **args)
            except Exception as exc:
                failed += 1
                ms = int((time.perf_counter() - t0) * 1000)
                print(f"  {spec.name:26} {'RAISED':9} {'':16} {ms:>7}  {'':>6}  "
                      f"{type(exc).__name__}: {str(exc)[:40]}")
                continue
            ms = int((time.perf_counter() - t0) * 1000)
            codes = ",".join(sorted({c.value for c in env.codes()})) or "-"
            src = (env.source_resolution.actual_source or "-")[:16]
            if env.source_resolution.fallback_used:
                src += "*"
            n = len([d for d in env.data if getattr(d, "value", None) is not None])
            status = env.status.value.upper()
            if status == "SUCCESS":
                ok += 1
            elif status in ("PARTIAL", "EMPTY"):
                degraded += 1
            else:
                failed += 1
            print(f"  {spec.name:26} {status:9} {src:16} {ms:>7}  {n:>6}  {codes}")

    print()
    print(f"  {ok} success · {degraded} degraded · {failed} failed · "
          f"{unavailable} no source configured        (* = served by a fallback)")
    print(BAR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
