"""Phase 20 — PD-SEC-06 production trace policy + provider-body sentinel
regression (logging file).

- ENVIRONMENT=production REJECTS startup when PD_DEV_TRACE=true or
  PD_GENERATION_DEBUG_LOGS=true (clear sanitized error; never a partial boot).
- With trace OFF, a provider error body containing SECURITY_SENTINEL_SECRET
  never reaches any log output — only the sanitized failure code/status.
- The provider adapters themselves never echo provider error bodies or URLs
  into ``ProviderResult.error`` (the only channel a dev trace could print).

The sentinel below is a synthetic secret marker; it must NEVER appear inside
any captured production log line.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.core.observability import (  # noqa: E402
    SERVICE_LOGGER_NAME,
    enforce_production_trace_policy,
)
from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import (  # noqa: E402
    GenerateRequest,
    GenerationStage,
    ProviderResult,
)
from app.generation.state_machine import GenerationState  # noqa: E402

SECURITY_SENTINEL_SECRET = "SECURITY_SENTINEL_SECRET"

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)


# --------------------------------------------------------------------------- #
# startup policy: production must reject development trace logging
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "env_overrides,settings_overrides",
    [
        ({"PD_DEV_TRACE": "true"}, {}),
        ({}, {"pd_generation_debug_logs": True}),
    ],
    ids=["PD_DEV_TRACE=true", "PD_GENERATION_DEBUG_LOGS=true"],
)
def test_production_rejects_dev_trace_startup(
    monkeypatch, env_overrides, settings_overrides
):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("PD_DEV_TRACE", raising=False)
    for key, value in env_overrides.items():
        monkeypatch.setenv(key, value)
    settings = Settings(
        database_url="sqlite:///./unused-test.db", **settings_overrides
    )
    with pytest.raises(RuntimeError, match="refusing to start in production"):
        enforce_production_trace_policy(settings)


def test_production_with_trace_off_starts_cleanly(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("PD_DEV_TRACE", raising=False)
    monkeypatch.delenv("PD_GENERATION_DEBUG_LOGS", raising=False)
    settings = Settings(
        database_url="sqlite:///./unused-test.db",
        pd_generation_debug_logs=False,
    )
    enforce_production_trace_policy(settings)  # must NOT raise
    assert settings.environment == "production"


def test_gate_never_fires_outside_production(monkeypatch):
    """Development/test environments keep dev-trace flexibility (no startup
    rejection) — the policy is scoped to ENVIRONMENT=production only."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("PD_DEV_TRACE", "true")
    settings = Settings(database_url="sqlite:///./unused-test.db")
    enforce_production_trace_policy(settings)  # must NOT raise


def test_production_startup_rejection_reaches_the_real_entrypoint(tmp_path):
    """The module-level ``app.main:app`` (the documented uvicorn entrypoint)
    refuses to boot in production with PD_DEV_TRACE=true — the policy is wired
    into ``create_app``, not only into the testable helper.

    The process exits non-zero; the message is the SANITIZED RuntimeError text
    (it names the offending setting names an operator must fix and never
    echoes operator VALUES, secrets or provider endpoints). A Python
    traceback wrapping a startup-config error is normal operational behavior.
    """
    backend_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["ENVIRONMENT"] = "production"
    env["PD_DEV_TRACE"] = "true"
    env.pop("ENV_FILE", None)
    env["ENV_FILE"] = os.devnull  # hermetic: never read the operator .env
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'entrypoint.db').as_posix()}"
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "refusing to start in production" in result.stderr
    # The sanitized message names ONLY the setting names (PD_DEV_TRACE /
    # PD_GENERATION_DEBUG_LOGS), never operator values/paths.
    assert "PD_DEV_TRACE" in result.stderr
    assert "procedural_detective" not in result.stderr  # no DB/filename echo
    assert "SECURITY_SENTINEL_SECRET" not in result.stderr


# --------------------------------------------------------------------------- #
# sentinel logging regression (trace OFF)
# --------------------------------------------------------------------------- #


