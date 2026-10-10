"""Phase36 — closed safe admission denial reason codes (backend §10/§12/§31).

Every anonymous-quota admission denial source in ``app.generation.admission``
must carry an exact ``AdmissionReasonCode``; the service layer must forward it
through ``AdmissionDeniedError.reason_code`` and the API boundary must surface
it as the sanitized ``reasonCode`` of the 429 ``ADMISSION_DENIED`` envelope —
while ``TOO_MANY_REQUESTS`` stays its own distinct code with NO reasonCode.

Tests are hermetic (autouse conftest network block is active): zero provider /
model / bridge calls, ManualClock for every timing-sensitive path, scratch
SQLite files, and the fake provider only where a SUCCESSFUL run is needed to
fill a window.

Covered denial paths (Phase36 §31 + orchestrator §5):
- ANONYMOUS_SESSION_CAPACITY_LIMIT  (controller + service + API)
- SESSION_EXPIRED_OR_INVALID        (unknown session id + expired window)
- SESSION_GENERATION_LIMIT          (3-per-session window exhausted)
- SESSION_CONCURRENCY_LIMIT         (one active + second attempt)
- GLOBAL_CONCURRENCY_LIMIT          (global concurrency filled)
- GLOBAL_GENERATION_WINDOW_LIMIT    (20-in-60s rolling window filled)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

from app.generation.admission import (  # noqa: E402
    AdmissionController,
    AdmissionDenied,
    AdmissionReasonCode,
)
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import CountingProvider, FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from phase5_helpers import (  # noqa: E402
    assert_no_hidden_leaks,
    assert_sanitized_error,
    auth,
    create_case,
    create_session,
)

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

_G = GOLDEN_STAGE_PAYLOADS

ALL_REASON_CODES = tuple(AdmissionReasonCode)


def _golden_sync_script():
    return {stage: [payload] for stage, payload in _G.items()}


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


def _counting_controller(clock, ids, admission, **overrides):
    counting = CountingProvider(FakeProvider(_golden_sync_script()))
    controller = GenerationController(
        provider=counting,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=9,
        **overrides,
    )
    return counting, controller


class _CountingFlock:
    """Fresh ``CountingProvider`` per attempt (the production per-attempt
    provider), aggregating call counts.

    ``FakeProvider`` scripts are CONSUMED via ``pop(0)``, so a single shared
    instance can never serve two generation attempts; the service creates a
    fresh provider per attempt (``ResolvedGeneration.provider_factory``). This
    helper mirrors that per-attempt contract while still proving the DENIED
    attempt performed ZERO provider calls.
    """

    def __init__(self, script_factory=_golden_sync_script):
        self._script_factory = script_factory
        self.providers: list[CountingProvider] = []

    def __call__(self):
        provider = CountingProvider(FakeProvider(self._script_factory()))
        self.providers.append(provider)
        return provider

    @property
    def call_count(self) -> int:
        return sum(p.call_count for p in self.providers)


# --------------------------------------------------------------------------- #
# Controller level — the closed code is set at each denial source
# --------------------------------------------------------------------------- #

def test_controller_anonymous_session_capacity_reason_code():
    """max_sessions=1 -> the 2nd create carries ANONYMOUS_SESSION_CAPACITY_LIMIT."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids, max_sessions=1)
    admission.create_anonymous_quota_session()
    with pytest.raises(AdmissionDenied) as excinfo:
        admission.create_anonymous_quota_session()
    assert excinfo.value.decision.reason_code is (
        AdmissionReasonCode.ANONYMOUS_SESSION_CAPACITY_LIMIT
    )


