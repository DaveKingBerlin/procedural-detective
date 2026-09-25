"""Shared test fixtures/helpers for the hermetic bridge tests.

No network is ever touched: the fake WSS server binds to 127.0.0.1 on an
ephemeral port inside the test's asyncio loop, and MockOllama is an httpx
ASGITransport (pure in-process). Tests own their event loop via asyncio.run,
so pytest-asyncio is not required.
"""

from __future__ import annotations

import httpx
import pytest

from pd_ollama_bridge.config import Config, TokenStore
from pd_ollama_bridge.ollama_client import OllamaClient
from pd_ollama_bridge.bridge_client import BridgeClient
from pd_ollama_bridge import protocol

from mock_ollama import MockOllama
from fake_server import FakeBridgeServer

TEST_TOKEN = "BRIDGE_TOKEN_FOR_TESTING_000001"
TEST_SESSION_ID = "PS-test-session-000000"
TEST_PAIRING_CODE = "PD-A2B3-C4D5"
TEST_SCHEMA = "ASSET_SPEC_v1"


@pytest.fixture(autouse=True)
def _hermetic_event_loops():
    yield


def make_token_store(path=None):
    return TokenStore(path)


def make_ollama(mock: MockOllama, *, connect_timeout: float = 2.0) -> OllamaClient:
    transport = httpx.ASGITransport(app=mock.app)
    http = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:11434")
    return OllamaClient(
        base_url="http://127.0.0.1:11434",
        model="hermes3:8b",
        connect_timeout_seconds=connect_timeout,
        http_client=http,
    )


def make_config(
    *,
    pairing_code=TEST_PAIRING_CODE,
    server_url=None,
    token_file=None,
    max_reconnect_attempts=0,
    idle_timeout_seconds=10.0,
    connect_timeout_seconds=2.0,
    reconnect_backoff_base_seconds=0.01,
    reconnect_backoff_cap_seconds=0.05,
    reconnect_reset_seconds=60.0,
) -> Config:
    return Config(
        pairing_code=pairing_code,
        server_url=server_url or "http://127.0.0.1:0",
        ollama_url="http://127.0.0.1:11434",
        model="hermes3:8b",
        connect_timeout_seconds=connect_timeout_seconds,
        token_file=token_file,
        idle_timeout_seconds=idle_timeout_seconds,
        reconnect_backoff_base_seconds=reconnect_backoff_base_seconds,
        reconnect_backoff_cap_seconds=reconnect_backoff_cap_seconds,
        reconnect_reset_seconds=reconnect_reset_seconds,
        max_reconnect_attempts=max_reconnect_attempts,
    ).validate()


def make_bridge(config: Config, token_store: TokenStore, ollama: OllamaClient):
    events: dict[str, list] = {"connected": [], "reconnecting": []}

    def on_connected(kind: str, host: str) -> None:
        events["connected"].append((kind, host))

    def on_reconnecting(attempt: int, delay: float) -> None:
        events["reconnecting"].append((attempt, delay))

    bridge = BridgeClient(
        config=config,
        token_store=token_store,
        ollama=ollama,
        on_connected=on_connected,
        on_reconnecting=on_reconnecting,
    )
    return bridge, events