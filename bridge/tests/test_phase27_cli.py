"""Phase 27 CLI tests: configure / config / connect (pairing vs zero-argument
reconnect vs fail-closed) and the security surface (no token/code in config,
no token printed, changed server never reuses a token, list-models endpoint
resolution, legacy explicit CLI compatibility).

Hermetic: tmp config/token files only; the fail-closed and plan-level paths
touch no network; list-models endpoint capture replaces the Ollama client.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pd_ollama_bridge import cli as cli_mod
from pd_ollama_bridge.cli import BridgeCliError, _resolve_connect_plan, build_argument_parser, main
from pd_ollama_bridge.config import TokenStore

TEST_TOKEN = "BRIDGE_TOKEN_FOR_TESTING_000001"
TEST_ORIGIN = "wss://pd.example.com"

# Hermetic env (see conftest.py): every CLI path that resolves through the LIVE
# process environment (``_resolve_connect_plan`` / ``_list_models`` /
# ``_resolve_toml_path``) is protected by the autouse ``_scrub_live_bridge_env``
# fixture, so a stray host ``PD_BRIDGE_*`` / ``LOCALAPPDATA`` / ``USERPROFILE`` /
# ``XDG_CONFIG_HOME`` / ``HOME`` cannot perturb results.


def _cfg(tmp_path, content: str = "") -> Path:
    cfg = tmp_path / "bridge.toml"
    if content:
        cfg.write_text(content, encoding="utf-8")
    return cfg


# --------------------------------------------------------------------------- #
# configure (§6 / tests 18, 19) — writes only supplied fields
# --------------------------------------------------------------------------- #


def test_configure_writes_requested_fields(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert main(["configure", "--server", "wss://pd.example.com", "--config", str(cfg)]) == 0
    assert cfg.exists()
    text = cfg.read_text(encoding="utf-8")
    assert 'server = "wss://pd.example.com"' in text


def test_configure_preserves_unspecified_fields(tmp_path):
    cfg = _cfg(tmp_path)
    assert main(["configure", "--server", "wss://pd.example.com", "--timeout", "120", "--config", str(cfg)]) == 0
    assert main(["configure", "--ollama", "http://127.0.0.1:11434", "--config", str(cfg)]) == 0
    text = cfg.read_text(encoding="utf-8")
    assert 'server = "wss://pd.example.com"' in text
    assert 'ollama = "http://127.0.0.1:11434"' in text
    assert "timeout = 120.0" in text


def test_configure_nothing_to_do_fails(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert main(["configure", "--config", str(cfg)]) == 1
    assert not cfg.exists()
    assert "nothing to configure" in capsys.readouterr().err


def test_configure_can_turn_lan_off(tmp_path):
    cfg = _cfg(tmp_path, "lan = true\n")
    assert main(["configure", "--no-lan", "--config", str(cfg)]) == 0
    assert "lan = false" in cfg.read_text(encoding="utf-8")


def test_configure_rejects_remote_ws(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert main(["configure", "--server", "ws://detective.example.com", "--config", str(cfg)]) == 1
    assert not cfg.exists()
    assert "ws://" in capsys.readouterr().err


def test_configure_rejects_malformed_server_url(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert main(["configure", "--server", "javascript:alert(1)", "--config", str(cfg)]) == 1
    assert not cfg.exists()
    assert "server" in capsys.readouterr().err


def test_configure_rejects_remote_ollama_without_lan(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert (
        main(
            [
                "configure",
                "--ollama",
                "http://192.168.1.10:11434",
                "--config",
                str(cfg),
            ]
        )
        == 1
    )
    assert not cfg.exists()
    assert "localhost" in capsys.readouterr().err


def test_configure_accepts_remote_wss(tmp_path):
    cfg = _cfg(tmp_path)
    assert main(["configure", "--server", "wss://pd.example.com", "--config", str(cfg)]) == 0
    assert 'server = "wss://pd.example.com"' in cfg.read_text(encoding="utf-8")


def test_configure_refuses_when_existing_config_malformed(tmp_path, capsys):
    cfg = _cfg(tmp_path, "server = 'broken")
    assert main(["configure", "--ollama", "http://127.0.0.1:11434", "--config", str(cfg)]) == 1
    assert "malformed" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# config display (§6 / test 20) — never reveals the token
# --------------------------------------------------------------------------- #


def test_config_display_safe_output_with_token(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert (
        main(
            [
                "configure",
                "--server",
                "wss://pd.example.com",
                "--ollama",
                "http://127.0.0.1:11434",
                "--timeout",
                "120",
                "--max-retries",
                "10",
                "--config",
                str(cfg),
            ]
        )
        == 0
    )
    TokenStore(tmp_path / "bridge_token").save(TEST_TOKEN, server_origin=TEST_ORIGIN)
    assert main(["config", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert f"Config file: {cfg}" in out
    assert "Server: wss://pd.example.com" in out
    assert "Ollama: http://127.0.0.1:11434" in out
    assert "Timeout: 120" in out
    assert "Max retries: 10" in out
    assert "Token present: yes" in out
    assert TEST_TOKEN not in out
    assert "BRIDGE_TOKEN" not in out


def test_config_display_token_absent(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert main(["config", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "Token present: no" in out


def test_config_display_malformed_toml_fails(tmp_path, capsys):
    cfg = _cfg(tmp_path, "server = 'broken")
    assert main(["config", "--config", str(cfg)]) == 1
    assert "malformed" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# connect — zero-argument reconnect + fail-closed (tests 11-16)
# --------------------------------------------------------------------------- #

_PLAN_SERVER = "ws://127.0.0.1:1234"


def test_connect_with_paired_token_reconnect(tmp_path):
    token_file = tmp_path / "bridge_token"
    TokenStore(token_file).save(TEST_TOKEN, server_origin=_PLAN_SERVER)
    args = build_argument_parser().parse_args(
        ["connect", "--server", _PLAN_SERVER, "--token-file", str(token_file)]
    )
    plan = _resolve_connect_plan(args)
    assert plan.mode == "reconnect"
    assert plan.config.pairing_code is None
    assert plan.token_store.get() == TEST_TOKEN
    assert plan.origin == _PLAN_SERVER


def test_connect_possible_on_first_pairing_with_code(tmp_path):
    args = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--server", _PLAN_SERVER, "--memory-only"]
    )
    plan = _resolve_connect_plan(args)
    assert plan.mode == "pairing"
    assert plan.config.pairing_code == "PD-A2B3-C4D5"


def test_connect_without_token_fails_closed(capsys, tmp_path):
    cfg = _cfg(tmp_path)
    rc = main(
        [
            "connect",
            "--server",
            _PLAN_SERVER,
            "--config",
            str(cfg),
            "--memory-only",
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "pairing code" in err


def test_connect_changed_server_never_reuses_old_token(tmp_path):
    token_file = tmp_path / "bridge_token"
    TokenStore(token_file).save(TEST_TOKEN, server_origin="wss://old-server.example")
    args = build_argument_parser().parse_args(
        ["connect", "--server", "wss://new-server.example", "--token-file", str(token_file)]
    )
    with pytest.raises(BridgeCliError) as exc:
        _resolve_connect_plan(args)
    assert "bound to wss://old-server.example" in str(exc.value)
    assert "NEVER reused" in str(exc.value)


def test_connect_changed_server_fail_message_does_not_reveal_token(capsys, tmp_path):
    token_file = tmp_path / "bridge_token"
    TokenStore(token_file).save(TEST_TOKEN, server_origin="wss://old-server.example")
    rc = main(["connect", "--server", "wss://new-server.example", "--token-file", str(token_file)])
    assert rc == 1
    err = capsys.readouterr().err
    assert TEST_TOKEN not in err


def test_connect_explicit_pairing_code_does_not_replay_old_token(tmp_path):
    token_file = tmp_path / "bridge_token"
    TokenStore(token_file).save(TEST_TOKEN, server_origin="wss://old-server.example")
    args = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--server", "wss://new-server.example", "--token-file", str(token_file)]
    )
    plan = _resolve_connect_plan(args)
    assert plan.mode == "pairing"
    assert plan.token_store.get() is None, "an explicit re-pair must never replay the old token"


def test_connect_legacy_unbound_token_file_fails_closed(capsys, tmp_path):
    token_file = tmp_path / "legacy_token"
    token_file.write_text(TEST_TOKEN, encoding="utf-8")  # legacy bare file, no binding
    rc = main(["connect", "--server", _PLAN_SERVER, "--token-file", str(token_file), "--memory-only"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "pairing code" in err


def test_connect_never_writes_config(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    wrote = []

    def _forbidden(*_a, **_k):
        wrote.append(True)
        raise AssertionError("connect must NEVER call write_config")

    monkeypatch.setattr(cli_mod, "write_config", _forbidden)
    assert main(["connect", "--server", _PLAN_SERVER, "--config", str(cfg), "--memory-only"]) == 1
    assert wrote == []
    assert not cfg.exists()
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# 30. legacy explicit CLI compatibility (§11) — works with NO config file
# --------------------------------------------------------------------------- #


def test_legacy_explicit_cli_remains_compatible(tmp_path):
    token_file = tmp_path / "t"
    args = build_argument_parser().parse_args(
        [
            "connect",
            "PD-A2B3-C4D5",
            "--server",
            "http://127.0.0.1:9999",
            "--ollama",
            "http://127.0.0.1:11435",
            "--model",
            "hermes3:8b",
            "--timeout",
            "120",
            "--max-retries",
            "10",
            "--token-file",
            str(token_file),
            "--config",
            str(tmp_path / "bridge.toml"),
        ]
    )
    plan = _resolve_connect_plan(args)
    assert plan.mode == "pairing"
    assert plan.config.server_url == "http://127.0.0.1:9999"
    assert plan.config.ollama_url == "http://127.0.0.1:11435"
    assert plan.config.model == "hermes3:8b"
    assert plan.config.connect_timeout_seconds == 120.0
    assert plan.config.max_reconnect_attempts == 10
    assert plan.config.pairing_code == "PD-A2B3-C4D5"
    # No config file existed before this plan — nothing was auto-persisted.
    assert not (tmp_path / "bridge.toml").exists()


def test_cli_server_overrides_config_file(tmp_path):
    cfg = _cfg(tmp_path, 'server = "wss://cfg-owner.example"\n')
    args = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--server", "wss://cli-owner.example", "--config", str(cfg), "--memory-only"]
    )
    plan = _resolve_connect_plan(args)
    assert plan.config.server_url == "wss://cli-owner.example"


def test_config_file_server_used_without_cli_or_env(tmp_path):
    cfg = _cfg(tmp_path, 'server = "wss://cfg-owner.example"\n')
    args = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--config", str(cfg), "--memory-only"]
    )
    plan = _resolve_connect_plan(args)
    assert plan.config.server_url == "wss://cfg-owner.example"


def test_env_server_overrides_config_file(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, 'server = "wss://cfg-owner.example"\n')
    monkeypatch.setenv("PD_BRIDGE_SERVER", "wss://env-owner.example")
    args = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--config", str(cfg), "--memory-only"]
    )
    plan = _resolve_connect_plan(args)
    assert plan.config.server_url == "wss://env-owner.example"


# --------------------------------------------------------------------------- #
# 26. list-models endpoint resolution (CLI -> env -> config -> localhost)
# --------------------------------------------------------------------------- #


class _CapturingOllama:
    def __init__(self, *, capture, ok=False, **kwargs):
        capture["base_url"] = kwargs.get("base_url")
        capture["allow_lan"] = kwargs.get("allow_lan")
        self._ok = ok

    async def check_available(self):
        return self._ok, ()

    async def aclose(self):
        return ""


def _capture_list_models(monkeypatch, cfg: Path) -> dict:
    captured: dict = {}
    monkeypatch.setattr(
        cli_mod,
        "OllamaClient",
        lambda **kw: _CapturingOllama(capture=captured, **kw),
    )
    rc = main(["list-models", "--config", str(cfg)])
    return captured, rc


def test_list_models_no_config_uses_localhost_default(monkeypatch, tmp_path, capsys):
    cfg = _cfg(tmp_path)
    captured, rc = _capture_list_models(monkeypatch, cfg)
    assert captured["base_url"] == "http://127.0.0.1:11434"
    assert captured["allow_lan"] is False
    assert rc == 1
    capsys.readouterr()


def test_list_models_resolves_configured_endpoint(monkeypatch, tmp_path, capsys):
    cfg = _cfg(tmp_path, 'ollama = "http://127.0.0.1:11435"\n')
    captured, _rc = _capture_list_models(monkeypatch, cfg)
    assert captured["base_url"] == "http://127.0.0.1:11435"
    capsys.readouterr()


def test_list_models_cli_overrides_config(monkeypatch, tmp_path, capsys):
    cfg = _cfg(tmp_path, 'ollama = "http://127.0.0.1:11435"\n')
    captured = {}
    monkeypatch.setattr(
        cli_mod,
        "OllamaClient",
        lambda **kw: _CapturingOllama(capture=captured, **kw),
    )
    rc = main(
        ["list-models", "--ollama", "http://127.0.0.1:11436", "--config", str(cfg)]
    )
    assert captured["base_url"] == "http://127.0.0.1:11436"
    assert rc == 1
    capsys.readouterr()


def test_list_models_env_overrides_config(monkeypatch, tmp_path, capsys):
    cfg = _cfg(tmp_path, 'ollama = "http://127.0.0.1:11435"\n')
    monkeypatch.setenv("PD_BRIDGE_OLLAMA", "http://127.0.0.1:11437")
    captured, _rc = _capture_list_models(monkeypatch, cfg)
    assert captured["base_url"] == "http://127.0.0.1:11437"
    capsys.readouterr()


def test_list_models_rejects_remote_ollama(monkeypatch, tmp_path, capsys):
    cfg = _cfg(tmp_path, 'ollama = "http://evil.example.com:11434"\n')
    rc = main(["list-models", "--config", str(cfg)])
    assert rc == 1
    assert "localhost" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# §17 security pins at the CLI surface
# --------------------------------------------------------------------------- #


def test_pairing_code_never_persisted_anywhere(tmp_path):
    token_file = tmp_path / "bridge_token"
    cfg = _cfg(tmp_path)
    args = build_argument_parser().parse_args(
        ["connect", "PD-A2B3-C4D5", "--server", _PLAN_SERVER,
         "--token-file", str(token_file), "--config", str(cfg)]
    )
    plan = _resolve_connect_plan(args)
    assert plan.config.pairing_code == "PD-A2B3-C4D5"
    # The plan itself never wrote anything.
    assert not cfg.exists()
    assert not token_file.exists()


def test_token_never_written_to_bridge_toml(tmp_path):
    cfg = _cfg(tmp_path)
    TokenStore(tmp_path / "bridge_token").save(TEST_TOKEN, server_origin=TEST_ORIGIN)
    assert main(["configure", "--server", TEST_ORIGIN, "--config", str(cfg)]) == 0
    assert TEST_TOKEN not in cfg.read_text(encoding="utf-8")
    assert "BRIDGE_TOKEN_FOR_TESTING" not in cfg.read_text(encoding="utf-8")


def test_config_command_reports_token_present_but_never_contents(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert main(["configure", "--server", TEST_ORIGIN, "--config", str(cfg)]) == 0
    TokenStore(tmp_path / "bridge_token").save(TEST_TOKEN, server_origin=TEST_ORIGIN)
    assert main(["config", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "Token present: yes" in out
    assert TEST_TOKEN not in out


# --------------------------------------------------------------------------- #
# F1 — junk/control chars in the netloc/port fail CLEANLY (exit 1, no
# traceback) at every surface: configure (persist), TOML, env, list-models,
# connect. Loopback policy and the remote ws:// rejection are unchanged.
# --------------------------------------------------------------------------- #


def _assert_clean_failure(capsys) -> None:
    out, err = capsys.readouterr()
    assert "error:" in err
    assert "Traceback" not in out
    assert "Traceback" not in err


@pytest.mark.parametrize(
    "junk_ollama",
    [
        'http://127.0.0.1:11434"',
        "http://127.0.0.1:11434;--",
        "http://127.0.0.1:11434\nx=1",
        "http://127.0.0.1:11434x",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
    ],
)
def test_configure_rejects_junk_ollama(tmp_path, capsys, junk_ollama):
    cfg = _cfg(tmp_path)
    rc = main(["configure", "--ollama", junk_ollama, "--config", str(cfg)])
    assert rc == 1
    assert not cfg.exists(), "a rejected Ollama URL must never be persisted"
    _assert_clean_failure(capsys)


def test_configure_rejects_junk_server_url(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    rc = main(["configure", "--server", "wss://127.0.0.1:8000\nx", "--config", str(cfg)])
    assert rc == 1
    assert not cfg.exists()
    _assert_clean_failure(capsys)


@pytest.mark.parametrize(
    "toml_ollama",
    [
        'ollama = "http://127.0.0.1:11434;--"\n',
        'ollama = "http://127.0.0.1:11434\\nx=1"\n',
        'ollama = "http://127.0.0.1:0"\n',
    ],
)
def test_list_models_rejects_junk_ollama_from_toml(tmp_path, capsys, toml_ollama):
    cfg = _cfg(tmp_path, toml_ollama)
    rc = main(["list-models", "--config", str(cfg)])
    assert rc == 1
    _assert_clean_failure(capsys)


def test_configure_refuses_toml_with_junk_ollama(tmp_path, capsys):
    cfg = _cfg(tmp_path, 'ollama = "http://127.0.0.1:11434;--"\n')
    rc = main(["configure", "--model", "hermes3:8b", "--config", str(cfg)])
    assert rc == 1
    _assert_clean_failure(capsys)


def test_list_models_rejects_junk_ollama_from_env(monkeypatch, tmp_path, capsys):
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("PD_BRIDGE_OLLAMA", 'http://127.0.0.1:11434"')
    rc = main(["list-models", "--config", str(cfg)])
    assert rc == 1
    _assert_clean_failure(capsys)


def test_list_models_rejects_junk_ollama_cli(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    rc = main(["list-models", "--ollama", "http://127.0.0.1:11434;--", "--config", str(cfg)])
    assert rc == 1
    _assert_clean_failure(capsys)


def test_connect_rejects_junk_ollama_before_any_network(tmp_path, capsys):
    rc = main(
        [
            "connect",
            "PD-A2B3-C4D5",
            "--server",
            _PLAN_SERVER,
            "--ollama",
            'http://127.0.0.1:11434"',
            "--memory-only",
            "--config",
            str(_cfg(tmp_path)),
        ]
    )
    assert rc == 1
    _assert_clean_failure(capsys)


def test_connect_rejects_junk_server_url_before_any_network(tmp_path, capsys):
    rc = main(
        [
            "connect",
            "PD-A2B3-C4D5",
            "--server",
            "wss://127.0.0.1:8000\nx",
            "--memory-only",
            "--config",
            str(_cfg(tmp_path)),
        ]
    )
    assert rc == 1
    _assert_clean_failure(capsys)


def test_remote_ws_and_http_still_rejected_at_configure(tmp_path, capsys):
    """F1 must not loosen the remote plain-ws/http rejection."""
    cfg = _cfg(tmp_path)
    for hostile in ("ws://detective.example.com", "http://detective.example.com"):
        rc = main(["configure", "--server", hostile, "--config", str(cfg)])
        assert rc == 1
        assert not cfg.exists()
        _, err = capsys.readouterr()
        assert "Traceback" not in err


# --------------------------------------------------------------------------- #
# F2 — ``timeout = nan/inf`` (CLI, env or persisted TOML) must be rejected at
# every surface; positive finite values (including floats) stay accepted.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_timeout", ["nan", "inf"])
def test_configure_rejects_non_finite_timeout(tmp_path, capsys, bad_timeout):
    """F2 — ``configure --timeout nan|inf`` is rejected, never persisted.
    (``-inf`` is likewise rejected, by argparse itself as a usage error.)"""
    cfg = _cfg(tmp_path)
    rc = main(["configure", "--timeout", bad_timeout, "--config", str(cfg)])
    assert rc == 1
    assert not cfg.exists(), "a non-finite timeout must never be persisted"
    _assert_clean_failure(capsys)


def test_configure_accepts_positive_finite_float_timeout(tmp_path):
    cfg = _cfg(tmp_path)
    assert main(["configure", "--timeout", "0.5", "--config", str(cfg)]) == 0
    assert "timeout = 0.5" in cfg.read_text(encoding="utf-8")


def test_env_nan_timeout_rejected(monkeypatch, tmp_path, capsys):
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("PD_BRIDGE_TIMEOUT", "nan")
    assert main(["list-models", "--config", str(cfg)]) == 1
    _assert_clean_failure(capsys)
    capsys.readouterr()


def test_env_inf_timeout_rejected(monkeypatch, tmp_path, capsys):
    cfg = _cfg(tmp_path)
    monkeypatch.setenv("PD_BRIDGE_TIMEOUT", "inf")
    assert main(["config", "--config", str(cfg)]) == 1
    _assert_clean_failure(capsys)
    capsys.readouterr()


def test_toml_nan_timeout_rejected(tmp_path, capsys):
    cfg = _cfg(tmp_path, "timeout = nan\n")
    assert main(["config", "--config", str(cfg)]) == 1
    _assert_clean_failure(capsys)
    capsys.readouterr()


def test_toml_inf_timeout_rejected(tmp_path, capsys):
    cfg = _cfg(tmp_path, "timeout = inf\n")
    assert main(["list-models", "--config", str(cfg)]) == 1
    _assert_clean_failure(capsys)
    capsys.readouterr()