def test_controller_unknown_session_reason_code():
    """Unknown session id -> SESSION_EXPIRED_OR_INVALID (closed token)."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    with pytest.raises(AdmissionDenied) as excinfo:
        admission.admit_generation("QUOTA-does-not-exist")
    assert (
        excinfo.value.decision.reason_code
        is AdmissionReasonCode.SESSION_EXPIRED_OR_INVALID
    )


def test_controller_expired_window_reason_code():
    """Session window expired (ManualClock) -> SESSION_EXPIRED_OR_INVALID."""
    clock = ManualClock(start_time=1000.0)
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    # passed the window end, still inside the eviction grace (one TTL past).
    clock.advance(86400 + 1)
    with pytest.raises(AdmissionDenied) as excinfo:
        admission.admit_generation(session.session_id)
    assert (
        excinfo.value.decision.reason_code
        is AdmissionReasonCode.SESSION_EXPIRED_OR_INVALID
    )
    assert "window expired" in excinfo.value.decision.reason


def test_controller_session_generation_limit_reason_code():
    """Exhaust the 3-per-session window -> SESSION_GENERATION_LIMIT."""
    clock = ManualClock()
    ids = IdSource()
    # The per-session concurrency ceiling must not bind a direct admit loop
    # (nothing releases the reservations between admits), so it is raised.
    admission = _admission(
        clock,
        ids,
        max_concurrent_generations=10,
        max_generations_per_session_per_window=3,
    )
    session = admission.create_anonymous_quota_session()
    for _ in range(3):
        assert admission.admit_generation(session.session_id).admitted is True
    with pytest.raises(AdmissionDenied) as excinfo:
        admission.admit_generation(session.session_id)
    assert (
        excinfo.value.decision.reason_code is AdmissionReasonCode.SESSION_GENERATION_LIMIT
    )


def test_controller_session_concurrency_limit_reason_code():
    """One active generation holds the per-session slot -> concurrency token."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids, max_concurrent_generations=1)
    session = admission.create_anonymous_quota_session()
    script = {GenerationStage.CASE_TRUTH: ["pending"]}
    counting = CountingProvider(FakeProvider(script))
    controller = GenerationController(
        provider=counting,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=9,
    )
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    assert controller.attempt(handle.attempt_id).state is GenerationState.GENERATING
    calls_after_first = counting.call_count
    with pytest.raises(AdmissionDenied) as excinfo:
        controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
        )
    assert (
        excinfo.value.decision.reason_code
        is AdmissionReasonCode.SESSION_CONCURRENCY_LIMIT
    )
    # The denied second attempt consumed ZERO provider calls.
    assert counting.call_count == calls_after_first


