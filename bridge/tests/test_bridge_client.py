"""Hermetic integration tests for the bridge client: real asyncio loop, a fake
WSS server, and an in-process MockOllama. Nothing touches the network.

Covered: pairing + reconnect handshakes, token file, one-job-at-a-time,
bounded-backoff reconnect, ping/pong, job_cancel abort, typed failure mapping
and hostile-frame rejection (unknown types / oversized / deep-nested /
extra-fields / bad version / malformed JSON / idle timeout).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import pytest

from pd_ollama_bridge import protocol

from conftest import (
    TEST_PAIRING_CODE,
    TEST_TOKEN,
    make_bridge,
    make_config,
    make_ollama,
    make_token_store,
)
from fake_server import FakeBridgeServer, job_frame, pairing_accepted_with_token
from mock_ollama import MockOllama

JOB_A = "JOB-test-0000000001"
JOB_B = "JOB-test-0000000002"


async def _stop_bridge(bridge_task: asyncio.Task) -> None:
    if not bridge_task.done():
        bridge_task.cancel()
    try:
        await bridge_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


async def _harness(
    scenario,
    *,
    mock: Optional[MockOllama] = None,
    config_kwargs: Optional[dict] = None,
    post: Optional[Callable[[FakeBridgeServer, MockOllama, dict], Any]] = None,
    server: Optional[FakeBridgeServer] = None,
) -> None:
    server = server or FakeBridgeServer(scenario)
    await server.start()
    mock = mock or MockOllama()
    config = make_config(server_url=server.server_url, **(config_kwargs or {}))
    ollama = make_ollama(mock, connect_timeout=1.0)
    bridge, events = make_bridge(config, make_token_store(), ollama)
    bridge_task = asyncio.create_task(bridge.run())
    try:
        if post is not None:
            await post(server, mock, events)
        await server.wait_idle(timeout=6.0)
        assert server.errors == []
    finally:
        await _stop_bridge(bridge_task)
        await server.aclose()
        await ollama.aclose()


# --------------------------------------------------------------------------- #
# handshake: pairing + reconnect + token file
# --------------------------------------------------------------------------- #


def test_pairing_handshake_and_job_success():
    expected_output = {"victim": "Sarah Miller", "murderer": "Thomas Reed"}
    mock = MockOllama()

    async def scenario(conn, server):
        hello = await conn.recv_json()
        assert hello["type"] == "pairing_hello"
        assert hello["pairingCode"] == TEST_PAIRING_CODE
        assert hello["model"] == "hermes3:8b"
        assert "STRUCTURED_MODEL_INFERENCE" in hello["capabilities"]
        server.saw_hello = hello
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        await conn.send(job_frame(job_id=JOB_A))
        result = await conn.recv_json()
        assert result["type"] == "job_result"
        assert result["jobId"] == JOB_A
        assert result["status"] == "SUCCESS"
        assert result["structuredOutput"] == expected_output

    async def post(server, mock, events):
        assert await server.wait_until(lambda: len(events["connected"]) == 1)
        kind, host = events["connected"][0]
        assert kind == "pairing"
        assert host == "127.0.0.1"
        assert mock.requests[0]["model"] == "hermes3:8b"

    asyncio.run(_harness(scenario, mock=mock, post=post))


def test_reconnect_uses_persisted_token_with_bounded_backoff():
    mock = MockOllama()

    async def scenario(conn, server):
        index = server.conn_count
        if not hasattr(server, "hello_types"):
            server.hello_types = []
            server.tokens_seen = []
        hello = await conn.recv_json()
        server.hello_types.append(hello["type"])
        server.tokens_seen.append(hello.get("bridgeSessionToken"))
        if index == 1:
            await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
            await conn.close(1000)
            return
        assert hello["type"] == "bridge_hello"
        assert hello["bridgeSessionToken"] == TEST_TOKEN
        await conn.send(
            {
                "protocolVersion": 1,
                "type": "pairing_accepted",
                "bridgeSessionId": "PS-test-session-000000",
                "model": "hermes3:8b",
                "capabilities": ["STRUCTURED_MODEL_INFERENCE"],
            }
        )
        await conn.send({"protocolVersion": 1, "type": "ping"})
        pong = await conn.recv_json()
        assert pong == {"type": "pong"}

    async def post(server, mock, events):
        ok = await server.wait_until(lambda: len(events["connected"]) >= 2, timeout=6.0)
        assert ok, (events["connected"], server.hello_types)
        await asyncio.sleep(0.1)
        assert server.hello_types == ["pairing_hello", "bridge_hello"]
        assert server.tokens_seen == [None, TEST_TOKEN]
        assert events["connected"] == [
            ("pairing", "127.0.0.1"),
            ("reconnected", "127.0.0.1"),
        ]
        assert events["reconnecting"], "expected at least one bounded-backoff step"
        for _attempt, delay in events["reconnecting"]:
            assert delay <= 0.2, "reconnect backoff must be capped"

    asyncio.run(
        _harness(
            scenario,
            mock=mock,
            config_kwargs={
                "max_reconnect_attempts": 1,
                "reconnect_backoff_base_seconds": 0.01,
                "reconnect_backoff_cap_seconds": 0.2,
            },
            post=post,
        )
    )


def test_token_file_saved_with_0600(tmp_path: Path):
    path = tmp_path / "tokens" / "session_token"
    store = make_token_store(path)
    store.save(TEST_TOKEN)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == TEST_TOKEN
    fresh = make_token_store(path)
    assert fresh.load() == TEST_TOKEN
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_session_token_memory_only_by_default():
    store = make_token_store()
    store.save(TEST_TOKEN)
    assert store.path is None
    assert store.load() is None
    assert store.get() == TEST_TOKEN


# --------------------------------------------------------------------------- #
# job execution + typed failure mapping
# --------------------------------------------------------------------------- #


def test_busy_second_job_rejected_typing():
    mock = MockOllama(hang=True)

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        await conn.send(job_frame(job_id=JOB_A, timeout_ms=300_000))
        assert await server.wait_until(lambda: mock.inflight == 1)
        await conn.send(job_frame(job_id=JOB_B, timeout_ms=300_000))
        result = await conn.recv_json()
        assert result["type"] == "job_result"
        assert result["jobId"] == JOB_B
        assert result["status"] == "FAILED"
        assert result["failureCode"] == "BRIDGE_BUSY"
        await conn.send({"protocolVersion": 1, "type": "job_cancel", "jobId": JOB_A})
        assert await server.wait_until(lambda: mock.cancelled == 1)

    asyncio.run(_harness(scenario, mock=mock))


def test_ollama_down_maps_to_local_ollama_unavailable():
    mock = MockOllama(raise_exc=httpx.ConnectError("connection refused"))

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        await conn.send(job_frame(job_id=JOB_A))
        result = await conn.recv_json()
        assert result["jobId"] == JOB_A
        assert result["status"] == "FAILED"
        assert result["failureCode"] == "LOCAL_OLLAMA_UNAVAILABLE"

    asyncio.run(_harness(scenario, mock=mock))


def test_model_missing_maps_to_local_model_unavailable():
    mock = MockOllama(status=404)

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        await conn.send(job_frame(job_id=JOB_A))
        result = await conn.recv_json()
        assert result["jobId"] == JOB_A
        assert result["status"] == "FAILED"
        assert result["failureCode"] == "LOCAL_MODEL_UNAVAILABLE"

    asyncio.run(_harness(scenario, mock=mock))


def test_provider_timeout_maps_typed():
    mock = MockOllama(hang=True)

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        await conn.send(job_frame(job_id=JOB_A, timeout_ms=1_000))
        result = await conn.recv_json(timeout=5.0)
        assert result["jobId"] == JOB_A
        assert result["status"] == "FAILED"
        assert result["failureCode"] == "LOCAL_PROVIDER_TIMEOUT"
        assert mock.cancelled == 1

    asyncio.run(
        _harness(
            scenario,
            mock=mock,
            config_kwargs={"connect_timeout_seconds": 1.0},
        )
    )


def test_invalid_ollama_output_maps_typed():
    mock = MockOllama(content='{"message":{"content":"this is not json"}}')

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        await conn.send(job_frame(job_id=JOB_A))
        result = await conn.recv_json()
        assert result["jobId"] == JOB_A
        assert result["status"] == "FAILED"
        assert result["failureCode"] == "LOCAL_PROVIDER_INVALID_OUTPUT"

    asyncio.run(_harness(scenario, mock=mock))


def test_jobs_processed_sequentially():
    mock = MockOllama()

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        for job_id in ("JOB-A", "JOB-B", "JOB-C"):
            await conn.send(job_frame(job_id=job_id))
            result = await conn.recv_json()
            assert result["jobId"] == job_id
            assert result["status"] == "SUCCESS"
            assert result["structuredOutput"]["victim"] == "Sarah Miller"

    asyncio.run(_harness(scenario, mock=mock))


# --------------------------------------------------------------------------- #
# control: ping/pong, cancel, idle
# --------------------------------------------------------------------------- #


def test_ping_answered_with_pong():
    mock = MockOllama()

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        for _ in range(2):
            await conn.send({"protocolVersion": 1, "type": "ping"})
            pong = await conn.recv_json()
            assert pong == {"type": "pong"}

    asyncio.run(_harness(scenario, mock=mock))


def test_job_cancel_aborts_local_call():
    mock = MockOllama(hang=True)

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        await conn.send(job_frame(job_id=JOB_A))
        assert await server.wait_until(lambda: mock.inflight == 1)
        await conn.send({"protocolVersion": 1, "type": "job_cancel", "jobId": JOB_A})
        assert await server.wait_until(lambda: mock.cancelled == 1)
        with pytest.raises(asyncio.TimeoutError):
            await conn.recv_raw(timeout=0.4)

    asyncio.run(_harness(scenario, mock=mock))


def test_idle_timeout_closes_with_1001():
    mock = MockOllama()

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        code, reason = await conn.wait_closed(timeout=5.0)
        server.idle_close = (code, reason)

    async def post(server, mock, events):
        assert await server.wait_until(
            lambda: getattr(server, "idle_close", None) is not None, timeout=5.0
        )
        code, _reason = server.idle_close
        assert code == protocol.CLOSE_IDLE_TIMEOUT

    asyncio.run(
        _harness(
            scenario,
            mock=mock,
            config_kwargs={"idle_timeout_seconds": 0.3, "max_reconnect_attempts": 0},
            post=post,
        )
    )


# --------------------------------------------------------------------------- #
# hostile frames
# --------------------------------------------------------------------------- #


def _run_hostile(frame: str, expected_close: int):
    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        await conn.send(frame)
        code, _reason = await conn.wait_closed(timeout=5.0)
        server.close_code = code

    async def post(server, mock, events):
        assert await server.wait_until(
            lambda: getattr(server, "close_code", None) is not None, timeout=5.0
        )
        assert server.close_code == expected_close, (server.close_code, server.errors)

    asyncio.run(
        _harness(
            scenario,
            config_kwargs={"max_reconnect_attempts": 0},
            post=post,
        )
    )


def test_unknown_type_closed_with_1003():
    _run_hostile(
        json.dumps({"protocolVersion": 1, "type": "run_command", "cmd": "rm -rf"}),
        protocol.CLOSE_UNSUPPORTED_TYPE,
    )


def test_oversized_frame_closed_with_1009():
    pad = "a" * (protocol.BRIDGE_MAX_MESSAGE_BYTES + 100)
    raw = json.dumps(
        {"protocolVersion": 1, "type": "ping", "pad": pad}, separators=(",", ":")
    )
    assert len(raw.encode("utf-8")) > protocol.BRIDGE_MAX_MESSAGE_BYTES
    _run_hostile(raw, protocol.CLOSE_MESSAGE_TOO_BIG)


def test_deep_nested_frame_closed_with_1002():
    raw = '{"a":' + "[" * 100 + "1" + "]" * 100 + "}"
    _run_hostile(raw, protocol.CLOSE_PROTOCOL_ERROR)


def test_wrong_protocol_version_closed_with_1002():
    _run_hostile(
        json.dumps({"protocolVersion": 2, "type": "ping"}),
        protocol.CLOSE_PROTOCOL_ERROR,
    )


def test_malformed_json_closed_with_1002():
    _run_hostile("this is not json at all", protocol.CLOSE_PROTOCOL_ERROR)


def test_job_frame_with_extra_fields_rejected_not_executed():
    mock = MockOllama()

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        hostile = job_frame(job_id=JOB_A)
        hostile["fetch"] = "http://attacker.example/steal"
        hostile["shell"] = "powershell -c evil"
        await conn.send(hostile)
        code, _reason = await conn.wait_closed(timeout=5.0)
        server.close_code = code

    async def post(server, mock, events):
        assert await server.wait_until(
            lambda: getattr(server, "close_code", None) is not None, timeout=5.0
        )
        assert server.close_code == protocol.CLOSE_PROTOCOL_ERROR, server.errors
        assert mock.requests == [], "the hostile job must never reach Ollama"

    asyncio.run(
        _harness(
            scenario,
            mock=mock,
            config_kwargs={"max_reconnect_attempts": 0},
            post=post,
        )
    )


# --------------------------------------------------------------------------- #
# reconnect failures
# --------------------------------------------------------------------------- #


def test_invalid_reconnect_token_is_terminal():
    mock = MockOllama()

    async def scenario(conn, server):
        hello = await conn.recv_json()
        assert hello["type"] == "bridge_hello"
        assert hello["bridgeSessionToken"] == TEST_TOKEN
        await conn.close(1008, "unauthorized")

    async def post(server, mock, events):
        await asyncio.sleep(0.3)
        assert server.conn_count == 1, "terminal rejection must not reconnect-loop"

    async def core():
        server = FakeBridgeServer(scenario)
        await server.start()
        config = make_config(
            server_url=server.server_url,
            max_reconnect_attempts=5,
            reconnect_backoff_base_seconds=0.01,
            reconnect_backoff_cap_seconds=0.1,
        )
        token_store = make_token_store()
        token_store.save(TEST_TOKEN)
        ollama = make_ollama(mock, connect_timeout=1.0)
        bridge, _events = make_bridge(config, token_store, ollama)
        bridge_task = asyncio.create_task(bridge.run())
        try:
            await asyncio.wait_for(bridge_task, timeout=5.0)
            await post(server, mock, None)
            assert server.errors == []
        finally:
            await _stop_bridge(bridge_task)
            await server.aclose()
            await ollama.aclose()

    asyncio.run(core())