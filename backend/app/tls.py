"""Certificate verification for outbound HTTPS.

Verification is never switched off. By default certificates are checked against the
operating system's trust store (via ``truststore``) — the same store the officer's
browser uses — rather than only the certifi bundle that ships with Python.

Why: some official Indian government sites (e.g. tnsdma.tn.gov.in, which hosts the
district plans) serve a certificate chain that browsers accept but Python's bundled
certifi list cannot complete ("unable to get local issuer certificate"). Windows and
macOS complete such chains themselves (they fetch the missing intermediate certificate);
on Linux the system CA store is used, which also honours SSL_CERT_FILE / SSL_CERT_DIR.

Set ``USE_OS_TRUST_STORE=false`` to fall back to certifi only.
"""
from __future__ import annotations

import ssl
from functools import lru_cache

from app.config import get_settings


@lru_cache(maxsize=1)
def ssl_verify() -> ssl.SSLContext | bool:
    """The value for httpx's ``verify=``: an OS-trust-store context, or True (certifi)."""
    if not get_settings().use_os_trust_store:
        return True
    try:
        import truststore
    except ImportError:  # optional dependency missing -> certifi, still verified
        return True
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def trust_store_label() -> str:
    return "operating system trust store" if isinstance(ssl_verify(), ssl.SSLContext) else "certifi bundle"


def is_certificate_error(exc: BaseException) -> bool:
    """True when an HTTP failure was a certificate-verification failure."""
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e):
            return True
        e = e.__cause__ or e.__context__
    return False
