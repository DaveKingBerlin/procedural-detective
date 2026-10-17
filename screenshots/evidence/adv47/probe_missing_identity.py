"""ADV-47 probe: does an AI-generated case with a victim that OMITS its entire
presentedData identity still publish (and then render a name-only profile)?

Run from backend/ with the repo Python 3.12.10:

    python ..\\screenshots\\evidence\\adv47\\probe_missing_identity.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
TESTS = BACKEND / "tests"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(TESTS))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
import conftest  # noqa: E402
from phase5_helpers import auth, create_session  # noqa: E402


def make_app(script_path: Path):
    db = Path(tempfile.mkdtemp()) / "adv47.sqlite3"
    url = f"sqlite+pysqlite:///{db}"
    conftest.upgrade_db(url)
    return create_app(Settings(database_url=url, fake_provider_script=script_path))


def mutate_script(mode: str) -> Path:
    src = BACKEND / "app" / "services" / "dev_mode_case.json"
    raw = json.loads(src.read_text(encoding="utf-8"))
    spec = json.loads(raw["public_world"][0])
    for person in spec["persons"]:
        if person.get("role") == "victim":
            if mode == "absent":
                person.pop("presentedData", None)
            elif mode == "null":
                person["presentedData"] = None
            elif mode == "empty":
                person["presentedData"] = {}
            elif mode == "string":
                person["presentedData"] = "CEO, age 39, female"
            elif mode == "list":
                person["presentedData"] = [{"age": 39}]
            elif mode == "no_gender":
                person["presentedData"] = {"age": 39, "occupation": "CEO, Miller Consulting"}
            elif mode == "no_age":
                person["presentedData"] = {"gender": "female", "occupation": "CEO, Miller Consulting"}
            elif mode == "no_occupation":
                person["presentedData"] = {"age": 39, "gender": "female"}
            elif mode == "bad_age":
                person["presentedData"] = {"age": 999, "gender": "female", "occupation": "CEO"}
            elif mode == "bad_gender":
                person["presentedData"] = {"age": 39, "gender": "banana", "occupation": "CEO"}
    raw["public_world"] = [json.dumps(spec)]
    out = Path(tempfile.mkdtemp()) / f"adv47_{mode}.json"
    out.write_text(json.dumps(raw), encoding="utf-8")
    return out


def create_and_inspect(mode: str):
    app = make_app(mutate_script(mode))
    try:
        with TestClient(app) as c:
            session_token, _ = create_session(c)
            res = c.post(
                "/api/v1/cases",
                json={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n"},
                headers=auth(session_token),
            )
            body = res.json()
            status = body.get("status")
            print(f"mode={mode!r}: create status={status} http={res.status_code}")
            if status != "PUBLISHED":
                print("  body:", json.dumps(body)[:500])
                return
            case_id = body["caseId"]
            res = c.post(
                f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                headers=auth(body["creatorAccessToken"]),
            )
            pt = res.json()
            res = c.get(
                f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
                headers=auth(pt["playthroughAccessToken"]),
            )
            boot = res.json()
            print("  victim block:", json.dumps(boot.get("victim"), ensure_ascii=False))
    finally:
        app.state.engine.dispose()
        if app.state.store is not None:
            try:
                app.state.store.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    for mode in ("absent", "null", "empty", "string", "list", "no_gender", "no_age", "no_occupation", "bad_age", "bad_gender"):
        try:
            create_and_inspect(mode)
        except Exception as exc:  # noqa: BLE001
            print(f"mode={mode!r}: EXCEPTION {type(exc).__name__}: {exc}")
