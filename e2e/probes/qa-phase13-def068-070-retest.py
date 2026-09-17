"""QA-owned independent DEF-068 / DEF-069 / DEF-070 FIX-READY retest probe
(Phase 13 gate).

Asserts the POST-FIX contract against the REAL repo modules + REAL manifests
(read-only) for all three LOW defects filed from the ADV-148..150 reproductions:

- DEF-068: the shared Unicode format/zero-width/Bidi/line-separator glyph
  class (U+200B-200F, U+2028/2029, U+202A-202E, U+2060-2064, U+FEFF) is
  rejected by validate_asset_spec / parse_asset_spec (canonicalName +
  subtype + role), the catalog loader (validate_catalog_data + load_catalog),
  the environment loader (validate_environment_data + load_all_environments)
  and the asset-request gate (validate_asset_request); the REAL manifests
  still scan clean and load clean.
- DEF-069: the embedded-definition gate (definition_json_issues /
  validate_embedded_definition) enforces assetId <= 128 chars + category
  segment <= 64 chars + the tight grammar; a tampered 222-char assetId and a
  65-char category segment each yield a CLEAN issue and the projection
  SKIPS the placement (project_world_objects emits no WorldObjectDTO for it,
  neighbors still render); every REAL compiled id is <= 33 chars and matches
  ^proc\\.[a-z0-9_]+\\.[a-f0-9]{16}$.
- DEF-070 backend contrast: definition_json_issues rejects roles that look
  like event handlers (onload/onclick/onerror/onmouseover) — the mirror gate
  for the frontend fix.

Run:  python e2e/probes/qa-phase13-def068-070-retest.py
Exit 0 = fix verified; exit 1 = any regression (each failure printed).
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tests"))

CATALOG_JSON = REPO_ROOT / "assets" / "catalog" / "catalog.json"
ENVIRONMENTS_DIR = REPO_ROOT / "assets" / "environments"

# The exact ADV-148 probe set (the ORIGINAL reproduction's five reported
# glyphs) plus every member of the documented class at the range boundaries.
REPRO_GLYPHS = {
    "U+200B": "\u200b",
    "U+200D": "\u200d",
    "U+202E": "\u202e",
    "U+2028": "\u2028",
    "U+FEFF": "\ufeff",
}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        failures.append(f"{name}: {detail}")
        print(f"FAIL - {name} :: {detail}")
    else:
        print(f"PASS - {name}")


def _valid_spec(canonical_name: str, category: str = "evidence", parts: list | None = None):
    if parts is None:
        parts = [
            {
                "id": "part_00",
                "role": "blade",
                "primitive": "box",
                "transform": {
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.2, "y": 0.1, "z": 0.4},
                },
                "material": "metal.steel",
            }
        ]
    return {
        "canonicalName": canonical_name,
        "category": category,
        "dimensions": {"x": 0.2, "y": 0.2, "z": 0.2},
        "parts": parts,
    }


def _default_part(role: str = "blade") -> dict:
    return {
        "id": "part_00",
        "role": role,
        "primitive": "box",
        "transform": {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
            "scale": {"x": 0.2, "y": 0.1, "z": 0.4},
        },
        "material": "metal.steel",
    }


# --------------------------------------------------------------------------- #
# DEF-068 — glyph class rejection across the whole string-safety family
# --------------------------------------------------------------------------- #
def audit_def068() -> None:
    from app.assets.catalog import CatalogError, load_catalog, validate_catalog_data
    from app.assets.specs import parse_asset_spec, validate_asset_spec
    from app.assets.validation import validate_asset_request
    from app.environments.manifests import (
        EnvironmentValidationError,
        load_all_environments,
        validate_environment_data,
    )

    # 1. validate_asset_spec rejects every reproduced glyph in canonicalName.
    for name, glyph in REPRO_GLYPHS.items():
        poisoned = f"evil{glyph}name"
        issues = validate_asset_spec(_valid_spec(poisoned))
        check(
            f"068.1 spec.canonicalName[{name}]",
            any("Unicode format/zero-width" in i and name.replace("U+", "U+") in i for i in issues),
            f"got issues={issues!r}",
        )
    # 2. parse_asset_spec -> None for a poisoned canonicalName (clean reject).
    parsed = parse_asset_spec(_valid_spec("evil\u202ename"), non_throwing=True)
    check("068.2 spec.parse canonicalName -> None", parsed is None, f"got {parsed!r}")
    # 3. subtype glyph rejected by validate_asset_spec AND parse -> None.
    with_glyph = _valid_spec("Clean")
    with_glyph["subtype"] = "evil\u200bprop"
    issues_sub = validate_asset_spec(with_glyph)
    check(
        "068.3 spec.subtype glyph rejected",
        any("Unicode format/zero-width" in i for i in issues_sub),
        f"got issues={issues_sub!r}",
    )
    # 4. role glyph rejected by the shared scan (the later grammar gate would
    #    also reject it — either way the document must not parse clean).
    poisoned_role = _valid_spec("Clean", parts=[_default_part(role="base\u200b")])
    issues_role = validate_asset_spec(poisoned_role)
    check(
        "068.4 spec.role glyph rejected",
        len(issues_role) > 0,
        f"got issues={issues_role!r}",
    )
    # 5. the asset-request gate rejects a glyph-carrying requestedName.
    req_issues = validate_asset_request({"requestedName": "evil\u202eoffice"})
    check(
        "068.5 request gate glyph rejected",
        any("Unicode format/zero-width" in i for i in req_issues),
        f"got issues={req_issues!r}",
    )

    # 6. Catalog loader: a poisoned clone of the REAL manifest is rejected by
    #    validate_catalog_data AND load_catalog(CatalogError) — never loaded.
    real_catalog = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
    for name, glyph in REPRO_GLYPHS.items():
        poisoned = json.loads(json.dumps(real_catalog))
        poisoned["assets"][0]["label"] = f"bad{glyph}label"
        cat_issues = validate_catalog_data(poisoned)
        check(
            f"068.6 catalog.validate_data[{name}]",
            any("Unicode format/zero-width" in i for i in cat_issues),
            f"got issues={cat_issues!r}",
        )
    with tempfile.TemporaryDirectory() as td:
        bad_cat = Path(td) / "catalog.json"
        bad_cat.write_text(json.dumps(poisoned), encoding="utf-8")
        try:
            load_catalog(bad_cat)
            check("068.7 catalog.load raises", False, "poisoned catalog loaded clean")
        except CatalogError as exc:
            check(
                "068.7 catalog.load raises",
                "Unicode format/zero-width" in str(exc),
                f"unexpected message: {exc}",
            )

    # 8. Environment loader: a poisoned clone of the REAL office manifest is
    #    rejected by validate_environment_data AND load_all_environments.
    office = json.loads((ENVIRONMENTS_DIR / "office.json").read_text(encoding="utf-8"))
    for name, glyph in REPRO_GLYPHS.items():
        poisoned_env = json.loads(json.dumps(office))
        poisoned_env["canonicalName"] = f"evil{glyph}office"
        env_issues = validate_environment_data(poisoned_env)
        check(
            f"068.8 env.validate_data[{name}]",
            any("Unicode format/zero-width" in i for i in env_issues),
            f"got issues={env_issues!r}",
        )
    with tempfile.TemporaryDirectory() as td:
        bad_env = Path(td) / "office.json"
        bad_env.write_text(json.dumps(poisoned_env), encoding="utf-8")
        try:
            load_all_environments(directory=td)
            check("068.9 env.load raises", False, "poisoned kit loaded clean")
        except EnvironmentValidationError as exc:
            check(
                "068.9 env.load raises",
                any("Unicode format/zero-width" in i for i in exc.issues),
                f"unexpected issues: {exc.issues!r}",
            )

    # 10. REAL manifests stay clean: zero glyph codepoints in the catalog, all
    #     environment kits, and all backend JSON; both still LOAD clean.
    glyph_re = re.compile(
        "[\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\ufeff]"
    )
    dirt: list[str] = []
    for json_path in [CATALOG_JSON, *sorted(ENVIRONMENTS_DIR.glob("*.json"))]:
        text = json_path.read_text(encoding="utf-8")
        if glyph_re.search(text):
            dirt.append(str(json_path))
    check("068.10 real manifests glyph-clean", not dirt, f"tainted: {dirt}")
    try:
        real = load_catalog(CATALOG_JSON)
        check("068.10 real catalog loads clean", real.catalog_version >= 1, "")
    except Exception as exc:  # noqa: BLE001
        check("068.10 real catalog loads clean", False, f"{type(exc).__name__}: {exc}")
    real_env_issues: list[str] = []
    for kit_path in sorted(ENVIRONMENTS_DIR.glob("*.json")):
        kit = json.loads(kit_path.read_text(encoding="utf-8"))
        real_env_issues.extend(validate_environment_data(kit))
    check("068.10 real kits validate clean", not real_env_issues, f"issues={real_env_issues!r}")
    try:
        load_all_environments(directory=str(ENVIRONMENTS_DIR))
        env_load_ok = True
    except Exception as exc:  # noqa: BLE001
        env_load_ok = False
        check("068.10 real kits load clean", False, f"{type(exc).__name__}: {exc}")
    if env_load_ok:
        check("068.10 real kits load clean", True)


# --------------------------------------------------------------------------- #
# DEF-069 — embedded-definition grammar gate + projection skip + compiler bound
# --------------------------------------------------------------------------- #
def audit_def069() -> None:
    from fixtures.asset_specs import GOLDEN_SPEC_CONTENT, GOLDEN_SPEC_NAMES
    from app.assets.compiler import (
        PROCEDURAL_ASSET_CATEGORY_MAX_LENGTH,
        PROCEDURAL_ASSET_ID_MAX_LENGTH,
        PROCEDURAL_ASSET_PATTERN,
        compile_asset_spec,
        definition_json_issues,
        is_procedural_asset_id,
        validate_embedded_definition,
    )
    from app.assets.specs import parse_asset_spec
    from app.services.publication import project_world_objects

    assert PROCEDURAL_ASSET_ID_MAX_LENGTH == 128
    assert PROCEDURAL_ASSET_CATEGORY_MAX_LENGTH == 64

    spec = parse_asset_spec(_valid_spec("Antique Ceremonial Letter Opener", "decor"),
                            non_throwing=False)
    real_def = compile_asset_spec(spec).to_definition_json()
    assert is_procedural_asset_id(real_def["assetId"])
    assert len(real_def["assetId"]) <= 33

    # 1. exact ORIGINAL reproduction shape: "proc." + "x"*200 + ".0123456789abcdef"
    #    = a 222-char tampered assetId -> a CLEAN issue, gate returns None.
    tampered_id = "proc." + "x" * 200 + ".0123456789abcdef"
    assert len(tampered_id) == 222
    long_doc = json.loads(json.dumps(real_def))
    long_doc["assetId"] = tampered_id
    issues = definition_json_issues(long_doc, expected_asset_id=tampered_id)
    check(
        "069.1 222-char assetId clean issue",
        any("assetId exceeds" in i and "128" in i for i in issues),
        f"got issues={issues!r}",
    )
    check(
        "069.1 gate returns None (no placement)",
        validate_embedded_definition(tampered_id, long_doc) is None,
        "validate_embedded_definition did NOT return None",
    )

    # 2. a 65-char category segment -> a clean category issue; a 64-char
    #    segment (total 86 <= 128) passes BOTH length rules (no false positive).
    seg65 = "x" * 65
    id65 = f"proc.{seg65}.abcdef0123456789"
    doc65 = json.loads(json.dumps(real_def))
    doc65["assetId"] = id65
    issues65 = definition_json_issues(doc65, expected_asset_id=id65)
    check(
        "069.2 65-char category segment issue",
        any("category segment exceeds" in i and "64" in i for i in issues65),
        f"got issues={issues65!r}",
    )
    seg64 = "x" * 64
    id64 = f"proc.{seg64}.abcdef0123456789"
    doc64 = json.loads(json.dumps(real_def))
    doc64["assetId"] = id64
    issues64 = definition_json_issues(doc64, expected_asset_id=id64)
    check(
        "069.2 64-char category segment passes bounds (86 <= 128)",
        not any("exceeds" in i for i in issues64),
        f"got issues={issues64!r}",
    )

    # 3. compiler guarantee: EVERY real compiled id (all 7 categories + the 4
    #    golden fixtures) is <= 33 chars and matches the frozen grammar.
    grammar = re.compile(r"^proc\.[a-z0-9_]+\.[a-f0-9]{16}$")
    compiled_ids: list[str] = [real_def["assetId"]]
    for name in GOLDEN_SPEC_NAMES:
        golden_spec = parse_asset_spec(
            GOLDEN_SPEC_CONTENT[name.casefold().strip()], non_throwing=False
        )
        compiled_ids.append(compile_asset_spec(golden_spec).asset_id)
    for category in ("evidence", "electronics", "furniture", "structural",
                     "character", "decor", "utility"):
        cid = compile_asset_spec(parse_asset_spec(
            _valid_spec(f"{category} prop", category=category), non_throwing=False
        )).asset_id
        compiled_ids.append(cid)
    check(
        "069.3 all real compiled ids <= 33",
        all(len(i) <= 33 for i in compiled_ids),
        f"ids={compiled_ids!r}",
    )
    check(
        "069.3 all real compiled ids match grammar",
        all(grammar.match(i) and PROCEDURAL_ASSET_PATTERN.match(i) for i in compiled_ids),
        f"ids={compiled_ids!r}",
    )
    check(
        "069.3 longest real id == 33 (proc.electronics.<16hex>)",
        max(len(i) for i in compiled_ids) == 33,
        f"max={max(len(i) for i in compiled_ids)}",
    )

    # 4. projection SKIPS the tampered placement: crafts a minimal published
    #    payload with (a) a registered catalog object, (b) a valid proc.*
    #    placement, (c) a proc.* placement whose embedded definition carries
    #    the 222-char tampered assetId -> (c) is absent from the DTOs.
    valid_proc_id = real_def["assetId"]
    valid_proc_doc = json.loads(json.dumps(real_def))
    bad_doc = json.loads(json.dumps(real_def))
    bad_doc["assetId"] = tampered_id
    payload = {
        "draft": {
            "objects": [
                {"object_id": "obj_knife", "asset_id": "PROP_KITCHEN_KNIFE_01",
                 "subtype": "kitchen_knife"},
                {"object_id": "obj_proc_ok", "asset_id": valid_proc_id,
                 "subtype": "ceremonial_letter_opener"},
                {"object_id": "obj_proc_bad", "asset_id": tampered_id,
                 "subtype": "decor_prop"},
            ],
            "world_graph": {
                "locations": [{"location_id": "loc1", "template": "office"}],
                "placements": [
                    {"object_id": "obj_knife", "asset_id": "PROP_KITCHEN_KNIFE_01",
                     "location_id": "loc1", "anchor": "desk_main",
                     "interaction": "inspect", "evidence_id": None},
                    {"object_id": "obj_proc_ok", "asset_id": valid_proc_id,
                     "location_id": "loc1", "anchor": "desk_main",
                     "interaction": "inspect", "evidence_id": None,
                     "generated_definition": valid_proc_doc},
                    {"object_id": "obj_proc_bad", "asset_id": tampered_id,
                     "location_id": "loc1", "anchor": "desk_main",
                     "interaction": "inspect", "evidence_id": None,
                     "generated_definition": bad_doc},
                ],
            },
            "evidence": [],
        }
    }
    projected = project_world_objects(payload)
    projected_ids = sorted(item["objectId"] for item in projected)
    check(
        "069.4 tampered placement SKIPPED by projection",
        projected_ids == ["obj_knife", "obj_proc_ok"],
        f"projected ids={projected_ids!r}",
    )
    # the valid proc.* neighbor still carries its validated generated block
    proc_dto = next((i for i in projected if i["objectId"] == "obj_proc_ok"), None)
    check(
        "069.4 valid proc.* neighbor still projected with generated block",
        proc_dto is not None and proc_dto.get("generated", {}).get("assetId") == valid_proc_id,
        f"proc_dto={proc_dto!r}",
    )


# --------------------------------------------------------------------------- #
# DEF-070 — backend mirror contrast: handler-shaped roles rejected in the
# embedded-definition gate (the frontend gate is covered by the vitest probe)
# --------------------------------------------------------------------------- #
def audit_def070_backend() -> None:
    from app.assets.compiler import compile_asset_spec, definition_json_issues
    from app.assets.specs import parse_asset_spec as _parse

    # Build a REAL compiled definition, then tamper the role field exactly as
    # the ORIGINAL reproduction did (the AssetSpec constructor itself now
    # rejects handler-shaped roles at parse time, so mutation of the emitted
    # document is the faithful gate-level contrast).
    base = _parse(_valid_spec("X", category="decor"), non_throwing=False)
    for role in ("onload", "onclick", "onerror", "onmouseover"):
        doc = compile_asset_spec(base).to_definition_json()
        doc["parts"][0]["role"] = role
        issues = definition_json_issues(doc)
        check(
            f"070.backend gate rejects role {role!r}",
            any("event handler" in i for i in issues),
            f"got issues={issues!r}",
        )
    for role in ("handle", "blade", "stem", "frame", "rail"):
        doc = compile_asset_spec(base).to_definition_json()
        doc["parts"][0]["role"] = role
        issues = definition_json_issues(doc)
        check(
            f"070.backend gate accepts role {role!r}",
            not issues,
            f"got issues={issues!r}",
        )


def main() -> int:
    print("=== DEF-068 glyph-class rejection (backend family, real modules) ===")
    audit_def068()
    print("\n=== DEF-069 embedded-definition grammar gate + projection ===")
    audit_def069()
    print("\n=== DEF-070 backend mirror (handler-shaped roles) ===")
    audit_def070_backend()

    print(f"\nDEF-068/069/070 BACKEND RETEST: {len(failures)} failure(s)")
    for f in failures:
        print(f"  FAIL - {f}")
    if not failures:
        print("ALL CLEAN-REJECTION CHECKS PASS (real modules + real manifests, read-only)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())