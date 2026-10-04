"""CLI-level smoke tests that require no network (fail fast on bad input)."""

import os

from pd_ollama_bridge.cli import _resolve_token_file, build_argument_parser, main

TEST_TOKEN = "BRIDGE_TOKEN_FOR_TESTING_000001"
TOKEN_ORIGIN = "wss://detective.example.com"


def test_help_exits_zero(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_connect_bad_pairing_code_exits_one(capsys):
    assert main(["connect", "not-a-code", "--memory-only"]) == 1
    err = capsys.readouterr().err
    assert "pairing code" in err


def test_connect_remote_http_server_rejected(capsys):
    assert (
        main(
            [
                "connect",
                "PD-A2B3-C4D5",
                "--server",
                "http://detective.example.com",
                "--memory-only",
            ]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "ws://" in err or "server" in err


def test_connect_remote_ollama_rejected(capsys):
    assert (
        main(
            [
                "connect",
                "PD-A2B3-C4D5",
                "--server",
                "https://detective.example.com",
                "--ollama",
                "http://evil.example.com:11434",
                "--memory-only",
            ]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "localhost" in err


def test_list_models_bad_url(capsys):
    assert main(["list-models", "--ollama", "ftp://127.0.0.1:11434"]) == 1


def test_default_pairing_uses_resolved_token_path(tmp_path):
    """Phase 27 §7/§8 — the default (no --memory-only, no --token-file) now
    resolves to the secure token file next to bridge.toml so zero-argument
    reconnects work after pairing without re-entering a code."""
    default = tmp_path / "bridge_token"
    args = build_argument_parser().parse_args(["connect", "PD-A2B3-C4D5"])
    assert args.pairing_code == "PD-A2B3-C4D5"
    assert not args.memory_only
    assert _resolve_token_file(args, default_token_file=default) == default
    assert _resolve_token_file(args, default_token_file=None) is None


def test_memory_only_flag_disables_token_persistence(tmp_path):
    args = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--memory-only"]
    )
    assert _resolve_token_file(args, default_token_file=tmp_path / "bridge_token") is None


def test_token_file_persists_only_with_explicit_flag(tmp_path):
    from pd_ollama_bridge.config import TokenStore

    target = tmp_path / "session_token"
    without = build_argument_parser().parse_args(["connect", "PD-A2B3-C4D5"])
    assert _resolve_token_file(without, default_token_file=None) is None
    assert not target.exists()

    with_flag = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--token-file", str(target)]
    )
    assert _resolve_token_file(with_flag, default_token_file=None) == target
    store = TokenStore(_resolve_token_file(with_flag, default_token_file=None))
    store.save(TEST_TOKEN, server_origin=TOKEN_ORIGIN)
    assert target.exists()
    assert TEST_TOKEN in target.read_text(encoding="utf-8")
    assert TokenStore(target).load(server_origin=TOKEN_ORIGIN) == TEST_TOKEN


def test_memory_only_wins_over_token_file(tmp_path):
    args = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--token-file", str(tmp_path / "t"), "--memory-only"]
    )
    assert _resolve_token_file(args, default_token_file=None) is None


def test_os_open_default_path_not_required_for_memory_only(tmp_path, monkeypatch):
    """A --memory-only invocation never touches the token file (no os.open)."""
    def _no_disk(*_args, **_kwargs):
        raise AssertionError("a --memory-only invocation must not write a token file")

    monkeypatch.setattr(os, "open", _no_disk)
    args = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--memory-only"]
    )
    assert _resolve_token_file(args, default_token_file=tmp_path / "bridge_token") is None