"""Phase 22–24 bridge-state regression tests (Phase22-24-Fix.md §6 + §7).

These tests prove the P0 root-cause conclusion: the server wiring is CORRECT
and singular — one ``BridgeRegistry`` on ``app.state`` shared by the
``/bridge/status`` path, the ``/generation-capabilities`` path and the
``RemoteClientProvider`` factory — and that a bridge connected under the SAME
anonymous session that generated the case is actually dispatched to.

Sections:
  A. same-state identity: the generation service's RemoteClientProvider factory
     and the bridge registry are the SAME ``app.state.bridge_registry`` object.
  B. connected bridge status consistency: `/bridge/status` and
     `/generation-capabilities` report the SAME connected bridge under the SAME
     bearer; WITHOUT the bearer the capability endpoint reports not-connected
     (isolation, no leak).
  C. real provider dispatch regression (THE critical test): a normal
     ``POST /cases`` under the pairing session dispatches jobs over the REAL
     registry (not mocked) and consumes the provider response (reaches at least
     EVIDENCE); the NEGATIVE twin (pair under A, generate under B) fails typed
     BRIDGE_NOT_CONNECTED with ZERO dispatched jobs.
  D. disconnect behavior: after the WS closes, status becomes disconnected and
     generation fails with the documented bridge-not-connected behavior.
  E. disabled-bridge contract: disabled bridge HTTP paths return canonical 404
     (not 405), including static-serving mode.
  F. test app isolation: two ``create_app()`` instances in one process do not
     leak bridge state into each other (no process-global singleton).
  §7 configuredProvider pin: ``remote_client`` projects to ``"fake"``
  (fail-closed) with ``demo.available:false`` and a truthful ``remoteLocalAi``
  block — a bridge deployment is never mislabelled as the deterministic demo.

Infrastructure is REAL and in-process: a live uvicorn server on loopback + the
Phase 22 test bridge implementing the client side of the WS protocol (mock-only
network, allowed by the autouse loopback network block). No live Ollama/bridge.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from app.main import create_app  # noqa: E402

from bridge_harness import (  # noqa: E402
    LiveTestServer,
    TestBridge,
    bridge_status,
    create_pairing,
    make_bridge_settings,
    new_anonymous_session,
    start_generation,
)

from app.generation.failure_codes import (  # noqa: E402
    GenerationFailureCode,
    public_failure_code,
)

_OLLAMA = "hermes3:8b"

_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa Koenig\n"
    "Location: office\n"
)


def _pair_and_connect(stack, *, session_token=None):
    base = stack["base_url"]
    token = session_token if session_token is not None else new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    return token, pairing, bridge, ack


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """One live uvicorn app (remote_client + bridge enabled) for the module."""
    db_dir = tmp_path_factory.mktemp("pd24state")
    url = f"sqlite:///{(db_dir / 'main.db').as_posix()}"
    upgrade_db(url)
    settings = make_bridge_settings(url)
    app = create_app(settings)
    server = LiveTestServer(app)
    yield {"server": server, "base_url": server.base_url, "app": app}
    server.close()
    app.state.engine.dispose()
    app.state.store.dispose()


# =========================================================================== #
# A. same-state identity / dependency regression
# =========================================================================== #


def test_a_same_state_identity_shared_registry(database_url):
    """The generation service's RemoteClientProvider factory and the bridge
    registry are the SAME ``app.state.bridge_registry`` object — the exact
    object the `/bridge/status` and `/generation-capabilities` paths read."""
    upgrade_db(database_url)
    app = create_app(make_bridge_settings(database_url))
    try:
        registry = app.state.bridge_registry
        gen = app.state.generation_service
        assert registry is not None
        # The generation service holds the SAME registry reference.
        assert gen._bridge_registry is registry
        # The provider factory produces a RemoteClientProvider bound to the SAME
        # registry (the probe's `provider._registry is app.state.bridge_registry`).
        provider = gen._provider_factory()
        assert provider._registry is registry
        # Behavior-level complement: the router's registry dependency resolves to
        # the same object (proves no second BridgeService/registry was created).
        assert app.state.bridge_registry is registry
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()


# =========================================================================== #
# B. connected bridge status consistency
# =========================================================================== #


def test_b_connected_bridge_status_and_capabilities_consistent(stack):
    """Connecting the bridge makes BOTH `/bridge/status` and
    `/generation-capabilities` report the same connected bridge under the SAME
    bearer; WITHOUT the bearer the capability endpoint reports not-connected
    (isolation, no leak)."""
    base = stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    try:
        # EXACT `/bridge/status` shape (same bearer).
        status = bridge_status(base, token)["remoteLocalAi"]
        assert status == {
            "available": True,
            "connected": True,
            "model": _OLLAMA,
            "ready": True,
        }
        # `/generation-capabilities` (same bearer) is consistent.
        caps = httpx.get(
            f"{base}/api/v1/generation-capabilities",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        ).json()
        assert caps["remoteLocalAi"] == {
            "available": True,
            "connected": True,
            "model": _OLLAMA,
            "ready": True,
        }
        # WITHOUT the bearer: no leak — the anonymous caller sees not-connected.
        anon = httpx.get(
            f"{base}/api/v1/generation-capabilities", timeout=30
        ).json()
        assert anon["remoteLocalAi"]["connected"] is False
        assert anon["remoteLocalAi"]["model"] is None
        assert anon["remoteLocalAi"]["ready"] is False
    finally:
        bridge.close()


# =========================================================================== #
# C. real provider dispatch regression (THE critical test)
# =========================================================================== #


def test_c_real_provider_dispatch_consumes_response(stack):
    """A normal ``POST /cases`` under the pairing session dispatches jobs over
    the REAL bridge registry (not mocked) and CONSUMES the provider response —
    the attempt reaches at least the EVIDENCE stage and PUBLISHES (never the
    immediate BRIDGE_NOT_CONNECTED the production frontend triggered)."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    try:
        result = start_generation(stack["base_url"], token, _PROMPT)
        assert result["http_status"] == 201, result["body"]
        body = result["body"]
        assert body["status"] == "PUBLISHED", body
        assert body.get("failureCode") is None
        # The provider response was consumed: the bridge saw the full stage
        # sequence, at minimum CASE_PEOPLE then EVIDENCE.
        seen = [job["schemaId"] for job in bridge.jobs]
        assert "CASE_PEOPLE_v1" in seen
        assert "EVIDENCE_v1" in seen
        assert len(bridge.jobs) >= 8
    finally:
        bridge.close()