class _SentryProvider:
    """A provider whose error text embeds a synthetic secret marker."""

    def __init__(self) -> None:
        self.content = None

    def generate(self, request: GenerateRequest) -> ProviderResult:
        return ProviderResult(
            error=f"provider failure: HTTP 502 {SECURITY_SENTINEL_SECRET}"
        )


class _CaptureLog:
    """Deterministic logging capture: a manual root-handler + a pinned app
    logger level. Unlike pytest's caplog, it survives alembic's in-process
    ``fileConfig`` (which resets the ROOT logger's handlers) — the capture
    handler is attached AFTER any migration has run."""

    def __init__(self, log_text: "io.StringIO") -> None:
        from app.core.observability import JsonEventFormatter

        self.handler = logging.StreamHandler(log_text)
        self.handler.setLevel(logging.DEBUG)
        # Replicate the production formatter so ``pd_fields`` (failureCode)
        # appear in the captured text exactly as they would in a real log.
        self.handler.setFormatter(JsonEventFormatter())
        self.service_logger = logging.getLogger(SERVICE_LOGGER_NAME)
        self._previous_logger_level = self.service_logger.level
        logging.getLogger().addHandler(self.handler)

    def __enter__(self) -> "_CaptureLog":
        self.service_logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *_exc) -> None:
        logging.getLogger().removeHandler(self.handler)
        self.service_logger.setLevel(self._previous_logger_level)


def test_sentinel_never_reaches_logs_with_trace_off(tmp_path):
    """With trace OFF (the production-normal posture), a provider error body
    containing the sentinel produces logs that carry ONLY the sanitized
    failure code/status — the sentinel string never appears anywhere."""
    import io

    from app.services.generation import GenerationService
    from app.persistence.store import Store
    from conftest import upgrade_db

    database_url = f"sqlite:///{(tmp_path / 'sentinel.db').as_posix()}"
    upgrade_db(database_url)
    store = Store(database_url)
    buffer = io.StringIO()
    try:
        service = GenerationService(
            settings=Settings(
                database_url=database_url,
                max_concurrent_generations=2,
                max_generations_per_session_per_window=5,
            ),
            store=store,
            provider_factory=lambda: _SentryProvider(),
        )
        with _CaptureLog(buffer):
            session = service.create_anonymous_quota_session()
            started = service.start_case_generation(
                GOLDEN_PROMPT,
                anonymous_quota_session_id=session.anonymous_quota_session_id,
            )
        assert started.status == "FAILED"
        # The failure code/status is the ONLY provider-derived signal logged.
        assert started.failure_code == "PROVIDER_UNAVAILABLE"
        text = buffer.getvalue()
        assert "PROVIDER_UNAVAILABLE" in text
        assert SECURITY_SENTINEL_SECRET not in text
    finally:
        store.dispose()


