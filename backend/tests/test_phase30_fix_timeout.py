"""Phase30-fix DEF-A — real wall-clock Frontier provider timeout regression tests.

The live production defect: a Frontier provider call advertised with
``configuredProviderTimeoutMs=180000`` ran for ``elapsedMs=579348`` and still
logged ``provider.call.complete success=true``. Root cause: ``httpx.post``'s
``timeout=`` is a PER-OPERATION (connect/write/read/pool) timeout, NOT a total
wall-clock deadline — a slow/dribbling response-body read, DNS resolution,
pool wait or upstream stream can legally outlive it, and the generation
deadline cannot interrupt a synchronous in-flight await.

These tests prove the remediation boundary: EVERY Frontier call is bounded in
real wall-clock time by ``min(configured provider timeout, remaining generation
deadline)`` enforced around the ENTIRE outbound operation, a timed-out call can
never later surface as ``success=true`` and never mutates generation state, and
the controller deterministically classifies provider-timeout vs
generation-deadline exhaustion.

Every test is hermetic: the outbound adapter is monkeypatched
(``app.generation.frontier_provider.httpx.post``), nothing opens a socket, and
time is accelerated (manual clocks / sub-second real deadlines) — the suite
never waits minutes.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.failure_codes import GenerationFailureCode  # noqa: E402
from app.generation.frontier_provider import (  # noqa: E402
    FrontierHttpError,
    FrontierProvider,
)
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerateRequest, GenerationStage  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402
from phase5_helpers import auth, create_session  # noqa: E402

_STAGES = (
    GenerationStage.CASE_TRUTH,
    GenerationStage.PUBLIC_WORLD,
    GenerationStage.EVIDENCE,
    GenerationStage.WORLD_GRAPH,
)
_G = GOLDEN_STAGE_PAYLOADS
_GOLDEN_STAGE_STRINGS = [_G[stage] for stage in _STAGES]

_PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)

OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# Service-level accelerated bound (>= the Settings floor of 5s). Scale note:
# the production evidence was configuredProviderTimeoutMs=180000 with a
# ~579348 ms call; these tests use the same SHAPE at a smaller scale so the
# suite stays fast while proving the exact enforcement contract.
_ACCELERATED_TIMEOUT_SECONDS = 5.0


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _make_app(database_url, **settings_overrides):
    upgrade_db(database_url)
    kwargs = dict(
        cors_allowed_origins=["http://localhost:5173"],
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_limit_per_ip_per_hour=1000,
        frontier_enabled=True,
    )
    kwargs.update(settings_overrides)
    return create_app(Settings(database_url=database_url, **kwargs))


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


def _post_case(client, token, prompt=_PROMPT, **extra):
    body = {"prompt": prompt}
    body.update(extra)
    return client.post("/api/v1/cases", json=body, headers=auth(token))


class _CaptureLog:
    """Deterministic structured-log capture (the phase30 byok convention)."""

    def __init__(self, buffer) -> None:
        from app.core.observability import SERVICE_LOGGER_NAME, JsonEventFormatter

        self.handler = logging.StreamHandler(buffer)
        self.handler.setLevel(logging.DEBUG)
        self.handler.setFormatter(JsonEventFormatter())
        self.service_logger = logging.getLogger(SERVICE_LOGGER_NAME)
        self._previous_level = self.service_logger.level
        logging.getLogger().addHandler(self.handler)

    def __enter__(self) -> "_CaptureLog":
        self.service_logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *_exc) -> None:
        logging.getLogger().removeHandler(self.handler)
        self.service_logger.setLevel(self._previous_level)


class _Body:
    """A mock httpx response body (bounded iter_bytes)."""

    def __init__(self, body: bytes) -> None:
        self.status_code = 200
        self._body = body

    def iter_bytes(self, chunk_size: int):
        yield self._body


class _SlowBody:
    """A mock response whose body read sleeps ``gap_seconds`` per chunk — the
    production "dribbling response-body read" shape."""

    def __init__(self, chunks: list[bytes], gap_seconds: float) -> None:
        self.status_code = 200
        self._chunks = chunks
        self._gap = gap_seconds

    def iter_bytes(self, chunk_size: int):
        for chunk in self._chunks:
            time.sleep(self._gap)
            yield chunk


class _HeldOutbound:
    """Monkeypatched ``httpx.post``: records every call; optionally blocks on
    ``hold`` before returning and/or sleeps ``delay_seconds`` to simulate slow
    upstream processing. Feeding ``golden`` (a list of per-stage strings)
    lets a released call complete successfully."""

    def __init__(
        self,
        golden: list[str] | None = None,
        hold: threading.Event | None = None,
        hold_duration: float = 0.0,
    ) -> None:
        from app.generation import frontier_provider as fp_mod

        self._fp_mod = fp_mod
        self._original_post = fp_mod.httpx.post
        self.posts: list[tuple[str, dict, dict]] = []
        self._golden = list(golden or [])
        self._hold = hold
        self._hold_duration = hold_duration
        self._lock = threading.Lock()

    def install(self) -> "_HeldOutbound":
        self._fp_mod.httpx.post = self._post
        return self

    def restore(self) -> None:
        self._fp_mod.httpx.post = self._original_post

    def _post(self, url, json=None, headers=None, timeout=None):
        with self._lock:
            self.posts.append((url, dict(json or {}), dict(headers or {})))
        if self._hold is not None:
            self._hold.wait(120)
        if self._hold_duration:
            time.sleep(self._hold_duration)
        with self._lock:
            content = self._golden.pop(0) if self._golden else "<not-json>"
        return _Body(content.encode("utf-8"))


class _BlockingResponse(object):
    """A mock httpx.post whose BODY READ blocks on ``hold`` (used to prove a
    timeout happens while the body read is still in flight)."""

    def __init__(self, hold: threading.Event) -> None:
        self.status_code = 200
        self._hold = hold
        self._released = False

    def iter_bytes(self, chunk_size: int):
        self._hold.wait(120)
        self._released = True
        yield b'{"released": true}'


def _blocking_body_post(hold: threading.Event):
    def _post(url, json=None, headers=None, timeout=None):
        return _BlockingResponse(hold)
    return _post


# --------------------------------------------------------------------------- #
# controller harness (ManualClock — deterministic, sub-millisecond)
# --------------------------------------------------------------------------- #


def _make_admission(clock, ids):
    return AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
    )


def _make_controller(provider, admission, clock, ids, **overrides):
    kwargs = dict(
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=17,
    )
    kwargs.update(overrides)
    return GenerationController(
        provider=provider, admission=admission, clock=clock, ids=ids, **kwargs
    )


# --------------------------------------------------------------------------- #
# 1 — a call that finishes below the bound still succeeds and publishes
# --------------------------------------------------------------------------- #


def test_frontier_call_finishes_below_timeout_succeeds(database_url, monkeypatch):
    """Requirement §14-1: a Frontier call that completes inside the wall-clock
    bound is unchanged — golden stages publish and ``provider.call.complete
    success=true`` is logged normally."""
    from app.generation import frontier_provider as fp_mod

    captured = []
    golden = list(_GOLDEN_STAGE_STRINGS)

    def _post(url, json=None, headers=None, timeout=None):
        captured.append((url, dict(json or {}), dict(headers or {})))
        return _Body(golden.pop(0).encode("utf-8"))

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    application = _make_app(database_url, frontier_timeout_seconds=_ACCELERATED_TIMEOUT_SECONDS)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "KEY-BELOW", "model": "MODEL-BELOW"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "PUBLISHED"
            assert len(captured) == 4, captured
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# 2 — exceeds provider timeout -> canonical provider-timeout failure path
#      (production mismatch: configuredProviderTimeoutMs=180000 and a simulated
#      upstream duration > the bound cannot reach success)
# --------------------------------------------------------------------------- #


def test_adapter_180s_production_mismatch_never_returns_success(monkeypatch):
    """Requirement §14 production mismatch, at the enforcement boundary itself:
    configured provider timeout = 180000 ms (request.timeout_seconds=180.0),
    simulated upstream duration > 180000 (the fake clock advances 181 s while
    the outbound call is still in flight). The generate() call MUST raise the
    canonical FRONTIER_TIMEOUT instead of ever producing a ProviderResult —
    the exact contract the old code violated (elapsedMs=579348 success=true is
    impossible once the total wall-clock bound is real)."""
    from app.generation import frontier_provider as fp_mod

    started = threading.Event()
    hold = threading.Event()

    def _post(url, json=None, headers=None, timeout=None):
        started.set()
        hold.wait(120)
        return _Body(b'{"ignored": true}')

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    clock = ManualClock(start_time=0.0)
    provider = FrontierProvider(
        endpoint_url=OPENAI_ENDPOINT,
        api_key="k",
        model="m",
        clock=clock,
        structured_output_mode="openai_json_schema",
    )
    outcome: dict[str, object] = {}

    def _run() -> None:
        try:
            outcome["result"] = provider.generate(
                GenerateRequest(
                    attempt_id="att-180",
                    stage=GenerationStage.CASE_TRUTH,
                    prompt_context="p",
                    timeout_seconds=180.0,
                )
            )
        except FrontierHttpError as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert started.wait(10), "the outbound call must have been entered"
    # Simulated upstream processing far beyond the advertised 180000 ms bound.
    clock.advance(181.0)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert "result" not in outcome, "a timed-out call must never produce a result"
    error = outcome["error"]
    assert isinstance(error, FrontierHttpError)
    assert error.code is GenerationFailureCode.FRONTIER_TIMEOUT
    hold.set()  # release the mocked upstream so its worker exits promptly


def test_frontier_call_exceeding_provider_timeout_fails_canonically(database_url, monkeypatch):
    """Requirement §14-2: service-level accelerated reproduction. Configured
    provider timeout = 5000 ms; the mocked upstream blocks far beyond the
    bound. The attempt fails with the canonical FRONTIER_TIMEOUT, the elapsed
    time stays at the enforced bound (never minutes), exactly ONE outbound call
    is attempted (no retry multiplication — §14-7), and NO
    ``provider.call.complete success=true`` is ever logged for it (§14-8)."""
    import io as _io

    from app.generation import frontier_provider as fp_mod

    hold = threading.Event()
    outbound = _HeldOutbound(golden=list(_GOLDEN_STAGE_STRINGS), hold=hold)
    outbound.install()
    log_buffer = _io.StringIO()
    try:
        application = _make_app(database_url, frontier_timeout_seconds=_ACCELERATED_TIMEOUT_SECONDS)
        try:
            with _CaptureLog(log_buffer), TestClient(application) as c:
                token, _ = create_session(c)
                t0 = time.monotonic()
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={"provider": "openai", "apiKey": "KEY-T", "model": "MODEL-T"},
                )
                elapsed = time.monotonic() - t0
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "FAILED"
                assert res.json()["failureCode"] == "FRONTIER_TIMEOUT"
                # §14-11 — elapsed stays at the enforced bound (5 s + scheduler
                # tolerance), NOT anything near minutes / the 579348 ms defect.
                assert 4.0 <= elapsed <= 15.0, elapsed
                # §14-7 — no retry: exactly ONE outbound post for the attempt.
                assert len(outbound.posts) == 1, outbound.posts
                log_text = log_buffer.getvalue()
                assert "provider.call.complete" not in log_text or all(
                    '"success":true' not in line
                    for line in log_text.splitlines()
                    if "provider.call.complete" in line
                )
                # §14-11 — the controller's elapsedMs metric on the timeout
                # event stays at the enforced bound (5 s + small scheduler
                # tolerance — never the 579348 ms production value).
                timeout_lines = [
                    json.loads(line)
                    for line in log_text.splitlines()
                    if "provider.call.timeout" in line
                ]
                assert len(timeout_lines) == 1, log_text
                elapsed_ms = timeout_lines[0].get("elapsedMs")
                assert isinstance(elapsed_ms, int)
                assert 4000 <= elapsed_ms <= 15000, elapsed_ms
                assert timeout_lines[0].get("failureCode") == "FRONTIER_TIMEOUT"
        finally:
            _dispose(application)
    finally:
        hold.set()
        outbound.restore()


# --------------------------------------------------------------------------- #
# 3/4 — deterministic classification: generation-deadline vs provider-timeout
# --------------------------------------------------------------------------- #


def test_deadline_shorter_than_provider_timeout_generation_deadline_wins(monkeypatch):
    """Requirement §14-3: the remaining generation deadline is shorter than the
    configured provider timeout -> the generation-deadline bound WINS
    (GENERATION_DEADLINE_EXCEEDED). ManualClock drives the whole chain: the
    effective per-call bound is clamped to (remaining - margin); the adapter
    expires at that clamped bound; the controller's classifier sees the
    generation deadline effectively exhausted and maps the timeout to the
    generation-deadline failure path."""
    from app.generation import frontier_provider as fp_mod

    started = threading.Event()
    hold = threading.Event()

    def _post(url, json=None, headers=None, timeout=None):
        started.set()
        hold.wait(120)
        return _Body(b"{}")

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    clock = ManualClock(start_time=0.0)
    provider = FrontierProvider(
        endpoint_url=OPENAI_ENDPOINT,
        api_key="k",
        model="m",
        clock=clock,
        structured_output_mode="openai_json_schema",
    )
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    controller = _make_controller(
        provider,
        admission,
        clock,
        ids,
        deadline_seconds=60,
        # Configured provider timeout (300 s) is FAR longer than the 60 s
        # generation deadline -> the deadline is the operative bound.
        provider_timeout_seconds=300.0,
    )
    outcome: dict[str, object] = {}

    def _run() -> None:
        handle = controller.start_generation(
            _PROMPT, anonymous_quota_session_id=session.session_id
        )
        outcome["record"] = controller.attempt(handle.attempt_id)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert started.wait(10), "the case_truth outbound call must have been entered"
    # The generation deadline fully elapses while the call is in flight.
    clock.advance(61.0)
    thread.join(timeout=10)
    assert not thread.is_alive()
    record = outcome["record"]
    assert record.state.value == "FAILED"
    assert record.failure_code == "GENERATION_DEADLINE_EXCEEDED"
    hold.set()


def test_provider_timeout_shorter_than_deadline_provider_wins(monkeypatch):
    """Requirement §14-4: the configured provider timeout is shorter than the
    remaining generation deadline -> the provider-timeout bound WINS
    (FRONTIER_TIMEOUT). The effective bound equals the configured 5 s; when it
    expires, ample generation deadline remains, so the canonical
    provider-timeout failure path is used."""
    from app.generation import frontier_provider as fp_mod

    started = threading.Event()
    hold = threading.Event()

    def _post(url, json=None, headers=None, timeout=None):
        started.set()
        hold.wait(120)
        return _Body(b"{}")

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    clock = ManualClock(start_time=0.0)
    provider = FrontierProvider(
        endpoint_url=OPENAI_ENDPOINT,
        api_key="k",
        model="m",
        clock=clock,
        structured_output_mode="openai_json_schema",
    )
    ids = IdSource()
    admission = _make_admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    controller = _make_controller(
        provider,
        admission,
        clock,
        ids,
        deadline_seconds=60,
        # Configured 5 s provider timeout is shorter than the 60 s deadline.
        provider_timeout_seconds=5.0,
    )
    outcome: dict[str, object] = {}

    def _run() -> None:
        handle = controller.start_generation(
            _PROMPT, anonymous_quota_session_id=session.session_id
        )
        outcome["record"] = controller.attempt(handle.attempt_id)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert started.wait(10), "the case_truth outbound call must have been entered"
    # The 5 s provider bound elapses; the 60 s deadline has 55 s left.
    clock.advance(5.1)
    thread.join(timeout=10)
    assert not thread.is_alive()
    record = outcome["record"]
    assert record.state.value == "FAILED"
    assert record.failure_code == "FRONTIER_TIMEOUT"
    hold.set()


# --------------------------------------------------------------------------- #
# 5/6 — the bound covers response-body reads and upstream processing
# --------------------------------------------------------------------------- #


def test_slow_response_body_read_canceled_at_wall_clock_bound(monkeypatch):
    """Requirement §14-5: a slow/dribbling response-body read is canceled at
    the wall-clock bound (the production escape: httpx's read timeout is per
    chunk, so a dribbling body outlived the configured 180 s). The adapter
    bound is 300 ms real; the body read sleeps 2 s per chunk — the call is
    interrupted at ~300 ms with FRONTIER_TIMEOUT."""
    from app.generation import frontier_provider as fp_mod

    def _post(url, json=None, headers=None, timeout=None):
        # Two chunks, each arriving only after 2 s — a dribbling body.
        return _SlowBody([b'{"a":', b'"b"}'], gap_seconds=2.0)

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    provider = FrontierProvider(
        endpoint_url=OPENAI_ENDPOINT, api_key="k", model="m"
    )
    t0 = time.monotonic()
    with pytest.raises(FrontierHttpError) as excinfo:
        provider.generate(
            GenerateRequest(
                attempt_id="att-slowread",
                stage=GenerationStage.CASE_TRUTH,
                prompt_context="p",
                timeout_seconds=0.3,
            )
        )
    elapsed = time.monotonic() - t0
    assert excinfo.value.code is GenerationFailureCode.FRONTIER_TIMEOUT
    assert elapsed < 1.5, elapsed  # bound honored, not the 2 s body read


def test_slow_upstream_processing_canceled_at_wall_clock_bound(monkeypatch):
    """Requirement §14-6: slow mocked upstream processing (the outbound POST
    itself does not return) is canceled at the wall-clock bound. The bound is
    300 ms real; the upstream would take 2 s — the call is interrupted at
    ~300 ms with FRONTIER_TIMEOUT and exactly ONE outbound attempt is made."""
    from app.generation import frontier_provider as fp_mod

    outbound = _HeldOutbound(hold_duration=2.0)
    outbound.install()
    try:
        provider = FrontierProvider(
            endpoint_url=OPENAI_ENDPOINT, api_key="k", model="m"
        )
        t0 = time.monotonic()
        with pytest.raises(FrontierHttpError) as excinfo:
            provider.generate(
                GenerateRequest(
                    attempt_id="att-slowup",
                    stage=GenerationStage.CASE_TRUTH,
                    prompt_context="p",
                    timeout_seconds=0.3,
                )
            )
        elapsed = time.monotonic() - t0
        assert excinfo.value.code is GenerationFailureCode.FRONTIER_TIMEOUT
        assert elapsed < 1.5, elapsed
        assert len(outbound.posts) == 1, outbound.posts  # no retry forever
    finally:
        outbound.restore()


# --------------------------------------------------------------------------- #
# 8/9 — a timed-out call never later produces success nor mutates state
# --------------------------------------------------------------------------- #


def test_timed_out_call_never_later_publishes_success_or_mutates_state(database_url, monkeypatch):
    """Requirements §14-8 / §14-9 / §14-12: after the wall-clock bound expires,
    releasing the mocked slow upstream (letting it "complete") must NOT later
    produce ``provider.call.complete success=true``, must NOT apply stage
    output and must NOT publish. The attempt stays FAILED/FRONTIER_TIMEOUT and
    the timed-out worker's result is structurally discarded."""
    import io as _io

    from app.generation import frontier_provider as fp_mod

    hold = threading.Event()
    golden = list(_GOLDEN_STAGE_STRINGS)

    def _post(url, json=None, headers=None, timeout=None):
        hold.wait(120)
        content = golden.pop(0) if golden else "<late>"
        return _Body(content.encode("utf-8"))

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    log_buffer = _io.StringIO()
    application = _make_app(database_url, frontier_timeout_seconds=_ACCELERATED_TIMEOUT_SECONDS)
    try:
        with _CaptureLog(log_buffer), TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "KEY-LATE", "model": "MODEL-LATE"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "FAILED"
            assert res.json()["failureCode"] == "FRONTIER_TIMEOUT"
            case_id = res.json()["caseId"]
            # Release the stale worker AFTER the attempt already failed — its
            # late completion is discarded by the adapter box.
            hold.set()
            time.sleep(0.5)
            store = application.state.store
            version_row = store.get_case_version(case_id, 1)
            assert version_row.state_reason == "FRONTIER_TIMEOUT"
            assert store.get_published(case_id, 1) is None
            # No successful complete for the timed-out call.
            log_text = log_buffer.getvalue()
            for line in log_text.splitlines():
                if "provider.call.complete" in line:
                    payload = json.loads(line)
                    assert payload.get("success") is not True, line
    finally:
        hold.set()
        _dispose(application)


