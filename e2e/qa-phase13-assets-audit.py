"""QA-owned Phase 13 CONTRACT AUDIT (independent; e2e/qa-*.py series).

Covers the Phase 13 gate task 2 (a-c) against the REAL repo catalog / manifests /
modules with a REAL migrated scratch DB + TestClient + in-process probes:

  2a. ORACLE RESOLUTION + CACHE: the 4 golden unknown examples resolve through
      resolve_or_generate with a CountingSpecProvider: each yields a proc.*
      assetId with provenance PROCEDURAL_GENERATED and a valid frozen
      definition; resolving the SAME requestedName twice = exactly 1 provider
      call (same id from the cache); a DIFFERENT name = a new call/id.
  2b. COMPILER SAFETY MATRIX: unknown primitives (capsule/extruded), >24 parts,
      NaN/Inf, out-of-bounds dims/position/scale, path/URL/script/onload-role
      strings, >2-deep parent chains, duplicate part ids, forward parents — all
      REJECTED with deterministic issues (never coerced); normalize_spec
      deterministic (part-order free, byte-identical across fresh parses);
      content-addressed ids differ under compiler/schema version change; every
      compiled definition is immutable (setattr raises).
  2c. INTEGRATION (generate_unknown_assets + FakeAssetSpecProvider on a real
      GenerationService over a migrated scratch DB + TestClient):
      - the published payload EMBEDS the generated definitions (world-graph
        placements carry generated_definition; draft.objects add the 4 proc.*
        objects) with zero world-graph issues;
      - the real investigation bootstrap (POST playthroughs + GET investigation
        over HTTP) carries a proc.* world object whose `generated` block is
        present, validated per the current compiler/schema, bounded, and the
        server's canonicalName never reaches a label-like field (the DTO has no
        label; scene text is app-authored);
      - a TAMPERED payload (hitbox 1e308 / wrong assetId / version 999) ->
        bootstrapping SKIPS the tampered object (sanitized) while neighbors
        still render — over BOTH project_world_objects (in-process) AND the
        real HTTP bootstrap (crafted payload persisted into published_versions);
      - world-graph validation rejects a proc.* placement without an embedded
        def (and a mismatched assetId);
      - the 4 generated definitions place into office + hotel_suite kits with
        ZERO placement-validation issues (REQUIRED "two environment kits").

Run:  python e2e/qa-phase13-assets-audit.py
Output JSON: argv[1] or e2e/artifacts/qa-phase13-assets-audit.json
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
ENVIRONMENTS_DIR = REPO_ROOT / "assets" / "environments"
sys.path.insert(0, str(BACKEND_DIR))
# Phase 13 golden-spec fixtures live under backend/tests/fixtures.
sys.path.insert(0, str(BACKEND_DIR / "tests"))

GOLDEN_PROMPT = "Victim: sarah_miller\nMurderer: thomas_reed\n"

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:420]}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------- #
# 2a — oracle resolution + bounded cache
# --------------------------------------------------------------------------- #
def audit_oracle() -> None:
    from fixtures.asset_specs import (
        ANTIQUE_LETTER_OPENER_NAME,
        CUSTOM_TROPHY_NAME,
        DESK_AWARD_NAME,
        GOLDEN_SPEC_CONTENT,
        LAB_SAMPLE_RACK_NAME,
    )
    from app.assets.catalog import load_catalog_from_repo
    from app.assets.compiler import (
        COMPILER_VERSION,
        SCHEMA_VERSION,
        is_procedural_asset_id,
    )
    from app.assets.oracle import GeneratedAssetOracle
    from app.assets.resolver import AssetRequest, Provenance
    from app.assets.spec_provider import CountingSpecProvider, FakeAssetSpecProvider

    section("2a — oracle resolution + bounded cache")
    catalog = load_catalog_from_repo()
    counting = CountingSpecProvider(FakeAssetSpecProvider(GOLDEN_SPEC_CONTENT))
    oracle = GeneratedAssetOracle(catalog=catalog)

    names = (ANTIQUE_LETTER_OPENER_NAME, LAB_SAMPLE_RACK_NAME, CUSTOM_TROPHY_NAME, DESK_AWARD_NAME)
    for name in names:
        outcome = oracle.resolve_or_generate(
            AssetRequest(requested_name=name),
            spec_provider=counting,
            force_generate=True,
        )
        gen = outcome.generated
        ok = (
            outcome.error is None
            and gen is not None
            and gen.provenance is Provenance.PROCEDURAL_GENERATED
            and is_procedural_asset_id(gen.asset_id)
            and gen.asset_id.startswith("proc.")
            and gen.definition is not None
        )
        detail = {
            "assetId": gen.asset_id if gen else None,
            "provenance": gen.provenance.value if gen else None,
            "compilerVersion": gen.compiler_version if gen else None,
            "schemaVersion": gen.schema_version if gen else None,
            "parts": len(gen.definition.parts) if gen and gen.definition else None,
        }
        record(
            f"2a golden resolves {name!r} -> proc.* PROCEDURAL_GENERATED valid definition",
            ok,
            detail,
        )

    # Cache: SAME requestedName twice -> 1 provider call, same id. (Fresh
    # oracle + fresh counting provider so no earlier resolve pollutes counts.)
    counting2 = CountingSpecProvider(FakeAssetSpecProvider(GOLDEN_SPEC_CONTENT))
    oracle2 = GeneratedAssetOracle(catalog=catalog)
    first = oracle2.resolve_or_generate(
        AssetRequest(requested_name=CUSTOM_TROPHY_NAME),
        spec_provider=counting2,
        force_generate=True,
    )
    second = oracle2.resolve_or_generate(
        AssetRequest(requested_name=CUSTOM_TROPHY_NAME),
        spec_provider=counting2,
        force_generate=True,
    )
    record(
        "2a SAME requestedName twice -> exactly 1 provider call, same id",
        counting2.call_count == 1
        and first.generated is not None
        and second.generated is not None
        and first.generated.asset_id == second.generated.asset_id,
        {
            "calls_after_first": counting2.call_count,
            "calls_after_second": counting2.call_count,
            "firstId": first.generated.asset_id if first.generated else None,
            "secondId": second.generated.asset_id if second.generated else None,
        },
    )
    # DIFFERENT name -> a NEW provider call and a DIFFERENT id.
    before2 = counting2.call_count
    third = oracle2.resolve_or_generate(
        AssetRequest(requested_name=DESK_AWARD_NAME),
        spec_provider=counting2,
        force_generate=True,
    )
    record(
        "2a DIFFERENT name -> new provider call + distinct id",
        counting2.call_count - before2 == 1
        and third.generated is not None
        and third.generated.asset_id != second.generated.asset_id,
        {
            "newCalls": counting2.call_count - before2,
            "previousId": second.generated.asset_id if second.generated else None,
            "newId": third.generated.asset_id if third.generated else None,
        },
    )

    # Definitions are the frozen type (immutability proved in 2b).
    record(
        "2a versions pinned to current compiler/schema",
        first.generated is not None
        and first.generated.compiler_version == COMPILER_VERSION
        and first.generated.schema_version == SCHEMA_VERSION,
        {
            "compilerVersion": first.generated.compiler_version if first.generated else None,
            "schemaVersion": first.generated.schema_version if first.generated else None,
        },
    )


# --------------------------------------------------------------------------- #
# 2b — compiler / parser safety matrix
# --------------------------------------------------------------------------- #
def _base_spec() -> dict:
    return {
        "canonicalName": "Safe Probe",
        "category": "decor",
        "subtype": None,
        "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
        "parts": [
            {
                "id": "part_00",
                "role": "base",
                "primitive": "box",
                "transform": {
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.2, "y": 0.2, "z": 0.2},
                },
                "material": "plastic",
            }
        ],
    }


def audit_compiler_safety() -> None:
    from app.assets.compiler import asset_id_for, compile_asset_spec
    from app.assets.specs import (
        AssetSpec,
        AssetSpecError,
        normalize_spec,
        parse_asset_spec,
        spec_hash,
        validate_asset_spec,
    )

    section("2b — compiler / parser safety matrix")

    def _rejected(name: str, mutate) -> None:
        spec = mutate(_base_spec())
        issues = validate_asset_spec(spec)
        raised = False
        try:
            parse_asset_spec(spec, non_throwing=False)
        except AssetSpecError as exc:
            raised = True
            issues = exc.issues
        record(name, bool(issues) and raised, list(issues)[:6])

    # unknown primitives (REJECT, never coerce onto another primitive)
    for primitive in ("capsule", "extruded_polygon", "extruded", "prism"):
        spec = _base_spec()
        spec["parts"][0]["primitive"] = primitive
        issues = validate_asset_spec(spec)
        record(
            f"2b unknown primitive {primitive!r} rejected (never coerced)",
            bool(issues) and any("not supported" in i for i in issues),
            list(issues)[:4],
        )

    # >24 parts
    spec = _base_spec()
    for i in range(25):
        spec["parts"].append(
            {
                "id": f"part_{i:02d}",
                "role": "base",
                "primitive": "box",
                "transform": {
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.1, "y": 0.1, "z": 0.1},
                },
                "material": "plastic",
            }
        )
    issues = validate_asset_spec(spec)
    record(
        "2b >24 parts rejected",
        bool(issues) and any("exceeds the maximum of 24" in i for i in issues),
        list(issues)[:3],
    )

    # NaN / Inf (dict objects + JSON "1e999" -> inf)
    for label, field in (("dimensions.x", None), (None, None)):
        for bad_name, bad_value in (
            ("NaN dims", float("nan")),
            ("Inf dims", float("inf")),
            ("-Inf dims", float("-inf")),
        ):
            spec = _base_spec()
            spec["dimensions"]["x"] = bad_value
            issues = validate_asset_spec(spec)
            record(
                f"2b {bad_name} rejected (finite only)",
                bool(issues) and any("must be finite" in i for i in issues),
                list(issues)[:2],
            )
    for bad in (float("nan"), float("inf"), float("-inf")):
        spec = _base_spec()
        spec["parts"][0]["transform"]["scale"]["x"] = bad
        issues = validate_asset_spec(spec)
        record(
            f"2b part scale {bad!r} rejected (finite only)",
            bool(issues) and any("must be finite" in i for i in issues),
            list(issues)[:2],
        )
    json_inf = '{"canonicalName":"J","category":"decor","dimensions":{"x":1e999,"y":0.3,"z":0.3},"parts":[{"id":"part_00","role":"base","primitive":"box","transform":{"position":{"x":0,"y":0,"z":0},"rotation":{"x":0,"y":0,"z":0},"scale":{"x":0.2,"y":0.2,"z":0.2}},"material":"plastic"}]}'
    issues = validate_asset_spec(json_inf)
    record(
        "2b JSON '1e999' decodes to inf -> rejected as non-finite",
        bool(issues) and any("must be finite" in i for i in issues),
        list(issues)[:2],
    )

    # out-of-bounds dims / position / scale
    cases = [
        ("2b dimension below minimum (0.01)", "dimensions", {"x": 0.01, "y": 0.3, "z": 0.3}, "within [0.05, 4]"),
        ("2b dimension above maximum (5.0)", "dimensions", {"x": 5.0, "y": 0.3, "z": 0.3}, "within [0.05, 4]"),
        ("2b part position out of bounds (5.0)", "position", {"x": 5.0, "y": 0.0, "z": 0.0}, "within [-4, 4]"),
        ("2b part scale below minimum (0.01)", "scale", {"x": 0.01, "y": 0.2, "z": 0.2}, "within [0.05, 2]"),
        ("2b part scale above maximum (3.0)", "scale", {"x": 3.0, "y": 0.2, "z": 0.2}, "within [0.05, 2]"),
        ("2b part rotation out of bounds (7.0 > 2pi)", "rotation", {"x": 7.0, "y": 0.0, "z": 0.0}, "within [-6.28"),
    ]
    for name, which, value, needle in cases:
        spec = _base_spec()
        if which == "dimensions":
            spec["dimensions"] = value
        else:
            spec["parts"][0]["transform"][which] = value
        issues = validate_asset_spec(spec)
        record(name, bool(issues), list(issues)[:2])

    # path / URL / script / event-handler strings
    string_cases = [
        ("canonicalName URL scheme", "canonicalName", "http://evil.example/logo.png"),
        ("canonicalName javascript scheme", "canonicalName", "javascript:alert(1)"),
        ("canonicalName path traversal", "canonicalName", "../../etc/passwd"),
        ("canonicalName absolute path", "canonicalName", "/etc/shadow"),
        ("canonicalName drive path", "canonicalName", "C:\\windows\\system32"),
        ("canonicalName script token", "canonicalName", "click script tag"),
        ("material URL string", "material", "plastic#http://x"),
        ("role onload handler", "role", "onload"),
        ("role onclick handler", "role", "onclick"),
    ]
    for name, key, bad in string_cases:
        spec = _base_spec()
        if key == "canonicalName":
            spec["canonicalName"] = bad
        elif key == "material":
            spec["parts"][0]["material"] = bad
        else:
            spec["parts"][0]["role"] = bad
        issues = validate_asset_spec(spec)
        record(name, bool(issues), list(issues)[:3])

    # >2-deep parent chains (base -> stem -> cup -> handle = depth 3)
    spec = _base_spec()
    spec["parts"] = [
        {"id": "part_00", "role": "base", "primitive": "box",
         "transform": {"position": {"x": 0, "y": 0, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0}, "scale": {"x": 0.2, "y": 0.2, "z": 0.2}}, "material": "plastic"},
        {"id": "part_01", "role": "stem", "primitive": "box", "parentId": "part_00",
         "transform": {"position": {"x": 0, "y": 0.2, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0}, "scale": {"x": 0.1, "y": 0.2, "z": 0.1}}, "material": "plastic"},
        {"id": "part_02", "role": "cup", "primitive": "box", "parentId": "part_01",
         "transform": {"position": {"x": 0, "y": 0.2, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0}, "scale": {"x": 0.1, "y": 0.2, "z": 0.1}}, "material": "plastic"},
        {"id": "part_03", "role": "handle", "primitive": "box", "parentId": "part_02",
         "transform": {"position": {"x": 0, "y": 0.2, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0}, "scale": {"x": 0.1, "y": 0.2, "z": 0.1}}, "material": "plastic"},
    ]
    issues = validate_asset_spec(spec)
    record(
        "2b parent chain depth >2 rejected",
        bool(issues) and any("maximum nesting depth of 2" in i for i in issues),
        list(issues)[:2],
    )

    # duplicate part ids + forward parent
    dup = _base_spec()
    dup["parts"].append({**dup["parts"][0]})
    issues = validate_asset_spec(dup)
    record(
        "2b duplicate part ids rejected",
        bool(issues) and any("duplicate part id" in i for i in issues),
        list(issues)[:2],
    )
    fwd = _base_spec()
    fwd["parts"][0]["parentId"] = "part_01"
    fwd["parts"].append(
        {"id": "part_01", "role": "cap", "primitive": "box",
         "transform": {"position": {"x": 0, "y": 0.2, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0}, "scale": {"x": 0.1, "y": 0.1, "z": 0.1}}, "material": "plastic"}
    )
    issues = validate_asset_spec(fwd)
    record(
        "2b forward parent (earlier part required) rejected",
        bool(issues) and any("EARLIER" in i for i in issues),
        list(issues)[:2],
    )

    # normalize_spec deterministic + part-order free + byte-identical re-parses
    from fixtures.asset_specs import (
        ANTIQUE_LETTER_OPENER_NAME,
        ANTIQUE_LETTER_OPENER_SPEC,
        GOLDEN_SPEC_CONTENT,
    )

    a = parse_asset_spec(ANTIQUE_LETTER_OPENER_SPEC, non_throwing=False)
    b = parse_asset_spec(ANTIQUE_LETTER_OPENER_SPEC, non_throwing=False)
    parts_reversed = {"asset_ok": True}
    # build the same spec with parts in reversed input order
    data = json.loads(ANTIQUE_LETTER_OPENER_SPEC)
    data["parts"] = list(reversed(data["parts"]))
    c = parse_asset_spec(data, non_throwing=False)
    record(
        "2b normalize_spec deterministic (re-parse + part-order free)",
        normalize_spec(a) == normalize_spec(b) == normalize_spec(c)
        and spec_hash(a) == spec_hash(b),
        {
            "a==b": normalize_spec(a) == normalize_spec(b),
            "a==c_reordered": normalize_spec(a) == normalize_spec(c),
            "hash": spec_hash(a)[:16],
        },
    )

    # content-addressed ids differ under version change
    one = asset_id_for(a)
    two = asset_id_for(a, compiler_version=2)
    three = asset_id_for(a, schema_version=2)
    record(
        "2b content-addressed ids differ under compiler/schema version change",
        one != two and one != three and isinstance(one, str) and one.startswith("proc."),
        {"v1": one, "compilerV2": two, "schemaV2": three},
    )

    # definitions frozen: setattr raises, parts tuple immutable
    definition = compile_asset_spec(a)
    frozen_ok = False
    try:
        definition.parts = ()  # type: ignore[misc]
    except AttributeError:
        frozen_ok = True
    part_frozen = False
    try:
        definition.parts[0].color = "#000000"  # type: ignore[misc]
    except AttributeError:
        part_frozen = True
    record(
        "2b definitions frozen (setattr raises on definition AND parts)",
        frozen_ok and part_frozen,
        {"definition": frozen_ok, "parts": part_frozen},
    )

    # compile rejects a hand-built out-of-bounds definition (never coerced)
    raises_compile = False
    try:
        from app.assets.specs import AssetSpecPart, SpecTransform

        bad = AssetSpec(
            canonical_name="X",
            category="decor",
            subtype=None,
            dimensions=(0.3, 0.3, 0.3),
            parts=(AssetSpecPart(
                id="part_00", role="base", primitive="box",
                transform=SpecTransform(position=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0), scale=(0.2, 0.2, 0.2)),
                material="plastic"),) * 3,
        )
    except AssetSpecError:
        # 3 same-id parts = duplicate ids -> constructor rejects (never coerced)
        raises_compile = True
    record("2b AssetSpec constructor rejects duplicate/invalid part sets", raises_compile, {"constructor": raises_compile})


# --------------------------------------------------------------------------- #
# 2c — integration: service generation -> published payload -> bootstrap
# --------------------------------------------------------------------------- #
def _fresh_migrated_db() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="qa_p13_audit_"))
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


def _published_payload(store, case_id) -> dict:
    row = store.get_published(case_id, 1)
    assert row is not None, "case must be published"
    return json.loads(row.payload_json)


def audit_integration() -> None:
    from fixtures.asset_specs import GOLDEN_SPEC_CONTENT, GOLDEN_UNKNOWN_REQUESTS
    from fastapi.testclient import TestClient

    from app.assets.catalog import load_catalog_from_repo
    from app.assets.compiler import (
        COMPILER_VERSION,
        SCHEMA_VERSION,
        is_procedural_asset_id,
        validate_embedded_definition,
    )
    from app.assets.generated_cache import GeneratedAssetCache
    from app.assets.spec_provider import FakeAssetSpecProvider
    from app.core.config import Settings
    from app.environments.manifests import load_all_environments
    from app.environments.placer import place_objects, validate_placement
    from app.generation.safety import validate_world_graph
    from app.main import create_app
    from app.persistence.store import Store
    from app.services.generation import GenerationService
    from app.services.publication import project_world_objects

    section("2c — service integration (generate_unknown_assets + fake provider)")
    db = _fresh_migrated_db()
    settings = Settings(database_url=f"sqlite:///{db.as_posix()}")
    store = Store(settings.database_url)

    service = GenerationService(
        settings=settings,
        store=store,
        publication=None,
        spec_provider=FakeAssetSpecProvider(GOLDEN_SPEC_CONTENT),
        generate_unknown_assets=True,
        generated_cache=GeneratedAssetCache(),
    )
    session = service.create_anonymous_quota_session()
    started = service.start_case_generation(
        GOLDEN_PROMPT,
        anonymous_quota_session_id=session.anonymous_quota_session_id,
        difficulty="medium",
        environment="office",
        unknown_asset_requests=list(GOLDEN_UNKNOWN_REQUESTS),
    )
    record(
        "2c start_case_generation(+unknown requests) -> PUBLISHED",
        started.status == "PUBLISHED",
        {"caseId": started.case_id, "status": started.status},
    )
    payload = _published_payload(store, started.case_id)
    draft = payload["draft"]

    # The published payload embeds the generated definitions.
    proc_object_ids = {
        obj["object_id"] for obj in draft["objects"]
        if is_procedural_asset_id(obj["asset_id"])
    }
    placements_with_defs = [
        p for p in draft["world_graph"]["placements"]
        if is_procedural_asset_id(p["asset_id"]) and p.get("generated_definition")
    ]
    record(
        "2c published payload embeds 4 proc.* objects + generated definitions",
        len(proc_object_ids) == 4 and len(placements_with_defs) == 4,
        {
            "procObjectIds": sorted(proc_object_ids),
            "proceduralPlacements": len(placements_with_defs),
        },
    )
    # every embedded definition validates per the CURRENT versions
    valid_embedded = all(
        validate_embedded_definition(p["asset_id"], p.get("generated_definition")) is not None
        for p in draft["world_graph"]["placements"]
        if is_procedural_asset_id(p["asset_id"])
    )
    record("2c all embedded generated definitions validate", valid_embedded, {"assets": sorted(proc_object_ids)})

    # world graph of the published payload carries no structural issues
    from app.generation.schemas import (
        PlacementSpec,
        WorldGraphLocationSpec,
        WorldGraphSpec,
    )

    wg = WorldGraphSpec(
        locations=tuple(
            WorldGraphLocationSpec(location_id=l["location_id"], template=l["template"])
            for l in draft["world_graph"]["locations"]
        ),
        placements=tuple(
            PlacementSpec(
                object_id=p["object_id"],
                asset_id=p["asset_id"],
                location_id=p["location_id"],
                anchor=p["anchor"],
                interaction=p["interaction"],
                evidence_id=p.get("evidence_id"),
                generated_definition=p.get("generated_definition"),
            )
            for p in draft["world_graph"]["placements"]
        ),
    )
    object_ids = {o["object_id"] for o in draft["objects"]}
    evidence_ids = {e["id"] for e in draft["evidence"]}
    issues = validate_world_graph(wg, object_ids, evidence_ids)
    record(
        "2c published world graph validates (no structural issues)",
        not issues,
        list(issues)[:5],
    )

    # --- real HTTP bootstrap: TestClient over the SAME db -------------------
    app = create_app(settings=settings)
    creator_token = started.creator_access_token
    with TestClient(app) as client:
        pt = client.post(
            f"/api/v1/cases/{started.case_id}/versions/1/playthroughs",
            headers={"Authorization": f"Bearer {creator_token}"},
        )
        record("2c POST playthroughs -> 201", pt.status_code == 201, pt.status_code)
        pt_body = pt.json()
        boot = client.get(
            f"/api/v1/playthroughs/{pt_body['playthroughId']}/investigation",
            headers={"Authorization": f"Bearer {pt_body['playthroughAccessToken']}"},
        )
        record("2c GET investigation -> 200", boot.status_code == 200, boot.status_code)
        world_objects = boot.json()["scene"]["worldObjects"]
        generated_objects = [o for o in world_objects if is_procedural_asset_id(o["assetId"])]
        record(
            "2c bootstrap carries proc.* world objects",
            len(generated_objects) == 4,
            {"generatedObjectIds": sorted(o["objectId"] for o in generated_objects)},
        )
        block_ok = all(
            o.get("generated") is not None
            and o["generated"]["assetId"] == o["assetId"]
            and o["generated"]["compilerVersion"] == COMPILER_VERSION
            and o["generated"]["schemaVersion"] == SCHEMA_VERSION
            for o in generated_objects
        )
        bounded = all(
            all(
                math.isfinite(v) and 0.15 <= v <= 10.0
                for v in o["generated"]["hitbox"]["scale"].values()
            )
            for o in generated_objects
        )
        record(
            "2c bootstrap `generated` blocks validated + bounded",
            block_ok and bounded,
            {
                "blockOk": block_ok,
                "hitboxBounded": bounded,
                "sampleId": generated_objects[0]["assetId"] if generated_objects else None,
            },
        )
        labels = [o.get("label") for o in generated_objects if "label" in o]
        record(
            "2c no label field on the proc.* DTO (server text cannot leak)",
            labels == [],
            {"labelKeysFound": labels},
        )

    # --- tampered payloads: bootstrap SKIPS invalid definitions ---------------
    tamper_a = dict(payload)
    placements = [dict(p) for p in draft["world_graph"]["placements"]]
    proc_placements = [p for p in placements if is_procedural_asset_id(p["asset_id"])]
    assert len(proc_placements) == 4
    p0, p1, p2, p3 = proc_placements[:4]
    # hitbox 1e308
    p0["generated_definition"] = {**p0["generated_definition"], "hitbox": {"scale": {"x": 1e308, "y": 1.0, "z": 1.0}}}
    # wrong assetId
    p1["generated_definition"] = {**p1["generated_definition"], "assetId": "proc.decor.0123456789abcdef"}
    # version 999 (schema-version confusion)
    p2["generated_definition"] = {**p2["generated_definition"], "schemaVersion": 999, "compilerVersion": 999}
    tamper_a["draft"] = {**draft, "world_graph": {**draft["world_graph"], "placements": placements}}
    projected = project_world_objects(tamper_a)
    ids = [o["objectId"] for o in projected]
    record(
        "2c tampered definitions (1e308 / wrong id / version 999) SKIPPED by projection; neighbors render",
        len(projected) >= 4 and all(x not in ids for x in (p0["object_id"], p1["object_id"], p2["object_id"]))
        and p3["object_id"] in ids,
        {"projectedIds": sorted(ids), "tampered": [p0["object_id"], p1["object_id"], p2["object_id"]]},
    )

    # Crafted payload persisted into published_versions -> REAL HTTP bootstrap.
    # (published_versions rows are immutable via DB triggers; the scratch COPY
    # drops those triggers so QA can inject the hostile payload — the real
    # endpoint then reads it through the exact get_published -> project path.)
    tampered_db = Path(tempfile.mkdtemp(prefix="qa_p13_tamper_")) / "audit.db"
    _copy_db(db, tampered_db)
    tamper_settings = Settings(database_url=f"sqlite:///{tampered_db.as_posix()}")
    # drop the published_versions immutability triggers on the copy, then rewrite
    con = sqlite3.connect(tampered_db)
    con.execute("DROP TRIGGER IF EXISTS published_versions_no_update")
    con.execute("DROP TRIGGER IF EXISTS published_versions_no_delete")
    con.execute(
        "UPDATE published_versions SET payload_json=? WHERE case_id=? AND case_version=1",
        (json.dumps(tamper_a), started.case_id),
    )
    con.commit()
    con.close()
    app2 = create_app(settings=tamper_settings)
    with TestClient(app2) as client:
        pt = client.post(
            f"/api/v1/cases/{started.case_id}/versions/1/playthroughs",
            headers={"Authorization": f"Bearer {creator_token}"},
        )
        pt2 = pt.json()
        boot2 = client.get(
            f"/api/v1/playthroughs/{pt2['playthroughId']}/investigation",
            headers={"Authorization": f"Bearer {pt2['playthroughAccessToken']}"},
        )
        ids2 = [o["objectId"] for o in boot2.json()["scene"]["worldObjects"]]
        record(
            "2c REAL HTTP bootstrap over crafted payload: tampered objects SKIPPED, neighbors render",
            boot2.status_code == 200
            and all(x not in ids2 for x in (p0["object_id"], p1["object_id"], p2["object_id"]))
            and p3["object_id"] in ids2,
            {"status": boot2.status_code, "projected": sorted(ids2)},
        )

    # --- world-graph validation rule: proc.* placement needs an embedded def --
    from app.generation.schemas import PlacementSpec, WorldGraphLocationSpec, WorldGraphSpec
    from app.generation.safety import ANCHOR_ALLOWLIST

    catalog = load_catalog_from_repo()
    kits = load_all_environments(directory=ENVIRONMENTS_DIR)
    office = next(k for k in kits if k.environment_id == "office")
    anchor_id = next(iter(office.anchors)).anchor_id
    proc_asset_id = proc_placements[0]["asset_id"]
    wg_missing = WorldGraphSpec(
        locations=(WorldGraphLocationSpec(location_id="loc_01", template="t"),),
        placements=(
            PlacementSpec(
                object_id="orphan_proc",
                asset_id=proc_asset_id,
                location_id="loc_01",
                anchor=anchor_id,
                interaction="",
                evidence_id=None,
                generated_definition=None,
            ),
        ),
    )
    wg_issues = validate_world_graph(wg_missing, {"orphan_proc"}, set())
    record(
        "2c world graph REJECTS proc.* placement without embedded def",
        any("requires an embedded generatedDefinition" in i for i in wg_issues),
        list(wg_issues)[:2],
    )
    wg_mismatch = WorldGraphSpec(
        locations=(WorldGraphLocationSpec(location_id="loc_01", template="t"),),
        placements=(
            PlacementSpec(
                object_id="orphan_proc",
                asset_id=proc_asset_id,
                location_id="loc_01",
                anchor=anchor_id,
                interaction="",
                evidence_id=None,
                generated_definition={"assetId": "proc.decor.0123456789abcdef"},
            ),
        ),
    )
    mm_issues = validate_world_graph(wg_mismatch, {"orphan_proc"}, set())
    record(
        "2c world graph REJECTS embedded-def/placement assetId mismatch",
        any("does not match the placement assetId" in i for i in mm_issues),
        list(mm_issues)[:2],
    )

    # --- placer: generated defs into office AND hotel_suite kits ---------------
    from fixtures.asset_specs import GOLDEN_SPEC_NAMES, GOLDEN_SPEC_CONTENT
    from app.assets.specs import parse_asset_spec
    from app.assets.compiler import compile_asset_spec
    from app.environments.placer import PlacementRequest

    definitions = {
        compile_asset_spec(parse_asset_spec(GOLDEN_SPEC_CONTENT[n.casefold().strip()], non_throwing=False)).asset_id:
        compile_asset_spec(parse_asset_spec(GOLDEN_SPEC_CONTENT[n.casefold().strip()], non_throwing=False))
        for n in GOLDEN_SPEC_NAMES
    }
    for kit_id in ("office", "hotel_suite"):
        kit = next(k for k in kits if k.environment_id == kit_id)
        requests = [
            PlacementRequest(asset_id=aid, object_id=f"gen_{i:02d}")
            for i, aid in enumerate(sorted(definitions))
        ]
        placed = place_objects(kit, requests, catalog=catalog, generated_definitions=definitions)
        issues = validate_placement(kit, placed, catalog=catalog, generated_definitions=definitions)
        record(
            f"2c generated assets place into {kit_id} with ZERO validation issues",
            len(placed) == 4 and issues == (),
            {"placedAnchors": [p.anchor for p in placed], "issues": list(issues)[:3]},
        )


def _copy_db(src: Path, dst: Path) -> None:
    con = sqlite3.connect(src)
    bak = sqlite3.connect(dst)
    con.backup(bak)
    bak.close()
    con.close()


def main() -> int:
    audit_oracle()
    audit_compiler_safety()
    audit_integration()
    import json

    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "e2e" / "artifacts" / "qa-phase13-assets-audit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    fails = [r for r in results if not r["ok"]]
    print(f"\nRESULT: {len(results) - len(fails)}/{len(results)} PASS")
    if fails:
        print("FAILURES:")
        for f in fails:
            print(" -", f["name"])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())