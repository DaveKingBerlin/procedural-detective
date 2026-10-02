"""Fix B (§11 observability) — the bridge CLIENT reports the honest send
outcome for job results (delivered vs ConnectionClosed) instead of implying
delivery. No secrets/payloads ever enter the log lines.

Hermetic: real asyncio loop + the in-process fake WSS server; no network.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest
import websockets

from pd_ollama_bridge.bridge_client import BridgeClient

from conftest import (
    TEST_PAIRING_CODE,
    make_config,
    make_ollama,
    make_token_store,
)
from fake_server import FakeBridgeServer, pairing_accepted_with_token
from mock_ollama import MockOllama

_JOB = "JOB-fixb-outcome"


async def _paired_ws(server) -> tuple[Any, BridgeClient]:
    """connect + pairing handshake; returns (ws, bridge)."""
    config = make_config(server_url=server.server_url, max_reconnect_attempts=0)
    bridge = BridgeClient(
        config=config,
        token_store=make_token_store(),
        ollama=make_ollama(MockOllama()),
    )
    ws = await websockets.connect(server.ws_url, open_timeout=5, close_timeout=2)
    await ws.send(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "pairing_hello",
                "pairingCode": TEST_PAIRING_CODE,
                "model": "hermes3:8b",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    await asyncio.wait_for(ws.recv(), timeout=5)  # pairing_accepted ack
    return ws, bridge


async def _teardown(ws, server, bridge) -> None:
    try:
        await ws.close()
    except Exception:  # noqa: BLE001 - teardown
        pass
    await server.aclose()
    await bridge.ollama.aclose()


def _joined(caplog) -> str:
    return "\n".join(r.message for r in caplog.records)


class _RaisingTransport:
    """A transport whose ``send`` raises a fixed exception on every frame —
    the F2 probe for a torn socket surfaced as OSError/RuntimeError instead of
    the library's ConnectionClosed."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def send(self, *_args: Any, **_kwargs: Any) -> None:
        raise self._exc


def test_send_success_reports_delivered(caplog):
    async def scenario(conn, server):
        hello = await conn.recv_json()
        assert hello["type"] == "pairing_hello"
        await conn.send(pairing_accepted_with_token())
        result = await conn.recv_json()
        assert result["type"] == "job_result"
        assert result["jobId"] == _JOB
        assert result["status"] == "SUCCESS"
        assert result["structuredOutput"] == {"victim": "A"}

    async def core():
        server = FakeBridgeServer(scenario)
        await server.start()
        ws, bridge = await _paired_ws(server)
        try:
            delivered = await bridge._send_success(ws, _JOB, {"victim": "A"})
            assert delivered is True
        finally:
            await _teardown(ws, server, bridge)

    with caplog.at_level(logging.INFO, logger="pd-ollama-bridge"):
        asyncio.run(core())
    joined = _joined(caplog)
    assert "result delivered" in joined
    assert "NOT delivered" not in joined
    # The log NEVER contains the payload or the token.
    assert "victim" not in joined


def test_send_success_reports_connection_closed(caplog):
    async def scenario(conn, server):
        hello = await conn.recv_json()
        assert hello["type"] == "pairing_hello"
        await conn.send(pairing_accepted_with_token())

    async def core():
        server = FakeBridgeServer(scenario)
        await server.start()
        ws, bridge = await _paired_ws(server)
        try:
            # The transport drops BEFORE the bridge can flush the result: the
            # client-side close fully completes, then the send raises
            # ConnectionClosed deterministically.
            await ws.close()
            delivered = await bridge._send_success(ws, _JOB, {"victim": "A"})
            assert delivered is False
        finally:
            await _teardown(ws, server, bridge)

    with caplog.at_level(logging.INFO, logger="pd-ollama-bridge"):
        asyncio.run(core())
    joined = _joined(caplog)
    assert "NOT delivered (connection closed)" in joined
    assert "result delivered" not in joined
    assert "victim" not in joined  # payload value never logged


