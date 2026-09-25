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
from app.core.ratelimit import resolve_client_ip
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


class BridgeMessageSizeGuard:
    """ADV-251 — enforce the WS frame byte bound at the ASGI transport edge.

    uvicorn's transport default ``ws_max_size`` is 16 MiB (verified against
    the installed uvicorn 0.52.4: ``uvicorn.Config.ws_max_size =
    16 * 1024 * 1024``) — 64x the documented ``BRIDGE_MAX_MESSAGE_BYTES``
    (256 KiB default), so without this guard a frame of up to ~16 MiB is FULLY
    BUFFERED before the app's decode-time check (``websocket.receive_text()``)
    ever runs. This middleware intercepts the inbound message stream of the
    bridge WebSocket scope and answers ANY frame whose UTF-8 byte size exceeds
    ``max_bytes`` with a 1009 close WITHOUT reaching the endpoint decode — the
    FIRST (pre-auth) frame included.

    Every repo-controlled uvicorn launch additionally passes
    ``ws_max_size``/``--ws-max-size`` (the REAL transport bound); this guard is
    the app-owned enforcement that holds under ANY uvicorn version/configuration
    (and in TestClient, which bypasses the uvicorn transport entirely).
    """

    def __init__(self, app: Any, *, max_bytes: int = 256 * 1024) -> None:
        self.app = app
        self._max_bytes = int(max_bytes)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "websocket" or not str(
            scope.get("path", "")
        ).startswith(_WS_PATH):
            await self.app(scope, receive, send)
            return

        async def guarded_receive() -> dict[str, Any]:
            message = await receive()
            if message.get("type") == "websocket.receive":
                text = message.get("text")
                if isinstance(text, str) and len(text.encode("utf-8")) > self._max_bytes:
                    await send(
                        {
                            "type": "websocket.close",
                            "code": CLOSE_MESSAGE_TOO_BIG,
                            "reason": "message too large",
                        }
                    )
                    raise WebSocketDisconnect(
                        CLOSE_MESSAGE_TOO_BIG, "message too large"
                    )
            return message

        await self.app(scope, guarded_receive, send)


async def _close_later(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:  # noqa: BLE001 - close is best-effort
        return


def _client_ip(websocket: WebSocket) -> str:
    """The TRUST_PROXY-aware bridge-connect identity (same rule as every other
    per-IP admission budget in the app — DEF-094/PD-SEC-02)."""
    settings = websocket.app.state.settings
    trust_proxy = bool(getattr(settings, "trust_proxy", False))
    return resolve_client_ip(websocket, trust_proxy=trust_proxy)


def _record_failed_handshake(state: Any, ip: str, now: float) -> None:
    """ADV-252: count ONE failed handshake against the PER-IP failed-handshake
    window (best-effort accounting; never raises)."""
    gate = getattr(state, "bridge_failed_handshake_gate", None)
    if gate is None:
        return
    gate.record(str(ip) if ip else "<unknown>", now)


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
    now = float(websocket.app.state.clock.now())
    ip = _client_ip(websocket)
    # ADV-252 — the failed-handshake bound is a PER-IP window of failures: only
    # an IP that already burned ITS OWN failed-handshake budget is refused
    # pre-accept. No other IP is ever throttled by a foreign brute-force flurry
    # and a legitimate reconnect is never counted against the failure window
    # (successful handshakes consume the SEPARATE admission budget below).
    failed_gate = getattr(websocket.app.state, "bridge_failed_handshake_gate", None)
    if failed_gate is not None and failed_gate.over_limit(ip, now):
        await _close_later(
            websocket, CLOSE_POLICY_VIOLATION, "bridge handshake rate limit"
        )
        return
    await websocket.accept()
    raw = await _read_first_frame(websocket, settings)
    if raw is None:
        _record_failed_handshake(websocket.app.state, ip, now)
        await _close_later(websocket, CLOSE_POLICY_VIOLATION, "unauthorized")
        return
    try:
        frame = decode_inbound_frame(
            raw, max_bytes=settings.bridge_max_message_bytes
        )
    except BridgeFrameRejected as exc:
        _record_failed_handshake(websocket.app.state, ip, now)
        await _close_later(websocket, exc.close_code, exc.reason)
        return
    if not is_handshake_frame(frame):
        _record_failed_handshake(websocket.app.state, ip, now)
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
            now=now,
        )
    except BridgeFrameRejected as exc:
        _record_failed_handshake(websocket.app.state, ip, now)
        await _close_later(websocket, exc.close_code, exc.reason)
        return
    # ADV-252 — SUCCESSFUL pairings/connections consume the SEPARATE global
    # admission budget. A brute-force flurry of FAILED attempts never touches
    # it, so legitimate bridge reconnects are never throttled by failed
    # attempts from any source.
    admission_gate = getattr(websocket.app.state, "bridge_admission_gate", None)
    if admission_gate is not None and not admission_gate.allow("global", now):
        registry.detach(
            conn.bridge_session_id,
            now=now,
            epoch=conn.connection_epoch,
        )
        await _close_later(
            websocket, CLOSE_POLICY_VIOLATION, "bridge handshake rate limit"
        )
        return
    # This handler's OWN generation: a LATE detach is a no-op once a newer
    # connection (same bridge session) has superseded it (ADV-248).
    connection_epoch = conn.connection_epoch
    try:
        await websocket.send_text(ack)
    except Exception:  # noqa: BLE001 - peer vanished during handshake
        registry.detach(
            conn.bridge_session_id,
            now=float(websocket.app.state.clock.now()),
            epoch=connection_epoch,
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
        registry.detach(conn.bridge_session_id, now=now, epoch=connection_epoch)
        emit_event(
            "bridge.disconnected",
            bridgeSessionId=conn.bridge_session_id,
            reasonCode="SOCKET_CLOSED",
        )


__all__ = ["BridgeMessageSizeGuard", "router", "bridge_ws_endpoint"]