def test_sentinel_failure_through_api_error_envelope_is_also_clean(database_url):
    """A failing generation surfaces the sanitized 201/FAILED + failureCode to
    the API; the sentinel never appears in the response or session rows."""
    from fastapi.testclient import TestClient

    from app.persistence.store import Store
    from app.services.generation import GenerationService
    from app.main import create_app
    from conftest import upgrade_db

    upgrade_db(database_url)
    store = Store(database_url)
    app = create_app(
        Settings(
            database_url=database_url,
            max_concurrent_generations=2,
            max_generations_per_session_per_window=5,
        )
    )
    app.state.generation_service = GenerationService(
        settings=Settings(
            database_url=database_url,
            max_concurrent_generations=2,
            max_generations_per_session_per_window=5,
        ),
        store=store,
        provider_factory=lambda: _SentryProvider(),
    )
    try:
        with TestClient(app) as client:
            res = client.post("/api/v1/sessions/anonymous")
            assert res.status_code == 201
            token = res.json()["anonymousSessionToken"]
            res = client.post(
                "/api/v1/cases",
                json={"prompt": GOLDEN_PROMPT},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert res.status_code == 201
            text = res.text
            assert SECURITY_SENTINEL_SECRET not in text
            assert "PROVIDER_UNAVAILABLE" in text
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()
        store.dispose()


# --------------------------------------------------------------------------- #
# provider adapters never echo bodies/URLs into the error channel
# --------------------------------------------------------------------------- #


class _FakeErrorResponse:
    status_code = 502

    @property
    def text(self) -> str:
        return f'{{"error": {{"message": "{SECURITY_SENTINEL_SECRET}"}}}}'


def test_live_provider_never_echoes_non_2xx_body(monkeypatch):
    from app.generation.live_provider import LiveHttpProvider

    import app.generation.live_provider as live_provider_mod

    monkeypatch.setattr(
        live_provider_mod.httpx,
        "post",
        lambda *args, **kwargs: _FakeErrorResponse(),
    )
    provider = LiveHttpProvider(
        endpoint_url="https://example.invalid/v1/chat/completions",
        api_key="opaque-key",
        model="probe",
    )
    result = provider.generate(
        GenerateRequest(attempt_id="att-1", stage=GenerationStage.CASE_TRUTH, prompt_context="p")
    )
    assert result.content is None
    assert result.error == "provider failure: HTTP 502"
    assert SECURITY_SENTINEL_SECRET not in (result.error or "")


class _FakeHttpxConnectError(httpx.RequestError):
    """Mimic httpx.ConnectError whose str() embeds the request URL."""

    def __init__(self, url: str) -> None:
        super().__init__(
            "no route to host", request=httpx.Request("POST", url)
        )
        self.url = url


def test_live_provider_never_echoes_request_url(monkeypatch):
    from app.generation.live_provider import LiveHttpProvider

    import app.generation.live_provider as live_provider_mod

    def _boom(*args, **kwargs):
        raise _FakeHttpxConnectError("https://10.0.0.99:11434/v1")

    monkeypatch.setattr(live_provider_mod.httpx, "post", _boom)
    provider = LiveHttpProvider(
        endpoint_url="https://10.0.0.99:11434/v1",
        api_key="opaque-key",
        model="probe",
    )
    result = provider.generate(
        GenerateRequest(attempt_id="att-2", stage=GenerationStage.CASE_TRUTH, prompt_context="p")
    )
    assert result.error == "provider request failed: _FakeHttpxConnectError"
    assert "10.0.0.99" not in (result.error or "")
    assert "url=" not in (result.error or "")
    assert "no route to host" not in (result.error or "")


class _FakeOversizeResponse:
    status_code = 200

    def iter_bytes(self, chunk_size: int):
        # 5 chunks of 64 KiB > the 256 KiB cap (the cap must trip).
        for index in range(5):
            yield (bytes([65 + index]) * chunk_size)


def test_live_provider_caps_oversize_2xx_body(monkeypatch):
    from app.generation.live_provider import LiveHttpProvider

    import app.generation.live_provider as live_provider_mod

    monkeypatch.setattr(
        live_provider_mod.httpx, "post", lambda *a, **k: _FakeOversizeResponse()
    )
    provider = LiveHttpProvider(
        endpoint_url="https://example.invalid/v1", api_key="k", model="m"
    )
    result = provider.generate(
        GenerateRequest(attempt_id="att-3", stage=GenerationStage.CASE_TRUTH, prompt_context="p")
    )
    assert result.error == "provider response exceeded the size cap"
    assert result.content is None


def test_controller_never_logs_error_body_even_through_emit_events(tmp_path):
    """PD-SEC-06 §16.1 — the controller's structured failure path only emits
    the sanitized failureCode; the raw provider error body is never an
    ``emit_event`` field (allowlisted fields only)."""
    import io

    from app.generation.controller import GenerationController

    clock = ManualClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=2,
        max_concurrent_generations_global=2,
        max_generations_per_session_per_window=5,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=_SentryProvider(),
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=3,
    )
    buffer = io.StringIO()
    with _CaptureLog(buffer):
        handle = controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
        )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "PROVIDER_UNAVAILABLE"
    text = buffer.getvalue()
    assert "PROVIDER_UNAVAILABLE" in text
    assert SECURITY_SENTINEL_SECRET not in text
    # Sanitized reason, never the provider body.
    assert SECURITY_SENTINEL_SECRET not in (record.reason or "")
    assert "provider failure: generator unavailable" == record.reason
