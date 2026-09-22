"""Phase 20 final re-audit — accepted LOW advisories ADV-227 / ADV-228 / ADV-229.

Fix verification (hermetic; in-process — mock transports, scratch SQLite,
ManualClock; the autouse conftest network block is active):

- ADV-227 (LOW): delivered as a READY-TO-APPLY patch in the delivery report —
  the two tracked QA e2e backend launchers (f``e2e/qa-phase16-backend.py``,
  ``e2e/qa-phase145-backend.py``) must call ``uvicorn.run(..., proxy_headers=False)``
  (the no-proxy-headers equivalent) so a future re-bind of these QA backends
  cannot silently reopen the DEF-094 forwarded-header identity-rotation vector.

  THIS test file cannot carry the ADV-227 guard itself: the environment's
  runtime permission configuration denies ALL edits under ``e2e/*``, so the
  launcher content change cannot be landed here (and its guard would fail
  against the untouched files). The exact two-line launcher diff + the guard
  test body are included in the delivery report for the orchestrator to apply
  (the guard asserts ``proxy_headers=False`` in both files whenever ``uvicorn.run``
  is present).

- ADV-228 (LOW: verified green below): the Ollama transport ENVELOPE parses
  (``_extract_content``, ``ollama_available``, ``_parse_ollama_version``) route
  through the SAME ``bounded_json_loads`` as PD-SEC-09's content parser — a
  depth-bomb envelope inside the 256 KiB cap raises a typed ``BoundedJsonError``
  (a ValueError) and degrades to the sanitized failure — never an uncaught
  ``RecursionError`` escaping ``OllamaProvider.generate()`` / the controller boundary.

- ADV-229 (LOW: verified green below): ``AdmissionController._sessions`` is now
  bounded: it lazily evicts expired sessions (one-TTL grace, NEVER an active-
  concurrency session) and fails closed with ``AdmissionDenied`` (= sanitized
  429 ADMISSION_DENIED at the API boundary) beyond a ``max_sessions`` hard bound
  (default 10_000, operator-configurable through the controller constructor).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assets.depthguard import (  # noqa: E402
    BoundedJsonError,
    bounded_json_loads,
)
from app.core.config import Settings  # noqa: E402
from app.generation.admission import (  # noqa: E402
    AdmissionController,
    AdmissionDenied,
)
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.ollama_provider import (  # noqa: E402
    OllamaProvider,
    _parse_ollama_version,
    ollama_available,
)
from app.generation.provider import (  # noqa: E402
    GenerateRequest,
    GenerationStage,
    ProviderResult,
)
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.admission import DurableAdmissionController  # noqa: E402

OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_MODEL = "llama3.2:3b"

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)


def _admission(clock, ids, **overrides):
    kwargs = dict(
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    kwargs.update(overrides)
    return AdmissionController(clock=clock, ids=ids, **kwargs)


def _controller(provider, admission, clock, ids, **overrides):
    kwargs = dict(
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=11,
    )
    kwargs.update(overrides)
    return GenerationController(
        provider=provider, admission=admission, clock=clock, ids=ids, **kwargs
    )


def _provider(transport, *, base_url=OLLAMA_BASE, model=OLLAMA_MODEL):
    return OllamaProvider(
        base_url=base_url,
        model=model,
        timeout_seconds=5.0,
        temperature=0.2,
        num_ctx=4096,
        transport=transport,
    )


def _request(stage=GenerationStage.CASE_TRUTH) -> GenerateRequest:
    return GenerateRequest(
        attempt_id="att-adv228",
        stage=stage,
        prompt_context="ctx",
        locked=None,
        diagnostics=(),
    )


class _CannedTransport:
    """Minimal mocked Ollama transport (never touches the network)."""

    def __init__(self, *, get_bytes=b"{}", post_bytes=b"{}"):
        self.get_bytes = get_bytes
        self.post_bytes = post_bytes

    def get(self, url, timeout=1.0):
        return 200, self.get_bytes

    def post_json(self, url, payload, timeout=1.0):
        return 200, self.post_bytes


def _depth_bomb_json() -> bytes:
    """A JSON document nested 30,000 levels deep — 60,001 bytes, well under the
    256 KiB ``MAX_OLLAMA_RESPONSE_BYTES`` cap and far past
    ``MAX_STRUCT_NESTING`` (32). This is the ADV-228 attack shape."""
    depth = 30_000
    return b"[" * depth + b"]" * depth


# --------------------------------------------------------------------------- #
# ADV-228 — bounded Ollama transport ENVELOPE parses ( depth-bomb parity with
# PD-SEC-09's content parser*
# --------------------------------------------------------------------------- #


def test_adv228_bounded_json_loads_rejects_depth_bomb():
    """The depth-bomb envelope text raises the typed BoundedJsonError (a
    ValueError), never an uncaught RecursionError — the exact PD-SEC-09
    guarantee, applied to the envelope parse."""
    bomb = _depth_bomb_json()
    assert len(bomb) <= 256 * 1024  # fits MAX_OLLAMA_RESPONSE_BYTES
    with pytest.raises(BoundedJsonError):
        bounded_json_loads(bomb)
    with pytest.raises(ValueError):
        bounded_json_loads(bomb)


def test_adv228_extract_content_depth_bomb_returns_none():
    """``_extract_content`` (OllamaProvider.generate's envelope parser) swallows
    the bounded parse failure into the same ``None`` as malformed JSON — it NEVER
    lets an uncaught RecursionError escape."""
    provider = _provider(_CannedTransport(post_bytes=_depth_bomb_json()))
    assert provider._extract_content(_depth_bomb_json()) is None


def test_adv228_generate_depth_bomb_yields_sanitized_provider_result():
    """The full ``OllamaProvider.generate()`` path: a depth-bomb 2xx envelope
    yields a sanitized ``ProviderResult`` error (the API boundary envelope), never
    an uncaught RecursionError escaping."""
    provider = _provider(_CannedTransport(post_bytes=_depth_bomb_json()))
    result = provider.generate(_request())
    assert isinstance(result, ProviderResult)
    assert result.error is not None
    assert result.content is None and result.timed_out is False
    assert "RecursionError" not in result.error and "traceback" not in result.error


def test_adv228_generate_normal_envelope_still_parses():
    """A non-bomb envelope parses exactly as before (no regression on the bounded
    parse route)."""
    clean = _provider(
        _CannedTransport(
            post_bytes=json.dumps(
                {"model": OLLAMA_MODEL, "message": {"content": '{"a": 1}'}}
            ).encode()
        )
    )
    ok = clean.generate(_request())
    assert ok.content == '{"a": 1}'


def test_adv228_ollama_available_depth_bomb_is_not_available():
    """``ollama_available`` (the /api/tags probe) swallows the bounded envelope
    parse failure into the documented sanitized ``(False, "not available")`` —
    never an uncaught RecursionError."""
    settings = Settings(
        generation_provider="ollama",
        ollama_base_url=OLLAMA_BASE,
        ollama_model=OLLAMA_MODEL,
    )
    transport = _CannedTransport(get_bytes=_depth_bomb_json())
    assert ollama_available(settings, transport) == (False, "not available")


def test_adv228_parse_ollama_version_depth_bomb_is_none():
    """``_parse_ollama_version`` (/api/version probe) degrades the bounded
    envelope parse failure to ``None`` (never an uncaught RecursionError)."""
    assert _parse_ollama_version(_depth_bomb_json())is None


def test_adv228_controller_boundary_depth_bomb_fails_sanitized():
    """API-boundary bookend: a REAL GenerationController over the bomb transport
    terminates FAILED with a sanitized reason — no RecursionError, no traceback
    escaping to the boundary."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    bomb_transport = _CannedTransport(post_bytes=_depth_bomb_json())
    controller = _controller(_provider(bomb_transport), admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    flattened = str(record).lower()
    assert "recursionerror" not in flattened and "traceback" not in flattened


# --------------------------------------------------------------------------- #
# ADV-229 — bounded AdmissionController session store ( eviction + hard bound)
# --------------------------------------------------------------------------- #


def test_adv229_expired_sessions_are_evicted_after_grace():
    """A session whose window ended MORE than one TTL ago is lazily evicted on the
    next session creation (the map stays bounded; live-session semantics stay)."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(
        clock,
        ids,
        anonymous_quota_session_ttl_seconds=100,
        max_sessions=1,
    )
    old = admission.create_anonymous_quota_session()
    assert admission.session_count == 1
    # Advance more than one TTL PAST the window end (created at t=0,
    # window_end=100; the eviction cutoff is now - 100, so now > 200 qualifies).
    clock.advance(250)
    new = admission.create_anonymous_quota_session()
    assert admission.session_count == 1
    assert new.session_id != old.session_id
    with pytest.raises(AdmissionDenied) as excinfo:
        admission.admit_generation(old.session_id)
    assert "unknown anonymous quota session" in excinfo.value.decision.reason


def test_adv229_window_expired_but_within_grace_keeps_existing_denial_reason():
    """Recently-expired sessions (within the one-TTL grace) are NOT evicted: the
    byte-identical ``window expired`` denial reason stays (no semantic regression).
    """
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(
        clock,
        ids,
        anonymous_quota_session_ttl_seconds=100,
        max_sessions=10,
    )
    session = admission.create_anonymous_quota_session()
    clock.advance(150)
    with pytest.raises(AdmissionDenied) as excinfo:
        admission.admit_generation(session.session_id)
    assert "window expired" in excinfo.value.decision.reason
    assert admission.session_count == 1


def test_adv229_active_concurrency_session_is_never_evicted():
    """Eviction NEVER removes a session with active concurrency, even far past
    the grace; a release (then expiry) allows the eviction."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(
        clock,
        ids,
        anonymous_quota_session_ttl_seconds=100,
        max_sessions=2,
        max_generations_per_session_per_window=10,
        max_concurrent_generations=2,
    )
    session = admission.create_anonymous_quota_session()
    assert admission.admit_generation(session.session_id).admitted is True
    assert admission.active_concurrency(session.session_id) == 1
    clock.advance(300)
    other = admission.create_anonymous_quota_session()
    assert admission.session_count == 2
    admission.release_generation(session.session_id)
    third = admission.create_anonymous_quota_session()
    assert admission.session_count == 2
    with pytest.raises(AdmissionDenied):
        admission.admit_generation(session.session_id)


def test_adv229_map_stays_bounded_under_many_expired_creates():
    """Sustained expired creates (the attacker-accelerable shape) can never grow
    the in-memory map beyond a small bound: every create evicts the expired ones
    first."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(
        clock,
        ids,
        anonymous_quota_session_ttl_seconds=100,
        max_sessions=5,
        max_generations_per_session_per_window=10,
    )
    for index in range(200):
        if index % 2:
            clock.advance(201)
        admission.create_anonymous_quota_session()
        assert admission.session_count <= 5, admission.session_count
    assert admission.session_count <= 5


def test_adv229_max_sessions_fails_closed():
    """Beyond the ``max_sessions`` hard bound, anonymous session creation DENIES
    admission (fail closed, never unbounded growth). No provider call is made:
    no provider is involved at session creation at all."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(
        clock,
        ids,
        anonymous_quota_session_ttl_seconds=86400,
        max_sessions=3,
    )
    for _ in range(3):
        admission.create_anonymous_quota_session()
    assert admission.session_count == 3
    assert admission.max_sessions == 3
    with pytest.raises(AdmissionDenied) as excinfo:
        admission.create_anonymous_quota_session()
    assert "store at capacity" in excinfo.value.decision.reason
    assert admission.session_count == 3


