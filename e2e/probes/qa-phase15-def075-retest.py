"""QA-owned ADV-153 / DEF-075 FIX-READY retest probe (independent; e2e/probes).

Reproduces the ORIGINAL phase-15 final-sweep finding (ADV-153: an unresolvable
DECORATIVE unseen noun discarded the WHOLE prompt composition — objects that
resolved fine were dropped with it, so the published world degraded to the
golden case with no indication) and verifies the fix end-to-end through the
REAL FastAPI app + public API (session -> POST /cases -> public-case DTO ->
playthrough bootstrap) over a FRESH MIGRATED scratch SQLite DB, with the QA
dev-mode provider seam (shipped KnownObjectSpecProvider chained to the
fixture-scripted unseen provider — same wiring as e2e/qa-phase145-backend.py,
zero product code modified).

Checks:
  1. POST /cases with "a claw hammer and a box on the floor" PUBLISHES; the
     resolved claw hammer IS present in the published world (objects + world
     graph), the unresolved decorative box is ABSENT.
  2. A sanitized bounded `compositionNotes` entry is surfaced on the PUBLIC
     case DTO (the browser seam): "the 'box' you described is not currently
     available - it was left out".
  3. The note carries NO URL/path/executable/control tokens and is bounded
     (<= MAX_COMPOSITION_NOTES notes, each <= 120 chars).
  4. The note NEVER appears in the solver inputs (public + evidence facts
     deep-scan) and never reaches the investigation bootstrap world objects.
  5. A decorative-only-unresolved prompt still PUBLISHES (no FAILED); the
     published payload is present.
  6. A REQUIRED unresolved object with a FAILING provider still FAILS and is
     NEVER published (no substitution; get_published() is None; the public
     version endpoint answers the documented not-published error).

Exit: 0 = all PASS, 1 = any FAIL. Evidence JSON: argv[1] or
e2e/artifacts/qa-phase15-def075-retest.json
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tests"))

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:520]}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


DECORATIVE_PROMPT = "A claw hammer and a box are on the floor in the apartment."
DECORATIVE_NOTE = "the 'box' you described is not currently available - it was left out"

HOSTILE_TOKENS = (
    "http://", "https://", "javascript:", "file://", "data:",
    "<script", "eval(", "onload=", "onclick=", "/", "\\", "..",
    "Traceback", ";", "&",
)


def _fresh_migrated_db() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="qa_def075_"))
    db = scratch / "retest.db"
    env = os_environ_copy()
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed: {proc.stderr[-2000:]}")
    con = sqlite3.connect(db)
    versions = [r[0] for r in con.execute("select version_num from alembic_version")]
    con.close()
    assert versions == ["0004"], versions
    return db


def os_environ_copy():
    import os

    env = os.environ.copy()
    env.pop("ENV_FILE", None)
    env.pop("STATIC_DIR", None)
    return env


def _dev_provider():
    from app.assets.spec_provider import AssetSpecRequest, AssetSpecResponse
    from app.assets.spec_provider import FakeAssetSpecProvider
    from app.world.composer import KnownObjectSpecProvider

    from fixtures.asset_specs_unseen import UNSEEN_SPEC_CONTENT

    class _DevModeProvider:
        def __init__(self):
            self._known = KnownObjectSpecProvider()
            self._unseen = FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT)

        def generate(self, request: AssetSpecRequest) -> AssetSpecResponse:
            known = self._known.generate(request)
            if known.content is not None:
                return known
            return self._unseen.generate(request)

    return _DevModeProvider()


def _empty_provider():
    from app.assets.spec_provider import FakeAssetSpecProvider

    return FakeAssetSpecProvider({})


def _build_app(database_url, provider):
    from app.assets.generated_cache import GeneratedAssetCache
    from app.core.config import Settings
    from app.main import create_app

    from app.services.generation import GenerationService
    from conftest import DEFAULT_CORS, upgrade_db

    upgrade_db(database_url)
    application = create_app(
        Settings(database_url=database_url, cors_allowed_origins=DEFAULT_CORS)
    )
    application.state.generation_service = GenerationService(
        settings=application.state.settings,
        store=application.state.store,
        clock=application.state.clock,
        publication=application.state.publication_service,
        spec_provider=provider,
        generated_cache=GeneratedAssetCache(),
    )
    return application


def _publish(client, prompt):
    token = client.post("/api/v1/sessions/anonymous").json()["anonymousSessionToken"]
    res = client.post(
        "/api/v1/cases",
        json={"prompt": prompt},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _public_case_dto(client, created):
    res = client.get(
        f"/api/v1/cases/{created['caseId']}?version=1",
        headers={"Authorization": f"Bearer {created['creatorAccessToken']}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def main() -> int:
    from fastapi.testclient import TestClient

    from app.persistence.store import Store
    from app.world.extract import extract_world_requirements

    if len(sys.argv) > 1:
        out_path = Path(sys.argv[1])
    else:
        out_path = REPO_ROOT / "e2e" / "artifacts" / "qa-phase15-def075-retest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    db1 = _fresh_migrated_db()
    db1_url = f"sqlite:///{db1.as_posix()}"

    # ---- 1-5. decorative-unresolved prompt through the REAL public API -------
    section("decorative-unresolved prompt (real app + public API)")
    app1 = _build_app(db1_url, _dev_provider())
    with TestClient(app1) as client1:
        created = _publish(client1, DECORATIVE_PROMPT)
        ok_publish = created["status"] == "PUBLISHED"
        record(
            "POST /cases with the decorative-noun prompt PUBLISHES",
            ok_publish,
            {"status": created.get("status"), "caseId": created.get("caseId")},
        )

        dto = _public_case_dto(client1, created)
        dto_obj_ids = {o["objectId"] for o in dto.get("objects") or ()}
        wg_ids = {
            (p or {}).get("objectId")
            for p in (dto.get("worldGraph") or {}).get("placements") or ()
        }
        record(
            "the RESOLVED claw hammer is present in the published world; the box is absent",
            "claw_hammer" in dto_obj_ids
            and "claw_hammer" in wg_ids
            and "box" not in dto_obj_ids
            and "box" not in wg_ids,
            {"dtoObjectIds": sorted(dto_obj_ids), "worldGraphObjectIds": sorted(wg_ids)},
        )

        notes = dto.get("compositionNotes") or []
        record(
            "a sanitized compositionNotes entry is surfaced on the PUBLIC DTO",
            notes == [DECORATIVE_NOTE],
            {"compositionNotes": notes},
        )

        note = notes[0] if notes else ""
        clean_note = (
            len(notes) <= 3
            and 0 < len(note) <= 120
            and all(tok not in note for tok in HOSTILE_TOKENS)
        )
        record(
            "the note is bounded and carries no URL/path/executable/control tokens",
            clean_note,
            {"notes": notes, "length": len(note), "banned_hits": [t for t in HOSTILE_TOKENS if t in note]},
        )

        # In-process: load the published payload; verify the note is stored with
        # the draft and NEVER reaches the solver inputs (the solver receives the
        # draft-derived PublicCase + evidence facts — compositionNotes live only
        # on the public wire DTO as the documented browser seam).
        from app.generation.pipeline import _draft_to_phase3

        def _draft_from_payload(payload_dict):
            from app.generation.schemas import (
                CrimeSpec, CrimeTimeSpec, EvidenceSpec, GeneratedDraft,
                LocationSpec, MotiveSpec, ObjectSpec, PersonSpec, PropSpec,
                SceneSpec, TravelRuleSpec, WorldGraphSpec,
            )

            d = payload_dict["draft"]
            return GeneratedDraft(
                crime=CrimeSpec(
                    type=d["crime"]["type"], victim_id=d["crime"]["victim_id"],
                    murderer_id=d["crime"]["murderer_id"], motive_id=d["crime"]["motive_id"],
                    weapon_id=d["crime"]["weapon_id"], location_id=d["crime"]["location_id"],
                    crime_time=CrimeTimeSpec(
                        canonical=d["crime"]["crime_time"]["canonical"],
                        accusation_tolerance_seconds=d["crime"]["crime_time"]["accusation_tolerance_seconds"],
                    ),
                ),
                persons=tuple(
                    PersonSpec(person_id=p["person_id"], name=p["name"], role=p["role"],
                               affordances=tuple(p["affordances"]), presented_data=p.get("presented_data") or {})
                    for p in d["persons"]
                ),
                motives=tuple(
                    MotiveSpec(motive_id=m["motive_id"], label=m["label"], affordances=tuple(m["affordances"]))
                    for m in d["motives"]
                ),
                objects=tuple(
                    ObjectSpec(object_id=o["object_id"], asset_id=o["asset_id"],
                               affordances=tuple(o["affordances"]), subtype=o.get("subtype"))
                    for o in d["objects"]
                ),
                locations=tuple(LocationSpec(location_id=l["location_id"], name=l["name"]) for l in d["locations"]),
                travel_rules=tuple(
                    TravelRuleSpec(from_location_id=t["from_location_id"], to_location_id=t["to_location_id"],
                                   travel_time_seconds=t["travel_time_seconds"])
                    for t in d["travel_rules"]
                ),
                scene=SceneSpec(location_id=d["scene"]["location_id"], name=d["scene"]["name"],
                                environment_id=d["scene"].get("environment_id"),
                                environment_version=d["scene"].get("environment_version")),
                evidence=tuple(
                    EvidenceSpec(id=e["id"], kind=e["kind"], reliability=e.get("reliability"),
                                 discoverable=e.get("discoverable") if e.get("discoverable") is not None else True,
                                 source_ref=e.get("source_ref"),
                                 presentation=e.get("presentation") or {},
                                 propositions=tuple(
                                     PropSpec(type=p["type"], person_id=p.get("person_id"),
                                              location_id=p.get("location_id"), object_id=p.get("object_id"),
                                              motive_id=p.get("motive_id"), observed_at=p.get("observed_at"),
                                              uncertainty_seconds=p.get("uncertainty_seconds", 0),
                                              structured=p.get("structured") or {})
                                     for p in e["propositions"]
                                 ))
                    for e in d["evidence"]
                ),
                world_graph=WorldGraphSpec(),
            )

        store = app1.state.store
        payload = json.loads(store.get_published(created["caseId"], 1).payload_json)
        stored_notes = tuple((payload["draft"] or {}).get("composition_notes") or ())
        stored_ok = stored_notes == (DECORATIVE_NOTE,)
        draft = _draft_from_payload(payload)
        public_model, facts, _truth = _draft_to_phase3(draft, case_id=created["caseId"], title="T")
        # repr() is a faithful, total serialization of the exact solver inputs
        # (PublicCase + evidence facts); note/repr can never raise.
        solver_input = repr(public_model) + repr(facts)
        record(
            "note stored with the published draft and absent from solver inputs",
            stored_ok and note not in solver_input
            and "compositionNotes" not in solver_input
            and "composition_notes" not in solver_input
            and "left out" not in solver_input,
            {"stored_notes": list(stored_notes),
             "note_in_solver_input": note in solver_input,
             "compositionNotes_key_in_solver_input": "compositionNotes" in solver_input,
             "left_out_in_solver_input": "left out" in solver_input},
        )

        # bootstrap world objects carry the hammer, never the note/box
        from phase5_helpers import create_playthrough

        status, body = create_playthrough(
            client1, created["creatorAccessToken"], created["caseId"], 1
        )
        assert status == 201, body
        boot = client1.get(
            f"/api/v1/playthroughs/{body['playthroughId']}/investigation",
            headers={"Authorization": f"Bearer {body['playthroughAccessToken']}"},
        ).json()
        boot_ids = {o["objectId"] for o in boot["scene"]["worldObjects"]}
        record(
            "investigation bootstrap carries the hammer (never box/note text)",
            "claw_hammer" in boot_ids and "box" not in boot_ids and "left out" not in json.dumps(boot),
            {"bootstrapObjectIds": sorted(boot_ids)},
        )

        # decorative-only unresolved still PUBLISHES + published row exists
        row = store.get_published(created["caseId"], 1)
        record(
            "decorative-only-unresolved case is PUBLISHED and stored",
            created["status"] == "PUBLISHED" and row is not None,
            {"status": created.get("status"), "publishedRow": row is not None},
        )
    app1.state.engine.dispose()
    app1.state.store.dispose()

    # ---- 6. REQUIRED unresolved with a failing provider -> FAILED, never published
    section("required-unresolved with failing provider")
    from fixtures.world_showcase import UNSEEN_WEAPON_PROMPT

    db2 = _fresh_migrated_db()
    db2_url = f"sqlite:///{db2.as_posix()}"
    app2 = _build_app(db2_url, _empty_provider())
    with TestClient(app2) as client2:
        created2 = _publish(client2, UNSEEN_WEAPON_PROMPT)
        store2 = app2.state.store
        row2 = store2.get_published(created2["caseId"], 1)
        # public version endpoint must NOT serve a published v1
        res2 = client2.get(
            f"/api/v1/cases/{created2['caseId']}?version=1",
            headers={"Authorization": f"Bearer {created2['creatorAccessToken']}"},
        )
        record(
            "a REQUIRED unresolved object with a failing provider FAILS and is NEVER published",
            created2["status"] == "FAILED"
            and row2 is None
            and res2.status_code in (404, 409),
            {"status": created2.get("status"), "publishedRow": row2 is not None,
             "publicVersionHttp": res2.status_code},
        )
    app2.state.engine.dispose()
    app2.state.store.dispose()

    # ---- summary ---------------------------------------------------------------
    passed = sum(1 for r in results if r["ok"])
    print(f"\n=== ADV-153/DEF-075 RETEST: {passed}/{len(results)} PASS ===")
    out_path.write_text(
        json.dumps({"probe": "qa-phase15-def075-retest", "results": results},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"evidence: {out_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())