def test_controller_global_concurrency_limit_reason_code():
    """Fill the global concurrency (2) -> GLOBAL_CONCURRENCY_LIMIT."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(
        clock,
        ids,
        max_concurrent_generations=2,
        max_concurrent_generations_global=2,
    )
    session_a = admission.create_anonymous_quota_session()
    session_b = admission.create_anonymous_quota_session()
    session_c = admission.create_anonymous_quota_session()

    def _pending_controller():
        counting = CountingProvider(
            FakeProvider({GenerationStage.CASE_TRUTH: ["pending"]})
        )
        controller = GenerationController(
            provider=counting,
            admission=admission,
            clock=clock,
            ids=ids,
            deadline_seconds=60,
            max_llm_calls_per_generation=8,
            max_repair_passes=2,
            max_full_regenerations=1,
            max_prompt_chars=4000,
            seed=9,
        )
        return counting, controller

    counting_a, ctrl_a = _pending_controller()
    handle_a = ctrl_a.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session_a.session_id
    )
    counting_b, ctrl_b = _pending_controller()
    handle_b = ctrl_b.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session_b.session_id
    )
    assert (
        ctrl_a.attempt(handle_a.attempt_id).state is GenerationState.GENERATING
    )
    assert (
        ctrl_b.attempt(handle_b.attempt_id).state is GenerationState.GENERATING
    )
    assert admission.global_active_concurrency == 2
    counting_c, ctrl_c = _pending_controller()
    calls_before = counting_c.call_count
    with pytest.raises(AdmissionDenied) as excinfo:
        ctrl_c.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session_c.session_id
        )
    assert (
        excinfo.value.decision.reason_code is AdmissionReasonCode.GLOBAL_CONCURRENCY_LIMIT
    )
    assert counting_c.call_count == calls_before  # zero provider calls


def test_controller_global_window_limit_reason_code():
    """Fill the rolling 20-in-60s global window -> GLOBAL_GENERATION_WINDOW_LIMIT.

    Per-session window is raised so the GLOBAL window is the binding constraint
    on the 21st admit; ManualClock proves the rolling reset after 60s.
    """
    clock = ManualClock(start_time=0.0)
    ids = IdSource()
    admission = _admission(
        clock,
        ids,
        max_concurrent_generations=30,
        max_concurrent_generations_global=30,
        max_generations_per_session_per_window=30,
        max_generations_global_per_window=20,
        global_window_seconds=60,
    )
    session = admission.create_anonymous_quota_session()
    for _ in range(20):
        assert admission.admit_generation(session.session_id).admitted is True
    assert admission.global_window_generations == 20
    with pytest.raises(AdmissionDenied) as excinfo:
        admission.admit_generation(session.session_id)
    assert (
        excinfo.value.decision.reason_code
        is AdmissionReasonCode.GLOBAL_GENERATION_WINDOW_LIMIT
    )
    # Rolling window: after 60s the fresh window admits again (same session).
    clock.advance(61)
    assert admission.admit_generation(session.session_id).admitted is True
    assert admission.global_window_generations == 1


def test_controller_reason_zero_provider_calls_on_rejected_admission():
    """The exhausted per-session window denial consumed ZERO provider calls."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids, max_generations_per_session_per_window=1)
    session = admission.create_anonymous_quota_session()
    counting, controller = _counting_controller(clock, ids, admission)

    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    assert controller.attempt(handle.attempt_id).state is GenerationState.PUBLISHED
    used = counting.call_count
    assert used == 4  # four stage calls without repairs
    with pytest.raises(AdmissionDenied) as excinfo:
        controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
        )
    assert (
        excinfo.value.decision.reason_code is AdmissionReasonCode.SESSION_GENERATION_LIMIT
    )
    assert counting.call_count == used  # zero provider calls on the denial


# --------------------------------------------------------------------------- #
# Service level — AdmissionDeniedError.reason_code is forwarded exactly
# --------------------------------------------------------------------------- #

def _service(store, database_url, *, clock, admission, ids, **settings_overrides):
    from app.core.config import Settings
    from app.services.generation import GenerationService

    settings = Settings(database_url=database_url, **settings_overrides)
    return GenerationService(
        settings=settings,
        store=store,
        clock=clock,
        admission=admission,
        ids=ids,
    )


def _golden_service(
    store,
    database_url,
    *,
    clock,
    ids,
    admission=None,
    **settings_overrides,
):
    """Service over a durable controller; provider_factory counts model calls."""
    from app.core.config import Settings
    from app.services.admission import DurableAdmissionController
    from app.services.generation import GenerationService

    if admission is None:
        admission = DurableAdmissionController(
            clock=clock,
            ids=IdSource(),
            max_concurrent_generations=1,
            max_concurrent_generations_global=2,
            max_generations_per_session_per_window=3,
            max_generations_global_per_window=20,
            anonymous_quota_session_ttl_seconds=86400,
            global_window_seconds=60,
        )
    flock = _CountingFlock()
    settings_overrides.setdefault("max_generations_per_session_per_window", 3)
    settings_overrides.setdefault("max_concurrent_generations", 1)

    service = GenerationService(
        settings=Settings(database_url=database_url, **settings_overrides),
        store=store,
        clock=clock,
        admission=admission,
        ids=ids,
        provider_factory=flock,
    )
    return service, flock


