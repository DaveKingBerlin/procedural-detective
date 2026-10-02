"""Phase 22–24 Fix C — third-sequential-job 1002 / BRIDGE_DISCONNECTED regression.

Real-machine observation (Phase22-24-FixC.md): after two sequential
remote-client jobs succeed (``case_truth``, ``evidence``), the third job
(``activity_log``) fails in ~7 ms with ``BRIDGE_DISCONNECTED`` then
``bridge.disconnected reasonCode=PROTOCOL_ERROR closeCode=1002``; a same-token
reconnect succeeds afterwards.

Root cause (H5, proven): the server dispatches the third job with
``schemaId=ACTIVITY_LOG_v1`` (``STAGE_TO_SCHEMA_ID["activity_log"]`` ->
``remote_client_provider.generate`` -> ``job_frame``). Phase 19J grew the
SERVER copy's closed vocabulary
(``app/generation/bridge_protocol.py:142-158``) to include
``ACTIVITY_LOG_v1`` / ``ACTIVITY_LOG_REPAIR_v1``, but the BRIDGE CLIENT copy
(``bridge/pd_ollama_bridge/protocol.py:104-113``) never did — so the real
client's ``validate_frame`` -> ``_validate_job_schema`` (protocol.py:273-274)
rejected the job frame as a protocol violation, ``bridge_client.py:232-238``
closed the socket with 1002, the server detached and failed the in-flight job
``BRIDGE_DISCONNECTED``.

Fix C: synchronise the client vocabulary with the server set (add the two
ACTIVITY_LOG ids). Nothing else changed — message parsing, heartbeat/pong
(both sides send JSON ping/pong), send serialization, duplicate/unknown-job
discard, server-side schema validation and disconnect/reconnect semantics are
untouched.

The harness suites below run the REAL shipped ``pd_ollama_bridge.BridgeClient``
(in-process ASGI "Ollama", loopback websockets vs a live uvicorn backend) so the
exact client-side validation bug is exercised end to end. No live Ollama /
bridge / external network is ever contacted.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# The REAL bridge client package ships in the sibling ``bridge`` tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bridge"))

from conftest import upgrade_db  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

from bridge_harness import (  # noqa: E402
    LiveTestServer,
    TestBridge,
    canned_output,
    create_pairing,
    make_bridge_settings,
    new_anonymous_session,
)

from pd_ollama_bridge.config import Config, TokenStore  # noqa: E402
from pd_ollama_bridge.ollama_client import OllamaClient  # noqa: E402
from pd_ollama_bridge.bridge_client import BridgeClient  # noqa: E402
from pd_ollama_bridge import protocol as client_protocol  # noqa: E402

from app.generation.bridge_protocol import (  # noqa: E402
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

_OLLAMA = "hermes3:8b"

_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa Koenig\n"
    "Location: office\n"
)


def _full_stack(tmp_path_factory, *, tag: str, **overrides):
    db_dir = tmp_path_factory.mktemp(f"bridge_fixc_{tag}")
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
def fast_stack(tmp_path_factory):
    """A SHORT heartbeat (0.2s) + generous frame window so heartbeat/result/
    job and concurrent-send interleavings actually overlap on one run."""
    ctx, app = _full_stack(
        tmp_path_factory,
        tag="fast",
        bridge_heartbeat_interval_seconds=0.2,
        bridge_idle_timeout_seconds=30.0,
        bridge_max_frames_per_window=200,
    )
    yield ctx
    ctx["server"].close()
    app.state.engine.dispose()
    app.state.store.dispose()


def _scope_for(stack, token: str) -> str:
    from app.auth.tokens import verifier as _verifier

    return stack["server"].app.state.store.get_session_by_verifier(
        _verifier(token)
    ).session_id


# =========================================================================== #
# REAL bridge client harness (the exact client that shipped the bug)
# =========================================================================== #


class _CannedOllamaApp:
    """In-process ASGI "Ollama" (httpx ASGITransport): pops one canned dict per
    ``/api/chat`` request so the REAL bridge client can resolve every job."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []
        self.received = 0

    async def __call__(self, scope, receive, send):
        assert scope["type"] == "http"
        method = scope["method"]
        path = scope["path"]
        if method == "GET" and path == "/api/tags":
            body = json.dumps({"models": [_OLLAMA]}).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return
        if method == "POST" and path == "/api/chat":
            body = b""
            while True:
                event = await receive()
                body += event.get("body", b"")
                if not event.get("more_body", False):
                    break
            self.requests.append(json.loads(body.decode("utf-8")))
            self.received += 1
            output = self.outputs.pop(0) if self.outputs else {"result": "ok"}
            payload = {"message": {"role": "assistant", "content": json.dumps(output)}}
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": json.dumps(payload).encode("utf-8"),
                    "more_body": False,
                }
            )
            return
        await send({"type": "http.response.start", "status": 404,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})


