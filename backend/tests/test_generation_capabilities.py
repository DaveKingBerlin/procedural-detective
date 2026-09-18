"""Phase16 D/I/J — player-safe generation-capabilities endpoint tests.

Public (NO auth) capability metadata. The exact published shape has ONE
top-level key ``modes``; the local mode carries ``label``/``model`` (the
operator-configured display name — public-safe). The response NEVER contains
the Ollama base URL, credentials, prompts, network details or internal errors.
Only the SELECTED provider is probed (a misconfigured unselected provider can
never block anything).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def _app(settings: Settings):
    return create_app(settings)


@contextmanager
def _client(application):
    with TestClient(application) as test_client:
        yield test_client


def test_capability_shape_with_default_fake_provider(database_url):
    application = _app(Settings(database_url=database_url))
    with _client(application) as c:
        response = c.get("/api/v1/generation-capabilities")
    assert response.status_code == 200
    body = response.json()
    # EXACT top-level shape: only "modes".
    assert set(body.keys()) == {"modes"}
    modes = body["modes"]
    ids = [m["id"] for m in modes]
    assert ids == ["demo", "local"]  # live is NOT configured -> hidden
    assert modes[0] == {"id": "demo", "available": True}
    local = modes[1]
    assert local["id"] == "local"
    assert local["available"] is False  # fake selected -> local unavailable
    assert local["label"] == "Local AI"
    assert local["model"] == "llama3.2:3b"  # operator-configured display name


def test_capability_never_leaks_base_url_or_credentials(database_url):
    application = _app(
        Settings(
            database_url=database_url,
            generation_provider="ollama",
            ollama_base_url="http://127.0.0.1:11434",
            llm_api_key="sekret-value",
        )
    )
    with _client(application) as c:
        response = c.get("/api/v1/generation-capabilities")
    text = response.text
    assert "11434" not in text
    assert "127.0.0.1" not in text
    assert "host.docker.internal" not in text
    assert "sekret-value" not in text
    assert "not available" not in text  # no reason detail is emitted at all


def test_local_available_when_selected_and_probe_reports_model(
    database_url, monkeypatch
):
    from app.api.v1 import generation_capabilities as cap_module

    # selected provider + probe finds the modelled available.
    monkeypatch.setattr(
        cap_module, "ollama_available", lambda settings: (True, "")
    )
    application = _app(
        Settings(
            database_url=database_url,
            generation_provider="ollama",
            ollama_base_url="http://127.0.0.1:11434",
            ollama_model="llama3.2:3b",
        )
    )
    with _client(application) as c:
        response = c.get("/api/v1/generation-capabilities")
    body = response.json()
    local = body["modes"][1]
    assert local["id"] == "local"
    assert local["available"] is True
    assert local["label"] == "Local AI"
    assert local["model"] == "llama3.2:3b"


def test_local_unavailable_when_selected_but_probe_fails(database_url, monkeypatch):
    from app.api.v1 import generation_capabilities as cap_module

    monkeypatch.setattr(
        cap_module, "ollama_available", lambda settings: (False, "not available")
    )
    application = _app(
        Settings(
            database_url=database_url,
            generation_provider="ollama",
            ollama_base_url="http://127.0.0.1:11434",
        )
    )
    with _client(application) as c:
        response = c.get("/api/v1/generation-capabilities")
    assert response.status_code == 200
    assert response.json()["modes"][1]["available"] is False
    assert "not available" not in response.text  # no reason leakage


def test_local_not_probed_when_provider_unselected(database_url, monkeypatch):
    """provider=fake -> local available:false WITHOUT any probe call."""
    from app.api.v1 import generation_capabilities as cap_module

    called = {"count": 0}

    def _unexpected_probe(settings):  # pragma: no cover - must never run
        called["count"] += 1
        return (True, "")

    monkeypatch.setattr(cap_module, "ollama_available", _unexpected_probe)
    application = _app(
        Settings(
            database_url=database_url,
            generation_provider="fake",
            ollama_base_url="http://127.0.0.1:11434",  # configured but unselected
        )
    )
    with _client(application) as c:
        response = c.get("/api/v1/generation-capabilities")
    assert response.status_code == 200
    assert response.json()["modes"][1]["available"] is False
    assert called["count"] == 0  # zero probes for an unselected provider


def test_live_mode_revealed_only_when_configured(database_url):
    # fully configured live -> revealed with available reflecting selection.
    application = _app(
        Settings(
            database_url=database_url,
            generation_provider="live",
            live_provider_url="https://api.example.com/v1/chat/completions",
            llm_api_key="k",
            llm_model="m",
        )
    )
    with _client(application) as c:
        response = c.get("/api/v1/generation-capabilities")
    live = [m for m in response.json()["modes"] if m["id"] == "live"]
    assert live == [{"id": "live", "available": True}]

    # configured but not selected -> live present but unavailable.
    application2 = _app(
        Settings(
            database_url=database_url,
            generation_provider="fake",
            live_provider_url="https://api.example.com/v1/chat/completions",
            llm_api_key="k",
            llm_model="m",
        )
    )
    with _client(application2) as c:
        response2 = c.get("/api/v1/generation-capabilities")
    live2 = [m for m in response2.json()["modes"] if m["id"] == "live"]
    assert live2 == [{"id": "live", "available": False}]


def test_readiness_does_not_depend_on_ollama(database_url, monkeypatch):
    """Global readiness is unchanged: an ollama misconfiguration can never make
    a non-selected app unready, and a selected-but-down app stays ready (the
    provider cannot fail the DB/migration gate)."""
    from app.api.v1 import generation_capabilities as cap_module
    from conftest import upgrade_db

    upgrade_db(database_url)
    monkeypatch.setattr(
        cap_module, "ollama_available", lambda settings: (False, "not available")
    )
    application = _app(Settings(database_url=database_url))
    with _client(application) as c:
        readiness = c.get("/api/v1/readiness")
        assert readiness.status_code == 200
        assert readiness.json() == {"status": "ready", "database": "ok", "migrations": "ok"}
        caps = c.get("/api/v1/generation-capabilities")
        assert caps.status_code == 200