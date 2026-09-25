"""Phase 22 — the BYO-Ollama RemoteClientProvider (Provider protocol).

``RemoteClientProvider`` implements the EXACT existing ``Provider`` protocol
(``Provider.generate(GenerateRequest) -> ProviderResult``) but it NEVER talks
to Ollama: it dispatches ONE ``STRUCTURED_INFERENCE`` job over the WebSocket
connection of the bridge bound to the generation attempt's creator session and
blocks (synchronously, bounded) until the bridge replies.

Design rules (Phase22 §2/§10/§16/§17 — the server stays authoritative):

- Same orchestration seat as ``OllamaProvider``: the generation runs through
  the SAME ``GenerationController`` / stage driver; this provider is just
  another transport-like backend for it. Deterministic validation, solver and
  publication gates are UNCHANGED — the bridge response is FULLY UNTRUSTED and
  the raw ``structuredOutput`` is returned as the provider ``content`` so the
  EXISTING strict per-stage parsers/validators receive exactly what another
  provider's text would receive.
- The provider is bound to the attempt's CREATOR SESSION SCOPE (bound by the
  stage driver at ``run_into`` time); the bridge registry selects the bridge
  by that scope. A generation session different from the pairing session finds
  no bridge -> typed ``BRIDGE_NOT_CONNECTED`` (cross-user bridge use is
  impossible by construction). Authorization of the WS handshake itself
  happens at the endpoint; no token is ever sent from the browser.
- Budgets: the STAGE DRIVER consumes the per-attempt ``BudgetTracker``
  (``consume_call``) for every dispatch exactly like every other provider call
  — a bridge job == one regular provider call, no extra accounting here.
- Deadline clamp: ``job.timeoutMs`` = min(effective remaining generation
  deadline carried by ``request.timeout_seconds``, ``BRIDGE_JOB_DEADLINE_SECONDS``)
  and NEVER exceeds the ADV-250 hard ceiling (the maximum generation deadline
  the app allows / 1800s) — an oversized configured value can never reach the
  wire. On timeout the provider best-effort sends ``job_cancel`` and fails
  typed ``LOCAL_PROVIDER_TIMEOUT``. Any later result finds no current job and
  is DISCARDED (Phase22 §17/§18).
- Typed failure mapping (never raw text): bridge absent -> BRIDGE_NOT_CONNECTED;
  connection died mid-job -> BRIDGE_DISCONNECTED; busy slot ->
  BRIDGE_BUSY; bridge-reported failureCode projected onto the closed
  vocabulary (LOCAL_OLLAMA_UNAVAILABLE / LOCAL_MODEL_UNAVAILABLE /
  LOCAL_PROVIDER_INVALID_OUTPUT / BRIDGE_BUSY / ...); allowlist violation ->
  LOCAL_MODEL_UNAVAILABLE; protocol violation -> BRIDGE_PROTOCOL_ERROR.
  A failure is raised as ``StageDriverProviderFailure`` (the driver/controller
  classify the attempt with the typed code; nothing is published).
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.observability import emit_event
from app.core.timeout_envelope import BRIDGE_JOB_DEADLINE_MAX_SECONDS
from app.generation.bridge_protocol import (
    encode_frame,
    generate_job_id,
    schema_id_for_stage,
)
from app.generation.failure_codes import GenerationFailureCode
from app.generation.provider import GenerateRequest, ProviderResult

# The model name a healthy bridge session may report: safe operator token set.
_MODEL_LABEL_LIMIT = 80

# send timeout for the dispatch frame itself (the socket should accept it far
# faster than the job deadline; a stuck socket surfaces as BRIDGE_DISCONNECTED).
_DISPATCH_SEND_TIMEOUT_SECONDS = 5.0


class RemoteClientProvider:
    """Synchronous bridge dispatch adapter implementing ``Provider.generate``.

    ``registry`` is the shared ``BridgeRegistry``; ``settings`` the app
    settings (job-deadline cap + model allowlist); ``session_scope`` is bound
    by the stage driver before any call (see ``bind_session_scope``).
    """

    def __init__(
        self,
        *,
        registry: Any,
        settings: Any,
        session_scope: str | None = None,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._session_scope = session_scope
        # Truthful internal trace of the last dispatch (never serialized).
        self.last_job_id: str | None = None
        self.last_latency_ms: int | None = None

    def bind_session_scope(self, session_scope: str | None) -> None:
        """Bound by the stage driver to the attempt's creator session scope."""
        self._session_scope = str(session_scope) if session_scope else None

    # -- Provider protocol ---------------------------------------------------

    def generate(self, request: GenerateRequest) -> ProviderResult:
        _t0 = time.perf_counter()
        if not self._session_scope:
            raise self._typed(
                "bridge not connected for this generation session",
                GenerationFailureCode.BRIDGE_NOT_CONNECTED,
            )
        schema_id = schema_id_for_stage(request.stage.value)
        if schema_id is None:
            # The server cannot map the stage to an authoritative schema —
            # fail closed (never a guessed schema).
            raise self._typed(
                "bridge dispatch has no authoritative schema for this stage",
                GenerationFailureCode.BRIDGE_PROTOCOL_ERROR,
            )
        conn = self._registry.lookup_for_scope(self._session_scope)
        if conn is None:
            raise self._typed(
                "bridge not connected for this generation session",
                GenerationFailureCode.BRIDGE_NOT_CONNECTED,
            )
        if not self._model_allowed(conn.model):
            raise self._typed(
                "selected local model is not available",
                GenerationFailureCode.LOCAL_MODEL_UNAVAILABLE,
            )
        timeout_s = self._effective_timeout(request)
        if timeout_s <= 0:
            raise self._typed(
                "generation deadline exceeded",
                GenerationFailureCode.GENERATION_DEADLINE_EXCEEDED,
            )
        job_id = generate_job_id()
        self.last_job_id = job_id
        payload = {
            "protocolVersion": 1,
            "type": "job",
            "jobId": job_id,
            "jobType": "STRUCTURED_INFERENCE",
            "schemaId": schema_id,
            "model": conn.model,
            "prompt": request.prompt_context,
            "temperature": 0.1,
            "timeoutMs": int(timeout_s * 1000),
        }
        waiter = self._registry.begin_job(conn, job_id)
        if waiter is None:
            raise self._typed(
                "bridge is busy with another job",
                GenerationFailureCode.BRIDGE_BUSY,
            )
        emit_event(
            "bridge.job.started",
            bridgeSessionId=conn.bridge_session_id,
            generationAttemptId=request.attempt_id,
            stage=request.stage.value,
            jobId=job_id,
            schemaId=schema_id,
            model=conn.model,
            timeoutMs=int(timeout_s * 1000),
            providerCallCount=None,
        )
        dispatch_ok = self._dispatch(conn, job_id, payload)
        if not dispatch_ok:
            self._registry.cancel_job(
                conn, job_id, GenerationFailureCode.BRIDGE_DISCONNECTED.value
            )
            raise self._typed(
                "bridge disconnected during dispatch",
                GenerationFailureCode.BRIDGE_DISCONNECTED,
            )
        resolved = waiter.wait(float(timeout_s))
        latency_ms = int((time.perf_counter() - _t0) * 1000)
        self.last_latency_ms = latency_ms
        if not resolved:
            self._registry.cancel_job(
                conn, job_id, GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT.value
            )
            emit_event(
                "bridge.job.failed",
                bridgeSessionId=conn.bridge_session_id,
                generationAttemptId=request.attempt_id,
                stage=request.stage.value,
                jobId=job_id,
                failureCode=GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT.value,
                latencyMs=latency_ms,
                reasonCode="JOB_TIMEOUT",
            )
            raise self._typed(
                "local provider timed out",
                GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT,
            )
        kind, value = waiter.result or ("error", GenerationFailureCode.BRIDGE_PROTOCOL_ERROR.value)
        if kind == "content":
            emit_event(
                "bridge.job.completed",
                bridgeSessionId=conn.bridge_session_id,
                generationAttemptId=request.attempt_id,
                stage=request.stage.value,
                jobId=job_id,
                success=True,
                latencyMs=latency_ms,
                providerCallCount=None,
            )
            return ProviderResult(content=json.dumps(value))
        code = str(value or GenerationFailureCode.BRIDGE_PROTOCOL_ERROR.value)
        emit_event(
            "bridge.job.failed",
            bridgeSessionId=conn.bridge_session_id,
            generationAttemptId=request.attempt_id,
            stage=request.stage.value,
            jobId=job_id,
            failureCode=code,
            latencyMs=latency_ms,
            reasonCode="JOB_FAILED",
        )
        raise self._typed("local provider failure", code)

    # -- private --------------------------------------------------------------

    def _model_allowed(self, model: str | None) -> bool:
        allowlist = getattr(self._settings, "bridge_model_allowlist", None) or None
        if not allowlist:
            return True
        return isinstance(model, str) and model in set(allowlist)

    def _effective_timeout(self, request: GenerateRequest) -> float:
        configured = float(
            getattr(self._settings, "bridge_job_deadline_seconds", 120.0) or 120.0
        )
        # ADV-250 defense-in-depth: ``BRIDGE_JOB_DEADLINE_SECONDS`` is
        # HARD-CAPPED at config validation (Settings le=...), but the wire
        # clamp must hold even for a settings object constructed WITHOUT
        # pydantic validation (or any future non-validating caller): the
        # dispatched ``job.timeoutMs`` may NEVER exceed
        # min(remaining generation deadline, this cap).
        configured = min(configured, float(BRIDGE_JOB_DEADLINE_MAX_SECONDS))
        requested = (
            float(request.timeout_seconds)
            if request.timeout_seconds is not None
            else configured
        )
        return min(max(0.0, requested), configured)

    def _dispatch(self, conn: Any, job_id: str, payload: dict[str, Any]) -> bool:
        """Best-effort async send of the job frame onto the bridge's loop."""
        if conn.socket is None or conn.loop is None:
            return False
        try:
            frame = encode_frame(payload)
            future = asyncio_run_coroutine_threadsafe(
                conn.socket.send_text(frame), conn.loop
            )
            future.result(timeout=_DISPATCH_SEND_TIMEOUT_SECONDS)
            return True
        except Exception:  # noqa: BLE001 - transport failure -> typed disconnect
            return False

    @staticmethod
    def _typed(message: str, code: Any) -> Exception:
        from app.generation.provider import StageDriverProviderFailure

        return StageDriverProviderFailure(message, code=code)


def asyncio_run_coroutine_threadsafe(coro: Any, loop: Any) -> Any:
    """Thin indirection so the provider and tests share one cross-thread seam."""
    import asyncio

    return asyncio.run_coroutine_threadsafe(coro, loop)


__all__ = [
    "RemoteClientProvider",
]