def test_send_failed_reports_delivered(caplog):
    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token())
        result = await conn.recv_json()
        assert result["type"] == "job_result"
        assert result["jobId"] == _JOB
        assert result["status"] == "FAILED"
        assert result["failureCode"] == "BRIDGE_BUSY"

    async def core():
        server = FakeBridgeServer(scenario)
        await server.start()
        ws, bridge = await _paired_ws(server)
        try:
            delivered = await bridge._send_failed(ws, _JOB, "BRIDGE_BUSY")
            assert delivered is True
        finally:
            await _teardown(ws, server, bridge)

    with caplog.at_level(logging.INFO, logger="pd-ollama-bridge"):
        asyncio.run(core())
    joined = _joined(caplog)
    assert "failure BRIDGE_BUSY delivered" in joined


def test_send_failed_reports_connection_closed(caplog):
    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token())

    async def core():
        server = FakeBridgeServer(scenario)
        await server.start()
        ws, bridge = await _paired_ws(server)
        try:
            await ws.close()
            delivered = await bridge._send_failed(ws, _JOB, "BRIDGE_BUSY")
            assert delivered is False
        finally:
            await _teardown(ws, server, bridge)

    with caplog.at_level(logging.INFO, logger="pd-ollama-bridge"):
        asyncio.run(core())
    joined = _joined(caplog)
    assert "NOT delivered (connection closed)" in joined
    assert "BRIDGE_BUSY" in joined  # the typed code is part of the safe message


def test_send_success_reports_oserror_transport_failure(caplog):
    """F2 — a torn transport raising ``OSError`` (not ConnectionClosed) must be
    treated as NOT delivered: honest bool, honest log, and the exception
    MESSAGE text / payload / token never enter the log (only the class name)."""

    async def core():
        bridge = BridgeClient(
            config=make_config(server_url="wss://127.0.0.1:1", max_reconnect_attempts=0),
            token_store=make_token_store(),
            ollama=make_ollama(MockOllama()),
        )
        try:
            transport = _RaisingTransport(OSError("broken pipe 127.0.0.1:11434"))
            delivered = await bridge._send_success(transport, _JOB, {"victim": "A"})
            assert delivered is False
        finally:
            await bridge.ollama.aclose()

    with caplog.at_level(logging.INFO, logger="pd-ollama-bridge"):
        asyncio.run(core())
    joined = _joined(caplog)
    assert "result NOT delivered (connection closed) [OSError]" in joined
    assert "result delivered" not in joined
    assert "victim" not in joined  # payload value never logged
    assert "broken pipe" not in joined  # exception MESSAGE never echoed


def test_send_failed_reports_runtimeerror_transport_failure(caplog):
    """F2 twin for ``_send_failed``: a ``RuntimeError``-raising transport is
    honestly reported as NOT delivered; the typed failure code + exception
    class name are the only diagnostic detail exposed."""

    async def core():
        bridge = BridgeClient(
            config=make_config(server_url="wss://127.0.0.1:1", max_reconnect_attempts=0),
            token_store=make_token_store(),
            ollama=make_ollama(MockOllama()),
        )
        try:
            transport = _RaisingTransport(RuntimeError("websocket write failed"))
            delivered = await bridge._send_failed(transport, _JOB, "BRIDGE_BUSY")
            assert delivered is False
        finally:
            await bridge.ollama.aclose()

    with caplog.at_level(logging.INFO, logger="pd-ollama-bridge"):
        asyncio.run(core())
    joined = _joined(caplog)
    assert "failure BRIDGE_BUSY NOT delivered (connection closed) [RuntimeError]" in joined
    assert "failure BRIDGE_BUSY delivered" not in joined
    assert "write failed" not in joined  # exception MESSAGE never echoed
    assert "victim" not in joined


def test_send_success_never_masks_cancelled_error(caplog):
    """F2 — ``asyncio.CancelledError`` is NEVER swallowed by the broadened
    send-outcome handling: a task cancelled mid-send must propagate (no
    dishonest 'NOT delivered' log, no implied-delivery log)."""

    async def core():
        bridge = BridgeClient(
            config=make_config(server_url="wss://127.0.0.1:1", max_reconnect_attempts=0),
            token_store=make_token_store(),
            ollama=make_ollama(MockOllama()),
        )
        try:
            transport = _RaisingTransport(asyncio.CancelledError())
            await bridge._send_success(transport, _JOB, {"victim": "A"})
        finally:
            await bridge.ollama.aclose()

    with caplog.at_level(logging.INFO, logger="pd-ollama-bridge"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(core())
    joined = _joined(caplog)
    assert "NOT delivered" not in joined
    assert "result delivered" not in joined
    assert "CAUGHT" not in joined