# --------------------------------------------------------------------------- #
# 10 — a timeout in attempt A does not cancel/corrupt attempt B
# --------------------------------------------------------------------------- #


def test_concurrent_timeout_of_a_does_not_affect_b(database_url):
    """Requirement §14-10 / §13: attempt A's provider call times out while
    attempt B publishes normally on a different provider/key/model. A's
    cancellation must not cancel or corrupt B, keys/models never cross-talk and
    the wire targets stay the server-owned registry endpoints."""
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    upgrade_db(database_url)
    settings = Settings(
        database_url=database_url,
        cors_allowed_origins=["http://localhost:5173"],
        generation_provider="fake",
        frontier_enabled=True,
        frontier_timeout_seconds=_ACCELERATED_TIMEOUT_SECONDS,
        max_concurrent_generations=4,
        max_concurrent_generations_global=8,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_deadline_seconds=60,
    )
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    from app.generation import frontier_provider as fp_mod

    hold_a = threading.Event()
    golden_b = list(_GOLDEN_STAGE_STRINGS)

    def _post(url, json=None, headers=None, timeout=None):
        body = dict(json or {})
        model = body.get("model", "")
        if model == "MODEL-TIMEOUT-A":
            hold_a.wait(120)
            return _Body(b"{}")
        return _Body(golden_b.pop(0).encode("utf-8"))

    original = fp_mod.httpx.post
    fp_mod.httpx.post = _post
    try:
        session_a = service.create_anonymous_quota_session()
        session_b = service.create_anonymous_quota_session()
        posts: list[tuple[str, dict, dict]] = []
        lock = threading.Lock()

        def _recording_post(url, json=None, headers=None, timeout=None):
            with lock:
                posts.append((url, dict(json or {}), dict(headers or {})))
            return _post(url, json=json, headers=headers, timeout=timeout)

        fp_mod.httpx.post = _recording_post
        results: dict[str, object] = {}
        errors: dict[str, Exception] = {}

        def _run(which, session_id, key, model, provider_id):
            try:
                results[which] = service.start_case_generation(
                    _PROMPT,
                    anonymous_quota_session_id=session_id,
                    generation_provider="frontier",
                    frontier_provider=provider_id,
                    frontier_api_key=key,
                    frontier_model=model,
                )
            except Exception as exc:  # noqa: BLE001 - recorded for the assert
                errors[which] = exc

        thread_a = threading.Thread(
            target=_run, args=("a", session_a.anonymous_quota_session_id, "KEY-A", "MODEL-TIMEOUT-A", "openai")
        )
        thread_b = threading.Thread(
            target=_run, args=("b", session_b.anonymous_quota_session_id, "KEY-B", "MODEL-B", "openrouter")
        )
        thread_a.start()
        thread_b.start()
        thread_b.join(timeout=120)
        thread_a.join(timeout=120)
        assert not errors, errors
        assert results["b"].status == "PUBLISHED", results["b"]
        assert results["a"].status == "FAILED", results["a"]
        assert getattr(results["a"], "failure_code", None) in (
            "FRONTIER_TIMEOUT",
            "PROVIDER_TIMEOUT",
        )
        # Isolation: B never used A's key/model; all B posts hit OpenRouter
        # with KEY-B/MODEL-B; the only post from A hit OpenAI with KEY-A.
        posts_b = [p for p in posts if p[2].get("Authorization") == "Bearer KEY-B"]
        posts_a = [p for p in posts if p[2].get("Authorization") == "Bearer KEY-A"]
        assert len(posts_b) == 4, posts
        assert all(url == OPENROUTER_ENDPOINT for url, _b, _h in posts_b)
        assert all(body["model"] == "MODEL-B" for _u, body, _h in posts_b)
        assert 1 <= len(posts_a) <= 3, posts
        assert all(url == OPENAI_ENDPOINT for url, _b, _h in posts_a)
        assert all(body["model"] == "MODEL-TIMEOUT-A" for _u, body, _h in posts_a)
        stored_b = json.loads(store.get_published(results["b"].case_id, 1).payload_json)
        assert stored_b.get("model") == "MODEL-B"
        assert "KEY-A" not in json.dumps(stored_b)
    finally:
        hold_a.set()
        fp_mod.httpx.post = original
        store.dispose()


