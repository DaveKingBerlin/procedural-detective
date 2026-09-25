"""A hermetic in-process fake bridge SERVER (loopback WSS) for tests.

Only the server side of the Phase 22 bridge protocol is modelled: the tests
drive frame-by-frame behaviour through a per-connection ``scenario`` coroutine
that receives a ``Conn`` helper. Everything runs on ONE asyncio event loop
with the bridge client under test, so no real network or certificate setup is
involved. Scenario assertion failures are recorded on the server object so a
test can fail loudly instead of silently swallowing them.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from typing import Any, Awaitable, Callable, List, Optional, Tuple

import websockets
from websockets.asyncio.server import serve

Scenario = Callable[["Conn", "FakeBridgeServer"], Awaitable[None]]


class Conn:
    """One server-side connection helper exposed to a scenario."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.sent: List[str] = []

    async def recv_raw(self, timeout: float = 5.0) -> str:
        raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
        if isinstance(raw, bytes):
            raise AssertionError("expected a text frame, got binary")
        return raw

    async def recv_json(self, timeout: float = 5.0) -> dict[str, Any]:
        return json.loads(await self.recv_raw(timeout))

    async def send(self, payload: Any) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
        self.sent.append(text)
        await self.ws.send(text)

    async def wait_closed(self, timeout: float = 5.0) -> Tuple[int, Optional[str]]:
        await asyncio.wait_for(self.ws.wait_closed(), timeout=timeout)
        return int(self.ws.close_code), self.ws.close_reason

    async def close(self, code: int = 1000, reason: Optional[str] = None) -> None:
        try:
            if reason is None:
                await self.ws.close(code)
            else:
                await self.ws.close(code, reason=reason)
        except Exception:  # noqa: BLE001 - already closed
            pass


class FakeBridgeServer:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self._server: Any = None
        self._tasks: List[asyncio.Task] = []
        self.port: int = 0
        self.conn_count = 0
        self.errors: List[str] = []

    @property
    def http_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/api/v1/bridge/ws"

    @property
    def server_url(self) -> str:
        return self.http_url

    async def start(self) -> None:
        self._server = await serve(
            self._on_conn, "127.0.0.1", 0, max_size=2 ** 20, ping_interval=None
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def _on_conn(self, ws: Any) -> None:
        self.conn_count += 1
        conn = Conn(ws)
        task = asyncio.current_task()
        self._tasks.append(task)
        try:
            await self.scenario(conn, self)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - record for the test
            self.errors.append(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            )
        finally:
            try:
                await conn.close(1000)
            except Exception:  # noqa: BLE001 - best effort
                pass
            try:
                self._tasks.remove(task)
            except ValueError:
                pass

    async def wait_until(self, predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            try:
                if predicate():
                    return True
            except (IndexError, AttributeError, KeyError, AssertionError):
                pass
            await asyncio.sleep(0.02)
        return False

    async def wait_idle(self, timeout: float = 6.0) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._tasks:
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.02)
        return True

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            try:
                await asyncio.wait_for(task, timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # noqa: BLE001 - best effort
                pass


_ACK_WITH_TOKEN = {
    "protocolVersion": 1,
    "type": "pairing_accepted",
    "bridgeSessionId": "PS-test-session-000000",
    "bridgeSessionToken": "BRIDGE_TOKEN_FOR_TESTING_000001",
    "model": "hermes3:8b",
    "capabilities": ["STRUCTURED_MODEL_INFERENCE"],
}

_ACK_NO_TOKEN = {
    "protocolVersion": 1,
    "type": "pairing_accepted",
    "bridgeSessionId": "PS-test-session-000000",
    "model": "hermes3:8b",
    "capabilities": ["STRUCTURED_MODEL_INFERENCE"],
}


def pairing_accepted_with_token(*, token: Optional[str] = None) -> dict[str, Any]:
    if token is None:
        return dict(_ACK_WITH_TOKEN)
    frame = dict(_ACK_WITH_TOKEN)
    frame["bridgeSessionToken"] = token
    return frame


def pairing_accepted_without_token() -> dict[str, Any]:
    return dict(_ACK_NO_TOKEN)


def job_frame(
    *,
    job_id: str = "JOB-test-0000000001",
    schema_id: str = "ASSET_SPEC_v1",
    model: Optional[str] = "hermes3:8b",
    prompt: str = "a test prompt",
    temperature: float = 0.1,
    timeout_ms: int = 120_000,
) -> dict[str, Any]:
    return {
        "protocolVersion": 1,
        "type": "job",
        "jobId": job_id,
        "jobType": "STRUCTURED_INFERENCE",
        "schemaId": schema_id,
        "model": model,
        "prompt": prompt,
        "temperature": temperature,
        "timeoutMs": timeout_ms,
    }


__all__ = [
    "Conn",
    "FakeBridgeServer",
    "Scenario",
    "job_frame",
    "pairing_accepted_with_token",
    "pairing_accepted_without_token",
]