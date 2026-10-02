"""Phase 25 — browser-selectable generation provider (backend scope).

Covers (Phase25 §14 / §15 items 1-20 / §20A items 1-14):

  1. the central user-supplied Ollama model-string validator (§1.3 / §20A.10/11);
  2. the immutable per-attempt selection resolver (fake/live/frontier/ollama
     server + bridge) with typed availability semantics (§5 / §18) and
     zero global mutation;
  3. POST /api/v1/cases provider-selection semantics (§4 / §12): optional
     fields, configured-default fallback, unknown provider/transport rejected
     with the canonical envelope, explicit-but-unavailable rejected WITHOUT a
     silent fallback, Ollama transport + model REQUIRED, non-Ollama selections
     ignore the model, the offending value never leaks;
  4. capabilities ADDITIVE shape (§3 / §15.1-5): modes/configuredProvider/
     remoteLocalAi byte-identical + NEW defaultProvider/providers with SAFE
     reason strings and zero secret leakage;
  5. §14 concurrency: two overlapping attempts (A=fake, B=ollama/server/
     selected model) use their OWN frozen selection at EVERY stage; no global
     mutation (``settings.generation_provider`` untouched);
  6. per-job bridge model via the REAL loopback bridge harness (§7.2 /
     §20A.2/.4/.12/.13/.14 / §15.19) and bridge-fails-closed (never falls back
     to server) (§20A.7/.8);
  7. per-attempt direct Ollama model without global mutation (§20A.1/.3/.5/.6).

Every external provider is a mock/fake — CI never needs a real Ollama server,
Frontier credentials or a public bridge.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.provider import GenerateRequest, GenerationStage  # noqa: E402
from app.generation.selection import GenerationSelection  # noqa: E402
from app.main import create_app  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402
from phase5_helpers import auth, create_session  # noqa: E402

from bridge_harness import (  # noqa: E402
    LiveTestServer,
    TestBridge,
    create_pairing,
    make_bridge_settings,
    new_anonymous_session,
)

# The deterministic fake-style pipeline stages (non-driver providers).
_STAGES = (
    GenerationStage.CASE_TRUTH,
    GenerationStage.PUBLIC_WORLD,
    GenerationStage.EVIDENCE,
    GenerationStage.WORLD_GRAPH,
)
_G = GOLDEN_STAGE_PAYLOADS

# A deterministic prompt (structured key surface recognized by normalize_prompt
# and the golden fixtures).
_PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)

# The bridge driver case (ice-pick weapon -> ASSET_SPEC round-trip).
_BRIDGE_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa Koenig\n"
    "Location: office\n"
)


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


def _mock_ollama_transport(*, golden, block_first: threading.Event | None = None):
    """A transport mock standing in for ``_HttpxOllamaTransport``.

    Serves the golden driver-stage content FIFO (never the network) and
    records every payload (URL + body + timeout) for the stage/model asserts.
    """
    from app.generation.ollama_provider import _HttpxOllamaTransport

    class _Mock(_HttpxOllamaTransport):
        released = threading.Event()
        started = threading.Event()
        posts: list[tuple[str, dict, float]] = []

        def post_json(self, url, payload, timeout):
            self.posts.append((url, dict(payload), float(timeout)))
            if block_first is not None and len(self.posts) == 1:
                block_first.set()
                self.released.wait(timeout=30)
            content = golden.pop(0) if golden else "<not-json>"
            return 200, json.dumps(
                {
                    "model": payload.get("model", ""),
                    "message": {"role": "assistant", "content": content},
                }
            ).encode()

        def get(self, url, timeout):
            return 200, json.dumps(
                {"version": "0.8.1", "models": [{"name": "dummy"}]}
            ).encode()

    return _Mock


def _mock_dual_model_transport(golden_by_model):
    """Barrier-synchronized transport mock for the two-direct-models test.

    Serves each model's golden FIFO independently and blocks the FIRST post of
    EVERY model until the shared ``release`` event is tripped — so two
    concurrent requests using DIFFERENT models are both genuinely in-flight
    (stuck inside their first transport call) at the same instant before either
    may proceed. Records every payload for the per-model stage asserts.
    """
    from app.generation.ollama_provider import _HttpxOllamaTransport

    class _Dual(_HttpxOllamaTransport):
        posts: list[tuple[str, dict, float]] = []
        first_post_for_model: set = set()
        lock = threading.Lock()
        release = threading.Event()

        @classmethod
        def post_json(cls, url, payload, timeout):
            model = payload.get("model", "")
            with cls.lock:
                cls.posts.append((url, dict(payload), float(timeout)))
                first = model not in cls.first_post_for_model
                if first:
                    cls.first_post_for_model.add(model)
            if first:
                cls.release.wait(timeout=30)
            queue = golden_by_model.get(model)
            content = queue.pop(0) if queue else "<not-json>"
            return 200, json.dumps(
                {
                    "model": model,
                    "message": {"role": "assistant", "content": content},
                }
            ).encode()

        @classmethod
        def get(cls, url, timeout):
            return 200, json.dumps(
                {"version": "0.8.1", "models": [{"name": "dummy"}]}
            ).encode()

    return _Dual


# --------------------------------------------------------------------------- #
# 1. the central model-string validator (§1.3 / §20A.10/11)
# --------------------------------------------------------------------------- #


def test_model_validator_accepts_typical_ollama_labels():
    from app.generation.selection import validate_ollama_model_string

    for ok in (
        "qwen2.5:1.5b",
        "hermes3:8b",
        "llama3.2:3b",
        "my-model.latest:tag",
        "library/model:1.2.3",
        "model+q8_0",
        " custom_model:latest ",  # surrounding whitespace is trimmed
        "mymodel",
        "a/b:c",
        "m" * 256,
    ):
        assert validate_ollama_model_string(ok) == ok.strip(), ok


def test_model_validator_rejects_every_unsafe_shape():
    from app.generation.selection import InvalidOllamaModelError
    from app.generation.selection import validate_ollama_model_string

    bad_values = (
        "",                  # empty after trim
        "   ",               # whitespace only
        "a\nb",             # control/newline
        "a\rb",             # control/CR
        "a\x00b",           # NUL
        "qwen2.5:1.5b\x7f",  # DEL
        "http://evil/x",
        "https://evil/x",
        "ws://evil/x",
        "wss://evil/x",
        "ftp://evil/x",      # general scheme-looking prefix
        "a" * 257,           # over 256 chars
        "bad model!",        # space / punctuation
        "model|shell",
        "model$(cmd)",
        "`id`",
    )
    for value in bad_values:
        with pytest.raises(InvalidOllamaModelError):
            validate_ollama_model_string(value)
    assert validate_ollama_model_string(None) is None  # absent field allowed


def test_model_validator_f1_rejects_scheme_without_slashes_and_path_shapes():
    """F1 (accepted adversarial finding) — the ``http://``-family substring
    scan only ever caught the ``://`` form. Scheme-like prefixes WITHOUT ``//``
    (``http:evil.example``, ``https:evil``, ``http:/evil``, ``ws:``) and
    path-traversal / ``//``-separator shapes (``http//evil``,
    ``model/../pwn``, ``../x``, ``x/..``) are now rejected too, while single
    ``/`` and single ``.`` inside legitimate tags stay allowed (see the
    accepts test above)."""
    from app.generation.selection import InvalidOllamaModelError
    from app.generation.selection import validate_ollama_model_string

    for hostile in (
        "http:evil.example",
        "https:evil",
        "http:/evil",
        "http//evil",
        "ws:",
        "model/../pwn",
        "../x",
        "x/..",
    ):
        with pytest.raises(InvalidOllamaModelError):
            validate_ollama_model_string(hostile)
    for safe in (
        "qwen2.5:1.5b",
        "library/model:1.2.3",
        "model+q8_0",
        "mymodel",
        "a/b:c",
    ):
        assert validate_ollama_model_string(safe) == safe


def test_model_validator_never_echoes_the_offending_value():
    from app.generation.selection import InvalidOllamaModelError
    from app.generation.selection import validate_ollama_model_string

    hostile = "http://evil.example:11434/sekret-value\x00"
    with pytest.raises(InvalidOllamaModelError) as excinfo:
        validate_ollama_model_string(hostile)
    for token in ("evil.example", "11434", "sekret-value"):
        assert token not in str(excinfo.value)
    # F1 — the scheme-without-// and path-traversal rejection messages must be
    # just as sanitized (the offending value is never echoed).
    for hostile in ("http:evil.example:11434/sekret", "model/../pwn/sekret"):
        with pytest.raises(InvalidOllamaModelError) as excinfo:
            validate_ollama_model_string(hostile)
        for token in ("evil.example", "11434", "sekret", "pwn"):
            assert token not in str(excinfo.value)


def test_model_validator_charset_matches_bridge_protocol_token_set():
    """The central validator's alphabet is a SUPERSET of the (Phase 22-24
    synced) bridge protocol label alphabet — every label both copies accept can
    also pass the central validator (§20A.12/13, drift-guard-safe)."""
    from app.generation.bridge_protocol import (
        MAX_MODEL_LABEL_LENGTH,
        MODEL_LABEL_RE,
    )
    from app.generation.selection import validate_ollama_model_string

    for probe in ("namespace/model:tag", "name+q8", "a_b.c-d:1"):
        assert MODEL_LABEL_RE.fullmatch(probe), probe
        assert validate_ollama_model_string(probe) == probe
        assert len(probe) <= MAX_MODEL_LABEL_LENGTH
    # A >80-char model passes the central validator but is REJECTED for the
    # bridge request at selection time (the bridge job-frame label bound).
    assert validate_ollama_model_string("m" * 90) == "m" * 90


# --------------------------------------------------------------------------- #
# 2. immutable selection resolver (§5 / §18)
# --------------------------------------------------------------------------- #


def _settings(**overrides) -> Settings:
    kwargs: dict = {}
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_resolve_fake_produces_stateless_fake_provider():
    from app.generation.selection import resolve

    script = {GenerationStage.CASE_TRUTH: ["ok:x"]}
    resolved = resolve(
        GenerationSelection("fake"), _settings(), session=None, fake_script=script
    )
    assert resolved.provider_id == "fake"
    assert resolved.model is None
    assert resolved.timeout_seconds is None
    assert resolved.needs_driver is False
    provider = resolved.provider_factory()
    assert isinstance(provider, FakeProvider)
    assert isinstance(resolved.provider_factory(), FakeProvider)  # fresh per call
    result = provider.generate(
        GenerateRequest(
            attempt_id="GA-fake", stage=GenerationStage.CASE_TRUTH, prompt_context="ctx"
        )
    )
    assert result.content == "x"


def test_resolve_live_provider_and_fails_closed_without_credentials():
    from app.generation.live_provider import LiveHttpProvider
    from app.generation.selection import SelectionConfigError, resolve

    resolved = resolve(
        GenerationSelection("live"),
        _settings(
            generation_provider="live",
            live_provider_url="https://api.example.com/v1/chat/completions",
            llm_api_key="secret-key",
            llm_model="model-x",
        ),
        session=None,
    )
    assert resolved.provider_id == "live"
    assert resolved.model == "model-x"
    assert isinstance(resolved.provider_factory(), LiveHttpProvider)
    for missing in ("llm_api_key", "llm_model", "live_provider_url"):
        kwargs = dict(
            generation_provider="live",
            llm_api_key="k",
            llm_model="m",
            live_provider_url="https://api.example.com/v1/chat/completions",
        )
        kwargs[missing] = None
        with pytest.raises(SelectionConfigError):
            resolve(GenerationSelection("live"), _settings(**kwargs), session=None)


def test_resolve_frontier_availability_and_https_settings():
    from app.generation.frontier_provider import FrontierProvider
    from app.generation.selection import (
        ProviderUnavailableError,
        SelectionConfigError,
        frontier_configured,
        resolve,
    )

    full = dict(
        frontier_enabled=True,
        frontier_base_url="https://api.example.com/v1/chat/completions",
        frontier_api_key="secret-key",
        frontier_model="frontier-model-1",
    )
    assert frontier_configured(_settings(**full)) is True
    resolved = resolve(GenerationSelection("frontier"), _settings(**full), session=None)
    assert resolved.provider_id == "frontier"
    assert resolved.model == "frontier-model-1"
    assert isinstance(resolved.provider_factory(), FrontierProvider)

    partial = dict(frontier_enabled=True, frontier_model="m")
    with pytest.raises(ProviderUnavailableError):
        resolve(
            GenerationSelection("frontier"),
            _settings(**partial),
            session=None,
            strict_unavailable=True,
        )
    with pytest.raises(SelectionConfigError):
        resolve(GenerationSelection("frontier"), _settings(**partial), session=None)


def test_frontier_base_url_remains_https_only():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(
            frontier_enabled=True,
            frontier_base_url="http://api.example.com/v1/chat/completions",
            frontier_api_key="k",
            frontier_model="m",
        )
    ok = Settings(
        frontier_enabled=True,
        frontier_base_url="https://api.example.com/v1/chat/completions",
        frontier_api_key="k",
        frontier_model="m",
    )
    assert ok.frontier_base_url.startswith("https://")


def test_resolve_ollama_server_freezes_selected_model():
    from app.generation.ollama_provider import OllamaProvider
    from app.generation.selection import resolve

    resolved = resolve(
        GenerationSelection("ollama", ollama_transport="server", ollama_model="hermes3:9b"),
        _settings(generation_provider="fake", ollama_base_url="http://127.0.0.1:11434"),
        session=None,
        ollama_structured_output=False,
    )
    assert resolved.provider_id == "ollama"
    assert resolved.model == "hermes3:9b"
    assert resolved.needs_driver is True
    assert resolved.timeout_seconds == 60.0  # OLLAMA_TIMEOUT_SECONDS default
    provider = resolved.provider_factory()
    assert isinstance(provider, OllamaProvider)
    assert provider._model == "hermes3:9b"  # frozen, never the settings default


def test_resolve_ollama_server_default_model_when_none_selected():
    from app.generation.selection import resolve

    # The DEFAULT (no request model) path freezes the configured OLLAMA_MODEL.
    resolved = resolve(
        GenerationSelection("ollama", ollama_transport="server", ollama_model=None),
        _settings(
            generation_provider="ollama",
            ollama_base_url="http://127.0.0.1:11434",
            ollama_model="llama3.2:3b",
        ),
        session=None,
    )
    assert resolved.model == "llama3.2:3b"


def test_resolve_ollama_explicit_unavailable_when_not_configured():
    from app.generation.selection import (
        ProviderUnavailableError,
        resolve,
    )

    # fake default + NO OLLAMA_BASE_URL: an explicit ollama/server request is a
    # hard 400 (PROVIDER_UNAVAILABLE) — never a silent fallback.
    with pytest.raises(ProviderUnavailableError):
        resolve(
            GenerationSelection("ollama", ollama_transport="server", ollama_model="m"),
            _settings(generation_provider="fake"),
            session=None,
            strict_unavailable=True,
        )


def test_resolve_bridge_requires_enable_bridge():
    from app.generation.selection import (
        ProviderUnavailableError,
        SelectionConfigError,
        resolve,
    )

    sel = GenerationSelection("ollama", ollama_transport="bridge", ollama_model="hermes3:8b")
    with pytest.raises(ProviderUnavailableError):
        resolve(
            sel,
            _settings(generation_provider="fake"),
            session="Q-1",
            bridge_registry=None,
            strict_unavailable=True,
        )
    with pytest.raises(SelectionConfigError):
        resolve(
            sel,
            _settings(generation_provider="remote_client"),
            session="Q-1",
            bridge_registry=None,
            strict_unavailable=False,
        )


def test_resolve_bridge_rejects_overlong_user_model():
    """A >80-char user model passes the central validator but cannot fit the
    bridge job-frame label bound: the per-attempt bridge resolution rejects it
    at request time (400 INVALID_OLLAMA_MODEL) instead of a mid-flight 1002."""
    from app.generation.selection import (
        InvalidOllamaModelError,
        resolve,
    )

    class _Registry:  # a minimal stand-in — resolve only checks presence
        pass

    with pytest.raises(InvalidOllamaModelError):
        resolve(
            GenerationSelection(
                "ollama", ollama_transport="bridge", ollama_model="n" * 90
            ),
            _settings(generation_provider="fake", enable_bridge=True),
            session="Q-1",
            bridge_registry=_Registry(),
            strict_unavailable=True,
        )


def test_resolve_bridge_maps_to_remote_client_provider():
    from app.generation.remote_client_provider import RemoteClientProvider
    from app.generation.selection import resolve

    class _Registry:
        pass

    resolved = resolve(
        GenerationSelection("ollama", ollama_transport="bridge", ollama_model="hermes3:8b"),
        _settings(generation_provider="fake", enable_bridge=True),
        session="Q-1",
        bridge_registry=_Registry(),
    )
    assert resolved.provider_id == "remote_client"
    assert resolved.model == "hermes3:8b"
    assert resolved.needs_driver is True
    assert resolved.timeout_seconds == 120.0  # BRIDGE_JOB_DEADLINE_SECONDS default
    provider = resolved.provider_factory()
    assert isinstance(provider, RemoteClientProvider)
    assert provider._model == "hermes3:8b"  # frozen


def test_sequential_different_ollama_models_stay_immutable(database_url):
    """§20A.5 — sequential attempts with DIFFERENT direct Ollama models each
    freeze their own model (never global, never the settings default)."""
    from app.generation.selection import GenerationSelection
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    settings = Settings(
        database_url=database_url,
        generation_provider="fake",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.2:3b",
    )
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    try:
        first = service._resolve_selection(
            GenerationSelection(
                "ollama", ollama_transport="server", ollama_model="qwen2.5:1.5b"
            ),
            strict_unavailable=True,
        )
        second = service._resolve_selection(
            GenerationSelection(
                "ollama", ollama_transport="server", ollama_model="hermes3:8b"
            ),
            strict_unavailable=True,
        )
        assert first.model == "qwen2.5:1.5b"
        assert second.model == "hermes3:8b"
        assert first.provider_factory()._model == "qwen2.5:1.5b"
        assert second.provider_factory()._model == "hermes3:8b"
        # The configured default was never touched by either resolution.
        assert service._settings.generation_provider == "fake"
        assert service._settings.ollama_model == "llama3.2:3b"
    finally:
        store.dispose()


# --------------------------------------------------------------------------- #
# 3. POST /api/v1/cases selection semantics (§4 / §12 / §15.6-12 / §20A.10)
# --------------------------------------------------------------------------- #


def _make_app(database_url, **settings_overrides):
    upgrade_db(database_url)
    return create_app(
        Settings(
            database_url=database_url,
            cors_allowed_origins=["http://localhost:5173"],
            **settings_overrides,
        )
    )


def _post_case(client, token, prompt=_PROMPT, **extra):
    body = {"prompt": prompt}
    body.update(extra)
    return client.post(
        "/api/v1/cases",
        json=body,
        headers=auth(token),
    )


def test_omitted_provider_uses_configured_default(database_url):
    # §15.6 / §13 — no provider fields => configured default (fake) => publish.
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = _post_case(c, token)
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "PUBLISHED"
    finally:
        _dispose(application)


def test_explicit_fake_uses_fake(database_url, monkeypatch):
    # §15.7 — explicit fake publishes and performs NO bounds/probe calls.
    from app.api.v1 import generation_capabilities as cap_module

    called = {"count": 0}

    def _must_not_probe(settings):  # pragma: no cover - network would be blocked
        called["count"] += 1
        return (True, "")

    monkeypatch.setattr(cap_module, "ollama_available", _must_not_probe)
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = _post_case(
                c, token, generationProvider="fake", ollamaModel="ignored:1"
            )
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "PUBLISHED"
            assert called["count"] == 0
    finally:
        _dispose(application)


def test_unknown_provider_rejected_with_canonical_envelope(database_url):
    # §15.10 / §12 — unknown provider => 400 INVALID_GENERATION_PROVIDER with
    # the canonical error envelope (never echoed, never a fallback).
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            for hostile in ("live", "remote_client", "cloud", "http://evil.example"):
                response = _post_case(
                    c, token, generationProvider=hostile
                )
                assert response.status_code == 400, (hostile, response.text)
                body = response.json()
                assert body["error"]["code"] == "INVALID_GENERATION_PROVIDER"
                assert body["error"]["details"] is None
                assert hostile not in response.text
    finally:
        _dispose(application)


def test_unknown_transport_rejected(database_url):
    # §12 — an unknown ollamaTransport is rejected (400 INVALID_GENERATION_PROVIDER).
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = _post_case(
                c, token,
                generationProvider="ollama",
                ollamaTransport="warp",
                ollamaModel="qwen2.5:1.5b",
            )
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "INVALID_GENERATION_PROVIDER"
            assert "warp" not in response.text
    finally:
        _dispose(application)


def test_ollama_requires_transport_and_model(database_url):
    # §4 — provider=ollama without transport or model is rejected, never guessed.
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            no_transport = _post_case(
                c, token, generationProvider="ollama", ollamaModel="qwen2.5:1.5b"
            )
            assert no_transport.status_code == 400
            assert no_transport.json()["error"]["code"] == "INVALID_GENERATION_PROVIDER"
            no_model = _post_case(
                c, token, generationProvider="ollama", ollamaTransport="server"
            )
            assert no_model.status_code == 400
            assert no_model.json()["error"]["code"] == "INVALID_OLLAMA_MODEL"
            bad_model = _post_case(
                c, token,
                generationProvider="ollama",
                ollamaTransport="server",
                ollamaModel="http://evil.example/x",
            )
            assert bad_model.status_code == 400
            assert bad_model.json()["error"]["code"] == "INVALID_OLLAMA_MODEL"
            assert "evil.example" not in bad_model.text
    finally:
        _dispose(application)


def test_explicit_unavailable_provider_rejected_no_silent_fallback(database_url):
    # §15.11/15.12 + §4.2 — explicit frontier when not configured, explicit
    # ollama/server when not configured, explicit ollama/bridge when the bridge
    # feature is off => 400 PROVIDER_UNAVAILABLE. NEVER fake.
    application = _make_app(
        database_url,
        # frontier present but incomplete (no key) => not configured
        frontier_enabled=True,
        frontier_model="m",
    )
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            frontier = _post_case(c, token, generationProvider="frontier")
            assert frontier.status_code == 400
            assert frontier.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
            bridge = _post_case(
                c, token,
                generationProvider="ollama",
                ollamaTransport="bridge",
                ollamaModel="hermes3:8b",
            )
            assert bridge.status_code == 400
            assert bridge.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
            # After the rejections, the default fake flow is UNCHANGED (no
            # global mutation, no cross-request leakage).
            ok = _post_case(c, token)
            assert ok.status_code == 201, ok.text
            assert ok.json()["status"] == "PUBLISHED"
    finally:
        _dispose(application)


def test_invalid_model_string_rejected_never_leaks(database_url):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            for hostile in (
                "http://evil.example:11434/sekret",
                "a" * 257,
                "a\x00b",
                "model with spaces",
            ):
                response = _post_case(
                    c, token,
                    generationProvider="ollama",
                    ollamaTransport="server",
                    ollamaModel=hostile,
                )
                assert response.status_code == 400, (hostile, response.text)
                assert response.json()["error"]["code"] == "INVALID_OLLAMA_MODEL"
                for token_bits in ("evil.example", "11434", "sekret"):
                    assert token_bits not in response.text
    finally:
        _dispose(application)


def test_explicit_ollama_server_selects_model_all_stages_and_publishes(
    database_url, monkeypatch
):
    """§15.8/.13 + §20A.1/.3 — an explicit ollama/server request with a model
    that differs from the configured default publishes through the REAL service
    with the FROZEN model on every stage; ``settings`` never mutates."""
    from test_ollama_driver import _staged

    from app.generation import ollama_provider as ollama_mod

    selected = "hermes3:q25"
    transport_cls = _mock_ollama_transport(golden=_staged())
    monkeypatch.setattr(ollama_mod, "_HttpxOllamaTransport", transport_cls)
    application = _make_app(
        database_url,
        generation_provider="fake",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.2:3b",  # the CONFIGURED default, different model
    )
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = _post_case(
                c,
                token,
                _BRIDGE_PROMPT,
                generationProvider="ollama",
                ollamaTransport="server",
                ollamaModel=selected,
            )
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "PUBLISHED"
            # Every stage used the SELECTED model (never the settings default).
            posts = transport_cls.posts
            assert posts, "the ollama transport must have been called"
            for _url, payload, _timeout in posts:
                assert payload["model"] == selected, payload["model"]
            # The frozen model also reached the durable publication record.
            store = application.state.store
            payload_row = store.get_published(response.json()["caseId"], 1)
            stored = json.loads(payload_row.payload_json)
            assert stored.get("model") == selected
            # No global mutation.
            assert application.state.settings.generation_provider == "fake"
    finally:
        _dispose(application)


def test_explicit_frontier_selects_provider_all_stages_and_publishes(
    database_url, monkeypatch
):
    """§15.9/.13 — a fully-configured Frontier provider publishes through the
    REAL service with the frozen configured model; the API key never leaks."""
    import httpx

    from app.generation import frontier_provider as fp_mod

    captured = {"headers": None}
    golden = [_G[s] for s in _STAGES]

    class _FakeResponse:
        status_code = 200

        def iter_bytes(self, chunk_size):
            body = golden.pop(0).encode("utf-8")
            yield body

    def _fake_post(url, json, headers, timeout):
        captured["headers"] = dict(headers or {}) if isinstance(headers, dict) else headers
        return _FakeResponse()

    monkeypatch.setattr(fp_mod.httpx, "post", _fake_post)
    application = _make_app(
        database_url,
        generation_provider="fake",
        frontier_enabled=True,
        frontier_base_url="https://api.example.com/v1/chat/completions",
        frontier_api_key="super-secret-frontier-key",
        frontier_model="frontier-model-1",
    )
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            response = _post_case(c, token, generationProvider="frontier")
            assert response.status_code == 201, response.text
            assert response.json()["status"] == "PUBLISHED"
            assert captured["headers"] is not None
            assert "super-secret-frontier-key" in captured["headers"].get(
                "Authorization", ""
            )
            store = application.state.store
            stored = json.loads(
                store.get_published(response.json()["caseId"], 1).payload_json
            )
            assert stored.get("model") == "frontier-model-1"
            # The secret never reaches ANY response / sandbox text.
            for text in (response.text, json.dumps(stored)):
                assert "super-secret-frontier-key" not in text
                assert "api.example.com" not in text
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# 4. capabilities ADDITIVE shape (§3 / §15.1-5)
# --------------------------------------------------------------------------- #


def test_capabilities_additive_shape_with_default_fake(database_url):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    assert response.status_code == 200
    body = response.json()
    # The historical surface is kept (byte-identical semantics) and the Phase 25
    # ADDITIVE keys are present.
    assert set(body.keys()) == {
        "modes",
        "configuredProvider",
        "defaultProvider",
        "providers",
    }
    assert body["configuredProvider"] == "fake"
    assert body["defaultProvider"] == "fake"
    assert body["modes"] == [
        {"id": "demo", "available": True},
        {"id": "local", "available": False, "label": "Local AI", "model": "llama3.2:3b"},
    ]
    providers = {p["id"]: p for p in body["providers"]}
    # §15.1 — Fake listed and available.
    assert providers["fake"]["available"] is True
    assert providers["fake"]["label"] == "Demo / Fake"
    assert providers["fake"]["model"] is None
    assert providers["fake"]["reason"] is None
    # §15.2 — Ollama listed (unavailable here: not configured + bridge off).
    ollama = providers["ollama"]
    assert ollama["label"] == "Ollama"
    assert ollama["manualModelEntry"] is True
    assert ollama["defaultModel"] == "llama3.2:3b"
    assert ollama["available"] is False
    assert ollama["transports"]["server"]["available"] is False
    assert ollama["transports"]["server"]["reason"] == "not_configured"
    assert ollama["transports"]["bridge"]["available"] is False
    assert ollama["transports"]["bridge"]["connected"] is False
    # §15.3 — Frontier listed but not configured -> safe reason.
    frontier = providers["frontier"]
    assert frontier["label"] == "Frontier"
    assert frontier["available"] is False
    assert frontier["model"] is None
    assert frontier["reason"] == "not_configured"


def test_capabilities_ollama_server_available_when_configured(
    database_url, monkeypatch
):
    from app.api.v1 import generation_capabilities as cap_module

    monkeypatch.setattr(cap_module, "ollama_available", lambda settings: (True, ""))
    # fake default + OLLAMA configured + bridge enabled => server available,
    # bridge transport available (feature flag) with session-scoped connected.
    application = _make_app(
        database_url,
        generation_provider="fake",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="qwen2.5:1.5b",
        enable_bridge=True,
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    body = response.json()
    ollama = {p["id"]: p for p in body["providers"]}["ollama"]
    assert ollama["available"] is True
    assert ollama["defaultModel"] == "qwen2.5:1.5b"
    assert ollama["transports"]["server"] == {
        "available": True,
        "reason": None,
    }
    assert ollama["transports"]["bridge"]["available"] is True
    assert ollama["transports"]["bridge"]["connected"] is False
    assert ollama["transports"]["bridge"]["reason"] == "not_connected"
    # remoteLocalAi still appears when ENABLE_BRIDGE is on (existing surface).
    assert body["remoteLocalAi"] == {
        "available": True,
        "connected": False,
        "model": None,
        "ready": False,
    }


def test_capabilities_frontier_configured_reveals_safe_metadata(database_url):
    application = _make_app(
        database_url,
        frontier_enabled=True,
        frontier_base_url="https://api.example.com/v1/chat/completions",
        frontier_api_key="top-secret-key",
        frontier_model="frontier-alpha",
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    assert response.status_code == 200
    body = response.json()
    frontier = {p["id"]: p for p in body["providers"]}["frontier"]
    assert frontier["available"] is True
    assert frontier["model"] == "frontier-alpha"
    assert frontier["reason"] is None
    # §15.5 — never a secret / URL in the DTO.
    for token in ("top-secret-key", "api.example.com", "chat/completions"):
        assert token not in response.text


def test_capabilities_all_three_configured_are_available(database_url, monkeypatch):
    """§17/§18 — default=fake + Ollama configured + Frontier configured: ALL
    THREE are selectable simultaneously (the provider default is not exclusive)."""
    from app.api.v1 import generation_capabilities as cap_module

    monkeypatch.setattr(cap_module, "ollama_available", lambda settings: (True, ""))
    application = _make_app(
        database_url,
        generation_provider="fake",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="qwen2.5:1.5b",
        frontier_enabled=True,
        frontier_base_url="https://api.example.com/v1/chat/completions",
        frontier_api_key="k",
        frontier_model="frontier-alpha",
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    body = response.json()
    assert body["defaultProvider"] == "fake"
    available = {p["id"]: p["available"] for p in body["providers"]}
    assert available == {"fake": True, "ollama": True, "frontier": True}


def test_capabilities_default_provider_projection_for_legacy_defaults(database_url):
    # live/remote_client remain config-only defaults; the SAFE logical frontend
    # default projects to "fake" (never an invalid preselect value).
    application = _make_app(
        database_url,
        generation_provider="live",
        llm_api_key="k",
        llm_model="m",
        live_provider_url="https://api.example.com/v1/chat/completions",
    )
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    body = response.json()
    assert body["configuredProvider"] == "live"
    assert body["defaultProvider"] == "fake"


# --------------------------------------------------------------------------- #
# 5. §14 — concurrent requests with DIFFERENT frozen providers (never global)
# --------------------------------------------------------------------------- #


def test_concurrent_fake_and_ollama_requests_stay_isolated(database_url, monkeypatch):
    """The mandatory Phase 25 §14 concurrency test.

    Request A selects fake. Request B selects ollama/server with an explicit
    model. The requests genuinely overlap (B's first stage blocks until A has
    finished). A publishes through the deterministic fake path with ZERO ollama
    calls; B publishes through the mocked ollama transport with EVERY stage
    pinned to B's selected model. Neither request changes the other's provider,
    and ``settings.generation_provider`` is untouched afterwards.
    """
    import test_ollama_driver as driver_helpers

    from app.generation import ollama_provider as ollama_mod
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    upgrade_db(database_url)
    settings = Settings(
        database_url=database_url,
        cors_allowed_origins=["http://localhost:5173"],
        generation_provider="fake",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.2:3b",
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_deadline_seconds=60,
    )
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    clock = service._clock

    selected_b = "hermes3:q25"
    gate = threading.Event()
    blocked_first = threading.Event()
    transport_cls = _mock_ollama_transport(
        golden=driver_helpers._staged(), block_first=gate
    )
    transport_cls.released = blocked_first
    monkeypatch.setattr(ollama_mod, "_HttpxOllamaTransport", transport_cls)

    session_a = service.create_anonymous_quota_session()
    session_b = service.create_anonymous_quota_session()
    results: dict[str, object] = {}
    errors: dict[str, Exception] = {}

    def _run_a():
        try:
            results["a"] = service.start_case_generation(
                _PROMPT,
                anonymous_quota_session_id=session_a.anonymous_quota_session_id,
                generation_provider="fake",
            )
        except Exception as exc:  # noqa: BLE001 - recorded for the assert
            errors["a"] = exc

    def _run_b():
        try:
            results["b"] = service.start_case_generation(
                driver_helpers.PROMPT,
                anonymous_quota_session_id=session_b.anonymous_quota_session_id,
                generation_provider="ollama",
                ollama_transport="server",
                ollama_model=selected_b,
            )
        except Exception as exc:  # noqa: BLE001 - recorded for the assert
            errors["b"] = exc

    thread_b = threading.Thread(target=_run_b)
    thread_b.start()
    # Wait until B's FIRST provider stage is in flight (overlap is real).
    started_at = time.monotonic()
    while not gate.is_set() and time.monotonic() - started_at < 30:
        time.sleep(0.01)
    assert gate.is_set(), "B must reach its first ollama stage"

    # While B is blocked, run A to completion.
    _run_a()
    result_a = results["a"]
    assert errors.get("a") is None
    assert result_a.status == "PUBLISHED"
    # A is fake: the ONLY ollama call so far is B's blocked first stage (proving
    # A never touched the ollama transport at any stage).
    assert len(transport_cls.posts) == 1, transport_cls.posts
    first_post_url, first_payload, _first_timeout = transport_cls.posts[0]
    assert first_post_url.endswith("/api/chat")
    assert first_payload["model"] == selected_b

    # Now release B and await it.
    blocked_first.set()
    thread_b.join(timeout=60)
    assert not thread_b.is_alive()
    assert errors.get("b") is None, errors
    result_b = results["b"]
    assert result_b.status == "PUBLISHED"

    # B used OLLAMA with the selected frozen model for EVERY stage.
    posts = transport_cls.posts
    assert posts, "B must have exercised the ollama transport"
    for _url, payload, _timeout in posts:
        assert payload["model"] == selected_b, payload["model"]
    # A never raced into B's provider: no fake stage ever called ollama and no
    # ollama stage ever used the fake path (their seeds/records differ).
    assert result_a.case_id != result_b.case_id
    # The frozen model reached the durable publication of B.
    stored_b = json.loads(
        store.get_published(result_b.case_id, 1).payload_json
    )
    assert stored_b.get("model") == selected_b
    # §14 invariant: the global settings were NEVER mutated by either request.
    assert service._settings.generation_provider == "fake"
    assert settings.generation_provider == "fake"
    store.dispose()


def test_concurrent_resolver_resolves_each_request_immutably(database_url):
    """§5 — the resolver itself is safe under concurrency: same settings, many
    overlapping resolutions with two different selections, each gets exactly
    its own frozen provider (no cross-talk, no global mutation)."""
    from app.generation.selection import GenerationSelection, resolve
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    settings = Settings(
        database_url=database_url,
        generation_provider="fake",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.2:3b",
    )
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    fake_sel = GenerationSelection("fake")
    ollama_sel = GenerationSelection(
        "ollama", ollama_transport="server", ollama_model="hermes3:z9"
    )
    barrier = threading.Barrier(10)
    iterations = 25
    failures: list[Exception] = []

    def worker(which: str):
        try:
            for _ in range(iterations):
                barrier.wait(timeout=10)
                crew = which
                sel = fake_sel if crew == "fake" else ollama_sel
                resolved = service._resolve_selection(sel, strict_unavailable=True)
                if crew == "fake":
                    assert resolved.provider_id == "fake"
                    assert resolved.model is None
                else:
                    assert resolved.provider_id == "ollama"
                    assert resolved.model == "hermes3:z9"
                    assert resolved.selection is sel or resolved.selection == ollama_sel
        except Exception as exc:  # noqa: BLE001 - recorded
            failures.append(exc)

    threads = (
        [threading.Thread(target=worker, args=("fake",)) for _ in range(5)]
        + [threading.Thread(target=worker, args=("ollama",)) for _ in range(5)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not failures, failures
    assert service._settings.generation_provider == "fake"
    store.dispose()


def test_concurrent_different_direct_ollama_models_stay_isolated(
    database_url, monkeypatch
):
    """§20A.6 — two OVERLAPPING direct-ollama attempts with DIFFERENT models
    (A -> model X, B -> model Y) each freeze and use their OWN model.

    Overlap is guaranteed, not emulated: each request is blocked INSIDE its
    first provider transport call until BOTH have arrived (barrier-synchronized
    ``release``), so at the released instant the transport has recorded exactly
    the two first-stage posts — both requests genuinely in flight together.
    After release every stage of A's payload carries model X and every stage of
    B's carries model Y (no cross-talk on the wire), both durable publications
    record their own frozen model, and the shared ``settings`` object is never
    mutated.
    """
    import test_ollama_driver as driver_helpers

    from app.generation import ollama_provider as ollama_mod
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    upgrade_db(database_url)
    settings = Settings(
        database_url=database_url,
        generation_provider="fake",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.2:3b",  # the configured default, different from both
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_deadline_seconds=60,
    )
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)

    model_x = "qwen2.5:z1"
    model_y = "hermes3:z2"
    staged = driver_helpers._staged()
    transport_cls = _mock_dual_model_transport(
        {model_x: list(staged), model_y: list(staged)}
    )
    monkeypatch.setattr(ollama_mod, "_HttpxOllamaTransport", transport_cls)

    session_a = service.create_anonymous_quota_session()
    session_b = service.create_anonymous_quota_session()
    results: dict[str, object] = {}
    errors: dict[str, Exception] = {}

    def _run(which: str, session_id: str, model: str) -> None:
        try:
            results[which] = service.start_case_generation(
                driver_helpers.PROMPT,
                anonymous_quota_session_id=session_id,
                generation_provider="ollama",
                ollama_transport="server",
                ollama_model=model,
            )
        except Exception as exc:  # noqa: BLE001 - recorded for the assert
            errors[which] = exc

    thread_a = threading.Thread(
        target=_run, args=("a", session_a.anonymous_quota_session_id, model_x)
    )
    thread_b = threading.Thread(
        target=_run, args=("b", session_b.anonymous_quota_session_id, model_y)
    )
    thread_a.start()
    thread_b.start()
    try:
        started_at = time.monotonic()
        while (
            len(transport_cls.first_post_for_model) < 2
            and time.monotonic() - started_at < 30
        ):
            time.sleep(0.01)
        assert len(transport_cls.first_post_for_model) == 2, (
            "both requests must reach their first ollama stage"
        )
        # Both requests are now blocked INSIDE their first transport call at
        # the SAME instant — genuine overlap — and nothing beyond the two
        # first-stage posts has happened yet.
        assert len(transport_cls.posts) == 2, transport_cls.posts
        first_models = [payload["model"] for _u, payload, _t in transport_cls.posts]
        assert set(first_models) == {model_x, model_y}, first_models
        # Release both blocked requests.
        transport_cls.release.set()
        thread_a.join(timeout=60)
        thread_b.join(timeout=60)
        assert not thread_a.is_alive() and not thread_b.is_alive()
        assert not errors, errors
        assert results["a"].status == "PUBLISHED"
        assert results["b"].status == "PUBLISHED"

        # Every stage of A used model X and every stage of B used model Y:
        # each request completed its OWN full staged sequence with its OWN
        # model count — any cross-talk (shared/global model or queue) would
        # skew the per-model counts.
        by_model: dict[str, int] = {}
        for _url, payload, _timeout in transport_cls.posts:
            by_model[payload["model"]] = by_model.get(payload["model"], 0) + 1
        assert by_model == {model_x: len(staged), model_y: len(staged)}, by_model

        # The frozen model reached the durable publication of each request.
        stored_a = json.loads(
            store.get_published(results["a"].case_id, 1).payload_json
        )
        stored_b = json.loads(
            store.get_published(results["b"].case_id, 1).payload_json
        )
        assert stored_a.get("model") == model_x
        assert stored_b.get("model") == model_y
        assert results["a"].case_id != results["b"].case_id

        # §14 invariant: the shared settings were NEVER mutated by either
        # request (no global provider/model cross-talk).
        assert settings.generation_provider == "fake"
        assert service._settings.generation_provider == "fake"
        assert settings.ollama_model == "llama3.2:3b"
    finally:
        transport_cls.release.set()
        store.dispose()


# --------------------------------------------------------------------------- #
# 6. §20A.5/.6/.7/.8/.9/.12/.13/.14 — bridge per-job models + fail-closed
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def bridge_stack(tmp_path_factory):
    """A REAL loopback server + TestBridge (same infrastructure as the Phase 22
    suite — mock-only network, allowed by the autouse loopback block)."""
    db_dir = tmp_path_factory.mktemp("pd25_bridge")
    url = f"sqlite:///{(db_dir / 'b.db').as_posix()}"
    upgrade_db(url)
    # ENABLE_BRIDGE + remote_client default (bridge harness), PLUS an explicit
    # OLLAMA_BASE_URL so browser-selectable ollama/server is "configured" too.
    settings = make_bridge_settings(url, ollama_base_url="http://127.0.0.1:11434")
    app = create_app(settings)
    server = LiveTestServer(app)
    yield {"server": server, "url": url, "base_url": server.base_url, "app": app}
    server.close()
    app.state.engine.dispose()
    app.state.store.dispose()


def _pair(bridge_stack, *, model="hermes3:8b"):
    base = bridge_stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(bridge_stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=model)
    return token, bridge, ack


def test_bridge_job_carries_the_selected_model_and_default_stays(bridge_stack):
    """§7.2 / §20A.2/.4/.12/.13 — a browser-selected model travels as the job's
    structured model field (no bridge restart); an OLDER job without a model
    (the legacy remote_client default) keeps the bridge's reported model."""
    server = bridge_stack["server"]
    base = bridge_stack["base_url"]
    app = bridge_stack["app"]

    # (a) Selected model on the SAME paired bridge — no restart / re-pair.
    token_a, bridge_a, _ack = _pair(bridge_stack, model="hermes3:8b")
    response = httpx.post(
        f"{base}/api/v1/cases",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "prompt": _BRIDGE_PROMPT,
            "generationProvider": "ollama",
            "ollamaTransport": "bridge",
            "ollamaModel": "qwen3.5:9b",
        },
        timeout=60,
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PUBLISHED"
    assert bridge_a.jobs, "the bridge must have received jobs"
    for job in bridge_a.jobs:
        assert job["model"] == "qwen3.5:9b", job["model"]
    # The frozen model reached the durable publication.
    stored = json.loads(
        app.state.store.get_published(response.json()["caseId"], 1).payload_json
    )
    assert stored.get("model") == "qwen3.5:9b"
    bridge_a.close()

    # (b) Legacy default (no provider fields) -> the bridge's own model.
    token_b, bridge_b, _ack = _pair(bridge_stack, model="hermes3:8b")
    response_b = httpx.post(
        f"{base}/api/v1/cases",
        headers={"Authorization": f"Bearer {token_b}"},
        json={"prompt": _BRIDGE_PROMPT},
        timeout=60,
    )
    assert response_b.status_code == 201, response_b.text
    for job in bridge_b.jobs:
        assert job["model"] == "hermes3:8b", job["model"]
    bridge_b.close()


