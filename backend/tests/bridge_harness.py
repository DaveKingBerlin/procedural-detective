"""Phase 22 — REAL in-process test bridge harness (client side of the bridge
job protocol) + a MiniOllama mock + a live uvicorn server wrapper.

The harness implements the exact WSS protocol the bridge CLIENT track must ship
(``app.generation.bridge_protocol``), over a REAL loopback uvicorn server, so
the Phase 22 integration tests exercise the FULL production stack:

    Server (uvicorn, real event loop)
      -> RemoteClientProvider (sync worker thread, run_coroutine_threadsafe)
      -> test bridge (websockets sync client in its own thread)
      -> MiniOllama (canned structured outputs)

Everything is loopback-only (the autouse backend network block allows it); no
external service is ever touched.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

import uvicorn

# --------------------------------------------------------------------------- #
# live uvicorn test server
# --------------------------------------------------------------------------- #


class LiveTestServer:
    """A real uvicorn server for one test app (ephemeral 127.0.0.1 port)."""

    def __init__(self, app: Any) -> None:
        self.app = app
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            log_config=None,
            ws="websockets",
            loop="asyncio",
            timeout_graceful_shutdown=2,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("uvicorn test server failed to start")
        sock = self._server.servers[0].sockets[0]
        self._port = int(sock.getsockname()[1])

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self._port}/api/v1/bridge/ws"

    def close(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=8)


# --------------------------------------------------------------------------- #
# MiniOllama — canned structured outputs per authoritative schema id
# --------------------------------------------------------------------------- #

_CANNED: dict[str, Callable[[], dict[str, Any]]] = {}


def _register_canned() -> None:
    """Import the SAME canned stage payloads the ollama-driver suite uses so the
    bridge path proves byte-identical stage data -> validation -> solver."""
    # Imported lazily: the helper module lives in the tests dir.
    from test_ollama_driver import ICEPICK_SPEC, _case_people, _evidence, _world

    def _case():
        return _case_people()

    def _evidence_out():
        return _evidence()

    def _world_out():
        return _world()

    def _asset_spec():
        return json.loads(ICEPICK_SPEC)

    def _asset_spec_repair():
        return json.loads(ICEPICK_SPEC)

    def _repair():
        # A minimal structurally valid full-draft repair is never reached on
        # the happy path; keep the mapping complete per Phase22 §9.
        return {"crime": {}, "persons": [], "motives": [], "objects": []}

    _CANNED.update(
        {
            "CASE_PEOPLE_v1": _case,
            "EVIDENCE_v1": _evidence_out,
            "WORLD_REQUIREMENTS_v1": _world_out,
            "ASSET_SPEC_v1": _asset_spec,
            "ASSET_SPEC_REPAIR_v1": _asset_spec_repair,
            "REPAIR_v1": _repair,
        }
    )


def canned_output(schema_id: str) -> dict[str, Any] | None:
    """The MiniOllama canned structuredOutput for an authoritative schemaId."""
    if not _CANNED:
        _register_canned()
    producer = _CANNED.get(schema_id)
    return producer() if producer is not None else None


class MiniOllama:
    """Deterministic scripted fake of the visitor's local Ollama: the responder
    side of the bridge. ``on_job(job, bridge)`` is the test hook run in the
    bridge's worker thread; the default replies the canned output (through the
    bridge socket)."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.replies: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # -- scripting hooks ---------------------------------------------------

    def on_job(self, job: dict[str, Any], bridge: "TestBridge") -> None:
        self.jobs.append(job)
        self.reply_success(job, bridge)

    def reply_success(self, job: dict[str, Any], bridge: "TestBridge") -> None:
        output = canned_output(job.get("schemaId", ""))
        if output is None:
            self.reply_fail(job, bridge, "LOCAL_MODEL_UNAVAILABLE")
            return
        self.reply(job, bridge, {"status": "SUCCESS", "structuredOutput": output})

    def reply_fail(self, job: dict[str, Any], bridge: "TestBridge", failure_code: str) -> None:
        self.reply(
            job,
            bridge,
            {"status": "FAILED", "failureCode": failure_code},
        )

    def reply(self, job: dict[str, Any], bridge: "TestBridge", extra: dict[str, Any]) -> None:
        frame = {
            "protocolVersion": 1,
            "type": "job_result",
            "jobId": job["jobId"],
            **extra,
        }
        with self._lock:
            self.replies.append(dict(frame))
        bridge.send_frame(frame)


