"""Independent QA probe — real API playability failure -> PUBLICATION_FAILED.

Drives the REAL FastAPI app in-process with a scripted FakeProvider whose world
graph has NO placements (an UNREPAIRABLE Gate 3 candidate) and asserts the
public create-case response reports status=FAILED + failureCode=PUBLICATION_FAILED
(the code the SPA maps to the non-prompt-blaming copy).

Usage: python e2e/probes/qa-p45fix-api-failure.py <backend_dir>
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(BACKEND / "tests"))
sys.path.insert(0, str(BACKEND))

os.environ["ENV_FILE"] = os.devnull
os.environ["GENERATION_PROVIDER"] = "fake"

from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)

tmpdir = Path(tempfile.mkdtemp(prefix="pd-p45fix-api-"))
db_url = f"sqlite:///{(tmpdir / 'test.db').as_posix()}"

# Scripted fake provider: golden payloads, but a schema-valid EMPTY world graph.
script: dict[str, list[str]] = {}
for stage, payload in GOLDEN_STAGE_PAYLOADS.items():
    name = stage.value if hasattr(stage, "value") else str(stage)
    if name == "world_graph":
        doc = json.loads(payload)
        doc["worldGraph"]["placements"] = []
        script[name] = [json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)]
    else:
        script[name] = [payload]
script_path = tmpdir / "empty_world_script.json"
script_path.write_text(json.dumps(script), encoding="utf-8")

cfg = AlembicConfig(str(BACKEND / "alembic.ini"))
cfg.set_main_option("sqlalchemy.url", db_url)
command.upgrade(cfg, "head")

settings = Settings(
    database_url=db_url,
    cors_allowed_origins=["http://localhost:5173"],
    generation_provider="fake",
    fake_provider_script=script_path,
)
application = create_app(settings)

from fastapi.testclient import TestClient  # noqa: E402

with TestClient(application) as client:
    s = client.post("/api/v1/sessions/anonymous")
    assert s.status_code == 201, s.text
    token = s.json()["anonymousSessionToken"]

    r = client.post(
        "/api/v1/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": PROMPT, "difficulty": "medium"},
    )
    print("create-case HTTP:", r.status_code)
    body = r.json()
    print("create-case body:", json.dumps(body, indent=2))
    assert r.status_code == 201, r.text
    assert body.get("status") == "FAILED", body
    assert body.get("failureCode") == "PUBLICATION_FAILED", body
    print("RESULT: status=%s failureCode=%s" % (body["status"], body["failureCode"]))

application.state.engine.dispose()
print("API-FAILURE PROBE: PASS")
