"""HTTP client for the NCEP GFS ERDDAP host (S-11).

Verified 2026-09-03. The path to this source was not obvious and the negatives
are worth recording, because the design document named "NOAA" without an
endpoint:

  * `nomads.ncep.noaa.gov/dods` -- **OPeNDAP has been RETIRED** (NWS Service
    Change Notice 25-81). It answers HTTP 200 with an HTML notice, so a naive
    client would treat the retirement page as data.
  * `coastwatch.pfeg.noaa.gov` and `upwell.pfeg.noaa.gov` -- both time out; the
    long-standing CoastWatch ERDDAP hosts are gone.
  * `nomads.ncep.noaa.gov/cgi-bin/filter_gfs_*.pl` -- alive, but serves GRIB2,
    which needs an eccodes/cfgrib binary dependency to decode.

PacIOOS (University of Hawaii, an IOOS regional association) republishes the
NCEP GFS run over ERDDAP griddap, which ORCA can already read. The ORIGINATING
AUTHORITY is NOAA NCEP; PacIOOS is the distributor. Provenance records both, and
never presents the redistributor as the authority.

Terms: NCEP GFS output is US Government work in the public domain. PacIOOS asks
for attribution, which every provenance record carries.
"""
from __future__ import annotations

from typing import Any

from ..erddap import ErddapClient as _BaseErddapClient
from ..erddap import ErddapError, ErddapResponse, encode_query

__all__ = ["ACCESS_METHOD", "DEFAULT_BASE_URL", "DISTRIBUTOR", "ORGANISATION",
           "SOURCE_ID", "SOURCE_NAME", "GfsClient", "ErddapError",
           "ErddapResponse", "encode_query"]

DEFAULT_BASE_URL = "https://pae-paha.pacioos.hawaii.edu/erddap"
SOURCE_ID = "S-11"
SOURCE_NAME = "NOAA NCEP GFS"
ORGANISATION = "NOAA / NCEP (US)"
DISTRIBUTOR = "PacIOOS, University of Hawaii"
ACCESS_METHOD = "ERDDAP griddap"


class GfsClient(_BaseErddapClient):
    """The shared ERDDAP client pointed at the GFS host.

    Standard TLS: unlike INCOIS (F-1) this host serves a complete chain, so no
    trust-store workaround is needed.
    """

    source_label = "noaa_gfs"

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 45.0,
                 max_retries: int = 2, verify: Any = True):
        super().__init__(base_url, timeout=timeout, max_retries=max_retries,
                         verify=verify)
