"""Phase 43-Fix1 / DEF-074A — RequestBodySizeMiddleware receive semantics.

PROVEN ROOT CAUSE (pre-fix): once the buffered replay deque emptied,
``RequestBodySizeMiddleware._streaming_capped_call.replayed_receive`` returned a
synthetic terminal ``http.request`` FOREVER instead of forwarding the transport
receive. A Starlette disconnect listener (``Response`` / ``FileResponse``)
therefore never observed the real ``http.disconnect`` and hot-looped, pinning
the event loop and stalling every subsequent request.

These tests are hermetic (no network, no sleeps where exact state can be
awaited). Test 1 and test 2 FAIL on the pre-fix code for the proven reason and
pass after the one-line fix::

    async def replayed_receive():
        if messages:
            return messages.popleft()
        return await receive()
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from phase5_helpers import create_case, create_session  # noqa: E402

# The middleware bound for the hermetic ASGI harnesses (small == fast).
_BOUND = 1024

# Safety cap ONLY: pre-fix the disconnect listener never terminates, so the
# bounded loop proves the hot-loop without hanging the test process.
_MAX_LISTEN_ITERATIONS = 64


def _scope(*, method: str = "POST", path: str = "/api/v1/cases", headers=None) -> dict:
    """A minimal HTTP ASGI scope. No Content-Length unless caller supplies it."""
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": list(headers) if headers else [],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 80),
        "scheme": "http",
        "root_path": "",
    }


def _sticky_outer_receive(payload: bytes = b"") -> tuple:
    """(receive, state): one terminal ``http.request`` then real disconnects.

    After the terminal body message the transport would only ever deliver
    ``http.disconnect`` (sticky), exactly like uvicorn once a bodyless/closed
    request is complete.
    """
    state = {"request_sent": False}

    async def receive():
        if not state["request_sent"]:
            state["request_sent"] = True
            return {"type": "http.request", "body": payload, "more_body": False}
        # A real transport only delivers the disconnect after a wait; yield once
        # so a concurrently scheduled heartbeat can be observed (cooperative
        # yield, not a wall-clock sleep).
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    return receive, state


# --------------------------------------------------------------------------- #
# 1. Unknown-length receive pass-through
# --------------------------------------------------------------------------- #


def test_de074a_downstream_second_receive_returns_real_disconnect():
    """An unknown-length terminal message is replayed; the NEXT receive() must
    forward the real transport ``http.disconnect`` — not a synthetic request."""
    from app.main import RequestBodySizeMiddleware

    observed: list[dict] = []

    async def downstream_app(scope, receive, send):
        observed.append(await receive())  # replayed terminal http.request
        observed.append(await receive())  # must be the REAL http.disconnect
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    outer_receive, _ = _sticky_outer_receive()

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    middleware = RequestBodySizeMiddleware(downstream_app, max_body_bytes=_BOUND)
    asyncio.run(middleware(_scope(), outer_receive, send))

    assert [m["type"] for m in observed] == ["http.request", "http.disconnect"]
    # The real disconnect is passed through unchanged (no synthesized keys).
    assert observed[1] == {"type": "http.disconnect"}
    assert sent  # the downstream response ran to completion


# --------------------------------------------------------------------------- #
# 2. FileResponse / disconnect-listener regression (no hot-loop)
# --------------------------------------------------------------------------- #


def test_de074a_disconnect_listener_sees_disconnect_without_hot_loop():
    """An unknown-length/bodyless request against a downstream consumer that
    awaits disconnect (Starlette ``FileResponse``/``Response`` listener shape):
    the response completes, an event-loop heartbeat keeps running, and the
    listener observes the real ``http.disconnect`` within a BOUNDED number of
    receive() calls. Pre-fix the synthetic terminal message spins forever."""
    from app.main import RequestBodySizeMiddleware

    disconnect_seen = {"value": False}
    listen_calls = {"count": 0}
    response_completed = {"value": False}
    heartbeat = {"count": 0}

    async def heartbeat_task(stop_event: asyncio.Event):
        while not stop_event.is_set():
            heartbeat["count"] += 1
            await asyncio.sleep(0)  # cooperative yield == loop keeps running

    async def downstream_app(scope, receive, send):
        # Mirror ``starlette.responses.Response.listen_for_disconnect``: stream
        # the body, then await receive() until a real http.disconnect. The loop
        # is bounded ONLY so a regressed middleware cannot hang the test.
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"asset", "more_body": False})
        response_completed["value"] = True
        for _ in range(_MAX_LISTEN_ITERATIONS):
            message = await receive()
            listen_calls["count"] += 1
            if message["type"] == "http.disconnect":
                disconnect_seen["value"] = True
                return

    outer_receive, _ = _sticky_outer_receive()
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    middleware = RequestBodySizeMiddleware(downstream_app, max_body_bytes=_BOUND)

    async def run():
        stop_event = asyncio.Event()
        hb = asyncio.create_task(heartbeat_task(stop_event))
        try:
            await middleware(_scope(), outer_receive, send)
        finally:
            stop_event.set()
            await hb

    asyncio.run(run())

    assert response_completed["value"] is True
    assert disconnect_seen["value"] is True  # real http.disconnect forwarded
    # Bounded: post-fix the listener stops after the replay + disconnect.
    assert listen_calls["count"] < _MAX_LISTEN_ITERATIONS
    assert heartbeat["count"] > 0  # event loop never starved
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert body == b"asset"


# --------------------------------------------------------------------------- #
# 3. Follow-up health request remains responsive
# --------------------------------------------------------------------------- #


def test_de074a_followup_health_after_disconnect_listener():
    """After the FileResponse-equivalent request completes, a GET /health-style
    probe (unknown-length, no body) answers 200 within a short bound."""
    from app.main import RequestBodySizeMiddleware

    async def file_like_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"asset", "more_body": False})
        for _ in range(_MAX_LISTEN_ITERATIONS):
            message = await receive()
            if message["type"] == "http.disconnect":
                return

    async def health_app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] == "http.request" and not message.get("more_body", False):
                break
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {"type": "http.response.body", "body": b'{"status":"ok"}', "more_body": False}
        )

    async def run():
        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        file_receive, _ = _sticky_outer_receive()
        await RequestBodySizeMiddleware(
            file_like_app, max_body_bytes=_BOUND
        )(_scope(), file_receive, send)

        sent.clear()
        health_receive, _ = _sticky_outer_receive()
        await asyncio.wait_for(
            RequestBodySizeMiddleware(health_app, max_body_bytes=_BOUND)(
                _scope(method="GET", path="/api/v1/health"), health_receive, send
            ),
            timeout=5.0,
        )
        return sent

    sent = asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert body == b'{"status":"ok"}'


# --------------------------------------------------------------------------- #
# 5. No regression to normal JSON POST bodies
# --------------------------------------------------------------------------- #


def test_de074a_declared_content_length_json_body_reaches_app_unchanged():
    """A declared Content-Length body (the normal JSON POST path) is forwarded
    byte-for-byte to the application, untouched by the middleware."""
    from app.main import RequestBodySizeMiddleware

    payload = b'{"interaction":""}'
    received: dict = {}

    async def downstream_app(scope, receive, send):
        received["message"] = await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def outer_receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        pass

    scope = _scope(headers=[(b"content-length", str(len(payload)).encode("ascii"))])
    asyncio.run(
        RequestBodySizeMiddleware(downstream_app, max_body_bytes=_BOUND)(
            scope, outer_receive, send
        )
    )
    assert received["message"] == {
        "type": "http.request",
        "body": payload,
        "more_body": False,
    }


def test_de074a_normal_json_post_case_creation_unchanged(phase5_app):
    """End-to-end: a normal JSON POST (anonymous session + case creation) still
    succeeds through the real middleware stack."""
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        assert case["status"] == "PUBLISHED"
