"""Bridge TLS trust-store selection and connection-failure classification.

Phase 31CD — the bridge client must build a VERIFIED ``wss://`` context on
every supported Python installation, including Windows/Anaconda builds whose
Python/OpenSSL default trust path is missing or unusable (``cafile=None``).

Selection precedence for ``wss://`` (never weakens verification):
1. explicit operator/env trust config: ``SSL_CERT_FILE``/``SSL_CERT_DIR``
   are honoured by ``ssl.create_default_context()`` itself — certifi never
   overrides an explicit trust choice that actually yields usable CA
   certificates (a stale/broken env path holding zero CAs is NOT labelled
   ``environment``: the bridge falls through to the fallback below);
2. usable Python/platform default trust when the loaded context actually holds
   CA certificates (judged by real context construction, not merely by
   ``get_default_verify_paths().cafile is not None`` — Phase 31CD §30);
3. a secure ``certifi`` fallback bundle when no usable default exists.

Fail-closed (Phase 31CD §31): when neither a usable default nor the
certifi bundle can be loaded, a ``TLSTrustStoreUnavailableError`` is raised —
nothing ever continues with verification disabled (no ``CERT_NONE``.
 No
global mutation of the process environment is performed either (a
per-connection ``ssl.SSLContext`` is passed to ``websockets.connect``..

 The
module also owns the closed vocabulary of classified connection failures
(``reason_code`` + ``retryable``) so the bridge can surface safe, actionable
TLS errors instead of collapsing them into a bare ``ConnectionError``
(Phase 31CD §13/§14/§16).
"""

from __future__ import annotations

import logging
import os
import socket
import ssl
from dataclasses import dataclass
from typing import Any, Mapping, Optional

LOGGER = logging.getLogger("pd-ollama-bridge")

# -- closed reason-code vocabulary (safe for CLI/log surfaces) --------------
REASON_CONNECTION_ERROR = "CONNECTION_ERROR"
REASON_CONNECTION_REFUSED = "CONNECTION_REFUSED"
REASON_CONNECTION_TIMEOUT = "CONNECTION_TIMEOUT"
REASON_DNS_RESOLUTION_FAILED = "DNS_RESOLUTION_FAILED"
REASON_TLS_CERTIFICATE_EXPIRED = "TLS_CERTIFICATE_EXPIRED"
REASON_TLS_CERTIFICATE_VERIFY_FAILED = "TLS_CERTIFICATE_VERIFY_FAILED"
REASON_TLS_HOSTNAME_MISMATCH = "TLS_HOSTNAME_MISMATCH"
REASON_TLS_TRUST_STORE_UNAVAILABLE = "TLS_TRUST_STORE_UNAVAILABLE"

# -- trust-source tags (Phase 31CD §17; debug-safe observability) ---------
TRUST_SOURCE_NONE = "none"
TRUST_SOURCE_ENVIRONMENT = "environment"
TRUST_SOURCE_DEFAULT = "default"
TRUST_SOURCE_CERTIFI = "certifi"


