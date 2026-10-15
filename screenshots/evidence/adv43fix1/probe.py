"""Adv43-Fix1 hostile probes (hermetic, zero network).

Drives RequestBodySizeMiddleware with crafted ASGI transports and records the
exact downstream receive() sequence, whether the ORIGINAL transport receive is
reached, send traffic, and app invocation.

Run from repo root (python 3.12.10):
    python screenshots/evidence/adv43fix1/probe.py
"""
import asyncio
import json
import sys
import time
from collections import deque

sys.path.insert(0, r"C:\Users\Fujitsu\coding\procedural-detective\backend")
from app.main import RequestBodySizeMiddleware  # noqa: E402

BOUND = 1024


def http_scope(method="POST", path="/api/v1/cases", headers=None):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [tuple(h) for h in headers] if headers else [],
        "query_string": b"",
        "server": ("t", 80),
        "client": ("t", 80),
        "scheme": "http",
        "root_path": "",
    }


def cl_scope(n, **kw):
    return http_scope(headers=[(b"content-length", str(n).encode())], **kw)


class Transport:
    """A scripted ASGI receive with sticky disconnect after the script."""

    def __init__(self, script, sticky=True):
        self.script = list(script)
        self.i = 0
        self.receive_calls = 0
        self.sticky = sticky

    async def receive(self):
        self.receive_calls += 1
        if self.i < len(self.script):
            m = self.script[self.i]
            self.i += 1
            return m
        if self.sticky:
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()  # never returns


def req(body, more):
    return {"type": "http.request", "body": body, "more_body": more}


async def drive(scope, transport, downstream, send):
    mw = RequestBodySizeMiddleware(downstream, max_body_bytes=BOUND)
    await mw(scope, transport.receive, send)


def collect_send():
    out = []

    async def send(m):
        out.append(m)

    return out, send


# --------------------------------------------------------------------------- #
# ATTACK 1 — replay-exhaustion semantics
# --------------------------------------------------------------------------- #
def attack1():
    print("=== ATTACK 1: replay exhaustion semantics ===")
    observed = []
    transport = Transport([req(b"abc", False)])
    accepted = {"n": 0}

    async def downstream(scope, receive, send):
        accepted["n"] += 1
        for _ in range(6):
            m = await receive()
            observed.append((m["type"], m.get("body"), m.get("more_body")))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    sent, send = collect_send()
    asyncio.run(drive(http_scope(), transport, downstream, send))
    print("downstream observed:", observed)
    print("transport.receive_calls:", transport.receive_calls,
          "(1 script + 5 forwarded disconnects)")
    print("app_invoked:", accepted["n"], "sent:", [m["type"] for m in sent])
    print("post-replay all exactly http.disconnect:",
          all(o == ("http.disconnect", None, None) for o in observed[1:]))
    print("no synthesized http.request after replay:",
          all(o[0] != "http.request" for o in observed[1:]))
    print()


