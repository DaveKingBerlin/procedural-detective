"""Phase 49 release-hardening tests for the ``pd-ollama-bridge`` CLI/client.

Covers the frozen Phase 49 release contract owned by the bridge/backend-dev
role (Phase49.md §4/§26/§27/§43/§47):

- the SINGLE canonical version source and its ``pyproject.toml`` dynamic wiring;
- a top-level ``--version`` / ``-V`` that works despite the ``required=True``
  subparser, plus top-level and per-subcommand ``--help``;
- the bounded, secret-free ``connect`` startup block (version / server origin /
  Ollama endpoint / truthful Ollama reachability);
- the default logging path never emitting a token, pairing code, prompt,
  request/response body or an environment dump.

Hermetic: no network. The connect flow is driven exclusively with in-process
fakes; the logging test drives the real job runner with a fake transport and a
fake Ollama client.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import tomllib
from pathlib import Path

import pytest

from pd_ollama_bridge import __version__, cli as cli_mod
from pd_ollama_bridge.bridge_client import BridgeClient
from pd_ollama_bridge.cli import _startup_block_lines, main
from pd_ollama_bridge.config import TokenStore

from conftest import make_config, make_token_store

PAIRING_CODE = "PD-A2B3-C4D5"
TOKEN_SENTINEL = "BRIDGE_TOKEN_PHASE49_SENTINEL_0001"
PROMPT_SENTINEL = "PROMPT_SENTINEL_PHASE49_MUST_NOT_BE_LOGGED"
BODY_SENTINEL = "BODY_SENTINEL_PHASE49_MUST_NOT_BE_LOGGED"
ENV_SENTINEL = "ENV_SENTINEL_PHASE49_MUST_NOT_BE_LOGGED"

EXPECTED_VERSION_LINE = "pd-ollama-bridge 1.0.0"


@pytest.fixture(autouse=True)
def _isolate_bridge_logger():
    """Keep ``cli._configure_logging`` from leaking handlers across tests."""
    logger = cli_mod.LOGGER
    saved = (list(logger.handlers), logger.level, logger.propagate)
    logger.handlers.clear()
    yield
    logger.handlers[:] = saved[0]
    logger.setLevel(saved[1])
    logger.propagate = saved[2]


# --------------------------------------------------------------------------- #
# 1. Canonical version (Phase49 §4 / §43)
# --------------------------------------------------------------------------- #


def test_init_version_is_the_frozen_release_version():
    assert __version__ == "1.0.0"


def test_pyproject_version_is_dynamic_and_matches_source():
    """The package metadata MUST resolve from the single source attribute."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data["project"]
    assert "version" not in project, (
        "a literal [project].version must not exist — "
        "pd_ollama_bridge.__version__ is the single source"
    )
    assert "version" in project["dynamic"]
    attr = data["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    assert attr == "pd_ollama_bridge.__version__"
    module_name, _, attr_name = attr.rpartition(".")
    module = importlib.import_module(module_name)
    assert getattr(module, attr_name) == __version__ == "1.0.0"


# --------------------------------------------------------------------------- #
# 2. CLI --version / -V / --help (Phase49 §4 / §26)
# --------------------------------------------------------------------------- #


def test_version_flag_exits_zero_and_prints_canonical_line(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == EXPECTED_VERSION_LINE


def test_version_short_flag_exits_zero_and_prints_canonical_line(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["-V"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == EXPECTED_VERSION_LINE


def test_top_level_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: pd-ollama-bridge" in out
    assert "--version" in out


@pytest.mark.parametrize(
    "subcommand", ["configure", "connect", "list-models", "config"]
)
def test_subcommand_help_exits_zero(subcommand, capsys):
    with pytest.raises(SystemExit) as exc:
        main([subcommand, "--help"])
    assert exc.value.code == 0
    assert subcommand in capsys.readouterr().out


def test_missing_subcommand_still_required(capsys):
    """``--version`` must not weaken the ``required=True`` subparser contract."""
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# 3. Startup block (Phase49 §26 / §29) — bounded and secret-free
# --------------------------------------------------------------------------- #


def test_startup_block_lines_reachable_are_bounded_and_labeled():
    lines = _startup_block_lines(
        origin="wss://pd.example.com",
        ollama_url="http://127.0.0.1:11434",
        mode="pairing",
        ollama_reachable=True,
    )
    assert lines == (
        f"Bridge version: {__version__}",
        "Server: wss://pd.example.com",
        "Ollama: http://127.0.0.1:11434",
        "Connection mode: pairing",
        "Ollama status: reachable",
    )


def test_startup_block_lines_report_unavailable_truthfully():
    lines = _startup_block_lines(
        origin="ws://127.0.0.1:1",
        ollama_url="http://127.0.0.1:11434",
        mode="reconnect",
        ollama_reachable=False,
    )
    assert "Ollama status: unavailable" in lines
    assert "Ollama status: reachable" not in lines


class _FakeOllama:
    def __init__(self, *, ok, tags=(), **_kwargs):
        self._ok = ok
        self._tags = tuple(tags)

    async def check_available(self):
        return self._ok, self._tags

    async def aclose(self):
        return None


class _FakeBridgeClient:
    def __init__(self, **_kwargs):
        self.sessions_connected = 1
        self.last_error = None

    async def run(self):
        return None


def test_connect_startup_block_unavailable(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli_mod, "OllamaClient", lambda **_kw: _FakeOllama(ok=False))
    rc = main(
        [
            "connect",
            PAIRING_CODE,
            "--server",
            "ws://127.0.0.1:1",
            "--memory-only",
            "--config",
            str(tmp_path / "bridge.toml"),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 1
    assert f"Bridge version: {__version__}" in out
    assert "Server: ws://127.0.0.1:1" in out
    assert "Ollama: http://127.0.0.1:11434" in out
    assert "Connection mode: pairing" in out
    assert "Ollama status: unavailable" in out
    # never the pairing code (nor any secret) on either stream
    assert PAIRING_CODE not in out
    assert PAIRING_CODE not in err


def test_connect_startup_block_reachable(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli_mod, "OllamaClient", lambda **_kw: _FakeOllama(ok=True, tags=("hermes3:8b",))
    )
    monkeypatch.setattr(cli_mod, "BridgeClient", _FakeBridgeClient)
    rc = main(
        [
            "connect",
            PAIRING_CODE,
            "--server",
            "ws://127.0.0.1:1",
            "--memory-only",
            "--config",
            str(tmp_path / "bridge.toml"),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 0
    assert "Ollama status: reachable" in out
    assert PAIRING_CODE not in out
    assert PAIRING_CODE not in err


def test_connect_startup_never_prints_the_stored_token(monkeypatch, tmp_path, capsys):
    token_file = tmp_path / "bridge_token"
    TokenStore(token_file).save(TOKEN_SENTINEL, server_origin="ws://127.0.0.1:1")
    monkeypatch.setattr(cli_mod, "OllamaClient", lambda **_kw: _FakeOllama(ok=False))
    rc = main(
        [
            "connect",
            "--server",
            "ws://127.0.0.1:1",
            "--token-file",
            str(token_file),
            "--config",
            str(tmp_path / "bridge.toml"),
        ]
    )
    out, err = capsys.readouterr()
    assert rc == 1
    assert "Server: ws://127.0.0.1:1" in out
    assert TOKEN_SENTINEL not in out
    assert TOKEN_SENTINEL not in err


# --------------------------------------------------------------------------- #
# 4. Logging safety defaults (Phase49 §27 / §47)
# --------------------------------------------------------------------------- #


def test_default_logging_level_is_info_and_debug_is_opt_in():
    cli_mod._configure_logging(False)
    assert cli_mod.LOGGER.level == logging.INFO
    cli_mod._configure_logging(True)
    assert cli_mod.LOGGER.level == logging.DEBUG


class _RecordingWs:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, data):
        self.sent.append(data)


class _RecordingOllama:
    def __init__(self):
        self.prompts: list[str] = []

    async def run_structured_inference(
        self, *, prompt, temperature, timeout_ms, model=None, format_schema=None
    ):
        self.prompts.append(prompt)
        return {"victim": BODY_SENTINEL}

    async def aclose(self):
        return None


def test_job_logging_never_leaks_prompt_body_token_code_or_env(monkeypatch, caplog):
    monkeypatch.setenv("PHASE49_ENV_SENTINEL", ENV_SENTINEL)
    config = make_config(
        pairing_code=PAIRING_CODE,
        server_url="ws://127.0.0.1:1",
        max_reconnect_attempts=0,
    )
    store = make_token_store()
    store.save(TOKEN_SENTINEL, server_origin="ws://127.0.0.1:1")
    ollama = _RecordingOllama()
    bridge = BridgeClient(config=config, token_store=store, ollama=ollama)
    ws = _RecordingWs()
    msg = {
        "jobId": "JOB-phase49-log",
        "schemaId": "EVIDENCE_v1",
        "prompt": PROMPT_SENTINEL,
        "temperature": 0.0,
        "timeoutMs": 1000,
        "model": "hermes3:8b",
    }

    async def core():
        await bridge._run_job(ws, msg)

    with caplog.at_level(logging.DEBUG, logger="pd-ollama-bridge"):
        asyncio.run(core())

    # Sanity: the job really ran and the sensitive values were live.
    assert ollama.prompts == [PROMPT_SENTINEL]
    assert ws.sent and BODY_SENTINEL in ws.sent[0]

    joined = "\n".join(record.message for record in caplog.records)
    assert PROMPT_SENTINEL not in joined
    assert BODY_SENTINEL not in joined
    assert TOKEN_SENTINEL not in joined
    assert PAIRING_CODE not in joined
    assert ENV_SENTINEL not in joined
    assert "victim" not in joined


def test_connect_default_logs_never_leak_pairing_code(monkeypatch, tmp_path, caplog):
    """The default (debug off) connect path logs no pairing code/token value."""
    monkeypatch.setattr(cli_mod, "OllamaClient", lambda **_kw: _FakeOllama(ok=False))
    with caplog.at_level(logging.DEBUG, logger="pd-ollama-bridge"):
        rc = main(
            [
                "connect",
                PAIRING_CODE,
                "--server",
                "ws://127.0.0.1:1",
                "--memory-only",
                "--config",
                str(tmp_path / "bridge.toml"),
            ]
        )
    assert rc == 1
    joined = "\n".join(record.message for record in caplog.records)
    assert PAIRING_CODE not in joined
    assert TOKEN_SENTINEL not in joined
    assert PROMPT_SENTINEL not in joined
