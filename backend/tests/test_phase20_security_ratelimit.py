"""Phase 20 — PD-SEC-02 public-admission rate limiting + quota-window renewal
(+ PD-SEC-08 CSP presence + PD-SEC-09 provider depth preflight).

Covers the §8.2 regression matrix:

  1. repeated anonymous session creation is rate-limited
  2. new sessions cannot trivially bypass generation caps (per-IP binds)
  3. spoofed forwarded IP header does not bypass limits when proxy trust is off
  4. trusted proxy mode uses the approved forwarded identity path
  5. global generation concurrency is bounded
  6. rolling global quota resets correctly after time advances (ManualClock)
  7. quota recovery does not require process restart
  8. quota decisions are deterministic under concurrency
  9. failed generations count per the documented reservation-attempts policy
 10. rate-limit responses are safe and do not leak internals
 11. legitimate Easy/Medium/Hard flow remains usable

Plus: PD-SEC-08 — the full CSP baseline is served on HTML/static, API and
429 responses; PD-SEC-09 — deep-nesting provider JSON is rejected with a clean
bounded error instead of an uncaught RecursionError.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.core.ratelimit import (  # noqa: E402
    AnonymousSessionLimiter,
    SlidingWindowRateLimiter,
    resolve_client_ip,
)
from app.generation.admission import (  # noqa: E402
    AdmissionController,
    AdmissionDenied,
)
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.parser import collect_issues  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.services.admission import DurableAdmissionController  # noqa: E402
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


def _build_path(database_url: str, **overrides):
    """Migrate + create_app with Phase 20 settings (defaults generous enough
    for the demo flow; per-test overrides tighten the limit under test)."""
    from conftest import upgrade_db
    from app.main import create_app

    upgrade_db(database_url)
    settings = Settings(
        database_url=database_url,
        cors_allowed_origins=["http://localhost:5173"],
        max_concurrent_generations=4,
        max_generations_per_session_per_window=20,
        max_concurrent_generations_global=8,
        max_generations_global_per_window=50,
        **overrides,
    )
    return create_app(settings)


# --------------------------------------------------------------------------- #
# limiter primitives (deterministic ManualClock)
# --------------------------------------------------------------------------- #


def test_limiter_rolling_window_recovers_after_time_advances():
    clock = ManualClock()
    limiter = SlidingWindowRateLimiter(clock=clock, limit=2, window_seconds=10)
    assert limiter.allow("10.0.0.1") is True
    assert limiter.allow("10.0.0.1") is True
    assert limiter.allow("10.0.0.1") is False   # window full
    assert limiter.count("10.0.0.1") == 2
    clock.advance(11)                            # the whole window aged out
    assert limiter.allow("10.0.0.1") is True     # recovered without a restart
    assert limiter.count("10.0.0.1") == 1


def test_limiter_is_per_key():
    clock = ManualClock()
    limiter = SlidingWindowRateLimiter(clock=clock, limit=2, window_seconds=10)
    assert limiter.allow("10.0.0.1") and limiter.allow("10.0.0.1")
    assert limiter.allow("10.0.0.2") is True     # independent bucket
    assert limiter.allow("10.0.0.1") is False


def test_limiter_burst_is_atomic_exactly_limit_succeed():
    """Determinism under concurrency: a thread burst can never double-spend
    a window slot (every mutating call runs under one lock)."""
    clock = ManualClock(start_time=100.0)
    limiter = SlidingWindowRateLimiter(clock=clock, limit=10, window_seconds=60)
    results: list[bool] = [False] * 80

    def worker(index: int) -> None:
        results[index] = limiter.allow("203.0.113.7")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(80)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(results) == 10
    assert limiter.count("203.0.113.7") == 10


def test_anonymous_session_limiter_global_ceiling():
    clock = ManualClock()
    limiter = AnonymousSessionLimiter(
        clock=clock,
        per_ip_limit=100,
        per_ip_window_seconds=600,
        global_limit=3,
        global_window_seconds=60,
    )
    for index in range(3):
        assert limiter.allow(f"198.51.100.{index}") == (True, "")
    # The 4th IP is denied by the GLOBAL ceiling (its per-IP bucket is empty).
    allowed, reason = limiter.allow("198.51.100.55")
    assert allowed is False
    assert reason == "global"


# --------------------------------------------------------------------------- #
# §6.2 trusted proxy: forwarded-IP identity rules (tests 3 and 4)
# --------------------------------------------------------------------------- #


class _FakeRequest:
    def __init__(self, client_host: str, headers: dict[str, str] | None = None) -> None:
        self.client = type("Client", (), {"host": client_host})()
        self.headers = headers or {}


def test_resolve_client_ip_never_trusts_forwarded_header_when_trust_off():
    request = _FakeRequest(
        client_host="127.0.0.1", headers={"x-forwarded-for": "203.0.113.66"}
    )
    assert resolve_client_ip(request, trust_proxy=False) == "127.0.0.1"


def test_resolve_client_ip_trust_on_uses_leftmost_forwarded_entry():
    # Single trusted TLS edge: LEFT-MOST X-Forwarded-For entry is the client.
    request = _FakeRequest(
        client_host="10.0.0.5",
        headers={"x-forwarded-for": "198.51.100.9, 10.0.0.5"},
    )
    assert resolve_client_ip(request, trust_proxy=True) == "198.51.100.9"
    # Missing header -> the direct socket peer (never an empty identity).
    bare = _FakeRequest(client_host="10.0.0.5")
    assert resolve_client_ip(bare, trust_proxy=True) == "10.0.0.5"


# --------------------------------------------------------------------------- #
# §6 anywhere an API app is needed
# --------------------------------------------------------------------------- #


def _anon_limited_app(database_url, per_ip=2, global_limit=100, trust_proxy=False):
    return _build_path(
        database_url,
        ANON_SESSION_LIMIT_PER_IP_PER_10_MIN=per_ip,
        ANON_SESSION_GLOBAL_LIMIT_PER_MIN=global_limit,
        TRUST_PROXY=trust_proxy,
    )


def test_01_repeated_anonymous_session_creation_is_rate_limited(database_url):
    """§8.2 #1 — after the per-IP budget the next session creation is a 429."""
    from fastapi.testclient import TestClient

    app = _anon_limited_app(database_url, per_ip=4)
    try:
        with TestClient(app) as client:
            statuses = []
            for _ in range(5):
                res = client.post("/api/v1/sessions/anonymous")
                statuses.append(res.status_code)
            assert statuses == [201, 201, 201, 201, 429]
            res = client.post("/api/v1/sessions/anonymous")
            assert res.status_code == 429
            body = res.json()
            assert body["error"]["code"] == "TOO_MANY_REQUESTS"
            assert_sanitized_error(res.text)
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


