"""Shared ERDDAP protocol: transport, selector encoding and error mapping.

ERDDAP is a SERVER PRODUCT, not a source. Several providers run one, and its
quirks belong to the software rather than to any of them: the selector syntax
the servlet container rejects (F-5), the errors returned with HTTP 200, the
"unknown datasetID" body that means a dataset was unloaded (F-3).

Encoding those once means a provider-specific adapter only has to describe its
own datasets, and a fix to the error mapping cannot apply to one ERDDAP and not
another. Per-provider concerns -- base URL, TLS, dataset bindings, metadata
validation -- stay with that provider's adapter.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..schemas.errors import ErrorCode

log = logging.getLogger("orca.adapters.erddap")


class ErddapError(Exception):
    """Adapter-level failure carrying a canonical code.

    Provider exceptions never cross the adapter boundary; they become this.
    """

    def __init__(self, code: ErrorCode, detail: str = "", status: int | None = None):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.status = status


@dataclass(slots=True)
class ErddapResponse:
    payload: Any
    url: str
    elapsed_ms: int
    bytes: int


#: ERDDAP selector syntax uses characters the servlet container rejects when
#: sent raw (observed: Tomcat returns an HTML 400 before ERDDAP ever parses the
#: query). We percent-encode exactly those, and nothing else -- a generic query
#: encoder would also encode `,` `:` `(` `)` `&` `=` and break the selector.
_MUST_ENCODE = {
    "[": "%5B", "]": "%5D", '"': "%22", " ": "%20",
    "<": "%3C", ">": "%3E", "|": "%7C", "{": "%7B", "}": "%7D",
    "\\": "%5C", "^": "%5E", "`": "%60",
}


def encode_query(query: str) -> str:
    for raw, enc in _MUST_ENCODE.items():
        query = query.replace(raw, enc)
    return query


_UNKNOWN_DATASET = re.compile(r"Currently unknown datasetID", re.I)
_NO_MATCHING = re.compile(r"Your query produced no matching results", re.I)


class ErddapClient:
    """Generic ERDDAP HTTP client.

    `verify` is whatever httpx accepts and is supplied by the provider adapter,
    because TLS trust is a property of the host, not of ERDDAP.
    """

    #: Overridden by provider subclasses for their log lines.
    source_label = "erddap"

    def __init__(self, base_url: str, timeout: float = 45.0, max_retries: int = 2,
                 verify: Any = True):
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._client = httpx.Client(
            timeout=timeout,
            verify=verify,
            headers={"User-Agent": "ORCA/0.1 (SIH26176 prototype; "
                                   "marine data integration)"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ErddapClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- request -----------------------------------------------------------

    def get_json(self, path: str, query: str = "") -> ErddapResponse:
        return self._get(path, query, parse=lambda r: r.json())

    def get_text(self, path: str, query: str = "") -> ErddapResponse:
        """For endpoints ERDDAP serves as text (.csv, .das)."""
        return self._get(path, query, parse=lambda r: r.text)

    def _get(self, path: str, query: str, parse) -> ErddapResponse:
        """GET an ERDDAP endpoint, mapping every failure to a canonical code.

        `query` is passed through the selector encoder: ERDDAP's syntax uses
        characters that must NOT be percent-encoded by a query-param encoder.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{encode_query(query)}"

        last: ErddapError | None = None
        for attempt in range(1, self.max_retries + 2):
            started = time.perf_counter()
            try:
                r = self._client.get(url)
            except httpx.TimeoutException as exc:
                last = ErddapError(ErrorCode.TIMEOUT, str(exc))
            except httpx.TransportError as exc:
                # DNS failure, connection refused, TLS failure.
                # NOTE: a DNS failure on OUR network is a local condition. We
                # report SOURCE_UNAVAILABLE, which never asserts the endpoint
                # itself is broken (03_DATA_SOURCE_MATRIX.md rule 3).
                last = ErddapError(ErrorCode.SOURCE_UNAVAILABLE, str(exc))
            else:
                elapsed = int((time.perf_counter() - started) * 1000)
                err = self._classify(r)
                if err is None:
                    return ErddapResponse(parse(r), url, elapsed, len(r.content))
                if err.code not in (ErrorCode.SOURCE_UNAVAILABLE, ErrorCode.TIMEOUT,
                                    ErrorCode.RATE_LIMITED):
                    raise err          # not retryable -- fail immediately
                last = err

            if attempt <= self.max_retries:
                time.sleep(min(2 ** attempt * 0.4, 5.0))
                log.warning("%s retry %d/%d url=%s code=%s", self.source_label,
                            attempt, self.max_retries, url, last.code)
        raise last or ErddapError(ErrorCode.ADAPTER_ERROR, "unreachable")

    @staticmethod
    def _classify(r: httpx.Response) -> ErddapError | None:
        body = r.text[:600]
        if r.status_code == 200:
            if body.lstrip().startswith("Error"):
                # ERDDAP occasionally returns errors with a 200 status.
                return ErddapClient._from_body(body, r.status_code)
            return None
        if r.status_code in (401, 403):
            return ErddapError(ErrorCode.AUTH_REQUIRED,
                               "ERDDAP returned an authentication challenge",
                               r.status_code)
        if r.status_code == 429:
            return ErddapError(ErrorCode.RATE_LIMITED, body, r.status_code)
        if r.status_code == 404:
            return ErddapClient._from_body(body, r.status_code)
        if r.status_code >= 500:
            return ErddapError(ErrorCode.SOURCE_UNAVAILABLE, body, r.status_code)
        return ErddapClient._from_body(body, r.status_code)

    @staticmethod
    def _from_body(body: str, status: int | None) -> ErddapError:
        if body.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
            return ErddapError(
                ErrorCode.ADAPTER_ERROR,
                f"servlet container rejected the request (HTTP {status}) before ERDDAP "
                f"parsed it -- this indicates a malformed/unencoded selector",
                status,
            )
        if _UNKNOWN_DATASET.search(body):
            # Observed live on 2026-09-02: NOAA_AVHRR_datasets vanished from the
            # catalogue mid-session. A dataset that disappears is DATASET_UNAVAILABLE
            # -- it is never silently substituted with another dataset.
            return ErddapError(ErrorCode.DATASET_UNAVAILABLE,
                               "dataset is not currently loaded by the ERDDAP server",
                               status)
        if _NO_MATCHING.search(body):
            return ErddapError(ErrorCode.NO_DATA,
                               "query was valid but matched no data", status)
        return ErddapError(ErrorCode.ADAPTER_ERROR, body.strip()[:300], status)
