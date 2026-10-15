"""QA independent probe for Phase43-Fix1 / DEF-074A (hermetic, zero network).

This is QA's OWN probe. It does not import or reuse the developer's
``test_phase43_fix1_request_body_receive.py``. It drives the production
``RequestBodySizeMiddleware`` with crafted ASGI transports and records the exact
downstream ``receive()`` sequence, whether the ORIGINAL transport receive is
consulted after replay exhaustion, send traffic, and event-loop liveness.

Point ``PD_BACKEND`` at a different backend tree to run the identical probe
against baseline code (proving the regression is real)::

    set PD_BACKEND=C:\\path\\to\\baseline\\backend
    python independent_probe.py

Exit code 0 == every independent check passed; 1 == at least one failed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import deque

BACKEND = os.environ.get("PD_BACKEND", r"C:\Users\Fujitsu\coding\procedural-detective\backend")
sys.path.insert(0, BACKEND)

from app.main import RequestBodySizeMiddleware  # noqa: E402

BOUND = 1024
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    print(f"  [{'OK  ' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILURES.append(label)


def http_scope(method: str = "POST", path: str = "/api/v1/cases", headers=None) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [tuple(h) for h in headers] if headers else [],
        "query_string": b"",
        "server": ("qa", 80),
        "client": ("qa", 80),
        "scheme": "http",
        "root_path": "",
    }


def cl_scope(n: int, **kw) -> dict:
    return http_scope(headers=[(b"content-length", str(n).encode("ascii"))], **kw)


def req(body: bytes, more: bool) -> dict:
    return {"type": "http.request", "body": body, "more_body": more}


class Transport:
    """Scripted ASGI receive; after the script -> real sticky ``http.disconnect``."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.i = 0
        self.calls = 0

    async def receive(self):
        self.calls += 1
        if self.i < len(self.script):
            m = self.script[self.i]
            self.i += 1
            return m
        await asyncio.sleep(0)  # cooperative yield; real transport semantics
        return {"type": "http.disconnect"}


def collect_send():
    out: list[dict] = []

    async def send(m):
        out.append(m)

    return out, send


def drive(scope, transport, downstream, send):
    mw = RequestBodySizeMiddleware(downstream, max_body_bytes=BOUND)
    return mw(scope, transport.receive, send)


# --------------------------------------------------------------------------- #
# 1. Unknown-length terminal http.request then REAL http.disconnect
# --------------------------------------------------------------------------- #
def probe_replay_exhaustion() -> None:
    print("=== 1. replay exhaustion forwards the REAL transport receive ===")
    observed: list[dict] = []

    async def downstream(scope, receive, send):
        observed.append(await receive())  # replayed terminal http.request
        observed.append(await receive())  # must forward REAL http.disconnect
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    t = Transport([req(b"", False)])
    sent, send = collect_send()
    asyncio.run(drive(http_scope(), t, downstream, send))

    types = [m["type"] for m in observed]
    print("  downstream observed:", types, "transport.calls:", t.calls)
    check(types == ["http.request", "http.disconnect"], "downstream 2nd receive is real http.disconnect")
    check(observed[1] == {"type": "http.disconnect"}, "disconnect dict is passed through unchanged")
    check(t.calls == 2, "original transport receive WAS consulted a 2nd time (not synthesized)")
    check(bool(sent), "downstream response completed")


# --------------------------------------------------------------------------- #
# 2. Starlette-style disconnect listener must progress / loop stays alive
# --------------------------------------------------------------------------- #
def probe_disconnect_listener_liveness() -> None:
    print("=== 2. disconnect listener progresses; event loop not starved ===")
    disconnect_seen = {"v": False}
    listen_calls = {"n": 0}
    hb = {"n": 0}
    CAP = 200_000  # bounded ONLY so a regressed middleware cannot hang the probe

    async def heartbeat(stop: asyncio.Event):
        while not stop.is_set():
            hb["n"] += 1
            await asyncio.sleep(0)

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"asset", "more_body": False})
        for _ in range(CAP):
            m = await receive()
            listen_calls["n"] += 1
            if m["type"] == "http.disconnect":
                disconnect_seen["v"] = True
                return

    async def run():
        stop = asyncio.Event()
        t = asyncio.create_task(heartbeat(stop))
        try:
            await drive(http_scope(method="GET", path="/assets/x.js"),
                        Transport([req(b"", False)]), downstream, lambda m: asyncio.sleep(0))
        finally:
            stop.set()
            await t

    asyncio.run(run())
    print(f"  heartbeat ticks: {hb['n']}  listen calls: {listen_calls['n']}  disconnect: {disconnect_seen['v']}")
    check(disconnect_seen["v"] is True, "listener observed the real http.disconnect")
    check(listen_calls["n"] < CAP, "listener stopped (no infinite synthetic loop)")
    check(hb["n"] > 0, "event loop kept running (heartbeat ticked)")