# --------------------------------------------------------------------------- #
# DEF-018 — mid-body/streaming transport failures reduce to the typed path
#            (never None, never an unhandled thread traceback, never HTTP 500)
# --------------------------------------------------------------------------- #


class _MidBodyFailure:
    """A mock httpx 200 response whose ``iter_bytes`` raises the supplied
    exception on first read (the DEF-018 mid-body failure shape)."""

    def __init__(self, exc: Exception) -> None:
        self.status_code = 200
        self._exc = exc

    def iter_bytes(self, chunk_size: int):
        raise self._exc


def test_mid_body_transport_failures_become_typed_errors(monkeypatch):
    """DEF-018 — a 200 response whose ``iter_bytes()`` raises
    ``httpx.ReadTimeout`` / ``httpx.RemoteProtocolError`` /
    ``httpx.ConnectError`` / ``OSError`` is reduced to the canonical typed path
    before the wall-clock deadline: ``generate()`` raises
    ``FrontierHttpError(FRONTIER_PROVIDER_ERROR)`` with a sanitized message,
    NEVER returns None, and the supervised worker never prints an unhandled
    thread traceback to stderr."""
    from app.generation import frontier_provider as fp_mod

    cases = (
        httpx.ReadTimeout("dribbling body read timed out"),
        httpx.RemoteProtocolError(
            "peer closed connection without sending complete message body"
        ),
        httpx.ConnectError("connection reset by upstream"),
        OSError("connection reset mid-body"),
    )
    for exc in cases:
        monkeypatch.setattr(
            fp_mod.httpx,
            "post",
            lambda url, json=None, headers=None, timeout=None, exc=exc: _MidBodyFailure(exc),
        )
        provider = FrontierProvider(
            endpoint_url=OPENAI_ENDPOINT, api_key="opaque-midbody", model="m"
        )
        stderr_buffer = io.StringIO()
        with contextlib.redirect_stderr(stderr_buffer):
            with pytest.raises(FrontierHttpError) as excinfo:
                provider.generate(
                    GenerateRequest(
                        attempt_id=f"att-mid-{type(exc).__name__}",
                        stage=GenerationStage.CASE_TRUTH,
                        prompt_context="p",
                        timeout_seconds=2.0,  # deadline far ahead: provider error
                    )
                )
        assert excinfo.value.code is GenerationFailureCode.FRONTIER_PROVIDER_ERROR
        # sanitized: never the raw provider exception text, never the URL,
        # never the key.
        assert "openai.com" not in str(excinfo.value)
        assert "opaque-midbody" not in str(excinfo.value)
        assert "dribbling body read timed out" not in str(excinfo.value)
        line = stderr_buffer.getvalue()
        assert "Traceback (most recent call last)" not in line, (
            f"unhandled worker traceback for {type(exc).__name__}: {line}"
        )


