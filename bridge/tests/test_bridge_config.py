"""Hermetic tests for persistent Bridge config (bridge.toml): platform paths,
TOML load/write, crash-safe writes, malformed-Toml failures, and the exact
CLI >= env >= bridge.toml >= default precedence chain (Phase 27 §1-§5, §13).

Everything uses INJECTED env/platform values — the live OS environment is
never consulted, so Windows and Linux path resolution is fully deterministic
on any host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pd_ollama_bridge import protocol
from pd_ollama_bridge.bridge_config import (
    BridgeConfigError,
    FileConfig,
    default_token_path,
    read_config,
    render_toml,
    resolve_config_path,
    resolve_run_options,
    validate_file_config,
    write_config,
)
from pd_ollama_bridge.urls import UrlValidationError

TOKEN = "BRIDGE_TOKEN_FOR_TESTING_000001"


# --------------------------------------------------------------------------- #
# 1-3. default behavior + platform config paths
# --------------------------------------------------------------------------- #


def test_no_config_safe_defaults():
    opts = resolve_run_options(cli={}, env={}, file_config=None, default_token_file=None)
    assert opts.server_url == protocol.DEFAULT_SERVER_URL
    assert opts.ollama_url == protocol.DEFAULT_OLLAMA_URL
    assert opts.model == protocol.DEFAULT_MODEL
    assert opts.connect_timeout_seconds == protocol.DEFAULT_CONNECT_TIMEOUT_SECONDS
    assert opts.max_retries == 12
    assert opts.lan is False
    assert opts.debug is False
    assert opts.token_file is None


def test_windows_config_path_localappdata(tmp_path):
    path = resolve_config_path(
        env={"LOCALAPPDATA": str(tmp_path / "AppData" / "Local")}, platform="nt"
    )
    assert path == tmp_path / "AppData" / "Local" / "ProceduralDetective" / "bridge.toml"


def test_windows_config_path_fallback_userprofile(tmp_path):
    path = resolve_config_path(
        env={"USERPROFILE": str(tmp_path / "Users" / "bob")}, platform="nt"
    )
    assert path == tmp_path / "Users" / "bob" / ".procedural-detective" / "bridge.toml"


def test_windows_config_path_prefers_localappdata_over_userprofile(tmp_path):
    path = resolve_config_path(
        env={
            "LOCALAPPDATA": str(tmp_path / "la"),
            "USERPROFILE": str(tmp_path / "up"),
        },
        platform="nt",
    )
    assert path == tmp_path / "la" / "ProceduralDetective" / "bridge.toml"


def test_windows_config_path_no_base_is_none():
    assert resolve_config_path(env={}, platform="nt") is None


def test_linux_xdg_config_path(tmp_path):
    path = resolve_config_path(
        env={"XDG_CONFIG_HOME": str(tmp_path / ".config")}, platform="posix"
    )
    assert path == tmp_path / ".config" / "procedural-detective" / "bridge.toml"


def test_linux_home_config_path(tmp_path):
    path = resolve_config_path(
        env={"HOME": str(tmp_path / "home")}, platform="posix"
    )
    assert path == tmp_path / "home" / ".config" / "procedural-detective" / "bridge.toml"


def test_linux_config_path_prefers_xdg_over_home(tmp_path):
    path = resolve_config_path(
        env={"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "xdg")},
        platform="posix",
    )
    assert path == tmp_path / "xdg" / "procedural-detective" / "bridge.toml"


def test_default_token_path_is_sibling_of_config(tmp_path):
    cfg = tmp_path / "ProceduralDetective" / "bridge.toml"
    assert default_token_path(cfg) == tmp_path / "ProceduralDetective" / "bridge_token"
    assert default_token_path(None) is None


# --------------------------------------------------------------------------- #
# 4-5. TOML load + malformed TOML
# --------------------------------------------------------------------------- #


def test_toml_load_round_trip(tmp_path):
    cfg = tmp_path / "bridge.toml"
    write_config(
        cfg,
        {
            "server": "wss://pd.example.com",
            "ollama": "http://127.0.0.1:11434",
            "timeout": 120.0,
            "max_retries": 10,
            "lan": False,
            "debug": True,
            "model": "hermes3:8b",
            "token_file": "C:\\tokens\\bridge_token",
        },
    )
    loaded = read_config(cfg)
    assert loaded.server_url == "wss://pd.example.com"
    assert loaded.ollama_url == "http://127.0.0.1:11434"
    assert loaded.timeout_seconds == 120.0
    assert loaded.max_retries == 10
    assert loaded.lan is False
    assert loaded.debug is True
    assert loaded.model == "hermes3:8b"
    assert loaded.token_file == "C:\\tokens\\bridge_token"


def test_missing_config_file_is_empty():
    assert read_config(Path("definitely/missing/bridge.toml")) == FileConfig()
    assert read_config(None) == FileConfig()


def test_malformed_toml_fails_clearly(tmp_path):
    cfg = tmp_path / "bridge.toml"
    cfg.write_text("server = 'unclosed string", encoding="utf-8")
    with pytest.raises(BridgeConfigError) as exc:
        read_config(cfg)
    assert "malformed" in str(exc.value)
    assert "bridge.toml" in str(exc.value)


def test_unknown_toml_key_fails(tmp_path):
    cfg = tmp_path / "bridge.toml"
    cfg.write_text('serer = "wss://typo.example"', encoding="utf-8")
    with pytest.raises(BridgeConfigError) as exc:
        read_config(cfg)
    assert "unknown field" in str(exc.value)
    assert "serer" in str(exc.value)


def test_wrong_type_toml_field_fails(tmp_path):
    cfg = tmp_path / "bridge.toml"
    cfg.write_text("server = 123", encoding="utf-8")
    with pytest.raises(BridgeConfigError):
        read_config(cfg)


def test_empty_security_sensitive_field_fails(tmp_path):
    cfg = tmp_path / "bridge.toml"
    cfg.write_text('server = ""', encoding="utf-8")
    with pytest.raises(BridgeConfigError):
        read_config(cfg)


def test_bool_typed_timeout_is_rejected(tmp_path):
    cfg = tmp_path / "bridge.toml"
    cfg.write_text("timeout = true", encoding="utf-8")
    with pytest.raises(BridgeConfigError):
        read_config(cfg)


def test_invalid_serialized_config_field_never_silently_ignored(tmp_path):
    """A hand-edited config with a bad security-sensitive 'server' is surfaced
    by validate_file_config (used by configure) — never silently ignored."""
    cfg = FileConfig(server_url="ws://remote-host.example")
    with pytest.raises(UrlValidationError):
        validate_file_config(cfg)


# --------------------------------------------------------------------------- #
# 6-10, 27-29. precedence chain
# --------------------------------------------------------------------------- #


def _file_cfg(**kw) -> FileConfig:
    kw.setdefault("server_url", "wss://config-server.example")
    kw.setdefault("ollama_url", "http://127.0.0.1:11435")
    kw.setdefault("timeout_seconds", 99.0)
    kw.setdefault("max_retries", 7)
    kw.setdefault("lan", True)
    kw.setdefault("debug", True)
    kw.setdefault("model", "config-model:1b")
    kw.setdefault("token_file", "C:\\config\\token")
    return FileConfig(**kw)


def test_cli_server_overrides_config():
    opts = resolve_run_options(
        cli={"server": "wss://cli-server.example"},
        env={},
        file_config=_file_cfg(),
    )
    assert opts.server_url == "wss://cli-server.example"


def test_env_server_overrides_config():
    opts = resolve_run_options(
        cli={}, env={"PD_BRIDGE_SERVER": "wss://env-server.example"}, file_config=_file_cfg()
    )
    assert opts.server_url == "wss://env-server.example"


def test_config_server_used_when_cli_and_env_absent():
    opts = resolve_run_options(cli={}, env={}, file_config=_file_cfg())
    assert opts.server_url == "wss://config-server.example"


def test_default_server_when_everything_absent():
    opts = resolve_run_options(cli={}, env={}, file_config=None)
    assert opts.server_url == protocol.DEFAULT_SERVER_URL


def test_cli_ollama_overrides_config():
    opts = resolve_run_options(
        cli={"ollama": "http://127.0.0.1:11436"}, env={}, file_config=_file_cfg()
    )
    assert opts.ollama_url == "http://127.0.0.1:11436"


def test_env_ollama_overrides_config():
    opts = resolve_run_options(
        cli={}, env={"PD_BRIDGE_OLLAMA": "http://127.0.0.1:11437"}, file_config=_file_cfg()
    )
    assert opts.ollama_url == "http://127.0.0.1:11437"


def test_default_ollama_localhost():
    opts = resolve_run_options(cli={}, env={}, file_config=None)
    assert opts.ollama_url == "http://127.0.0.1:11434"


def test_timeout_precedence_cli_env_config():
    assert (
        resolve_run_options(cli={"timeout": 1.5}, env={}, file_config=_file_cfg())
    ).connect_timeout_seconds == 1.5
    assert (
        resolve_run_options(
            cli={}, env={"PD_BRIDGE_TIMEOUT": "2.5"}, file_config=_file_cfg()
        )
    ).connect_timeout_seconds == 2.5
    assert (
        resolve_run_options(cli={}, env={}, file_config=_file_cfg())
    ).connect_timeout_seconds == 99.0
    assert (
        resolve_run_options(cli={}, env={}, file_config=None)
    ).connect_timeout_seconds == protocol.DEFAULT_CONNECT_TIMEOUT_SECONDS


def test_max_retries_precedence_including_zero():
    assert (
        resolve_run_options(cli={"max_retries": 0}, env={}, file_config=_file_cfg())
    ).max_retries == 0
    assert (
        resolve_run_options(
            cli={}, env={"PD_BRIDGE_MAX_RETRIES": "3"}, file_config=_file_cfg()
        )
    ).max_retries == 3
    assert (
        resolve_run_options(cli={}, env={}, file_config=_file_cfg())
    ).max_retries == 7
    assert (
        resolve_run_options(cli={}, env={}, file_config=None)
    ).max_retries == 12


def test_lan_debug_precedence():
    # CLI wins over env and config.
    assert (
        resolve_run_options(cli={"lan": False}, env={"PD_BRIDGE_LAN": "true"}, file_config=_file_cfg(lan=True))
    ).lan is False
    assert (
        resolve_run_options(cli={"debug": False}, env={"PD_BRIDGE_DEBUG": "true"}, file_config=_file_cfg(debug=True))
    ).debug is False
    # env wins over config.
    assert (
        resolve_run_options(cli={}, env={"PD_BRIDGE_LAN": "true"}, file_config=_file_cfg(lan=False))
    ).lan is True
    assert (
        resolve_run_options(cli={}, env={"PD_BRIDGE_DEBUG": "false"}, file_config=_file_cfg(debug=True))
    ).debug is False
    # config when cli/env absent.
    assert resolve_run_options(cli={}, env={}, file_config=_file_cfg()).lan is True
    assert resolve_run_options(cli={}, env={}, file_config=_file_cfg()).debug is True


def test_debug_config_false_survives():
    """A persisted explicit `lan = false` / `debug = false` must not be
    overridden by Python falsy checks."""
    assert resolve_run_options(cli={}, env={}, file_config=_file_cfg(lan=False, debug=False)).lan is False
    assert resolve_run_options(cli={}, env={}, file_config=_file_cfg(lan=False, debug=False)).debug is False


# --------------------------------------------------------------------------- #
# token-file precedence (Phase 27 §4 / §3 — path only, never contents)
# --------------------------------------------------------------------------- #


def test_token_file_precedence():
    default = Path("C:/default/token")
    # CLI --token-file wins over env/config/default.
    opts = resolve_run_options(
        cli={"token_file": "C:/cli/token"},
        env={"PD_BRIDGE_TOKEN_FILE": "C:/env/token"},
        file_config=_file_cfg(),
        default_token_file=default,
    )
    assert opts.token_file == Path("C:/cli/token")
    # env wins over config/default.
    opts = resolve_run_options(
        cli={},
        env={"PD_BRIDGE_TOKEN_FILE": "C:/env/token"},
        file_config=_file_cfg(),
        default_token_file=default,
    )
    assert opts.token_file == Path("C:/env/token")
    # config path wins over default.
    opts = resolve_run_options(cli={}, env={}, file_config=_file_cfg(), default_token_file=default)
    assert opts.token_file == Path("C:/config/token")
    # default when nothing else.
    opts = resolve_run_options(cli={}, env={}, file_config=None, default_token_file=default)
    assert opts.token_file == default


def test_memory_only_always_wins_for_token_file():
    opts = resolve_run_options(
        cli={"token_file": "C:/cli/token", "memory_only": True},
        env={"PD_BRIDGE_TOKEN_FILE": "C:/env/token"},
        file_config=_file_cfg(),
        default_token_file=Path("C:/default/token"),
    )
    assert opts.token_file is None


def test_unparseable_env_values_fail_clearly():
    with pytest.raises(BridgeConfigError) as exc:
        resolve_run_options(cli={}, env={"PD_BRIDGE_TIMEOUT": "abc"})
    assert "PD_BRIDGE_TIMEOUT" in str(exc.value)
    with pytest.raises(BridgeConfigError) as exc:
        resolve_run_options(cli={}, env={"PD_BRIDGE_MAX_RETRIES": "many"})
    assert "PD_BRIDGE_MAX_RETRIES" in str(exc.value)
    with pytest.raises(BridgeConfigError) as exc:
        resolve_run_options(cli={}, env={"PD_BRIDGE_LAN": "sure"})
    assert "PD_BRIDGE_LAN" in str(exc.value)


# --------------------------------------------------------------------------- #
# 21-24. writes + URL validation for persisted fields
# --------------------------------------------------------------------------- #


def test_atomic_write_leaves_no_temp_files(tmp_path):
    cfg = tmp_path / "bridge.toml"
    write_config(cfg, {"server": "wss://pd.example.com", "timeout": 120.0})
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert read_config(cfg).server_url == "wss://pd.example.com"


def test_atomic_write_creates_parent_dirs(tmp_path):
    cfg = tmp_path / "a" / "b" / "bridge.toml"
    write_config(cfg, {"lan": True})
    assert cfg.exists()


def test_render_and_reread_assorted_types(tmp_path):
    cfg = tmp_path / "bridge.toml"
    write_config(
        cfg,
        {"lan": True, "debug": False, "max_retries": 0, "timeout": 0.5},
    )
    loaded = read_config(cfg)
    assert loaded.lan is True
    assert loaded.debug is False
    assert loaded.max_retries == 0
    assert loaded.timeout_seconds == 0.5


def test_write_config_rejects_unknown_keys(tmp_path):
    with pytest.raises(BridgeConfigError):
        write_config(tmp_path / "bridge.toml", {"server": "wss://x.example", "pairing_code": "PD-A2B3-C4D5"})


def test_render_toml_never_contains_secret_like_fields():
    text = render_toml({"server": "wss://pd.example.com"})
    assert "token" not in text
    assert "pairing" not in text


def test_write_failure_is_clear_and_leaves_no_config(tmp_path, monkeypatch):
    cfg = tmp_path / "bridge.toml"
    real_replace = __import__("os").replace

    def _boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("os.replace", _boom)
    with pytest.raises(BridgeConfigError) as exc:
        write_config(cfg, {"server": "wss://pd.example.com"})
    assert "could not write bridge.toml" in str(exc.value)
    monkeypatch.setattr("os.replace", real_replace)
    assert not cfg.exists()


def test_malformed_server_url_rejected_for_persist():
    with pytest.raises(UrlValidationError):
        validate_file_config(FileConfig(server_url="javascript:alert(1)"))
    with pytest.raises(UrlValidationError):
        validate_file_config(FileConfig(server_url="not a url at all"))


def test_remote_ws_server_rejected_for_persist():
    with pytest.raises(UrlValidationError):
        validate_file_config(FileConfig(server_url="ws://detective.example.com"))


def test_remote_wss_server_accepted_for_persist():
    cfg = FileConfig(server_url="wss://pd.example.com")
    validate_file_config(cfg)  # must not raise


def test_persisted_ollama_must_be_local_unless_lan(tmp_path):
    validate_file_config(FileConfig(ollama_url="http://127.0.0.1:11434"))
    with pytest.raises(UrlValidationError):
        validate_file_config(FileConfig(ollama_url="http://192.168.1.10:11434"))
    # lan=true makes a LAN Ollama legal at configure time (still explicit).
    validate_file_config(FileConfig(ollama_url="http://192.168.1.10:11434", lan=True))


def test_persisted_timeout_and_retries_validated():
    with pytest.raises(UrlValidationError):
        validate_file_config(FileConfig(timeout_seconds=0))
    with pytest.raises(UrlValidationError):
        validate_file_config(FileConfig(max_retries=-1))


# --------------------------------------------------------------------------- #
# F1/F2 adversarial — junk netloc/port and non-finite timeouts fail cleanly
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "junk",
    [
        'http://127.0.0.1:11434"',
        "http://127.0.0.1:11434;--",
        "http://127.0.0.1:11434\nx=1",
        "http://127.0.0.1:11434\tx=1",
        "http://127.0.0.1:11434x",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
    ],
)
def test_persisted_junk_ollama_port_rejected(junk):
    """F1 — a junk netloc/port must be refused for persist with the canonical
    typed error (never silently written, never reaching httpx)."""
    with pytest.raises(UrlValidationError):
        validate_file_config(FileConfig(ollama_url=junk))


@pytest.mark.parametrize(
    "junk",
    [
        'ws://127.0.0.1:1"',
        "ws://127.0.0.1:1\nx=1",
        "ws://127.0.0.1:0",
    ],
)
def test_persisted_junk_server_url_rejected(junk):
    with pytest.raises(UrlValidationError):
        validate_file_config(FileConfig(server_url=junk))


def test_toml_timeout_nan_rejected(tmp_path):
    """F2 — a persisted ``timeout = nan`` fails clearly at read time."""
    cfg = tmp_path / "bridge.toml"
    cfg.write_text("timeout = nan\n", encoding="utf-8")
    with pytest.raises(BridgeConfigError) as exc:
        read_config(cfg)
    assert "finite" in str(exc.value)


def test_toml_timeout_inf_rejected(tmp_path):
    cfg = tmp_path / "bridge.toml"
    cfg.write_text("timeout = inf\n", encoding="utf-8")
    with pytest.raises(BridgeConfigError):
        read_config(cfg)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_validate_file_config_rejects_non_finite_timeout(value):
    with pytest.raises(UrlValidationError):
        validate_file_config(FileConfig(timeout_seconds=value))


@pytest.mark.parametrize("value", [0.5, 120.0, 5, 1e-9])
def test_validate_file_config_accepts_positive_finite_timeout(value):
    validate_file_config(FileConfig(timeout_seconds=value))  # must not raise


def test_resolve_run_options_rejects_nan_timeout():
    for cli_timeout in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(BridgeConfigError) as exc:
            resolve_run_options(cli={"timeout": cli_timeout}, env={}, file_config=None)
        assert "finite" in str(exc.value)


@pytest.mark.parametrize("env_value", ["nan", "inf", "-inf", "0", "-5"])
def test_resolve_run_options_rejects_bad_env_timeout(env_value):
    with pytest.raises(BridgeConfigError):
        resolve_run_options(
            cli={}, env={"PD_BRIDGE_TIMEOUT": env_value}, file_config=None
        )


def test_resolve_run_options_accepts_positive_finite_timeout():
    opts = resolve_run_options(cli={"timeout": 2.5}, env={}, file_config=None)
    assert opts.connect_timeout_seconds == 2.5


# --------------------------------------------------------------------------- #
# merged_with — configure preserves unspecified fields (§6 / test 19)
# --------------------------------------------------------------------------- #


def test_merged_with_preserves_unspecified_fields():
    existing = _file_cfg()
    updated = existing.merged_with(FileConfig(ollama_url="http://127.0.0.1:11436"))
    assert updated.server_url == existing.server_url == "wss://config-server.example"
    assert updated.ollama_url == "http://127.0.0.1:11436"
    assert updated.max_retries == 7
    assert updated.token_file == "C:\\config\\token"