# --------------------------------------------------------------------------- #
# 3. Chunked under-bound bodies forwarded byte-for-byte (unchanged)
# --------------------------------------------------------------------------- #
def probe_chunked_under_bound() -> None:
    print("=== 3. chunked under-bound body forwarded byte-for-byte ===")
    chunks = [b'{"a":', b"1", b',"b":[', b"2,3", b"]}"]
    script = [req(c, True) for c in chunks[:-1]] + [req(chunks[-1], False)]
    got: list[bytes] = []

    async def downstream(scope, receive, send):
        while True:
            m = await receive()
            if m["type"] != "http.request":
                break
            got.append(m["body"])
            if not m.get("more_body", False):
                break

    asyncio.run(drive(http_scope(), Transport(script), downstream, lambda m: asyncio.sleep(0)))
    check(got == chunks, f"chunk order preserved {got!r}")
    check(b"".join(got) == b"".join(chunks), "chunk bytes exact")


# --------------------------------------------------------------------------- #
# 4. Oversized terminal message -> 413, app NOT invoked, single receive
# --------------------------------------------------------------------------- #
def probe_oversized_terminal() -> None:
    print("=== 4. terminal over-bound -> 413 pre-route, app never invoked ===")
    invoked = {"n": 0}

    async def app(scope, receive, send):
        invoked["n"] += 1

    t = Transport([req(b"z" * (BOUND + 1), False)])
    sent, send = collect_send()
    asyncio.run(drive(http_scope(), t, app, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    status = starts[0]["status"] if starts else None
    check(invoked["n"] == 0, "app not invoked for oversized body")
    check(status == 413, f"413 returned (got {status})")
    check(t.calls == 1, f"terminal over-bound does NOT call receive again (calls={t.calls})")


# --------------------------------------------------------------------------- #
# 5. Oversized mid-stream (more_body=True) -> drain then 413
# --------------------------------------------------------------------------- #
def probe_oversized_chunked() -> None:
    print("=== 5. mid-stream over-bound -> drain then 413 ===")
    invoked = {"n": 0}

    async def app(scope, receive, send):
        invoked["n"] += 1

    script = [req(b"a" * 600, True), req(b"b" * 600, True), req(b"c", False)]
    t = Transport(script)
    sent, send = collect_send()
    asyncio.run(drive(http_scope(), t, app, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    status = starts[0]["status"] if starts else None
    check(invoked["n"] == 0, "app not invoked")
    check(status == 413, f"413 returned (got {status})")
    check(t.calls == 3, f"remaining body drained (calls={t.calls})")


# --------------------------------------------------------------------------- #
# 6. Declared Content-Length over bound -> 413 without touching receive
# --------------------------------------------------------------------------- #
def probe_declared_over_bound() -> None:
    print("=== 6. declared Content-Length over-bound -> 413, no receive ===")
    invoked = {"n": 0}

    async def app(scope, receive, send):
        invoked["n"] += 1

    t = Transport([])
    sent, send = collect_send()
    asyncio.run(drive(cl_scope(BOUND + 1), t, app, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    check(invoked["n"] == 0, "app not invoked")
    check(starts and starts[0]["status"] == 413, "413 returned")
    check(t.calls == 0, f"no body read for declared length (calls={t.calls})")


# --------------------------------------------------------------------------- #
# 7. Normal JSON POST declared Content-Length reaches app unchanged
# --------------------------------------------------------------------------- #
def probe_normal_json_post() -> None:
    print("=== 7. normal JSON POST declared CL forwarded byte-for-byte ===")
    payload = b'{"interaction":""}'
    seen: dict = {}

    async def app(scope, receive, send):
        seen["msg"] = await receive()

    async def outer():
        return req(payload, False)

    asyncio.run(
        RequestBodySizeMiddleware(app, max_body_bytes=BOUND)(
            cl_scope(len(payload)), outer, lambda m: asyncio.sleep(0)
        )
    )
    check(
        seen.get("msg") == {"type": "http.request", "body": payload, "more_body": False},
        "body arrives unchanged",
    )


def main() -> int:
    print(f"backend tree: {BACKEND}")
    print(f"RequestBodySizeMiddleware from: {sys.modules['app.main'].__file__}")
    print()
    probe_replay_exhaustion()
    probe_disconnect_listener_liveness()
    probe_chunked_under_bound()
    probe_oversized_terminal()
    probe_oversized_chunked()
    probe_declared_over_bound()
    probe_normal_json_post()
    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} INDEPENDENT CHECK(S) FAILED")
        for f in FAILURES:
            print("  FAIL:", f)
        return 1
    print("RESULT: ALL INDEPENDENT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())