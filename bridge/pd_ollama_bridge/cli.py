"""Command-line entry point for the Phase 22 bridge client (``pd-ollama-bridge``).

Subcommands:

- ``connect <PAIRING_CODE>`` — the long-running bridge session (pairing or
  reconnect), with the Phase22(19) section-13 human progress flow.
- ``list-models`` — operator tool that prints the local Ollama model tags
  (the LIST_LOCAL_MODELS path; the bridge always selects the model locally).

The bridge OWNS its server/Ollama URLs and model locally; the remote server
can never change them through the protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import protocol
from .bridge_client import BridgeClient
from .config import Config, TokenStore
from .ollama_client import OllamaClient
from .urls import UrlValidationError

LOGGER = logging.getLogger("pd-ollama-bridge")

BANNER = "Procedural Detective Local AI Bridge"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pd-ollama-bridge",
        description=(
            "Procedural Detective Local AI Bridge. Connects your local Ollama "
            "to a Procedural Detective server. Your Ollama stays localhost-only."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    connect = sub.add_parser("connect", help="connect to the Procedural Detective server")
    connect.add_argument("pairing_code", help="pairing code, e.g. PD-X7K4-92QP")
    connect.add_argument(
        "--server", default=protocol.DEFAULT_SERVER_URL, help="server origin URL"
    )
    connect.add_argument(
        "--ollama",
        default=protocol.DEFAULT_OLLAMA_URL,
        help="local Ollama base URL (localhost only unless --lan)",
    )
    connect.add_argument(
        "--model", default=protocol.DEFAULT_MODEL, help="local Ollama model tag"
    )
    connect.add_argument(
        "--timeout",
        type=float,
        default=protocol.DEFAULT_CONNECT_TIMEOUT_SECONDS,
        help="connect/check timeout in seconds",
    )
    connect.add_argument(
        "--token-file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "EXPLICIT opt-in: persist the bridge session token to this file "
            "so reconnect works across CLI restarts. The file is created with "
            "the strongest practical permissions (0600 on POSIX; an owner-only "
            "ACL is attempted on Windows). Without this flag the token is "
            "memory-only: it never touches disk and reconnect survives only "
            "while this process keeps running."
        ),
    )
    connect.add_argument(
        "--memory-only",
        action="store_true",
        help=(
            "keep the session token in memory only (this is the default). "
            "Never writes a token file; if the CLI restarts you must re-pair."
        ),
    )
    connect.add_argument(
        "--lan",
        action="store_true",
        help="EXPLICITLY allow a non-loopback Ollama endpoint (advanced; prints a warning)",
    )
    connect.add_argument("--debug", action="store_true", help="verbose bridge logging")
    connect.add_argument(
        "--max-retries",
        type=int,
        default=12,
        help="max reconnect attempts after a link drop (0 disables reconnect)",
    )

    list_models = sub.add_parser("list-models", help="list local Ollama model tags and exit")
    list_models.add_argument(
        "--ollama", default=protocol.DEFAULT_OLLAMA_URL, help="local Ollama base URL"
    )
    list_models.add_argument(
        "--timeout",
        type=float,
        default=protocol.DEFAULT_CONNECT_TIMEOUT_SECONDS,
        help="connect/check timeout in seconds",
    )
    list_models.add_argument(
        "--lan", action="store_true", help="EXPLICITLY allow a non-loopback Ollama endpoint"
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


async def _list_models(args: argparse.Namespace) -> int:
    try:
        ollama = OllamaClient(
            base_url=args.ollama,
            model="",
            connect_timeout_seconds=float(args.timeout),
            allow_lan=bool(args.lan),
        )
    except UrlValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    ok, tags = await ollama.check_available()
    await ollama.aclose()
    if not ok:
        print(f"✗ Ollama not reachable at {args.ollama}", file=sys.stderr)
        return 1
    for tag in tags:
        print(tag)
    return 0


def _resolve_token_file(args: argparse.Namespace) -> Optional[Path]:
    """Session-token file path, or ``None`` for the default memory-only mode.

    A token file is used ONLY when the operator explicitly passes
    ``--token-file PATH``; ``--memory-only`` (now the default) also wins over
    an accidentally combined ``--token-file``.
    """
    if args.memory_only:
        return None
    return args.token_file


async def _connect(args: argparse.Namespace) -> int:
    token_file: Optional[Path] = _resolve_token_file(args)
    store = TokenStore(token_file)
    stored_token = store.load()

    config = Config(
        pairing_code=None if stored_token else args.pairing_code,
        server_url=args.server,
        ollama_url=args.ollama,
        model=args.model,
        connect_timeout_seconds=float(args.timeout),
        lan=bool(args.lan),
        debug=bool(args.debug),
        token_file=token_file,
        max_reconnect_attempts=int(args.max_retries),
    )
    try:
        config = config.validate()
    except UrlValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if config.lan:
        _warn_lan()

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
        token_store=store,
        ollama=ollama,
        on_connected=on_connected,
        on_reconnecting=on_reconnecting,
    )
    try:
        await bridge.run()
    finally:
        await ollama.aclose()
    if bridge.sessions_connected == 0:
        print("error: could not establish a bridge session", file=sys.stderr)
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
    _configure_logging(bool(getattr(args, "debug", False)))
    try:
        if args.command == "list-models":
            return asyncio.run(_list_models(args))
        return asyncio.run(_connect(args))
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())