def test_03_spoofed_forwarded_header_does_not_bypass_when_trust_off(database_url):
    """§8.2 #3 — TRUST_PROXY=false: a hostile X-Forwarded-For never changes the
    rate-limit identity (testclient), so it cannot bypass the per-IP window."""
    from fastapi.testclient import TestClient

    app = _anon_limited_app(database_url, per_ip=3)
    try:
        with TestClient(app) as client:
            hostile = {"X-Forwarded-For": "203.0.113.1"}
            assert client.post("/api/v1/sessions/anonymous", headers=hostile).status_code == 201
            assert client.post("/api/v1/sessions/anonymous").status_code == 201
            assert client.post("/api/v1/sessions/anonymous", headers=hostile).status_code == 201
            # A DIFFERENT spoofed IP still maps to the same socket peer -> denied.
            switched = client.post(
                "/api/v1/sessions/anonymous", headers={"X-Forwarded-For": "203.0.113.99"}
            )
            assert switched.status_code == 429
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


def test_04_trusted_proxy_mode_uses_approved_forwarded_identity_path(database_url):
    """§8.2 #4 — TRUST_PROXY=true: LEFT-MOST X-Forwarded-For is honored; each
    forwarded identity has its own window; fallback is the socket peer."""
    from fastapi.testclient import TestClient

    app = _anon_limited_app(database_url, per_ip=2, trust_proxy=True)
    try:
        with TestClient(app) as client:
            assert client.post(
                "/api/v1/sessions/anonymous", headers={"X-Forwarded-For": "198.51.100.1"}
            ).status_code == 201
            assert client.post(
                "/api/v1/sessions/anonymous", headers={"X-Forwarded-For": "198.51.100.1"}
            ).status_code == 201
            denied = client.post(
                "/api/v1/sessions/anonymous", headers={"X-Forwarded-For": "198.51.100.1"}
            )
            assert denied.status_code == 429  # that forwarded identity is spent
            # A DIFFERENT forwarded identity is unaffected.
            assert client.post(
                "/api/v1/sessions/anonymous", headers={"X-Forwarded-For": "198.51.100.2"}
            ).status_code == 201
            # "198.51.100.9, 10.0.0.5": LEFT-MOST is the client, the second is
            # the proxy hop — 10.0.0.5 is never the identity.
            proxy_chain = client.post(
                "/api/v1/sessions/anonymous",
                headers={"X-Forwarded-For": "198.51.100.9, 10.0.0.5"},
            )
            assert proxy_chain.status_code == 201
            assert client.post(
                "/api/v1/sessions/anonymous",
                headers={"X-Forwarded-For": "198.51.100.9, 10.0.0.5"},
            ).status_code == 201
            third = client.post(
                "/api/v1/sessions/anonymous",
                headers={"X-Forwarded-For": "198.51.100.9, 10.0.0.5"},
            )
            assert third.status_code == 429  # 198.51.100.9 spent its window
            # Header absent -> the direct socket peer (testclient) is used.
            assert client.post("/api/v1/sessions/anonymous").status_code == 201
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


