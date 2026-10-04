"""URL ownership and loopback enforcement for the bridge.

Two separate concerns:

1. ``resolve_server_ws_url`` — the bridge connects OUTBOUND to the operator-
   configured Procedural Detective server. ``https``/``wss`` is required for
   non-loopback hosts; plain ``ws`` is accepted ONLY for localhost/development
   (Phase22 section 5).
2. ``validate_ollama_url`` — the bridge OWNS its local Ollama endpoint. The
   default production-safe set is exactly ``localhost`` / ``127.0.0.1`` /
   ``::1`` unless the operator explicitly opts into ``--lan`` (which prints a
   warning). A remote server can never change this URL through the protocol.
"""

from __future__ import annotations

import re
import urllib.parse

from . import protocol

LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

# F1 — strict netloc character guard for BOTH validators: control characters,
# quotes and whitespace are NEVER legitimate inside a server/Ollama URL, and
# ``urllib.parse`` silently tolerates them (e.g. ``http://127.0.0.1:11434\nx=1``
# parses with netloc ``127.0.0.1:11434x=1``) where ``httpx`` would then blow up
# with an unhandled ``httpx.InvalidURL`` at request time. Rejecting the raw
# characters up front keeps every failing surface on the typed
# ``UrlValidationError`` -> clean CLI message path.
_INVALID_URL_CHARS_RE = re.compile(r'[\x00-\x20\x7f"\'`]')


class UrlValidationError(ValueError):
    pass


def _reject_invalid_url_chars(raw: str, field: str) -> None:
    if _INVALID_URL_CHARS_RE.search(raw):
        raise UrlValidationError(
            f"{field} must not contain control characters, whitespace or quotes"
        )


def _validate_url_port(parsed: urllib.parse.SplitResult, field: str) -> None:
    """Reject any malformed netloc port with the canonical typed error.

    ``SplitResult.port`` raises ``ValueError`` for a non-numeric or
    out-of-range port; httpx would otherwise fail much later with an unhandled
    ``httpx.InvalidURL`` at request time. Port ``0`` is additionally rejected
    (never a legitimate service port).
    """
    try:
        port = parsed.port
    except ValueError as exc:
        raise UrlValidationError(f"{field} has an invalid port") from exc
    if port is not None and not (1 <= port <= 65535):
        raise UrlValidationError(f"{field} has an invalid port")


def _normalize_host(host: str) -> str:
    lowered = host.lower()
    if lowered.startswith("[") and lowered.endswith("]"):
        return lowered[1:-1]
    return lowered


def is_loopback_host(host: str) -> bool:
    return _normalize_host(host) in LOOPBACK_HOSTS


def resolve_server_ws_url(server_url: str) -> str:
    """Map an ``http(s)://`` string to the ``ws(s)://`` bridge endpoint URL.

    Rejects anything but an http(s) URL with a host and an origin-level path.
    The endpoint path is FIXED at ``/api/v1/bridge/ws`` (Phase22 section 5).
    """
    if not isinstance(server_url, str) or not server_url.strip():
        raise UrlValidationError("server URL is required")
    # F1 — the character guard runs on the RAW input so control chars/quotes/
    # whitespace are rejected even when ``str.strip()`` would have hidden them.
    _reject_invalid_url_chars(server_url, "server URL")
    raw = server_url.strip()
    parsed = urllib.parse.urlparse(raw)
    _validate_url_port(parsed, "server URL")
    if parsed.scheme not in ("http", "https", "ws", "wss"):
        raise UrlValidationError("server URL must use http(s) or ws(s)")
    if not parsed.hostname:
        raise UrlValidationError("server URL must include a host")
    if parsed.username or parsed.password:
        raise UrlValidationError("server URL must not embed credentials")
    if parsed.query or parsed.fragment:
        raise UrlValidationError("server URL must not contain a query or fragment")
    path = parsed.path or "/"
    if path != "/":
        raise UrlValidationError("server URL must be an origin URL (no path)")
    scheme = parsed.scheme
    if scheme in ("https", "wss"):
        ws_scheme = "wss"
    else:
        ws_scheme = "ws"
        if not is_loopback_host(parsed.hostname):
            raise UrlValidationError(
                "plain ws:// is accepted only for localhost/development servers; "
                "use https/wss for a remote server"
            )
    netloc = parsed.netloc
    return f"{ws_scheme}://{netloc}/api/v1/bridge/ws"


def server_origin(server_url: str) -> str:
    """The canonical server binding identity used for token binding (Phase 27 §9).

    Maps an operator server URL to its ``ws(s)://host[:port]`` ORIGIN without
    the fixed ``/api/v1/bridge/ws`` path, e.g. ``https://pd.example.com:8443``
    -> ``wss://pd.example.com:8443``. Validation is identical to
    ``resolve_server_ws_url`` (remote plain ``ws`` is still rejected). A token
    stored for one origin MUST NEVER be replayed against a different origin.
    """
    ws = resolve_server_ws_url(server_url)
    prefix = "wss://" if ws.startswith("wss://") else "ws://"
    return prefix + ws[len(prefix):].split("/", 1)[0]


def validate_ollama_url(raw: str, *, allow_lan: bool = False) -> str:
    """Validate an operator-configured Ollama base URL.

    Loopback-only unless ``allow_lan`` is explicitly set (the ``--lan`` flag).
    Raises ``UrlValidationError`` otherwise. The remote server can never pass
    a URL into this function — the bridge always uses its own local value.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise UrlValidationError("Ollama URL is required")
    # F1 — the character guard runs on the RAW input so control chars/quotes/
    # whitespace are rejected even when ``str.strip()`` would have hidden them.
    _reject_invalid_url_chars(raw, "Ollama URL")
    raw = raw.strip()
    parsed = urllib.parse.urlparse(raw)
    _validate_url_port(parsed, "Ollama URL")
    if parsed.scheme not in ("http", "https"):
        raise UrlValidationError("Ollama URL must use http or https")
    if not parsed.hostname:
        raise UrlValidationError("Ollama URL must include a host")
    if parsed.username or parsed.password:
        raise UrlValidationError("Ollama URL must not embed credentials")
    if parsed.query or parsed.fragment:
        raise UrlValidationError("Ollama URL must not contain a query or fragment")
    if parsed.path not in ("", "/"):
        raise UrlValidationError("Ollama URL must be a base URL (no path)")
    host = _normalize_host(parsed.hostname)
    if not allow_lan and host not in LOOPBACK_HOSTS:
        raise UrlValidationError(
            "Ollama URL host must be localhost, 127.0.0.1 or ::1 "
            "(OLLAMA_BASE_URL is operator-local; the server can never set it). "
            "Pass --lan to explicitly allow a LAN host."
        )
    return f"{parsed.scheme}://{parsed.netloc}"


__all__ = [
    "LOOPBACK_HOSTS",
    "UrlValidationError",
    "is_loopback_host",
    "resolve_server_ws_url",
    "server_origin",
    "validate_ollama_url",
]