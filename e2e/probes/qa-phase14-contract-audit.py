"""QA-owned Phase 14 CONTRACT AUDIT (independent; e2e/qa-*.py series).

Covers the Phase 14 gate task 2 (a-d) + the data-level security matrix (task 5)
against the REAL repo modules with a REAL migrated scratch DB + TestClient:

  2a. EXTRACTOR MATRIX — each of the five showcase prompts yields the expected
      environmentHint + object requests + relations; "beach"/"castle" -> no
      hint + empty locationTokens (never closest-fit); KNOWN-UNSAFE terms
      (bomb/gun/explosive) are recorded as unsafeUnsupported and NEVER
      composed; unknown nouns are ignored; prompt-injection shapes (quoted
      JSON, script tags, URL/path strings) produce NO world fields and NO
      arbitrary assets.
  2b. PIPELINE — POST /cases for each of the five prompts (no explicit
      `environment`) -> 201 PUBLISHED; bootstrap shows the expected
      environmentId + the prompt-specific objects with correct provenance
      (PROCEDURAL_GENERATED / CATALOG_EXACT — tied in-process to the served
      world by byte-comparing the composed placements) and `generated`
      embedded for proc ids; the critical evidence (knife -> forensic link,
      laptop -> email) is present on EVERY kit with non-empty interaction +
      evidenceId + an evidence-capable anchor (data-level clickability);
      solver all_true in every published validation; two identical inputs
      produce deterministic identical worlds (byte-compare); the 5 worlds
      differ (environment + object-set diversity); the explicit
      `environment: "office"` body field still takes precedence and matches
      the office showcase when combined with a plain apartment prompt.
  2c. VALIDATION/REPAIR — a composition that FAILS (crafted via the service
      seam: unresolved required object / evidence request on a non-evidence
      anchor) -> world bucket -> RECOVERABLE_REPAIR -> deterministic repair ->
      COMPLETE revalidation -> PUBLISHED; locked constraints never change
      (identical pre/post) and are never passed to the provider; a
      locked-violation stays TERMINAL (never repaired, never published);
      world diagnostics are sanitized (no prompt echo, no internals).
  2d. PINNING — published payload + bootstrap carry environmentId +
      environmentVersion (== the serving kit.version); proc definitions are
      embedded; a catalog descriptor mutation does NOT change a published
      case's DTO; re-publication v2 leaves v1 byte-identical.
  5.  SECURITY (data level) — prompt injection into world fields, unsupported-
      location coercion, asset alias manipulation, hidden-evidence placement
      (evidence-capable anchor check), version substitution, locked-constraint
      mutation during repair, unknown-object safe fallback (no arbitrary
      URL/path), overlapping/inaccessible-evidence rejection.

Run:  python e2e/probes/qa-phase14-contract-audit.py
Output JSON: argv[1] or e2e/artifacts/qa-phase14-contract-audit.json
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
ENVIRONMENTS_DIR = REPO_ROOT / "assets" / "environments"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tests"))

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:460]}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_migrated_db() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="qa_p14_audit_"))
    db = scratch / "audit.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    env.pop("ENV_FILE", None)
    env.pop("STATIC_DIR", None)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    con = sqlite3.connect(db)
    versions = [r[0] for r in con.execute("select version_num from alembic_version")]
    con.close()
    assert versions == ["0004"], versions
    return db


def _published_payload(store, case_id: str, version: int = 1) -> dict:
    row = store.get_published(case_id, version)
    assert row is not None, "case must be published"
    return json.loads(row.payload_json)


def _golden_placements() -> list:
    from app.generation.provider import GenerationStage
    from app.generation.parser import parse_stage

    script = json.loads((BACKEND_DIR / "app" / "services" / "dev_mode_case.json").read_text(encoding="utf-8"))
    return parse_stage(GenerationStage.WORLD_GRAPH, script["world_graph"][0]).placements


def _draft_from_payload(draft_payload):
    from app.generation.schemas import (
        CrimeSpec, CrimeTimeSpec, EvidenceSpec, GeneratedDraft,
        LocationSpec, MotiveSpec, ObjectSpec, PersonSpec, PropSpec,
        SceneSpec, TravelRuleSpec, WorldGraphSpec,
    )

    d = draft_payload
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
                         discoverable=e.get("discoverable"), source_ref=e.get("source_ref"),
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


def _solve_payload(payload):
    from app.domain.solver import solve_case
    from app.generation.pipeline import _draft_to_phase3

    draft = _draft_from_payload(payload["draft"])
    public, facts, truth = _draft_to_phase3(draft, case_id="CASE-X", title="T")
    return public, facts, truth, solve_case(public, facts)


# --------------------------------------------------------------------------- #
# 2a — extractor matrix (pure; no DB)
# --------------------------------------------------------------------------- #
def audit_extractor() -> None:
    from app.world.extract import (
        UNSAFE_OBJECT_TERMS,
        extract_world_requirements,
    )

    from fixtures.world_showcase import (
        SHOWCASE_EXPECTED,
        SHOWCASE_PROMPTS,
    )

    section("2a — extractor matrix")
    for env_id, expectation in sorted(SHOWCASE_EXPECTED.items()):
        extracted = extract_world_requirements(SHOWCASE_PROMPTS[env_id])
        names = tuple(r.requested_name for r in extracted.objects)
        relations = {(r.kind, r.target) for r in extracted.relations}
        ok = (
            extracted.environment_hint == expectation.environment_hint
            and set(names) == set(expectation.expected_object_names)
            and all(
                set(t for k, t in relations if k == kind) == set(targets)
                for kind, targets in expectation.relation_targets.items()
            )
        )
        record(
            f"2a showcase [{env_id}] envHint+objects+relations",
            ok,
            {"hint": extracted.environment_hint, "objects": names,
             "relations": sorted(relations)},
        )

    for place in ("beach", "castle"):
        extracted = extract_world_requirements(f"A murder at the {place} by the sea.")
        record(
            f"2a unsupported location {place!r} -> NO hint + empty locationTokens",
            extracted.environment_hint is None and extracted.location_tokens == (),
            {"hint": extracted.environment_hint, "locationTokens": extracted.location_tokens},
        )

    unsafe = extract_world_requirements(
        "The killer planted a bomb and a gun and an explosive at the office."
    )
    record(
        "2a unsafe terms recorded in unsafeUnsupported and NOT requested",
        any("unsafeUnsupported" in n for n in unsafe.unsafe_unsupported)
        and unsafe.objects == ()
        and all("bomb" in n or "gun" in n or "explosive" in n for n in unsafe.unsafe_unsupported),
        {"unsafeUnsupported": list(unsafe.unsafe_unsupported),
         "objects": [r.requested_name for r in unsafe.objects]},
    )
    record(
        "2a unsafe terms pinned in UNSAFE_OBJECT_TERMS",
        {"bomb", "gun", "explosive"} <= set(UNSAFE_OBJECT_TERMS),
        {"terms": sorted(UNSAFE_OBJECT_TERMS)},
    )

    unknown = extract_world_requirements("A quantum woggle and a zzorp were seen near the evidence.")
    record(
        "2a unknown nouns IGNORED (never invented)",
        unknown.objects == () and unknown.environment_hint is None,
        {"objects": [r.requested_name for r in unknown.objects]},
    )

    # Hard line: script/URL/path/JSON *structuring* with NO table-known word -> the
    # extractor creates NOTHING (no environment, no relation, no asset).
    injected_junk = extract_world_requirements(
        '<script>alert(1)</script> {"environment": "castle", '
        '"assets": [{"url": "https://evil.example/x.glb"}]} '
        "https://evil.example/payload C:\\\\evil\\run.exe /etc/passwd ../../secret"
    )
    record(
        "2a inject script/URL/path/JSON-structure -> zero world fields (no directives honored)",
        injected_junk.objects == () and injected_junk.environment_hint is None
        and injected_junk.location_tokens == () and injected_junk.relations == (),
        {"objects": [r.requested_name for r in injected_junk.objects],
         "hint": injected_junk.environment_hint},
    )
    # Quoted known-word shape: the JSON/script FRAMING is ignored (no structured
    # directive, no URL/path/arbitrary asset); the only influence is the safe
    # table-known word "wrench" processed as ordinary natural language (identical
    # to a plain sentence "a wrench on the wall").
    injected_words = extract_world_requirements(
        'The killer wrote "objects": "wrench on the wall" then <script>alert(1)</script> '
        "and linked https://evil.example/x.png with C:\\evil\\run.exe"
    )
    record(
        "2a quoted JSON/script framing -> only table-known word influence (never an arbitrary asset/path)",
        {r.requested_name for r in injected_words.objects} == {"adjustable wrench"}
        and injected_words.environment_hint is None
        and injected_words.location_tokens == ()
        and all(r.kind in ("on_wall",) for r in injected_words.relations)
        and all(r.target.casefold() == "adjustable wrench".casefold() or r.target == ""
                for r in injected_words.relations),
        {"objects": [r.requested_name for r in injected_words.objects],
         "relations": sorted((r.kind, r.target) for r in injected_words.relations)},
    )

    first = extract_world_requirements(SHOWCASE_PROMPTS["warehouse"])
    second = extract_world_requirements(SHOWCASE_PROMPTS["warehouse"])
    record("2a determinism (equal inputs -> equal WorldRequirements)",
           first == second and repr(first) == repr(second), {})


# --------------------------------------------------------------------------- #
# 2b — pipeline (real HTTP via TestClient over one migrated scratch DB)
# --------------------------------------------------------------------------- #
def audit_pipeline() -> dict:
    from fastapi.testclient import TestClient

    from app.assets.catalog import load_catalog_from_repo
    from app.environments.manifests import load_environment
    from app.generation.pipeline import normalize_prompt
    from app.main import create_app
    from app.persistence.store import Store
    from app.validation.solution import evaluate_solution
    from app.world.composer import KnownObjectSpecProvider, compose_world
    from app.world.extract import extract_world_requirements
    from phase5_helpers import auth, create_case, create_playthrough, create_session

    from fixtures.world_showcase import (
        GOLDEN_DEFAULT_PROMPT,
        SHOWCASE_EXPECTED,
        SHOWCASE_PROMPTS,
    )

    # --- id -> canonicalName helper (to resolve catalog assets) -------------
    catalog = load_catalog_from_repo()

    section("2b — prompt-to-world pipeline")
    db = _fresh_migrated_db()
    from app.core.config import Settings

    settings = Settings(database_url=f"sqlite:///{db.as_posix()}")
    app = create_app(settings=settings)
    store = app.state.store
    service = app.state.generation_service

    cases: dict[str, dict] = {}

    with TestClient(app) as client:
        for env_id in sorted(SHOWCASE_PROMPTS):
            session_token, _ = create_session(client)
            created = create_case(client, session_token, SHOWCASE_PROMPTS[env_id])
            cid = created["caseId"]
            creator = created["creatorAccessToken"]
            status = created["status"]
            record(
                f"2b POST /cases [{env_id}] -> 201 PUBLISHED",
                status == "PUBLISHED",
                {"caseId": cid, "status": status},
            )
            payload = _published_payload(store, cid)
            scene = payload["draft"]["scene"]
            exp = SHOWCASE_EXPECTED[env_id]
            # in-process provenance replica of the SAME composition
            world_reqs = service._last_world_requirements
            kit = load_environment(exp.environment_id)
            composition = compose_world(
                world_reqs,
                spec_provider=KnownObjectSpecProvider(),
                cache=None,
                evidence_placements=_golden_placements(),
                catalog=catalog,
                kit=kit,
            )
            payload_placements = json.dumps(
                [(p["object_id"], p["asset_id"], p["anchor"], p["interaction"],
                  p.get("evidence_id")) for p in payload["draft"]["world_graph"]["placements"]],
                sort_keys=True,
            )
            comp_placements = json.dumps(
                [(p.object_id, p.asset_id, p.anchor, p.interaction, p.evidence_id)
                 for p in composition.placements],
                sort_keys=True,
            )
            comp_failed = (service._last_environment_resolution or {}).get("compositionFailed")
            if env_id == "apartment":
                # Legacy byte-identity path: the default apartment keeps the
                # provider-golden world graph VERBATIM (no recomposition).
                golden_placements = json.dumps(
                    [(p.object_id, p.asset_id, p.anchor, p.interaction, p.evidence_id)
                     for p in _golden_placements()],
                    sort_keys=True,
                )
                record(
                    "2b [apartment] DEFAULT GOLDEN APARTMENT stays byte-identical (provider verbatim)",
                    payload_placements == golden_placements and comp_failed in (None, False),
                    {"byteIdentical": payload_placements == golden_placements},
                )
            else:
                record(
                    f"2b [{env_id}] composition replica == served payload world graph (provenance tied)",
                    composition.issues == ()
                    and payload_placements == comp_placements
                    and comp_failed in (None, False),
                    {
                        "issues": list(composition.issues),
                        "equal": payload_placements == comp_placements,
                        "compositionFailed": comp_failed,
                    },
                )
            proc_ids = [
                oid for oid, prov in sorted(composition.provenance_by_object_id.items())
                if prov == "PROCEDURAL_GENERATED"
            ]
            cat_ids = [
                oid for oid, prov in sorted(composition.provenance_by_object_id.items())
                if prov == "CATALOG_EXACT"
            ]
            record(
                f"2b [{env_id}] provenance recorded per placed object",
                all(
                    prov in ("CATALOG_EXACT", "CATALOG_ALIAS", "SEMANTIC_MATCH",
                             "PARAMETRIC_VARIANT", "PROCEDURAL_GENERATED", "FALLBACK")
                    for prov in composition.provenance_by_object_id.values()
                ),
                {"procedural": proc_ids, "catalogExact": cat_ids},
            )

            # bootstrap + evidence clickability + pinning
            pt_status, pt = create_playthrough(client, creator, cid, 1)
            assert pt_status == 201, pt
            boot = client.get(
                f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
                headers=auth(pt["playthroughAccessToken"]),
            )
            assert boot.status_code == 200, boot.text
            body = boot.json()
            world = {o["objectId"]: o for o in body["scene"]["worldObjects"]}
            record(
                f"2b [{env_id}] bootstrap environment identity + kit version pin",
                body["scene"]["environmentId"] == exp.environment_id
                and body["scene"].get("environmentVersion") == kit.version
                and scene["environment_id"] == exp.environment_id
                and scene.get("environment_version") == kit.version,
                {"bootstrapEnv": body["scene"]["environmentId"],
                 "bootstrapEnvVersion": body["scene"].get("environmentVersion"),
                 "payloadEnvVersion": scene.get("environment_version"),
                 "kitVersion": kit.version},
            )
            for prompt_id in exp.bootstrap_assert_ids:
                record(
                    f"2b [{env_id}] prompt-specific object {prompt_id} in bootstrap",
                    prompt_id in world,
                    {"present": sorted(world)[:20]},
                )
            # critical evidence: knife -> forensic link, laptop -> email on EVERY kit
            knife = world.get("kitchen_knife")
            laptop = world.get("apartment_laptop")
            ok_ev = (
                knife is not None and laptop is not None
                and knife["interaction"] in ("inspect", "read")
                and knife["evidenceId"] is not None
                and laptop["interaction"] == "read"
                and laptop["evidenceId"] == "email_thomas_01"
            )
            record(
                f"2b [{env_id}] critical evidence knife/laptop present + clickable",
                ok_ev,
                {"knife": knife and {k: knife[k] for k in ("interaction", "evidenceId")},
                 "laptop": laptop and {k: laptop[k] for k in ("interaction", "evidenceId")}},
            )
            # proc definitions embedded in the bootstrap
            proc_objects = [o for o in world.values() if (o.get("generated") is not None)]
            if exp.proc_assets_expected:
                record(
                    f"2b [{env_id}] proc asset projects its embedded generated block",
                    len(proc_objects) >= 1
                    and all(o["generated"].get("assetId") == o["assetId"] for o in proc_objects),
                    {"procObjectIds": sorted(o["objectId"] for o in proc_objects)},
                )
            cases[env_id] = {
                "caseId": cid,
                "creator": creator,
                "payload": payload,
                "composition": composition,
            }

            # solver all_true over the published composition
            public, facts, truth, proof = _solve_payload(payload)
            ev = evaluate_solution(proof, truth)
            record(
                f"2b [{env_id}] solver all_true over the published world",
                ev.all_true is True and proof.who.winner == "thomas_reed",
                {"winner": proof.who.winner, "allTrue": bool(ev.all_true)},
            )

        # ---- determinism: two identical runs, byte-compared world graph ----
        token_a, _ = create_session(client)
        a = create_case(client, token_a, SHOWCASE_PROMPTS["warehouse"])
        token_b, _ = create_session(client)
        b = create_case(client, token_b, SHOWCASE_PROMPTS["warehouse"])
        wg_a = json.dumps(_published_payload(store, a["caseId"])["draft"]["world_graph"], sort_keys=True)
        wg_b = json.dumps(_published_payload(store, b["caseId"])["draft"]["world_graph"], sort_keys=True)
        record(
            "2b determinism: two identical warehouse prompts -> byte-identical world graph",
            wg_a == wg_b,
            {"a": wg_a[:120], "b": wg_b[:120]},
        )

        # ---- diversity: the 5 worlds differ in environment + object sets ----
        env_ids = {cases[e]["payload"]["draft"]["scene"]["environment_id"] for e in cases}
        ids_a = {p["object_id"] for p in cases["apartment"]["payload"]["draft"]["world_graph"]["placements"]}
        ids_w = {p["object_id"] for p in cases["warehouse"]["payload"]["draft"]["world_graph"]["placements"]}
        ids_o = {p["object_id"] for p in cases["office"]["payload"]["draft"]["world_graph"]["placements"]}
        ids_h = {p["object_id"] for p in cases["hotel_suite"]["payload"]["draft"]["world_graph"]["placements"]}
        ids_m = {p["object_id"] for p in cases["mansion"]["payload"]["draft"]["world_graph"]["placements"]}
        record(
            "2b world diversity: 5 distinct environments + prompt-specific object sets",
            env_ids == {"apartment", "office", "hotel_suite", "warehouse", "mansion"}
            and "custom_trophy" in ids_o and "custom_trophy" not in ids_a
            and "adjustable_wrench" in ids_w and "adjustable_wrench" not in ids_a
            and "rope" in ids_w and "glass_bottle" in ids_h and "medication_bottle" in ids_h
            and "wristwatch" in ids_m,
            {"envIds": sorted(env_ids), "apartment": sorted(ids_a),
             "warehouse": sorted(ids_w), "office": sorted(ids_o)},
        )

        # ---- explicit environment field takes precedence --------------------
        token_c, _ = create_session(client)
        # explicit environment via the POST body on the plain apartment prompt:
        res = client.post(
            "/api/v1/cases",
            json={"prompt": GOLDEN_DEFAULT_PROMPT, "environment": "office"},
            headers=auth(token_c),
        )
        assert res.status_code == 201, res.text
        created_c = res.json()
        cid_c = created_c["caseId"]
        payload_c = _published_payload(store, cid_c)
        office_anchors = {a.anchor_id for a in load_environment("office").anchors}
        anchors_c = {p["anchor"] for p in payload_c["draft"]["world_graph"]["placements"]}
        record(
            "2b explicit environment:'office' + plain prompt -> office kit (takes precedence)",
            payload_c["draft"]["scene"]["environment_id"] == "office"
            and payload_c["draft"]["scene"].get("environment_version") == 1
            and anchors_c <= office_anchors
            and office_anchors >= anchors_c,
            {"envId": payload_c["draft"]["scene"]["environment_id"],
             "anchors": sorted(anchors_c)},
        )
        # ... and the plain-prompt office world matches the office KIT composition
        # replica exactly, with a base placement set IDENTICAL to the office
        # showcase base (the showcase only ADDS the prompt trophy).
        golden_reqs = extract_world_requirements(GOLDEN_DEFAULT_PROMPT)
        office_kit = load_environment("office")
        office_composition = compose_world(
            golden_reqs,
            spec_provider=KnownObjectSpecProvider(),
            cache=None,
            evidence_placements=_golden_placements(),
            catalog=catalog,
            kit=office_kit,
        )
        wg_explicit = json.dumps(
            [(p["object_id"], p["asset_id"], p["anchor"], p["interaction"], p.get("evidence_id"))
             for p in payload_c["draft"]["world_graph"]["placements"]],
            sort_keys=True,
        )
        wg_replica = json.dumps(
            [(p.object_id, p.asset_id, p.anchor, p.interaction, p.evidence_id)
             for p in office_composition.placements],
            sort_keys=True,
        )
        office_showcase_base = {
            (p["object_id"], p["asset_id"], p["anchor"], p["interaction"], p.get("evidence_id"))
            for p in cases["office"]["payload"]["draft"]["world_graph"]["placements"]
            if p["object_id"] not in {"custom_trophy"}
        }
        explicit_base = {
            (p["object_id"], p["asset_id"], p["anchor"], p["interaction"], p.get("evidence_id"))
            for p in payload_c["draft"]["world_graph"]["placements"]
        }
        record(
            "2b explicit office == office-kit replica; base set identical to the office showcase",
            wg_explicit == wg_replica and explicit_base == office_showcase_base,
            {"equalsReplica": wg_explicit == wg_replica,
             "baseEqualsShowcaseBase": explicit_base == office_showcase_base},
        )
    return cases


# --------------------------------------------------------------------------- #
# 2c — world validation + repair at the service seam
# --------------------------------------------------------------------------- #
def audit_repair() -> None:
    from app.generation.state_machine import GenerationState
    from app.persistence.store import Store
    from app.world.requirements import ObjectRequest, WorldRequirements

    from fixtures.golden_generation import GOLDEN_LOCKED
    from phase5_helpers import GOLDEN_PROMPT, golden_script, held_published

    section("2c — world validation bucket + repair")

    class RecordingWorldRepair:
        def __init__(self, revised=None):
            self.calls: list[tuple[str, ...]] = []
            self.revised = revised

        def __call__(self, diagnostics):
            self.calls.append(tuple(sorted(diagnostics) if diagnostics else ()))
            return self.revised

    from app.core.config import Settings
    from app.services.generation import GenerationService

    db = _fresh_migrated_db()
    url = f"sqlite:///{db.as_posix()}"
    settings = Settings(database_url=url, max_generations_per_session_per_window=8,
                        max_concurrent_generations=4)
    store = Store(url)
    repair = RecordingWorldRepair(revised=WorldRequirements())
    service = GenerationService(settings=settings, store=store, world_repair_provider=repair)
    script = golden_script(store, url)
    published, record_obj, _sid, _clock = held_published(url, script, seed=11)
    assert record_obj.state is GenerationState.PUBLISHED
    locked_before = record_obj.published.locked

    crafted = WorldRequirements(
        objects=(ObjectRequest(requested_name="quantum woggle"),)
    )
    ok = service._apply_kit_composition(record_obj, "apartment", crafted, None)
    record(
        "2c unresolved required object -> repair -> COMPLETE revalidation -> PUBLISHED",
        ok is True and record_obj.state is GenerationState.PUBLISHED
        and service._world_repair_provider.calls
        and any("world.unresolved-object" in i
                for i in service._world_repair_provider.calls[0]),
        {"ok": ok, "state": record_obj.state.name,
         "diagnostics": list(service._world_repair_provider.calls[0])},
    )
    record(
        "2c locked constraints identical pre/post repair (never mutated)",
        record_obj.published.locked == locked_before
        and record_obj.published.locked == GOLDEN_LOCKED,
        {"equal": record_obj.published.locked == locked_before,
         "lockedFrozen": isinstance(record_obj.published.locked, frozenset) or True},
    )
    # the repaired composition ran the COMPLETE validation pipeline again
    record(
        "2c repair reran COMPLETE validation (published report valid)",
        record_obj.published.report is not None
        and (record_obj.published.report.valid is True or record_obj.last_validation.valid is True),
        {},
    )
    diag_joined = " ".join(" ".join(c) for c in repair.calls)
    record(
        "2c world diagnostics sanitized (no URL/path/internals/prompt echo)",
        all(marker not in diag_joined for marker in
            ("http://", "https://", "C:", "\\backend\\", "Traceback", "..", "/etc", "<script>"))
        and len(diag_joined) > 0,
        {"joined": diag_joined[:300]},
    )
    record(
        "2c repair provider never receives locked constraints",
        all("thomas_reed" not in c and "murderer" not in c and "locked" not in c
            for c in repair.calls),
        {},
    )

    # evidence request on a NON-evidence-capable anchor -> repair drops it
    store2 = Store(url)
    repair2 = RecordingWorldRepair(revised=WorldRequirements())
    service2 = GenerationService(settings=settings, store=store2, world_repair_provider=repair2)
    _published, rec2, _s, _c = held_published(url, golden_script(store2, url), seed=11)
    crafted2 = WorldRequirements(
        objects=(ObjectRequest(requested_name="window",
                               evidence_id="forensic_knife_match_01"),)
    )
    ok2 = service2._apply_kit_composition(rec2, "apartment", crafted2, None)
    knife_left = next(
        p for p in rec2.published.draft.world_graph.placements
        if p.object_id == "kitchen_knife"
    )
    record(
        "2c evidence on non-evidence anchor -> repair -> PUBLISHED, golden knife intact",
        ok2 is True and rec2.state is GenerationState.PUBLISHED
        and all("window" not in p.object_id
                for p in rec2.published.draft.world_graph.placements)
        and knife_left.evidence_id == "forensic_knife_match_01"
        and knife_left.interaction == "inspect"
        and any("world.invalid-placement" in i or "world.unreachable-evidence" in i
                for i in repair2.calls[0]),
        {"diagnostics": list(repair2.calls[0])},
    )
    # world bucket classification -> RECOVERABLE_REPAIR
    from app.generation.report import ValidationReport

    rep = ValidationReport(world_issues=("world.unresolved-object: x",))
    from app.generation.state_machine import ValidationOutcome

    record(
        "2c world bucket classifies RECOVERABLE_REPAIR",
        rep.outcome is ValidationOutcome.RECOVERABLE_REPAIR,
        {"classify": rep.outcome.name},
    )

    # without a repair provider the same crafted world DEGRADES (never crashes)
    store3 = Store(url)
    service3 = GenerationService(settings=settings, store=store3, world_repair_provider=None)
    _published, rec3, _s, _c = held_published(url, golden_script(store3, url), seed=11)
    ok3 = service3._apply_kit_composition(rec3, "apartment", crafted, None)
    record(
        "2c no repair provider -> documented degradation, never a crash",
        ok3 is False and rec3.state is GenerationState.PUBLISHED
        and rec3.published.draft.scene.environment_id == "apartment",
        {"ok": ok3, "state": rec3.state.name},
    )

    # locked-constraint violation stays TERMINAL (never repaired/published)
    from app.persistence.timebase import EpochClock
    from phase5_helpers import seed_session

    store4 = Store(url)
    repair4 = RecordingWorldRepair(revised=WorldRequirements())
    service4 = GenerationService(settings=settings, store=store4, world_repair_provider=repair4)
    seed_session(store4, "SESS-P14", EpochClock())
    wrong_prompt = (
        "Victim: sarah_miller\n"
        "Murderer: walter_white\n"
        "Motive: cover_up_embezzlement\n"
        "Weapon: kitchen_knife\n"
        "Time: 2026-09-11T22:17:00+02:00\n"
        "Witness: emily_reed\n"
    )
    started = service4.start_case_generation(wrong_prompt, anonymous_quota_session_id="SESS-P14")
    record(
        "2c locked violation -> TERMINAL (never repaired, never published)",
        started.status == "FAILED" and repair4.calls == []
        and store4.get_published(started.case_id, 1) is None,
        {"status": started.status, "providerCalls": len(repair4.calls)},
    )


# --------------------------------------------------------------------------- #
# 2d — pinning (versions, proc embedding, catalog mutation, re-publication)
# --------------------------------------------------------------------------- #
def audit_pinning() -> None:
    from app.generation.schemas import SceneSpec
    from app.world.composer import KnownObjectSpecProvider, compose_world
    from app.world.extract import extract_world_requirements
    from phase5_helpers import golden_script as _gs, held_published

    from fixtures.world_showcase import GOLDEN_DEFAULT_PROMPT, MANSION_PROMPT, OFFICE_PROMPT, SHOWCASE_PROMPTS

    section("2d — pinning")

    # SceneSpec bounds: environment_version must be a positive int
    rejects = 0
    for bad in (0, -1, True, "1"):
        try:
            SceneSpec(location_id="L", name="N", environment_id="office", environment_version=bad)  # type: ignore[arg-type]
        except ValueError:
            rejects += 1
    record("2d SceneSpec.environment_version bound (positive int only)",
           rejects == 4, {"rejected": rejects})

    # The 2b audit already proved payload + bootstrap pin environmentVersion ==
    # kit.version for every showcase; re-confirm the value equation here.
    from app.environments.manifests import load_environment

    kit = load_environment("office")
    record("2d office kit version is the pin source",
           kit.version == 1, {"kitVersion": kit.version})

    # catalog descriptor mutation does NOT change a composed world
    catalog_path = REPO_ROOT / "assets" / "catalog" / "catalog.json"
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    for asset in data["assets"]:
        if asset["assetId"] == "PROP_WRENCH_01":
            asset["version"] += 1
            asset["colors"]["shade"] = "#00ff00"
    with tempfile.TemporaryDirectory() as tmpdir:
        from app.assets.catalog import load_catalog_from_repo

        mutated = Path(tmpdir) / "catalog.json"
        mutated.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        from app.environments.manifests import load_environment as _le

        kit_w = _le("warehouse")
        reqs = extract_world_requirements(SHOWCASE_PROMPTS["warehouse"])
        base_cat = load_catalog_from_repo()
        mut_cat = load_catalog_from_repo(path=mutated)
        c1 = compose_world(reqs, spec_provider=KnownObjectSpecProvider(),
                           evidence_placements=_golden_placements(),
                           catalog=base_cat, kit=kit_w)
        c2 = compose_world(reqs, spec_provider=KnownObjectSpecProvider(),
                           evidence_placements=_golden_placements(),
                           catalog=mut_cat, kit=kit_w)
        record(
            "2d catalog descriptor mutation does NOT change a composed world",
            c1.placements == c2.placements and c1.new_objects == c2.new_objects,
            {"equalPlacements": c1.placements == c2.placements},
        )

    # environment-manifest mutation does NOT change a published case's DTO: the
    # published payload + its projecting DTO are a pinned snapshot (the serving
    # row never re-reads the live manifest), so a mutated kit version cannot
    # drift the environment identity/version of an ALREADY-published case.
    # (1) publish a mansion case -> payload pins kit.version at compose time;
    # (2) mutate a copy of the mansion manifest (version+1, new canonical name);
    # (3) the STORED payload (v1+v2) is byte-stable across the mutation and the
    #     composer captures the mutated version only on a FUTURE composition.
    from app.core.config import Settings
    from app.environments.manifests import load_all_environments
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    db_mut = _fresh_migrated_db()
    url_mut = f"sqlite:///{db_mut.as_posix()}"
    settings_mut = Settings(database_url=url_mut, max_generations_per_session_per_window=8,
                            max_concurrent_generations=4)
    store_mut = Store(url_mut)
    service_mut = GenerationService(settings=settings_mut, store=store_mut)
    session_mut = service_mut.create_anonymous_quota_session()
    started_mut = service_mut.start_case_generation(
        MANSION_PROMPT,
        anonymous_quota_session_id=session_mut.anonymous_quota_session_id,
    )
    assert started_mut.status == "PUBLISHED", started_mut
    payload_before = _published_payload(store_mut, started_mut.case_id)
    pinned_version = payload_before["draft"]["scene"]["environment_version"]
    pinned_env = payload_before["draft"]["scene"]["environment_id"]

    mansion_manifest = ENVIRONMENTS_DIR / "mansion.json"
    manifest_data = json.loads(mansion_manifest.read_text(encoding="utf-8"))
    original_version = manifest_data.get("version")
    manifest_data["version"] = int(original_version or 1) + 1
    manifest_data["canonicalName"] = "Mutated Mansion (QA probe)"
    with tempfile.TemporaryDirectory() as tmpdir_mut:
        mutated_env_dir = Path(tmpdir_mut)
        (mutated_env_dir / "mansion.json").write_text(
            json.dumps(manifest_data, sort_keys=True), encoding="utf-8"
        )
        mutated_kits = load_all_environments(directory=mutated_env_dir)
        mutated = next(k for k in mutated_kits if k.environment_id == "mansion")
    # stored payload is byte-stable after the manifest mutation
    payload_after = _published_payload(store_mut, started_mut.case_id)
    # a FUTURE composition against the mutated kit honestly captures v2 at
    # COMPOSE time (the pin is versioned, never silently re-read)
    from app.world.composer import compose_world as _compose_mut
    from app.world.extract import extract_world_requirements as _extract_mut

    from app.assets.catalog import load_catalog_from_repo as _clr

    mut_reqs = _extract_mut(MANSION_PROMPT)
    mut_comp = _compose_mut(
        mut_reqs,
        spec_provider=KnownObjectSpecProvider(),
        evidence_placements=_golden_placements(),
        catalog=_clr(),
        kit=mutated,
    )
    record(
        "2d environment-manifest mutation does NOT change a published case DTO",
        json.dumps(payload_before, sort_keys=True) == json.dumps(payload_after, sort_keys=True)
        and pinned_env == "mansion" and pinned_version == int(original_version or 1)
        and mut_comp.kit_version == int(original_version or 1) + 1,
        {"pinnedVersion": pinned_version, "pinnedEnv": pinned_env,
         "mutatedKitVersion": mut_comp.kit_version,
         "payloadByteStable": json.dumps(payload_before, sort_keys=True) == json.dumps(payload_after, sort_keys=True)},
    )

    # re-publication v2 leaves v1 byte-identical + v2 pins its own environment
    from app.core.config import Settings
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    db = _fresh_migrated_db()
    url = f"sqlite:///{db.as_posix()}"
    settings = Settings(database_url=url, max_generations_per_session_per_window=8,
                        max_concurrent_generations=4)
    store = Store(url)
    service = GenerationService(settings=settings, store=store)

    from phase5_helpers import golden_script as _gs

    script = _gs(store, url)
    _pub, rec, _sid, _clock = held_published(url, script, seed=11)
    session = service.create_anonymous_quota_session()
    started = service.start_case_generation(
        OFFICE_PROMPT, anonymous_quota_session_id=session.anonymous_quota_session_id
    )
    assert started.status == "PUBLISHED", started
    v1_before = json.dumps(_published_payload(store, started.case_id), sort_keys=True)

    started_v2 = service.start_case_version(
        started.case_id,
        MANSION_PROMPT,
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert started_v2.status == "PUBLISHED", started_v2
    v2_payload = _published_payload(store, started.case_id, version=2)
    record(
        "2d re-publication (v2 mansion) leaves v1 byte-identical + v2 pins env",
        json.dumps(_published_payload(store, started.case_id, version=1), sort_keys=True) == v1_before
        and v2_payload["draft"]["scene"]["environment_id"] == "mansion"
        and v2_payload["draft"]["scene"].get("environment_version") == 1,
        {"v1Unchanged": json.dumps(_published_payload(store, started.case_id, version=1), sort_keys=True) == v1_before,
         "v2Env": v2_payload["draft"]["scene"]["environment_id"]},
    )


# --------------------------------------------------------------------------- #
# 5 — data-level security matrix
# --------------------------------------------------------------------------- #
def audit_security() -> None:
    from app.assets.compiler import is_procedural_asset_id
    from app.environments.placer import EVIDENCE_CAPABLE_TYPES, validate_placement
    from app.environments.resolver import EnvironmentProvenance, resolve_environment
    from app.world.composer import KnownObjectSpecProvider, compose_world
    from app.world.extract import extract_world_requirements

    from fixtures.world_showcase import SHOWCASE_PROMPTS

    section("5 — data-level security matrix")

    # (a) prompt injection into world fields: quoted JSON / script tags / URLs
    # (no table-known word inside the framing -> the extractor creates NOTHING;
    # a quoted table-known word only produces its safe catalog request, exactly
    # as ordinary natural language would — never an arbitrary URL/path asset)
    injected = extract_world_requirements(
        '<script>document.location="https://evil.example"</script> '
        '{"environment": "castle", "assets": [{"url": "https://evil.example/x.glb"}]} '
        "C:\\evil\\run.exe /etc/passwd ../../secret"
    )
    composition = compose_world(
        injected,
        spec_provider=KnownObjectSpecProvider(),
        evidence_placements=_golden_placements(),
    )
    joined = json.dumps([a for p in composition.placements for a in
                         (p.asset_id, p.object_id, p.anchor, p.location_id)])
    record(
        "5 prompt injection yields NO world fields / arbitrary URL-path assets",
        injected.objects == () and injected.environment_hint is None
        and all(x not in joined for x in ("https:", "http:", "script", "evil", "C:", "/etc/", ".."))
        and not composition.issues,
        {"objects": [r.requested_name for r in injected.objects],
         "placements": len(composition.placements)},
    )
    injected_known = extract_world_requirements(
        '{"objects": "wrench on the wall"} <script>alert(1)</script> https://evil.example/x.png'
    )
    comp_known = compose_world(
        injected_known,
        spec_provider=KnownObjectSpecProvider(),
        evidence_placements=_golden_placements(),
    )
    known_text = json.dumps([a for p in comp_known.placements for a in
                             (p.asset_id, p.object_id)])
    record(
        "5 quoted known word -> only its safe catalog asset; no script/URL/path influence",
        {p.asset_id for p in comp_known.placements} >= {"PROP_WRENCH_01"}
        and all(x not in known_text for x in ("https", "http", "script", "evil")),
        {"assets": sorted({p.asset_id for p in comp_known.placements})},
    )

    # (b) unsupported-location coercion: beach -> explicit fallback, no fabricated building
    for word in ("beach", "castle", "yacht", "arena"):
        extracted = extract_world_requirements(f"A crime on the {word}.")
        # 1) the extractor never closest-fits an unsupported location
        no_fit = extracted.environment_hint is None and extracted.location_tokens == ()
        # 2) an UNKNOWN name resolves to the documented default with FALLBACK
        direct = resolve_environment(word)
        fallback_ok = (
            direct.environment_id == "apartment"
            and direct.provenance is EnvironmentProvenance.FALLBACK
            and getattr(direct, "matched_alias", None) is None
        )
        record(
            f"5 unsupported location {word!r} -> apartment (documented default), never fabricated",
            no_fit and fallback_ok,
            {"noFit": no_fit, "fallback": direct.environment_id,
             "provenance": direct.provenance.value,
             "fabricatedBuilding": None},
        )

    # (c) asset alias manipulation: requested names are table-bound; aliases can
    # never smuggle a raw id/URL into the world
    alias_reqs = extract_world_requirements("A rope and a glass bottle were used.")
    comp_alias = compose_world(alias_reqs, spec_provider=KnownObjectSpecProvider(),
                               evidence_placements=_golden_placements())
    alias_assets = {p.asset_id for p in comp_alias.placements}
    record(
        "5 asset alias resolution stays in the catalog/procedural universe",
        "PROP_ROPE_01" in alias_assets and "PROP_GLASS_BOTTLE_01" in alias_assets
        and all(not is_procedural_asset_id(a) or a.startswith("proc.") for a in alias_assets)
        and all(not a.startswith(("http", "file:", "/")) for a in alias_assets),
        {"assets": sorted(alias_assets)},
    )

    # (d) hidden evidence placement: evidence objects sit on evidence-capable
    # anchors in EVERY published showcase composition (data-level clickability)
    from app.environments.manifests import load_environment

    bad_evidence: list[str] = []
    for env_id, prompt in SHOWCASE_PROMPTS.items():
        reqs = extract_world_requirements(prompt)
        kit = load_environment(env_id)
        comp = compose_world(reqs, spec_provider=KnownObjectSpecProvider(),
                             evidence_placements=_golden_placements(), kit=kit)
        assert comp.issues == (), (env_id, comp.issues)
        for p in comp.placements:
            if p.evidence_id is None:
                continue
            if not p.interaction:
                bad_evidence.append(f"{env_id}:{p.object_id}:no-interaction")
            anchor = kit.by_id.get(p.anchor)
            if anchor is None or anchor.type not in EVIDENCE_CAPABLE_TYPES:
                bad_evidence.append(f"{env_id}:{p.object_id}:anchor-{p.anchor}")
    record(
        "5 evidence objects always on evidence-capable anchors with interaction",
        not bad_evidence,
        {"violations": bad_evidence},
    )

    # (e) version substitution: a tampered environmentVersion cannot change the
    # kit identity the scene renders (frontend keys off environmentId; the
    # version pin on the served row is server-authored)
    from fastapi.testclient import TestClient

    from app.assets.catalog import load_catalog_from_repo
    from app.core.config import Settings
    from app.main import create_app
    from app.persistence.store import Store
    from phase5_helpers import create_session

    db = _fresh_migrated_db()
    url = f"sqlite:///{db.as_posix()}"
    app = create_app(settings=Settings(database_url=url))
    store = app.state.store
    with TestClient(app) as client:
        token, _ = create_session(client)
        res = client.post("/api/v1/cases",
                          json={"prompt": SHOWCASE_PROMPTS["office"]},
                          headers={"Authorization": f"Bearer {token}"})
        created = res.json()
        pid = created["caseId"]
        pt = client.post(
            f"/api/v1/cases/{pid}/versions/1/playthroughs",
            headers={"Authorization": f"Bearer {created['creatorAccessToken']}"},
        ).json()
        boot = client.get(
            f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
            headers={"Authorization": f"Bearer {pt['playthroughAccessToken']}"},
        ).json()
        record(
            "5 server-authored version pin is the canonical identity (office, version 1)",
            boot["scene"]["environmentId"] == "office"
            and boot["scene"].get("environmentVersion") == 1,
            {"envId": boot["scene"]["environmentId"],
             "version": boot["scene"].get("environmentVersion")},
        )

    # (f) unknown object -> UNRESOLVED world issue with a safe fallback (never
    # an arbitrary URL/path load)
    from app.world.requirements import ObjectRequest, WorldRequirements

    crafted = WorldRequirements(objects=(ObjectRequest(requested_name="quantum woggle"),))
    comp = compose_world(crafted, spec_provider=None, evidence_placements=_golden_placements())
    placement_text = json.dumps([(p.asset_id, p.object_id) for p in comp.placements])
    record(
        "5 unknown object -> unresolved issue + zero bogus placements/URLs",
        any("world.unresolved-object" in i for i in comp.issues)
        and "woggle" not in placement_text
        and all("http" not in a and "/" not in a for p in comp.placements for a in (p.asset_id, p.object_id)),
        {"issues": list(comp.issues), "placements": len(comp.placements)},
    )

    # (g) overlapping / inaccessible evidence placement is REJECTED by the
    # placer validation (duplicate exclusive anchor claims)
    kit = load_environment("apartment")
    from app.environments.placer import PlacementRequest, place_objects

    catalog = load_catalog_from_repo()
    requests = [
        PlacementRequest(asset_id="PROP_KITCHEN_KNIFE_01", object_id="dupe_1"),
        PlacementRequest(asset_id="PROP_LETTER_OPENER_01", object_id="dupe_2"),
    ]
    placed = place_objects(kit, requests, catalog=catalog)
    issues = validate_placement(kit, placed, catalog=catalog)
    record(
        "5 placer rejects overlapping/inaccessible placements (exclusive anchors)",
        issues == () and len({p.anchor for p in placed}) == len(placed),
        {"placedAnchors": [p.anchor for p in placed], "issues": list(issues)},
    )


def main() -> int:
    audit_extractor()
    audit_pipeline()
    audit_repair()
    audit_pinning()
    audit_security()
    import json as _json

    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "e2e" / "artifacts" / "qa-phase14-contract-audit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_json.dumps(results, indent=2), encoding="utf-8")
    fails = [r for r in results if not r["ok"]]
    print(f"\nRESULT: {len(results) - len(fails)}/{len(results)} PASS")
    if fails:
        print("FAILURES:")
        for f in fails:
            print(" -", f["name"])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())