# --------------------------------------------------------------------------- #
# test bridge client (the exact client-side protocol, over websockets)
# --------------------------------------------------------------------------- #

class TestBridge:
    """One real bridge connection over an ASYNC websockets client owning its
    own event loop + reader task in a background thread.

    The bridge protocol REQUIRES the handshake first frame to be the client's,
    so the handshake coroutine (connect + pairing_hello/bridge_hello + ack)
    runs to completion on the bridge loop BEFORE the reader task starts; the
    test thread blocks on it via ``asyncio.run_coroutine_threadsafe``. The job/
    pong loop then keeps running on that same loop, so a synchronous
    generation (POST /cases) in the test thread never deadlocks.

    ``send_frame``/``send_raw`` are callable from ANY thread (the bridge loop
    sends inline when already on the loop, e.g. inside a responder).
    """

    def __init__(self, url: str, *, auto_pong: bool = True, mini: MiniOllama | None = None) -> None:
        import asyncio

        self._url = url
        self.auto_pong = auto_pong
        self.mini = mini if mini is not None else MiniOllama()
        self._ws: Any = None
        self.pairing_accepted: dict[str, Any] | None = None
        self.cancels: list[dict[str, Any]] = []
        self._jobs: list[dict[str, Any]] = []
        self._sent: list[str] = []
        self._stop = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close the socket (a mid-job close simulates a bridge disconnect).

        Safe from ANY thread, including the bridge loop itself (a responder
        may call ``bridge.close()`` mid-job): on the loop it schedules the
        close; off the loop it blocks on the cross-thread close + join."""
        import asyncio

        self._stop.set()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            try:
                asyncio.ensure_future(self._close_ws())
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            self._call_async(self._close_ws())
        except Exception:  # noqa: BLE001 - teardown never raises
            pass
        self._thread.join(timeout=3)

    async def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _call_async(self, coro: Any, timeout: float = 30):
        import asyncio

        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    # -- handshakes (the FIRST frame is always the client's) -----------------

    def connect_pairing(self, code: str, *, model: str | None = "hermes3:8b") -> dict[str, Any]:
        return self._call_async(self._handshake("pairing_hello", code, model))

    def connect_reconnect(self, token: str) -> dict[str, Any]:
        return self._call_async(self._handshake("bridge_hello", token, None))

    async def _handshake(self, kind: str, credential: str, model: str | None) -> dict[str, Any]:
        import asyncio
        import websockets

        self._ws = await websockets.connect(self._url, open_timeout=10, close_timeout=3)
        if kind == "pairing_hello":
            hello: dict[str, Any] = {
                "protocolVersion": 1,
                "type": "pairing_hello",
                "pairingCode": credential,
            }
            if model is not None:
                hello["model"] = model
        else:
            hello = {
                "protocolVersion": 1,
                "type": "bridge_hello",
                "bridgeSessionToken": credential,
            }
        await self._ws.send(json.dumps(hello, sort_keys=True, separators=(",", ":")))
        ack = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=10))
        self.pairing_accepted = ack
        # Only now may the reader loop start (first frame was ours).
        asyncio.get_running_loop().create_task(self._reader_loop())
        return ack

    # -- reader loop (runs on the bridge loop) -------------------------------

    async def _reader_loop(self) -> None:
        import asyncio

        try:
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:  # noqa: BLE001 - peer closed
                    self._record_close(exc)
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                mtype = msg.get("type")
                if mtype == "job":
                    with self._lock:
                        self._jobs.append(msg)
                    self._handle_job(msg)
                elif mtype == "ping":
                    if self.auto_pong:
                        self.send_frame({"type": "pong"})
                elif mtype == "job_cancel":
                    with self._lock:
                        self.cancels.append(msg)
                elif mtype == "pong":
                    continue
        finally:
            try:
                if self._ws is not None:
                    await self._ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _handle_job(self, msg: dict[str, Any]) -> None:
        try:
            self.mini.on_job(msg, self)
        except Exception:  # noqa: BLE001 - a misbehaving responder never kills
            # the reader loop; the test observes the missing reply.
            pass

    # -- outbound helpers (any thread) ---------------------------------------

    def send_frame(self, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._sent.append(text)
        self.send_raw(text)

    def send_raw(self, text: str) -> None:
        import asyncio

        async def _send() -> None:
            if self._ws is not None:
                try:
                    await self._ws.send(text)
                except Exception:  # noqa: BLE001 - surfaced via close
                    return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is self._loop:
            asyncio.ensure_future(_send())
        else:
            asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def _record_close(self, exc: Exception) -> None:
        try:
            code = getattr(getattr(exc, "rcvd", None), "code", None)
            reason = getattr(getattr(exc, "rcvd", None), "reason", None)
            if code is None:
                code = getattr(exc, "code", None)
                reason = str(getattr(exc, "reason", "") or "")
        except Exception:  # noqa: BLE001
            code, reason = None, None
        with self._lock:
            if self.close_code is None:
                self.close_code = code
                self.close_reason = reason

    # -- test-facing accessors --------------------------------------------------

    @property
    def ws(self) -> Any:
        return self._ws

    @property
    def sent_frames(self) -> list[dict[str, Any]]:
        with self._lock:
            return [json.loads(t) for t in self._sent]

    @property
    def jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._jobs)

    @property
    def job_count(self) -> int:
        with self._lock:
            return len(self._jobs)

    @property
    def bridge_session_id(self) -> str | None:
        if self.pairing_accepted is None:
            return None
        return self.pairing_accepted.get("bridgeSessionId")

    @property
    def bridge_session_token(self) -> str | None:
        if self.pairing_accepted is None:
            return None
        return self.pairing_accepted.get("bridgeSessionToken")

    def wait_for(self, condition: Callable[[], bool], timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.05)
        return condition()


# --------------------------------------------------------------------------- #
# HTTP helpers for the tests (session creation, pairing request, generation)
# --------------------------------------------------------------------------- #

def make_bridge_settings(database_url: str, **overrides) -> Any:
    """The standard Phase 22 server settings (generous admission, bounded
    bridge timings). Each test file may override the moments it exercises."""
    from app.core.config import Settings

    kwargs = dict(
        database_url=database_url,
        cors_allowed_origins=["http://localhost:5173"],
        enable_bridge=True,
        generation_provider="remote_client",
        generation_deadline_seconds=60,
        max_llm_calls_per_generation=16,
        max_core_llm_calls_per_generation=12,
        max_llm_calls_per_procedural_asset=5,
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        bridge_job_deadline_seconds=20,
        bridge_pairing_code_ttl_seconds=60.0,
        bridge_session_ttl_seconds=600.0,
        bridge_idle_timeout_seconds=30.0,
        bridge_heartbeat_interval_seconds=10.0,
        bridge_reconnect_grace_seconds=5.0,
        bridge_max_message_bytes=64 * 1024,
        bridge_max_registry_sessions=16,
        bridge_max_frames_per_window=60,
        bridge_failed_handshake_limit=50,
        # A module-scoped server shares ONE loopback client IP: the public
        # per-IP admission windows must stay out of the way of the suite.
        anon_session_limit_per_ip_per_10_min=10000,
        anon_session_global_limit_per_min=20000,
        generation_limit_per_ip_per_hour=10000,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def new_anonymous_session(base_url: str) -> dict[str, Any]:
    import httpx

    response = httpx.post(f"{base_url}/api/v1/sessions/anonymous", timeout=30)
    response.raise_for_status()
    return response.json()


def create_pairing(base_url: str, session_token: str) -> dict[str, Any]:
    import httpx

    response = httpx.post(
        f"{base_url}/api/v1/bridge/pairing",
        headers={"Authorization": f"Bearer {session_token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def bridge_status(base_url: str, session_token: str) -> dict[str, Any]:
    import httpx

    response = httpx.get(
        f"{base_url}/api/v1/bridge/status",
        headers={"Authorization": f"Bearer {session_token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def start_generation(
    base_url: str, session_token: str, prompt: str
) -> dict[str, Any]:
    import httpx

    response = httpx.post(
        f"{base_url}/api/v1/cases",
        headers={"Authorization": f"Bearer {session_token}"},
        json={"prompt": prompt},
        timeout=120,
    )
    return {"http_status": response.status_code, "body": response.json()}


# --------------------------------------------------------------------------- #
# scripted bridge (service-level tests: no uvicorn, deterministic)
# --------------------------------------------------------------------------- #

class _LoopThread:
    """A dedicated running asyncio loop in a background thread (the target of
    run_coroutine_threadsafe from the test's sync thread)."""

    def __init__(self) -> None:
        import asyncio

        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)


