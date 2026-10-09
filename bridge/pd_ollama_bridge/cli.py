"""Command-line entry point for the Phase 27 bridge client (``pd-ollama-bridge``).

Subcommands:

- ``configure [options]`` — persist stable non-secret operator settings in
  ``bridge.toml``. Only explicitly supplied fields change; unspecified existing
  values are preserved. This is the ONLY command that writes ``bridge.toml``.
- ``connect [PAIRING_CODE] [options]`` — the long-running bridge session.
  With a pairing code: pairing flow -> persist token -> connect. Without a
  code: zero-argument reconnect using the stored server-bound token (fail-closed
  with a clear message when no usable token exists). Never modifies config.
- ``config`` — safe introspection (path, server, ollama, timeout, max retries,
  token present yes/no; the token itself is never printed).
- ``list-models [options]`` — list local Ollama model tags; resolves the
  Ollama endpoint through CLI -> env -> bridge.toml -> localhost.

Configuration precedence (Phase 27 §4):

    CLI argument -> environment variable -> bridge.toml -> safe built-in default

The bridge OWNS its server/Ollama URLs and model locally; the remote server
can never change them through the protocol. Pairing codes and tokens are never
stored in ``bridge.toml`` (Phase 27 §3/§14).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import httpx
from websockets.exceptions import InvalidURI

from . import protocol
from .bridge_client import BridgeClient
from .bridge_config import (
    BridgeConfigError,
    FileConfig,
    default_token_path,
    read_config,
    resolve_config_path,
    resolve_run_options,
    validate_file_config,
    write_config,
)
from .config import Config, TokenStore
from .ollama_client import OllamaClient
from .tls import (
    REASON_TLS_CERTIFICATE_EXPIRED,
    REASON_TLS_CERTIFICATE_VERIFY_FAILED,
    REASON_TLS_HOSTNAME_MISMATCH,
    REASON_TLS_TRUST_STORE_UNAVAILABLE,
    BridgeConnectionError,
)
from .urls import UrlValidationError, server_origin

LOGGER = logging.getLogger("pd-ollama-bridge")

BANNER = "Procedural Detective Local AI Bridge"

# Sentinel: an option that was NOT explicitly supplied on the command line
# (argparse would otherwise fill in ``None``/the built-in default and hide the
# difference between "explicit value" and "no value, resolve from env/config").
_UNSET = object()


class BridgeCliError(Exception):
    """An operator-facing CLI error with a clear, no-network message."""


@dataclass(frozen=True)
class ConnectPlan:
    """The fully-resolved decision for one ``connect`` invocation (Phase 27
    §7/§8): either ``"pairing"`` (a code was supplied) or ``"reconnect"``
    (a valid server-bound stored token is available for the SAME origin)."""

    mode: str
    config: Config
    token_store: TokenStore
    origin: str
    config_path: Optional[Path]


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pd-ollama-bridge",
        description=(
            "Procedural Detective Local AI Bridge. Connects your local Ollama "
            "to a Procedural Detective server. Your Ollama stays localhost-only."
        ),
        epilog=(
            "Examples:\n"
            "  First setup:  pd-ollama-bridge configure --server wss://pd.example.com\n"
            "  Pair:         pd-ollama-bridge connect PD-ABCD-1234\n"
            "  Reconnect:    pd-ollama-bridge connect\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    configure = sub.add_parser(
        "configure", help="persist stable bridge settings in bridge.toml"
    )
    configure.add_argument(
        "--server",
        default=_UNSET,
        help="server origin URL, e.g. wss://pd.example.com (wss/https only; plain ws is localhost-only)",
    )
    configure.add_argument(
        "--ollama",
        default=_UNSET,
        help="local Ollama base URL (localhost only; default http://127.0.0.1:11434)",
    )
    configure.add_argument(
        "--timeout", type=float, default=_UNSET, help="connect/check timeout in seconds"
    )
    configure.add_argument(
        "--max-retries",
        type=int,
        default=_UNSET,
        help="max reconnect attempts after a link drop (0 disables reconnect)",
    )
    configure.add_argument(
        "--model", default=_UNSET, help="legacy/fallback local Ollama model tag"
    )
    configure.add_argument(
        "--token-file",
        type=Path,
        default=_UNSET,
        metavar="PATH",
        help="persist the bridge session token to this file (PATH ONLY, never the token value)",
    )
    configure.add_argument(
        "--lan",
        action=argparse.BooleanOptionalAction,
        default=_UNSET,
        help="allow a non-loopback Ollama endpoint (advanced; default no)",
    )
    configure.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=_UNSET,
        help="verbose bridge logging (default no)",
    )
    configure.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="use this bridge.toml instead of the platform default",
    )

    connect = sub.add_parser("connect", help="connect to the Procedural Detective server")
    connect.add_argument(
        "pairing_code",
        nargs="?",
        default=None,
        help="pairing code PD-XXXX-XXXX (required only for first pairing or re-pairing)",
    )
    connect.add_argument(
        "--server", default=_UNSET, help="server origin URL (wss/https; ws is localhost-only)"
    )
    connect.add_argument(
        "--ollama",
        default=_UNSET,
        help="local Ollama base URL (localhost only unless --lan; default http://127.0.0.1:11434)",
    )
    connect.add_argument(
        "--model", default=_UNSET, help="local Ollama model tag (job frames stay authoritative)"
    )
    connect.add_argument(
        "--timeout",
        type=float,
        default=_UNSET,
        help="connect/check timeout in seconds",
    )
    connect.add_argument(
        "--token-file",
        type=Path,
        default=_UNSET,
        metavar="PATH",
        help=(
            "persist the bridge session token to this file so reconnect works "
            "across CLI restarts. The file is created with the strongest "
            "practical permissions (0600 on POSIX; an owner-only ACL is "
            "attempted on Windows) and binds the token to the server origin it "
            "was issued for. Defaults to the secure token file next to "
            "bridge.toml."
        ),
    )
    connect.add_argument(
        "--memory-only",
        action="store_true",
        help=(
            "keep the session token in memory only (never writes the token "
            "file; if the CLI restarts you must re-pair)."
        ),
    )
    connect.add_argument(
        "--lan",
        action="store_true",
        default=_UNSET,
        help="EXPLICITLY allow a non-loopback Ollama endpoint (advanced; prints a warning)",
    )
    connect.add_argument("--debug", action="store_true", default=_UNSET, help="verbose bridge logging")
    connect.add_argument(
        "--max-retries",
        type=int,
        default=_UNSET,
        help="max reconnect attempts after a link drop (0 disables reconnect)",
    )
    connect.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="use this bridge.toml instead of the platform default",
    )

    list_models = sub.add_parser("list-models", help="list local Ollama model tags and exit")
    list_models.add_argument(
        "--ollama",
        default=_UNSET,
        help="local Ollama base URL (default: resolved via CLI/env/config/localhost)",
    )
    list_models.add_argument(
        "--timeout",
        type=float,
        default=_UNSET,
        help="connect/check timeout in seconds",
    )
    list_models.add_argument(
        "--lan",
        action="store_true",
        default=_UNSET,
        help="EXPLICITLY allow a non-loopback Ollama endpoint",
    )
    list_models.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="use this bridge.toml instead of the platform default",
    )

    config_cmd = sub.add_parser(
        "config", help="show the effective bridge configuration (safe output)"
    )
    config_cmd.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="use this bridge.toml instead of the platform default",
    )
    return parser


def _configure_logging(debug: bool) -> None:
    LOGGER.setLevel(logging.DEBUG if debug else logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)


def _warn_lan() -> None:
    print(
        "WARNING: --lan allows a NON-LOCAL Ollama endpoint. This weakens the "
        "Phase 22 local-only guarantee and sends prompts to the LAN host you "
        "configured. You have explicitly opted in."
    )


# --------------------------------------------------------------------------- #
# resolution helpers (precedence: CLI >= env >= bridge.toml >= default)
# --------------------------------------------------------------------------- #


def _cli_value(args: Any, name: str) -> Any:
    value = getattr(args, name, None)
    if value is _UNSET:
        return None
    return value


def _cli_overrides(args: Any) -> Mapping[str, Any]:
    """Map parsed CLI args into the precedence input mapping (None = absent).

    The explicit ``--memory-only`` flag is CLI-level and wins over every other
    token-file source (Phase 27 §4 CLI supersedes).
    """
    return {
        "server": _cli_value(args, "server"),
        "ollama": _cli_value(args, "ollama"),
        "model": _cli_value(args, "model"),
        "timeout": _cli_value(args, "timeout"),
        "max_retries": _cli_value(args, "max_retries"),
        "lan": _cli_value(args, "lan"),
        "debug": _cli_value(args, "debug"),
        "token_file": _cli_value(args, "token_file"),
        "memory_only": bool(getattr(args, "memory_only", False)),
    }


def _resolve_toml_path(args: Any) -> Optional[Path]:
    explicit = getattr(args, "config", None)
    if explicit is not None:
        return Path(explicit)
    return resolve_config_path(env=os.environ)


def _resolve_token_file(args: Any, *, default_token_file: Optional[Path] = None) -> Optional[Path]:
    """Token-file path for one run, honouring ``--memory-only`` first."""
    if getattr(args, "memory_only", False):
        return None
    value = _cli_value(args, "token_file")
    if value is not None:
        return Path(str(value))
    return default_token_file


def _resolve_connect_plan(args: Any, env: Optional[Mapping[str, str]] = None) -> ConnectPlan:
    """Resolve config/token precedence and DECIDE pairing vs reconnect.

    Fail-closed logic (Phase 27 §7/§8/§9):

    - pairing code supplied                     -> pairing (code never persisted);
    - no code + valid server-bound stored token -> reconnect;
    - no code + token bound to a DIFFERENT origin -> clear re-pair instruction;
    - no code + no usable token                  -> clear fail-closed message.

    A ``BridgeCliError`` (clear operator message) is raised for every failure;
    no network is touched here.
    """
    env = os.environ if env is None else env
    cfg_path = _resolve_toml_path(args)
    try:
        file_cfg = read_config(cfg_path)
        opts = resolve_run_options(
            cli=_cli_overrides(args),
            env=env,
            file_config=file_cfg,
            default_token_file=default_token_path(cfg_path),
        )
    except BridgeConfigError as exc:
        raise BridgeCliError(str(exc)) from exc

    # The server is bound to this origin; a stored token is usable ONLY for the
    # exact same origin (Phase 27 §9 - critical security criterion).
    try:
        origin = server_origin(opts.server_url)
    except UrlValidationError as exc:
        raise BridgeCliError(str(exc)) from exc

    store = TokenStore(opts.token_file)
    pairing_code = getattr(args, "pairing_code", None)
    if pairing_code is not None:
        # Explicit pairing: the operator deliberately supplied a fresh code.
        # The stored token is NOT loaded (never replayed during an explicit
        # re-pair); a successful pairing overwrites the token file (Phase 27
        # §7/§14 — the code itself is never persisted).
        stored_token = None
    else:
        stored_token = store.load(server_origin=origin)
        if stored_token is None:
            record = store.peek_record()
            if record is not None:
                raise BridgeCliError(
                    f"the stored bridge session token is bound to {record.server_origin}, "
                    f"but the configured server is {origin}. A token is NEVER reused "
                    f"on a changed server - pair again with a new pairing code."
                )
            if store.has_file():
                raise BridgeCliError(
                    "the stored token cannot be used (missing or invalid server binding "
                    "metadata); supply a new pairing code to re-pair."
                )
            raise BridgeCliError(
                "no bridge session token is stored for this server. First-time use "
                "requires a pairing code: e.g. 'pd-ollama-bridge connect PD-X7K4-92QP'"
            )

    config = Config(
        pairing_code=pairing_code,
        server_url=opts.server_url,
        ollama_url=opts.ollama_url,
        model=opts.model,
        connect_timeout_seconds=opts.connect_timeout_seconds,
        lan=opts.lan,
        debug=opts.debug,
        token_file=opts.token_file,
        max_reconnect_attempts=opts.max_retries,
    )
    try:
        config = config.validate()
    except UrlValidationError as exc:
        raise BridgeCliError(str(exc)) from exc

    mode = "pairing" if pairing_code is not None else "reconnect"
    return ConnectPlan(
        mode=mode,
        config=config,
        token_store=store,
        origin=origin,
        config_path=cfg_path,
    )


async def _list_models(args: argparse.Namespace) -> int:
    try:
        cfg_path = _resolve_toml_path(args)
        file_cfg = read_config(cfg_path)
        opts = resolve_run_options(
            cli=_cli_overrides(args),
            env=os.environ,
            file_config=file_cfg,
            default_token_file=default_token_path(cfg_path),
        )
    except BridgeConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        ollama = OllamaClient(
            base_url=opts.ollama_url,
            model="",
            connect_timeout_seconds=opts.connect_timeout_seconds,
            allow_lan=opts.lan,
        )
    except UrlValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    LOGGER.info("bridge: resolved Ollama endpoint %s (list-models)", opts.ollama_url)
    ok, tags = await ollama.check_available()
    await ollama.aclose()
    if not ok:
        print(f"✗ Ollama not reachable at {opts.ollama_url}", file=sys.stderr)
        return 1
    for tag in tags:
        print(tag)
    return 0


def _configure(args: argparse.Namespace) -> int:
    """Write stable NON-SECRET settings to bridge.toml (the ONLY writer).

    Only explicitly supplied fields change; unspecified existing values stay
    intact (Phase 27 §6). The merged result is validated (a bad ``server`` or
    remote plain ``ws`` URL is refused, never silently written) and the write
    is crash-safe.
    """
    cfg_path = _resolve_toml_path(args)
    try:
        existing = read_config(cfg_path)
    except BridgeConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    supplied = FileConfig(
        server_url=str(args.server) if args.server is not _UNSET else None,
        ollama_url=str(args.ollama) if args.ollama is not _UNSET else None,
        timeout_seconds=float(args.timeout) if args.timeout is not _UNSET else None,
        max_retries=int(args.max_retries) if args.max_retries is not _UNSET else None,
        lan=bool(args.lan) if args.lan is not _UNSET else None,
        debug=bool(args.debug) if args.debug is not _UNSET else None,
        model=str(args.model) if args.model is not _UNSET else None,
        token_file=str(args.token_file) if args.token_file is not _UNSET else None,
    )
    if not supplied.as_mapping():
        print(
            "error: nothing to configure; pass at least one of --server/--ollama/"
            "--timeout/--max-retries/--model/--lan/--debug/--token-file",
            file=sys.stderr,
        )
        return 1

    merged = existing.merged_with(supplied)
    try:
        validate_file_config(merged)
    except UrlValidationError as exc:
        print(f"error: refusing to write {cfg_path}: {exc}", file=sys.stderr)
        return 1
    try:
        write_config(cfg_path, merged.as_mapping())
    except BridgeConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"✓ wrote {cfg_path}")
    return 0


def _config_display(args: argparse.Namespace) -> int:
    """Safe introspection: never prints the token or the pairing code."""
    cfg_path = _resolve_toml_path(args)
    try:
        file_cfg = read_config(cfg_path)
        opts = resolve_run_options(
            cli={},
            env=os.environ,
            file_config=file_cfg,
            default_token_file=default_token_path(cfg_path),
        )
        origin = server_origin(opts.server_url)
    except BridgeConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except UrlValidationError as exc:
        print(f"error: invalid server configuration: {exc}", file=sys.stderr)
        return 1

    store = TokenStore(opts.token_file)
    token_present = store.load(server_origin=origin) is not None

    print(f"Config file: {cfg_path if cfg_path is not None else '(no default config path)'}")
    if cfg_path is None or not Path(cfg_path).exists():
        print("Config file not found; showing effective defaults and environment.")
    print(f"Server: {opts.server_url}")
    print(f"Ollama: {opts.ollama_url}")
    print(f"Timeout: {format(opts.connect_timeout_seconds, 'g')}")
    print(f"Max retries: {opts.max_retries}")
    print(f"Model: {opts.model}")
    print(f"LAN: {'yes' if opts.lan else 'no'}")
    print(f"Debug: {'yes' if opts.debug else 'no'}")
    print(f"Token present: {'yes' if token_present else 'no'}")
    return 0


def _safe_connect_error_message(last_error: Any) -> str:
    """Map the classified terminal connect failure onto a SAFE CLI message
    (Phase 31CD §14): actionable, no cert chains, no tokens, no raw
    exception texts. Non-classified failures keep the legacy generic line."""
    if not isinstance(last_error, BridgeConnectionError):
        return "error: could not establish a bridge session"
    if last_error.reason_code == REASON_TLS_TRUST_STORE_UNAVAILABLE:
        return (
            "error: no usable CA trust store was found for secure WebSocket "
            "verification."
        )
    if last_error.reason_code == REASON_TLS_HOSTNAME_MISMATCH:
        return (
            "error: TLS certificate verification failed for the bridge server "
            "(the certificate does not match the server hostname)."
        )
    if last_error.reason_code in (
        REASON_TLS_CERTIFICATE_VERIFY_FAILED,
        REASON_TLS_CERTIFICATE_EXPIRED,
    ):
        return (
            "error: TLS certificate verification failed for the bridge server. "
            "The local Python trust store may be missing or outdated."
        )
    return "error: could not establish a bridge session"


async def _connect(args: argparse.Namespace) -> int:
    try:
        plan = _resolve_connect_plan(args)
    except BridgeCliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    config = plan.config
    if config.lan:
        _warn_lan()

    # Safe startup logging (Phase 27 §19) — never secrets.
    LOGGER.info(
        "bridge: config path %s",
        str(plan.config_path) if plan.config_path is not None else "(none)",
    )
    LOGGER.info("bridge: resolved server origin %s", plan.origin)
    LOGGER.info("bridge: resolved Ollama endpoint %s", config.normalized_ollama_url)
    LOGGER.info("bridge: token present %s", "yes" if plan.token_store.get() else "no")
    LOGGER.info("bridge: connection mode %s", plan.mode)

    print(BANNER)
    print()
    print("Checking Ollama...")
    ollama = OllamaClient(
        base_url=config.normalized_ollama_url,
        model=config.model,
        connect_timeout_seconds=config.connect_timeout_seconds,
        allow_lan=config.lan,
    )
    ok, tags = await ollama.check_available()
    if not ok:
        print(f"✗ Ollama not reachable at {config.normalized_ollama_url}", file=sys.stderr)
        await ollama.aclose()
        return 1
    print("✓ Ollama reachable")
    if config.model not in tags:
        print(f"✗ local model '{config.model}' not available", file=sys.stderr)
        if tags:
            shown = ", ".join(list(tags)[:12])
            print(f"  available models: {shown}", file=sys.stderr)
        await ollama.aclose()
        return 1
    print(f"✓ {config.model} available")

    print()
    print("Connecting...")

    def on_connected(kind: str, host: str) -> None:
        print(f"✓ Connected to {host}")
        if kind == "pairing":
            print("✓ Pairing successful")
        else:
            print("✓ Reconnected")
        print()
        print("Waiting for generation jobs...")
        sys.stdout.flush()

    def on_reconnecting(attempt: int, delay: float) -> None:
        print(f"... reconnecting in {delay:.1f}s (attempt {attempt})")
        sys.stdout.flush()

    bridge = BridgeClient(
        config=config,
        token_store=plan.token_store,
        ollama=ollama,
        on_connected=on_connected,
        on_reconnecting=on_reconnecting,
    )
    try:
        await bridge.run()
    finally:
        await ollama.aclose()
    if bridge.sessions_connected == 0:
        print(_safe_connect_error_message(bridge.last_error), file=sys.stderr)
        return 1
    return 0


def _configure_stdout_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _configure_stdout_utf8()
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "debug", None) is True)
    try:
        if args.command == "list-models":
            return asyncio.run(_list_models(args))
        if args.command == "configure":
            return _configure(args)
        if args.command == "config":
            return _config_display(args)
        return asyncio.run(_connect(args))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    except UrlValidationError as exc:
        # F1 — every malformed URL surface (CLI/env/bridge.toml) fails with the
        # canonical typed error; surface it as a CLEAN CLI message, never a
        # traceback (§10/§13 "malformed URL fails clearly").
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (httpx.InvalidURL, InvalidURI) as exc:
        # Defensive backstop: even if an invalid URL somehow reached the HTTP/
        # WS transport, fail clearly (exit 1, no traceback).
        print(f"error: invalid server or Ollama URL ({exc})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())