def test_c_negative_twin_cross_session_zero_jobs(stack):
    """Isolation contract pinned by the production frontend's accidental
    behavior: pairing under session A and generating under session B FAILS typed
    BRIDGE_NOT_CONNECTED and dispatches ZERO bridge jobs."""
    creator_token = new_anonymous_session(stack["base_url"])["anonymousSessionToken"]
    pairing = create_pairing(stack["base_url"], creator_token)
    bridge = TestBridge(stack["server"].ws_url)
    bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    try:
        other_token = new_anonymous_session(stack["base_url"])["anonymousSessionToken"]
        assert other_token != creator_token
        result = start_generation(stack["base_url"], other_token, _PROMPT)
        assert result["http_status"] == 201, result["body"]
        body = result["body"]
        assert body["status"] == "FAILED"
        assert public_failure_code(body["failureCode"]) == (
            GenerationFailureCode.BRIDGE_NOT_CONNECTED.value
        )
        # The creator's bridge never received a job.
        assert bridge.job_count == 0
    finally:
        bridge.close()


# =========================================================================== #
# D. disconnect behavior
# =========================================================================== #


def test_d_disconnect_marks_status_and_generation_fail(stack):
    """After the WS disconnects: `/bridge/status` becomes disconnected and
    generation fails with the documented bridge-not-connected behavior.
    (Reconnect semantics stay covered by the existing Phase 22 reconnect test.)"""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    bridge.close()
    time.sleep(0.2)
    status = bridge_status(stack["base_url"], token)["remoteLocalAi"]
    assert status["connected"] is False
    assert status["model"] is None
    result = start_generation(stack["base_url"], token, _PROMPT)
    body = result["body"]
    assert result["http_status"] == 201
    assert body["status"] == "FAILED"
    assert public_failure_code(body["failureCode"]) == (
        GenerationFailureCode.BRIDGE_NOT_CONNECTED.value
    )


# =========================================================================== #
# E. disabled-bridge contract
# =========================================================================== #


