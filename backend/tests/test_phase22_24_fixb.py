"""Phase 22–24 Fix B — bridge result delivery / reconnect lifecycle regression.

The P0 defect (anonymous session mismatch) was fixed by Fix A. Fix B addresses
the NEXT lifecycle defect proven during the real two-machine run:

    job reaches Ollama -> Ollama completes (~52s frame-silent) -> WS closes
    -> backend fails the job BRIDGE_DISCONNECTED -> the same running CLI's
    memory-only bridge session token is REJECTED on reconnect.

Root cause (proven, ``Phase22-24-FixB.md`` H3):

- ``BridgeRegistry.detach`` only mutated the in-memory ``BridgeConnection``
  and never persisted the disconnect; the DB ``bridge_sessions`` row stayed
  ``CONNECTED`` with a STALE ``last_seen`` (``mark_seen`` only ran on inbound
  frames and a long silent local job froze it).
- Reconnect auth (``BridgePairingService.authenticate_bridge_token``)
  anchors the grace to that STALE ``last_seen``, so a silent job + ~1 s
  backoff easily exceeded the grace and the documented same-process
  memory-only reconnect (``--max-retries``; cli.py) was rejected.

Infrastructure is REAL and in-process: live uvicorn servers on loopback + the
Phase 22 test bridge (``bridge_harness.TestBridge``) implementing the client
side of the WS protocol. NO live Ollama / bridge / network.

Sections (each Bx maps 1:1 to Phase22-24-FixB.md §10):

  B1  first-job result delivery keeps the socket usable
  B2  three sequential generations on ONE pairing (one socket, correct ids,
      no pending-future leak, no cross-job confusion)
  B3  after job N is accepted the bridge is still connected and dispatches
      job N+1
  B4  THE defining regression: transient reconnect with the SAME running
      memory-only token AFTER a long FRAME-SILENT local job. FAILS on the old
      behavior (stale last_seen -> grace exceeded -> rejected); passes after
      detach persists the drop timestamp.
  B4b mechanism pin: detach persists DISCONNECTED + last_seen = drop moment
      (registry/store level, deterministic)
  B5  pairing code remains single-use; the reconnect uses the post-pairing
      bridge session credential
  B6  cross-session isolation intact (Fix A negative test stays green)
  B7  disconnect during an active job -> typed BRIDGE_DISCONNECTED; reconnect
      semantics verified independently afterwards
  B8  malformed result / wrong-jobId result -> protocol error, no truth leak,
      no crash, no accidental success (adds the WS-level wrong-jobId case)
  B9  long-running local job with a HEALTHY event loop (pongs) does NOT drop
      the WebSocket (refutes H4 for the shipped path; no timeout inflation)
  B10 real RemoteClientProvider end-to-end (same anonymous scope pair)
"""

from __future__ import annotations

import asyncio
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
from app.auth.tokens import verifier  # noqa: E402

from bridge_harness import (  # noqa: E402
    LiveTestServer,
    TestBridge,
    bridge_status,
    create_pairing,
    make_bridge_settings,
    new_anonymous_session,
    start_generation,
)

from app.generation.bridge_protocol import (  # noqa: E402
    CLOSE_POLICY_VIOLATION,
    CLOSE_PROTOCOL_ERROR,
    PROTOCOL_VERSION,
)
from app.generation.failure_codes import (  # noqa: E402
    GenerationFailureCode,
    public_failure_code,
)
from app.generation.provider import (  # noqa: E402
    GenerateRequest,
    GenerationStage,
)
from app.generation.remote_client_provider import RemoteClientProvider  # noqa: E402
from app.models.bridge import (  # noqa: E402
    BRIDGE_STATE_CONNECTED,
    BRIDGE_STATE_DISCONNECTED,
)

_OLLAMA = "hermes3:8b"

_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa Koenig\n"
    "Location: office\n"
)

# B4 — the defining regression: a local job long enough that NO inbound frame
# is processed while it runs (server heartbeat disabled -> last_seen freezes).
# The harness's ``TestBridge.close()`` includes a ~3s client thread-join, so
# the drop->reconnect window is ~3.3s: the grace (5s) covers the POST-fix
# reconnect, while the STALE last_seen (job start) still EXCEEDS it pre-fix by
# a comfortable margin (drop at 5s + 3.3s window >> 5s).
_B4_SILENT_JOB_SECONDS = 20.0  # "Ollama" work; the result is NEVER accepted
_B4_DROP_DELAY_SECONDS = 5.0