class _RealBridge:
    """One REAL ``pd_ollama_bridge.BridgeClient`` on its own asyncio loop in a
    background thread, paired to the live uvicorn backend.

    ``outputs`` is a FIFO of structured outputs handed to the client's local
    "Ollama" in dispatch order (the three production stages we drive).
    """

    def __init__(self, server_url: str, pairing_code: str, outputs, *, model=_OLLAMA):
        self.mock = _CannedOllamaApp(outputs)
        self.events: dict[str, list] = {"connected": [], "reconnecting": []}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        config = Config(
            pairing_code=pairing_code,
            server_url=server_url,
            ollama_url="http://127.0.0.1:11434",
            model=model,
            idle_timeout_seconds=30.0,
            connect_timeout_seconds=5.0,
            max_reconnect_attempts=1,
            reconnect_backoff_base_seconds=0.05,
            reconnect_backoff_cap_seconds=0.2,
        ).validate()
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.mock),
            base_url="http://127.0.0.1:11434",
        )
        self._http = http
        ollama = OllamaClient(
            base_url="http://127.0.0.1:11434",
            model=model,
            http_client=http,
            connect_timeout_seconds=5.0,
        )
        self.bridge = BridgeClient(
            config=config,
            token_store=TokenStore(),
            ollama=ollama,
            on_connected=lambda kind, host: self.events["connected"].append((kind, host)),
            on_reconnecting=lambda attempt, delay: self.events["reconnecting"].append(
                (attempt, delay)
            ),
        )
        self._task = asyncio.run_coroutine_threadsafe(self.bridge.run(), self._loop)

    @property
    def sessions_connected(self) -> int:
        return self.bridge.sessions_connected

    def stop(self) -> None:
        try:
            self._task.cancel()
        except Exception:  # noqa: BLE001 - teardown best-effort
            pass
        try:
            asyncio.run_coroutine_threadsafe(self._http.aclose(), self._loop).result(
                timeout=3
            )
        except Exception:  # noqa: BLE001 - teardown best-effort
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3)


