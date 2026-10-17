"""ADV-47 probe 4: export a REAL SavegameV1 from a live Demo Case and write it
to evidence so the frontend validator/replay can be checked for drift.

Run from backend/:
    python ..\\screenshots\\evidence\\adv47\\probe_export_doc.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
OUT = Path(__file__).resolve().parent / "exported_demo_apartment.pdcase.json"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
import conftest  # noqa: E402
from phase5_helpers import auth, create_session  # noqa: E402
from app.services import demo_cases, savegame as sg  # noqa: E402


def main():
    db = Path(tempfile.mkdtemp()) / "adv47_export.sqlite3"
    url = f"sqlite+pysqlite:///{db}"
    conftest.upgrade_db(url)
    app = create_app(Settings(database_url=url))
    try:
        with TestClient(app) as c:
            session_token, _ = create_session(c)
            record = demo_cases.get_demo_case("demo-apartment")
            started = c.post(
                "/api/v1/cases",
                json={"prompt": record.prompt, "demoCaseId": "demo-apartment"},
                headers=auth(session_token),
            ).json()
            assert started["status"] == "PUBLISHED", started
            row = app.state.store.get_published(started["caseId"], 1)
            payload = json.loads(row.payload_json)
            doc = sg.project_savegame_v1(payload, difficulty="medium")
            text = sg.serialize_savegame_v1(doc)
            OUT.write_text(text, encoding="utf-8")
            print("wrote", OUT, len(text), "bytes")
            print("exportedAt:", doc.get("exportedAt"))
            victim_id = payload["draft"]["crime"]["victim_id"]
            persons = {p["personId"]: p for p in doc["case"]["publicCase"]["persons"]}
            print("victim person keys:", sorted(persons[victim_id].keys()))
    finally:
        app.state.engine.dispose()
        if app.state.store is not None:
            try:
                app.state.store.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    main()