# B9 — the local job outlasts the server's heartbeat (1s) while the client
# keeps the event loop healthy (pongs), proving the shipped path never blocks.
_B9_LONG_JOB_SECONDS = 1.6


def _full_stack(tmp_path_factory, *, tag: str, **overrides):
    db_dir = tmp_path_factory.mktemp(f"bridge_fixb_{tag}")
    url = f"sqlite:///{(db_dir / f'{tag}.db').as_posix()}"
    upgrade_db(url)
    settings = make_bridge_settings(url, **overrides)
    app = create_app(settings)
    server = LiveTestServer(app)
    return {"server": server, "url": url, "base_url": server.base_url}, app


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    ctx, app = _full_stack(tmp_path_factory, tag="main")
    yield ctx
    ctx["server"].close()
    app.state.engine.dispose()
    app.state.store.dispose()


@pytest.fixture(scope="module")
def reconnect_stack(tmp_path_factory):
    # Heartbeat (120s) can NEVER fire during the 7s frame-silent job and idle
    # (3600s) never closes it: no inbound frame is processed while the local
    # work runs, so ``last_seen`` freezes. Grace = 5s: the STALE last_seen
    # deterministically exceeds it on the old behavior (7s job + ~3.3s
    # drop->reconnect window >> 5s) while the drop-anchored last_seen stays
    # comfortably inside it after the fix. (Settings requires heartbeat < idle.)
    ctx, app = _full_stack(
        tmp_path_factory,
        tag="reconnect",
        bridge_reconnect_grace_seconds=5.0,
        bridge_heartbeat_interval_seconds=120.0,
        bridge_idle_timeout_seconds=3600.0,
    )
    yield ctx
    ctx["server"].close()
    app.state.engine.dispose()
    app.state.store.dispose()


@pytest.fixture(scope="module")
def liveness_stack(tmp_path_factory):
    # A SHORT heartbeat so the server pings during the long job; the client
    # keeps the loop healthy (pongs). Idle bound stays generous so only a
    # genuinely blocked loop could trip it.
    ctx, app = _full_stack(
        tmp_path_factory,
        tag="liveness",
        bridge_heartbeat_interval_seconds=1.0,
        bridge_idle_timeout_seconds=30.0,
        bridge_max_frames_per_window=100,
    )
    yield ctx
    ctx["server"].close()
    app.state.engine.dispose()
    app.state.store.dispose()


def _pair_and_connect(stack, *, model=_OLLAMA, session_token=None):
    base = stack["base_url"]
    token = (
        session_token
        if session_token is not None
        else new_anonymous_session(base)["anonymousSessionToken"]
    )
    pairing = create_pairing(base, token)
    bridge = TestBridge(stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=model)
    return token, pairing, bridge, ack


def _generate(stack, token, prompt=_PROMPT, *, timeout=60):
    return httpx.post(
        f"{stack['base_url']}/api/v1/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": prompt},
        timeout=timeout,
    )


def _scope_for(stack, token: str) -> str:
    return stack["server"].app.state.store.get_session_by_verifier(
        verifier(token)
    ).session_id


def _conn_for(stack, token: str):
    return stack["server"].app.state.bridge_registry.lookup_for_scope(
        _scope_for(stack, token)
    )


# =========================================================================== #
# B1 — first-job result delivery keeps the socket usable
# =========================================================================== #