class ScriptedSocket:
    """Async-compatible fake bridge socket.

    ``outputs`` is a list of items consumed in order per received job:
      - a dict                                  -> SUCCESS structuredOutput
      - ("error", <typed code>)                 -> FAILED result
      - ("timeout",)                            -> never replies
      - ("close",)                              -> transport failure at dispatch
    """

    def __init__(self, outputs, loop):
        self.outputs = list(outputs)
        self.loop = loop
        self.sent_frames: list[str] = []
        self._registry = None
        self._conn = None

    def attach(self, registry, conn) -> None:
        self._registry = registry
        self._conn = conn

    async def send_text(self, data: str) -> None:
        self.sent_frames.append(data)
        frame = json.loads(data)
        if frame.get("type") != "job":
            return
        job_id = frame["jobId"]
        if not self.outputs:
            self._registry.resolve_job(self._conn, job_id, "error", "LOCAL_MODEL_UNAVAILABLE")
            return
        item = self.outputs.pop(0)
        if isinstance(item, dict):
            self._registry.resolve_job(self._conn, job_id, "content", item)
        elif isinstance(item, tuple) and item and item[0] == "error":
            self._registry.resolve_job(self._conn, job_id, "error", item[1])
        elif isinstance(item, tuple) and item and item[0] == "close":
            raise ConnectionError("scripted socket closed mid-dispatch")
        # ("timeout",) -> no reply; the provider waits and times out


def make_scripted_bridge(registry, *, session_scope, model, outputs, settings=None):
    """Bind a scripted (non-socket, loop-backed) bridge for service-level tests.

    Returns ``(conn, socket, loop_thread)``. The provider dispatches through
    ``run_coroutine_threadsafe`` onto the loop-thread, so a REAL
    GenerationService run works without any network server.
    """
    from app.generation.bridge_protocol import generate_bridge_session_id

    loop_thread = _LoopThread()
    sock = ScriptedSocket(list(outputs), loop_thread.loop)
    conn = registry.bind(
        bridge_session_id=generate_bridge_session_id(),
        session_scope=session_scope,
        model=model,
        capabilities=("STRUCTURED_MODEL_INFERENCE",),
        expires_at=float(time.time() + 600),
        socket=sock,
        loop=loop_thread.loop,
        now=float(time.time()),
    )
    sock.attach(registry, conn)
    return conn, sock, loop_thread


__all__ = [
    "LiveTestServer",
    "MiniOllama",
    "ScriptedSocket",
    "TestBridge",
    "bridge_status",
    "canned_output",
    "create_pairing",
    "make_bridge_settings",
    "make_scripted_bridge",
    "new_anonymous_session",
    "start_generation",
]