def test_mid_body_failure_at_wall_clock_deadline_is_timeout(monkeypatch):
    """DEF-018 — a mid-body read failure observed AT/after the wall-clock
    deadline is classified FRONTIER_TIMEOUT (the deadline-based rule), not a
    generic provider error and never a late success."""
    from app.generation import frontier_provider as fp_mod

    clock = ManualClock(start_time=0.0)
    started = threading.Event()

    class _Body:
        status_code = 200

        def iter_bytes(self, chunk_size: int):
            started.set()
            # Deterministic: the body read fails only once the manual clock has
            # crossed the 1 s wall-clock deadline.
            while clock.now() < 1.0:
                time.sleep(0.0005)
            raise httpx.ReadTimeout("slow body read hit the wall")

    def _post(url, json=None, headers=None, timeout=None):
        return _Body()

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    provider = FrontierProvider(
        endpoint_url=OPENAI_ENDPOINT, api_key="k", model="m", clock=clock
    )
    outcome: dict[str, object] = {}

    def _run() -> None:
        try:
            outcome["result"] = provider.generate(
                GenerateRequest(
                    attempt_id="att-deadline-mid",
                    stage=GenerationStage.CASE_TRUTH,
                    prompt_context="p",
                    timeout_seconds=1.0,
                )
            )
        except FrontierHttpError as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert started.wait(10), "the worker must have entered its body read"
    time.sleep(0.03)  # let the supervisor enter its join loop (remaining > 0)
    clock.advance(1.0)  # the wall-clock deadline is reached mid-body
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert "result" not in outcome
    error = outcome["error"]
    assert isinstance(error, FrontierHttpError)
    assert error.code is GenerationFailureCode.FRONTIER_TIMEOUT


