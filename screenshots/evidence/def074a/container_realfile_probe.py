"""QA container probe: pre-fix vs fixed receive around the REAL deployed
FileResponse (starlette 1.8.0). Run INSIDE the backend container.

Bounded by CAP so a starved loop terminates instead of hanging. Compares:
  - pre-fix replayed_receive (synthetic terminal http.request forever)
  - fixed replayed_receive (return await receive())
around the app's REAL `_asset_response` FileResponse, with ASGI spec_version
2.3 (uvicorn's default), recording receive-call count, heartbeat ticks and
whether the response completed.

Piped via:  Get-Content probe.py -Raw | docker exec -i <container> python -
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app/backend")
import app.main as m  # noqa: E402

CAP = 200_000


def make_scope(asset_name: str) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": "/assets/" + asset_name,
        "headers": [(b"host", b"enshrouded-server")],
        "query_string": b"",
        "server": ("t", 80),
        "client": ("t", 80),
        "scheme": "https",
        "root_path": "",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
    }


def make_receive(mode: str):
    state = {"sent": False}
    calls = {"n": 0}

    async def transport():
        if not state["sent"]:
            state["sent"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    async def receive():
        calls["n"] += 1
        if mode == "prefix":
            # Pre-fix: buffered terminal once, then synthetic terminal FOREVER.
            if not state["sent"]:
                state["sent"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            if calls["n"] > CAP:
                raise RuntimeError("cap")
            return {"type": "http.request", "body": b"", "more_body": False}
        # Fixed: buffered terminal once, then forward the real transport.
        return await transport()

    return receive, calls


async def run(mode: str, resp, asset_name: str):
    receive, calls = make_receive(mode)
    hb = {"n": 0}
    stop = asyncio.Event()

    async def hb_task():
        while not stop.is_set():
            hb["n"] += 1
            await asyncio.sleep(0)

    sent = []
    disconnect_seen = {"v": False}

    async def send(msg):
        sent.append(msg["type"])
        await asyncio.sleep(0)

    async def watched_receive():
        m_ = await receive()
        if m_["type"] == "http.disconnect":
            disconnect_seen["v"] = True
        return m_

    t = asyncio.create_task(hb_task())
    err = None
    start = asyncio.get_event_loop().time()
    try:
        await resp(make_scope(asset_name), watched_receive, send)
    except Exception as e:  # noqa: BLE001
        err = type(e).__name__ + ": " + str(e)
    elapsed = asyncio.get_event_loop().time() - start
    stop.set()
    await t
    body = b"".join(x.get("body", b"") for x in [] )
    return {
        "mode": mode,
        "receive_calls": calls["n"],
        "heartbeat_ticks": hb["n"],
        "disconnect_seen": disconnect_seen["v"],
        "response_started": "http.response.start" in sent,
        "response_completed": sent.count("http.response.body"),
        "error": err,
        "elapsed_s": round(elapsed, 4),
    }


def main():
    root = Path(os.environ["STATIC_DIR"]).resolve()
    asset_name = sorted(p.name for p in (root / "assets").iterdir() if p.suffix == ".js")[0]
    resp = m._asset_response(root, asset_name)
    print("container starlette file response type:", type(resp).__name__, "asset:", asset_name)
    for mode in ("prefix", "fixed"):
        result = asyncio.run(run(mode, resp, asset_name))
        print(json.dumps(result))


if __name__ == "__main__":
    main()