def test_b1_first_job_result_delivery_keeps_socket(stack):
    """Same-session pair -> dispatch -> the bridge answers every job -> the
    provider futures resolve SUCCESS -> the socket NEVER closes from successful
    result handling and stays usable for the next job."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    try:
        result = _generate(stack, token)
        assert result.status_code == 201, result.text
        body = result.json()
        assert body["status"] == "PUBLISHED", body  # provider future resolved success
        assert body.get("failureCode") is None  # never BRIDGE_DISCONNECTED
        assert bridge.job_count == 8
        # No SOCKET_CLOSED caused by successful result handling.
        assert bridge.close_code is None, bridge.close_code
        status = bridge_status(stack["base_url"], token)["remoteLocalAi"]
        assert status["connected"] is True and status["ready"] is True
        # The socket is usable for a follow-up job.
        result2 = _generate(stack, token)
        assert result2.json()["status"] == "PUBLISHED"
        assert bridge.job_count == 16
        assert bridge.close_code is None
    finally:
        bridge.close()


# =========================================================================== #
# B2 — multiple sequential jobs on one pairing
# =========================================================================== #


def test_b2_three_sequential_generations_one_pairing(stack):
    """job1/job2/job3 success: the SAME logical session, the SAME registry
    binding (no re-pair, no new generation), correct distinct job ids, no
    cross-job confusion and no pending-future leak."""
    token, _pairing, bridge, ack = _pair_and_connect(stack)
    session_id = ack["bridgeSessionId"]
    registry = stack["server"].app.state.bridge_registry
    scope = _scope_for(stack, token)
    first_conn = registry.lookup_for_scope(scope)
    assert first_conn is not None
    try:
        case_ids = []
        seen_jobs: set[str] = set()
        for _generation in range(3):
            result = _generate(stack, token, f"{_PROMPT} generation {_generation}")
            assert result.status_code == 201, result.text
            body = result.json()
            assert body["status"] == "PUBLISHED", body
            case_ids.append(body["caseId"])
            assert bridge.close_code is None, bridge.close_code
            conn = registry.lookup_for_scope(scope)
            assert conn is first_conn  # SAME socket / binding — never re-paired
            assert conn.bridge_session_id == session_id
            with conn._job_lock:
                assert conn.current_job_id is None  # no pending-future leak
                assert conn.current_waiter is None
            for job in bridge.jobs:
                seen_jobs.add(job["jobId"])
        assert len(set(case_ids)) == 3  # three distinct published cases
        assert bridge.job_count == 24  # 3 x 8 authoritative-stage jobs
        assert len(seen_jobs) == 24  # every dispatched job id is distinct
    finally:
        bridge.close()


# =========================================================================== #
# B3 — accepted result keeps the bridge connected; job N+1 dispatches
# =========================================================================== #


def test_b3_bridge_connected_after_result_then_next_job(stack):
    """Explicit pin: after job N's result is accepted the server still reports
    the bridge connected == True and immediately dispatches job N+1 over the
    SAME socket."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    try:
        for _ in range(2):  # N and N+1 on the same live socket
            status = bridge_status(stack["base_url"], token)["remoteLocalAi"]
            assert status["connected"] is True, status
            assert status["ready"] is True, status
            result = _generate(stack, token)
            assert result.json()["status"] == "PUBLISHED"
        status = bridge_status(stack["base_url"], token)["remoteLocalAi"]
        assert status["connected"] is True and status["ready"] is True
        assert bridge.close_code is None
    finally:
        bridge.close()


# =========================================================================== #
# B4 — THE DEFINING regression: same-token reconnect after a long silent job
# =========================================================================== #


def _frame_silent_responder(job, b):
    """The local "Ollama" work runs as a sibling task and takes LONGER than the
    reconnect grace — while it runs, ZERO inbound frames return to the server
    (the result is never accepted before the transport drops). Mirrors the real
    ~52 s generation run. The reader loop stays free, exactly like the shipped
    client's ``asyncio.create_task(self._run_job(...))`` (strong task
    reference included)."""

    async def _work():
        await asyncio.sleep(_B4_SILENT_JOB_SECONDS)
        b.mini.reply_success(job, b)

    b._silent_worker = asyncio.ensure_future(_work())  # strong ref (never GC'd)


def _drop_bridge_after(bridge, delay: float) -> None:
    """Close the bridge WebSocket ``delay`` seconds from now (thread target)."""
    time.sleep(delay)
    bridge.close()


