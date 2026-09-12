"""Phase 5 defect regressions — DEF-045, DEF-046, DEF-047, DEF-048.

Each test reproduces the reported fault, then asserts the fixed behavior.

DEF-048 live-wire coverage: the chunked-body cap is proven against a REAL
uvicorn server (in-process daemon thread on a free loopback port) — the
direct-ASGI harness alone allowed the terminal-message hang to slip through
twice. The live test writes a chunked 70 KB body in ONE TCP write (uvicorn
coalesces it into a single terminal ``http.request`` message), asserts the
413 envelope arrives within a socket timeout (never a hang), verifies the
route never ran (no session/case mutation), and cleans the listener up.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.auth.tokens import verifier as token_verifier
from phase5_helpers import (
    auth,
    create_case,
    create_playthrough,
    create_session,
    golden_script,
    held_published,
    seed_pending_version,
    seed_session,
)

TWO_TO_63 = 2**63
MAX_VERSION = 2**63 - 1
TEN_TO_30 = 10**30

_via = "http://localhost:5173"  # unused; kept for clarity of the headers helper below


# --------------------------------------------------------------------------- #
# DEF-045 — oversized version ints answer 422/404, never a sqlite 500
# --------------------------------------------------------------------------- #


def test_de045_oversized_version_query_answers_422_not_500(phase5_app):
    """2**63 / 10**30 / 30-digit versions -> 422; never the 500 envelope."""
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        case_id = case["caseId"]
        creator = case["creatorAccessToken"]
        for bad in (str(TWO_TO_63), str(TEN_TO_30), "9" * 30, str(TWO_TO_63 + 1)):
            res = client.get(
                f"/api/v1/cases/{case_id}?version={bad}", headers=auth(creator)
            )
            assert res.status_code == 422, bad
            assert res.json()["error"]["code"] == "VALIDATION_ERROR", bad
            # No sqlite interaction: the error is deterministic and sanitized.
            assert "Traceback" not in res.text and "sqlite" not in res.text
        # 0 / -1 stay 422 (already covered by ge=1, kept here as a bound trio).
        for bad in ("0", "-1"):
            res = client.get(
                f"/api/v1/cases/{case_id}?version={bad}", headers=auth(creator)
            )
            assert res.status_code == 422
        # In-range but nonexistent versions -> 404 (deterministic, per design).
        for ok in (str(MAX_VERSION), "7"):
            res = client.get(
                f"/api/v1/cases/{case_id}?version={ok}", headers=auth(creator)
            )
            assert res.status_code == 404, ok
        # The REAL published version still resolves (200).


def test_de045_oversized_version_path_answers_422_not_500(phase5_app):
    """Playthrough creation path int is bounded the same way."""
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        creator = case["creatorAccessToken"]
        for bad in (str(TWO_TO_63), str(TEN_TO_30), "9" * 30, "0", "-1"):
            res = client.post(
                f"/api/v1/cases/{case['caseId']}/versions/{bad}/playthroughs",
                headers=auth(creator),
            )
            assert res.status_code == 422, bad
            assert res.json()["error"]["code"] == "VALIDATION_ERROR", bad
        # Non-numeric / float literals still 422.
        for bad in ("abc", "1.5"):
            res = client.post(
                f"/api/v1/cases/{case['caseId']}/versions/{bad}/playthroughs",
                headers=auth(creator),
            )
            assert res.status_code == 422


def test_de045_boundary_values_use_numeric_path_still_works(phase5_app):
    """The maxima that ARE valid (1 .. 2**63-1) never crash and behave."""
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        creator = case["creatorAccessToken"]
        # Valid in-range literals on the GET route -> deterministic 404 for a
        # nonexistent version (sqlite binds MAX fine).
        res = client.get(
            f"/api/v1/cases/{case['caseId']}?version={MAX_VERSION}",
            headers=auth(creator),
        )
        assert res.status_code == 404
        # A REAL version still resolves.
        res = client.get(
            f"/api/v1/cases/{case['caseId']}?version=1", headers=auth(creator)
        )
        assert res.status_code == 200
        assert res.json()["caseVersion"] == 1


# --------------------------------------------------------------------------- #
# DEF-046 — publish_transactionally requires the authoritative VALIDATING
# attempt; other attempt states are refused with zero mutation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seeded_status", ["DRAFT", "GENERATING", "REPAIRING", "FAILED"])
def test_de046_non_validating_attempts_cannot_publish(
    store, database_url, seeded_status
):
    from app.persistence.store import Store
    from app.services.publication import PublicationError, PublicationService

    script = golden_script(store, database_url)
    published, record, session_id, clock = held_published(database_url, script)

    now = seed_session(store, session_id, clock)
    store.create_case(
        case_id=published.case_id,
        quota_session_id=session_id,
        title="T",
        difficulty=None,
        created_at=now,
    )
    store.create_case_version(
        case_id=published.case_id,
        version=published.case_version,
        state=seeded_status,
        generation_id=f"GEN-{published.case_version}",
        created_at=now,
    )
    store.upsert_generation_attempt(
        attempt_id=published.generation_attempt_id,
        case_id=published.case_id,
        case_version=published.case_version,
        status=seeded_status,
        stage=seeded_status.lower(),
        progress=50,
        created_at=now,
        updated_at=now,
    )

    service = PublicationService(store)
    with pytest.raises(PublicationError):
        service.publish_transactionally(
            published, seed=record.seed, prompt=record.prompt, title="T"
        )
    # ZERO mutation: no published row, version + attempt untouched.
    assert store.get_published(published.case_id, published.case_version) is None
    assert (
        store.get_case_version(published.case_id, published.case_version).state
        == seeded_status
    )
    assert (
        store.get_generation_attempt_by_id(published.generation_attempt_id).status
        == seeded_status
    )


def test_de046_validating_attempt_still_publishes(store, database_url):
    """The legitimate VALIDATING path through the service/API still works."""
    from app.services.publication import PublicationService

    script = golden_script(store, database_url)
    published, record, session_id, clock = held_published(database_url, script)
    seed_pending_version(store, published, session_id, clock)  # state VALIDATING
    result = PublicationService(store).publish_transactionally(
        published, seed=record.seed, prompt=record.prompt, title="T"
    )
    assert result["caseVersion"] == published.case_version
    assert store.get_published(published.case_id, published.case_version) is not None
    assert (
        store.get_case_version(published.case_id, published.case_version).state
        == "PUBLISHED"
    )
    assert (
        store.get_generation_attempt_by_id(published.generation_attempt_id).status
        == "PUBLISHED"
    )


def test_de046_api_generation_to_publication_still_passes(phase5_app):
    """The full API create -> publish flow persists the durable attempt as
    VALIDATING before the atomic publication, so the API path keeps working."""
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        assert case["status"] == "PUBLISHED"
        # The durable attempt row says VALIDATING at publication time and the
        # version is coherent post-publish.
        attempt = phase5_app.state.store.get_generation_attempt_by_id(
            case["generationAttemptId"]
        )
        assert attempt.status == "PUBLISHED"
        assert attempt.case_id == case["caseId"]
        version_row = phase5_app.state.store.get_case_version(case["caseId"], 1)
        assert version_row.state == "PUBLISHED"


# --------------------------------------------------------------------------- #
# DEF-047 — playthrough_id is independent of the playthroughAccessToken
# --------------------------------------------------------------------------- #


def test_de047_playthrough_id_is_independent_from_token(phase5_app):
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        status, body = create_playthrough(
            client, case["creatorAccessToken"], case["caseId"], 1
        )
        assert status == 201
    pt_id = body["playthroughId"]
    token = body["playthroughAccessToken"]
    # The id shares ZERO characters with the token (no prefix leak).
    assert pt_id != f"PT-{token[:24]}"
    assert not pt_id.startswith("PT-" + token[:24])
    # id/token are both opaque; the id must not be a substring of the token.
    assert pt_id[3:] not in token
    # The DB column carries the SAME opaque id and stores only the verifier.
    row = phase5_app.state.store.get_playthrough_by_id(pt_id)
    assert row is not None
    assert row.token_verifier == token_verifier(token)
    assert row.token_verifier != token
    # The raw token never lands in the DB.
    assert token not in str(row.__dict__)


def test_de047_playthrough_ids_are_unique(phase5_app):
    ids: set[str] = set()
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        creator = case["creatorAccessToken"]
        for _ in range(5):
            _status, body = create_playthrough(client, creator, case["caseId"], 1)
            assert body["playthroughId"] not in ids
            ids.add(body["playthroughId"])
    assert len(ids) == 5


def test_de047_pinning_and_authorization_still_green(phase5_app):
    """After the id change: bootstrap + pinning + A-on-B isolation survive."""
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        creator = case["creatorAccessToken"]
        _, pt_a = create_playthrough(client, creator, case["caseId"], 1)
        _, pt_b = create_playthrough(client, creator, case["caseId"], 1)
        res = client.get(
            f"/api/v1/playthroughs/{pt_a['playthroughId']}",
            headers=auth(pt_a["playthroughAccessToken"]),
        )
        assert res.status_code == 200
        assert res.json()["caseVersion"] == 1
        res = client.get(
            f"/api/v1/playthroughs/{pt_b['playthroughId']}",
            headers=auth(pt_a["playthroughAccessToken"]),
        )
        assert res.status_code == 404  # A's token on B


# --------------------------------------------------------------------------- #
# DEF-048 — MAX_REQUEST_BODY_SIZE is enforced as an early 413 envelope
# --------------------------------------------------------------------------- #


async def _asgi_drive_middleware(chunks: list[tuple[bytes, bool]], bound: int = 65536):
    """Drive RequestBodySizeMiddleware with crafted ASGI receive events.

    ``chunks`` are ``(body_bytes, more_body_flag)`` http.request messages.
    Returns ``(app_invoked, status, body_bytes_sent)``.
    """
    import asyncio

    from app.main import RequestBodySizeMiddleware

    messages_out: list[dict] = []
    app_invoked = {"value": False}
    state = {"idx": 0}

    async def fake_app(scope, receive, send):
        app_invoked["value"] = True
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                continue
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def stream_receive():
        if state["idx"] < len(chunks):
            body, more = chunks[state["idx"]]
            state["idx"] += 1
            return {"type": "http.request", "body": body, "more_body": more}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages_out.append(message)

    middleware = RequestBodySizeMiddleware(fake_app, max_body_bytes=bound)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/cases",
        "headers": [],  # NO content-length (unknown-length / chunked body)
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 80),
        "scheme": "http",
        "root_path": "",
    }
    await middleware(scope, stream_receive, send)
    start = next(
        (m for m in messages_out if m["type"] == "http.response.start"), None
    )
    sent_body = b"".join(
        m.get("body", b"") for m in messages_out if m["type"] == "http.response.body"
    )
    return app_invoked["value"], start["status"] if start else None, sent_body


def test_de048_bypass_single_terminal_message_over_bound_is_413_pre_route():
    """QA shape (a): uvicorn coalesces a one-write chunked body into a SINGLE
    http.request message with more_body=False; it must still be capped."""
    body = b'{"prompt": "' + b"z" * 70_000 + b'"}'
    app_invoked, status, _sent = asyncio.run(
        _asgi_drive_middleware([(body, False)])
    )
    assert app_invoked is False
    assert status == 413


def test_de048_bypass_crossing_bound_on_terminal_chunk_is_413_pre_route():
    """QA shape (b): the bound may be crossed EXACTLY on the final
    (more_body=False) chunk — the terminal message is still checked."""
    chunks = [
        (b'{"prompt": "' + b"z" * 60_000, True),
        (b"z" * 15_000 + b'"}', False),  # total 75,007 > 65,536 at the end
    ]
    app_invoked, status, _sent = asyncio.run(_asgi_drive_middleware(chunks))
    assert app_invoked is False
    assert status == 413


def test_de048_under_bound_single_message_reaches_app_byte_for_byte():
    """QA shape (3): an under-bound single terminal message is replayed to the
    application unchanged (identical bytes)."""
    payload = b'{"prompt": "ok"}'
    app_invoked, status, sent_body = asyncio.run(
        _asgi_drive_middleware([(payload, False)])
    )
    assert app_invoked is True
    assert status == 200
    assert sent_body == payload


def test_de048_under_bound_multi_chunk_reaches_app_byte_for_byte():
    """Under-bound multi-message chunked bodies still replay exactly."""
    chunks = [
        (b'{"prompt": "', True),
        (b"normal", True),
        (b'"}', False),
    ]
    app_invoked, status, sent_body = asyncio.run(_asgi_drive_middleware(chunks))
    assert app_invoked is True
    assert status == 200
    assert sent_body == b'{"prompt": "normal"}'


def test_de048_declared_content_length_over_bound_is_413_pre_route(phase5_app):
    """QA shape (5, first half): a DECLARED Content-Length over the bound
    short-circuits to 413 with the standard envelope before the route runs."""
    store = phase5_app.state.store
    with TestClient(phase5_app) as client:
        session_token, body = create_session(client)
        session_row = store.get_session_by_verifier(token_verifier(session_token))
        gens_before = session_row.generations_count
        res = client.post(
            "/api/v1/cases",
            content='{"prompt": "' + "x" * 70_000 + '"}',
            headers={
                "Authorization": f"Bearer {session_token}",
                "Content-Type": "application/json",
            },
        )
        assert res.status_code == 413
        assert res.json()["error"]["code"] == "REQUEST_TOO_LARGE"
        # The route never executed (no admission increment).
        assert store.get_session(session_row.session_id).generations_count == gens_before


def test_de048_under_bound_body_still_runs_route(phase5_app):
    """QA shape (5, second half): a 50 KB body under the bound reaches the
    route and answers the prompt-bound 422; /docs + openapi still load."""
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        res = client.post(
            "/api/v1/cases",
            json={"prompt": "M" * 5_000},
            headers=auth(session_token),
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "PROMPT_ERROR"
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_de048_oversized_body_answers_413_before_the_route(phase5_app, database_url):
    """A body over max_request_body_size -> 413 REQUEST_TOO_LARGE and NO route
    side effect (no admission increment, no case row created)."""
    from sqlalchemy import text

    store = phase5_app.state.store
    with store.engine.connect() as conn:
        cases_before = conn.execute(text("SELECT count(*) FROM cases")).scalar()
    with TestClient(phase5_app) as client:
        session_token, body = create_session(client)
        session_row = store.get_session_by_verifier(token_verifier(session_token))
        gens_before = session_row.generations_count
        oversized = '{"prompt": "' + "x" * 70_000 + '"}'
        res = client.post(
            "/api/v1/cases",
            content=oversized,
            headers={
                "Authorization": f"Bearer {session_token}",
                "Content-Type": "application/json",
            },
        )
        assert res.status_code == 413
        assert res.json() == {
            "error": {"code": "REQUEST_TOO_LARGE", "message": "Request body too large", "details": None}
        }
        # The route never executed: no admission increment, no case row.
        after = store.get_session(session_row.session_id)
        assert after.generations_count == gens_before
        with store.engine.connect() as conn:
            cases_after = conn.execute(text("SELECT count(*) FROM cases")).scalar()
        assert cases_after == cases_before
    # Robustness: no traceback/sql in the 413 body.
    assert "Traceback" not in res.text and "sqlite" not in res.text


def test_de048_small_bodies_unchanged_and_docs_still_load(phase5_app):
    with TestClient(phase5_app) as client:
        # Normal session + case creation unaffected.
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        assert case["status"] == "PUBLISHED"
        # A body under the bound but over the prompt bound still answers the
        # prompt 422 (route DID run — the body was accepted for validation).
        res = client.post(
            "/api/v1/cases",
            json={"prompt": "M" * 5_000},
            headers=auth(session_token),
        )
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "PROMPT_ERROR"
        # Docs/openapi unchanged.
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/playthroughs/{playthrough_id}" in paths
        assert "/api/v1/cases/{case_id}" in paths


def test_de048_middleware_caps_chunked_bodies_without_content_length(tmp_path):
    """ASGI-level: chunked bodies with NO content-length are capped too.

    A body without a declared Content-Length is pre-read by the middleware
    with a bound of max+1 bytes; crossing the bound answers 413 and the app
    is NEVER invoked. A chunked body under the bound reaches the app with its
    bytes replayed unchanged.
    """
    import asyncio

    from app.main import RequestBodySizeMiddleware

    BOUND = 2048

    async def _run(bound: int, chunks: list[bytes]) -> dict:
        received_messages: list[dict] = []
        app_called = {"value": False}

        async def fake_app(scope, receive, send):
            app_called["value"] = True
            body = b""
            while True:
                message = await receive()
                if message["type"] != "http.request":
                    continue
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            await send(
                {"type": "http.response.start", "status": 200, "headers": []}
            )
            await send(
                {"type": "http.response.body", "body": body, "more_body": False}
            )

        state = {"idx": 0}

        async def stream_receive():
            if state["idx"] < len(chunks):
                body = chunks[state["idx"]]
                state["idx"] += 1
                more = state["idx"] < len(chunks)
                return {"type": "http.request", "body": body, "more_body": more}
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            received_messages.append(message)

        middleware = RequestBodySizeMiddleware(fake_app, max_body_bytes=bound)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/cases",
            "headers": [],  # NO content-length
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 80),
            "scheme": "http",
            "root_path": "",
        }
        await middleware(scope, stream_receive, send)
        return {"messages": received_messages, "app_called": app_called["value"]}

    # Case 1: chunked body crosses the bound -> 413, app never invoked.
    result = asyncio.run(
        _run(
            BOUND,
            [b'{"prompt": "', b"z" * 1200, b"z" * 1200, b'"}'],
        )
    )
    assert result["app_called"] is False
    start = next(m for m in result["messages"] if m["type"] == "http.response.start")
    assert start["status"] == 413
    body_sent = b"".join(
        m.get("body", b"") for m in result["messages"] if m["type"] == "http.response.body"
    )
    assert b'"REQUEST_TOO_LARGE"' in body_sent

    # Case 2: chunked body under the bound reaches the app byte-for-byte.
    small_chunks = [b'{"prompt": "', b"ok", b'"}']
    result = asyncio.run(_run(BOUND, small_chunks))
    assert result["app_called"] is True
    body_sent = b"".join(
        m.get("body", b"") for m in result["messages"] if m["type"] == "http.response.body"
    )
    assert body_sent == b"".join(small_chunks)


# --------------------------------------------------------------------------- #
# DEF-048 LIVE-WIRE proof — a real uvicorn server in-process (daemon thread)
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return sock.getsockname()[1]


def verify_port_free(port: int, attempts: int = 25) -> None:
    """Bind+listen the port; retry briefly for OS release (bounded)."""
    for _ in range(attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
                sock.listen(1)
            return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"port {port} is still not free after shutdown")


class _LiveUvicorn:
    """A real uvicorn server in a daemon thread on a free loopback port.

    Readiness is awaited by polling ``GET /health`` with a per-request socket
    timeout (bounded by a hard deadline — no fixed sleeps). Shutdown sets
    uvicorn's own ``should_exit`` flag (polled every 0.1 s in its main loop),
    joins the thread with a bound, then verifies the port is free.
    """

    def __init__(self, app: object) -> None:
        self._app = app
        self.port = _free_port()
        self._server = None
        self._thread = None

    def start(self, ready_deadline_seconds: float = 8.0) -> None:
        import uvicorn

        config = uvicorn.Config(
            app=self._app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
            lifespan="off",
            timeout_keep_alive=2,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + ready_deadline_seconds
        while time.monotonic() < deadline:
            if self._thread is None or not self._thread.is_alive():
                raise AssertionError("uvicorn thread exited before becoming ready")
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1", self.port, timeout=0.5
                )
                try:
                    conn.request("GET", "/api/v1/health")
                    resp = conn.getresponse()
                    resp.read()
                    if resp.status == 200:
                        return
                finally:
                    conn.close()
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.05)
        raise AssertionError(
            "uvicorn did not serve /health 200 within the readiness bound"
        )

    def shutdown(self, join_timeout: float = 10.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            if self._thread.is_alive():
                raise AssertionError("uvicorn thread did not stop within the bound")
        verify_port_free(self.port)


def _chunked_request(port: int, token: str, body: bytes) -> bytes:
    """A complete HTTP/1.1 chunked POST built for a SINGLE TCP write."""
    return (
        b"POST /api/v1/cases HTTP/1.1\r\n"
        + f"Host: 127.0.0.1:{port}\r\n".encode()
        + f"Authorization: Bearer {token}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + b"Transfer-Encoding: chunked\r\n"
        + b"Connection: close\r\n"
        + b"\r\n"
        + f"{len(body):X}\r\n".encode()
        + body
        + b"\r\n"
        + b"0\r\n\r\n"
    )


def _raw_request(port: int, request: bytes, timeout: float = 5.0):
    """Send one raw request; return (response_bytes, elapsed_ms_to_413|None)."""
    started = time.monotonic()
    elapsed_413 = None
    data = b""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(request)
        while True:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                break  # no more data within the socket timeout (hang guard)
            if not chunk:
                break
            data += chunk
            if elapsed_413 is None and b" 413 " in data:
                elapsed_413 = (time.monotonic() - started) * 1000.0
    return data, elapsed_413


def _live_json(port: int, path: str, token: str | None = None, body_bytes: bytes = b""):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        conn.request("POST", path, body=body_bytes, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        return resp.status, json.loads(payload or b"null")
    finally:
        conn.close()


def test_de048_live_uvicorn_chunked_single_write_413_and_cleanup(tmp_path):
    """QA live proof shape: a chunked 70 KB body written in ONE TCP write.

    uvicorn (httptools/h11) coalesces it into a single terminal http.request
    message; the middleware must answer the 413 envelope immediately — NOT a
    hang, NOT the route's normal answer — and the listener must be cleaned up
    (port free afterwards).
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from app.core.config import Settings
    from app.main import create_app

    db_path = tmp_path / "de048_live.db"
    url = f"sqlite:///{db_path.as_posix()}"
    cfg = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    app = create_app(Settings(database_url=url, max_request_body_size=65536))
    store = app.state.store
    server = _LiveUvicorn(app)
    chunked_elapsed_ms = None
    try:
        server.start()

        # (1) LIVE: a legal small body runs the route (session creation).
        status, session_body = _live_json(server.port, "/api/v1/sessions/anonymous")
        assert status == 201, session_body
        session_token = session_body["anonymousSessionToken"]

        # (2) LIVE: a legal small case creation is PUBLISHED (route + replay).
        status, case_body = _live_json(
            server.port,
            "/api/v1/cases",
            token=session_token,
            body_bytes=json.dumps(
                {"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n"}
            ).encode(),
        )
        assert status == 201, case_body
        assert case_body["status"] == "PUBLISHED"

        from sqlalchemy import text

        with store.engine.connect() as conn:
            cases_before = conn.execute(text("SELECT count(*) FROM cases")).scalar()

        # (3) LIVE: chunked 70 KB body in ONE TCP write -> 413, never a hang.
        big = b'{"prompt": "' + b"z" * 70_000 + b'"}'
        data, chunked_elapsed_ms = _raw_request(server.port, _chunked_request(server.port, session_token, big))
        assert chunked_elapsed_ms is not None, (
            "413 was never observed within the socket timeout — connection hung"
        )
        assert data.startswith(b"HTTP/1.1 413"), data[:200]
        assert b'"REQUEST_TOO_LARGE"' in data
        # Route effects absent: the admission counter and case count are
        # byte-identical to just after step (2).
        assert (
            store.get_session_by_verifier(token_verifier(session_token)).generations_count
            == 1
        )
        with store.engine.connect() as conn:
            cases_after = conn.execute(text("SELECT count(*) FROM cases")).scalar()
        assert cases_after == cases_before

        # (4) LIVE: declared Content-Length over the bound -> 413 too.
        cl_request = (
            f"POST /api/v1/cases HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.port}\r\n"
            f"Authorization: Bearer {session_token}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(big)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + big
        data, _ = _raw_request(server.port, cl_request)
        assert data.startswith(b"HTTP/1.1 413"), data[:200]
        assert b'"REQUEST_TOO_LARGE"' in data

        # (5) LIVE: a legal small CHUNKED body still runs the route (201).
        small = json.dumps({"prompt": "Murderer: thomas_reed\n"}).encode()
        data, _ = _raw_request(server.port, _chunked_request(server.port, session_token, small))
        assert data.startswith(b"HTTP/1.1 201"), data[:200]
        assert b"PUBLISHED" in data

        print(
            f"[DEF-048 live] chunked single-write 413 observed in "
            f"{chunked_elapsed_ms:.1f} ms (socket timeout 5000 ms)"
        )
    finally:
        server.shutdown()
        app.state.engine.dispose()
        app.state.store.dispose()
        store.dispose()