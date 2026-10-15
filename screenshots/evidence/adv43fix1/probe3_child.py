"""Child: one pre-fix / fixed response scenario. Prints START then COMPLETED.

Killed by the driver's timeout if the event loop is starved (pre-fix
StreamingResponse listener hot loop).
"""
import asyncio
import sys
import tempfile
from collections import deque
from pathlib import Path

sys.path.insert(0, r"C:\Users\Fujitsu\coding\procedural-detective\backend")
from starlette.responses import FileResponse, StreamingResponse  # noqa: E402

BOUND = 1024


def http_scope():
    return {
        "type": "http", "method": "GET", "path": "/assets/x.js", "headers": [],
        "query_string": b"", "server": ("t", 80), "client": ("t", 80),
        "scheme": "http", "root_path": "",
    }


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


async def fixed_mw(app, scope, receive, send, max_body):
    from app.main import RequestBodySizeMiddleware
    await RequestBodySizeMiddleware(app, max_body_bytes=max_body)(scope, receive, send)


def scenario(label, use_old):
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "asset.js"
        payload = b"y" * 4096
        f.write_bytes(payload)
        if label == "FileResponse":
            resp = FileResponse(f)
        else:
            async def gen():
                for _ in range(20):
                    yield payload
            resp = StreamingResponse(gen())

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

        mw = old_mw if use_old else fixed_mw
        print("START", ("pre-fix" if use_old else "fixed"), label, flush=True)
        asyncio.run(mw(resp, http_scope(), receive, send, BOUND))
        body = b"".join(m.get("body", b"") for m in sent
                        if m["type"] == "http.response.body")
        print("COMPLETED", ("pre-fix" if use_old else "fixed"), label,
              "recv_calls=%d" % state["recv"], "body_bytes=%d" % len(body),
              "started=%s" % any(m["type"] == "http.response.start" for m in sent),
              flush=True)


if __name__ == "__main__":
    label = sys.argv[1]
    mode = sys.argv[2]  # old|fixed
    scenario(label, use_old=(mode == "old"))