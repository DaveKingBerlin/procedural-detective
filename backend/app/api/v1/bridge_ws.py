"""Phase 22 — BYO-Ollama WebSocket bridge endpoint (``/api/v1/bridge/ws``).

THIN TRANSPORT wrapper: this module only accepts the socket, reads bounded
frames, delegates every protocol decision to the service layer
(``app.services.bridge_session``) and writes back frames/closes. The API layer
imports NO ``app.generation`` / ``app.domain`` material (test_boundaries.py).

Transport contract (Phase22 §5/§27):

1. The FIRST frame must be ``pairing_hello`` (fresh pairing; single-use code)
   or ``bridge_hello`` (reconnect; bridge session token). Any other first
   frame closes WITHOUT binding. The handshake is NOT header-based: the
   protocol authenticates on the first frame.
2. After a successful handshake the server replies ``pairing_accepted`` (the
   raw bridge token appears EXACTLY once; a reconnect confirmation echoes the
   frame WITHOUT the token — the bridge already holds it) and the socket is
   BOUND to the pairing's creator session scope.
3. Frames are JSON text, depth-bounded, size-bounded, rate-bounded and
   strict-schema-validated. Unknown/unauthorized/malformed frames close with a
   short sanitized reason.
4. Heartbeat: the server sends ``ping`` every ``BRIDGE_HEARTBEAT_INTERVAL_SECONDS``;
   a socket silent for ``BRIDGE_IDLE_TIMEOUT_SECONDS`` is closed (1001).
5. ``job_result`` frames are routed to the in-flight job of THIS connection;
   late/duplicate/cross-session results are DISCARDED.
6. On disconnect (any exit) the in-flight job fails typed
   ``BRIDGE_DISCONNECTED`` so a waiting generation worker unblocks.

Logging: opaque ids / safe model labels / numbers only — never the pairing
code, bridge token, prompt, model output, CaseTruth, Ollama URL or IP.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.observability import emit_event
from app.services.bridge_session import (
    CLOSE_IDLE_TIMEOUT,
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_POLICY_VIOLATION,
    CLOSE_UNSUPPORTED_TYPE,
    MSG_JOB_RESULT,
    MSG_PONG,
    BridgeFrameRateGate,
    BridgeFrameRejected,
    authenticate_handshake,
    decode_inbound_frame,
    is_handshake_frame,
    ping_frame,
    route_job_result,
)

logger = logging.getLogger("procedural-detective")

router = APIRouter(tags=["bridge-ws"])

_WS_PATH = "/api/v1/bridge/ws"


async def _close_later(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001 - close is best-effort
        return


def _handshake_gate_blocked(request: Any) -> bool:
    gate = getattr(request.app.state, "bridge_handshake_gate", None)
    if gate is None:
        return False
    return not gate.allow("global", request.app.state.clock.now())


async def _read_first_frame(websocket: WebSocket, settings: Any) -> str | None:
    """Read the bounded first frame; None when the peer never authenticated."""
    timeout = float(
        getattr(settings, "bridge_idle_timeout_seconds", 300.0) or 300.0
    )
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout)
    except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
        return None
    except Exception:  # noqa: BLE001 - any handshake failure closes
        return None
    return raw


@router.websocket(_WS_PATH)
async def bridge_ws_endpoint(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    registry = getattr(websocket.app.state, "bridge_registry", None)
    pairing_service = getattr(websocket.app.state, "bridge_pairing_service", None)
    if registry is None or pairing_service is None:
        await _close_later(websocket, CLOSE_POLICY_VIOLATION, "bridge disabled")
        return
    if _handshake_gate_blocked(websocket):
        await _close_later(
            websocket, CLOSE_POLICY_VIOLATION, "bridge handshake rate limit"
        )
        return
    await websocket.accept()
    raw = await _read_first_frame(websocket, settings)
    if raw is None:
        await _close_later(websocket, CLOSE_POLICY_VIOLATION, "unauthorized")
        return
    try:
        frame = decode_inbound_frame(
            raw, max_bytes=settings.bridge_max_message_bytes
        )
    except BridgeFrameRejected as exc:
        await _close_later(websocket, exc.close_code, exc.reason)
        return
    if not is_handshake_frame(frame):
        await _close_later(websocket, CLOSE_UNSUPPORTED_TYPE, "unknown message type")
        return
    try:
        conn, ack = authenticate_handshake(
            frame,
            registry=registry,
            pairing_service=pairing_service,
            settings=settings,
            websocket=websocket,
            loop=asyncio.get_running_loop(),
            now=float(websocket.app.state.clock.now()),
        )
    except BridgeFrameRejected as exc:
        await _close_later(websocket, exc.close_code, exc.reason)
        return
    try:
        await websocket.send_text(ack)
    except Exception:  # noqa: BLE001 - peer vanished during handshake
        registry.detach(
            conn.bridge_session_id, now=float(websocket.app.state.clock.now())
        )
        return

    rate = BridgeFrameRateGate(
        limit=settings.bridge_max_frames_per_window,
        window_seconds=settings.bridge_frame_window_seconds,
        clock=websocket.app.state.clock,
    )
    heartbeat = float(
        getattr(settings, "bridge_heartbeat_interval_seconds", 30.0) or 30.0
    )
    idle = float(getattr(settings, "bridge_idle_timeout_seconds", 300.0) or 300.0)
    max_bytes = int(getattr(settings, "bridge_max_message_bytes", 256 * 1024))
    last_activity = time.monotonic()
    try:
        while True:
            wait_for = max(0.0, (last_activity + heartbeat) - time.monotonic())
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=wait_for
                )
            except asyncio.TimeoutError:
                if time.monotonic() - last_activity >= idle:
                    await _close_later(websocket, CLOSE_IDLE_TIMEOUT, "idle timeout")
                    break
                try:
                    await websocket.send_text(ping_frame())
                except Exception:  # noqa: BLE001 - dead peer
                    break
                continue
            except (WebSocketDisconnect, RuntimeError):
                break
            except Exception:  # noqa: BLE001 - abnormal close
                break
            if len(raw.encode("utf-8")) > max_bytes:
                await _close_later(websocket, CLOSE_MESSAGE_TOO_BIG, "message too large")
                break
            try:
                frame = decode_inbound_frame(raw, max_bytes=max_bytes)
            except BridgeFrameRejected as exc:
                await _close_later(websocket, exc.close_code, exc.reason)
                break
            if not rate.allow():
                await _close_later(
                    websocket, CLOSE_POLICY_VIOLATION, "bridge frame rate limit"
                )
                break
            last_activity = time.monotonic()
            registry.mark_seen(conn, float(websocket.app.state.clock.now()))
            if frame["type"] == MSG_PONG:
                continue
            if frame["type"] == MSG_JOB_RESULT:
                route_job_result(conn, frame, registry)
                continue
            # Unauthorized/unknown message type -> close with reason.
            await _close_later(websocket, CLOSE_UNSUPPORTED_TYPE, "unknown message type")
            break
    finally:
        now = float(websocket.app.state.clock.now())
        registry.detach(conn.bridge_session_id, now=now)
        emit_event(
            "bridge.disconnected",
            bridgeSessionId=conn.bridge_session_id,
            reasonCode="SOCKET_CLOSED",
        )


__all__ = ["router", "bridge_ws_endpoint"]