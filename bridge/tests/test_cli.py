"""CLI-level smoke tests that require no network (fail fast on bad input)."""

import os

from pd_ollama_bridge.cli import _resolve_token_file, build_argument_parser, main
from pd_ollama_bridge.config import TokenStore

TEST_TOKEN = "BRIDGE_TOKEN_FOR_TESTING_000001"


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


def test_default_invocation_requires_explicit_token_file(tmp_path, monkeypatch):
    args = build_argument_parser().parse_args(["connect", "PD-A2B3-C4D5"])
    assert args.token_file is None
    assert not args.memory_only
    assert _resolve_token_file(args) is None

    store = TokenStore(_resolve_token_file(args))
    store.save(TEST_TOKEN)
    assert store.get() == TEST_TOKEN
    assert store.path is None
    assert store.load() is None

    def _no_disk(*_args, **_kwargs):
        raise AssertionError("a default (no --token-file) invocation must not write a token file")

    monkeypatch.setattr(os, "open", _no_disk)
    store.save(TEST_TOKEN)
    assert store.get() == TEST_TOKEN


def test_token_file_persists_only_with_explicit_flag(tmp_path):
    target = tmp_path / "session_token"
    without = build_argument_parser().parse_args(["connect", "PD-A2B3-C4D5"])
    assert _resolve_token_file(without) is None
    assert not target.exists()

    with_flag = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--token-file", str(target)]
    )
    assert _resolve_token_file(with_flag) == target
    store = TokenStore(_resolve_token_file(with_flag))
    store.save(TEST_TOKEN)
    assert target.exists()
    assert target.read_text(encoding="utf-8") == TEST_TOKEN
    assert TokenStore(target).load() == TEST_TOKEN


def test_memory_only_wins_over_token_file(tmp_path):
    args = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--token-file", str(tmp_path / "t"), "--memory-only"]
    )
    assert _resolve_token_file(args) is None