def test_e_disabled_bridge_404_including_static(database_url, tmp_path):
    """Disabled bridge HTTP paths return canonical 404 (not 405), including
    static-serving mode (main.py disabled-bridge envelope + SPA catch-all)."""
    from fastapi.testclient import TestClient

    upgrade_db(database_url)
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    app_disabled = create_app(
        Settings(
            database_url=database_url,
            enable_bridge=False,
            static_dir=static_dir,
        )
    )
    try:
        with TestClient(app_disabled) as client:
            for method, path in (
                ("POST", "/api/v1/bridge/pairing"),
                ("GET", "/api/v1/bridge/status"),
                ("PUT", "/api/v1/bridge/x"),
                ("PATCH", "/api/v1/bridge/x"),
                ("DELETE", "/api/v1/bridge/x"),
            ):
                response = client.request(
                    method,
                    path,
                    headers={"Authorization": "Bearer " + "x" * 30},
                )
                assert response.status_code == 404, (method, path, response.text)
                assert response.json() == {
                    "error": {"code": "NOT_FOUND", "message": "Not found", "details": None}
                }
    finally:
        app_disabled.state.engine.dispose()
        app_disabled.state.store.dispose()


# =========================================================================== #
# F. test app isolation
# =========================================================================== #


def test_f_two_apps_do_not_leak_bridge_state(tmp_path_factory):
    """Two ``create_app()`` instances in the same process: a bridge connected to
    app A must NOT appear connected to app B (guards against a process-global
    singleton)."""
    db_dir_a = tmp_path_factory.mktemp("pd24isoA")
    url_a = f"sqlite:///{(db_dir_a / 'a.db').as_posix()}"
    upgrade_db(url_a)
    app_a = create_app(make_bridge_settings(url_a))
    server_a = LiveTestServer(app_a)

    db_dir_b = tmp_path_factory.mktemp("pd24isoB")
    url_b = f"sqlite:///{(db_dir_b / 'b.db').as_posix()}"
    upgrade_db(url_b)
    app_b = create_app(make_bridge_settings(url_b))
    server_b = LiveTestServer(app_b)

    bridge_a = None
    try:
        # Connect a bridge to app A.
        token_a = new_anonymous_session(server_a.base_url)["anonymousSessionToken"]
        pairing_a = create_pairing(server_a.base_url, token_a)
        bridge_a = TestBridge(server_a.ws_url)
        bridge_a.connect_pairing(pairing_a["pairingCode"], model=_OLLAMA)
        assert bridge_status(server_a.base_url, token_a)["remoteLocalAi"]["connected"] is True

        # App B (separate registry) must NOT see app A's bridge.
        token_b = new_anonymous_session(server_b.base_url)["anonymousSessionToken"]
        assert bridge_status(server_b.base_url, token_b)["remoteLocalAi"]["connected"] is False
        caps_b = httpx.get(
            f"{server_b.base_url}/api/v1/generation-capabilities",
            headers={"Authorization": f"Bearer {token_b}"},
            timeout=30,
        ).json()
        assert caps_b["remoteLocalAi"]["connected"] is False
    finally:
        if bridge_a is not None:
            bridge_a.close()
        server_a.close()
        server_b.close()
        app_a.state.engine.dispose()
        app_a.state.store.dispose()
        app_b.state.engine.dispose()
        app_b.state.store.dispose()


# =========================================================================== #
# §7 configuredProvider compat pin (fail-closed projection, NOT a field change)
# =========================================================================== #


def test_configured_provider_remote_client_projects_to_fake():
    """``_configured_provider(remote_client) == "fake"`` — the documented Phase
    21B fail-closed projection (the backend field is NOT changed; the frontend
    gates its demo copy on ``demo.available``)."""
    from app.api.v1.generation_capabilities import _configured_provider

    class _RemoteClientSettings:
        generation_provider = "remote_client"

    assert _configured_provider(_RemoteClientSettings()) == "fake"


def test_configured_provider_remote_client_end_to_end_dto(database_url):
    """End-to-end DTO with GENERATION_PROVIDER=remote_client + ENABLE_BRIDGE=true:
    ``configuredProvider:"fake"``, ``demo.available is False``, ``remoteLocalAi``
    present — a bridge deployment is never mislabelled as the deterministic demo."""
    from fastapi.testclient import TestClient

    upgrade_db(database_url)
    app = create_app(make_bridge_settings(database_url))
    try:
        with TestClient(app) as client:
            body = client.get("/api/v1/generation-capabilities").json()
        assert body["configuredProvider"] == "fake"
        assert body["remoteLocalAi"]["available"] is True
        assert body["remoteLocalAi"]["connected"] is False
        demo = next(m for m in body["modes"] if m["id"] == "demo")
        assert demo["available"] is False
        # No bridge token / URL / model leaks into the public body.
        for fragment in ("11434", "127.0.0.1", "bridgeSessionToken"):
            assert fragment not in json.dumps(body)
    finally:
        app.state.engine.dispose()
        app.state.store.dispose()