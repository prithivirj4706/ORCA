"""HTTP client for the INCOIS GeoServer (S-06).

**Verification, 2026-09-03.** `03_DATA_SOURCE_MATRIX.md` §S-06 recorded this
source as PENDING VERIFICATION because the original test network could not
resolve `services.incois.gov.in`. That host still does not resolve here, but
`incois.gov.in/geoserver` answers `GetCapabilities` with 342 layers, and the
PFZ layers are among them. The verification is done; the endpoint differs from
the one the audit recorded.

What this endpoint will and will not do:

  * **WFS is 403 Forbidden.** No bulk feature download.
  * **WMS `GetFeatureInfo` is enabled and returns GeoJSON**, including real
    geometry. That is what makes PFZ a *vector* capability here rather than the
    `RASTER_ONLY` branch the design prepared for.
  * `GetMap` renders, so a map layer is available for the UI.

Because WFS is closed, a spatial search is expressed as a `GetFeatureInfo` with
a bbox and a pixel BUFFER -- the buffer is the search radius, in pixels, which
the adapter converts from kilometres.

Terms: INCOIS data policy. Attribution is carried on every provenance record.
PFZ is an OFFICIAL INCOIS advisory: ORCA quotes it and never recomputes it.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from ...schemas.errors import ErrorCode

log = logging.getLogger("orca.adapters.incois_wms")

DEFAULT_BASE_URL = "https://incois.gov.in/geoserver/wms"
SOURCE_ID = "S-06"
SOURCE_NAME = "INCOIS GeoServer"
ORGANISATION = "INCOIS (MoES)"
ACCESS_METHOD = "OGC WMS GetFeatureInfo"
ATTRIBUTION = "Indian National Centre for Ocean Information Services (INCOIS), MoES"


class WmsError(Exception):
    def __init__(self, code: ErrorCode, detail: str = "", status: int | None = None):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.status = status


@dataclass(slots=True)
class WmsResponse:
    payload: Any
    url: str
    elapsed_ms: int
    bytes: int


class IncoisWmsClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 45.0,
                 max_retries: int = 2, verify: Any = None):
        self.base_url = base_url
        self.max_retries = max_retries
        if verify is None:
            # Same host family as INCOIS ERDDAP, so reuse the trust handling
            # that copes with an incomplete chain (F-1).
            from ..incois_erddap.client import _build_ssl_context
            verify = _build_ssl_context()
        self._client = httpx.Client(
            timeout=timeout, verify=verify, follow_redirects=True,
            headers={"User-Agent": "ORCA/0.1 (SIH26176 prototype; "
                                   "marine data integration)"})

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "IncoisWmsClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def get_feature_info(self, layer: str, *, bbox: tuple[float, float, float, float],
                         size: int = 101, buffer_px: int = 20,
                         feature_count: int = 20) -> WmsResponse:
        """A point query with a pixel buffer, which is how a WMS does 'near'."""
        params = {
            "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetFeatureInfo",
            "LAYERS": layer, "QUERY_LAYERS": layer, "SRS": "EPSG:4326",
            "BBOX": ",".join(f"{v:.6f}" for v in bbox),
            "WIDTH": str(size), "HEIGHT": str(size),
            "X": str(size // 2), "Y": str(size // 2),
            "BUFFER": str(buffer_px),
            "INFO_FORMAT": "application/json",
            "FEATURE_COUNT": str(feature_count),
        }
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        return self._get(url)

    def _get(self, url: str) -> WmsResponse:
        last: WmsError | None = None
        for attempt in range(1, self.max_retries + 2):
            started = time.perf_counter()
            try:
                r = self._client.get(url)
            except httpx.TimeoutException as exc:
                last = WmsError(ErrorCode.TIMEOUT, str(exc))
            except httpx.TransportError as exc:
                last = WmsError(ErrorCode.SOURCE_UNAVAILABLE, str(exc))
            else:
                elapsed = int((time.perf_counter() - started) * 1000)
                err = self._classify(r)
                if err is None:
                    try:
                        payload = json.loads(r.text)
                    except ValueError:
                        raise WmsError(
                            ErrorCode.ADAPTER_ERROR,
                            f"expected GeoJSON, got {r.text[:120]!r}") from None
                    return WmsResponse(payload, url, elapsed, len(r.content))
                if err.code not in (ErrorCode.SOURCE_UNAVAILABLE, ErrorCode.TIMEOUT,
                                    ErrorCode.RATE_LIMITED):
                    raise err
                last = err
            if attempt <= self.max_retries:
                time.sleep(min(2 ** attempt * 0.4, 5.0))
        raise last or WmsError(ErrorCode.ADAPTER_ERROR, "unreachable")

    @staticmethod
    def _classify(r: httpx.Response) -> WmsError | None:
        body = r.text[:400]
        if r.status_code == 200:
            # A ServiceException arrives with HTTP 200 (F-26's lesson: a 200 is
            # not a result).
            if "ServiceException" in body or body.lstrip().startswith("<?xml"):
                return WmsError(ErrorCode.ADAPTER_ERROR,
                                f"WMS ServiceException: {body[:200]}", 200)
            return None
        if r.status_code in (401, 403):
            return WmsError(ErrorCode.AUTH_REQUIRED,
                            "GeoServer refused the request (WFS is 403 on this "
                            "host; GetFeatureInfo is the supported path)",
                            r.status_code)
        if r.status_code == 429:
            return WmsError(ErrorCode.RATE_LIMITED, body, r.status_code)
        if r.status_code >= 500:
            return WmsError(ErrorCode.SOURCE_UNAVAILABLE, body, r.status_code)
        return WmsError(ErrorCode.ADAPTER_ERROR, body, r.status_code)
