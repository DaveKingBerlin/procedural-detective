"""Persistent Bridge configuration (``bridge.toml``) + precedence resolution.

Phase 27 — a dedicated NON-SECRET operator config file plus a strict
deterministic precedence chain:

    CLI argument -> environment variable -> bridge.toml -> safe built-in default

Secrets (pairing code, Bridge token, API keys, credentials, CaseTruth, raw
prompts) are NEVER allowed here. The TOML may store at most a token file PATH
(``token_file``), never token contents.

Platform config paths (Phase 27 §1):

- Windows: ``%LOCALAPPDATA%\\ProceduralDetective\\bridge.toml`` with the
  fallback ``%USERPROFILE%\\.procedural-detective\\bridge.toml`` when
  ``LOCALAPPDATA`` is unavailable;
- Linux: ``$XDG_CONFIG_HOME/procedural-detective/bridge.toml`` or
  ``~/.config/procedural-detective/bridge.toml``.

Writes are crash-safe (temp file + flush + atomic ``os.replace``), malformed
TOML fails clearly, and only ``configure`` may modify the file.
"""

from __future__ import annotations

import json
import math
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from . import protocol
from .urls import UrlValidationError, resolve_server_ws_url, validate_ollama_url

CONFIG_FILENAME = "bridge.toml"
TOKEN_FILENAME = "bridge_token"

# Phase 27 §4 environment variable names (canonical).
ENV_SERVER = "PD_BRIDGE_SERVER"
ENV_OLLAMA = "PD_BRIDGE_OLLAMA"
ENV_TIMEOUT = "PD_BRIDGE_TIMEOUT"
ENV_MAX_RETRIES = "PD_BRIDGE_MAX_RETRIES"
ENV_TOKEN_FILE = "PD_BRIDGE_TOKEN_FILE"
ENV_LAN = "PD_BRIDGE_LAN"
ENV_DEBUG = "PD_BRIDGE_DEBUG"

# Safe built-in defaults (Phase 27 §5). No production hostname is hard-coded.
DEFAULT_MAX_RETRIES = 12

# The only keys allowed in bridge.toml (canonical order used for writes).
ALLOWED_KEYS: tuple[str, ...] = (
    "server",
    "ollama",
    "timeout",
    "max_retries",
    "lan",
    "debug",
    "model",
    "token_file",
)


class BridgeConfigError(Exception):
    """An operator-facing bridge.toml failure (load/validate/write)."""


@dataclass(frozen=True)
class FileConfig:
    """The non-secret fields persisted in ``bridge.toml`` (all optional)."""

    server_url: Optional[str] = None
    ollama_url: Optional[str] = None
    timeout_seconds: Optional[float] = None
    max_retries: Optional[int] = None
    lan: Optional[bool] = None
    debug: Optional[bool] = None
    model: Optional[str] = None
    token_file: Optional[str] = None

    def as_mapping(self) -> dict[str, Any]:
        """Only the explicitly-set fields, in canonical order (safe for TOML)."""
        out: dict[str, Any] = {}
        for key in ALLOWED_KEYS:
            value = getattr(self, _KEY_TO_ATTR[key])
            if value is not None:
                out[key] = value
        return out

    def merged_with(self, other: "FileConfig") -> "FileConfig":
        """Return a FileConfig where ``other``'s set fields win over ``self``."""
        return FileConfig(
            server_url=other.server_url if other.server_url is not None else self.server_url,
            ollama_url=other.ollama_url if other.ollama_url is not None else self.ollama_url,
            timeout_seconds=(
                other.timeout_seconds
                if other.timeout_seconds is not None
                else self.timeout_seconds
            ),
            max_retries=(
                other.max_retries if other.max_retries is not None else self.max_retries
            ),
            lan=other.lan if other.lan is not None else self.lan,
            debug=other.debug if other.debug is not None else self.debug,
            model=other.model if other.model is not None else self.model,
            token_file=other.token_file if other.token_file is not None else self.token_file,
        )


_KEY_TO_ATTR: dict[str, str] = {
    "server": "server_url",
    "ollama": "ollama_url",
    "timeout": "timeout_seconds",
    "max_retries": "max_retries",
    "lan": "lan",
    "debug": "debug",
    "model": "model",
    "token_file": "token_file",
}
_ALLOWED_KEYS_SET = frozenset(ALLOWED_KEYS)