def test_service_anonymous_session_capacity_reason_code(store, database_url):
    """Store at max_sessions -> AdmissionDeniedError ANONYMOUS_SESSION_CAPACITY_LIMIT.

    Session creation never touches a provider: zero provider calls implicit.
    """
    from app.services.admission import DurableAdmissionController
    from app.services.generation import AdmissionDeniedError

    clock = ManualClock(start_time=0.0)
    capped = DurableAdmissionController(
        clock=clock,
        ids=IdSource(),
        max_concurrent_generations=1,
        max_concurrent_generations_global=2,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_seconds=60,
        max_sessions=1,
    )
    service, _counting = _golden_service(
        store, database_url, clock=clock, ids=IdSource(), admission=capped
    )
    assert service.create_anonymous_quota_session().anonymous_quota_session_id
    with pytest.raises(AdmissionDeniedError) as excinfo:
        service.create_anonymous_quota_session()
    assert (
        excinfo.value.reason_code
        == AdmissionReasonCode.ANONYMOUS_SESSION_CAPACITY_LIMIT.value
    )
    assert capped.session_count == 1  # fail closed, never unbounded


def test_service_unknown_session_durable_reason_code(store, database_url):
    """start_case_generation on a session with NO durable row
    -> SESSION_EXPIRED_OR_INVALID, zero provider calls, no case published."""
    from app.services.generation import AdmissionDeniedError

    clock = ManualClock(start_time=0.0)
    ids = IdSource()
    service, counting = _golden_service(
        store, database_url, clock=clock, ids=ids,
        max_generations_per_session_per_window=3,
    )
    with pytest.raises(AdmissionDeniedError) as excinfo:
        service.start_case_generation(
            GOLDEN_PROMPT,
            anonymous_quota_session_id="QUOTA-999-never-created",
        )
    assert excinfo.value.reason_code == AdmissionReasonCode.SESSION_EXPIRED_OR_INVALID.value
    assert counting.call_count == 0  # zero provider/model calls
    # No generation was published: no CASE-1 exists.
    assert store.get_case("CASE-1") is None


def test_service_session_generation_limit_reason_code(store, database_url):
    """Exhaust the 3-per-session window at the durable service
    -> SESSION_GENERATION_LIMIT, zero provider calls on the denial, no
    publication of the denied attempt."""
    from app.services.generation import AdmissionDeniedError

    clock = ManualClock(start_time=1000.0)
    ids = IdSource()  # deterministic: CASE-1..CASE-3, CASE-4 never exists
    service, counting = _golden_service(
        store, database_url, clock=clock, ids=ids,
        max_generations_per_session_per_window=3,
    )
    session = service.create_anonymous_quota_session()
    sid = session.anonymous_quota_session_id
    for index in range(3):
        started = service.start_case_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=sid
        )
        assert started.status == "PUBLISHED"
    used = counting.call_count
    assert used >= 4  # the three admitted runs really called the provider
    assert store.get_case(f"CASE-{index + 1}") is not None
    with pytest.raises(AdmissionDeniedError) as excinfo:
        service.start_case_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=sid
        )
    assert (
        excinfo.value.reason_code == AdmissionReasonCode.SESSION_GENERATION_LIMIT.value
    )
    # Zero provider/model calls on the denied attempt.
    assert counting.call_count == used
    # The DENIED attempt's own provider was allocated but never called.
    assert counting.providers[-1].call_count == 0
    # No publication for the denied 4th attempt (CASE-4 was never allocated).
    assert store.get_case("CASE-4") is None


def test_service_start_case_version_unknown_session_reason_code(store, database_url):
    """start_case_version on an unknown session
    -> SESSION_EXPIRED_OR_INVALID (same durable check as the v1 path).

    A case row must exist first: ``start_case_version`` resolves the case
    BEFORE the session check (UnknownCaseError otherwise).
    """
    from app.services.generation import AdmissionDeniedError

    clock = ManualClock(start_time=1000.0)
    service, counting = _golden_service(
        store, database_url, clock=clock, ids=IdSource()
    )
    now = float(clock.now())
    # FK parent row for the seeded case (PRAGMA foreign_keys=ON at runtime).
    from app.auth.tokens import issue_token, verifier as _v

    store.create_session(
        session_id="QUOTA-seed-parent",
        token_verifier=_v(issue_token()),
        quota_window_end=now + 3600,
        created_at=now,
    )
    store.create_case(
        case_id="CASE-SEED",
        quota_session_id="QUOTA-seed-parent",
        title="Seed",
        difficulty=None,
        created_at=now,
    )
    with pytest.raises(AdmissionDeniedError) as excinfo:
        service.start_case_version(
            "CASE-SEED", GOLDEN_PROMPT, anonymous_quota_session_id="QUOTA-missing"
        )
    assert excinfo.value.reason_code == AdmissionReasonCode.SESSION_EXPIRED_OR_INVALID.value
    assert counting.call_count == 0


