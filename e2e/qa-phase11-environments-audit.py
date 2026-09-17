"""QA-owned Phase 11 ENVIRONMENT CONTRACT AUDIT (independent).

Covers the Phase 11 gate task 2 (a-d) against the REAL repo manifests /
modules / catalog with a REAL migrated scratch DB + TestClient:

  2a. Manifest contract: all five kits load with ZERO issues; per-kit required
      anchor-class coverage (BODY / FLOOR_EVIDENCE / DESK_EVIDENCE /
      GENERIC_PROP / DOOR / WINDOW + PLAYER_SPAWN and >= 2 secondary types);
      spawn validity; structural assets resolve in the catalog; the six
      additive catalog assets validate.
  2b. Resolver matrix: EXACT / canonical -> EXACT, aliases -> ALIAS (verbatim),
      semantic -> SEMANTIC_TYPE, crafted REAL-kit tie -> AMBIGUOUS (no
      arbitrary winner), unknown -> FALLBACK (apartment) with provenance;
      order independence.
  2c. Placer: duplicate exclusive-anchor occupancy, incompatible
      category/anchor, evidence on non-evidence-capable anchors (DOOR / CCTV /
      ACCESS_CONTROL / PLAYER_SPAWN), evidence spacing < 0.3, spawn
      intersecting anchor / body within 0.8 — all rejected; shuffle-stable
      output; stable objectIds.
  2d. Generation integration: no hint -> apartment kit (golden 9-object set
      byte-stable vs a second no-hint publish, environmentId "apartment",
      solver all_true); environment:"office"/"hotel_suite"/"warehouse"/
      "mansion" -> their kits with the 9-object set on kit anchors + solver
      all_true; unsafe hints -> 422 ENVIRONMENT_ERROR / VALIDATION_ERROR
      (never 5xx, never echoed); unknown safe hint -> FALLBACK apartment with
      provenance; bootstrap carries environmentId; evidence-linked placements
      non-empty + decorative "" (Phase 10 contract preserved).

Run from the repo root:
    python e2e/qa-phase11-environments-audit.py

Output JSON is written next to this script's output path (passed as argv[1],
defaults to e2e/artifacts/qa-phase11-environments-audit.json).
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import random
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
ENVIRONMENTS_DIR = REPO_ROOT / "assets" / "environments"
CATALOG_PATH = REPO_ROOT / "assets" / "catalog" / "catalog.json"
DEV_MODE_CASE = BACKEND_DIR / "app" / "services" / "dev_mode_case.json"
sys.path.insert(0, str(BACKEND_DIR))

KIT_IDS = ("apartment", "office", "hotel_suite", "warehouse", "mansion")
SIX_ADDITIVE_ASSETS = {
    "PROP_WINDOW_01", "PROP_WALL_01", "PROP_DESK_01",
    "PROP_HOTEL_BED_01", "PROP_WAREHOUSE_SHELF_01", "PROP_OFFICE_CHAIR_01",
}
GOLDEN_PROMPT = "Victim: sarah_miller\nMurderer: thomas_reed\n"
GOLDEN_OBJECT_IDS = (
    "kitchen_knife", "letter_opener", "scissors", "vase_01", "apartment_laptop",
    "apartment_table", "apartment_door", "apartment_lamp", "victim_body_placeholder",
)

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:400]}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------- #
# 2a — manifest contract
# --------------------------------------------------------------------------- #
def audit_manifests() -> None:
    section("2a — kit manifests (zero issues, coverage, spawn, structural assets)")
    from app.assets.catalog import load_catalog_from_repo, validate_catalog_data
    from app.environments.manifests import (
        load_all_environments,
        validate_environment_data,
    )

    catalog = load_catalog_from_repo()
    raw_catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    record("catalog validation", validate_catalog_data(raw_catalog) == (),
           list(validate_catalog_data(raw_catalog)))
    missing_additive = sorted(SIX_ADDITIVE_ASSETS - set(catalog.by_id))
    extra_additive = sorted(a for a in six_additive() if a not in SIX_ADDITIVE_ASSETS)
    record("six additive catalog assets present", not missing_additive,
           {"missing": missing_additive, "extra": extra_additive})
    for asset_id in sorted(SIX_ADDITIVE_ASSETS):
        asset = catalog.by_id[asset_id]
        assert asset.render_kind in ("box", "cylinder", "sphere", "flat")

    kits = load_all_environments()
    record("all five kits declared", [k.environment_id for k in kits] == sorted(KIT_IDS),
           [k.environment_id for k in kits])
    for kit in kits:
        raw = json.loads((ENVIRONMENTS_DIR / f"{kit.environment_id}.json").read_text(encoding="utf-8"))
        issues = list(validate_environment_data(raw))
        record(f"{kit.environment_id}: zero load issues", issues == [], issues)

        covered = {t for t, ids in kit.default_anchor_coverage.items() if ids}
        required = {"BODY", "FLOOR_EVIDENCE", "DESK_EVIDENCE", "GENERIC_PROP", "DOOR", "WINDOW", "PLAYER_SPAWN"}
        missing_required = sorted(required - covered)
        secondaries = {"COMPUTER", "DOCUMENT", "TABLE_PROP", "STORAGE", "CCTV", "ACCESS_CONTROL"}
        secondary_covered = sorted(secondaries & covered)
        record(
            f"{kit.environment_id}: required coverage + >=2 secondary",
            not missing_required and len(secondary_covered) >= 2,
            {"missingRequired": missing_required, "secondaryCovered": secondary_covered},
        )

        spawn_anchor = kit.by_id[kit.spawn.anchor_id]
        collisions = [
            a.anchor_id for a in kit.anchors
            if a.anchor_id != kit.spawn.anchor_id
            and kit.spawn.position.distance_to(a.position) < 0.4
        ]
        record(
            f"{kit.environment_id}: spawn valid + non-colliding",
            spawn_anchor.type == "PLAYER_SPAWN" and collisions == [],
            {"spawnAnchor": spawn_anchor.anchor_id, "spawnType": spawn_anchor.type, "collisions": collisions},
        )

        unresolvable = sorted(a for a in kit.structural_assets if a not in catalog.by_id)
        record(
            f"{kit.environment_id}: structural assets resolve in catalog",
            len(kit.structural_assets) >= 6 and unresolvable == [],
            {"declared": len(kit.structural_assets), "unresolvable": unresolvable},
        )

        # deep string-safety scan over the raw manifest (URL/path/executable markers)
        forbidden = ("http://", "https://", "data:", "file:", "javascript:", "<script", "\\", "://", "\x00")
        hits = sorted({m for m in forbidden if m in raw_text(kit.environment_id)})
        record(f"{kit.environment_id}: no URL/path/executable markers", hits == [], hits)


def raw_text(kit_id: str) -> str:
    return (ENVIRONMENTS_DIR / f"{kit_id}.json").read_text(encoding="utf-8")


def six_additive() -> list[str]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return [a["assetId"] for a in catalog["assets"]]


# --------------------------------------------------------------------------- #
# 2b — resolver matrix
# --------------------------------------------------------------------------- #
def audit_resolver() -> None:
    section("2b — resolver matrix (EXACT / ALIAS / SEMANTIC / AMBIGUOUS / FALLBACK)")
    from app.environments.resolver import resolve_environment

    exact = {
        "apartment": "apartment",
        "office": "office",
        "hotel_suite": "hotel_suite",
        "warehouse": "warehouse",
        "mansion": "mansion",
    }
    for hint, expected in exact.items():
        r = resolve_environment(hint)
        record(f"EXACT {hint!r}", r.environment_id == expected and r.provenance.value == "EXACT" and r.resolved,
               {"id": r.environment_id, "provenance": r.provenance.value})

    r = resolve_environment("Hotel Suite")
    record("canonical 'Hotel Suite' -> EXACT", r.environment_id == "hotel_suite" and r.provenance.value == "EXACT",
           {"id": r.environment_id, "provenance": r.provenance.value})

    aliases = {
        "flat": ("apartment", "flat"),
        "condo": ("apartment", "condo"),
        "workplace": ("office", "workplace"),
        "company office": ("office", "company office"),
        "hotel": ("hotel_suite", "hotel"),
        "depot": ("warehouse", "depot"),
        "storage hall": ("warehouse", "storage hall"),
        "villa": ("mansion", "villa"),
        "manor": ("mansion", "manor"),
    }
    for hint, (expected, verbatim) in aliases.items():
        r = resolve_environment(hint)
        record(
            f"ALIAS {hint!r}",
            r.environment_id == expected and r.provenance.value == "ALIAS"
            and r.matched_alias == verbatim and r.resolved,
            {"id": r.environment_id, "provenance": r.provenance.value, "matchedAlias": r.matched_alias},
        )

    semantic = {
        "room 312": "hotel_suite",
        "storage depot": "warehouse",
    }
    for hint, expected in semantic.items():
        r = resolve_environment(hint)
        record(
            f"SEMANTIC {hint!r}",
            r.environment_id == expected and r.provenance.value == "SEMANTIC_TYPE" and r.resolved,
            {"id": r.environment_id, "provenance": r.provenance.value},
        )

    r = resolve_environment("luxury suite")  # mansion tag + hotel alias: real tie
    record(
        "crafted REAL tie 'luxury suite' -> AMBIGUOUS, no winner",
        r.ambiguous and not r.resolved and r.environment_id == "" and r.candidates == ("hotel_suite", "mansion"),
        {"ambiguous": r.ambiguous, "resolved": r.resolved, "candidates": r.candidates},
    )

    r = resolve_environment("unknownthing")
    record(
        "unknown 'unknownthing' -> FALLBACK apartment",
        r.environment_id == "apartment" and r.provenance.value == "FALLBACK" and r.resolved
        and not r.ambiguous,
        {"id": r.environment_id, "provenance": r.provenance.value, "ambiguous": r.ambiguous},
    )

    # deterministic order independence for the whole matrix (reversed kit list)
    from app.environments.manifests import load_all_environments

    kits = load_all_environments()
    reversed_kits = tuple(reversed(kits))
    for name in ("flat", "condo", "company office", "room 312", "storage depot",
                 "villa", "depot", "manor", "luxury suite", "unknownthing",
                 "Hotel Suite", "mansion"):
        first = resolve_environment(name, kits=kits)
        second = resolve_environment(name, kits=reversed_kits)
        same = (
            first.environment_id == second.environment_id
            and first.provenance is second.provenance
            and first.ambiguous == second.ambiguous
            and first.candidates == second.candidates
            and first.resolved == second.resolved
        )
        record(f"order-independent {name!r}", same,
               {"a": (first.environment_id, first.provenance.value, first.candidates, first.resolved),
                "b": (second.environment_id, second.provenance.value, second.candidates, second.resolved)})


# --------------------------------------------------------------------------- #
# 2c — placer contract
# --------------------------------------------------------------------------- #
def audit_placer() -> None:
    section("2c — placement validation + deterministic placer")
    from app.assets.catalog import load_catalog_from_repo
    from app.environments.manifests import Vec3, load_all_environments
    from app.environments.placer import (
        PlacedObject,
        PlacementError,
        PlacementRequest,
        place_objects,
        validate_placement,
    )

    catalog = load_catalog_from_repo()
    kits = {k.environment_id: k for k in load_all_environments()}
    office = kits["office"]

    body = next(a for a in office.anchors if a.type == "BODY")
    dup = (
        PlacedObject(object_id="victim", asset_id="PROP_BODY_PLACEHOLDER_01",
                     location_id=body.zone_id, anchor=body.anchor_id,
                     interaction="", evidence_id=None),
        PlacedObject(object_id="second_victim", asset_id="PROP_BODY_PLACEHOLDER_01",
                     location_id=body.zone_id, anchor=body.anchor_id,
                     interaction="", evidence_id=None),
    )
    issues = validate_placement(office, dup, catalog=catalog)
    record("duplicate exclusive-anchor occupancy rejected",
           any("duplicate occupancy of exclusive anchor" in i for i in issues), issues)

    door = next(a for a in office.anchors if a.type == "DOOR")
    bad = (
        PlacedObject(object_id="knife", asset_id="PROP_KITCHEN_KNIFE_01",
                     location_id=door.zone_id, anchor=door.anchor_id,
                     interaction="inspect", evidence_id="ev1"),
    )
    issues = validate_placement(office, bad, catalog=catalog)
    record(
        "incompatible category/anchor rejected",
        any("is not allowed on anchor" in i for i in issues)
        and any("cannot host asset" in i for i in issues)
        and any("inaccessible/unpickable" in i for i in issues),
        issues,
    )

    for anchor_type, anchor in [("DOOR", door),
                                ("CCTV", next(a for a in office.anchors if a.type == "CCTV")),
                                ("ACCESS_CONTROL", next(a for a in office.anchors if a.type == "ACCESS_CONTROL")),
                                ("PLAYER_SPAWN", next(a for a in office.anchors if a.type == "PLAYER_SPAWN"))]:
        placed = (PlacedObject(object_id="knife", asset_id="PROP_KITCHEN_KNIFE_01",
                               location_id=anchor.zone_id, anchor=anchor.anchor_id,
                               interaction="inspect", evidence_id="forensic_knife_match_01"),)
        issues = validate_placement(office, placed, catalog=catalog)
        record(f"evidence on non-evidence-capable {anchor_type} rejected",
               any("inaccessible/unpickable" in i for i in issues), issues)

    mansion = kits["mansion"]
    desk = next(a for a in mansion.anchors if a.type == "DESK_EVIDENCE")
    close = (
        PlacedObject(object_id="knife", asset_id="PROP_KITCHEN_KNIFE_01",
                     location_id=desk.zone_id, anchor=desk.anchor_id,
                     interaction="inspect", evidence_id="ev1"),
        PlacedObject(object_id="scissors", asset_id="PROP_SCISSORS_01",
                     location_id=desk.zone_id, anchor=desk.anchor_id,
                     interaction="inspect", evidence_id="ev2"),
    )
    issues = validate_placement(mansion, close, catalog=catalog)
    record("evidence spacing < 0.3 rejected",
           any("apart" in i and "minimum 0.3" in i for i in issues), issues)

    # spawn intersecting an anchor / body within 0.8 (placement-time checks on
    # an otherwise-valid kit — the real kits are designed to pass, so QA
    # mutates a copy exactly like the shipped unit tests do).
    moved_body = dataclasses.replace(body, position=Vec3(x=office.spawn.position.x, y=0.0, z=office.spawn.position.z))
    mutated = dataclasses.replace(office)
    object.__setattr__(mutated, "anchors",
                       tuple(moved_body if a.anchor_id == moved_body.anchor_id else a for a in office.anchors))
    issues = validate_placement(mutated, (), catalog=catalog)
    record("body within 0.8 of spawn rejected",
           any("must stay navigable" in i for i in issues), issues)

    mutated_spawn = dataclasses.replace(office)
    object.__setattr__(mutated_spawn, "spawn",
                       dataclasses.replace(office.spawn, position=Vec3(x=door.position.x, y=door.position.y, z=door.position.z)))
    issues = validate_placement(mutated_spawn, (), catalog=catalog)
    record("spawn intersecting anchor rejected",
           any("intersects anchor" in i for i in issues), issues)

    # shuffle-stable output + stable object ids on every kit
    golden = [
        {"objectId": "kitchen_knife", "assetId": "PROP_KITCHEN_KNIFE_01", "interaction": "inspect", "evidenceId": "forensic_knife_match_01"},
        {"objectId": "letter_opener", "assetId": "PROP_LETTER_OPENER_01", "interaction": "inspect", "evidenceId": "forensic_letter_opener_01"},
        {"objectId": "scissors", "assetId": "PROP_SCISSORS_01", "interaction": "inspect", "evidenceId": "forensic_scissors_01"},
        {"objectId": "vase_01", "assetId": "PROP_VASE_01", "interaction": "", "evidenceId": None},
        {"objectId": "apartment_laptop", "assetId": "PROP_LAPTOP_01", "interaction": "read", "evidenceId": "email_thomas_01"},
        {"objectId": "apartment_table", "assetId": "PROP_TABLE_01", "interaction": "", "evidenceId": None},
        {"objectId": "apartment_door", "assetId": "DOOR_APARTMENT_01", "interaction": "", "evidenceId": None},
        {"objectId": "apartment_lamp", "assetId": "PROP_LAMP_01", "interaction": "", "evidenceId": None},
        {"objectId": "victim_body_placeholder", "assetId": "PROP_BODY_PLACEHOLDER_01", "interaction": "", "evidenceId": None},
    ]
    for kit_id in KIT_IDS:
        kit = kits[kit_id]
        requests = [
            PlacementRequest(asset_id=g["assetId"], object_id=g["objectId"],
                             interaction=g["interaction"], evidence_id=g["evidenceId"],
                             category_hint=catalog.by_id[g["assetId"]].category)
            for g in golden
        ]
        base = place_objects(kit, requests, catalog=catalog)
        assert validate_placement(kit, base, catalog=catalog) == ()
        shuffled = list(requests)
        rng = random.Random(17)
        outputs = set()
        for _ in range(5):
            rng.shuffle(shuffled)
            outputs.add(tuple(place_objects(kit, shuffled, catalog=catalog)))
        record(f"{kit_id}: shuffle-stable output", len(outputs) == 1 and next(iter(outputs)) == base,
               {"distinctOutputs": len(outputs), "objects": [p.object_id for p in base]})

    # generated (declared-id-less) object ids stable + unique
    placed = place_objects(
        kits["warehouse"],
        [PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01"),
         PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01"),
         PlacementRequest(asset_id="PROP_VASE_01")],
        catalog=catalog,
    )
    ids = [p.object_id for p in placed]
    again = place_objects(
        kits["warehouse"],
        [PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01"),
         PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01"),
         PlacementRequest(asset_id="PROP_VASE_01")],
        catalog=catalog,
    )
    record("generated object ids stable + unique",
           len(set(ids)) == len(ids) and [p.object_id for p in again] == ids, ids)


# --------------------------------------------------------------------------- #
# 2d — generation integration (fresh migrated scratch DB + TestClient)
# --------------------------------------------------------------------------- #
def _fresh_migrated_db() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="qa_p11_audit_"))
    db = scratch / "audit.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    env.pop("ENV_FILE", None)
    env.pop("STATIC_DIR", None)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    con = sqlite3.connect(db)
    versions = [r[0] for r in con.execute("select version_num from alembic_version")]
    con.close()
    assert versions == ["0004"], versions
    return db


def _golden_placements():
    from app.generation.parser import parse_stage
    from app.generation.provider import GenerationStage

    with open(DEV_MODE_CASE, encoding="utf-8") as handle:
        script = json.load(handle)
    return parse_stage(GenerationStage.WORLD_GRAPH, script["world_graph"][0])


def _payload_from_store(app, case_id):
    return json.loads(app.state.store.get_published(case_id, 1).payload_json)


def _bootstrap(app, client, case_id, creator):
    pt = client.post(
        f"/api/v1/cases/{case_id}/versions/1/playthroughs",
        headers={"Authorization": f"Bearer {creator}"},
    )
    assert pt.status_code == 201, pt.text
    body = pt.json()
    return client.get(
        f"/api/v1/playthroughs/{body['playthroughId']}/investigation",
        headers={"Authorization": f"Bearer {body['playthroughAccessToken']}"},
    )


def _publish(client, environment=None):
    session = client.post("/api/v1/sessions/anonymous")
    assert session.status_code == 201, session.text
    anon = session.json()["anonymousSessionToken"]
    payload = {"prompt": GOLDEN_PROMPT}
    if environment is not None:
        payload["environment"] = environment
    res = client.post("/api/v1/cases", json=payload,
                      headers={"Authorization": f"Bearer {anon}"})
    if res.status_code == 201:
        return res, res.json()["creatorAccessToken"]
    return res, None


def _solve(app, case_id):
    from app.generation.pipeline import _draft_to_phase3
    from app.domain.solver import solve_case
    from app.validation.solution import evaluate_solution
    from app.generation.schemas import (
        CrimeSpec, CrimeTimeSpec, EvidenceSpec, GeneratedDraft, LocationSpec,
        MotiveSpec, ObjectSpec, PersonSpec, PropSpec, SceneSpec,
        TravelRuleSpec, WorldGraphSpec,
    )

    payload = _payload_from_store(app, case_id)
    d = payload["draft"]
    draft = GeneratedDraft(
        crime=CrimeSpec(type=d["crime"]["type"], victim_id=d["crime"]["victim_id"],
                        murderer_id=d["crime"]["murderer_id"], motive_id=d["crime"]["motive_id"],
                        weapon_id=d["crime"]["weapon_id"], location_id=d["crime"]["location_id"],
                        crime_time=CrimeTimeSpec(canonical=d["crime"]["crime_time"]["canonical"],
                                                 accusation_tolerance_seconds=d["crime"]["crime_time"]["accusation_tolerance_seconds"])),
        persons=tuple(PersonSpec(person_id=p["person_id"], name=p["name"], role=p["role"],
                                 affordances=tuple(p["affordances"]),
                                 presented_data=p.get("presented_data") or {}) for p in d["persons"]),
        motives=tuple(MotiveSpec(motive_id=m["motive_id"], label=m["label"],
                                 affordances=tuple(m["affordances"])) for m in d["motives"]),
        objects=tuple(ObjectSpec(object_id=o["object_id"], asset_id=o["asset_id"],
                                 affordances=tuple(o["affordances"]), subtype=o.get("subtype")) for o in d["objects"]),
        locations=tuple(LocationSpec(location_id=l["location_id"], name=l["name"]) for l in d["locations"]),
        travel_rules=tuple(TravelRuleSpec(from_location_id=t["from_location_id"],
                                          to_location_id=t["to_location_id"],
                                          travel_time_seconds=t["travel_time_seconds"]) for t in d["travel_rules"]),
        scene=SceneSpec(location_id=d["scene"]["location_id"], name=d["scene"]["name"],
                        environment_id=d["scene"].get("environment_id")),
        evidence=tuple(EvidenceSpec(id=e["id"], kind=e["kind"], reliability=e.get("reliability"),
                                    discoverable=e.get("discoverable"), source_ref=e.get("source_ref"),
                                    presentation=e.get("presentation") or {},
                                    propositions=tuple(PropSpec(type=p["type"], person_id=p.get("person_id"),
                                                                location_id=p.get("location_id"), object_id=p.get("object_id"),
                                                                motive_id=p.get("motive_id"), observed_at=p.get("observed_at"),
                                                                uncertainty_seconds=p.get("uncertainty_seconds", 0),
                                                                structured=p.get("structured") or {}) for p in e["propositions"]))
                        for e in d["evidence"]),
        world_graph=WorldGraphSpec(),
    )
    public, facts, truth = _draft_to_phase3(draft, case_id="CASE-X", title="T")
    proof = solve_case(public, facts)
    validation = evaluate_solution(proof, truth)
    return proof, validation


def _pl_key(p):
    return (p["object_id"], p["asset_id"], p["location_id"], p["anchor"],
            p["interaction"], p.get("evidence_id"))


def audit_generation() -> None:
    section("2d — generation integration (fresh migrated DB + TestClient)")
    db = _fresh_migrated_db()
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.environments.manifests import load_all_environments
    from app.main import create_app

    app = create_app(settings=Settings(database_url=f"sqlite:///{db.as_posix()}"))
    with TestClient(app) as client:
        # --- no hint: apartment kit, golden byte-stable ----------------------
        res, token = _publish(client)
        record("POST /cases (no hint) -> 201 PUBLISHED",
               res.status_code == 201 and res.json()["status"] == "PUBLISHED", res.status_code)
        case_id = res.json()["caseId"]
        payload = _payload_from_store(app, case_id)
        scene = payload["draft"]["scene"]
        record("no hint -> scene.environmentId apartment",
               scene["environment_id"] == "apartment"
               and scene["location_id"] == "miller_apartment_kitchen"
               and scene["name"] == "Miller Apartment - Kitchen",
               scene)
        golden = _golden_placements()
        wg = payload["draft"]["world_graph"]
        locs = [(l["location_id"], l["template"], tuple(l["rooms"])) for l in wg["locations"]]
        glocs = [(l.location_id, l.template, l.rooms) for l in golden.locations]
        pls = [_pl_key(p) for p in wg["placements"]]
        gpls = [(p.object_id, p.asset_id, p.location_id, p.anchor, p.interaction, p.evidence_id) for p in golden.placements]
        record("no hint -> world graph placement-identical to golden",
               locs == glocs and pls == gpls and len(pls) == 9,
               {"locationsMatch": locs == glocs, "placementsMatch": pls == gpls, "count": len(pls)})

        boot = _bootstrap(app, client, case_id, token)
        body = boot.json()
        object_ids = sorted(o["objectId"] for o in body["scene"]["worldObjects"])
        record("no hint -> bootstrap environmentId + 9-object set",
               boot.status_code == 200 and body["scene"]["environmentId"] == "apartment"
               and tuple(object_ids) == tuple(sorted(GOLDEN_OBJECT_IDS)),
               {"environmentId": body["scene"]["environmentId"], "objectIds": object_ids})

        # byte-stability: publish a SECOND no-hint case and compare world_graph
        res2, _token2 = _publish(client)
        case2 = res2.json()["caseId"]
        wg2 = _payload_from_store(app, case2)["draft"]["world_graph"]
        record("no hint repeated -> stored world_graph BYTE-IDENTICAL",
               json.dumps(wg, sort_keys=True) == json.dumps(wg2, sort_keys=True),
               {"firstLen": len(json.dumps(wg, sort_keys=True)),
                "secondLen": len(json.dumps(wg2, sort_keys=True))})

        proof, validation = _solve(app, case_id)
        record("no hint -> solver all_true",
               validation.all_true and proof.who.winner == "thomas_reed"
               and proof.why.winner == "cover_up_embezzlement"
               and proof.weapon.winner == "kitchen_knife",
               {"all_true": validation.all_true, "who": proof.who.winner,
                "why": proof.why.winner, "weapon": proof.weapon.winner})

        # --- per-kit publishes ----------------------------------------------
        kits = {k.environment_id: k for k in load_all_environments()}
        interface = {
            "kitchen_knife": ("inspect", "forensic_knife_match_01"),
            "letter_opener": ("inspect", "forensic_letter_opener_01"),
            "scissors": ("inspect", "forensic_scissors_01"),
            "apartment_laptop": ("read", "email_thomas_01"),
            "vase_01": ("", None),
            "apartment_table": ("", None),
            "apartment_door": ("", None),
            "apartment_lamp": ("", None),
            "victim_body_placeholder": ("", None),
        }
        first_case_id = case_id
        for kit_id in ("office", "hotel_suite", "warehouse", "mansion"):
            res, token = _publish(client, environment=kit_id)
            record(f"POST /cases {{environment:{kit_id}}} -> 201 PUBLISHED",
                   res.status_code == 201 and res.json()["status"] == "PUBLISHED", res.status_code)
            kid = res.json()["caseId"]
            payload = _payload_from_store(app, kid)
            scene = payload["draft"]["scene"]
            kit = kits[kit_id]
            kit_anchors = {a.anchor_id for a in kit.anchors}
            kit_zones = {z.zone_id for z in kit.zones}
            wg = payload["draft"]["world_graph"]
            by_object = {p["object_id"]: p for p in wg["placements"]}
            interface_ok = all(
                by_object[oid]["interaction"] == inter and by_object[oid].get("evidence_id") == ev
                for oid, (inter, ev) in interface.items()
            )
            on_kit = all(p["anchor"] in kit_anchors and p["location_id"] in kit_zones for p in wg["placements"])
            record(
                f"{kit_id}: scene + 9-object set on kit anchors + interface split",
                scene["environment_id"] == kit_id
                and scene["location_id"] == "miller_apartment_kitchen"
                and set(by_object) == set(GOLDEN_OBJECT_IDS)
                and on_kit and interface_ok and len(wg["placements"]) == 9,
                {"scene": scene, "onKitAnchors": on_kit, "interfaceOk": interface_ok,
                 "anchors": sorted({p["anchor"] for p in wg["placements"]})},
            )
            boot = _bootstrap(app, client, kid, token)
            body = boot.json()
            ids = sorted(o["objectId"] for o in body["scene"]["worldObjects"])
            record(f"{kit_id}: bootstrap environmentId + 9 objects",
                   boot.status_code == 200 and body["scene"]["environmentId"] == kit_id
                   and tuple(ids) == tuple(sorted(GOLDEN_OBJECT_IDS)),
                   {"environmentId": body["scene"]["environmentId"], "count": len(ids)})
            proof, validation = _solve(app, kid)
            truth = _payload_from_store(app, kid)["truth"]
            golden_truth = _payload_from_store(app, first_case_id)["truth"]
            record(
                f"{kit_id}: solver all_true + CaseTruth/evidence unchanged",
                validation.all_true and proof.who.winner == "thomas_reed"
                and truth["crime"] == golden_truth["crime"]
                and _payload_from_store(app, kid)["draft"]["evidence"]
                == _payload_from_store(app, first_case_id)["draft"]["evidence"],
                {"all_true": validation.all_true, "crimeIdentical": truth["crime"] == golden_truth["crime"]},
            )

        # --- unsafe environment hints ---------------------------------------
        for unsafe in ("https://evil.example", "..\\nowhere", "x" * 121):
            res, _ = _publish(client, environment=unsafe)
            try:
                code = res.json()["error"]["code"]
                body = res.json()
            except Exception:  # noqa: BLE001
                code = None
                body = {}
            record(
                f"unsafe environment {unsafe[:24]!r} -> 422, never 5xx, never echoed",
                res.status_code == 422 and code in ("ENVIRONMENT_ERROR", "VALIDATION_ERROR")
                and unsafe not in res.text,
                {"status": res.status_code, "code": code, "echoed": unsafe in res.text},
            )

        # --- unknown safe environment ---------------------------------------
        res, token = _publish(client, environment="unknownthing")
        payload = _payload_from_store(app, res.json()["caseId"])
        service = client.app.state.generation_service
        resolution = getattr(service, "_last_environment_resolution", None)
        boot = _bootstrap(app, client, res.json()["caseId"], token)
        record(
            "unknown safe hint -> FALLBACK apartment + provenance + bootstrap",
            res.status_code == 201
            and payload["draft"]["scene"]["environment_id"] == "apartment"
            and resolution is not None
            and resolution["environmentId"] == "apartment"
            and resolution["provenance"] == "FALLBACK"
            and resolution["compositionFailed"] is False
            and boot.json()["scene"]["environmentId"] == "apartment",
            {"payloadScene": payload["draft"]["scene"]["environment_id"],
             "resolution": resolution},
        )

        # --- Phase 10 contract preserved on the DEFAULT case ----------------
        default_payload = _payload_from_store(app, first_case_id)
        wg = default_payload["draft"]["world_graph"]
        evidence_linked = [
            (p["object_id"], p["interaction"], p.get("evidence_id"))
            for p in wg["placements"] if p["object_id"] in ("kitchen_knife", "letter_opener", "scissors", "apartment_laptop")
        ]
        decorative = [
            (p["object_id"], p["interaction"], p.get("evidence_id"))
            for p in wg["placements"] if p["object_id"] in ("vase_01", "apartment_table", "apartment_door", "apartment_lamp", "victim_body_placeholder")
        ]
        record(
            "Phase 10 contract: evidence-linked non-empty + decorative ''",
            all(i and e for _, i, e in evidence_linked)
            and all(i == "" and e is None for _, i, e in decorative),
            {"evidenceLinked": evidence_linked, "decorative": decorative},
        )


def main() -> int:
    audit_manifests()
    audit_resolver()
    audit_placer()
    audit_generation()
    failed = [r for r in results if not r["ok"]]
    print(f"\nTOTAL {len(results)}  PASS {len(results) - len(failed)}  FAIL {len(failed)}")
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "e2e" / "artifacts" / "qa-phase11-environments-audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"written: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())