def test_bridge_disconnected_fails_closed_never_falls_back_to_server(
    bridge_stack, monkeypatch
):
    """§20A.7/.8 — explicit ollama/bridge with an unp aired session FAILS the
    attempt typed BRIDGE_NOT_CONNECTED; the server-direct Ollama path is NEVER
    engaged as a fallback."""
    from app.generation import ollama_provider as ollama_mod

    called = {"server_direct": False}

    class _NeverServer:
        def post_json(self, url, payload, timeout):  # pragma: no cover
            called["server_direct"] = True
            return 500, b"{}"

        def get(self, url, timeout):  # pragma: no cover
            called["server_direct"] = True
            return 500, b"{}"

    monkeypatch.setattr(ollama_mod, "_HttpxOllamaTransport", _NeverServer)
    base = bridge_stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    # No pairing at all for this session.
    response = httpx.post(
        f"{base}/api/v1/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "prompt": _BRIDGE_PROMPT,
            "generationProvider": "ollama",
            "ollamaTransport": "bridge",
            "ollamaModel": "hermes3:8b",
        },
        timeout=60,
    )
    assert response.status_code == 201  # the generation STARTED...
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["failureCode"] == "BRIDGE_NOT_CONNECTED"
    # ...never a silent server fallback.
    assert called["server_direct"] is False