# --------------------------------------------------------------------------- #
# platform path resolution (fully injectable for hermetic tests — Phase 27 §1)
# --------------------------------------------------------------------------- #


def resolve_config_path(
    env: Optional[Mapping[str, str]] = None, *, platform: Optional[str] = None
) -> Optional[Path]:
    """Resolve the platform-appropriate ``bridge.toml`` path.

    ``env`` and ``platform`` are injectable so path behavior for BOTH Windows
    and Linux can be tested hermetically on any host without touching the live
    OS environment. Returns ``None`` when no base directory can be determined
    (the bridge then runs with defaults and no persistent config).
    """
    env = os.environ if env is None else env
    platform = os.name if platform is None else platform
    if platform.startswith("win") or platform == "nt":
        local = env.get("LOCALAPPDATA")
        if local:
            return Path(local) / "ProceduralDetective" / CONFIG_FILENAME
        profile = env.get("USERPROFILE")
        if profile:
            return Path(profile) / ".procedural-detective" / CONFIG_FILENAME
        return None
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "procedural-detective" / CONFIG_FILENAME
    home = env.get("HOME")
    if home:
        return Path(home) / ".config" / "procedural-detective" / CONFIG_FILENAME
    return None


def default_token_path(config_path: Optional[Path]) -> Optional[Path]:
    """The default secure token-file location next to the config file.

    Phase 27 §3 — the token file is separate from ``bridge.toml``; the TOML
    may hold at most this PATH, never the token contents.
    """
    if config_path is None:
        return None
    return Path(config_path).parent / TOKEN_FILENAME


# --------------------------------------------------------------------------- #
# TOML read/write (Phase 27 §13 — crash-safe, malformed fails clearly)
# --------------------------------------------------------------------------- #


def _field(path: Path, key: str) -> str:
    return f"{path} field '{key}'"


def _opt_str(obj: Mapping[str, Any], key: str, path: Path) -> Optional[str]:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BridgeConfigError(f"{_field(path, key)} must be a string")
    if value.strip() == "":
        raise BridgeConfigError(f"{_field(path, key)} must not be empty")
    return value


def _opt_float(obj: Mapping[str, Any], key: str, path: Path) -> Optional[float]:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BridgeConfigError(f"{_field(path, key)} must be a number")
    parsed = float(value)
    # F2 — ``nan``/``inf`` are numbers but would disable the connect timeout;
    # a persisted timeout must be a positive FINITE number.
    if not math.isfinite(parsed):
        raise BridgeConfigError(f"{_field(path, key)} must be a finite number")
    return parsed


def _opt_int(obj: Mapping[str, Any], key: str, path: Path) -> Optional[int]:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise BridgeConfigError(f"{_field(path, key)} must be an integer")
    return int(value)


def _opt_bool(obj: Mapping[str, Any], key: str, path: Path) -> Optional[bool]:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise BridgeConfigError(f"{_field(path, key)} must be true or false")
    return bool(value)


def read_config(path: Optional[Path]) -> FileConfig:
    """Load ``bridge.toml``. A missing file yields an empty ``FileConfig``.

    Malformed TOML, unknown keys, empty security-sensitive values and wrong
    types FAIL LOUDLY with ``BridgeConfigError`` — never silently ignored.
    """
    if path is None:
        return FileConfig()
    path = Path(path)
    if not path.exists():
        return FileConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BridgeConfigError(f"bridge.toml at {path} is not readable: {exc}") from exc
    try:
        obj = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise BridgeConfigError(f"bridge.toml at {path} is malformed TOML: {exc}") from exc
    if not isinstance(obj, dict):
        raise BridgeConfigError(f"bridge.toml at {path} must contain a TOML table")
    unknown = sorted(set(obj) - _ALLOWED_KEYS_SET)
    if unknown:
        raise BridgeConfigError(
            f"bridge.toml at {path} contains unknown field(s): {', '.join(unknown)}"
        )
    return FileConfig(
        server_url=_opt_str(obj, "server", path),
        ollama_url=_opt_str(obj, "ollama", path),
        timeout_seconds=_opt_float(obj, "timeout", path),
        max_retries=_opt_int(obj, "max_retries", path),
        lan=_opt_bool(obj, "lan", path),
        debug=_opt_bool(obj, "debug", path),
        model=_opt_str(obj, "model", path),
        token_file=_opt_str(obj, "token_file", path),
    )


