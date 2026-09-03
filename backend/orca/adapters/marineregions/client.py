"""WFS client for the MarineRegions GeoServer (S-08).

Source: VLIZ Maritime Boundaries Geodatabase, served from
https://geo.vliz.be/geoserver/MarineRegions/wfs.

Access note, verified live 2026-09-02: GetCapabilities and GetFeature both
answered unauthenticated (HTTP 200). No credentials are configured.

Axis order, verified live 2026-09-02: the layers declare
`urn:ogc:def:crs:EPSG::4326`, whose authority axis order is LATITUDE, LONGITUDE.
A CQL `BBOX(the_geom, 60, -2, 100, 26)` is therefore read as lat 60..100,
lon -2..26 and returns the Arctic. Every bbox built here is emitted in
lat, lon, lat, lon order and the order is recorded in the request fingerprint.

This client is used by the capture script only. At query time ORCA reads the
local versioned snapshot, so a boundary answer never depends on the service
being reachable.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from ...schemas.errors import ErrorCode

log = logging.getLogger("orca.adapters.marineregions")

WFS_BASE = "https://geo.vliz.be/geoserver/MarineRegions/wfs"
SOURCE_NAME = "MarineRegions"
SOURCE_ID = "S-08"
ORGANISATION = "Flanders Marine Institute (VLIZ)"
ATTRIBUTION = ("Flanders Marine Institute (VLIZ), Maritime Boundaries Geodatabase; "
               "https://www.marineregions.org/ (CC-BY 4.0)")
ACCESS_METHOD = "MarineRegions WFS 2.0 GetFeature (GeoJSON) -> versioned local snapshot"

#: The version and release year the service publishes in each layer title, e.g.
#: "Exclusive Economic Zones (200 NM) (v12, world, 2023)".
_TITLE_VERSION = re.compile(r"\(v(?P<version>[0-9.]+),\s*[^,]*,\s*(?P<year>\d{4})\)")


class MarineRegionsError(Exception):
    def __init__(self, code: ErrorCode, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def parse_layer_version(title: str) -> tuple[str | None, str | None]:
    """Extract (dataset_version, release_year) from a published layer title.

    The service publishes no separate version field, so the title is the only
    machine-readable statement of which release the geometry belongs to. If the
    format changes this returns (None, None) and the capture refuses to write an
    unversioned snapshot rather than guessing.
    """
    m = _TITLE_VERSION.search(title or "")
    if not m:
        return None, None
    return f"v{m.group('version')}", m.group("year")


class MarineRegionsWfs:
    """Minimal WFS 2.0 client with canonical failure classification."""

    def __init__(self, base_url: str = WFS_BASE, timeout: float = 300.0,
                 max_retries: int = 2):
        self.base_url = base_url
        self.max_retries = max_retries
        self._client = httpx.Client(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": "ORCA/0.1 (SIH26176 prototype; "
                                   "marine data integration)"})
        self.bytes_read = 0
        self.requests = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MarineRegionsWfs":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _get(self, params: dict[str, str]) -> httpx.Response:
        last: MarineRegionsError | None = None
        for attempt in range(1, self.max_retries + 2):
            try:
                r = self._client.get(self.base_url, params=params)
            except httpx.TimeoutException as exc:
                last = MarineRegionsError(ErrorCode.TIMEOUT, str(exc))
            except httpx.TransportError as exc:
                last = MarineRegionsError(ErrorCode.SOURCE_UNAVAILABLE, str(exc))
            else:
                self.requests += 1
                if r.status_code == 200:
                    self.bytes_read += len(r.content)
                    return r
                if r.status_code in (401, 403):
                    raise MarineRegionsError(
                        ErrorCode.AUTH_REQUIRED,
                        f"MarineRegions WFS returned {r.status_code}")
                if r.status_code == 404:
                    raise MarineRegionsError(ErrorCode.DATASET_UNAVAILABLE,
                                             "layer not found")
                if r.status_code == 429:
                    last = MarineRegionsError(ErrorCode.RATE_LIMITED, "rate limited")
                elif r.status_code >= 500:
                    last = MarineRegionsError(ErrorCode.SOURCE_UNAVAILABLE,
                                              f"HTTP {r.status_code}")
                else:
                    raise MarineRegionsError(ErrorCode.ADAPTER_ERROR,
                                             f"HTTP {r.status_code}: {r.text[:200]}")
            if attempt <= self.max_retries:
                time.sleep(min(2 ** attempt * 0.5, 6.0))
                log.warning("marineregions retry %d/%d", attempt, self.max_retries)
        raise last or MarineRegionsError(ErrorCode.SOURCE_UNAVAILABLE, "unreachable")

    def capabilities(self) -> str:
        return self._get({"service": "WFS", "version": "2.0.0",
                          "request": "GetCapabilities"}).text

    def features(self, layer: str, *, min_lat: float, min_lon: float,
                 max_lat: float, max_lon: float) -> tuple[dict, str]:
        """GetFeature as GeoJSON for everything intersecting a bbox.

        Returns (feature_collection, request_url). The CQL bbox is emitted in
        latitude, longitude order -- see the module docstring.
        """
        cql = f"BBOX(the_geom,{min_lat},{min_lon},{max_lat},{max_lon})"
        params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                  "typeName": layer, "outputFormat": "application/json",
                  "CQL_FILTER": cql}
        r = self._get(params)
        try:
            return r.json(), str(r.url)
        except ValueError as exc:
            raise MarineRegionsError(
                ErrorCode.ADAPTER_ERROR,
                f"{layer}: response was not GeoJSON ({exc})") from exc
