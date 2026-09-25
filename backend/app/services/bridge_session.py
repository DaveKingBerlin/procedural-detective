"""Phase 22 — server-side bridge WebSocket session state machine (service layer).

The ``app.api.v1.bridge_ws`` endpoint stays a THIN transport wrapper (accept /
receive / send / close only); every protocol decision lives HERE in the
service layer so the API never imports ``app.generation``/``app.domain``
material (test_boundaries.py contract).

Responsibilities:

- decode + strict-validate an inbound JSON frame (size/depth/unknown-type/
  invalid-version) and translate rejections into a carryable WS close code +
  short sanitized reason (``BridgeFrameRejected``);
- authenticate the FIRST frame (``pairing_hello`` -> consume the single-use
  code + issue the bridge session; ``bridge_hello`` -> constant-time token
  verification for reconnect) and BIND the socket to the creator session
  scope (``authenticate_handshake``);
- route ``job_result`` frames to the connection's in-flight job and project
  the bridge-reported failureCode onto the canonical typed vocabulary;
- per-connection bounded frame-rate gate.
"""

from __future__ import annotations

from typing import Any

from app.core.observability import emit_event
from app.generation.bridge_protocol import (
    CLOSE_IDLE_TIMEOUT,
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_POLICY_VIOLATION,
    CLOSE_UNSUPPORTED_TYPE,
    MSG_BRIDGE_HELLO,
    MSG_JOB_RESULT,
    MSG_PAIRING_HELLO,
    MSG_PONG,
    BridgeProtocolError,
    decode_frame,
    pairing_accepted_frame,
    ping_frame,
    validate_frame,
)
from app.generation.failure_codes import GenerationFailureCode
from app.services.bridge import PairingInvalid

# Close codes + control frames re-exported so the API transport wrapper never
# imports app.generation material itself (test_boundaries.py contract).
__all__ = [
    "CLOSE_IDLE_TIMEOUT",
    "CLOSE_MESSAGE_TOO_BIG",
    "CLOSE_POLICY_VIOLATION",
    "CLOSE_UNSUPPORTED_TYPE",
    "MSG_JOB_RESULT",
    "MSG_PONG",
    "BridgeFrameRateGate",
    "BridgeFrameRejected",
    "authenticate_handshake",
    "decode_inbound_frame",
    "is_handshake_frame",
    "ping_frame",
    "project_bridge_failure",
    "route_job_result",
]


class BridgeFrameRejected(Exception):
    """A frame the server refuses; carries a WS close code + short reason."""

    def __init__(self, close_code: int, reason: str) -> None:
        super().__init__(reason)
        self.close_code = int(close_code)
        self.reason = str(reason)


def decode_inbound_frame(raw_text: str, *, max_bytes: int) -> dict[str, Any]:
    """Decode + strict-validate one inbound frame (size/depth/version/type)."""
    try:
        return validate_frame(decode_frame(raw_text, max_bytes=max_bytes))
    except BridgeProtocolError as exc:
        raise BridgeFrameRejected(exc.close_code, exc.reason) from None


def is_handshake_frame(frame: dict[str, Any]) -> bool:
    return frame.get("type") in (MSG_PAIRING_HELLO, MSG_BRIDGE_HELLO)


def route_job_result(
    conn: Any, frame: dict[str, Any], registry: Any
) -> None:
    """Route a validated job_result to the connection's in-flight job.

    A late/duplicate/cross-session result (jobId not current on THIS
    connection) is DISCARDED with zero state mutation (Phase22 §17/§33)."""
    job_id = frame["jobId"]
    if frame["status"] == "SUCCESS":
        applied = registry.resolve_job(conn, job_id, "content", frame["structuredOutput"])
    else:
        code = project_bridge_failure(frame.get("failureCode"))
        applied = registry.resolve_job(conn, job_id, "error", code)
    if not applied:
        emit_event(
            "bridge.job.discarded",
            bridgeSessionId=conn.bridge_session_id,
            jobId=job_id,
            reasonCode="LATE_OR_DUPLICATE_RESULT",
        )


