"""INCOIS GeoServer layer bindings. Read from GetCapabilities on 2026-09-03.

Bindings are DATA, not logic.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WmsLayer:
    layer: str
    title: str
    #: Advertised geographic extent. A query outside it is INSUFFICIENT_COVERAGE,
    #: never "nothing found" -- the same rule as the boundary snapshot (D-15).
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float
    note: str | None = None


#: The current PFZ advisory lines. NOTE: the layer carries NO time dimension,
#: so the server serves only the issue that is current at request time. The
#: issue date is read from each feature's Year/Julian_day attributes, and a
#: stale issue is reported as stale rather than presented as today's advisory.
PFZ_LINES = WmsLayer(
    "PFZ_Automation:pfzlines", "PFZ advisory lines",
    11.640359838020025, 67.1538152839089, 23.05862713980332, 93.36869570038917,
    note="official INCOIS PFZ advisory; quoted, never recomputed")

#: Named advisory sectors, used to say WHICH sector an advisory belongs to.
PFZ_SECTORS = WmsLayer(
    "PFZ_Sectors:sector_new", "PFZ sectors",
    6.3468482531006885, 67.880108207999, 23.757668253099837, 94.77946820800025)

LAYERS = {"pfz_lines": PFZ_LINES, "pfz_sectors": PFZ_SECTORS}