def test_10_rate_limit_responses_are_safe_no_internal_leak(database_url):
    """§8.2 #10 — the 429 envelope carries no internals (no reason strings, no
    counters, no window/limiter names, no stack/SQL/path material)."""
    from fastapi.testclient import TestClient

    app = _anon_limited_app(database_url, per_ip=1)
    try:
        with TestClient(app) as client:
            client.post("/api/v1/sessions/anonymous")
            res = client.post("/api/v1/sessions/anonymous")
            assert res.status_code == 429
            body = res.json()
            assert_no_hidden_leaks(body)
            assert_sanitized_error(res.text)
            text = res.text.lower()
            for leaked in ("per_ip", "global", "window", "limiter", "quota", "reservation"):
                assert leaked not in text, f"429 leaks internal marker {leaked!r}"
            # Cache-Control: no-store audit (§20): private/playthrough responses
            # (rate-limited admission included) are never cacheable.
            assert res.headers.get("cache-control") == "no-store"
            assert res.headers.get("pragma") == "no-cache"
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


# --------------------------------------------------------------------------- #
# §7 generation rate limiting (tests 2, 5, 6, 7, 8, 9, 11)
# --------------------------------------------------------------------------- #


def test_02_new_sessions_cannot_bypass_generation_caps(database_url):
    """§8.2 #2 — per-IP generation budget binds ACROSS sessions: minting fresh
    sessions never resets it."""
    from fastapi.testclient import TestClient

    app = _build_path(
        database_url,
        GENERATION_LIMIT_PER_IP_PER_HOUR=2,
        ANON_SESSION_LIMIT_PER_IP_PER_10_MIN=100,
    )
    try:
        with TestClient(app) as client:
            token_a, _ = create_session(client)
            assert create_case(client, token_a)["status"] == "PUBLISHED"
            token_b, _ = create_session(client)   # fresh session minted
            assert create_case(client, token_b)["status"] == "PUBLISHED"
            denied = client.post(
                "/api/v1/cases",
                json={"prompt": GOLDEN_PROMPT},
                headers=auth(token_b),  # a THIRD attempt from the same peer IP
            )
            assert denied.status_code == 429
            assert denied.json()["error"]["code"] == "TOO_MANY_REQUESTS"
            assert_sanitized_error(denied.text)
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


def test_05_global_generation_concurrency_is_bounded():
    """§8.2 #5 — MAX_CONCURRENT_GENERATIONS_GLOBAL=1 denies a concurrent start
    and recovers after release."""
    clock = ManualClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=4,
        max_concurrent_generations_global=1,
        max_generations_per_session_per_window=10,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_seconds=3600,
    )
    session = admission.create_anonymous_quota_session()
    assert admission.admit_generation(session.session_id).admitted is True
    with pytest.raises(AdmissionDenied) as excinfo:
        admission.admit_generation(session.session_id)
    assert "global concurrency" in excinfo.value.decision.reason
    assert admission.global_active_concurrency == 1
    admission.release_generation(session.session_id)
    assert admission.admit_generation(session.session_id).admitted is True


def test_06_rolling_global_quota_resets_after_time_advances():
    """§8.2 #6 — ManualClock: when time reaches window_end a fresh window
    starts and the rolled counter resets atomically."""
    clock = ManualClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=4,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=10,
        max_generations_global_per_window=2,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_seconds=60,
    )
    session = admission.create_anonymous_quota_session()
    assert admission.admit_generation(session.session_id).admitted is True
    second = admission.admit_generation(session.session_id)
    assert second.admitted is True
    assert admission.global_window_generations == 2
    with pytest.raises(AdmissionDenied) as excinfo:
        admission.admit_generation(session.session_id)
    assert "global generation window exhausted" in excinfo.value.decision.reason
    # Time advances past the rolling window end -> NEW window + reset.
    clock.advance(61)
    admitted = admission.admit_generation(session.session_id)
    assert admitted.admitted is True
    assert admission.global_window_generations == 1   # fresh window counter
    assert admission.global_window_end > float(clock.now())


