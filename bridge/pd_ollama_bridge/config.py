"""Bridge configuration container and session-token persistence.

A ``Config`` is an immutable description of ONE operator-selected run: the
server to connect outbound to, the LOCAL ONLY Ollama endpoint, the selected
local model and the protocol bounds. Everything here is operator-owned; the
remote server never contributes a configuration value.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import protocol
from .urls import UrlValidationError, resolve_server_ws_url, validate_ollama_url


@dataclass(frozen=True, slots=True)
class Config:
    pairing_code: Optional[str] = None
    server_url: str = protocol.DEFAULT_SERVER_URL
    ollama_url: str = protocol.DEFAULT_OLLAMA_URL
    model: str = protocol.DEFAULT_MODEL
    connect_timeout_seconds: float = protocol.DEFAULT_CONNECT_TIMEOUT_SECONDS
    lan: bool = False
    debug: bool = False
    token_file: Optional[Path] = None
    idle_timeout_seconds: float = protocol.IDLE_TIMEOUT_SECONDS
    reconnect_backoff_base_seconds: float = protocol.RECONNECT_BACKOFF_BASE_SECONDS
    reconnect_backoff_cap_seconds: float = protocol.RECONNECT_BACKOFF_CAP_SECONDS
    reconnect_reset_seconds: float = 300.0
    max_reconnect_attempts: int = 12
    capabilities: tuple[str, ...] = field(
        default=(protocol.CAPABILITY_STRUCTURED_MODEL_INFERENCE,)
    )
    max_message_bytes: int = field(default=protocol.BRIDGE_MAX_MESSAGE_BYTES)
    max_prompt_bytes: int = field(default=protocol.MAX_PROMPT_BYTES)
    max_schema_bytes: int = field(default=protocol.MAX_SCHEMA_BYTES)
    max_response_bytes: int = field(default=protocol.MAX_RESPONSE_BYTES)
    max_json_depth: int = field(default=protocol.MAX_JSON_DEPTH)
    max_collection_length: int = field(default=protocol.MAX_COLLECTION_LENGTH)

    @property
    def server_ws_url(self) -> str:
        return resolve_server_ws_url(self.server_url)

    @property
    def normalized_ollama_url(self) -> str:
        return validate_ollama_url(self.ollama_url, allow_lan=self.lan)

    def validate(self) -> "Config":
        self.server_ws_url
        self.normalized_ollama_url
        if self.pairing_code is not None:
            if not protocol.PAIRING_CODE_RE.fullmatch(self.pairing_code):
                raise UrlValidationError("pairing code must have the form PD-XXXX-XXXX")
        if not protocol.MODEL_LABEL_RE.fullmatch(self.model) or not self.model:
            raise UrlValidationError(
                "model label may contain only [A-Za-z0-9._:-] characters"
            )
        if self.connect_timeout_seconds <= 0:
            raise UrlValidationError("connect timeout must be positive")
        if self.max_reconnect_attempts < 0:
            raise UrlValidationError("max reconnect attempts must be >= 0")
        allowed = set(self.capabilities)
        bad = allowed - set(protocol.ALLOWED_CAPABILITIES)
        if bad or protocol.CAPABILITY_STRUCTURED_MODEL_INFERENCE not in allowed:
            raise UrlValidationError("capabilities must be a subset of the allowed set")
        return self


def _token_permission_warning(path: Path, detail: str) -> None:
    print(
        f"WARNING: could not apply restrictive permissions to token file "
        f"{path} ({detail}). The token may be readable by other local users. "
        "Prefer a private directory, or delete the token file after use.",
        file=sys.stderr,
    )


def _apply_restrictive_permissions(path: Path) -> None:
    """Best-effort strongest-practical permissions for a token file.

    POSIX: ``0600`` (owner read/write only). Windows: inherited ACLs are
    removed and the current owner is granted read/write only, via ``icacls``.
    If the ACL cannot be applied, a warning is printed and the file is left
    as-is (Windows does not enforce POSIX mode bits).
    """
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return
    try:
        user = getpass.getuser()
        result = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{user}:(R,W)",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            _token_permission_warning(
                path, f"icacls failed: {result.stderr.strip() or 'unknown error'}"
            )
    except Exception as exc:  # noqa: BLE001 - best-effort only
        _token_permission_warning(path, str(exc))


class TokenStore:
    """Memory-only bridge session token persistence with an optional file.

    By default the token is kept in memory for the process lifetime only; no
    file is written. When ``path`` is given (the CLI ``--token-file`` opt-in)
    it is also persisted with the strongest practical permissions: ``0600`` on
    POSIX, best-effort owner-only ACL on Windows (with a warning if the ACL
    cannot be applied).
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else None
        self._token: Optional[str] = None

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def get(self) -> Optional[str]:
        return self._token

    def load(self) -> Optional[str]:
        if self._path is None or not self._path.exists():
            return None
        try:
            raw = self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw:
            return None
        self._token = raw
        return self._token

    def save(self, token: str) -> None:
        self._token = token
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, token.encode("utf-8"))
            finally:
                os.close(fd)
        except OSError:
            return
        _apply_restrictive_permissions(self._path)

    def clear(self) -> None:
        self._token = None
        if self._path is not None:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = ["Config", "TokenStore"]