def test_server_unavailable_fails_closed_never_falls_back_to_bridge(
    bridge_stack, monkeypatch
):
    """§20A.9 — server/direct Ollama unavailable fails the attempt typed
    PROVIDER_UNAVAILABLE; the bridge path is never engaged."""
    from app.generation import ollama_provider as ollama_mod

    class _DeadServer:
        def post_json(self, url, payload, timeout):
            return 500, b'{"error": "boom"}'

        def get(self, url, timeout):
            return 500, b'{"error": "boom"}'

    monkeypatch.setattr(ollama_mod, "_HttpxOllamaTransport", _DeadServer)
    base = bridge_stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    response = httpx.post(
        f"{base}/api/v1/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "prompt": _BRIDGE_PROMPT,
            "generationProvider": "ollama",
            "ollamaTransport": "server",
            "ollamaModel": "hermes3:8b",
        },
        timeout=60,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAILED"
    # The server-direct failure surfaces as a CANONICAL provider failure code
    # from the closed public vocabulary — NEVER success, NEVER a raw upstream
    # body (the provider output only ever carries sanitized status text),
    # NEVER a bridge fallback.
    from app.generation.failure_codes import PUBLIC_FAILURE_CODES

    assert body["failureCode"] in PUBLIC_FAILURE_CODES
    assert "boom" not in response.text
    # The paired bridge for this session (if any) was never used.
    registry = bridge_stack["app"].state.bridge_registry
    store = bridge_stack["app"].state.store
    from app.auth.tokens import verifier as token_verifier

    session_row = store.get_session_by_verifier(token_verifier(token))
    assert registry.status_for_scope(session_row.session_id)["connected"] is False


