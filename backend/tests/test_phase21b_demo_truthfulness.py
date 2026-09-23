"""Phase21B Finding 3 — demo CTA truthfulness (DEF-096, BACKEND HALF).

When ``GENERATION_PROVIDER=ollama`` and the Ollama capability probe FAILS
(Ollama down / merely-slow), the capability DTO used to collapse to the
byte-identical fake-only shape ``[{demo:true},{local:false,...}]`` — the
frontend then re-shows "Try Demo Case / Deterministic demo — no API keys, no
cost." while POST /cases actually invokes the OLLAMA provider
(Phase21B-PAC §3 contract violation; ADV-232 / DEF-096).

The backend fix makes the DTO reveal the CONFIGURED provider authoritatively
and makes ``demo.available`` truthful:

  1. NEW top-level ``configuredProvider`` field: the EXACT raw
     ``Settings.generation_provider`` enum string (sanitized — never a URL,
     host/IP, model token or credential), the backend-authoritative
     "what will actually run" signal INDEPENDENT of probe availability;
  2. ``demo.available`` is TRUE ONLY when ``configuredProvider == "fake"``
     (the deterministic demo is actually server-enforced on that profile
     alone). An ollama/live backend reports ``demo.available:false`` even
     while its probe is down, so the DTO can never look fake-only;
  3. defensive fail-closed allowlist: a hostile settings value can never turn
     ``configuredProvider`` into a non-enum string (any non-enum resolves to
     ``"fake"`` — the provider factory's runtime fallback branch for unknown
     values), so the field always reports what will actually run.

A fake backend KEEPS the historical modes semantics byte-identical
(``[{demo:true},{local:false,...}]``) plus ``configuredProvider:"fake"``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import generation_capabilities as cap_service  # noqa: E402


def _modes(body: dict) -> dict[str, dict]:
    """mode-id -> entry map (the modes ORDER is asserted separately)."""
    return {m["id"]: m for m in body["modes"]}


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


# --------------------------------------------------------------------------- #
# 1. GENERATION_PROVIDER=fake -> demo truthful, configuredProvider "fake",
#    modes semantics byte-identical to the pre-DEF-096 DTO.
# --------------------------------------------------------------------------- #


def test_fake_provider_demo_is_truthful_and_modes_byte_identical(database_url):
    application = create_app(
        Settings(database_url=database_url, generation_provider="fake")
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"modes", "configuredProvider"}
    assert body["configuredProvider"] == "fake"
    # EXACT historical modes semantics (byte-identical content + key order):
    # demo available + local unavailable with the public display model name.
    assert body["modes"] == [
        {"id": "demo", "available": True},
        {
            "id": "local",
            "available": False,
            "label": "Local AI",
            "model": "llama3.2:3b",
        },
    ]


# --------------------------------------------------------------------------- #
# 2. GENERATION_PROVIDER=ollama + probe SUCCESS -> configuredProvider "ollama",
#    demo.available FALSE (the deterministic path is NOT server-enforced on an
#    ollama profile), local.available TRUE.
# --------------------------------------------------------------------------- #


def test_ollama_probe_success_dto(database_url, monkeypatch):
    from app.api.v1 import generation_capabilities as cap_module

    monkeypatch.setattr(cap_module, "ollama_available", lambda settings: (True, ""))
    application = create_app(
        Settings(
            database_url=database_url,
            generation_provider="ollama",
            ollama_base_url="http://127.0.0.1:11434",
            ollama_model="llama3.2:3b",
        )
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    assert response.status_code == 200
    body = response.json()
    assert body["configuredProvider"] == "ollama"
    modes = _modes(body)
    assert modes["demo"]["available"] is False
    assert modes["local"]["available"] is True


# --------------------------------------------------------------------------- #
# 3. GENERATION_PROVIDER=ollama + probe FAILURE (Ollama down / slow) -> the
#    DEF-096 FIX: the DTO no longer looks fake-only. configuredProvider stays
#    "ollama", demo.available FALSE, local.available FALSE.
# --------------------------------------------------------------------------- #


def test_ollama_probe_failure_dto_monkeypatched(database_url, monkeypatch):
    """The exact DEF-096 probe-failure state, injected at the API seam: the
    probe returns False (Ollama down). Before the fix this DTO was
    byte-identical to the fake backend; now configuredProvider reveals the
    configured OLLAMA provider while demo.available is False."""
    from app.api.v1 import generation_capabilities as cap_module

    monkeypatch.setattr(
        cap_module, "ollama_available", lambda settings: (False, "not available")
    )
    application = create_app(
        Settings(
            database_url=database_url,
            generation_provider="ollama",
            ollama_base_url="http://127.0.0.1:11434",
        )
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    assert response.status_code == 200
    body = response.json()
    # Backend-authoritative: the CONFIGURED provider is ollama regardless of
    # the probe outcome — the DTO can never be mistaken for a fake-only backend.
    assert body["configuredProvider"] == "ollama"
    modes = _modes(body)
    assert modes["demo"]["available"] is False
    assert modes["local"]["available"] is False


def test_ollama_probe_failure_dto_via_bounded_seam(database_url):
    """The same DEF-096 state injected at the AUTHORITATIVE service seam
    (the bounded probe cache returns a sanitized False — Ollama down)."""
    cap_service.reset_capability_probe_cache(
        clock=ManualClock(start_time=0.0),
        probe=lambda settings_, transport=None: (False, "not available"),
    )
    application = create_app(
        Settings(
            database_url=database_url,
            generation_provider="ollama",
            ollama_base_url="http://127.0.0.1:11434",
        )
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    assert response.status_code == 200
    body = response.json()
    assert body["configuredProvider"] == "ollama"
    modes = _modes(body)
    assert modes["demo"]["available"] is False
    assert modes["local"]["available"] is False


def test_ollama_probe_hang_dto_via_bounded_seam(database_url):
    """DEF-096's merely-SLOW/hung Ollama case through the bounded seam: the
    probe blocks past a tiny ceiling then fails closed sanitized; the endpoint
    still answers promptly and never collapses to the fake-only shape."""
    started = time.monotonic()

    def _hung_probe(settings_, transport=None):
        time.sleep(0.15)  # a slow/hung Ollama probe (well under any ceiling)
        return (False, "not available")

    cap_service.reset_capability_probe_cache(
        clock=ManualClock(start_time=0.0), probe=_hung_probe
    )
    application = create_app(
        Settings(
            database_url=database_url,
            generation_provider="ollama",
            ollama_base_url="http://127.0.0.1:11434",
        )
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed < 1.0
    body = response.json()
    assert body["configuredProvider"] == "ollama"
    modes = _modes(body)
    assert modes["demo"]["available"] is False
    assert modes["local"]["available"] is False


# --------------------------------------------------------------------------- #
# 4. GENERATION_PROVIDER=live -> configuredProvider "live", demo.available
#    FALSE, live.available TRUE.
# --------------------------------------------------------------------------- #


def test_live_configured_dto(database_url):
    application = create_app(
        Settings(
            database_url=database_url,
            generation_provider="live",
            llm_api_key="k",
            llm_model="m",
            live_provider_url="https://api.example.com/v1/chat/completions",
        )
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    assert response.status_code == 200
    body = response.json()
    assert body["configuredProvider"] == "live"
    modes = _modes(body)
    assert modes["demo"]["available"] is False
    assert modes["live"]["available"] is True


# --------------------------------------------------------------------------- #
# 5. leak scan: the response text never carries a URL / IP / model token /
#    credential, and hostile settings values can never turn
#    configuredProvider into a non-enum string (defensive allowlist, fail
#    closed to the runtime "fake" fallback).
# --------------------------------------------------------------------------- #


def test_configured_provider_allowlist_fails_closed_on_hostile_values():
    from app.api.v1.generation_capabilities import _configured_provider

    assert _configured_provider(SimpleNamespace(generation_provider="fake")) == "fake"
    assert _configured_provider(SimpleNamespace(generation_provider="ollama")) == "ollama"
    assert _configured_provider(SimpleNamespace(generation_provider="live")) == "live"
    # Hostile / unknown / missing values can NEVER become a non-enum string:
    # the closed allowlist fails closed to "fake" — the one provider that is
    # always server-enforced and the factory's runtime fallback branch.
    for hostile in (
        "http://evil.example:11434/sekret-value",
        "ollama llama3.2:3b",
        "live 'ollama'",
        "fake\n<script>",
        42,
        "",
        None,
    ):
        assert _configured_provider(
            SimpleNamespace(generation_provider=hostile)
        ) == "fake", hostile
    # Missing attribute entirely -> "fake".
    assert _configured_provider(object()) == "fake"


def test_hostile_settings_cannot_leak_via_configured_provider(database_url):
    """End-to-end: a hostile injected settings object with a hostile
    generation_provider (full URL + credential tokens) cannot smuggle any of
    it into the response — configuredProvider stays on the closed enum, and
    the response text stays free of every hostile token, URL/IP material and
    the secret credential."""
    application = create_app(Settings(database_url=database_url))
    application.state.settings = SimpleNamespace(
        trust_proxy=False,
        generation_provider="http://evil.example:11434/sekret-value",
        ollama_model="llama3.2:3b",  # public display name (allowed field)
        llm_api_key="sekret-value",
        live_provider_url="https://api.example.com/v1/chat/completions",
        llm_model="m",
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    assert response.status_code == 200
    body = response.json()
    # Fail closed to the runtime fallback enum value: never the hostile string.
    assert body["configuredProvider"] == "fake"
    modes = _modes(body)
    assert modes["demo"]["available"] is True  # fake fallback = demo is truthful
    text = response.text
    for token in (
        "evil.example",
        "sekret-value",
        "11434",
        "127.0.0.1",
        "://",
        "host.docker.internal",
    ):
        assert token not in text, token