def test_b4_transient_reconnect_after_long_framesilent_job(reconnect_stack):
    """Pair -> dispatch a job whose local work keeps the socket frame-silent
    LONGER than the reconnect grace -> the transport drops MID-JOB (the result
    is NEVER accepted: the job fails typed BRIDGE_DISCONNECTED) -> the SAME
    running process reconnects with the SAME memory-only bridge session token.

    FAILS on the old behavior: the DB ``last_seen`` stays frozen at the job
    start (mark_seen only runs on inbound frames), ``(now - last_seen) >
    BRIDGE_RECONNECT_GRACE_SECONDS`` and the handshake closes 1008 (the CLI's
    "bridge session rejected or expired"). Passes after the fix: ``detach``
    persists the DISCONNECTED state + the DROP timestamp so the grace anchors
    to the actual transport drop instead of the stale last frame.
    """
    import threading

    from app.generation.provider import StageDriverProviderFailure

    base = reconnect_stack["base_url"]
    app = reconnect_stack["server"].app
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(reconnect_stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    secret = ack["bridgeSessionToken"]  # memory-only (never persisted to disk)
    session_id = ack["bridgeSessionId"]
    scope = _scope_for(reconnect_stack, token)
    try:
        bridge.mini.on_job = _frame_silent_responder
        provider = RemoteClientProvider(
            registry=app.state.bridge_registry,
            settings=app.state.settings,
            session_scope=scope,
        )
        drop_thread = threading.Thread(
            target=_drop_bridge_after,
            args=(bridge, _B4_DROP_DELAY_SECONDS),
            daemon=True,
        )
        drop_started = time.time()
        drop_thread.start()
        try:
            provider.generate(
                GenerateRequest(
                    attempt_id="ATT-fixb-b4",
                    stage=GenerationStage.CASE_TRUTH,
                    prompt_context=_PROMPT,
                    timeout_seconds=25.0,
                )
            )
        except StageDriverProviderFailure as exc:
            # Exactly the production outcome: the frame-silent job was failed
            # server-side when the socket died — its result was never accepted.
            assert public_failure_code(str(exc.code)) == (
                GenerationFailureCode.BRIDGE_DISCONNECTED.value
            )
        else:
            raise AssertionError("expected BRIDGE_DISCONNECTED for the mid-job drop")
        # Let the client-side teardown settle (the close handshake + join), then
        # reconnect with the SAME in-process token INSIDE the drop-anchored grace.
        drop_thread.join(timeout=10)
        assert time.time() - drop_started < 9.0  # sanity: bounded wall time

        bridge2 = TestBridge(reconnect_stack["server"].ws_url)
        ack2 = bridge2.connect_reconnect(secret)
        assert ack2["type"] == "pairing_accepted"
        assert ack2["bridgeSessionId"] == session_id
        assert "bridgeSessionToken" not in ack2  # issued exactly once at pairing
        # The reconnect landed INSIDE the 5s grace anchored to the drop (the
        # measured drop->reconnect window is ~3.3s).
        assert time.time() - drop_started < 5.0 + _B4_DROP_DELAY_SECONDS
        status = bridge_status(base, token)["remoteLocalAi"]
        assert status["connected"] is True and status["model"] == _OLLAMA
        # Future jobs continue over the reconnected socket (no new pairing).
        provider.bind_session_scope(scope)
        result2 = provider.generate(
            GenerateRequest(
                attempt_id="ATT-fixb-b4b",
                stage=GenerationStage.CASE_TRUTH,
                prompt_context=_PROMPT,
                timeout_seconds=20.0,
            )
        )
        assert result2.content is not None
        assert bridge2.job_count == 1  # the reconnected socket served the follow-up
        assert bridge2.close_code is None
        bridge2.close()
    finally:
        if bridge.close_code is None:
            bridge.close()


# =========================================================================== #
# B4b — mechanism pin: detach persists DISCONNECTED + the drop timestamp
# =========================================================================== #


def test_b4b_detach_persists_disconnect_and_anchors_grace(database_url):
    """Registry/store-level, deterministic: after ``detach`` the durable
    ``bridge_sessions`` row flips to DISCONNECTED and ``last_seen`` equals the
    DROP moment — so a reconnect inside BRIDGE_RECONNECT_GRACE_SECONDS from the
    drop is accepted even after a long frame-silent job, and a reconnect after
    the grace is still rejected. FAILS on the old behavior (row stayed
    CONNECTED with a frozen last_seen; in-grace auth rejected)."""
    upgrade_db(database_url)
    from app.persistence.store import Store
    from app.services.bridge import BridgePairingService, BridgeRegistry

    settings = Settings(database_url=database_url, bridge_reconnect_grace_seconds=5.0)
    store = Store(database_url)
    registry = BridgeRegistry(settings=settings, store=store)
    service = BridgePairingService(settings=settings, store=store)
    try:
        pairing = service.create_pairing("QUOTA-fixb-b4b")
        data, scope = service.consume_pairing(pairing.pairing_code)
        binding = service.bind_bridge_from_pairing(
            data, model=_OLLAMA, capabilities=("STRUCTURED_MODEL_INFERENCE",)
        )
        sid = binding.bridge_session_id
        now0 = float(time.time())
        conn = registry.bind(
            bridge_session_id=sid,
            session_scope=scope,
            model=_OLLAMA,
            capabilities=("STRUCTURED_MODEL_INFERENCE",),
            expires_at=now0 + 600.0,
            socket=None,
            loop=None,
            now=now0,
        )
        assert conn is not None
        row = store.get_bridge_session(sid)
        assert row is not None and row.connection_state == BRIDGE_STATE_CONNECTED
        assert abs(row.last_seen - now0) < 1.0

        # A long frame-silent job passes with NO inbound frames (last_seen
        # frozen at now0), then the transport drops at now0 + 6.0.
        drop_at = now0 + 6.0
        registry.detach(sid, now=drop_at)
        row = store.get_bridge_session(sid)
        assert row.connection_state == BRIDGE_STATE_DISCONNECTED
        assert abs(row.last_seen - drop_at) < 1e-6  # anchored to the DROP moment
        # Reconnect INSIDE the 5s grace from the drop is accepted.
        inside = service.authenticate_bridge_token(
            binding.bridge_session_token, now=drop_at + 3.0
        )
        assert inside is not None and inside.bridge_session_id == sid
        # Reconnect AFTER the grace (anchored to the drop) is still rejected.
        outside = service.authenticate_bridge_token(
            binding.bridge_session_token, now=drop_at + 5.5
        )
        assert outside is None
    finally:
        store.dispose()


# =========================================================================== #
# B4c — F3 pin: the durable disconnect write runs OUTSIDE the registry lock
# =========================================================================== #


def test_b4c_detach_store_write_after_unlock_uses_drop_ts_never_raises():
    """F3 (adversarial) — the best-effort durable disconnect write

    (a) is invoked with the CAPTURED drop timestamp (B4b already pins the
        effect through a real SQLite store; this is the invocation-level pin),
    (b) runs ONLY AFTER the registry lock is released — an operator-held
        SQLite writer lock must never block the event loop WHILE the registry
        RLock is held by another thread's lookup,
    (c) NEVER propagates: a store failure does not raise from ``detach`` and
        the in-memory disconnect record is kept either way.

    Code-reading proof the (b) assertion bites: with the pre-fix
    ``_persist_disconnect`` INSIDE ``with self._lock``, the store write is
    reached with the RLock still owned by this thread, so
    ``registry._lock._is_owned()`` is True and assertion (b) fails.
    """
    import types

    from app.services.bridge import BridgeRegistry

    now = float(time.time())
    settings = types.SimpleNamespace(
        bridge_max_registry_sessions=16,
        bridge_reconnect_grace_seconds=5.0,
    )
    recorded: list[tuple[str, float]] = []

    class RecordingStore:
        def set_bridge_session_disconnected(self, bridge_session_id, *, last_seen):
            # (b) the write must NOT run while the registry RLock is held (a
            # blocking SQLite write must not stall every registry lock).
            assert registry._lock._is_owned() is False
            recorded.append((bridge_session_id, float(last_seen)))

    registry = BridgeRegistry(settings=settings, store=RecordingStore())
    conn = registry.bind(
        bridge_session_id="SID-fixb-f3a",
        session_scope="QUOTA-fixb-f3a",
        model=_OLLAMA,
        capabilities=("STRUCTURED_MODEL_INFERENCE",),
        expires_at=now + 600.0,
        socket=None,
        loop=None,
        now=now,
    )
    drop_at = float(now + 4.0)
    assert registry.detach("SID-fixb-f3a", now=drop_at) is conn
    # (a) the store was invoked with the CAPTURED drop timestamp.
    assert recorded == [("SID-fixb-f3a", drop_at)]

    # (c) a store failure NEVER propagates from detach (best-effort write).
    class FailingStore:
        def set_bridge_session_disconnected(self, *args, **kwargs):
            raise RuntimeError("sqlite database is locked")

    registry._store = FailingStore()  # swap the durable singleton for the pin
    conn2 = registry.bind(
        bridge_session_id="SID-fixb-f3b",
        session_scope="QUOTA-fixb-f3b",
        model=_OLLAMA,
        capabilities=("STRUCTURED_MODEL_INFERENCE",),
        expires_at=now + 600.0,
        socket=None,
        loop=None,
        now=now + 0.5,
    )
    drop_at2 = drop_at + 1.0
    assert registry.detach("SID-fixb-f3b", now=drop_at2) is conn2  # no raise
    # The in-memory disconnect record is authoritative regardless of the store.
    assert conn2.disconnected_at == drop_at2
    assert conn2.last_seen == drop_at2


def test_b5_pairing_code_single_use_and_bridge_token_reconnect(stack):
    token, pairing, bridge, ack = _pair_and_connect(stack)
    secret = ack["bridgeSessionToken"]
    session_id = ack["bridgeSessionId"]
    try:
        # Original PD-XXXX-XXXX code can NOT be reused (already consumed).
        ws = _sync_connect(stack["server"].ws_url)
        ws.send(
            json.dumps(
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "type": "pairing_hello",
                    "pairingCode": pairing["pairingCode"],
                    "model": _OLLAMA,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        try:
            ws.recv(timeout=10)
            raise AssertionError("single-use pairing code was accepted twice")
        except AssertionError:
            raise
        except Exception as exc:  # noqa: BLE001 - expect the typed close
            assert (
                getattr(getattr(exc, "rcvd", None), "code", None)
                == CLOSE_POLICY_VIOLATION
            )
        finally:
            ws.close()
        # Reconnect uses the post-pairing BRIDGE SESSION credential (the code
        # is never re-enabled): reconnect accepted elsewhere within the grace.
        bridge2 = TestBridge(stack["server"].ws_url)
        ack2 = bridge2.connect_reconnect(secret)
        assert ack2["bridgeSessionId"] == session_id
        assert "bridgeSessionToken" not in ack2
        bridge2.close()
    finally:
        bridge.close()


# =========================================================================== #
# B6 — cross-session isolation intact (Fix A negative test stays green)
# =========================================================================== #


def test_b6_cross_session_isolation_intact(stack):
    """Anonymous session A owns the bridge; an unrelated session B still gets
    typed BRIDGE_NOT_CONNECTED and dispatches ZERO jobs to A's bridge."""
    base = stack["base_url"]
    creator_token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, creator_token)
    bridge = TestBridge(stack["server"].ws_url)
    bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    try:
        other_token = new_anonymous_session(base)["anonymousSessionToken"]
        assert other_token != creator_token
        result = _generate(stack, other_token)
        body = result.json()
        assert result.status_code == 201, body
        assert body["status"] == "FAILED"
        assert public_failure_code(body["failureCode"]) == (
            GenerationFailureCode.BRIDGE_NOT_CONNECTED.value
        )
        assert bridge.job_count == 0
    finally:
        bridge.close()


# =========================================================================== #
# B7 — disconnect during an active job: typed BRIDGE_DISCONNECTED; reconnect ok
# =========================================================================== #


def test_b7_disconnect_during_active_job_then_independent_reconnect(stack):
    token, _pairing, bridge, ack = _pair_and_connect(stack)
    secret = ack["bridgeSessionToken"]
    session_id = ack["bridgeSessionId"]

    def _drop_after_first(job, b):
        mini = b.mini
        # Answer the first job, then close the socket while the next job is on
        # its way -> the provider sees deterministic BRIDGE_DISCONNECTED.
        mini.reply_success(job, b)
        b.close()

    bridge.mini.on_job = _drop_after_first
    try:
        result = _generate(stack, token)
        body = result.json()
        assert result.status_code == 201, body
        assert body["status"] == "FAILED"
        assert public_failure_code(body["failureCode"]) == (
            GenerationFailureCode.BRIDGE_DISCONNECTED.value
        )
    finally:
        bridge.close()
    # Reconnect semantics INDEPENDENTLY afterwards (fresh, within grace).
    bridge2 = TestBridge(stack["server"].ws_url)
    ack2 = bridge2.connect_reconnect(secret)
    assert ack2["bridgeSessionId"] == session_id
    assert bridge_status(stack["base_url"], token)["remoteLocalAi"]["connected"] is True
    result2 = _generate(stack, token)
    assert result2.json()["status"] == "PUBLISHED"
    bridge2.close()


# =========================================================================== #
# B8 — malformed / wrong-jobId results: explicit error, no leak, no crash
# =========================================================================== #


def test_b8_malformed_and_wrong_jobid_results_no_leak_no_crash(stack):
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    try:
        # (a) WS-level wrong-jobId result (well-formed, references a job the
        # server never dispatched to THIS connection): explicitly DISCARDED
        # (bridge.job.discarded — zero state mutation), socket stays open, a
        # subsequent generation is unaffected (no accidental success/leak).
        bridge.send_frame(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "type": "job_result",
                "jobId": "JOB-does-not-exist-0001",
                "status": "SUCCESS",
                "structuredOutput": {"leak": True},
            }
        )
        time.sleep(0.3)
        assert bridge.close_code is None, bridge.close_code  # never closed
        result = _generate(stack, token)
        body = result.json()
        assert result.status_code == 201, body
        assert body["status"] == "PUBLISHED", body  # no accidental success/leak
        assert bridge.close_code is None

        # (b) A MALFORMED job_result (invalid status vocabulary) is an explicit
        # protocol error: typed close 1002, no crash, no truth leak.
        bridge.send_raw(
            json.dumps(
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "type": "job_result",
                    "jobId": "JOB-malformed-0002",
                    "status": "SOMETHING_ELSE",
                }
            )
        )
        assert bridge.wait_for(lambda: bridge.close_code is not None, timeout=10)
        assert bridge.close_code == CLOSE_PROTOCOL_ERROR
    finally:
        bridge.close()