def _toml_repr(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    # Basic TOML strings accept JSON escaping for the characters this schema
    # allows (no control chars / quotes in config values).
    return json.dumps(str(value))


def render_toml(data: Mapping[str, Any]) -> str:
    """Deterministic TOML text for the fixed bridge.toml schema."""
    extra = set(data) - _ALLOWED_KEYS_SET
    if extra:
        raise BridgeConfigError(
            f"cannot write unknown bridge.toml field(s): {', '.join(sorted(extra))}"
        )
    lines: list[str] = []
    for key in ALLOWED_KEYS:
        if key in data and data[key] is not None:
            lines.append(f"{key} = {_toml_repr(data[key])}")
    return "\n".join(lines) + ("\n" if lines else "")


def write_config(path: Path, data: Mapping[str, Any]) -> None:
    """Crash-safe config write: parent mkdir, temp file, flush, atomic replace.

    Phase 27 §13 — on POSIX the file is chmod'ed to ``0600`` (best effort). A
    failing write raises ``BridgeConfigError`` and leaves no temp litter.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        text = render_toml(data)
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except (OSError, BridgeConfigError) as exc:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        if isinstance(exc, BridgeConfigError):
            raise
        raise BridgeConfigError(f"could not write bridge.toml at {path}: {exc}") from exc
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def validate_file_config(cfg: FileConfig) -> None:
    """Validate the SECURITY-SENSITIVE fields of a FileConfig (never silently
    ignore a bad ``server``/``ollama``). Raises ``UrlValidationError`` with a
    clear message on the first invalid field."""
    if cfg.server_url is not None:
        resolve_server_ws_url(cfg.server_url)
    if cfg.ollama_url is not None:
        validate_ollama_url(cfg.ollama_url, allow_lan=bool(cfg.lan))
    if cfg.timeout_seconds is not None and (
        not math.isfinite(cfg.timeout_seconds) or cfg.timeout_seconds <= 0
    ):
        raise UrlValidationError("timeout must be a positive finite number")
    if cfg.max_retries is not None and cfg.max_retries < 0:
        raise UrlValidationError("max retries must be >= 0")
    if cfg.model is not None:
        if not cfg.model or not protocol.MODEL_LABEL_RE.fullmatch(cfg.model):
            raise UrlValidationError(
                "model label may contain only [A-Za-z0-9._:-] characters"
            )


# --------------------------------------------------------------------------- #
# precedence resolution (Phase 27 §4) — CLI >= env >= bridge.toml >= default
# --------------------------------------------------------------------------- #


def _env_str(env: Mapping[str, str], name: str) -> Optional[str]:
    value = env.get(name)
    if value is None or value == "":
        return None
    return value


def _env_float(env: Mapping[str, str], name: str) -> Optional[float]:
    value = _env_str(env, name)
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise BridgeConfigError(f"environment variable {name} must be a number") from exc
    # F2 — ``nan``/``inf`` parse cleanly but would disable the timeout.
    if not math.isfinite(parsed):
        raise BridgeConfigError(
            f"environment variable {name} must be a finite number"
        ) from None
    return parsed


def _env_int(env: Mapping[str, str], name: str) -> Optional[int]:
    value = _env_str(env, name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise BridgeConfigError(f"environment variable {name} must be an integer") from exc


def _env_bool(env: Mapping[str, str], name: str) -> Optional[bool]:
    value = _env_str(env, name)
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise BridgeConfigError(
        f"environment variable {name} must be true or false (got {value!r})"
    )


@dataclass(frozen=True)
class ResolvedOptions:
    """The fully-resolved non-secret run options (never contains a token)."""

    server_url: str
    ollama_url: str
    model: str
    connect_timeout_seconds: float
    max_retries: int
    lan: bool
    debug: bool
    token_file: Optional[Path]


def _first(*values: Any) -> Any:
    """The first non-None value (deterministic precedence chain)."""
    for value in values:
        if value is not None:
            return value
    raise AssertionError("precedence chain resolved no value")


def resolve_run_options(
    *,
    cli: Optional[Mapping[str, Any]] = None,
    env: Optional[Mapping[str, str]] = None,
    file_config: Optional[FileConfig] = None,
    default_token_file: Optional[Path] = None,
) -> ResolvedOptions:
    """Deterministic precedence resolution for one bridge run.

    ``cli`` keys use ``None`` for "not supplied on the command line"
    (``token_file`` may be supplied as a str/Path; ``memory_only`` is a bool
    that overrides every other token-file source — the CLI wins or explicitly
    opts out). Raises ``BridgeConfigError`` for unparseable env values.
    """
    cli = cli or {}
    env = os.environ if env is None else env
    file_config = file_config or FileConfig()

    server_url = _first(
        cli.get("server"),
        _env_str(env, ENV_SERVER),
        file_config.server_url,
        protocol.DEFAULT_SERVER_URL,
    )
    ollama_url = _first(
        cli.get("ollama"),
        _env_str(env, ENV_OLLAMA),
        file_config.ollama_url,
        protocol.DEFAULT_OLLAMA_URL,
    )
    model = _first(cli.get("model"), file_config.model, protocol.DEFAULT_MODEL)

    timeout = _first(
        cli.get("timeout"),
        _env_float(env, ENV_TIMEOUT),
        file_config.timeout_seconds,
        protocol.DEFAULT_CONNECT_TIMEOUT_SECONDS,
    )
    # F2 — a CLI/env/config timeout must be a positive FINITE number; a
    # ``nan``/``inf`` value would otherwise pass ``<= 0`` and silently DISABLE
    # the connect timeout (``asyncio.wait_for(timeout=nan)`` never fires).
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError) as exc:  # pragma: no cover - guarded upstream
        raise BridgeConfigError("timeout must be a number") from exc
    if not math.isfinite(timeout_value) or timeout_value <= 0:
        raise BridgeConfigError("timeout must be a positive finite number")
    max_retries = _first(
        cli.get("max_retries"),
        _env_int(env, ENV_MAX_RETRIES),
        file_config.max_retries,
        DEFAULT_MAX_RETRIES,
    )

    lan = _pick_bool(cli.get("lan"), _env_bool(env, ENV_LAN), file_config.lan, False)
    debug = _pick_bool(cli.get("debug"), _env_bool(env, ENV_DEBUG), file_config.debug, False)

    if cli.get("memory_only"):
        token_file: Optional[Path] = None
    elif cli.get("token_file") is not None:
        token_file = Path(str(cli["token_file"]))
    elif _env_str(env, ENV_TOKEN_FILE) is not None:
        token_file = Path(str(_env_str(env, ENV_TOKEN_FILE)))
    elif file_config.token_file is not None:
        token_file = Path(file_config.token_file)
    else:
        token_file = default_token_file

    return ResolvedOptions(
        server_url=server_url,
        ollama_url=ollama_url,
        model=model,
        connect_timeout_seconds=timeout_value,
        max_retries=int(max_retries),
        lan=lan,
        debug=debug,
        token_file=token_file,
    )


def _pick_bool(cli_value: Any, env_value: Any, config_value: Any, default: bool) -> bool:
    if cli_value is not None:
        return bool(cli_value)
    if env_value is not None:
        return bool(env_value)
    if config_value is not None:
        return bool(config_value)
    return default


__all__ = [
    "ALLOWED_KEYS",
    "BridgeConfigError",
    "CONFIG_FILENAME",
    "DEFAULT_MAX_RETRIES",
    "ENV_DEBUG",
    "ENV_LAN",
    "ENV_MAX_RETRIES",
    "ENV_OLLAMA",
    "ENV_SERVER",
    "ENV_TIMEOUT",
    "ENV_TOKEN_FILE",
    "FileConfig",
    "ResolvedOptions",
    "TOKEN_FILENAME",
    "default_token_path",
    "read_config",
    "render_toml",
    "resolve_config_path",
    "resolve_run_options",
    "validate_file_config",
    "write_config",
]