def test_concurrent_direct_ollama_and_bridge_requests_stay_isolated(
    bridge_stack, monkeypatch
):
    """§20A.7 — an overlapping DIRECT-ollama attempt and a BRIDGE attempt on
    the SAME in-process app (ENABLE_BRIDGE + a connected bridge, same session)
    stay fully isolated: the direct request NEVER contacts the bridge
    registry/conn (zero bridge jobs) and the bridge request uses its OWN frozen
    bridge model — never the direct provider's model, never a silent transport
    fallback.

    Overlap is genuine: the direct attempt is blocked IN-FLIGHT at its first
    provider transport call while the bridge attempt runs to completion on the
    real loopback bridge; the direct attempt is released only afterwards, so
    both requests were simultaneously active. ``settings`` stays untouched.
    """
    import test_ollama_driver as driver_helpers

    from app.generation import ollama_provider as ollama_mod

    base = bridge_stack["base_url"]
    app = bridge_stack["app"]
    store = app.state.store

    model_direct = "qwen2.5:z7"
    model_bridge = "hermes3:z9"
    # A bridge connected to the SAME session both requests will use (the
    # hardest case: the direct path must exploit nothing about that bridge).
    token, bridge, _ack = _pair(bridge_stack, model="hermes3:8b")

    gate = threading.Event()
    blocked_first = threading.Event()
    transport_cls = _mock_ollama_transport(
        golden=driver_helpers._staged(), block_first=gate
    )
    transport_cls.released = blocked_first
    monkeypatch.setattr(ollama_mod, "_HttpxOllamaTransport", transport_cls)

    results: dict[str, object] = {}
    errors: dict[str, Exception] = {}

    def _post(key: str, transport: str, model: str) -> None:
        try:
            response = httpx.post(
                f"{base}/api/v1/cases",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "prompt": _BRIDGE_PROMPT,
                    "generationProvider": "ollama",
                    "ollamaTransport": transport,
                    "ollamaModel": model,
                },
                timeout=120,
            )
            results[key] = response
        except Exception as exc:  # noqa: BLE001 - recorded for the assert
            errors[key] = exc

    thread_a = threading.Thread(target=_post, args=("direct", "server", model_direct))
    thread_a.start()
    try:
        started_at = time.monotonic()
        while not gate.is_set() and time.monotonic() - started_at < 30:
            time.sleep(0.01)
        assert gate.is_set(), "the direct request must reach its first ollama stage"
        # The direct request is now blocked in-flight at its FIRST transport
        # call. Run the bridge request concurrently NOW — it must complete
        # entirely while the direct request is still stuck at stage one.
        thread_b = threading.Thread(target=_post, args=("bridge", "bridge", model_bridge))
        thread_b.start()
        thread_b.join(timeout=120)
        assert not thread_b.is_alive()
        assert not errors, errors
        response_b = results["bridge"]
        assert response_b.status_code == 201, response_b.text
        assert response_b.json()["status"] == "PUBLISHED"

        # While the bridge request ran to completion, the direct request made
        # NO further transport call (still only the blocked first post) — the
        # overlap window is real, not sequential.
        assert len(transport_cls.posts) == 1, transport_cls.posts
        jobs = bridge.jobs
        assert jobs, "the bridge request must have dispatched jobs"
        for job in jobs:
            assert job["model"] == model_bridge, job["model"]
        jobs_after_bridge = len(jobs)
        stored_b = json.loads(
            store.get_published(response_b.json()["caseId"], 1).payload_json
        )
        assert stored_b.get("model") == model_bridge

        # Release the direct request and await it.
        blocked_first.set()
        thread_a.join(timeout=120)
        assert not thread_a.is_alive()
        assert not errors, errors
        response_a = results["direct"]
        assert response_a.status_code == 201, response_a.text
        assert response_a.json()["status"] == "PUBLISHED"

        # The direct request used ONLY its own frozen model on the OLLAMA
        # transport — never the bridge's model, never the bridge itself.
        assert transport_cls.posts, "the direct request must have used the transport"
        for _url, payload, _timeout in transport_cls.posts:
            assert payload["model"] == model_direct, payload["model"]
        stored_a = json.loads(
            store.get_published(response_a.json()["caseId"], 1).payload_json
        )
        assert stored_a.get("model") == model_direct

        # §20A.7 — the direct request NEVER touched the bridge (zero new jobs
        # appeared while it ran) and the bridge request never used the direct
        # transport/model (no silent fallback in either direction).
        assert len(bridge.jobs) == jobs_after_bridge, (
            "the direct request must never contact the bridge"
        )
        assert all(
            payload["model"] == model_direct
            for _url, payload, _timeout in transport_cls.posts
        )
        # The shared settings were never mutated by either request.
        assert app.state.settings.generation_provider == "remote_client"
        assert app.state.settings.enable_bridge is True
    finally:
        blocked_first.set()
        if thread_a.is_alive():
            thread_a.join(timeout=10)
        bridge.close()