def test_b8_wrong_jobid_never_resolves_pending_waiter(stack):
    """F1 (adversarial) — NON-VACUOUS cross-job pin for Phase22 §17/§33.

    The previous B8(a) sent the wrong jobId while NO waiter was pending, so a
    (mis)implementation that resolved the pending waiter by PRESENCE alone
    could never have been caught. This regression runs the REAL provider
    dispatch: while a REAL waiter for job X is PENDING on the socket, a
    WELL-FORMED ``job_result`` whose jobId is a foreign (unknown) id must

      (a) leave X's pending future UNRESOLVED (still pending),
      (b) be DISCARDED — no close, no crash, zero state mutation,
      (c) let the LATER GENUINE result for X resolve X (and only X) with the
          genuine payload (the wrong frame's payload is never applied).

    Code-reading proof it is non-vacuous: ``registry.resolve_job`` compares
    ``conn.current_job_id != job_id`` → the foreign frame returns False and is
    DISCARDED today. A misimplementation that resolved ``conn.current_waiter``
    by presence alone (dropping the jobId comparison) would fire X's waiter on
    the foreign frame: the provider thread below would surface the wrong
    payload, assertion (a) fails (``done`` is set) AND assertion (c) fails
    (the genuine result finds no current job and is discarded).
    """
    import threading

    from app.generation.provider import GenerateRequest as _GenReq

    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    scope = _scope_for(stack, token)
    registry = stack["server"].app.state.bridge_registry
    provider = RemoteClientProvider(
        registry=registry,
        settings=stack["server"].app.state.settings,
        session_scope=scope,
    )
    outcomes: list[tuple[str, Any]] = []
    done = threading.Event()

    def _hold(job, b):
        # The local "Ollama" job is accepted but NEVER answered (exactly the
        # frame-silent local job of B4): the provider future stays PENDING.
        pass

    bridge.mini.on_job = _hold
    try:

        def _run():
            try:
                outcomes.append(
                    (
                        "ok",
                        provider.generate(
                            _GenReq(
                                attempt_id="ATT-fixb-f1",
                                stage=GenerationStage.CASE_TRUTH,
                                prompt_context=_PROMPT,
                                timeout_seconds=15.0,
                            )
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - surfacing via outcome
                outcomes.append(("err", type(exc).__name__))
            finally:
                done.set()

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        # A REAL pending waiter for job X exists on the socket.
        assert bridge.wait_for(lambda: bridge.job_count == 1, timeout=10)
        conn = registry.lookup_for_scope(scope)
        assert conn is not None
        waiter = conn.current_waiter
        assert waiter is not None  # the pending future for X
        job_x = bridge.jobs[-1]["jobId"]

        # (1) A well-formed job_result for an UNKNOWN/foreign job id while X is
        # pending (the VACUOUS twin sent this with nobody waiting).
        bridge.send_frame(
            {
                "protocolVersion": PROTOCOL_VERSION,
                "type": "job_result",
                "jobId": "JOB-cross-job-0001",
                "status": "SUCCESS",
                "structuredOutput": {"leak": True},
            }
        )
        time.sleep(0.3)
        # (a) X's future is STILL pending — the foreign frame resolved nobody.
        assert done.is_set() is False
        assert waiter.result is None
        assert conn.current_waiter is waiter
        # (b) the wrong-id frame was DISCARDED: no close, no crash.
        assert bridge.close_code is None, bridge.close_code

        # (c) the GENUINE result for X (the bridge's own MiniOllama reply)
        # resolves X — and the wrong frame's payload is NEVER applied.
        bridge.mini.reply_success(bridge.jobs[-1], bridge)
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert done.is_set()
        assert len(outcomes) == 1 and outcomes[0][0] == "ok", outcomes
        result_content = json.loads(outcomes[0][1].content)
        assert isinstance(result_content, dict) and result_content
        assert "leak" not in outcomes[0][1].content  # foreign payload discarded
        assert provider.last_job_id == job_x  # the resolved job IS X
    finally:
        bridge.close()


# =========================================================================== #
# B9 — long local job with a healthy event loop does NOT drop the socket
# =========================================================================== #


def _slow_first_responder(job, b):
    """Run the long local work as a SIBLING task (exactly like the shipped
    client's ``asyncio.create_task(self._run_job(...))`` — including the strong
    reference the shipped ``_Inflight.task`` keeps), so the reader loop stays
    free to answer the server's heartbeats (pongs) while the job runs LONGER
    than the ping interval."""
    if b.job_count == 1:
        async def _work():
            await asyncio.sleep(_B9_LONG_JOB_SECONDS)
            b.mini.reply_success(job, b)

        b._slow_worker = asyncio.ensure_future(_work())  # strong ref (never GC'd)
    else:
        b.mini.reply_success(job, b)


def test_b9_long_job_event_loop_stays_healthy(liveness_stack):
    """Holds the local job LONGER than the heartbeat interval while the client
    keeps the event loop healthy (pongs). The WebSocket REMAINS connected and
    the result resolves — refutes the event-loop-block hypothesis (H4) for the
    shipped path (no timeout/keepalive inflation was needed)."""
    token, _pairing, bridge, _ack = _pair_and_connect(liveness_stack)
    try:
        bridge.mini.on_job = _slow_first_responder
        result = _generate(liveness_stack, token, timeout=60)
        body = result.json()
        assert result.status_code == 201, body
        assert body["status"] == "PUBLISHED", body
        assert bridge.close_code is None, bridge.close_code  # WS REMAINS connected
        status = bridge_status(liveness_stack["base_url"], token)["remoteLocalAi"]
        assert status["connected"] is True and status["ready"] is True
    finally:
        bridge.close()


# =========================================================================== #
# B10 — real RemoteClientProvider end-to-end (not only the registry)
# =========================================================================== #


def test_b10_remote_client_provider_end_to_end(stack):
    """Same anonymous scope: pair -> dispatch through the REAL
    RemoteClientProvider (the exact Provider.protocol implementation the
    generation controller uses) -> the bridge returns a valid result -> the
    provider call succeeds."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    scope = _scope_for(stack, token)
    provider = RemoteClientProvider(
        registry=stack["server"].app.state.bridge_registry,
        settings=stack["server"].app.state.settings,
        session_scope=scope,
    )
    try:
        result = provider.generate(
            GenerateRequest(
                attempt_id="ATT-fixb-b10",
                stage=GenerationStage.CASE_TRUTH,
                prompt_context=_PROMPT,
                timeout_seconds=20.0,
            )
        )
        assert result.content is not None
        payload = json.loads(result.content)
        assert isinstance(payload, dict) and payload  # a VALID bridge result
        assert provider.last_job_id is not None and provider.last_job_id.startswith("JOB-")
        assert provider.last_latency_ms is not None
        assert bridge.job_count == 1  # the provider dispatched exactly one job
        assert bridge.close_code is None
        # The registry-level control: the job was consumed and the slot freed.
        conn = _conn_for(stack, token)
        assert conn is not None and conn.current_waiter is None
    finally:
        bridge.close()


# =========================================================================== #
# helpers
# =========================================================================== #


def _sync_connect(url: str):
    from websockets.sync.client import connect as ws_connect

    return ws_connect(url, open_timeout=10, close_timeout=3)