def test_service_global_window_reason_code(store, database_url):
    """A small rolling global window (2 per 60s) denies the 3rd admit
    -> GLOBAL_GENERATION_WINDOW_LIMIT through the durable service."""
    from app.core.config import Settings
    from app.services.admission import DurableAdmissionController
    from app.services.generation import AdmissionDeniedError, GenerationService

    clock = ManualClock(start_time=0.0)
    ids = IdSource()
    settings = Settings(
        database_url=database_url,
        max_concurrent_generations=2,
        max_concurrent_generations_global=2,
        max_generations_per_session_per_window=30,
        max_generations_global_per_window=2,
        global_generation_window_seconds=60,
    )
    admission = DurableAdmissionController(
        clock=clock,
        ids=IdSource(),
        max_concurrent_generations=2,
        max_concurrent_generations_global=2,
        max_generations_per_session_per_window=30,
        max_generations_global_per_window=2,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_seconds=60,
    )
    flock = _CountingFlock()
    service = GenerationService(
        settings=settings,
        store=store,
        clock=clock,
        admission=admission,
        ids=ids,
        provider_factory=flock,
    )
    session = service.create_anonymous_quota_session()
    sid = session.anonymous_quota_session_id
    assert service.start_case_generation(GOLDEN_PROMPT, anonymous_quota_session_id=sid).status == "PUBLISHED"
    assert service.start_case_generation(GOLDEN_PROMPT, anonymous_quota_session_id=sid).status == "PUBLISHED"
    used = flock.call_count
    with pytest.raises(AdmissionDeniedError) as excinfo:
        service.start_case_generation(GOLDEN_PROMPT, anonymous_quota_session_id=sid)
    assert (
        excinfo.value.reason_code
        == AdmissionReasonCode.GLOBAL_GENERATION_WINDOW_LIMIT.value
    )
    assert flock.call_count == used  # zero provider calls
    # The DENIED attempt's own provider was allocated but never called.
    assert flock.providers[-1].call_count == 0
    # Rolling reset after 60s -> same valid session is admitted again.
    clock.advance(61)
    assert service.start_case_generation(GOLDEN_PROMPT, anonymous_quota_session_id=sid).status == "PUBLISHED"


# --------------------------------------------------------------------------- #
# API boundary — the sanitized 429 envelope carries the exact closed reasonCode
# --------------------------------------------------------------------------- #

def test_map_service_error_admission_reason_codes_boundary():
    """map_service_error emits the exact envelope for EVERY closed reason code:
    HTTP 429, code ADMISSION_DENIED, sanitized message, exact reasonCode and
    no extra detail (no more, no less)."""
    from app.api.v1.errors import map_service_error
    from app.services.generation import AdmissionDeniedError

    for code in ALL_REASON_CODES:
        exc = AdmissionDeniedError("internal human string", reason_code=code)
        http_exc = map_service_error(exc)
        assert http_exc.status_code == 429
        assert http_exc.detail == {
            "code": "ADMISSION_DENIED",
            "message": "Generation capacity exhausted",
            "details": None,
            "reasonCode": code.value,
        }
        # The internal human reason string must NEVER surface.
        assert "internal human string" not in repr(http_exc.detail)


