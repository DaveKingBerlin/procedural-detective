"""The bridge client: outbound WSS session, handshake, job loop, reconnect.

Security-relevant decisions live here and are exercised only with the
operator-owned values from ``Config``:

- the WSS endpoint is derived from the operator's ``--server`` (never from the
  server);
- TLS with hostname validation is used for any non-local host (an ``ssl``
  context with default verification); ``ws://`` is accepted ONLY for
  loopback/dev;
- every inbound frame is byte-, depth- and schema-validated BEFORE dispatch
  (a hostile server cannot smuggle extra fields — the bridge runs structural
  inference only);
- at most ONE job runs at a time; a second job while busy is answered with the
  typed ``BRIDGE_BUSY`` failure;
- ``job_cancel`` aborts the in-flight local Ollama call (task cancellation);
  a cancelled job never sends a result;
- reconnect uses the persisted bridge session token with bounded backoff;
  a server 1008 close during a reconnect handshake is terminal (re-pair).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import ssl
import time
from typing import Any, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from . import protocol
from .config import Config, TokenStore
from .ollama_client import OllamaClient, OllamaClientError

LOGGER = logging.getLogger("pd-ollama-bridge")

_WS_PATH = "/api/v1/bridge/ws"


class SessionRejected(Exception):
    """The pairing/reconnect handshake was authorization-failed (terminal)."""


class _Inflight:
    __slots__ = ("job_id", "task")

    def __init__(self, job_id: str, task: asyncio.Task) -> None:
        self.job_id = job_id
        self.task = task


def _effective_job_timeout_seconds(timeout_ms: int) -> float:
    margin = min(5_000, int(timeout_ms) // 2)
    return max(0.3, (int(timeout_ms) - margin) / 1000.0)


class BridgeClient:
    def __init__(
        self,
        *,
        config: Config,
        token_store: TokenStore,
        ollama: OllamaClient,
        logger: logging.Logger = LOGGER,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
        on_connected: Optional[Callable[[str, str], None]] = None,
        on_reconnecting: Optional[Callable[[int, float], None]] = None,
    ) -> None:
        self.config = config
        self.token_store = token_store
        self.ollama = ollama
        self.logger = logger
        self._clock = clock
        self._sleep = sleep
        self._on_connected = on_connected
        self._on_reconnecting = on_reconnecting
        self._inflight: Optional[_Inflight] = None
        self._last_seen: float = 0.0
        self.sessions_connected: int = 0

    async def run(self) -> None:
        consecutive = 0
        while True:
            stable = False
            try:
                ws = await self._connect_once()
                started = self._clock()
                await self._serve(ws)
                stable = (self._clock() - started) >= self.config.reconnect_reset_seconds
            except SessionRejected as exc:
                self.logger.error("bridge: %s", exc)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - link drop, retry
                self.logger.warning("bridge: link dropped: %s", type(exc).__name__)
            if stable:
                consecutive = 0
            if consecutive >= self.config.max_reconnect_attempts:
                self.logger.warning("bridge: max reconnect attempts reached; giving up")
                return
            delay = min(
                self.config.reconnect_backoff_base_seconds * (2 ** consecutive),
                self.config.reconnect_backoff_cap_seconds,
            )
            consecutive += 1
            self.logger.info(
                "bridge: disconnected; reconnecting in %.1fs (attempt %d)",
                delay,
                consecutive,
            )
            await self._maybe_call(self._on_reconnecting, consecutive, delay)
            await self._sleep(delay)

    # -- connection + handshake ------------------------------------------------

    @staticmethod
    async def _maybe_call(callback: Optional[Callable[..., Any]], *args: Any) -> None:
        if callback is None:
            return
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    async def _connect_once(self) -> Any:
        ws_url = self.config.server_ws_url
        ssl_ctx = None
        if ws_url.startswith("wss://"):
            ssl_ctx = ssl.create_default_context()
        try:
            ws = await websockets.connect(
                ws_url,
                ssl=ssl_ctx,
                max_size=self.config.max_message_bytes + 1024,
                max_queue=32,
                ping_interval=None,
                ping_timeout=None,
                open_timeout=self.config.connect_timeout_seconds,
                close_timeout=10.0,
                proxy=None,
                user_agent_header="pd-ollama-bridge/1",
            )
        except Exception as exc:  # noqa: BLE001 - retried by run()
            raise ConnectionError(f"cannot connect to {self._display_host(ws_url)}") from exc
        try:
            session_id, kind = await self._handshake(ws)
        except SessionRejected:
            await self._safe_close(ws, protocol.CLOSE_POLICY_VIOLATION, "unauthorized")
            raise
        except Exception as exc:  # noqa: BLE001 - retried by run()
            await self._safe_close(ws, 1011, "handshake error")
            raise ConnectionError("handshake failed") from exc
        self.logger.info("bridge: %s session %s bound", kind, session_id)
        self.sessions_connected += 1
        await self._maybe_call(self._on_connected, kind, self._display_host(ws_url))
        return ws

    async def _handshake(self, ws: Any) -> tuple[str, str]:
        token = self.token_store.get()
        if token:
            hello = protocol.bridge_hello_frame(bridge_session_token=token)
            kind = "reconnected"
        else:
            if not self.config.pairing_code:
                raise SessionRejected(
                    "pairing code required and no bridge session token present"
                )
            hello = protocol.pairing_hello_frame(
                pairing_code=self.config.pairing_code,
                model=self.config.model,
                capabilities=self.config.capabilities,
            )
            kind = "pairing"
        await ws.send(hello)
        try:
            raw = await asyncio.wait_for(
                ws.recv(), timeout=self.config.connect_timeout_seconds
            )
        except ConnectionClosed as exc:
            if exc.rcvd is not None and exc.rcvd.code == protocol.CLOSE_POLICY_VIOLATION:
                raise SessionRejected(
                    "bridge session rejected or expired; re-pair with a new code"
                ) from exc
            raise ConnectionError("server closed during handshake") from exc
        except asyncio.TimeoutError as exc:
            raise ConnectionError("server did not reply to the handshake") from exc
        try:
            msg = protocol.validate_frame(
                protocol.decode_frame(raw, max_bytes=self.config.max_message_bytes)
            )
        except protocol.BridgeProtocolError as exc:
            raise SessionRejected(
                f"server sent an invalid handshake reply ({exc.reason})"
            ) from exc
        if msg["type"] != protocol.MSG_PAIRING_ACCEPTED:
            raise SessionRejected("server sent an unexpected handshake reply")
        session_id = msg["bridgeSessionId"]
        server_token = msg.get("bridgeSessionToken")
        if kind == "pairing":
            if not isinstance(server_token, str):
                raise SessionRejected("server did not issue a bridge session token")
            self.token_store.save(server_token)
        return session_id, kind

    # -- serving loop ----------------------------------------------------------

    async def _serve(self, ws: Any) -> None:
        self._last_seen = self._clock()
        try:
            while True:
                idle_remaining = (
                    self._last_seen + self.config.idle_timeout_seconds - self._clock()
                )
                wait = max(0.0, idle_remaining)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=wait)
                except asyncio.TimeoutError:
                    self.logger.info("bridge: idle timeout; closing")
                    await self._safe_close(ws, protocol.CLOSE_IDLE_TIMEOUT, "idle timeout")
                    return
                except ConnectionClosed:
                    return
                self._last_seen = self._clock()
                try:
                    msg = protocol.validate_frame(
                        protocol.decode_frame(raw, max_bytes=self.config.max_message_bytes)
                    )
                except protocol.BridgeProtocolError as exc:
                    self.logger.warning(
                        "bridge: rejecting frame (close %s): %s",
                        exc.close_code,
                        exc.reason,
                    )
                    await self._safe_close(ws, exc.close_code, exc.reason)
                    return
                await self._dispatch(ws, msg)
        finally:
            if self._inflight is not None:
                self._inflight.task.cancel()
                self._inflight = None

    async def _dispatch(self, ws: Any, msg: dict[str, Any]) -> None:
        msg_type = msg["type"]
        if msg_type == protocol.MSG_PING:
            try:
                await ws.send(protocol.pong_frame())
            except ConnectionClosed:
                pass
            return
        if msg_type == protocol.MSG_JOB:
            if self._inflight is not None:
                await self._send_failed(ws, msg["jobId"], "BRIDGE_BUSY")
                return
            self._inflight = _Inflight(
                job_id=msg["jobId"],
                task=asyncio.create_task(self._run_job(ws, msg)),
            )
            return
        if msg_type == protocol.MSG_JOB_CANCEL:
            if self._inflight is not None and self._inflight.job_id == msg["jobId"]:
                self._inflight.task.cancel()
            return
        if msg_type == protocol.MSG_PAIRING_ACCEPTED:
            await self._safe_close(ws, protocol.CLOSE_PROTOCOL_ERROR, "invalid frame")
            return
        await self._safe_close(ws, protocol.CLOSE_UNSUPPORTED_TYPE, "unknown message type")

    # -- job runner -------------------------------------------------------------

    async def _run_job(self, ws: Any, msg: dict[str, Any]) -> None:
        job_id = msg["jobId"]
        my_task = asyncio.current_task()
        started = self._clock()
        try:
            output = await asyncio.wait_for(
                self.ollama.run_structured_inference(
                    prompt=msg["prompt"],
                    temperature=msg["temperature"],
                    timeout_ms=msg["timeoutMs"],
                ),
                timeout=_effective_job_timeout_seconds(msg["timeoutMs"]),
            )
        except asyncio.CancelledError:
            self.logger.info("bridge: job %s cancelled; local call aborted", job_id)
            return
        except asyncio.TimeoutError:
            await self._send_failed(ws, job_id, "LOCAL_PROVIDER_TIMEOUT")
            self.logger.info("bridge: job %s failed LOCAL_PROVIDER_TIMEOUT", job_id)
            return
        except OllamaClientError as exc:
            code = exc.failure_code
            await self._send_failed(ws, job_id, code)
            self.logger.info("bridge: job %s failed %s", job_id, code)
            return
        except Exception:  # noqa: BLE001 - closed vocabulary, no raw text
            await self._send_failed(ws, job_id, "LOCAL_PROVIDER_INVALID_OUTPUT")
            self.logger.info("bridge: job %s failed LOCAL_PROVIDER_INVALID_OUTPUT", job_id)
            return
        finally:
            if self._inflight is not None and self._inflight.task is my_task:
                self._inflight = None
        latency_ms = (self._clock() - started) * 1000.0
        await self._send_success(ws, job_id, output)
        self.logger.info("bridge: job %s completed in %dms", job_id, int(latency_ms))

    async def _send_success(self, ws: Any, job_id: str, output: Any) -> None:
        try:
            await ws.send(
                protocol.job_result_success_frame(
                    job_id=job_id, structured_output=dict(output)
                )
            )
        except ConnectionClosed:
            pass

    async def _send_failed(self, ws: Any, job_id: str, failure_code: str) -> None:
        try:
            await ws.send(
                protocol.job_result_failed_frame(job_id=job_id, failure_code=failure_code)
            )
        except ConnectionClosed:
            pass

    @staticmethod
    async def _safe_close(ws: Any, code: int, reason: str) -> None:
        try:
            await ws.close(code=code, reason=reason)
        except Exception:  # noqa: BLE001 - best effort
            pass

    @staticmethod
    def _display_host(ws_url: str) -> str:
        prefix = "wss://" if ws_url.startswith("wss://") else "ws://"
        remainder = ws_url[len(prefix):]
        host = remainder.split("/", 1)[0]
        return host.split(":")[0].strip("[]") or "server"


__all__ = ["BridgeClient", "SessionRejected", "_effective_job_timeout_seconds"]