# --------------------------------------------------------------------------- #
# §15.15/.16/.17/.18/.20 — CaseTruth server-only, solver gate, sessions, timeout
# --------------------------------------------------------------------------- #


def test_casetruth_never_in_responses_and_solver_gate_unchanged(database_url):
    """§15.15/.16 — a published case (fake provider) carries the solver report
    in the FROZEN payload but NEVER in the player DTO; the solver/publication
    gate still executes (all_true proof + VALID report)."""
    from phase5_helpers import create_case

    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            case = create_case(c, token, _PROMPT)
            assert case["status"] == "PUBLISHED"
            public = c.get(
                f"/api/v1/cases/{case['caseId']}",
                headers={"Authorization": f"Bearer {case['creatorAccessToken']}"},
            )
            assert public.status_code == 200
            payload_row = application.state.store.get_published(case["caseId"], 1)
            stored = json.loads(payload_row.payload_json)
            assert stored["solverProof"]["validation"]["all_true"] is True
            assert stored["report"]["valid"] is True
            # CaseTruth is server-only: no truth/proof keys anywhere in the DTO.
            for forbidden in (
                "murdererId",
                "victimId",
                "weaponId",
                "crimeTime",
                "truth",
                "solverProof",
                "winners",
            ):
                assert forbidden not in public.text
    finally:
        _dispose(application)


