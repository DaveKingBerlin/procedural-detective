"""Adv43-Fix1 probe4: real app static FileResponse + normal requests via TestClient.

Hermetic in-process (TestClient uses ASGI transport; no network).
Run from repo root:
    python screenshots/evidence/adv43fix1/probe4_real_app_static.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\Fujitsu\coding\procedural-detective\backend")

from fastapi.testclient import TestClient  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "index.html").write_bytes(b"<!doctype html><title>spa</title>")
    assets = root / "assets"
    assets.mkdir()
    payload = b"console.log('asset');" * 1000
    (assets / "app-abc123.js").write_bytes(payload)

    db = root / "adv43fix1.db"
    app = create_app(Settings(
        database_url=f"sqlite:///{db.as_posix()}",
        static_dir=str(root),
    ))

    with TestClient(app) as client:
        t0 = time.time()
        r = client.get("/")
        print("GET / ->", r.status_code, "bytes:", len(r.content), "elapsed_ms: %.1f" % ((time.time() - t0) * 1000))
        t0 = time.time()
        r2 = client.get("/assets/app-abc123.js")
        print("GET /assets/app-abc123.js ->", r2.status_code,
              "bytes:", len(r2.content), "byte_exact:", r2.content == payload,
              "elapsed_ms: %.1f" % ((time.time() - t0) * 1000))
        t0 = time.time()
        r3 = client.get("/assets/missing.js")
        print("GET /assets/missing.js ->", r3.status_code,
              "elapsed_ms: %.1f" % ((time.time() - t0) * 1000))
        t0 = time.time()
        r4 = client.get("/api/v1/health")
        print("GET /api/v1/health ->", r4.status_code,
              "elapsed_ms: %.1f" % ((time.time() - t0) * 1000))
        # normal JSON POST (session) through the middleware
        t0 = time.time()
        r5 = client.post("/api/v1/sessions/anonymous")
        print("POST /api/v1/sessions/anonymous ->", r5.status_code,
              "elapsed_ms: %.1f" % ((time.time() - t0) * 1000))
        # repeated assets to confirm no stall/accumulation
        t0 = time.time()
        for _ in range(50):
            rr = client.get("/assets/app-abc123.js")
            assert rr.status_code == 200 and rr.content == payload
        print("50x asset GET -> elapsed_ms: %.1f" % ((time.time() - t0) * 1000))
    app.state.engine.dispose()
    app.state.store.dispose()