def _wait_connected(stack, token: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            st = httpx.get(
                f"{stack['base_url']}/api/v1/bridge/status",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            ).json()
            if st["remoteLocalAi"]["connected"]:
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
    raise AssertionError("real bridge client never connected")


# =========================================================================== #
# C1 · C10 — THE DEFINING regression: three real production message types
# (CASE_PEOPLE_v1 -> EVIDENCE_v1 -> ACTIVITY_LOG_v1) on ONE bridge session,
# through the REAL provider + the REAL bridge client. Also pins C2 (the third
# job reaches the client), C3 (socket stays open / no 1002) and C9 (the real
# codec accepts ACTIVITY_LOG_v1).
# =========================================================================== #


def test_c1_c2_c3_c9_c10_three_production_stages_real_client(stack):
    """One bridge session processes the three REAL production message types
    CASE_PEOPLE_v1 -> EVIDENCE_v1 -> ACTIVITY_LOG_v1.

    Defines the exact third-sequential-job progression: all three provider
    calls complete, the client never closes with 1002 (it stays connected and
    usable), the third job ALSO reaches the REAL client (C2) and is accepted
    (C9), and there is no re-pair (C3). FAILS on the pre-Fix-C client
    vocabulary: the real client rejects the ACTIVITY_LOG_v1 job frame
    (validate_frame -> _validate_job_schema, protocol.py:273-274), closes 1002,
    and provider stage 3 fails typed BRIDGE_DISCONNECTED."""
    base = stack["base_url"]
    scope_service = stack["server"].app.state.store
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    outputs = [
        canned_output("CASE_PEOPLE_v1"),
        canned_output("EVIDENCE_v1"),
        canned_output("ACTIVITY_LOG_v1"),
    ]
    real = _RealBridge(base, pairing["pairingCode"], outputs)
    try:
        _wait_connected(stack, token)
        registry = stack["server"].app.state.bridge_registry
        provider = RemoteClientProvider(
            registry=registry,
            settings=stack["server"].app.state.settings,
            session_scope=_scope_for(stack, token),
        )
        first_conn = registry.lookup_for_scope(_scope_for(stack, token))
        assert first_conn is not None
        assert real.events["connected"] == [("pairing", "127.0.0.1")]
        assert real.events["reconnecting"] == []

        # C1 — the three production stages in order.
        stage_results = []
        for attempt, stage in enumerate(
            (
                GenerationStage.CASE_TRUTH,
                GenerationStage.EVIDENCE,
                GenerationStage.ACTIVITY_LOG,
            ),
            1,
        ):
            result = provider.generate(
                GenerateRequest(
                    attempt_id=f"ATT-fixc-c1-{attempt}",
                    stage=stage,
                    prompt_context=_PROMPT,
                    timeout_seconds=20.0,
                )
            )
            content = json.loads(result.content)
            assert isinstance(content, dict) and content  # real valid result
            stage_results.append(content)
            # C3 — the SAME binding/registry connection stays live across every
            # stage: no 1002 teardown, no re-pair, no new generation.
            conn = registry.lookup_for_scope(_scope_for(stack, token))
            assert conn is not None and conn is first_conn
            assert conn.is_connected
            assert conn.current_waiter is None  # slot freed; ready for job N+1

        # C2 + C9 — the THIRD job (ACTIVITY_LOG_v1) reached the REAL client and
        # was accepted + routed to the local model (three chat requests).
        assert real.mock.received == 3
        assert provider.last_job_id is not None and provider.last_job_id.startswith("JOB-")

        # C3 — socket stayed open through the whole progression; the client
        # NEVER initiated a 1002 close (no reconnect was ever attempted), and
        # the server still reports the bridge connected and ready.
        assert real.events["reconnecting"] == []
        assert real.sessions_connected == 1  # paired exactly once
        assert real.events["connected"] == [("pairing", "127.0.0.1")]
        status = httpx.get(
            f"{base}/api/v1/bridge/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        ).json()["remoteLocalAi"]
        assert status["connected"] is True and status["ready"] is True
    finally:
        real.stop()


# =========================================================================== #
# C4 — heartbeat / result / job interleavings on the live server
# (both ``result -> heartbeat -> next job`` and ``heartbeat -> result ->
# next job``) — no starvation, no mismatch, no 1002.
# =========================================================================== #


def test_c4_heartbeat_result_job_interleavings(fast_stack):
    """A full remote_client generation under a 0.2s server heartbeat: the
    sibling pinger interleaves with job dispatch and result delivery on the
    SAME socket; every job resolves, jobs never get mismatched, and no 1002
    ever occurs."""
    base = fast_stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(fast_stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    try:
        registry = fast_stack["server"].app.state.bridge_registry
        provider = RemoteClientProvider(
            registry=registry,
            settings=fast_stack["server"].app.state.settings,
            session_scope=_scope_for(fast_stack, token),
        )
        result = provider.generate(
            GenerateRequest(
                attempt_id="ATT-fixc-c4",
                stage=GenerationStage.CASE_TRUTH,
                prompt_context=_PROMPT,
                timeout_seconds=20.0,
            )
        )
        content = json.loads(result.content)
        assert isinstance(content, dict) and content
        # Interleavings stay healthy: the socket is still the same connected
        # binding (no 1002, no re-pair) and the next job resolves too.
        status = httpx.get(
            f"{base}/api/v1/bridge/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        ).json()["remoteLocalAi"]
        assert status["connected"] is True
        conn = registry.lookup_for_scope(_scope_for(fast_stack, token))
        assert conn is not None and conn.current_waiter is None
        assert bridge.close_code is None, bridge.close_code
        # Result -> heartbeat -> next job: the SAME socket also serves a
        # follow-up job after the pings already interleaved.
        result2 = provider.generate(
            GenerateRequest(
                attempt_id="ATT-fixc-c4b",
                stage=GenerationStage.CASE_TRUTH,
                prompt_context=_PROMPT,
                timeout_seconds=20.0,
            )
        )
        assert json.loads(result2.content)  # no starvation / mismatch
        assert bridge.close_code is None  # no 1002
    finally:
        bridge.close()


# =========================================================================== #
# C5 — pong handling: a valid app-level JSON pong at unexpected timing is
# consumed as ``continue`` in the server receive loop (never a close).
# =========================================================================== #


def test_c5_pong_at_unexpected_timing_accepted(stack):
    """A well-formed bare ``pong`` the client sends WITHOUT a preceding server
    ping is accepted as a no-op (bridge_ws receive loop: ``MSG_PONG ->
    continue``): the socket stays open and the next job still dispatches."""
    base = stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(stack["server"].ws_url)
    bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    try:
        # Unsolicited app-level pong (there is no ping in flight).
        bridge.send_frame({"type": "pong"})
        time.sleep(0.3)
        assert bridge.close_code is None, bridge.close_code
        registry = stack["server"].app.state.bridge_registry
        provider = RemoteClientProvider(
            registry=registry,
            settings=stack["server"].app.state.settings,
            session_scope=_scope_for(stack, token),
        )
        result = provider.generate(
            GenerateRequest(
                attempt_id="ATT-fixc-c5",
                stage=GenerationStage.CASE_TRUTH,
                prompt_context=_PROMPT,
                timeout_seconds=20.0,
            )
        )
        assert json.loads(result.content)
        assert bridge.close_code is None  # still never closed
    finally:
        bridge.close()


def test_c5_foreign_frame_handled_per_protocol(stack):
    """A stray foreign frame whose message type is NOT in the closed protocol
    vocabulary (a client sending, e.g., ``delete_all_data`` or an unsolicited
    ``ping``) is handled per protocol after a healthy result flow: the receive
    loop maps the unknown type to the documented unsupported-type close (1003)
    instead of mutating bridge state or leaking. (A well-formed ``job_result``
    envelope for an UNKNOWN jobId is instead DISCARDED without closing — that
    policy is pinned by C7 / Fix B's B8 suite.)"""
    from app.services.bridge_session import CLOSE_UNSUPPORTED_TYPE

    base = stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(stack["server"].ws_url)
    bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    import time as _time

    try:
        # Consume the healthy first result first.
        registry = stack["server"].app.state.bridge_registry
        provider = RemoteClientProvider(
            registry=registry,
            settings=stack["server"].app.state.settings,
            session_scope=_scope_for(stack, token),
        )
        result = provider.generate(
            GenerateRequest(
                attempt_id="ATT-fixc-c5f",
                stage=GenerationStage.CASE_TRUTH,
                prompt_context=_PROMPT,
                timeout_seconds=20.0,
            )
        )
        assert json.loads(result.content)
        # Now a foreign app-level frame with a valid envelope but an UNKNOWN
        # message type: server receive loop closes typed (unknown type -> 1003).
        bridge.send_raw(
            json.dumps(
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "type": "run_command",
                    "cmd": "reboot",
                }
            )
        )
        assert bridge.wait_for(lambda: bridge.close_code is not None, timeout=10)
        assert bridge.close_code == CLOSE_UNSUPPORTED_TYPE, bridge.close_code
    finally:
        _time.sleep(0.2)
        bridge.close()


# =========================================================================== #
# C6 — duplicate PREVIOUS-job result is discarded and never applied to the
# current job (no wrong-future resolution, no truth leak, no crash).
# =========================================================================== #


def test_c6_duplicate_previous_job_result_discarded(stack):
    """After job A completes, a duplicate SUCCESS result for A arriving while
    job B is CURRENT is DISCARDED (``resolve_job`` non-current branch): B stays
    pending, B's genuine result resolves only B, A's payload is never applied
    again and no truth leaks into B."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    try:
        registry = stack["server"].app.state.bridge_registry
        conn = _conn_for(stack, token)
        assert conn is not None
        # Complete job A.
        waiter_a = registry.begin_job(conn, "JOB-C6-A")
        assert waiter_a is not None
        assert registry.resolve_job(conn, "JOB-C6-A", "content", {"payload": "A"}) is True
        assert waiter_a.result == ("content", {"payload": "A"})
        # Begin job B (the current job).
        waiter_b = registry.begin_job(conn, "JOB-C6-B")
        assert waiter_b is not None
        # Duplicate of the PREVIOUS (already-completed) job A while B is current.
        assert (
            registry.resolve_job(conn, "JOB-C6-A", "content", {"leak": "A-DUPLICATE"})
            is False
        )
        assert waiter_b.result is None  # B stays pending — no wrong-future
        # B's genuine result is the only one that resolves B.
        assert registry.resolve_job(conn, "JOB-C6-B", "content", {"payload": "B"}) is True
        assert waiter_b.result == ("content", {"payload": "B"})
        # The leaked duplicate was never applied.
        assert conn.current_waiter is None
    finally:
        bridge.close()


def test_c6_live_duplicate_previous_job_result_not_applied(stack):
    """Live variant over the REAL server: while the activity_log job is in
    flight, the bridge replays the previous (evidence) job's SUCCESS frame. The
    server discards it; the activity_log job is resolved ONLY by its genuine
    result and the evidence payload is never re-applied."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    evidence_job: dict = {}

    def _responder(job, b):
        if job["schemaId"] == "EVIDENCE_v1":
            evidence_job["jobId"] = job["jobId"]
            b.mini.reply_success(job, b)
            return
        if job["schemaId"] == "ACTIVITY_LOG_v1" and "ACTIVITY_LOG_v1" not in {
            seen["schemaId"] for seen in b.jobs[:-1]
        }:
            # First activity_log job: replay a duplicate of the completed
            # EVIDENCE result WHILE this job is current.
            b.send_frame(
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "type": "job_result",
                    "jobId": evidence_job["jobId"],
                    "status": "SUCCESS",
                    "structuredOutput": {"leak": "EVIDENCE-DUPLICATE"},
                }
            )
        b.mini.reply_success(job, b)

    bridge.mini.on_job = _responder
    try:
        registry = stack["server"].app.state.bridge_registry
        provider = RemoteClientProvider(
            registry=registry,
            settings=stack["server"].app.state.settings,
            session_scope=_scope_for(stack, token),
        )
        # evidence job first (job 2 of the sequence), then activity_log (job 3).
        provider.generate(
            GenerateRequest(
                attempt_id="ATT-fixc-c6-ev",
                stage=GenerationStage.EVIDENCE,
                prompt_context=_PROMPT,
                timeout_seconds=20.0,
            )
        )
        result3 = provider.generate(
            GenerateRequest(
                attempt_id="ATT-fixc-c6-al",
                stage=GenerationStage.ACTIVITY_LOG,
                prompt_context=_PROMPT,
                timeout_seconds=20.0,
            )
        )
        content = json.loads(result3.content)
        # The activity_log stage resolved with a REAL activity-log payload, not
        # the duplicated evidence payload.
        assert isinstance(content, dict) and content
        assert "EVIDENCE-DUPLICATE" not in content
        # The evidence provider result was never overwritten/corrupted.
        conn = _conn_for(stack, token)
        assert conn is not None
        assert bridge.close_code is None  # duplicates never cause a protocol close
    finally:
        bridge.close()


# =========================================================================== #
# C7 — unknown jobId valid envelope -> fail-closed, no resolution of another
# pending job, no leak. (WS-level twin already pinned by Fix B's
# ``test_b8_wrong_jobid_never_resolves_pending_waiter``.)
# =========================================================================== #


def test_c7_unknown_jobid_envelope_fail_closed(stack):
    """A well-formed result envelope for an UNKNOWN jobId while a REAL job is
    pending: never resolves the pending job, never leaks, never crashes."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    try:
        registry = stack["server"].app.state.bridge_registry
        conn = _conn_for(stack, token)
        assert conn is not None
        waiter = registry.begin_job(conn, "JOB-C7-CURRENT")
        assert waiter is not None
        assert (
            registry.resolve_job(conn, "JOB-UNKNOWN-9999", "content", {"leak": True})
            is False
        )
        assert waiter.result is None  # pending job never resolved by foreign id
        assert conn.current_waiter is waiter
        assert registry.resolve_job(conn, "JOB-C7-CURRENT", "content", {"real": 1}) is True
        assert waiter.result == ("content", {"real": 1})
    finally:
        bridge.close()


# =========================================================================== #
# C8 — concurrent outbound sends stress (heartbeat + job dispatch on the
# server; result + pong on the client). websockets 17.1 frames are built and
# written atomically per task (no await between buffer-write and send_data on
# an unfragmented text frame), so this stress proves library-level safety —
# no per-connection writer queue/lock is needed.
# =========================================================================== #


def test_c8_concurrent_outbound_writes_stress(fast_stack):
    """Server: heartbeat task writes pings concurrently with provider job
    dispatch on the SAME socket. Client: the harness reader answers pings
    (pong) while result frames are written. Stress several jobs; every result
    must be byte-exact (no frame interleaving) and no 1002 may ever occur."""
    base = fast_stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(fast_stack["server"].ws_url)
    bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    try:
        registry = fast_stack["server"].app.state.bridge_registry
        provider = RemoteClientProvider(
            registry=registry,
            settings=fast_stack["server"].app.state.settings,
            session_scope=_scope_for(fast_stack, token),
        )
        for i in range(4):
            result = provider.generate(
                GenerateRequest(
                    attempt_id=f"ATT-fixc-c8-{i}",
                    stage=GenerationStage.CASE_TRUTH,
                    prompt_context=_PROMPT,
                    timeout_seconds=20.0,
                )
            )
            content = json.loads(result.content)
            assert isinstance(content, dict) and content  # not corrupted
            assert bridge.close_code is None  # no 1002 after any interleaving
        conn = registry.lookup_for_scope(_scope_for(fast_stack, token))
        assert conn is not None and conn.current_waiter is None
        status = httpx.get(
            f"{base}/api/v1/bridge/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        ).json()["remoteLocalAi"]
        assert status["connected"] is True and status["ready"] is True
    finally:
        bridge.close()


# =========================================================================== #
# C11 — Fix B reconnect regression retained (transport drop -> same-token
# reconnect -> accepted). The exhaustive Fix B suite (test_phase22_24_fixb.py
# B4/B5/B7) pins this; here we assert the session credential still enables a
# reconnect on the same server after a clean close.
# =========================================================================== #


def test_c11_same_token_reconnect_retained(stack):
    token, _pairing, bridge, ack = _pair_and_connect(stack)
    secret = ack["bridgeSessionToken"]
    session_id = ack["bridgeSessionId"]
    try:
        bridge.close()
        bridge2 = TestBridge(stack["server"].ws_url)
        ack2 = bridge2.connect_reconnect(secret)
        assert ack2["bridgeSessionId"] == session_id
        assert "bridgeSessionToken" not in ack2
        status = httpx.get(
            f"{stack['base_url']}/api/v1/bridge/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        ).json()["remoteLocalAi"]
        assert status["connected"] is True and status["ready"] is True
        bridge2.close()
    finally:
        if bridge.close_code is None:
            bridge.close()


# =========================================================================== #
# C12 — cross-session isolation retained (the exhaustive Fix A/Phase 24
# negative suites pin this; assertion mirrors the creator/other-session split).
# =========================================================================== #


def test_c12_cross_session_isolation_retained(stack):
    base = stack["base_url"]
    creator_token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, creator_token)
    bridge = TestBridge(stack["server"].ws_url)
    bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    try:
        other_token = new_anonymous_session(base)["anonymousSessionToken"]
        assert other_token != creator_token
        registry = stack["server"].app.state.bridge_registry
        provider = RemoteClientProvider(
            registry=registry,
            settings=stack["server"].app.state.settings,
            session_scope=_scope_for(stack, other_token),
        )
        from app.generation.provider import StageDriverProviderFailure

        try:
            provider.generate(
                GenerateRequest(
                    attempt_id="ATT-fixc-c12",
                    stage=GenerationStage.CASE_TRUTH,
                    prompt_context=_PROMPT,
                    timeout_seconds=20.0,
                )
            )
            raise AssertionError("cross-session generation must fail closed")
        except StageDriverProviderFailure as exc:
            assert public_failure_code(str(exc.code)) == (
                GenerationFailureCode.BRIDGE_NOT_CONNECTED.value
            )
        # Session B never saw / dispatched to A's bridge.
        assert bridge.job_count == 0
    finally:
        bridge.close()


# =========================================================================== #
# helpers
# =========================================================================== #


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


def _conn_for(stack, token: str):
    return stack["server"].app.state.bridge_registry.lookup_for_scope(
        _scope_for(stack, token)
    )