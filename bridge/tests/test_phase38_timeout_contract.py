"""Phase38 — Bridge/Ollama timeout-contract tests (§19-§23, §21/§37/§39).

Proves the SINGLE-authority timeout ownership:

- ``timeoutMs`` IS the server-issued authoritative per-job budget; the bridge
  outer wait honors it EXACTLY (no implicit second 50% reduction — the §21
  real-shape regression: remaining 6281ms -> server effective 6181ms must NOT
  become ~3091ms again);
- ONLY the local Ollama HTTP/body read subtracts ONE small, bounded, documented
  cleanup reserve (single owner) so the bridge can cancel/serialize/deliver the
  FAILED frame before the outer wait fires;
- timeout firing is classified with the closed machine-readable vocabulary
  (BRIDGE_JOB_TIMEOUT / OLLAMA_HTTP_TIMEOUT) — never raw exception text.

Hermetic: real asyncio loop + in-process fake server / MockOllama; no network.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from pd_ollama_bridge.bridge_client import BridgeClient, _effective_job_timeout_seconds
from pd_ollama_bridge.ollama_client import (
    OLLAMA_HTTP_MIN_TIMEOUT_MS,
    OLLAMA_HTTP_READ_RESERVE_MS,
    ProviderTimeout,
    _effective_timeout_ms,
)

from conftest import (
    TEST_PAIRING_CODE,
    TEST_TOKEN,
    make_config,
    make_ollama,
    make_token_store,
)
from fake_server import FakeBridgeServer, job_frame, pairing_accepted_with_token
from mock_ollama import MockOllama

JOB = "JOB-phase38-timeout"

# The §21/§37 REAL-SHAPE values: generation deadline remaining = 6281ms,
# configured provider timeout = 180000ms, server effective timeout = 6181ms.
SERVER_REMAINING_MS = 6_281
SERVER_EFFECTIVE_MS = 6_181


class _NeverReplies:
    """A stub local-Ollama that never answers within any server budget."""

    async def run_structured_inference(self, **_kwargs):
        await asyncio.sleep(30)  # far past the outer 10s window


async def _stop_bridge(bridge_task: asyncio.Task) -> None:
    if not bridge_task.done():
        bridge_task.cancel()
    try:
        await bridge_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# §21/§37 — no undocumented second 50% clamp
# --------------------------------------------------------------------------- #


def test_bridge_outer_wait_honors_server_issued_timeout_exactly():
    """The bridge's outer wait is the server-issued budget converted to
    seconds — 6181ms -> 6.181s (NOT ~3.091s, the old silent 50% cut)."""
    assert _effective_job_timeout_seconds(SERVER_EFFECTIVE_MS) == pytest.approx(
        SERVER_EFFECTIVE_MS / 1000.0, abs=0.0001
    )
    # the regression: the second half-window reduction is gone.
    assert _effective_job_timeout_seconds(SERVER_EFFECTIVE_MS) > 3.1


def test_ollama_http_read_uses_single_documented_reserve_not_half():
    """Only the local HTTP/body read subtracts ONE small documented reserve
    from the server budget (single owner); it never halves the budget."""
    effective = _effective_timeout_ms(SERVER_EFFECTIVE_MS)
    assert effective == SERVER_EFFECTIVE_MS - OLLAMA_HTTP_READ_RESERVE_MS
    assert effective > SERVER_EFFECTIVE_MS / 2  # no 50% cut
    assert OLLAMA_HTTP_READ_RESERVE_MS <= 500
    assert OLLAMA_HTTP_MIN_TIMEOUT_MS >= 100


def test_real_shape_sequence_ends_with_documented_numbers():
    """remaining 6281 -> server effective 6181 -> bridge outer 6.181s ->
    Ollama HTTP read 5931ms. Every number follows the documented contract."""
    # server-side (backend BudgetTracker clamps by the 100ms margin)
    assert SERVER_EFFECTIVE_MS == SERVER_REMAINING_MS - 100
    # bridge
    assert _effective_job_timeout_seconds(SERVER_EFFECTIVE_MS) == 6.181
    # ollama HTTP read (single reserve)
    assert _effective_timeout_ms(SERVER_EFFECTIVE_MS) == (
        SERVER_EFFECTIVE_MS - OLLAMA_HTTP_READ_RESERVE_MS
    )
    assert _effective_timeout_ms(SERVER_EFFECTIVE_MS) != pytest.approx(
        SERVER_EFFECTIVE_MS // 2, abs=1
    )


def test_tiny_server_budgets_keep_the_documented_floor():
    assert _effective_timeout_ms(400) == max(OLLAMA_HTTP_MIN_TIMEOUT_MS, 400 - OLLAMA_HTTP_READ_RESERVE_MS)
    assert _effective_job_timeout_seconds(1) == pytest.approx(0.3, abs=0.0001)


# --------------------------------------------------------------------------- #
# §36 — timeout derivation is deterministic and closed
# --------------------------------------------------------------------------- #


def test_derivations_are_total_and_monotonic():
    for ms in range(300, 10_000, 97):
        assert _effective_timeout_ms(ms) >= OLLAMA_HTTP_MIN_TIMEOUT_MS
        assert _effective_timeout_ms(ms) <= ms
        assert _effective_job_timeout_seconds(ms) >= 0.3
        assert _effective_job_timeout_seconds(ms) <= ms / 1000.0 + 1e-9
    # the inner read is always strictly short enough for the outer wait window.
    assert _effective_timeout_ms(10_000) < 10_000


# --------------------------------------------------------------------------- #
# §39 — the timeout firing is classified with the closed machine-readable
# vocabulary (never raw exception text)
# --------------------------------------------------------------------------- #


def test_bridge_job_timeout_classified_closed_source(caplog):
    """Outer-wait timeout (the server budget expires without any local reply)
    -> LOCAL_PROVIDER_TIMEOUT + ``timeoutSource=BRIDGE_JOB_TIMEOUT``.

    A sleeping stub stands in for a local Ollama that never answers within the
    server budget; the bridge's outer ``asyncio.wait_for`` fires FIRST (there
    is no inner HTTP-read to beat it), which is exactly the configuration the
    BRIDGE_JOB_TIMEOUT source documents.
    """
    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        # outer wait = 1000ms; the stub never answers -> the BRIDGE outer
        # asyncio.wait_for fires (no inner HTTP read can beat it).
        await conn.send(job_frame(job_id=JOB, timeout_ms=1_000))
        result = await conn.recv_json(timeout=5.0)
        assert result["jobId"] == JOB
        assert result["status"] == "FAILED"
        assert result["failureCode"] == "LOCAL_PROVIDER_TIMEOUT"

    captured = asyncio.run(
        _run_harness(
            scenario,
            stub_ollama=_NeverReplies(),
            config_kwargs={"connect_timeout_seconds": 1.0},
        )
    )
    # Closed machine-readable timeout source; never raw exception text.
    assert _has_logged(captured, "LOCAL_PROVIDER_TIMEOUT", "BRIDGE_JOB_TIMEOUT")


def test_ollama_http_timeout_classified_closed_source(caplog):
    """Inner LOCAL Ollama HTTP/body-read timeout fires first (the documented
    single reserve guarantees delivery before the outer wait) ->
    ``timeoutSource=OLLAMA_HTTP_TIMEOUT``."""
    mock = MockOllama(hang=True)

    async def scenario(conn, server):
        await conn.recv_json()
        await conn.send(pairing_accepted_with_token(token=TEST_TOKEN))
        # inner HTTP read = 1000 - 250 = 750ms; outer wait = 1.0s. The inner
        # fires first (single documented reserve).
        await conn.send(job_frame(job_id=JOB, timeout_ms=1_000))
        result = await conn.recv_json(timeout=5.0)
        assert result["jobId"] == JOB
        assert result["status"] == "FAILED"
        assert result["failureCode"] == "LOCAL_PROVIDER_TIMEOUT"
        assert mock.cancelled >= 1

    captured = asyncio.run(
        _run_harness(
            scenario,
            mock=mock,
            config_kwargs={"connect_timeout_seconds": 1.0},
        )
    )
    assert _has_logged(captured, "LOCAL_PROVIDER_TIMEOUT", "OLLAMA_HTTP_TIMEOUT")


def test_ollama_client_raises_typed_provider_timeout_only():
    """The local HTTP read NEVER surfaces a raw TimeoutError — the typed
    ``ProviderTimeout`` carrier (failure_code=LOCAL_PROVIDER_TIMEOUT) is the
    only exception class, so no exception text can reach the bridge."""

    async def run() -> None:
        client = make_ollama(MockOllama(hang=True))
        try:
            with pytest.raises(ProviderTimeout) as excinfo:
                await client.run_structured_inference(
                    prompt="p", temperature=0.1, timeout_ms=400
                )
            exc = excinfo.value
            assert exc.failure_code == "LOCAL_PROVIDER_TIMEOUT"
            assert "timer" not in str(exc).lower()
        finally:
            await client.aclose()

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


async def _run_harness(scenario, *, mock=None, stub_ollama=None, config_kwargs) -> list[str]:
    server = FakeBridgeServer(scenario)
    await server.start()
    config = make_config(server_url=server.server_url, **(config_kwargs or {}))
    ollama = (
        stub_ollama
        if stub_ollama is not None
        else make_ollama(mock, connect_timeout=1.0)
    )
    bridge = BridgeClient(
        config=config,
        token_store=make_token_store(),
        ollama=ollama,
        logger=logging.getLogger("pd-ollama-bridge"),
    )
    # A direct capture handler on the bridge logger (deterministic across the
    # async bridge task; pytest caplog cannot always observe records emitted
    # inside the bridge's own task context).
    class _Capture(logging.Handler):
        def __init__(self, sink):
            super().__init__()
            self._sink = sink

        def emit(self, record):
            self._sink.append(record.getMessage())

    captured: list[str] = []
    logger = logging.getLogger("pd-ollama-bridge")
    handler = _Capture(captured)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    bridge_task = asyncio.create_task(bridge.run())
    try:
        # First wait for the connection to arrive (wait_idle alone is NOT a
        # barrier for a not-yet-started connection), then for the whole job.
        assert await server.wait_until(
            lambda: server.conn_count >= 1, timeout=5.0
        ), "the bridge must establish one connection"
        await server.wait_idle(timeout=8.0)
        assert server.errors == []
    finally:
        await _stop_bridge(bridge_task)
        await server.aclose()
        if not isinstance(ollama, _NeverReplies):
            await ollama.aclose()
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
    return captured


def _has_logged(captured: list[str], failure_code: str, source: str) -> bool:
    for message in captured:
        if (
            failure_code in message
            and f"timeoutSource={source}" in message
        ):
            return True
    return False


def _has_logged(captured: list[str], failure_code: str, source: str) -> bool:
    for message in captured:
        if (
            failure_code in message
            and f"timeoutSource={source}" in message
        ):
            return True
    return False
