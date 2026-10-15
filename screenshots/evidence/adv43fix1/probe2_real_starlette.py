"""Adv43-Fix1 probe2: REAL Starlette Response/FileResponse + middleware + real app static.

Hermetic (in-process ASGI; no network). Run from repo root:
    python screenshots/evidence/adv43fix1/probe2_real_starlette.py
"""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\Fujitsu\coding\procedural-detective\backend")
from app.main import RequestBodySizeMiddleware  # noqa: E402
from starlette.responses import FileResponse, Response, StreamingResponse  # noqa: E402

BOUND = 1024


def http_scope(method="GET", path="/assets/x.js"):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "server": ("t", 80),
        "client": ("t", 80),
        "scheme": "http",
        "root_path": "",
    }


async def drive_real_response(make_response):
    """Drive a real Starlette response through the middleware.

    Transport: one terminal http.request, then sticky disconnect after a
    cooperative yield. A heartbeat task proves the loop is not starved.
    """
    state = {"i": 0, "recv_calls": 0}

    async def receive():
        state["recv_calls"] += 1
        if state["i"] == 0:
            state["i"] = 1
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    sent = []

    async def send(m):
        sent.append(m)

    hb = {"n": 0}

    async def hb_task(stop):
        while not stop.is_set():
            hb["n"] += 1
            await asyncio.sleep(0)

    downstream = make_response()
    mw = RequestBodySizeMiddleware(downstream, max_body_bytes=BOUND)

    stop = asyncio.Event()
    t = asyncio.create_task(hb_task(stop))
    t0 = time.time()
    await asyncio.wait_for(mw(http_scope(), receive, send), timeout=5.0)
    elapsed = time.time() - t0
    stop.set()
    await t
    return sent, hb["n"], state["recv_calls"], elapsed


def run_file_response():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "asset.js"
        payload = b"console.log('hi');" * 500  # ~8KB
        f.write_bytes(payload)
        resp = FileResponse(f, media_type="application/javascript")
        sent, hb, rc, el = asyncio.run(drive_real_response(lambda: resp))
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        starts = [m for m in sent if m["type"] == "http.response.start"]
        print("FileResponse -> status:", starts[0]["status"] if starts else None,
              "body_bytes:", len(body), "expected:", len(payload),
              "byte_exact:", body == payload)
        print("  heartbeat ticks:", hb, "transport receive calls:", rc,
              "elapsed_s: %.4f" % el)


def run_streaming_response():
    async def gen():
        for i in range(1000):
            yield b"x" * 8

    resp = StreamingResponse(gen(), media_type="text/plain")
    sent, hb, rc, el = asyncio.run(drive_real_response(lambda: resp))
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    print("StreamingResponse -> body_bytes:", len(body), "expected 8000:",
          len(body) == 8000, "heartbeat ticks:", hb, "recv calls:", rc,
          "elapsed_s: %.4f" % el)


def run_plain_response():
    resp = Response(content=b"hello", media_type="text/plain")
    sent, hb, rc, el = asyncio.run(drive_real_response(lambda: resp))
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    print("Plain Response -> body:", body, "heartbeat ticks:", hb,
          "recv calls:", rc, "elapsed_s: %.4f" % el)


def run_pre_fix_counterfactual():
    """Same real responses, but with the PRE-FIX synthetic-terminal replay.

    Demonstrate whether the hot loop is real against Starlette 1.6.0's listener
    (only StreamingResponse runs listen_for_disconnect in this version).
    """
    answer = run_one_pre_fix("FileResponse", FileResponse)
    answer2 = run_one_pre_fix("StreamingResponse", StreamingResponse)
    return answer, answer2


def run_one_pre_fix(label, cls):
    from collections import deque

    async def old_mw(app, scope, receive, send, max_body):
        messages = deque()

        async def run():
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
                if not message.get("more_body", False):
                    break

            async def replayed_receive():
                return messages.popleft() if messages else {
                    "type": "http.request", "body": b"", "more_body": False}

            await app(scope, replayed_receive, send)
        await run()

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "asset.js"
        payload = b"y" * 4096
        f.write_bytes(payload)
        if label == "FileResponse":
            resp = cls(f)
        else:
            async def gen():
                for _ in range(20):
                    yield payload
            resp = cls(gen())

        state = {"i": 0, "recv": 0}

        async def receive():
            state["recv"] += 1
            if state["i"] == 0:
                state["i"] = 1
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}

        sent = []

        async def send(m):
            sent.append(m)

        async def main():
            try:
                await asyncio.wait_for(
                    old_mw(resp, http_scope(), receive, send, BOUND), timeout=2.0)
                return "completed"
            except asyncio.TimeoutError:
                return "TIMEOUT (hot loop / starvation)"
        outcome = asyncio.run(main())
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        print(f"PRE-FIX {label} -> outcome:", outcome,
              "recv calls:", state["recv"], "body_bytes:", len(body),
              "response started:", any(m["type"] == "http.response.start" for m in sent))
        return outcome


if __name__ == "__main__":
    print("=== probe2: real Starlette responses through the FIXED middleware ===")
    run_file_response()
    run_streaming_response()
    run_plain_response()
    # Pre-fix counterfactual lives in probe3_driver.py (subprocess with hard
    # timeout) because the pre-fix StreamingResponse hot loop starves the loop
    # so completely that even asyncio.wait_for cannot fire.