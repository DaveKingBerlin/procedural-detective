"""ADV-48 independent in-process reproduction (adversary evidence; NOT product code).

Isolation matrix for two cross-phase lock mismatches:

  A) The Phase-48 Victim line carries the required parenthetical profile
     ``Victim: <name> (<gender>, <age>, <occupation>)``. ``prompt.parse_prompt``
     stores the value VERBATIM, so the locked victim becomes the whole string.
     ``constraints.violations_against`` normalizes that RAW value, so a draft
     whose victim id is the plain ``sarah_miller`` FAILS the lock.
  B) The curated ``Location:`` values do not match the draft scene identity
     (and 13/16 do not canonicalize to a supported environment kit).

Drives the real FastAPI app (GENERATION_PROVIDER=fake, scratch sqlite) via
TestClient. No network, no paid calls.

Run from the repo root:
    python screenshots/evidence/adv48/adv48_provider_repro.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["ENV_FILE"] = os.devnull
os.environ["GENERATION_PROVIDER"] = "fake"

HERE = Path(__file__).resolve()
REPO = HERE
while not (REPO / "backend").is_dir():
    REPO = REPO.parent
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

CASES: dict[str, str] = {
    # QA control (canonical ids, no profile parenthetical): baseline PASS.
    "1_canonical_ids": "Victim: sarah_miller\nMurderer: thomas_reed\n",
    # Display names, no parenthetical: should also pass via normalization.
    "2_display_noparens": "Victim: Sarah Miller\nMurderer: Thomas Reed\n",
    # Display name WITH the Phase-48 parenthetical: isolates the parenthetical.
    "3_display_parens": "Victim: Sarah Miller (Female, 42, accountant)\nMurderer: Thomas Reed\n",
    # The full Phase-48 seven-line shape for the golden identities.
    "4_golden_full_seven_lines": "\n".join(
        [
            "Victim: Sarah Miller (Female, 42, accountant)",
            "Murderer: Thomas Reed",
            "Motive: financial gain",
            "Weapon: kitchen knife",
            "Time: 22:17",
            "Witness: Emily Reed",
        ]
    ),
    # Exact canonical location id that the fake scene actually uses.
    "5_canonical_plus_scene_location": (
        "Victim: sarah_miller\nMurderer: thomas_reed\n"
        "Location: miller_apartment_kitchen\n"
    ),
    # A curated generator location that is NOT the fixture scene id.
    "6_canonical_plus_curated_location": (
        "Victim: sarah_miller\nMurderer: thomas_reed\nLocation: apartment\n"
    ),
    # A representative generator output (seed 12345, hard).
    "7_random_seed12345_hard": "\n".join(
        [
            "Victim: Emma Lehmann (Female, 74, nurse)",
            "Murderer: Petra Braun",
            "Motive: fear of dismissal",
            "Weapon: nightstand",
            "Time: 02:43",
            "Witness: Hugo Dietrich",
            "Location: warehouse loading bay",
        ]
    ),
}


def build_client():
    tmp = Path(tempfile.mkdtemp(prefix="adv48-"))
    db_url = f"sqlite:///{(tmp / 'adv48.db').as_posix()}"
    cfg = AlembicConfig(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")

    from app.core.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(Settings(database_url=db_url, cors_allowed_origins=["http://localhost:5173"]))
    return app, TestClient(app), tmp


def submit(client, prompt: str) -> dict:
    token = client.post("/api/v1/sessions/anonymous").json()["anonymousSessionToken"]
    resp = client.post(
        "/api/v1/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": prompt, "difficulty": "medium", "generationProvider": "fake"},
    )
    body = resp.json()
    return {
        "http": resp.status_code,
        "status": body.get("status"),
        "failureCode": body.get("failureCode"),
        "caseId": body.get("caseId"),
    }


def main() -> int:
    app, client, tmp = build_client()
    try:
        with client:
            out = {name: submit(client, prompt) for name, prompt in CASES.items()}
    finally:
        app.state.engine.dispose()
    print(f"scratch={tmp}")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