class BridgeConnectionError(ConnectionError):
    """A classified bridge connection failure with a safe ``reason_code``.



    Classes that can be reliably detected on the supported Python/websockets
    stack (Phase 31CD §13) map to subclasses;; everything else stays
    ``CONNECTION_ERROR``. ``retryable`` tells ``run()`` whether the backoff
    reconnect loop can possibly repair the failure: deterministic TLS trust
    failures are ``retryable=False`` (retry does not repair a trust failure)..
    Only the class name / reason code / safe fields are ever logged — never raw
    exception messages that could echo URLs, certificate bodies or secrets..
    """

    reason_code: str = REASON_CONNECTION_ERROR
    retryable: bool = True

    def __init__(
        self,
        message: str = "",
        *,
        transport: str = "ws",
        trust_source: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.transport = transport
        self.trust_source = trust_source


class TLSVerifyFailedError(BridgeConnectionError):
    """Certificate verification failed (generic verify failure.."""

    reason_code: str = REASON_TLS_CERTIFICATE_VERIFY_FAILED
    retryable: bool = False


class TLSCertificateExpiredError(TLSVerifyFailedError):
    """The verify failure was reported as an expired certificate (Python
    3.12 / OpenSSL surface)."""

    reason_code: str = REASON_TLS_CERTIFICATE_EXPIRED


class TLSHostnameMismatchError(TLSVerifyFailedError):
    """Certificate does not match the server hostname (distinct from every
    other verify failure where the OpenSSL message makes it reliable)."""

    reason_code: str = REASON_TLS_HOSTNAME_MISMATCH


class TLSTrustStoreUnavailableError(BridgeConnectionError):
    """Fail-closed: no usable CA trust store could be established at all."""

    reason_code: str = REASON_TLS_TRUST_STORE_UNAVAILABLE
    retryable: bool = False


class ConnectionRefusedBridgeError(BridgeConnectionError):
    reason_code: str = REASON_CONNECTION_REFUSED


class ConnectionTimeoutBridgeError(BridgeConnectionError):
    reason_code: str = REASON_CONNECTION_TIMEOUT


class DnsResolutionFailedBridgeError(BridgeConnectionError):
    reason_code: str = REASON_DNS_RESOLUTION_FAILED


# -- injectable seams (hermetic tests mock these; production uses ssl/certifi)



def _create_default_context(*args: Any, **kwargs: Any) -> ssl.SSLContext:
    """Production seam: Python's verified default context constructor."""
    return ssl.create_default_context(*args, **kwargs)


def _context_has_cas(ctx: ssl.SSLContext) -> bool:
    """True when the context actually loaded at least one CA certificate.


    ``ssl.create_default_context()`` does not raise when the compiled-in default
    CA file is missing on a platform — it silently builds a verification-off
    empty store and every real ``wss://`` handshake then fails with atrusted
    certificate error. "Usable" is therefore judged by REAL context
    construction behavior (Phase 31CD §30), not by
    ``get_default_verify_paths().cafile is not None``.。”
"""
    try:
        stats = ctx.cert_store_stats()
    except Exception:  # noqa: BLE001 - treat an unreadable store as unusable
        return False
    return int(stats.get("x509_ca", 0)) > 0


def _certifi_cafile() -> str:
    """Lazy certifi import seam (tests simulate an absent certifi cleanly)."""
    import certifi

    return certifi.where()


def _env_trust_configured(env: Mapping[str, str]) -> bool:
    """Explicit operator/env trust configuration present?(Phase 31CD §8)."""
    return bool((env.get("SSL_CERT_FILE") or "").strip()) or bool(
        (env.get("SSL_CERT_DIR") or "").strip()
    )


@dataclass(frozen=True, slots=True)
class BridgeTlsContext:
    """The verified context PLUS its debug-safe ``trustSource`` tag."""

    ssl_context: Optional[ssl.SSLContext]
    trust_source: str


def build_client_ssl_context(
    uri: str,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> BridgeTlsContext:
    """Build the TLS context for one bridge WebSocket URI (Phase 31CD §5-§8).

    ``ws://``  -> ``BridgeTlsContext(None, "none")``  (TLS path unchanged).
    ``wss://`` -> a VERIFIED context, preserving ``check_hostname=True`` and
    ``verify_mode=CERT_REQUIRED``; never ``CERT_NONE``. Explicit
    ``SSL_CERT_FILE``/``SSL_CERT_DIR`` take precedence over certifi and are
    honoured exactly as ``ssl.create_default_context()`` already does; certifi
    is used ONLY as a fallback when no usable default trust exists, and the
    helper FAILS CLOSED (``TLSTrustStoreUnavailableError``) rather than ever
    continuing unverified. An explicit env trust that yields a context with NO
    usable CAs is never reported as trusted: it falls through to the default /
    certifi fallback (or fails closed).
    """
    if not isinstance(uri, str) or not uri.startswith("wss://"):
        return BridgeTlsContext(None, TRUST_SOURCE_NONE)
    env = os.environ if env is None else env
    transport = "wss"

    default_ctx: Optional[ssl.SSLContext] = None
    if _env_trust_configured(env):
        # Explicit operator/env trust config: honor it; NEVER replace with certifi.
        try:
            env_ctx = _create_default_context()
        except (OSError, ValueError) as exc:
            raise TLSTrustStoreUnavailableError(
                "explicit SSL_CERT_FILE/SSL_CERT_DIR trust configuration "
                "could not be loaded for secure WebSocket verification",
                transport=transport,
                trust_source=TRUST_SOURCE_NONE,
            ) from exc
        # "Usable" is judged by real context construction (Phase 31CD §30): a
        # stale/broken SSL_CERT_FILE/DIR silently builds an EMPTY verified
        # context on some platforms, so never label that "environment" — fall
        # through to the default/certifi fallback (or fail closed below).
        if _context_has_cas(env_ctx):
            return BridgeTlsContext(env_ctx, TRUST_SOURCE_ENVIRONMENT)
        default_ctx = env_ctx

    if default_ctx is None:
        try:
            default_ctx = _create_default_context()
        except (OSError, ValueError):
            default_ctx = None
    if default_ctx is not None and _context_has_cas(default_ctx):
        return BridgeTlsContext(default_ctx, TRUST_SOURCE_DEFAULT)


    # Secure certifi fallback (Phase 31CD §7/§23) — never a downgrade.
    try:
        cafile = _certifi_cafile()
    except ImportError as exc:
        raise TLSTrustStoreUnavailableError(
            "no usable CA trust store was found for secure WebSocket "
            "verification (certifi is unavailable)",
            transport=transport,
            trust_source=TRUST_SOURCE_NONE,
        ) from exc
    if not cafile:
        raise TLSTrustStoreUnavailableError(
            "no usable CA trust store was found for secure WebSocket "
            "verification (certifi returned no bundle)",
            transport=transport,
            trust_source=TRUST_SOURCE_NONE,
        )
    try:
        ctx = _create_default_context(cafile=cafile)
    except (OSError, ValueError) as exc:
        raise TLSTrustStoreUnavailableError(
            "no usable CA trust store was found for secure WebSocket "
            "verification (certifi bundle could not be loaded)",
            transport=transport,
            trust_source=TRUST_SOURCE_NONE,
        ) from exc
    LOGGER.info(
        "bridge.tls.trust_store_fallback transport=%s trustSource=%s",
        transport,
        TRUST_SOURCE_CERTIFI,
    )
    return BridgeTlsContext(ctx, TRUST_SOURCE_CERTIFI)


def classify_connect_exception(
    exc: BaseException,
    *,
    transport: str,
    trust_source: Optional[str] = None,
) -> BridgeConnectionError:
    """Map a raw transport exception onto the classified bridge vocabulary.


    Order matters: ``ssl.SSLCertVerificationError`` is an ``OSError``
    subclass, so it must be checked before the OSError-based generic cases.


    Only subtypes that can be reliably detected on the supported Python are
    implemented (Phase 31CD §13): hostname mismatch and expired are
    recognized from the stable OpenSSL message text on Python 3.12; every
    other verify failure maps to the generic ``TLS_CERTIFICATE_VERIFY_FAILED``。
"""
    if isinstance(exc, ssl.SSLCertVerificationError):
        message = str(exc) or ""
        lowered = message.lower()
        if (
            "hostname" in lowered
            or "doesn't match" in lowered
            or "does not match" in lowered
            or "not valid for" in lowered
        ):
            return TLSHostnameMismatchError(
                "TLS hostname verification failed for the bridge server",
                transport=transport,
                trust_source=trust_source,
            )
        if "expired" in lowered:
            return TLSCertificateExpiredError(
                "TLS certificate verification failed for the bridge server",
                transport=transport,
                trust_source=trust_source,
            )
        return TLSVerifyFailedError(
            "TLS certificate verification failed for the bridge server",
            transport=transport,
            trust_source=trust_source,
        )
    if isinstance(exc, ConnectionRefusedError):
        return ConnectionRefusedBridgeError(
            "connection refused by the bridge server",
            transport=transport,
            trust_source=trust_source,
        )
    if isinstance(exc, socket.gaierror):
        return DnsResolutionFailedBridgeError(
            "could not resolve the bridge server hostname",
            transport=transport,
            trust_source=trust_source,
        )
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ConnectionTimeoutBridgeError(
            "connection to the bridge server timed out",
            transport=transport,
            trust_source=trust_source,
        )
    return BridgeConnectionError(
        "cannot connect to the bridge server",
        transport=transport,
        trust_source=trust_source,
    )


__all__ = [
    "BridgeConnectionError",
    "BridgeTlsContext",
    "ConnectionRefusedBridgeError",
    "ConnectionTimeoutBridgeError",
    "DnsResolutionFailedBridgeError",
    "REASON_CONNECTION_ERROR",
    "REASON_CONNECTION_REFUSED",
    "REASON_CONNECTION_TIMEOUT",
    "REASON_DNS_RESOLUTION_FAILED",
    "REASON_TLS_CERTIFICATE_EXPIRED",
    "REASON_TLS_CERTIFICATE_VERIFY_FAILED",
    "REASON_TLS_HOSTNAME_MISMATCH",
    "REASON_TLS_TRUST_STORE_UNAVAILABLE",
    "TLSCertificateExpiredError",
    "TLSHostnameMismatchError",
    "TLSTrustStoreUnavailableError",
    "TLSVerifyFailedError",
    "TRUST_SOURCE_CERTIFI",
    "TRUST_SOURCE_DEFAULT",
    "TRUST_SOURCE_ENVIRONMENT",
    "TRUST_SOURCE_NONE",
    "build_client_ssl_context",
    "classify_connect_exception",
]