def test_map_service_error_unknown_reason_falls_back_conservative():
    """An AdmissionDeniedError constructed WITHOUT a known code still answers a
    bounded CLOSED conservative capacity reason (Phase36 §30), never None."""
    from app.api.v1.errors import map_service_error
    from app.services.generation import AdmissionDeniedError

    http_exc = map_service_error(AdmissionDeniedError("unclassified"))
    assert http_exc.status_code == 429
    assert http_exc.detail["code"] == "ADMISSION_DENIED"
    assert http_exc.detail["reasonCode"] == (
        "GLOBAL_GENERATION_WINDOW_LIMIT"  # the documented conservative default
    )


def test_api_session_generation_limit_envelope(store, database_url):
    """POST /cases beyond the per-session window answers the FULL sanitized
    envelope with the exact SESSION_GENERATION_LIMIT reasonCode and no secret
    fields."""
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    app = create_app(
        Settings(
            database_url=database_url,
            max_generations_per_session_per_window=1,
            max_concurrent_generations=1,
        )
    )
    try:
        with TestClient(app) as client:
            token, _body = create_session(client)
            first = create_case(client, token)
            assert first["status"] == "PUBLISHED"
            res = client.post(
                "/api/v1/cases",
                json={"prompt": "Another mystery"},
                headers=auth(token),
            )
            assert res.status_code == 429
            body = res.json()
            assert body == {
                "error": {
                    "code": "ADMISSION_DENIED",
                    "message": "Generation capacity exhausted",
                    "details": None,
                    "reasonCode": "SESSION_GENERATION_LIMIT",
                }
            }
            assert_sanitized_error(res.text)
            # No secret fields anywhere in the response.
            assert "anonymousSessionToken" not in res.text
            assert "creatorToken" not in res.text
            assert "127.0.0.1" not in res.text
            assert_no_hidden_leaks(body)
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


def test_api_anonymous_session_capacity_envelope(store, database_url):
    """POST /sessions/anonymous beyond a capped store answers the FULL
    sanitized envelope with ANONYMOUS_SESSION_CAPACITY_LIMIT."""
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app
    from app.services.admission import DurableAdmissionController
    from app.services.generation import GenerationService

    app = create_app(
        Settings(
            database_url=database_url,
            max_concurrent_generations=2,
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
        max_concurrent_generations=2,
        max_concurrent_generations_global=8,
        max_generations_per_session_per_window=20,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=(
            app.state.settings.anonymous_quota_session_ttl_seconds
        ),
        global_window_seconds=app.state.settings.global_generation_window_seconds,
        max_sessions=2,
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
            for _ in range(3):
                res = client.post("/api/v1/sessions/anonymous")
                statuses.append(res.status_code)
                last = res
            assert statuses == [201, 201, 429], statuses
            assert last is not None
            body = last.json()
            assert body == {
                "error": {
                    "code": "ADMISSION_DENIED",
                    "message": "Generation capacity exhausted",
                    "details": None,
                    "reasonCode": "ANONYMOUS_SESSION_CAPACITY_LIMIT",
                }
            }
            assert_sanitized_error(last.text)
            assert "anonymousSessionToken" not in last.text
            assert_no_hidden_leaks(body)
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


def test_api_too_many_requests_stays_distinct_without_reason_code(store, database_url):
    """The per-IP POST /cases budget keeps the DISTINCT TOO_MANY_REQUESTS code and
    NEVER carries an admission reasonCode (Phase36 §18)."""
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    app = create_app(
        Settings(
            database_url=database_url,
            generation_limit_per_ip_per_hour=1,
            max_generations_per_session_per_window=20,
            max_concurrent_generations=2,
        )
    )
    try:
        with TestClient(app) as client:
            token, _body = create_session(client)
            first = create_case(client, token)
            assert first["status"] == "PUBLISHED"
            res = client.post(
                "/api/v1/cases",
                json={"prompt": "A second case"},
                headers=auth(token),
            )
            assert res.status_code == 429
            body = res.json()
            assert body["error"]["code"] == "TOO_MANY_REQUESTS"
            assert "reasonCode" not in body["error"]
            assert_sanitized_error(res.text)
            assert "anonymousSessionToken" not in res.text
            assert_no_hidden_leaks(body)
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()