def test_07_quota_recovery_without_process_restart(database_url):
    """§8.2 #7 — service level: the global window renews in-process after its
    expiry; no restart is needed (regression for "reject-forever until
    restart")."""
    from app.persistence.store import Store
    from app.services.generation import AdmissionDeniedError, GenerationService

    from conftest import upgrade_db

    upgrade_db(database_url)
    clock = ManualClock(start_time=0.0)
    store = Store(database_url)
    try:
        service = GenerationService(
            settings=Settings(
                database_url=database_url,
                max_generations_global_per_window=1,
                global_generation_window_seconds=60,
                max_generations_per_session_per_window=10,
            ),
            store=store,
            clock=clock,
        )
        session = service.create_anonymous_quota_session()
        sid = session.anonymous_quota_session_id
        first = service.start_case_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=sid
        )
        assert first.status == "PUBLISHED"
        # Global window (0..60) is now at its limit; the session still has quota.
        with pytest.raises(AdmissionDeniedError):
            service.start_case_generation(GOLDEN_PROMPT, anonymous_quota_session_id=sid)
        # WITHOUT restarting anything: advance the clock past window_end …
        clock.advance(61)
        second = service.start_case_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=sid
        )
        assert second.status == "PUBLISHED"
    finally:
        store.dispose()


def test_08_quota_decisions_deterministic_under_concurrency():
    """§8.2 #8 — a thread burst over the (locked) DurableAdmissionController
    admits EXACTLY the global window limit; never more, never double-spend."""
    clock = ManualClock(start_time=1000.0)
    ids = IdSource()
    admission = DurableAdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=10,
        max_concurrent_generations_global=100,
        max_generations_per_session_per_window=10,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_seconds=3600,
    )
    sessions = [
        admission.create_anonymous_quota_session() for _ in range(60)
    ]
    results: list[bool] = [False] * 60

    def worker(index: int) -> None:
        try:
            decision = admission.admit_generation(sessions[index].session_id)
            results[index] = decision.admitted
        except AdmissionDenied:
            results[index] = False

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(60)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(results) == 20  # exactly the rolling global window capacity
    assert admission.global_window_generations == 20
    # Releasing a concurrency slot does NOT refund a window slot: the window
    # is still exhausted until it rolls over.
    admission.release_generation(sessions[0].session_id)
    with pytest.raises(AdmissionDenied):
        admission.admit_generation(sessions[0].session_id)


def test_09_failed_generations_count_per_reservation_policy(database_url):
    """§8.2 #9 — documented policy: FAILED generations count as ATTEMPTED
    generations (reservation counts; admission happens BEFORE the provider
    call, REQUIREMENTS 32.10). A provider-failing attempt still consumes the
    per-session + global-window reservation."""
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    from conftest import upgrade_db

    upgrade_db(database_url)
    store = Store(database_url)
    try:
        failing = FakeProvider({GenerationStage.CASE_TRUTH: ["exception"]})
        service = GenerationService(
            settings=Settings(
                database_url=database_url,
                max_concurrent_generations=4,
                max_generations_per_session_per_window=10,
            ),
            store=store,
            provider_factory=lambda: failing,
        )
        session = service.create_anonymous_quota_session()
        sid = session.anonymous_quota_session_id
        started = service.start_case_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=sid
        )
        assert started.status == "FAILED"
        # The FAILED attempt consumed the reservation (durable + in-memory).
        assert store.get_session(sid).generations_count == 1
        assert service._admission.session_generations(sid) == 1
        assert service._admission.global_window_generations == 1
        # Concurrency was released for the failed attempt (no leak).
        assert service._admission.global_active_concurrency == 0
    finally:
        store.dispose()