# --------------------------------------------------------------------------- #
# ATTACK 2 — disconnect timing
# --------------------------------------------------------------------------- #
def attack2():
    print("=== ATTACK 2: disconnect timing ===")

    observed = []
    transport = Transport([{"type": "http.disconnect"}])

    async def d_a(scope, receive, send):
        for _ in range(3):
            observed.append((await receive())["type"])

    sent, send = collect_send()
    asyncio.run(drive(http_scope(), transport, d_a, send))
    print("2a first-message disconnect -> observed:", observed,
          "transport_calls:", transport.receive_calls)

    observed = []
    transport = Transport([req(b"ab", True), {"type": "http.disconnect"}])

    async def d_b(scope, receive, send):
        for _ in range(4):
            m = await receive()
            observed.append((m["type"], m.get("body"), m.get("more_body")))

    sent, send = collect_send()
    asyncio.run(drive(http_scope(), transport, d_b, send))
    print("2b mid-body disconnect -> observed:", observed,
          "transport_calls:", transport.receive_calls)

    app_invoked = {"v": 0}

    async def d_c(scope, receive, send):
        app_invoked["v"] += 1

    transport = Transport([req(b"z" * (BOUND + 5), True), {"type": "http.disconnect"}])
    sent, send = collect_send()
    asyncio.run(drive(http_scope(), transport, d_c, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    print("2c oversize+mid-drain disconnect -> app_invoked:", app_invoked["v"],
          "status:", starts[0]["status"] if starts else None,
          "transport_calls:", transport.receive_calls)

    class SendRaises:
        def __init__(self):
            self.calls = []

        async def __call__(self, m):
            self.calls.append(m["type"])
            raise RuntimeError("client disconnected (simulated send failure)")

    s = SendRaises()
    transport = Transport([req(b"z" * (BOUND + 5), True), {"type": "http.disconnect"}])
    try:
        asyncio.run(drive(http_scope(), transport, d_c, s))
        print("2c2 send-after-disconnect: no exception (send calls:", s.calls, ")")
    except Exception as e:  # noqa: BLE001
        print("2c2 send-after-disconnect RAISED:", type(e).__name__, e,
              "send calls:", s.calls)

    observed = []
    transport = Transport([req(b"x", False), {"type": "http.disconnect"},
                           {"type": "http.disconnect"}, {"type": "http.disconnect"}])

    async def d_d(scope, receive, send):
        for _ in range(4):
            observed.append((await receive())["type"])

    sent, send = collect_send()
    asyncio.run(drive(http_scope(), transport, d_d, send))
    print("2d multiple disconnects -> observed:", observed)
    print()


# --------------------------------------------------------------------------- #
# ATTACK 3 — oversized / chunked invariants
# --------------------------------------------------------------------------- #
def attack3():
    print("=== ATTACK 3: oversized/chunked invariants ===")
    app_invoked = {"v": 0}

    async def app(scope, receive, send):
        app_invoked["v"] += 1

    transport = Transport([])
    sent, send = collect_send()
    asyncio.run(drive(cl_scope(BOUND + 1), transport, app, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    print("3a declared CL over -> app_invoked:", app_invoked["v"],
          "receive_calls:", transport.receive_calls,
          "status:", starts[0]["status"] if starts else None)

    transport = Transport([req(b"a" * 600, True), req(b"b" * 600, True), req(b"c", False)])
    sent, send = collect_send()
    asyncio.run(drive(http_scope(), transport, app, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    print("3b chunked cross mid-stream -> app_invoked:", app_invoked["v"],
          "status:", starts[0]["status"] if starts else None,
          "transport_calls:", transport.receive_calls)

    transport = Transport([req(b"z" * (BOUND + 1), False), {"type": "http.disconnect"}])
    sent, send = collect_send()
    asyncio.run(drive(http_scope(), transport, app, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    print("3c terminal over-bound -> app_invoked:", app_invoked["v"],
          "status:", starts[0]["status"] if starts else None,
          "transport_calls (expect 1):", transport.receive_calls)

    transport = Transport([req(b"z" * BOUND, False)])
    sent, send = collect_send()
    asyncio.run(drive(http_scope(), transport, app, send))
    print("3d exactly at bound -> app_invoked:", app_invoked["v"])

    transport = Transport([req(b"z" * (BOUND + 1), False)])
    sent, send = collect_send()
    asyncio.run(drive(http_scope(), transport, app, send))
    starts = [m for m in sent if m["type"] == "http.response.start"]
    print("3e bound+1 terminal -> app_invoked:", app_invoked["v"],
          "status:", starts[0]["status"] if starts else None)

    transport = Transport([req(b"z" * BOUND, False)])
    sent, send = collect_send()
    asyncio.run(drive(cl_scope(BOUND), transport, app, send))
    print("3f declared CL == bound -> app_invoked:", app_invoked["v"])

    seen = {"n": None}

    async def app_g(scope, receive, send):
        m = await receive()
        seen["n"] = len(m.get("body", b""))

    transport = Transport([req(b"z" * (BOUND + 100), False)])
    sent, send = collect_send()
    asyncio.run(drive(cl_scope(10), transport, app_g, send))
    print("3g declared CL=10 body=1124 -> app saw bytes:", seen["n"],
          "(middleware trusts declared CL; server framing is the guard)")
    print()


# --------------------------------------------------------------------------- #
# ATTACK 4 — body integrity
# --------------------------------------------------------------------------- #
def attack4():
    print("=== ATTACK 4: body integrity ===")
    chunks = [b'{"a":', b"1", b',"b":[', b"2,3", b"]}"]
    transport = Transport([req(c, True) for c in chunks[:-1]] + [req(chunks[-1], False)])
    got = []

    async def downstream(scope, receive, send):
        while True:
            m = await receive()
            if m["type"] != "http.request":
                break
            got.append(m["body"])
            if not m.get("more_body", False):
                break

    sent, send = collect_send()
    asyncio.run(drive(http_scope(), transport, downstream, send))
    print("chunks in :", chunks)
    print("chunks out:", got)
    print("byte-exact & ordered:", b"".join(got) == b"".join(chunks),
          "no dup/loss:", got == chunks)
    print()


# --------------------------------------------------------------------------- #
# ATTACK 5 — non-http scope
# --------------------------------------------------------------------------- #
def attack5():
    print("=== ATTACK 5: non-http scope ===")
    seen = {}

    async def transport_receive():
        return {"type": "websocket.connect"}

    async def downstream(scope, receive, send):
        seen["same_receive"] = receive is transport_receive
        seen["msg"] = await receive()

    async def send(m):
        pass

    ws_scope = {"type": "websocket", "path": "/ws", "headers": [], "query_string": b"",
                "server": ("t", 80), "client": ("t", 80), "scheme": "ws", "root_path": ""}
    mw = RequestBodySizeMiddleware(downstream, max_body_bytes=BOUND)
    asyncio.run(mw(ws_scope, transport_receive, send))
    print("ws: downstream receive is ORIGINAL transport receive:", seen["same_receive"],
          "msg:", seen["msg"]["type"])
    print()


# --------------------------------------------------------------------------- #
# ATTACK 6 — resource / DoS hostile transport
# --------------------------------------------------------------------------- #
def attack6():
    print("=== ATTACK 6: resource / DoS ===")

    block = asyncio.Event()
    hb = {"n": 0}
    seen = {}

    async def downstream(scope, receive, send):
        await receive()  # replay terminal
        m = await receive()  # blocks on transport
        seen["after"] = m["type"]

    async def hb_task(stop):
        while not stop.is_set():
            hb["n"] += 1
            await asyncio.sleep(0)

    async def run_a():
        calls = {"i": 0}

        async def receive():
            if calls["i"] == 0:
                calls["i"] += 1
                return req(b"", False)
            await block.wait()
            return {"type": "http.disconnect"}

        stop = asyncio.Event()
        t = asyncio.create_task(hb_task(stop))
        mw = RequestBodySizeMiddleware(downstream, max_body_bytes=BOUND)
        task = asyncio.create_task(mw(http_scope(), receive, lambda m: asyncio.sleep(0)))
        await asyncio.sleep(0.05)
        seen["done_while_blocked"] = task.done()
        seen["hb_while_blocked"] = hb["n"]
        block.set()
        await asyncio.wait_for(task, timeout=2.0)
        stop.set()
        await t

    asyncio.run(run_a())
    print("6a block-on-transport -> not done while blocked:",
          not seen["done_while_blocked"], "hb ticks:", seen["hb_while_blocked"],
          "after disconnect:", seen["after"])

    class Infinite:
        def __init__(self):
            self.n = 0

        async def receive(self):
            self.n += 1
            if self.n > 200000:
                raise RuntimeError("cap")
            return req(b"", False)

    inf = Infinite()
    limit = {"n": 0}

    async def downstream_b(scope, receive, send):
        try:
            while limit["n"] < 50000:
                await receive()
                limit["n"] += 1
        except RuntimeError:
            pass

    async def run_b():
        mw = RequestBodySizeMiddleware(downstream_b, max_body_bytes=BOUND)
        try:
            await asyncio.wait_for(
                mw(http_scope(), inf.receive, lambda m: asyncio.sleep(0)), timeout=3.0)
        except asyncio.TimeoutError:
            pass

    t0 = time.time()
    asyncio.run(run_b())
    print("6b hostile infinite-terminal transport -> downstream iterations:",
          limit["n"], "transport calls:", inf.n, "elapsed_s: %.3f" % (time.time() - t0))

    class EmptyStream:
        def __init__(self, cap):
            self.cap = cap
            self.n = 0

        async def receive(self):
            self.n += 1
            if self.n > self.cap:
                raise RuntimeError("cap reached")
            return req(b"", True)

    cap = 300000
    es = EmptyStream(cap)
    app_invoked = {"v": 0}

    async def app_c(scope, receive, send):
        app_invoked["v"] += 1

    async def run_c():
        mw = RequestBodySizeMiddleware(app_c, max_body_bytes=BOUND)
        try:
            await mw(http_scope(), es.receive, lambda m: asyncio.sleep(0))
        except RuntimeError as e:
            print("6c pre-read aborted by hostile cap:", e, "messages consumed:", es.n)
        except Exception as e:  # noqa: BLE001
            print("6c raised:", type(e).__name__, e, "messages consumed:", es.n)

    asyncio.run(run_c())
    print("6c app_invoked:", app_invoked["v"], "consulted messages:", es.n,
          "(empty non-terminal frames -> unbounded buffering; see note)")
    print()


# --------------------------------------------------------------------------- #
# ATTACK 7 — original bug class regression (pre-fix hot loop)
# --------------------------------------------------------------------------- #
async def old_streaming_capped(app, scope, receive, send, max_body):
    """Exact pre-fix _streaming_capped_call (synthetic terminal forever)."""

    async def _send_413(send_):
        body = json.dumps({"error": {"code": "REQUEST_TOO_LARGE",
                                     "message": "Request body too large",
                                     "details": None}}).encode()
        await send_({"type": "http.response.start", "status": 413,
                     "headers": [(b"content-type", b"application/json"),
                                 (b"content-length", str(len(body)).encode())]})
        await send_({"type": "http.response.body", "body": body, "more_body": False})

    async def _drain_body(recv):
        while True:
            d = await recv()
            if d["type"] == "http.request" and not d.get("more_body", False):
                return
            if d["type"] == "http.disconnect":
                return

    async def main():
        messages = deque()
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                messages.append(message)
                if message["type"] == "http.disconnect":
                    break
                continue
            messages.append(message)
            total += len(message.get("body", b"") or b"")
            if total > max_body:
                if message.get("more_body", False):
                    await _drain_body(receive)
                await _send_413(send)
                return
            if not message.get("more_body", False):
                break

        async def replayed_receive():
            return messages.popleft() if messages else {
                "type": "http.request", "body": b"", "more_body": False}

        await app(scope, replayed_receive, send)

    await main()


def attack7():
    print("=== ATTACK 7: original bug class (hot loop) ===")

    async def scenario(use_old):
        hb = {"n": 0}
        listen = {"n": 0, "disconnect": False}
        stop = asyncio.Event()

        async def hb_task():
            while not stop.is_set():
                hb["n"] += 1
                await asyncio.sleep(0)

        async def downstream(scope, receive, send):
            for _ in range(200000):
                m = await receive()
                listen["n"] += 1
                if m["type"] == "http.disconnect":
                    listen["disconnect"] = True
                    return

        i = {"v": 0}

        async def receive():
            if i["v"] == 0:
                i["v"] = 1
                return req(b"", False)
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}

        t = asyncio.create_task(hb_task())
        if use_old:
            task = asyncio.create_task(
                old_streaming_capped(downstream, http_scope(), receive,
                                     lambda m: asyncio.sleep(0), BOUND))
        else:
            mw = RequestBodySizeMiddleware(downstream, max_body_bytes=BOUND)
            task = asyncio.create_task(mw(http_scope(), receive, lambda m: asyncio.sleep(0)))
        await asyncio.wait_for(task, timeout=3.0)
        stop.set()
        await t
        return hb["n"], listen["n"], listen["disconnect"]

    hb_new, listen_new, disc_new = asyncio.run(scenario(use_old=False))
    print("FIXED  -> heartbeat ticks:", hb_new, "listen calls:", listen_new,
          "saw real disconnect:", disc_new)
    hb_old, listen_old, disc_old = asyncio.run(scenario(use_old=True))
    print("PRE-FIX-> heartbeat ticks:", hb_old, "listen calls:", listen_old,
          "saw real disconnect:", disc_old)
    print()


if __name__ == "__main__":
    attack1()
    attack2()
    attack3()
    attack4()
    attack5()
    attack6()
    attack7()