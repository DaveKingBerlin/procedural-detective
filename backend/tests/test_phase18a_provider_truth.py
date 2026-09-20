"""Phase 18A — provider UX truthful against the ACTUAL backend behavior.

With fully hermetic environments (the autouse conftest fixture pins
ENV_FILE=os.devnull and drops every operator provider key, so the operator
LAN host is never read):

1. the generation-capabilities DTO truthfully reports the backend's chosen
   provider: demo always; local available == True ONLY when
   GENERATION_PROVIDER=ollama AND the sanitized probe passes; live available
   ONLY when GENERATION_PROVIDER=live with all three credential vars set —
   and never when not;
2. GENERATION_PROVIDER=fake (the default) never advertises local/live and the
   fake path makes no network calls;
3. request bodies carrying provider/mode/base_url extra keys are ignored and
   can NEVER change the provider — selection is 100 % config-driven (asserted
   with a sentinel env matrix);
4. the provider factory is config-driven only (live without all three
   credentials fails fast at construction with a sanitized ProviderConfigError).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.cases import CaseCreateRequest  # noqa: E402
from phase5_helpers import auth, create_case, create_session  # noqa: E402

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _capabilities(app, monkeypatch=None) -> dict:
    with TestClient(app) as c:
        response = c.get("/api/v1/generation-capabilities")
        assert response.status_code == 200
        return response.json()


def _modes(body: dict) -> dict[str, dict]:
    """mode-id -> entry map (exact modes are asserted separately)."""
    return {m["id"]: m for m in body["modes"]}


def _ollama_settings(database_url: str, **overrides):
    return Settings(
        database_url=database_url,
        generation_provider="ollama",
        ollama_base_url="http://127.0.0.1:11434",
        **overrides,
    )


# --------------------------------------------------------------------------- #
# part 1 — capabilities truthfully mirror the operator env / settings
# --------------------------------------------------------------------------- #


def test_fake_provider_never_advertises_local_or_live(database_url):
    """GENERATION_PROVIDER=fake (the default) advertises demo ALWAYS and never
    reports local/live as available — even with every live credential and the
    ollama URL configured in the settings object."""
    application = create_app(
        Settings(
            database_url=database_url,
            generation_provider="fake",
            ollama_base_url="http://127.0.0.1:11434",
            llm_api_key="sentinel-key",
            live_provider_url="https://api.example.com/v1/chat/completions",
            llm_model="sentinel-model",
        )
    )
    try:
        modes = _modes(_capabilities(application))
    finally:
        application.state.engine.dispose()
        application.state.store.dispose()

    assert modes["demo"]["available"] is True
    assert modes["local"]["available"] is False
    assert modes["local"]["label"] == "Local AI"
    # provider=fake with creds set -> live is present but NEVER available.
    assert modes["live"]["available"] is False


def test_live_capability_truthful_matrix(database_url):
    """live available == True ONLY for GENERATION_PROVIDER=live + all three
    credential vars; an operator selecting live with a credential missing gets
    a fail-fast configuration error (never a silently-available DTO); live is
    present but unavailable when configured while another provider is selected."""
    from app.services.generation import ProviderConfigError

    # provider=live + all three creds -> available True.
    app_live = create_app(
        Settings(
            database_url=database_url,
            generation_provider="live",
            llm_api_key="k",
            llm_model="m",
            live_provider_url="https://api.example.com/v1/chat/completions",
        )
    )
    try:
        modes = _modes(_capabilities(app_live))
    finally:
        app_live.state.engine.dispose()
        app_live.state.store.dispose()
    assert modes["live"] == {"id": "live", "available": True}

    # provider=live but one credential missing (each permutation): the app
    # refuses to start (construct-time ProviderConfigError) — "never when not".
    for missing in ("llm_api_key", "llm_model", "live_provider_url"):
        kwargs = dict(
            generation_provider="live",
            llm_api_key="k",
            llm_model="m",
            live_provider_url="https://api.example.com/v1/chat/completions",
        )
        kwargs[missing] = None
        with pytest.raises(ProviderConfigError):
            create_app(Settings(database_url=database_url, **kwargs))

    # fake selected + creds set -> live present but unavailable.
    app_fake = create_app(
        Settings(
            database_url=database_url,
            generation_provider="fake",
            llm_api_key="k",
            llm_model="m",
            live_provider_url="https://api.example.com/v1/chat/completions",
        )
    )
    try:
        modes = _modes(_capabilities(app_fake))
    finally:
        app_fake.state.engine.dispose()
        app_fake.state.store.dispose()
    assert modes["live"] == {"id": "live", "available": False}


def test_local_capability_truthful_matrix(database_url, monkeypatch):
    """local available == True ONLY when the provider is selected AND the
    sanitized probe passes; otherwise False without any reason detail."""
    from app.api.v1 import generation_capabilities as cap_module

    # selected + probe finds the modeled model -> available True.
    monkeypatch.setattr(cap_module, "ollama_available", lambda settings: (True, ""))
    app = create_app(_ollama_settings(database_url))
    try:
        modes = _modes(_capabilities(app))
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()
    assert modes["local"]["available"] is True
    assert modes["local"]["model"] == "llama3.2:3b"

    # selected + probe fails -> available False, sanitized (no detail leak).
    monkeypatch.setattr(cap_module, "ollama_available", lambda settings: (False, "not available"))
    app = create_app(_ollama_settings(database_url))
    try:
        body = _capabilities(app)
        modes = _modes(body)
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()
    assert modes["local"]["available"] is False
    assert "not available" not in str(body)

    # provider != ollama: the probe must NEVER run even with the URL set.
    calls = {"count": 0}

    def _must_not_run(settings):  # pragma: no cover - network would be blocked
        calls["count"] += 1
        return (True, "")

    monkeypatch.setattr(cap_module, "ollama_available", _must_not_run)
    app = create_app(
        Settings(database_url=database_url, generation_provider="fake")
    )
    try:
        modes = _modes(_capabilities(app))
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()
    assert modes["local"]["available"] is False
    assert calls["count"] == 0


def test_capabilities_env_driven_selection(monkeypatch, database_url):
    """The SELECTION is operator-env driven: real env vars decide provider and
    model display, exactly like the factory does (hermetic ENV_FILE)."""
    from app.api.v1 import generation_capabilities as cap_module

    monkeypatch.setenv("ENV_FILE", os.devnull)
    monkeypatch.setenv("GENERATION_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "hermes3:8b")
    monkeypatch.setattr(cap_module, "ollama_available", lambda settings: (True, ""))
    application = create_app(Settings(database_url=database_url))
    try:
        modes = _modes(_capabilities(application))
    finally:
        application.state.engine.dispose()
        application.state.store.dispose()
    assert application.state.settings.generation_provider == "ollama"
    assert modes["local"]["available"] is True
    assert modes["local"]["model"] == "hermes3:8b"


# --------------------------------------------------------------------------- #
# part 2 — no client-supplied provider selection claims
# --------------------------------------------------------------------------- #


def test_case_create_request_ignores_provider_extra_keys():
    """The public POST /cases DTO exposes NO provider fields: extra keys
    (provider/mode/base_url) are silently ignored and never change selection."""
    body = CaseCreateRequest(
        prompt="Victim: sarah_miller\nMurderer: thomas_reed\n",
        provider="live",
        mode="ollama",
        base_url="http://192.168.1.5:11434",
        llm_api_key="sentinel",
    )
    assert body.prompt.startswith("Victim:")
    assert body.difficulty is None
    assert body.environment is None
    assert not hasattr(body, "provider")
    assert not hasattr(body, "mode")
    assert not hasattr(body, "base_url")
    assert not hasattr(body, "llm_api_key")
    # Pydantic model_fields is the exact public surface: no provider keys.
    declared = {name for name in CaseCreateRequest.model_fields}
    assert {"provider", "mode", "base_url", "llm_api_key"} & declared == set()


def test_post_cases_with_hostile_provider_keys_uses_fake(database_url):
    """A request body demanding a different provider/URL is IGNORED: the run
    still publishes with the deterministic fake provider (no network — the
    autouse block would fail any provider endpoint reach-out)."""
    from conftest import upgrade_db

    upgrade_db(database_url)
    application = create_app(
        Settings(database_url=database_url, cors_allowed_origins=["http://localhost:5173"])
    )
    try:
        with TestClient(application) as c:
            session_token, _ = create_session(c)
            case = c.post(
                "/api/v1/cases",
                json={
                    "prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n",
                    "provider": "live",
                    "mode": "ollama",
                    "base_url": "http://192.168.1.5:11434",
                    "llm_api_key": "sentinel-key",
                    "live_provider_url": "https://api.example.com/v1/chat/completions",
                },
                headers=auth(session_token),
            )
            assert case.status_code == 201, case.json()
            assert case.json()["status"] == "PUBLISHED"
            # capabilities still report the operator-chosen demo mode only.
            caps = c.get("/api/v1/generation-capabilities")
            modes = _modes(caps.json())
            assert modes["demo"]["available"] is True
            assert modes["local"]["available"] is False
    finally:
        application.state.engine.dispose()
        application.state.store.dispose()


def test_provider_factory_selection_is_config_driven(monkeypatch, database_url):
    """The provider factory answers ONLY to Settings — never to request data.
    A sentinel matrix proves each selection is 100 % config-driven."""
    from app.generation.fake_provider import FakeProvider
    from app.generation.live_provider import LiveHttpProvider
    from app.generation.ollama_provider import OllamaProvider
    from app.persistence.store import Store
    from app.services.generation import GenerationService, ProviderConfigError

    # ---- fake: creds + ollama url present but provider=fake -> FakeProvider.
    store = Store(database_url)
    try:
        fake_settings = Settings(
            database_url=database_url,
            generation_provider="fake",
            llm_api_key="sentinel",
            live_provider_url="https://api.example.com/v1/chat/completions",
            llm_model="sentinel-model",
            ollama_base_url="http://127.0.0.1:11434",
        )
        service = GenerationService(settings=fake_settings, store=store)
        assert isinstance(service._build_default_provider_factory()(), FakeProvider)
    finally:
        store.dispose()

    # ---- live: missing credential -> ProviderConfigError (fail-fast).
    for missing in ("llm_api_key", "llm_model", "live_provider_url"):
        kwargs = dict(
            generation_provider="live",
            llm_api_key="k",
            llm_model="m",
            live_provider_url="https://api.example.com/v1/chat/completions",
        )
        kwargs[missing] = None
        settings = Settings(database_url=database_url, **kwargs)
        with pytest.raises(ProviderConfigError) as excinfo:
            GenerationService(settings=settings, store=store)
        assert excinfo.value.args[0] == (
            "generation_provider=live requires LIVE_PROVIDER_URL, "
            "LLM_API_KEY and LLM_MODEL"
        )
        assert "k" not in str(excinfo.value)  # key never echoed (sanitized)
        assert "sentinel" not in str(excinfo.value)

    # ---- live: all three creds -> LiveHttpProvider (constructed, no I/O).
    live_store = Store(database_url)
    try:
        live_settings = Settings(
            database_url=database_url,
            generation_provider="live",
            llm_api_key="k",
            llm_model="m",
            live_provider_url="https://api.example.com/v1/chat/completions",
        )
        live_service = GenerationService(settings=live_settings, store=live_store)
        provider = live_service._build_default_provider_factory()()
        assert isinstance(provider, LiveHttpProvider)
    finally:
        live_store.dispose()

    # ---- ollama: only when selected; no network probe in this unit test
    # (the capability probe is monkeypatched to avoid a loopback connect).
    monkeypatch.setattr(
        "app.generation.ollama_provider.ollama_structured_output_supported",
        lambda settings: False,
    )
    ollama_store = Store(database_url)
    try:
        ollama_settings = Settings(
            database_url=database_url,
            generation_provider="ollama",
            ollama_base_url="http://127.0.0.1:11434",
        )
        ollama_service = GenerationService(settings=ollama_settings, store=ollama_store)
        provider = ollama_service._build_default_provider_factory()()
        assert isinstance(provider, OllamaProvider)
    finally:
        ollama_store.dispose()