def test_09b_route_level_per_ip_counts_failed_attempts(database_url, tmp_path):
    """Route-level corollary: the per-IP generation limiter counts ATTEMPTS;
    a generation that FAILS still consumes the per-IP slot (the next attempt
    is denied even though the previous one never published)."""
    from fastapi.testclient import TestClient

    from app.core.config import Settings as _S
    from app.main import create_app
    from conftest import upgrade_db

    script = tmp_path / "failing_provider.json"
    script.write_text('{"case_truth": ["exception"]}', encoding="utf-8")
    upgrade_db(database_url)
    app = create_app(
        _S(
            database_url=database_url,
            generation_limit_per_ip_per_hour=1,
            anon_session_limit_per_ip_per_10_min=100,
            fake_provider_script=str(script),
        )
    )
    try:
        with TestClient(app) as client:
            token, _ = create_session(client)
            res = client.post(
                "/api/v1/cases",
                json={"prompt": GOLDEN_PROMPT},
                headers=auth(token),
            )
            assert res.status_code == 201            # admitted (then FAILED)
            assert res.json()["status"] == "FAILED"
            denied = client.post(
                "/api/v1/cases",
                json={"prompt": GOLDEN_PROMPT},
                headers=auth(token),
            )
            assert denied.status_code == 429          # per-IP slot consumed
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


def test_11_legitimate_easy_medium_hard_flow_remains_usable(database_url):
    """§8.2 #11 — a normal Easy/Medium/Hard flow still publishes under the
    shipped default limits (per-session, per-IP, global concurrency, rolling
    global quota all generous enough for the demo)."""
    from fastapi.testclient import TestClient

    app = _build_path(database_url)
    try:
        with TestClient(app) as client:
            for difficulty, prompt in (
                ("easy", "A simple mystery: Victim sarah_miller."),
                ("medium", GOLDEN_PROMPT),
                ("hard", GOLDEN_PROMPT),
            ):
                token, _ = create_session(client)
                created = create_case(client, token, prompt=prompt, difficulty=difficulty)
                assert created["status"] == "PUBLISHED", created
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


# --------------------------------------------------------------------------- #
# PD-SEC-08 — CSP on HTML/static + API responses (documented baseline)
# --------------------------------------------------------------------------- #


def test_phase20_csp_present_on_spa_html_assets_and_api_and_429(database_url, tmp_path):
    """The Phase 20 CSP baseline is served on the SPA shell, on a built JS
    asset, on an API response AND on a 429 rate-limit response (defense in
    depth everywhere, same policy)."""
    from fastapi.testclient import TestClient

    from app.main import _CSP_BASELINE

    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        "<!doctype html><html><head><script type=module src=./assets/app.js>"
        "</script></head><body>PD</body></html>\n",
        encoding="utf-8",
    )
    assets = build / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("window.__cspTest = 1;\n", encoding="utf-8")

    app = _build_path(
        database_url,
        ANON_SESSION_LIMIT_PER_IP_PER_10_MIN=1,
        static_dir=str(build),
    )
    try:
        with TestClient(app) as client:
            index = client.get("/")
            asset = client.get("/assets/app.js")
            health = client.get("/api/v1/health")
            assert index.status_code == 200
            assert asset.status_code == 200
            assert health.status_code == 200
            for res in (index, asset, health):
                assert res.headers["content-security-policy"] == _CSP_BASELINE
            client.post("/api/v1/sessions/anonymous")  # spend the only slot
            denied = client.post("/api/v1/sessions/anonymous")
            assert denied.status_code == 429
            assert denied.headers["content-security-policy"] == _CSP_BASELINE
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


# --------------------------------------------------------------------------- #
# PD-SEC-09 — bounded provider-JSON depth preflight
# --------------------------------------------------------------------------- #


def test_phase20_deep_nesting_provider_json_rejected_cleanly():
    """PD-SEC-09 — a deep nesting bomb in provider output is rejected with a
    sanitized parse issue; it NEVER raises an uncaught RecursionError."""
    bomb = "[" * 50000 + "]" * 50000   # 100k chars, 50k nesting levels
    assert len(bomb) <= 200000          # fits MAX_PROVIDER_OUTPUT_CHARS
    issues = collect_issues(GenerationStage.CASE_TRUTH, bomb)
    assert issues
    assert any("not valid JSON" in issue for issue in issues)
    flattened = " ".join(issues).lower()
    assert "recursionerror" not in flattened and "traceback" not in flattened


def test_phase20_nested_provider_json_below_limit_parses_normally():
    """PD-SEC-09 — content far below the nesting bound is parsed exactly as
    before (no regression for legitimate stage payloads)."""
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

    payload = GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE]
    issues = collect_issues(GenerationStage.EVIDENCE, payload)
    # The golden evidence payload parses cleanly (0 issues).
    assert issues == ()
