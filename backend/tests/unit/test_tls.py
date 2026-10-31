"""HTTPS verification uses the OS trust store; it can fall back to certifi but is never off."""
import ssl

import httpx

from app import tls
from app.config import get_settings


def test_os_trust_store_is_the_default_and_verifies():
    tls.ssl_verify.cache_clear()
    v = tls.ssl_verify()
    assert isinstance(v, ssl.SSLContext)
    assert v.verify_mode == ssl.CERT_REQUIRED and v.check_hostname
    assert tls.trust_store_label() == "operating system trust store"


def test_switch_falls_back_to_certifi_never_to_unverified(monkeypatch):
    monkeypatch.setattr(get_settings(), "use_os_trust_store", False)
    tls.ssl_verify.cache_clear()
    try:
        assert tls.ssl_verify() is True  # httpx default = certifi bundle, still verified
        assert tls.trust_store_label() == "certifi bundle"
    finally:
        tls.ssl_verify.cache_clear()


def test_certificate_failures_are_recognised_through_httpx_wrapping():
    msg = "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"
    try:
        try:
            raise ssl.SSLCertVerificationError(1, msg)
        except ssl.SSLError as inner:
            raise httpx.ConnectError(str(inner)) from inner
    except httpx.ConnectError as wrapped:
        assert tls.is_certificate_error(wrapped)
    assert not tls.is_certificate_error(httpx.ConnectTimeout("timed out"))