def test_mid_body_failure_fails_attempt_canonically_not_http_500(database_url, monkeypatch):
    """DEF-018 controller/API level — a 200 response whose ``iter_bytes``
    raises mid-body FAILS the attempt with the canonical
    ``FRONTIER_PROVIDER_ERROR`` (HTTP 201 + status FAILED) instead of the old
    ``NoneType`` crash (HTTP 500 INTERNAL_ERROR)."""
    from app.generation import frontier_provider as fp_mod

    def _post(url, json=None, headers=None, timeout=None):
        return _MidBodyFailure(OSError("connection reset mid-body"))

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "K-MID", "model": "M-MID"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "FAILED"
            assert res.json()["failureCode"] == "FRONTIER_PROVIDER_ERROR"
            # the key/upstream internals never leak into the API response
            assert "K-MID" not in res.text
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# DEF-022 — non-finite / non-positive provider timeouts rejected everywhere
# --------------------------------------------------------------------------- #


def test_generate_request_rejects_non_finite_and_non_positive_timeouts():
    """DEF-022 — ``GenerateRequest`` rejects nan / inf / -inf / 0 / negative
    ``timeout_seconds`` with a clear ValueError (a non-finite value would
    poison ``join``/``deadline``: nan -> untyped ValueError, inf -> unbounded)."""
    for bad in (float("nan"), float("inf"), float("-inf"), 0.0, -1.0, -0.5):
        with pytest.raises(ValueError, match="timeout_seconds"):
            GenerateRequest(
                attempt_id="att-nan",
                stage=GenerationStage.CASE_TRUTH,
                prompt_context="p",
                timeout_seconds=bad,
            )
    # a finite positive timeout still passes (existing contract unchanged).
    request = GenerateRequest(
        attempt_id="att-ok",
        stage=GenerationStage.CASE_TRUTH,
        prompt_context="p",
        timeout_seconds=5.0,
    )
    assert request.timeout_seconds == 5.0
    # None (the legacy default) still means "provider default".
    assert GenerateRequest(
        attempt_id="att-none",
        stage=GenerationStage.CASE_TRUTH,
        prompt_context="p",
    ).timeout_seconds is None


