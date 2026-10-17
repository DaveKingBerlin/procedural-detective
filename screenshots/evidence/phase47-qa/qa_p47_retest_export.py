"""Phase 47 QA retest — export a REAL post-fix SavegameV1 for DEF-116.

Run from backend/:
    python ..\\screenshots\\evidence\\phase47-qa\\qa_p47_retest_export.py

Writes retest_demo_apartment.pdcase.json next to this file and prints which
saved records carry the public-safe ``victimRelated`` marker.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
OUT = Path(__file__).resolve().parent / "retest_demo_apartment.pdcase.json"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
import conftest  # noqa: E402
from phase5_helpers import auth, create_session  # noqa: E402
from app.services import demo_cases, savegame as sg  # noqa: E402


def main() -> None:
    db = Path(tempfile.mkdtemp()) / "qa_p47_retest.sqlite3"
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
            victim_id = payload["draft"]["crime"]["victim_id"]
            persons = {p["personId"]: p for p in doc["case"]["publicCase"]["persons"]}
            marked = [
                r["evidenceId"]
                for r in doc["case"]["evidence"]
                if isinstance(r.get("content"), dict) and r["content"].get("victimRelated") is True
            ]
            print("wrote", OUT.name, len(text), "bytes")
            print("victim person:", json.dumps(persons[victim_id], ensure_ascii=False))
            print("records carrying victimRelated:", marked)
    finally:
        app.state.engine.dispose()
        if app.state.store is not None:
            try:
                app.state.store.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    main()
