"""ADV-47 probe 3: does the savegame export strip `victimRelated`, losing the
victim panel's Known facts / Forensic findings on replay?

Run from backend/:
    python ..\\screenshots\\evidence\\adv47\\probe_savegame_replay.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
import conftest  # noqa: E402
from phase5_helpers import auth, create_session  # noqa: E402
from app.services import demo_cases, savegame as sg  # noqa: E402


def main():
    db = Path(tempfile.mkdtemp()) / "adv47_sg.sqlite3"
    url = f"sqlite+pysqlite:///{db}"
    conftest.upgrade_db(url)
    app = create_app(Settings(database_url=url))
    try:
        with TestClient(app) as c:
            session_token, _ = create_session(c)
            record = demo_cases.get_demo_case("demo-apartment")
            res = c.post(
                "/api/v1/cases",
                json={"prompt": record.prompt, "demoCaseId": "demo-apartment"},
                headers=auth(session_token),
            )
            started = res.json()
            assert started["status"] == "PUBLISHED", started
            res = c.post(
                f"/api/v1/cases/{started['caseId']}/versions/1/playthroughs",
                headers=auth(started["creatorAccessToken"]),
            )
            pt = res.json()
            pt_id, pt_token = pt["playthroughId"], pt["playthroughAccessToken"]

            # Discover + read the victim last-seen record through the real flow.
            for obj in ("victim_body_placeholder",):
                c.post(
                    f"/api/v1/playthroughs/{pt_id}/objects/{obj}/interact",
                    json={"interaction": "inspect"},
                    headers=auth(pt_token),
                )
            body = c.get(
                f"/api/v1/playthroughs/{pt_id}/records/body_found_01",
                headers=auth(pt_token),
            ).json()
            print("live body_found_01 content keys:", sorted(body["content"].keys()))
            # Find the live last_seen if there is an object linking it.
            payload_row = app.state.store.get_published(started["caseId"], 1)
            payload = json.loads(payload_row.payload_json)
            from app.services import publication as pub

            ls = pub.project_read_content(payload, "last_seen_01")
            print("live last_seen_01 victimRelated:", ls.get("victimRelated"))

            doc = sg.project_savegame_v1(payload, difficulty="medium")
            victim_id = payload["draft"]["crime"]["victim_id"]
            exported_persons = {p["personId"]: p for p in doc["case"]["publicCase"]["persons"]}
            print("exported victim person:", json.dumps(exported_persons.get(victim_id), ensure_ascii=False))
            victim_related_saved = []
            for rec in doc["case"]["evidence"]:
                if isinstance(rec.get("content"), dict) and "victimRelated" in rec["content"]:
                    victim_related_saved.append(rec["evidenceId"])
            print("saved records carrying victimRelated:", victim_related_saved)
            print("total saved records:", len(doc["case"]["evidence"]))
    finally:
        app.state.engine.dispose()
        if app.state.store is not None:
            try:
                app.state.store.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    main()