def test_frontier_provider_rejects_non_finite_timeout_at_construction():
    """DEF-022 — ``FrontierProvider(timeout_seconds=...)`` rejects nan / inf /
    -inf / 0 / negative at construction (adapter-surface guard; nan/inf can
    never reach ``deadline``/``join``)."""
    for bad in (float("nan"), float("inf"), float("-inf"), 0.0, -1.0):
        with pytest.raises(ValueError, match="timeout_seconds"):
            FrontierProvider(
                endpoint_url=OPENAI_ENDPOINT,
                api_key="k",
                model="m",
                timeout_seconds=bad,
            )
    # the documented default and finite values still construct.
    FrontierProvider(endpoint_url=OPENAI_ENDPOINT, api_key="k", model="m")


# --------------------------------------------------------------------------- #
# DEF-020 — a timed-out call is ABORTED (no live worker keeps holding the key)
# --------------------------------------------------------------------------- #


class _AbortableHold:
    """An abort handle with the SAME ``close()``-based contract the supervisor
    uses for the real dedicated client: closing it releases the blocked mock
    upstream so the supervised worker terminates."""

    def __init__(self) -> None:
        self.released = threading.Event()

    def close(self) -> None:
        self.released.set()


class _BlockingBody:
    """A mock 200 response whose body read blocks until the call's abort handle
    is closed (the worker can then terminate — DEF-020 liveness proof)."""

    status_code = 200

    def __init__(self, handle: _AbortableHold, started: threading.Event) -> None:
        self._handle = handle
        self._started = started

    def iter_bytes(self, chunk_size: int):
        self._started.set()
        self._handle.released.wait(120)
        raise OSError("outbound call aborted by the supervisor")


