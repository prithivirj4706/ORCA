"""HTTP client for INCOIS ERDDAP.

This module owns what is specific to the INCOIS host: its base URL, its identity,
and the TLS workaround for its incomplete certificate chain (F-1). The ERDDAP
protocol itself -- transport, selector encoding, error mapping -- is shared with
any other ERDDAP host in `adapters/erddap.py`.

Nothing above the adapter layer may import this.

Source: S-01..S-04 (03_DATA_SOURCE_MATRIX.md). Audit status: VERIFIED.
Terms of use: INCOIS ERDDAP terms -- see source portal. No authentication was
observed for the P0 datasets; that is not a guarantee and may change.
"""
from __future__ import annotations

import logging
import pathlib
from typing import Any

import certifi

from ..erddap import ErddapClient as _BaseErddapClient
from ..erddap import ErddapError, ErddapResponse, encode_query

__all__ = ["DEFAULT_BASE_URL", "ORGANISATION", "SOURCE_NAME", "ErddapClient",
           "ErddapError", "ErddapResponse", "encode_query"]

log = logging.getLogger("orca.adapters.incois_erddap")

#: INCOIS ERDDAP serves ONLY its leaf certificate -- the GlobalSign intermediate
#: ("GlobalSign RSA OV SSL CA 2018") is absent from the TLS chain. Verified
#: 2026-09-02 via `openssl s_client -showcerts` (chain length 1).
#:
#: macOS/Windows succeed anyway because the OS verifier fetches the issuer via
#: the certificate's AIA extension; `certifi` (roots only) does not, and a plain
#: Linux container would therefore FAIL to reach a source that works on a laptop.
#:
#: We handle this in two portable ways and NEVER by disabling verification:
#:   1. the OS trust store via `truststore` (AIA-capable on macOS/Windows), then
#:   2. a bundle of certifi roots + the missing intermediate (portable to Linux).
_TLS_DIR = pathlib.Path(__file__).resolve().parents[4] / "config" / "tls"
_INTERMEDIATE_PEM = _TLS_DIR / "globalsign_rsa_ov_ssl_ca_2018.pem"
_BUNDLE_PEM = _TLS_DIR / "incois_bundle.pem"


def _ensure_bundle() -> pathlib.Path | None:
    """Build `certifi roots + missing intermediate` on demand.

    The bundle is generated, never committed: vendoring a copy of certifi's root
    store would go stale silently. Only the 1.5 kB intermediate is tracked.
    """
    if not _INTERMEDIATE_PEM.is_file():
        return None
    roots = pathlib.Path(certifi.where())
    if (_BUNDLE_PEM.is_file()
            and _BUNDLE_PEM.stat().st_mtime >= max(roots.stat().st_mtime,
                                                   _INTERMEDIATE_PEM.stat().st_mtime)):
        return _BUNDLE_PEM
    try:
        _BUNDLE_PEM.write_text(
            roots.read_text() + "\n" + _INTERMEDIATE_PEM.read_text()
        )
    except OSError:
        return None
    return _BUNDLE_PEM


def _build_ssl_context() -> Any:
    """Return an httpx `verify` value. Verification is never disabled."""
    try:
        import ssl

        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # pragma: no cover - truststore unavailable
        log.info("truststore unavailable; falling back to bundled intermediate")
    bundle = _ensure_bundle()
    if bundle is not None:
        return str(bundle)
    log.warning(
        "no intermediate at %s; using certifi roots only -- INCOIS ERDDAP may fail "
        "TLS verification because it omits its intermediate certificate",
        _INTERMEDIATE_PEM,
    )
    return certifi.where()

DEFAULT_BASE_URL = "https://erddap.incois.gov.in/erddap"
SOURCE_NAME = "INCOIS ERDDAP"
ORGANISATION = "INCOIS (MoES)"


class ErddapClient(_BaseErddapClient):
    """The shared ERDDAP client, pointed at INCOIS and carrying its TLS fix."""

    source_label = "incois_erddap"

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 45.0,
                 max_retries: int = 2, verify: Any = None):
        super().__init__(base_url, timeout=timeout, max_retries=max_retries,
                         verify=_build_ssl_context() if verify is None else verify)
