"""Phase 31CD - hermetic TLS trust-store handling tests for the bridge client.

No network is ever touched. Production trust selection is exercised through
the module's injectable seams (``_create_default_context``, ``_context_has_cas``,
``_certifi_cafile``;), real context construction is used wherever the security
invariants themselves are asserted, and ``websockets.connect`` is stubbed for
the bridge-client integration paths（no real WSS endpoint cities.

Real-crypto TLS fixtures (self-signed CA websocket server) could not be added
here: the repository currently ships neither ``cryptography`` nor an
``openssl`` CLI available to generate a test CA, so per Phase 31CD §21-§22
mock-level proof is used instead — it still proves the full selection + verify
invariants + fail-closed + classified-error surface.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl

import pytest
import websockets

from pd_ollama_bridge import bridge_client as bridge_client_mod
from pd_ollama_bridge import cli as cli_mod
from pd_ollama_bridge import tls
from pd_ollama_bridge.bridge_client import BridgeClient
from pd_ollama_bridge.cli import main
from pd_ollama_bridge.tls import (
    REASON_CONNECTION_ERROR,
    REASON_CONNECTION_REFUSED,
    REASON_CONNECTION_TIMEOUT,
    REASON_DNS_RESOLUTION_FAILED,
    REASON_TLS_CERTIFICATE_EXPIRED,
    REASON_TLS_CERTIFICATE_VERIFY_FAILED,
    REASON_TLS_HOSTNAME_MISMATCH,
    REASON_TLS_TRUST_STORE_UNAVAILABLE,
    BridgeConnectionError,
    BridgeTlsContext,
    ConnectionRefusedBridgeError,
    ConnectionTimeoutBridgeError,
    DnsResolutionFailedBridgeError,
    TLSCertificateExpiredError,
    TLSHostnameMismatchError,
    TLSTrustStoreUnavailableError,
    TLSVerifyFailedError,
    build_client_ssl_context,
    classify_connect_exception,
)
from pd_ollama_bridge.config import Config
from pd_ollama_bridge.ollama_client import OllamaClient

from conftest import (
    TEST_PAIRING_CODE,
    make_config,
    make_ollama,
    make_token_store,
)
from mock_ollama import MockOllama

_VERIFY = ssl.SSLCertVerificationError


class _Ctx:
    """A stand-in context whose ``cert_store_stats`` mirrors a real
    ``CERT_REQUIRED``+``check_hostname=True`` context."""

    def __init__(self, ca_count: int = 0) -> None:
        self.ca_count = ca_count
        self.check_hostname = True
        self.verify_mode = ssl.CERT_REQUIRED

    def cert_store_stats(self) -> dict:
        return {"x509": self.ca_count, "crl": 0, "x509_ca": self.ca_count}


class _BrokenWs:
    """A stand-in WebSocket whose handshake send always fails — sof the
    connect-call itself can be captured without a real server."""

    async def send(self, _payload: str) -> None:
        raise RuntimeError("no real handshake in hermetic test")

    async def close(self, *_args: object, **_kwargs: object) -> None:
        return None


def _stub_connect(monkeypatch, fake_connect) -> None:
    monkeypatch.setattr(websockets, "connect", fake_connect)


# --------------------------------------------------------------------------- #
# trust-source precedence (Phase 31CD §19/§30)
# --------------------------------------------------------------------------- #


def test_ws_uri_returns_no_tls_context(monkeypatch) -> None:
    result = build_client_ssl_context("ws://127.0.0.1:1")
    assert result.ssl_context is None
    assert result.trust_source == tls.TRUST_SOURCE_NONE


def test_default_trust_available_uses_default(monkeypatch) -> None:
    ctx = _Ctx(ca_count=5)
    monkeypatch.setattr(tls, "_create_default_context", lambda *_a, **_k: ctx)
    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: True)
    called: list = []

    def _no_certifi() -> str:
        called.append("certifi")
        return "C:/cacert.pem"

    monkeypatch.setattr(tls, "_certifi_cafile", _no_certifi)
    result = build_client_ssl_context("wss://server.example")
    assert result.trust_source == tls.TRUST_SOURCE_DEFAULT
    assert result.ssl_context is ctx
    assert called == [], "certifi must NOT be used when default trust is usable"


def test_default_unavailable_falls_back_to_certifi(monkeypatch) -> None:
    created: dict = {}
    ctx = _Ctx(ca_count=7)

    def _create(*_a: object, cafile: str = "", **_k: object) -> object:
        created["cafile"] = cafile
        return ctx

    monkeypatch.setattr(tls, "_create_default_context", _create)
    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: False)
    monkeypatch.setattr(tls, "_certifi_cafile", lambda: "C:/bundle/cacert.pem")
    result = build_client_ssl_context("wss://server.example")
    assert created["cafile"] == "C:/bundle/cacert.pem"
    assert result.trust_source == tls.TRUST_SOURCE_CERTIFI


def test_default_create_raises_still_falls_back_to_certifi(monkeypatch) -> None:
    def _create(*_a: object, cafile: str = "", **_k: object) -> object:
        if not cafile:
            raise OSError("no default trust")
        return _Ctx(ca_count=7)

    monkeypatch.setattr(tls, "_create_default_context", _create)
    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: True)
    monkeypatch.setattr(tls, "_certifi_cafile", lambda: "C:/bundle/cacert.pem")
    result= build_client_ssl_context("wss://server.example")
    assert result.trust_source == tls.TRUST_SOURCE_CERTIFI


def test_ssl_cert_file_honored_certifi_not_used(monkeypatch) -> None:
    ctx = _Ctx(ca_count=3)
    monkeypatch.setenv("SSL_CERT_FILE", "C:/env/cacert.pem")
    monkeypatch.setattr(tls, "_create_default_context", lambda *_a, **_k: ctx)
    called: list = []

    def _no_certifi() -> str:
        called.append("certifi")
        return "C:/cacert.pem"

    monkeypatch.setattr(tls, "_certifi_cafile", _no_certifi)
    result = build_client_ssl_context("wss://server.example")
    assert result.trust_source == tls.TRUST_SOURCE_ENVIRONMENT
    assert result.ssl_context is ctx
    assert called == [], "explicit env trust config must NOT be replaced by certifi"


def test_ssl_cert_dir_honored_certifi_not_used(monkeypatch) -> None:
    ctx = _Ctx(ca_count=3)
    monkeypatch.setenv("SSL_CERT_DIR", "C:/env/certs")
    monkeypatch.setattr(tls, "_create_default_context", lambda *_a, **_k: ctx)
    called: list = []

    def _no_certifi() -> str:
        called.append("certifi")
        return"C:/cacert.pem"

    monkeypatch.setattr(tls, "_certifi_cafile", _no_certifi)
    result = build_client_ssl_context("wss://server.example")
    assert result.trust_source == tls.TRUST_SOURCE_ENVIRONMENT
    assert result.ssl_context is ctx
    assert called == []


def test_env_valid_ca_file_honored_certifi_not_used(monkeypatch) -> None:
    # DEF-070: a usable env trust store IS honored as trustSource=environment,
    # and the certifi fallback is never consulted.
    ctx = _Ctx(ca_count=3)
    monkeypatch.setenv("SSL_CERT_FILE", "C:/env/cacert.pem")
    monkeypatch.setattr(tls, "_create_default_context", lambda *_a, **_k: ctx)
    called: list = []

    def _no_certifi() -> str:
        called.append("certifi")
        return "C:/cacert.pem"

    monkeypatch.setattr(tls, "_certifi_cafile", _no_certifi)
    result = build_client_ssl_context("wss://server.example")
    assert result.trust_source == tls.TRUST_SOURCE_ENVIRONMENT
    assert result.ssl_context is ctx
    assert called == [], "usable env trust must never be replaced by certifi"


def test_env_broken_path_empty_store_falls_back_to_certifi(monkeypatch) -> None:
    # DEF-070: a stale/broken SSL_CERT_FILE pointing at a path that yields a
    # context with ZERO CAs must NOT be reported as trustSource=environment;
    # the helper must fall through to the certifi fallback instead.
    created: dict = {}
    empty = _Ctx(ca_count=0)

    def _create(*_a: object, cafile: str = "", **_k: object) -> object:
        created["cafile"] = cafile
        return empty

    monkeypatch.setenv("SSL_CERT_FILE", "C:/does/not/exist.pem")
    monkeypatch.setattr(tls, "_create_default_context", _create)
    monkeypatch.setattr(tls, "_certifi_cafile", lambda: "C:/bundle/cacert.pem")
    result = build_client_ssl_context("wss://server.example")
    assert created["cafile"] == "C:/bundle/cacert.pem"
    assert result.trust_source == tls.TRUST_SOURCE_CERTIFI
    assert result.ssl_context is empty


def test_env_broken_path_empty_store_fails_closed_without_certifi(monkeypatch) -> None:
    # DEF-070 fail-closed leg: broken env path + unusable certifi -> raises
    # TLSTrustStoreUnavailableError with trust_source=none, never environment.
    monkeypatch.setenv("SSL_CERT_FILE", "C:/does/not/exist.pem")
    monkeypatch.setattr(tls, "_create_default_context", lambda *_a, **_k: _Ctx(ca_count=0))

    def _no_certifi() -> str:
        raise ImportError("certifi unavailable")

    monkeypatch.setattr(tls, "_certifi_cafile", _no_certifi)
    with pytest.raises(TLSTrustStoreUnavailableError) as exc:
        build_client_ssl_context("wss://server.example")
    assert exc.value.reason_code == REASON_TLS_TRUST_STORE_UNAVAILABLE
    assert exc.value.trust_source == tls.TRUST_SOURCE_NONE
    assert exc.value.transport == "wss"


# --------------------------------------------------------------------------- #
# security invariants + certifi path construct (Phase 31CD §20/§23)
# --------------------------------------------------------------------------- #


def test_wss_uri_builds_verified_real_context() -> None:
    result = build_client_ssl_context("wss://127.0.0.1:1")
    assert result.ssl_context is not None
    assert result.ssl_context.check_hostname is True
    assert result.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert result.ssl_context.verify_mode != ssl.CERT_NONE


def test_env_context_preserves_full_verification(monkeypatch) -> None:
    import certifi

    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    result = build_client_ssl_context("wss://server.example")
    assert result.trust_source == tls.TRUST_SOURCE_ENVIRONMENT
    assert result.ssl_context.check_hostname is True
    assert result.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert result.ssl_context.verify_mode != ssl.CERT_NONE


def test_default_context_preserves_full_verification() -> None:
    result = build_client_ssl_context("wss://server.example")
    assert result.ssl_context is not None
    assert result.ssl_context.check_hostname is True
    assert result.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert result.ssl_context.verify_mode != ssl.CERT_NONE


def test_certifi_fallback_context_preserves_full_verification(monkeypatch) -> None:
    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: False)
    result = build_client_ssl_context("wss://server.example")
    assert result.trust_source == tls.TRUST_SOURCE_CERTIFI
    assert result.ssl_context.check_hostname is True
    assert result.ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert result.ssl_context.cert_store_stats()["x509_ca"] > 0
    assert result.ssl_context.verify_mode != ssl.CERT_NONE


def test_certifi_fallback_uses_certifi_bundle_real(monkeypatch) -> None:
    import certifi

    real = ssl.create_default_context
    seen: dict = {}

    def _create(*_a: object, cafile: str = "", **_k: object) -> object:
        seen["cafile"] = cafile
        return real(cafile=cafile)

    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: False)
    monkeypatch.setattr(tls, "_create_default_context", _create)
    monkeypatch.setattr(tls, "_certifi_cafile", lambda: certifi.where())
    result = build_client_ssl_context("wss://server.example")
    assert seen["cafile"] == certifi.where()
    assert result.ssl_context.check_hostname is True
    assert result.ssl_context.verify_mode == ssl.CERT_REQUIRED


def test_regression_tls_verification_never_disabled(monkeypatch) -> None:
    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: False)
    monkeypatch.setattr(tls, "_create_default_context", lambda *_a, **_k: _Ctx(ca_count=1))
    monkeypatch.setattr(tls, "_certifi_cafile", lambda: "C:/cacert.pem")
    result= build_client_ssl_context("wss://server.example")
    ctx = result.ssl_context
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.verify_mode != ssl.CERT_NONE
    assert ctx.check_hostname is True


# --------------------------------------------------------------------------- #
# fail-closed trust store (Phase 31CD §31)
# --------------------------------------------------------------------------- #


def test_fail_closed_when_certifi_missing(monkeypatch) -> None:
    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: False)

    def _no_certifi() -> str:
        raise ImportError("certifi unavailable")

    monkeypatch.setattr(tls, "_certifi_cafile", _no_certifi)
    with pytest.raises(TLSTrustStoreUnavailableError) as exc:
        build_client_ssl_context("wss://server.example")
    assert exc.value.reason_code == REASON_TLS_TRUST_STORE_UNAVAILABLE
    assert exc.value.retryable is False
    assert exc.value.transport == "wss"


def test_fail_closed_when_certifi_empty(monkeypatch) -> None:
    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: False)
    monkeypatch.setattr(tls, "_certifi_cafile", lambda: "")
    with pytest.raises(TLSTrustStoreUnavailableError):
        build_client_ssl_context("wss://server.example")


def test_fail_closed_when_certifi_bundle_unloadable(monkeypatch) -> None:
    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: False)
    monkeypatch.setattr(tls, "_certifi_cafile", lambda: "C:/nonexistent/bundle.pem")

    def _boom(*_a: object, **_k: object) -> object:
        raise ssl.SSLError("cannot load cafile")

    monkeypatch.setattr(tls, "_create_default_context", _boom)
    with pytest.raises(TLSTrustStoreUnavailableError):
        build_client_ssl_context("wss://server.example")


def test_fail_closed_when_env_trust_config_unloadable(monkeypatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "C:/nonexistent/cacert.pem")

    def _boom(*_a: object, **_k: object) -> object:
        raise OSError("unreadable trust config")

    monkeypatch.setattr(tls, "_create_default_context", _boom)
    with pytest.raises(TLSTrustStoreUnavailableError):
        build_client_ssl_context("wss://server.example")


# --------------------------------------------------------------------------- #
# exception classification （Phase 31CD §13）
# --------------------------------------------------------------------------- #


def test_classify_verify_failed_generic() -> None:
    exc = _VERIFY("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate")
    classified= classify_connect_exception(exc, transport="wss", trust_source="default")
    assert isinstance(classified, TLSVerifyFailedError)
    assert classified.reason_code == REASON_TLS_CERTIFICATE_VERIFY_FAILED
    assert classified.retryable is False
    assert classified.transport == "wss"
    assert classified.trust_source == "default"


def test_classify_hostname_mismatch() -> None:
    exc = _VERIFY("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: Hostname mismatch, certificate is not valid for 'wrong.example'")
    classified= classify_connect_exception(exc, transport="wss", trust_source="certifi")
    assert isinstance(classified, TLSHostnameMismatchError)
    assert classified.reason_code == REASON_TLS_HOSTNAME_MISMATCH
    assert classified.retryable is False


def test_classify_expired() -> None:
    exc = _VERIFY("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired")
    classified= classify_connect_exception(exc, transport="wss", trust_source="default")
    assert isinstance(classified, TLSCertificateExpiredError)
    assert classified.reason_code == REASON_TLS_CERTIFICATE_EXPIRED
    assert classified.retryable is False


def test_classify_expired_word_in_cn_substring_is_generic() -> None:
    # DEF-072: the word "expired" inside a certificate CN must NOT over-classify
    # an unrelated verify failure as TLS_CERTIFICATE_EXPIRED.
    exc = _VERIFY(
        "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer "
        "certificate for CN=my-expired-proxy.example"
    )
    classified = classify_connect_exception(exc, transport="wss")
    assert isinstance(classified, TLSVerifyFailedError)
    assert not isinstance(classified, TLSCertificateExpiredError)
    assert classified.reason_code == REASON_TLS_CERTIFICATE_VERIFY_FAILED


def test_classify_not_yet_valid_phrase_maps_to_expired() -> None:
    # DEF-072: the stable OpenSSL clock-skew phrase maps to the expired family.
    exc = _VERIFY(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "certificate is not yet valid"
    )
    classified = classify_connect_exception(exc, transport="wss")
    assert isinstance(classified, TLSCertificateExpiredError)
    assert classified.reason_code == REASON_TLS_CERTIFICATE_EXPIRED


def test_classify_hostname_word_in_unrelated_message_is_generic() -> None:
    # DEF-072: merely containing the word "hostname" (e.g. a deferred-check
    # note) must NOT map to TLS_HOSTNAME_MISMATCH.
    exc = _VERIFY(
        "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer "
        "certificate (hostname check deferred)"
    )
    classified = classify_connect_exception(exc, transport="wss")
    assert isinstance(classified, TLSVerifyFailedError)
    assert not isinstance(classified, TLSHostnameMismatchError)
    assert classified.reason_code == REASON_TLS_CERTIFICATE_VERIFY_FAILED


def test_classify_ip_address_mismatch_phrase_maps_to_hostname() -> None:
    # DEF-072: the stable OpenSSL IP-address mismatch phrase maps to the
    # hostname-mismatch family.
    exc = _VERIFY(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: IP address "
        "mismatch, certificate is not valid for '127.0.0.1'"
    )
    classified = classify_connect_exception(exc, transport="wss")
    assert isinstance(classified, TLSHostnameMismatchError)
    assert classified.reason_code == REASON_TLS_HOSTNAME_MISMATCH


def test_classify_connection_refused() -> None:
    classified= classify_connect_exception(ConnectionRefusedError("refused"), transport="wss")
    assert isinstance(classified, ConnectionRefusedBridgeError)
    assert classified.reason_code == REASON_CONNECTION_REFUSED
    assert classified.retryable is True


def test_classify_timeout() -> None:
    classified= classify_connect_exception(TimeoutError("timed out"), transport="wss")
    assert isinstance(classified, ConnectionTimeoutBridgeError)
    assert classified.reason_code == REASON_CONNECTION_TIMEOUT
    assert classified.retryable is True


def test_classify_dns() -> None:
    classified= classify_connect_exception(socket.gaierror(-2,"Name or service not known",), transport="wss")
    assert isinstance(classified, DnsResolutionFailedBridgeError)
    assert classified.reason_code == REASON_DNS_RESOLUTION_FAILED
    assert classified.retryable is True


def test_classify_generic_network_error() -> None:
    classified= classify_connect_exception(OSError("peer reset"), transport="wss")
    assert isinstance(classified, BridgeConnectionError)
    assert classified.reason_code == REASON_CONNECTION_ERROR
    assert classified.retryable is True


def test_classify_generic_ssl_error_not_verify() -> None:
    exc = ssl.SSLError("wrong version number")
    classified= classify_connect_exception(exc, transport="wss")
    assert isinstance(classified, BridgeConnectionError)
    assert classified.reason_code == REASON_CONNECTION_ERROR


def test_classified_error_keeps_transport_and_trust_source() -> None:
    classified= classify_connect_exception(ConnectionRefusedError("x"), transport="wss", trust_source="environment")
    assert classified.transport == "wss"
    assert classified.trust_source == "environment"


# --------------------------------------------------------------------------- #
# bridge-client integration: context injection + reconnect semantics
# --------------------------------------------------------------------------- #


def test_connect_once_passes_verified_context_for_wss(monkeypatch) -> None:
    captured: dict = {}
    sentinel = object()

    def _fake_connect(*_a: object, **_k: object) -> object:
        captured["ssl"] = _k.get("ssl")
        return _BrokenWs()

    _stub_connect(monkeypatch, _fake_connect)
    monkeypatch.setattr(bridge_client_mod, "build_client_ssl_context", lambda _uri: BridgeTlsContext(sentinel,"default"))
    config= make_config(server_url="https://127.0.0.1:1", max_reconnect_attempts=0)
    bridge = BridgeClient(config=config, token_store=make_token_store(), ollama=make_ollama(MockOllama()))

    async def core() -> None:
        with pytest.raises(BridgeConnectionError):
            await bridge._connect_once()
        assert captured["ssl"] is sentinel

    asyncio.run(core())


def test_connect_once_passes_no_ssl_for_ws(monkeypatch) -> None:
    captured: dict = {}

    def _fake_connect(*_a: object, **_k: object) -> object:
        captured["ssl"] = _k.get("ssl")
        return _BrokenWs()

    _stub_connect(monkeypatch, _fake_connect)
    config= make_config(server_url="http://127.0.0.1:1", max_reconnect_attempts=0)
    bridge = BridgeClient(config=config, token_store=make_token_store(), ollama=make_ollama(MockOllama()))

    async def core() -> None:
        with pytest.raises(BridgeConnectionError):
            await bridge._connect_once()
        assert captured["ssl"] is None

    asyncio.run(core())


def test_tls_verify_failure_fails_fast_no_reconnect(monkeypatch) -> None:
    attempts: list = [0]

    def _fake_connect(*_a: object, **_k: object) -> object:
        attempts[0] += 1
        raise _VERIFY("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired")

    _stub_connect(monkeypatch, _fake_connect)



    def _must_not_reconnect(*_a: object, **_k: object) -> None:
        raise AssertionError("deterministic TLS trust failure must NEVER retry")


    async def core() -> None:
        config= make_config(server_url="https://127.0.0.1:1", max_reconnect_attempts=5, reconnect_backoff_base_seconds=0.01, reconnect_backoff_cap_seconds=0.05)
        bridge = BridgeClient(config=config, token_store=make_token_store(), ollama=make_ollama(MockOllama()), on_reconnecting=_must_not_reconnect)

        await bridge.run()
        assert attempts == [1], "exactly one attempt, no retry loop"
        assert bridge.sessions_connected == 0
        assert bridge.last_error is not None
        assert bridge.last_error.reason_code == REASON_TLS_CERTIFICATE_EXPIRED

    asyncio.run(core())


def test_connection_refused_retryable_still_reconnects(monkeypatch) -> None:
    attempts: list = [0]
    seen: list = []

    def _fake_connect(*_a: object, **_k: object) -> object:
        attempts[0] += 1
        raise ConnectionRefusedError("refused")

    _stub_connect(monkeypatch, _fake_connect)



    def _on_reconnecting(attempt: int, delay: float) -> None:
        seen.append((attempt, delay))


    async def core() -> None:
        config= make_config(server_url="https://127.0.0.1:1", max_reconnect_attempts=1, reconnect_backoff_base_seconds=0.01, reconnect_backoff_cap_seconds=0.05)
        bridge = BridgeClient(config=config, token_store=make_token_store(), ollama=make_ollama(MockOllama()), on_reconnecting=_on_reconnecting)


        await bridge.run()
        assert attempts == [2], "one retry cycle, then give-up by max_reconnect_attempts"
        assert bridge.sessions_connected == 0
        assert bridge.last_error is None
        assert len(seen) == 1

    asyncio.run(core())


# --------------------------------------------------------------------------- #
# structured digits-safe logging （Phase 31CD §16）
# --------------------------------------------------------------------------- #


def test_certifi_fallback_logs_structured(caplog, monkeypatch) -> None:
    ctx = _Ctx(ca_count=7)
    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: False)
    monkeypatch.setattr(tls, "_create_default_context", lambda *_a, **_k: ctx)
    monkeypatch.setattr(tls, "_certifi_cafile", lambda: "C:/cacert.pem")

    with caplog.at_level(logging.INFO, logger="pd-ollama-bridge"):
        result = build_client_ssl_context("wss://server.example")
    assert result.trust_source == tls.TRUST_SOURCE_CERTIFI
    joined= "\n".join(record.message for record in caplog.records)
    assert "bridge.tls.trust_store_fallback" in joined
    assert "trustSource=certifi" in joined


def test_run_logs_classified_tls_failure(caplog, monkeypatch) -> None:
    def _fake_connect(*_a: object, **_k: object) -> object:
        raise _VERIFY("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired")

    _stub_connect(monkeypatch, _fake_connect)



    async def core() -> None:
        config= make_config(server_url="https://127.0.0.1:1", max_reconnect_attempts=0)
        bridge = BridgeClient(config=config, token_store=make_token_store(), ollama=make_ollama(MockOllama()))
        await bridge.run()
        assert bridge.last_error is not None

    with caplog.at_level(logging.ERROR, logger="pd-ollama-bridge"):
        asyncio.run(core())
    joined= "\n".join(record.message for record in caplog.records)
    assert "bridge.tls.verify_failed" in joined
    assert "reasonCode=TLS_CERTIFICATE_EXPIRED" in joined
    assert "transport=wss" in joined
    assert "retryable=false" in joined


# --------------------------------------------------------------------------- #
# CLI regression ($24): before-> bare ConnectionError / generic session message;
# after-> the safe classified TLS message, no raw exception text.


# --------------------------------------------------------------------------- #


class _AvailableOllama:
    async def check_available(self) -> tuple:
        return True, ("hermes3:8b",)

    async def aclose(self) -> None:
        return None


def test_cli_tls_verify_failure_prints_safe_message(monkeypatch, tmp_path, capsys) -> None:
    def _fake_connect(*_a: object, **_k: object) -> object:
        raise _VERIFY("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired")

    _stub_connect(monkeypatch, _fake_connect)
    monkeypatch.setattr(cli_mod, "OllamaClient", lambda **_kw: _AvailableOllama())
    rc= main(["connect", TEST_PAIRING_CODE, "--server", "wss://127.0.0.1:1", "--memory-only", "--config", str(tmp_path / "bridge.toml")])
    cli_mod.LOGGER.handlers.clear()
    out, err= capsys.readouterr()
    assert rc == 1
    assert "TLS certificate verification failed" in err
    assert "could not establish a bridge session" not in err
    assert "certificate has expired" not in err
    assert "Traceback" not in err


def test_cli_no_trust_store_prints_safe_message(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(tls, "_context_has_cas", lambda _c: False)

    def _no_certifi() -> str:
        raise ImportError("certifi unavailable")

    monkeypatch.setattr(tls, "_certifi_cafile", _no_certifi)
    monkeypatch.setattr(cli_mod, "OllamaClient", lambda **_kw: _AvailableOllama())
    rc= main(["connect", TEST_PAIRING_CODE, "--server", "wss://127.0.0.1:1", "--memory-only", "--config", str(tmp_path / "bridge.toml")])

    cli_mod.LOGGER.handlers.clear()
    out, err= capsys.readouterr()
    assert rc == 1
    assert "no usable CA trust store was found" in err
    assert "could not establish a bridge session" not in err
    assert "Traceback" not in err