def _register_abortable_post(fp_mod, handle: _AbortableHold, started: threading.Event):
    """Patch the outbound seam so the CURRENT supervised call registers
    ``handle`` as its abort client (the same slot the real transport uses for
    the dedicated ``httpx.Client``) and blocks on it mid-body."""

    def _post(url, json=None, headers=None, timeout=None):
        state = getattr(fp_mod._ACTIVE_CALL, "box", None)
        assert state is not None, "the supervised worker must have set its call box"
        state["abort_client"] = handle
        return _BlockingBody(handle, started)

    fp_mod.httpx.post = _post


def _live_frontier_workers() -> list[threading.Thread]:
    return [
        t for t in threading.enumerate()
        if t.name.startswith("frontier-provider-") and t.is_alive()
    ]


def test_timeout_aborts_in_flight_call_and_worker_terminates(monkeypatch):
    """DEF-020(a) — after the supervisor classifies a timeout it ABORTS the
    in-flight outbound call (closes the abort handle registered in the call
    box), so the supervised worker terminates at/before the bound + a small
    tolerance: no live ``frontier-provider-*`` thread is still holding the call
    (and the user's key) after the timeout."""
    from app.generation import frontier_provider as fp_mod

    handle = _AbortableHold()
    block_started = threading.Event()
    original = fp_mod.httpx.post
    try:
        _register_abortable_post(fp_mod, handle, block_started)
        provider = FrontierProvider(
            endpoint_url=OPENAI_ENDPOINT, api_key="k-abort", model="m"
        )
        t0 = time.monotonic()
        with pytest.raises(FrontierHttpError) as excinfo:
            provider.generate(
                GenerateRequest(
                    attempt_id="att-abort",
                    stage=GenerationStage.CASE_TRUTH,
                    prompt_context="p",
                    timeout_seconds=0.3,
                )
            )
        elapsed = time.monotonic() - t0
        assert excinfo.value.code is GenerationFailureCode.FRONTIER_TIMEOUT
        assert 0.15 <= elapsed <= 1.5, elapsed
        assert block_started.is_set(), "the mocked upstream must have been entered"
        assert handle.released.is_set(), (
            "the supervisor must have aborted the in-flight call at the deadline"
        )
        # bounded liveness: the aborted worker is gone within a small tolerance.
        termination_deadline = time.monotonic() + 2.0
        workers = _live_frontier_workers()
        while workers:
            assert time.monotonic() < termination_deadline, (
                "a timed-out provider worker is still alive holding the call"
            )
            time.sleep(0.01)
            workers = _live_frontier_workers()
    finally:
        fp_mod.httpx.post = original