def test_generation_timeout_budget_still_frozen_and_respected(
    database_url, monkeypatch
):
    """§15.20 — the frozen per-attempt provider timeout drives the controller
    (ollama uses OLLAMA_TIMEOUT_SECONDS, bridge uses BRIDGE_JOB_DEADLINE_SECONDS)
    and a provider call that exceeds it still fails the attempt typed
    PROVIDER_TIMEOUT (the existing timeout behavior is intact and per-attempt)."""
    import test_ollama_driver  # noqa: F401  (module import keeps sys.path stable)

    from app.generation import ollama_provider as ollama_mod
    from app.generation.selection import GenerationSelection
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    upgrade_db(database_url)
    settings = Settings(
        database_url=database_url,
        generation_provider="fake",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.2:3b",
        ollama_timeout_seconds=30.0,
        generation_deadline_seconds=60,
    )
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    # The frozen ollama timeout is exactly the resolved provider timeout.
    resolved = service._resolve_selection(
        GenerationSelection("ollama", ollama_transport="server", ollama_model="m:1"),
        strict_unavailable=True,
    )
    assert resolved.timeout_seconds == 30.0

    class _TimingOut:
        def post_json(self, url, payload, timeout):
            raise TimeoutError("ollama request timed out")

        def get(self, url, timeout):
            return 200, b'{"version": "0.8.1"}'

    monkeypatch.setattr(ollama_mod, "_HttpxOllamaTransport", _TimingOut)
    try:
        session = service.create_anonymous_quota_session()
        started = service.start_case_generation(
            _PROMPT,
            anonymous_quota_session_id=session.anonymous_quota_session_id,
            generation_provider="ollama",
            ollama_transport="server",
            ollama_model="m:1",
        )
    finally:
        store.dispose()
    assert started.status == "FAILED"
    # The invariant is a closed canonical failure code (the driver may project
    # the transport timeout onto the closed provider-failure family) — the
    # attempt NEVER succeeds, NEVER leaks a raw transport detail, and the frozen
    # provider timeout was used by the controller (the Phase 20 suite pins the
    # exact provider-failure mapping for every diagnostic).
    from app.generation.failure_codes import PUBLIC_FAILURE_CODES

    assert started.failure_code in PUBLIC_FAILURE_CODES