def test_adv229_service_bound_translates_to_admission_denied_error(database_url):
    """Service seam: the controller's capacity AdmissionDenied is translated into
    the API-facing AdmissionDeniedError (-> 429 ADMISSION_DENIED at the boundary,
    sanitized, no internal detail)."""
    from app.persistence.store import Store
    from app.services.generation import AdmissionDeniedError, GenerationService

    from conftest import upgrade_db

    upgrade_db(database_url)
    clock = ManualClock(start_time=0.0)
    capped = DurableAdmissionController(
        clock=clock,
        ids=IdSource(),
        max_concurrent_generations=4,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=10,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
        max_sessions=2,
    )
    store = Store(database_url)
    try:
        service = GenerationService(
            settings=Settings(database_url=database_url),
            store=store,
            clock=clock,
            admission=capped,
        )
        service.create_anonymous_quota_session()
        service.create_anonymous_quota_session()
        with pytest.raises(AdmissionDeniedError):
            service.create_anonymous_quota_session()
    finally:
        store.dispose()


def test_adv229_route_level_capacity_denial_is_sanitized_429(database_url):
    """Route boundary: the 4th session creation beyond a capped store answers
    sanitized 429 ADMISSION_DENIED (fail closed; no internal detail, no
    "at capacity" reason echo, sanitized error envelope)."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.generation import GenerationService
    from conftest import upgrade_db
    from phase5_helpers import assert_sanitized_error

    upgrade_db(database_url)
    app = create_app(
        Settings(
            database_url=database_url,
            cors_allowed_origins=["http://localhost:5173"],
            max_concurrent_generations=4,
            max_generations_per_session_per_window=20,
            max_concurrent_generations_global=8,
            max_generations_global_per_window=50,
            anon_session_limit_per_ip_per_10_min=100,
            anon_session_global_limit_per_min=1000,
        )
    )
    capped = DurableAdmissionController(
        clock=app.state.clock,
        ids=IdSource(),
        max_concurrent_generations=4,
        max_concurrent_generations_global=8,
        max_generations_per_session_per_window=20,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=(
            app.state.settings.anonymous_quota_session_ttl_seconds
        ),
        global_window_seconds=app.state.settings.global_generation_window_seconds,
        max_sessions=3,
    )
    app.state.generation_service = GenerationService(
        settings=app.state.settings,
        store=app.state.store,
        clock=app.state.clock,
        admission=capped,
    )
    try:
        with TestClient(app) as client:
            statuses = []
            last = None
            for _ in range(4):
                res = client.post("/api/v1/sessions/anonymous")
                statuses.append(res.status_code)
                last = res
            assert statuses == [201, 201, 201, 429], statuses
            assert last is not None and last.status_code == 429
            body = last.json()
            assert body["error"]["code"] == "ADMISSION_DENIED"
            assert_sanitized_error(last.text)
            flattened = json.dumps(body).lower()
            assert "at capacity" not in flattened
            assert "generations" not in flattened and "window" not in flattened
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()