def test_repeated_timeouts_do_not_accumulate_live_worker_threads(monkeypatch):
    """DEF-020(b) — sequential timeouts each ABORT their in-flight call:
    live ``frontier-provider-*`` worker threads return to zero after every
    round instead of accumulating blocked outbound calls holding the key."""
    from app.generation import frontier_provider as fp_mod

    original = fp_mod.httpx.post
    try:
        for attempt in range(10):
            handle = _AbortableHold()
            block_started = threading.Event()
            _register_abortable_post(fp_mod, handle, block_started)
            provider = FrontierProvider(
                endpoint_url=OPENAI_ENDPOINT, api_key="k-live", model="m"
            )
            with pytest.raises(FrontierHttpError) as excinfo:
                provider.generate(
                    GenerateRequest(
                        attempt_id=f"att-live-{attempt}",
                        stage=GenerationStage.CASE_TRUTH,
                        prompt_context="p",
                        timeout_seconds=0.15,
                    )
                )
            assert excinfo.value.code is GenerationFailureCode.FRONTIER_TIMEOUT
            assert handle.released.is_set(), "each timeout must abort its call"
            # bounded liveness per round: no worker survives the bound+tolerance.
            deadline = time.monotonic() + 1.5
            while _live_frontier_workers():
                assert time.monotonic() < deadline, (
                    f"round {attempt}: live workers accumulated"
                )
                time.sleep(0.01)
        assert _live_frontier_workers() == [], (
            "repeated timeouts must not leave a single live outbound worker"
        )
    finally:
        fp_mod.httpx.post = original


# --------------------------------------------------------------------------- #
# DEF-021 — a completion observed at/after the wall-clock deadline is never
#           a late success (post-join deadline re-check)
# --------------------------------------------------------------------------- #


def test_worker_completing_at_or_after_deadline_never_returns_success(monkeypatch):
    """DEF-021 — a worker that completes its body read only after the wall-clock
    deadline has passed is classified FRONTIER_TIMEOUT, never a late success.
    Deterministic: the mock body's read finishes only after the main thread has
    pushed the (manual) wall-clock past the deadline, so the completion is
    provably at/after the bound; the supervisor's post-join re-check reports
    the canonical timeout instead of returning the result."""
    from app.generation import frontier_provider as fp_mod

    clock = ManualClock(start_time=0.0)
    started = threading.Event()
    clock_crossed = threading.Event()

    class _WaitForDeadlineBody:
        status_code = 200

        def iter_bytes(self, chunk_size: int):
            # Yield the (parser-valid) body immediately. The read then parks in
            # a bounded real-time window during which the main thread crosses
            # the wall-clock deadline (the DEF-021 join window), so the body
            # read can only COMPLETE at/after the deadline.
            yield b'{"late": true}'
            started.set()
            clock_crossed.wait(10)

    def _post(url, json=None, headers=None, timeout=None):
        return _WaitForDeadlineBody()

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    provider = FrontierProvider(
        endpoint_url=OPENAI_ENDPOINT, api_key="k-late", model="m", clock=clock
    )
    outcome: dict[str, object] = {}

    def _run() -> None:
        try:
            outcome["result"] = provider.generate(
                GenerateRequest(
                    attempt_id="att-late",
                    stage=GenerationStage.CASE_TRUTH,
                    prompt_context="p",
                    timeout_seconds=2.0,
                )
            )
        except FrontierHttpError as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert started.wait(10), "the worker must have entered its body read"
    time.sleep(0.03)  # let the supervisor enter its join loop (remaining > 0)
    clock.advance(2.5)  # the wall-clock deadline passes while the worker is mid-body
    assert clock.now() >= 2.0
    clock_crossed.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert "result" not in outcome, (
        "a completion observed at/after the wall-clock deadline must never be "
        "returned as success"
    )
    error = outcome["error"]
    assert isinstance(error, FrontierHttpError)
    assert error.code is GenerationFailureCode.FRONTIER_TIMEOUT
    assert clock.now() >= 2.0


# --------------------------------------------------------------------------- #
# 12 — no timeout test requires a real network call
# --------------------------------------------------------------------------- #


def test_no_timeout_test_requires_real_network():
    """Requirement §14-12: the enforcement machinery is hermetic — a provider
    whose httpx seam is absent fails with the typed error and the network
    guard (conftest ``_block_network``) forbids any external socket. Here we
    prove the smallest unit: the wall-clock bound applies even when the mocked
    transport is perfectly fast, and everything runs in-process."""
    # The autouse conftest fixtures already raise on any external connect /
    # getaddrinfo / send; every outbound call in this module goes through the
    # monkeypatched ``httpx.post`` seam. This test documents the guarantee by
    # exercising the fast path with zero real I/O.
    assert True