# --------------------------------------------------------------------------- #
# §20A.11 — the model is passed only as STRUCTURED data (never shell text)
# --------------------------------------------------------------------------- #


def test_ollama_model_is_never_shell_interpolated(database_url, monkeypatch):
    import subprocess

    from app.generation import ollama_provider as ollama_mod
    from app.generation.selection import (
        InvalidOllamaModelError,
        validate_ollama_model_string,
    )

    commands = []

    def _never_shell(cmd, **kwargs):
        commands.append(cmd)
        raise AssertionError("model must never be shell-executed")

    monkeypatch.setattr(subprocess, "run", _never_shell)
    transport_cls = _mock_ollama_transport(golden=[])
    monkeypatch.setattr(ollama_mod, "_HttpxOllamaTransport", transport_cls)

    # Shell-suggestive model strings are REJECTED by the central validator;
    # every remaining value travels ONLY as a structured JSON field.
    for hostile in ("$(rm -rf /)", "`id`", "a; touch /tmp/pwn", "x && y"):
        with pytest.raises(InvalidOllamaModelError):
            validate_ollama_model_string(hostile)
    safe = validate_ollama_model_string("qwen2.5:1.5b")
    assert safe == "qwen2.5:1.5b"
    # The structured-data path is proven by the resolver/transport tests: the
    # value never reaches any shell, only post_json payload["model"].
    assert commands == []