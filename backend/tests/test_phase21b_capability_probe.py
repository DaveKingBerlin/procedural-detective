"""Phase21B Finding 4 — bounded unauthenticated Ollama capability probing.

When GENERATION_PROVIDER=ollama, `GET /api/v1/generation-capabilities` used to
fire a FRESH synchronous `/api/tags` probe on EVERY public request, with NO
cache, NO single-flight, and using the GENERATION timeout (up to 300s). This
suite pins the bounded-probe closure at the authoritative service seam
(``app.services.generation_capabilities``) and at the endpoint:

  1. 100 concurrent capability requests -> at most ONE active provider probe
     per cache window (all 100 share the same cached result);
  2. cached response returned safely (sequential reuse);
  3. a slow/hung transport respects the DEDICATED small probe timeout
     (independent of OLLAMA_TIMEOUT_SECONDS) and fails closed sanitized;
  4. cache expiration causes exactly ONE new probe (ManualClock past TTL);
  5. failure results stay sanitized — no URL / LAN IP / raw provider error in
     any response;
  6. generation requests still perform their OWN normal provider checks (the
     bounded probe never short-circuits OllamaProvider / GenerationService);
  7. capability-endpoint per-IP rate limit: 429 beyond the ceiling, and a
     spoofed X-Forwarded-For never bypasses while TRUST_PROXY=false.

Deterministic time via ``ManualClock``; every transport is an injected fake
(single-flight is proven with a BLOCKING counting transport). The autouse
conftest network block stays untouched.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.ollama_provider import (  # noqa: E402
    OllamaProvider,
    OLLAMA_CHAT_ENDPOINT,
)
from app.generation.provider import GenerateRequest, GenerationStage  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import generation_capabilities as cap_service  # noqa: E402

OLLAMA_MODEL = "llama3.2:3b"


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #


class CountingTransport:
    """Injects canned /api/tags responses and counts every get() call.

    ``block_first`` (an Event) makes the FIRST probe block until the test
    releases it, so a concurrent burst genuinely overlaps one in-flight probe.
    """

    def __init__(
        self,
        models=(OLLAMA_MODEL,),
        *,
        block_first: threading.Event | None = None,
    ):
        self.models = list(models)
        self.block_first = block_first
        self._lock = threading.Lock()
        self.get_calls: list[tuple[str, float]] = []

    def get(self, url: str, timeout: float) -> tuple[int, bytes]:
        with self._lock:
            self.get_calls.append((url, float(timeout)))
            call_index = len(self.get_calls)
        if call_index == 1 and self.block_first is not None:
            self.block_first.wait(timeout + 30)
        body = json.dumps({"models": [{"name": name} for name in self.models]}).encode()
        return 200, body

    def post_json(self, url: str, payload, timeout: float):  # pragma: no cover
        raise AssertionError("a capability probe must never POST")

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self.get_calls)


class HungTransport:
    """Simulates a hung Ollama socket: records the timeout it was CAPPED to,
    blocks past it, then times out — the adapter must return sanitized False
    within ~the small probe timeout (never the 300s generation timeout)."""

    def __init__(self):
        self.observed_timeouts: list[float] = []

    def get(self, url: str, timeout: float) -> tuple[int, bytes]:
        self.observed_timeouts.append(float(timeout))
        time.sleep(float(timeout) + 0.2)
        raise TimeoutError("ollama probe timed out")

    def post_json(self, url: str, payload, timeout: float):  # pragma: no cover
        raise AssertionError("a capability probe must never POST")


class _RawErrorTransport:
    """A failing transport whose body carries hostile network/token detail."""

    def get(self, url: str, timeout: float) -> tuple[int, bytes]:
        return 500, b'{"error": "http://192.168.1.5:11434 LAN_SECRET"}'

    def post_json(self, url: str, payload, timeout: float):  # pragma: no cover
        raise AssertionError("no POST")


def _ollama_settings(database_url: str, **overrides) -> Settings:
    kwargs = dict(
        database_url=database_url,
        generation_provider="ollama",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.2:3b",
        capability_probe_timeout_seconds=5.0,
        capability_cache_ttl_seconds=15.0,
        max_concurrent_capability_probes=1,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


# --------------------------------------------------------------------------- #
# 1. 100 concurrent requests -> ONE active provider probe per cache window
# --------------------------------------------------------------------------- #


def test_100_concurrent_requests_share_one_probe_per_cache_window():
    """A 100-thread burst over the SAME (settings, transport) key runs ONE
    in-flight probe (single-flight ceiling 1) and every thread receives the
    exact same cached result."""
    clock = ManualClock(start_time=100.0)
    cap_service.reset_capability_probe_cache(clock=clock)
    settings = _ollama_settings("sqlite:///tmp-concurrent.db")
    blocked = threading.Event()
    transport = CountingTransport(block_first=blocked)
    results: list = [None] * 100
    errors: list = [None] * 100
    start = threading.Barrier(101)

    def worker(index: int) -> None:
        try:
            start.wait(timeout=10)
            results[index] = cap_service.ollama_available(settings, transport)
        except Exception as exc:  # noqa: BLE001 - recorded for the assertion
            errors[index] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for thread in threads:
        thread.start()
    # Release the start gate together with the workers so the burst arrives as
    # close to simultaneously as deterministic test scheduling allows.
    start.wait(timeout=10)
    # Wait until the (single) first probe is IN FLIGHT (blocking), so every
    # other thread observes the single-flight marker, then release it.
    deadline = time.monotonic() + 10
    while transport.call_count < 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert transport.call_count == 1
    blocked.set()
    for thread in threads:
        thread.join(timeout=10)
    assert start.broken is False
    assert all(err is None for err in errors)
    # EXACTLY one active provider probe for the whole burst.
    assert transport.call_count == 1
    # All 100 requests got the same cached (True, "") result.
    assert all(result == (True, "") for result in results)
    # A follow-up request inside the same window is served from the cache.
    again = cap_service.ollama_available(settings, transport)
    assert again == (True, "")
    assert transport.call_count == 1


# --------------------------------------------------------------------------- #
# 2. cached response returned safely (sequential reuse)
# --------------------------------------------------------------------------- #


def test_cached_response_returned_safely_within_ttl():
    cap_service.reset_capability_probe_cache(clock=ManualClock(start_time=0.0))
    settings = _ollama_settings("sqlite:///tmp-cached.db")
    transport = CountingTransport()
    assert cap_service.ollama_available(settings, transport) == (True, "")
    assert transport.call_count == 1
    # Second request inside the TTL window: served from cache, no new probe.
    assert cap_service.ollama_available(settings, transport) == (True, "")
    assert transport.call_count == 1


# --------------------------------------------------------------------------- #
# 3. slow/hung transport respects the DEDICATED small probe timeout
# --------------------------------------------------------------------------- #


def test_hung_transport_respects_small_dedicated_probe_timeout():
    """The probe is capped to CAPABILITY_PROBE_TIMEOUT_SECONDS (a SMALL value),
    INDEPENDENT of a huge OLLAMA_TIMEOUT_SECONDS (300 here). The hung transport
    must receive the small cap and the call must return sanitized (False, ...)
    within roughly the small timeout, NOT the 300s generation timeout."""
    cap_service.reset_capability_probe_cache(clock=ManualClock(start_time=0.0))
    settings = _ollama_settings(
        "sqlite:///tmp-hung.db",
        capability_probe_timeout_seconds=1,  # small dedicated probe cap
        ollama_timeout_seconds=300,          # generation path stays large
    )
    transport = HungTransport()
    started = time.monotonic()
    available, detail = cap_service.ollama_available(settings, transport)
    elapsed = time.monotonic() - started
    assert available is False
    assert detail == "not available"  # sanitized — never the transport detail
    assert elapsed < 3.0, f"probe took {elapsed:.2f}s, far over the small cap"
    # The transport observed the DEDICATED small timeout, not the 300s one.
    assert transport.observed_timeouts == [1.0]
    # The failure is negative-cached: a follow-up within the window returns
    # instantly with NO further probe attempt (a hung server is never re-probed
    # by every request).
    started = time.monotonic()
    again = cap_service.ollama_available(settings, transport)
    assert again == (False, "not available")
    assert time.monotonic() - started < 0.5  # served from the negative cache
    assert transport.observed_timeouts == [1.0]  # still one probe total


# --------------------------------------------------------------------------- #
# 4. cache expiration causes exactly ONE new probe
# --------------------------------------------------------------------------- #


def test_cache_expiration_causes_exactly_one_new_probe():
    clock = ManualClock(start_time=0.0)
    cap_service.reset_capability_probe_cache(clock=clock)
    settings = _ollama_settings(
        "sqlite:///tmp-expiry.db", capability_cache_ttl_seconds=15.0
    )
    transport = CountingTransport()
    assert cap_service.ollama_available(settings, transport) == (True, "")
    assert transport.call_count == 1
    # Fresh window: served from cache.
    clock.advance(14.9)
    assert cap_service.ollama_available(settings, transport) == (True, "")
    assert transport.call_count == 1
    # Past the TTL: exactly ONE new probe, then cached again.
    clock.advance(0.2)  # t=15.1 > 15.0
    assert cap_service.ollama_available(settings, transport) == (True, "")
    assert transport.call_count == 2
    clock.advance(14.9)
    assert cap_service.ollama_available(settings, transport) == (True, "")
    assert transport.call_count == 2


# --------------------------------------------------------------------------- #
# 5. failure results stay sanitized (no URL / IP / raw provider error)
# --------------------------------------------------------------------------- #


def test_failure_result_is_sanitized_at_the_service_seam():
    def _exploding_probe(settings_, transport=None):
        raise RuntimeError("http://192.168.1.5:11434 LAN_SECRET boom")

    cap_service.reset_capability_probe_cache(
        clock=ManualClock(start_time=0.0), probe=_exploding_probe
    )
    settings = _ollama_settings("sqlite:///tmp-sanitized.db")
    # A hostile injected probe that RAISES: the seam degrades to the exact
    # sanitized tuple and never raises / never leaks.
    assert cap_service.ollama_available(settings) == (False, "not available")

    # A failing transport (HTTP 500 + LAN detail in the body) stays sanitized.
    cap_service.reset_capability_probe_cache(clock=ManualClock(start_time=0.0))
    assert cap_service.ollama_available(settings, _RawErrorTransport()) == (
        False,
        "not available",
    )


def test_endpoint_failure_stays_sanitized(database_url):
    """The public DTO never leaks any hostile probe detail — exercised end to
    end through the API with the failure injected at the service seam."""

    def _exploding_probe(settings_, transport=None):
        raise RuntimeError("http://192.168.1.5:11434 LAN_SECRET boom")

    cap_service.reset_capability_probe_cache(
        clock=ManualClock(start_time=0.0), probe=_exploding_probe
    )
    application = create_app(
        _ollama_settings(database_url, llm_api_key="sekret-value")
    )
    try:
        with TestClient(application) as client:
            response = client.get(
                "/api/v1/generation-capabilities",
                headers={"X-Forwarded-For": "203.0.113.9"},
            )
    finally:
        _dispose(application)
    assert response.status_code == 200
    assert response.json()["modes"][1]["available"] is False
    text = response.text
    for token in (
        "11434",
        "127.0.0.1",
        "192.168",
        "host.docker.internal",
        "sekret-value",
        "not available",
        "LAN_SECRET",
    ):
        assert token not in text, token


# --------------------------------------------------------------------------- #
# the endpoint reuses the bounded probe cache
# --------------------------------------------------------------------------- #


def test_endpoint_shares_one_probe_across_sequential_requests(database_url):
    counting = {"calls": 0}

    def _counting_probe(settings_, transport=None):
        counting["calls"] += 1
        return (True, "")

    cap_service.reset_capability_probe_cache(
        clock=ManualClock(start_time=0.0), probe=_counting_probe
    )
    application = create_app(_ollama_settings(database_url))
    try:
        with TestClient(application) as client:
            first = client.get("/api/v1/generation-capabilities")
            second = client.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()
    assert counting["calls"] == 1  # ONE probe for BOTH requests (TTL cache)


# --------------------------------------------------------------------------- #
# 6. generation requests still perform their OWN normal provider checks
# --------------------------------------------------------------------------- #


def test_generation_path_is_not_shortcircuited_by_the_probe_cache():
    """The bounded probe feeds ONLY the capability DTO. Generation still builds
    its own OllamaProvider via the service factory (which performs its OWN
    structured-output capability probe) and calls the provider with the
    GENERATION timeout — never the capability-probe timeout, and never through
    the capability probe cache."""
    cap_service.reset_capability_probe_cache(clock=ManualClock(start_time=0.0))

    from app.generation import ollama_provider as provider_module
    from app.services.generation import GenerationService

    settings = _ollama_settings("sqlite:///tmp-gencheck.db")

    structured_calls = {"calls": 0}
    original_structured = provider_module.ollama_structured_output_supported

    def _counting_structured(settings_, transport=None):
        structured_calls["calls"] += 1
        return False

    provider_module.ollama_structured_output_supported = _counting_structured
    try:
        # store/publication are stubs: nothing here is ever persisted — only
        # the provider factory build path is exercised.
        service = GenerationService(
            settings=settings,
            store=object(),  # not a Store — never used by these calls
            publication=object(),  # bypasses PublicationService construction
            clock=ManualClock(start_time=0.0),
        )
        # The Ollama provider factory was already built EAGERLY at service
        # construction (the DEFAULT provider factory) — its own /api/version
        # capability probe ran exactly once.
        factory = service._provider_factory
        provider = factory()
    finally:
        provider_module.ollama_structured_output_supported = original_structured
    assert structured_calls["calls"] == 1  # its OWN normal capability probe
    assert isinstance(provider, OllamaProvider)
    # The capability-probe cache (the capability DTO path) was never consulted
    # on the way to building the generation provider.
    assert cap_service._probe_cache._cache == {}

    # And a generate() call uses the GENERATION timeout, not the 5s probe cap.
    recorded = {"timeout": None}
    seen = {"url": None}

    class RecordingTransport:
        def post_json(self, url, payload, timeout):
            recorded["timeout"] = float(timeout)
            seen["url"] = url
            return 200, json.dumps(
                {
                    "model": OLLAMA_MODEL,
                    "message": {"role": "assistant", "content": '{"ok": true}'},
                }
            ).encode()

        def get(self, url, timeout):  # pragma: no cover
            raise AssertionError("no GET on the generation path")

    generation_provider = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        model=OLLAMA_MODEL,
        timeout_seconds=7.5,  # the GENERATION timeout (deliberately != 5s probe)
        temperature=0.2,
        transport=RecordingTransport(),
    )
    result = generation_provider.generate(
        GenerateRequest(
            attempt_id="GA-test",
            stage=GenerationStage.CASE_TRUTH,
            prompt_context="x",
            locked=None,
            diagnostics=(),
        )
    )
    assert seen["url"] == "http://127.0.0.1:11434" + OLLAMA_CHAT_ENDPOINT
    assert recorded["timeout"] == 7.5  # the generation timeout, NOT 5.0
    assert result.content == '{"ok": true}'
    # The capability-probe cache is still untouched (generation never reads it).
    assert cap_service._probe_cache._cache == {}


# --------------------------------------------------------------------------- #
# 7. endpoint per-IP rate limit
# --------------------------------------------------------------------------- #


def test_endpoint_rate_limit_429_beyond_ceiling(database_url):
    cap_service.reset_capability_probe_cache(clock=ManualClock(start_time=0.0))
    application = create_app(
        _ollama_settings(database_url, capability_requests_per_ip_per_min=2)
    )
    try:
        with TestClient(application) as client:
            statuses = [
                client.get("/api/v1/generation-capabilities").status_code
                for _ in range(4)
            ]
    finally:
        _dispose(application)
    assert statuses == [200, 200, 429, 429]


def test_endpoint_rate_limit_envelope_is_sanitized(database_url):
    cap_service.reset_capability_probe_cache(clock=ManualClock(start_time=0.0))
    application = create_app(
        _ollama_settings(database_url, capability_requests_per_ip_per_min=1)
    )
    try:
        with TestClient(application) as client:
            assert client.get("/api/v1/generation-capabilities").status_code == 200
            denied = client.get("/api/v1/generation-capabilities")
            assert denied.status_code == 429
            body = denied.json()
            assert body["error"]["code"] == "TOO_MANY_REQUESTS"
            assert body["error"]["message"]
            assert body["error"]["details"] is None
    finally:
        _dispose(application)


def test_endpoint_xff_spoof_never_bypasses_while_trust_off(database_url):
    """TRUST_PROXY=false: the direct socket peer ("testclient") is the identity;
    rotating hostile X-Forwarded-For values can never reset the window."""
    cap_service.reset_capability_probe_cache(clock=ManualClock(start_time=0.0))
    application = create_app(
        _ollama_settings(
            database_url,
            capability_requests_per_ip_per_min=2,
            trust_proxy=False,
        )
    )
    try:
        with TestClient(application) as client:
            assert client.get("/api/v1/generation-capabilities").status_code == 200
            assert client.get("/api/v1/generation-capabilities").status_code == 200
            # Bucket spent on the socket peer; hostile XFF must not mint a new
            # per-IP budget while TRUST_PROXY=false.
            for spoof in ("203.0.113.7", "198.51.100.9", "192.0.2.1"):
                res = client.get(
                    "/api/v1/generation-capabilities",
                    headers={"X-Forwarded-For": spoof},
                )
                assert res.status_code == 429, spoof
    finally:
        _dispose(application)


def test_endpoint_trust_proxy_true_honors_forwarded_identity(database_url):
    """TRUST_PROXY=true (documented trusted TLS edge): the LEFT-MOST
    X-Forwarded-For entry is the identity, so two forwarded clients get their
    OWN windows (mirror of the Phase 20 session-identity contract)."""
    cap_service.reset_capability_probe_cache(clock=ManualClock(start_time=0.0))
    application = create_app(
        _ollama_settings(
            database_url,
            capability_requests_per_ip_per_min=2,
            trust_proxy=True,
        )
    )
    try:
        with TestClient(application) as client:
            for _ in range(2):
                assert (
                    client.get(
                        "/api/v1/generation-capabilities",
                        headers={"X-Forwarded-For": "198.51.100.1"},
                    ).status_code
                    == 200
                )
            # A different client identity has its own fresh window.
            assert (
                client.get(
                    "/api/v1/generation-capabilities",
                    headers={"X-Forwarded-For": "198.51.100.2"},
                ).status_code
                == 200
            )
            # The first client's window is exhausted.
            assert (
                client.get(
                    "/api/v1/generation-capabilities",
                    headers={"X-Forwarded-For": "198.51.100.1"},
                ).status_code
                == 429
            )
    finally:
        _dispose(application)