def project_bridge_failure(code: Any) -> str:
    """Project a bridge-reported failureCode onto the canonical closed
    vocabulary (never echo arbitrary text)."""
    from app.generation.bridge_protocol import BRIDGE_FAILURE_CODES

    value = code if isinstance(code, str) else ""
    mapping = {
        "LOCAL_OLLAMA_UNAVAILABLE": GenerationFailureCode.LOCAL_OLLAMA_UNAVAILABLE.value,
        "LOCAL_MODEL_UNAVAILABLE": GenerationFailureCode.LOCAL_MODEL_UNAVAILABLE.value,
        "LOCAL_PROVIDER_TIMEOUT": GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT.value,
        "LOCAL_PROVIDER_INVALID_OUTPUT": GenerationFailureCode.LOCAL_PROVIDER_INVALID_OUTPUT.value,
        "BRIDGE_BUSY": GenerationFailureCode.BRIDGE_BUSY.value,
        "BRIDGE_PROTOCOL_ERROR": GenerationFailureCode.BRIDGE_PROTOCOL_ERROR.value,
    }
    if value in BRIDGE_FAILURE_CODES and value in mapping:
        return mapping[value]
    return GenerationFailureCode.BRIDGE_PROTOCOL_ERROR.value


def authenticate_handshake(
    frame: dict[str, Any],
    *,
    registry: Any,
    pairing_service: Any,
    settings: Any,
    websocket: Any,
    loop: Any,
    now: float,
) -> tuple[Any, str]:
    """Authenticate the handshake and BIND the socket.

    Returns ``(registry_connection, ack_frame_text)``. Raises
    ``BridgeFrameRejected`` (close 1008) for every authentication failure —
    NEVER binds, NEVER leaks the token/code."""
    if frame["type"] == MSG_PAIRING_HELLO:
        try:
            data, _scope = pairing_service.consume_pairing(
                frame["pairingCode"], now=now
            )
        except PairingInvalid:
            raise BridgeFrameRejected(1008, "pairing invalid or expired") from None
        model = frame.get("model")
        capabilities = frame.get("capabilities")
        allowlist = getattr(settings, "bridge_model_allowlist", None) or None
        if model is not None and allowlist and model not in set(allowlist):
            raise BridgeFrameRejected(1008, "model not allowed")
        try:
            binding = pairing_service.bind_bridge_from_pairing(
                data,
                model=model,
                capabilities=capabilities,
                now=now,
            )
        except PairingInvalid:
            raise BridgeFrameRejected(1008, "pairing invalid or expired") from None
        conn = registry.bind(
            bridge_session_id=binding.bridge_session_id,
            session_scope=binding.session_scope,
            model=binding.model,
            capabilities=binding.capabilities,
            expires_at=now
            + float(
                getattr(settings, "bridge_session_ttl_seconds", 14400.0) or 14400.0
            ),
            socket=websocket,
            loop=loop,
            now=now,
        )
        ack = pairing_accepted_frame(
            bridge_session_id=binding.bridge_session_id,
            bridge_session_token=binding.bridge_session_token,
            model=binding.model,
            capabilities=binding.capabilities,
        )
        emit_event(
            "bridge.connected",
            bridgeSessionId=binding.bridge_session_id,
            reasonCode="PAIRING_BOUND",
            model=binding.model,
        )
        return conn, ack
    # bridge_hello (reconnect)
    row = pairing_service.authenticate_bridge_token(
        frame["bridgeSessionToken"], now=now
    )
    if row is None:
        raise BridgeFrameRejected(1008, "bridge session invalid")
    conn = registry.bind(
        bridge_session_id=row.bridge_session_id,
        session_scope=row.session_scope,
        model=row.model,
        capabilities=row.capabilities,
        expires_at=row.expires_at,
        socket=websocket,
        loop=loop,
        now=now,
    )
    ack = pairing_accepted_frame(
        bridge_session_id=row.bridge_session_id,
        bridge_session_token=None,
        model=row.model,
        capabilities=row.capabilities,
    )
    emit_event(
        "bridge.connected",
        bridgeSessionId=row.bridge_session_id,
        reasonCode="RECONNECTED",
        model=row.model,
    )
    return conn, ack


class BridgeFrameRateGate:
    """Per-connection bounded sliding-window frame gate (Phase22 §27)."""

    def __init__(self, *, limit: int, window_seconds: float, clock: Any) -> None:
        self._limit = int(limit)
        self._window = float(window_seconds)
        self._clock = clock
        self._hits: list[float] = []

    def allow(self) -> bool:
        now = float(self._clock.now())
        cutoff = now - self._window
        self._hits = [hit for hit in self._hits if hit >= cutoff]
        if len(self._hits) >= self._limit:
            return False
        self._hits.append(now)
        return True