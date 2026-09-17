"""QA-owned Phase 13 SECURITY AUDIT (independent).

Covers the Phase 13 gate task 4 battery:

  1. hostile AssetSpec battery through parse_asset_spec / validate_asset_spec:
     code strings, JS URLs, huge arrays, huge numbers, negative scale,
     material injection, duplicate ids — every hostile document is REJECTED
     with deterministic issues (never coerced, never crashes).
     |  recursion / nesting bombs -> see the separate recursion probe
     |  (e2e/artifacts/qa_recursion_probe.py): an UNCAUGHT RecursionError
     |  escapes parse_asset_spec / validate_asset_spec / oracle entry points
     |  at ~2998-deep nesting inside the 262144-char cap -> DEF-067.
  2. hash-collision sanity: 128 deterministically DIFFERENT specs produce 128
     DISTINCT content-addressed assetIds.
  3. cache bound + version guard: LRU eviction at max_entries; CacheBoundError
     on an oversized normalized-spec; stale-version entries are never reused
     (get -> miss) and never cached (put raises).
  4. deep scan of the proc.* payloads (embedded definitions + bootstrap
     `generated` blocks) for URL / path / traversal / executable tokens: ZERO
     forbidden tokens anywhere in the generated-asset surface.
  5. truth-leak scan: every /api/v1 response the 2c audit session produces is
     scanned for the forbidden key paths -> ZERO matched paths.

Run:  python e2e/qa-phase13-security-audit.py
Output JSON: argv[1] or e2e/artifacts/qa-phase13-security-audit.json
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tests"))

FORBIDDEN_URL_TOKENS = ("http:", "https:", "data:", "file:", "javascript:")
FORBIDDEN_EXEC_TOKENS = re.compile(r"\b(?:script|handler|shader|function|eval)\b", re.IGNORECASE)
FORBIDDEN_KEEP_PATHS = {
    "murdererId", "victimId", "weaponId", "motiveId", "truthfulness", "crimeTime",
    "canonical", "solutionProof", "acceptedScoring", "solverProof", "proof",
    "diagnostics", "prompt", "providerOutput", "verifier", "tokenVerifier",
    "token", "seed", "model", "locked", "report", "universes", "universe",
    "observedAt", "propositions", "sourceRef", "remainingCandidateIds",
    "remainingMotiveIds", "remainingWeaponIds", "truth",
}

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:420]}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _deep_scan(value, path: str, hits: list[str]) -> None:
    if isinstance(value, str):
        lowered = value.casefold()
        for scheme in FORBIDDEN_URL_TOKENS:
            if scheme in lowered:
                hits.append(f"{path}: url-scheme {scheme!r}")
                break
        for token_match in FORBIDDEN_EXEC_TOKENS.finditer(value):
            hits.append(f"{path}: exec-token {token_match.group(0).lower()!r}")
            break
        if any(ch in value for ch in ("/", "\\")):
            hits.append(f"{path}: path separator")
        if ".." in value:
            hits.append(f"{path}: traversal")
        if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
            hits.append(f"{path}: absolute path")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _deep_scan(child, f"{path}.{key}", hits)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _deep_scan(child, f"{path}[{index}]", hits)


def audit_parser_battery() -> None:
    from app.assets.specs import validate_asset_spec

    section("1 — hostile AssetSpec parser battery (reject, never coerce)")

    def _hostile(name: str, doc, needle: str | None = None) -> None:
        issues = validate_asset_spec(doc)
        ok = bool(issues) and (needle is None or any(needle in i for i in issues))
        record(name, ok, list(issues)[:4])

    # code strings
    _hostile("code string in canonicalName (JS snippet)",
             {"canonicalName": "return eval('alert(1)')", "category": "decor",
              "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
              "parts": [{"id": "part_00", "role": "base", "primitive": "box",
                         "transform": {"position": {"x": 0, "y": 0, "z": 0},
                                       "rotation": {"x": 0, "y": 0, "z": 0},
                                       "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},
                         "material": "plastic"}]},
             "forbidden token")
    _hostile("new Function string in subtype",
             {"canonicalName": "x", "category": "decor", "subtype": "new Function('alert()')()",
              "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
              "parts": [{"id": "part_00", "role": "base", "primitive": "box",
                         "transform": {"position": {"x": 0, "y": 0, "z": 0},
                                       "rotation": {"x": 0, "y": 0, "z": 0},
                                       "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},
                         "material": "plastic"}]},
             "forbidden token")
    _hostile("onload-in-material code token",
             {"canonicalName": "x", "category": "decor",
              "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
              "parts": [{"id": "part_00", "role": "base", "primitive": "box",
                         "transform": {"position": {"x": 0, "y": 0, "z": 0},
                                       "rotation": {"x": 0, "y": 0, "z": 0},
                                       "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},
                         "material": "plastic);alert(1)"}]},
             "not in MATERIAL_VOCABULARY")
    # JS URLs
    _hostile("javascript: URL in canonicalName",
             {"canonicalName": "javascript:document.location='evil'", "category": "decor",
              "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
              "parts": [{"id": "part_00", "role": "base", "primitive": "box",
                         "transform": {"position": {"x": 0, "y": 0, "z": 0},
                                       "rotation": {"x": 0, "y": 0, "z": 0},
                                       "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},
                         "material": "plastic"}]},
             "javascript:")
    _hostile("data: URL in role",
             {"canonicalName": "x", "category": "decor",
              "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
              "parts": [{"id": "part_00", "role": "data:text/html;base64", "primitive": "box",
                         "transform": {"position": {"x": 0, "y": 0, "z": 0},
                                       "rotation": {"x": 0, "y": 0, "z": 0},
                                       "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},
                         "material": "plastic"}]},
             "URL scheme")
    # huge arrays
    parts = [{"id": f"part_{i:02d}", "role": "base", "primitive": "box",
              "transform": {"position": {"x": 0, "y": 0, "z": 0},
                            "rotation": {"x": 0, "y": 0, "z": 0},
                            "scale": {"x": 0.05, "y": 0.05, "z": 0.05}},
              "material": "plastic"} for i in range(10_000)]
    _hostile("huge parts array (10000) rejected",
             {"canonicalName": "x", "category": "decor",
              "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3}, "parts": parts},
             "exceeds the maximum of 24")
    # huge numbers
    _hostile("huge number = 1e308 dimensions via JSON (finite, out of bounds)",
             '{"canonicalName":"x","category":"decor","dimensions":{"x":1e308,"y":0.3,"z":0.3},'
             '"parts":[{"id":"part_00","role":"base","primitive":"box",'
             '"transform":{"position":{"x":0,"y":0,"z":0},"rotation":{"x":0,"y":0,"z":0},'
             '"scale":{"x":0.2,"y":0.2,"z":0.2}},"material":"plastic"}]}',
             "within [0.05, 4]")
    _hostile("huge int 10**30 in position",
             {"canonicalName": "x", "category": "decor",
              "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
              "parts": [{"id": "part_00", "role": "base", "primitive": "box",
                         "transform": {"position": {"x": 10**30, "y": 0, "z": 0},
                                       "rotation": {"x": 0, "y": 0, "z": 0},
                                       "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},
                         "material": "plastic"}]},
             "within [-4, 4]")
    # negative scale
    _hostile("negative scale rejected",
             {"canonicalName": "x", "category": "decor",
              "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
              "parts": [{"id": "part_00", "role": "base", "primitive": "box",
                         "transform": {"position": {"x": 0, "y": 0, "z": 0},
                                       "rotation": {"x": 0, "y": 0, "z": 0},
                                       "scale": {"x": -1.0, "y": 0.2, "z": 0.2}},
                         "material": "plastic"}]},
             "within [0.05, 2]")
    # material injection / duplicate ids
    _hostile("material injection (unknown token) rejected",
             {"canonicalName": "x", "category": "decor",
              "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
              "parts": [{"id": "part_00", "role": "base", "primitive": "box",
                         "transform": {"position": {"x": 0, "y": 0, "z": 0},
                                       "rotation": {"x": 0, "y": 0, "z": 0},
                                       "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},
                         "material": "uranium.depleted"}]},
             "not in MATERIAL_VOCABULARY")
    dup = {"canonicalName": "x", "category": "decor",
           "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
           "parts": [
               {"id": "part_00", "role": "base", "primitive": "box",
                "transform": {"position": {"x": 0, "y": 0, "z": 0},
                              "rotation": {"x": 0, "y": 0, "z": 0},
                              "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},
                "material": "plastic"},
               {"id": "part_00", "role": "cap", "primitive": "sphere",
                "transform": {"position": {"x": 0, "y": 0.3, "z": 0},
                              "rotation": {"x": 0, "y": 0, "z": 0},
                              "scale": {"x": 0.1, "y": 0.1, "z": 0.1}},
                "material": "plastic"},
           ]}
    record("duplicate part ids rejected", bool(validate_asset_spec(dup)) and
           any("duplicate part id" in i for i in validate_asset_spec(dup)),
           list(validate_asset_spec(dup))[:2])


def audit_hash_collision() -> None:
    from app.assets.catalog import load_catalog_from_repo  # noqa: F401  (import guard)
    from app.assets.compiler import compile_asset_spec
    from app.assets.specs import CATEGORY_SPEC_ALLOWLIST, parse_asset_spec

    section("2 — hash-collision sanity (128 distinct specs -> distinct ids)")
    categories = list(CATEGORY_SPEC_ALLOWLIST)
    materials = ["plastic", "wood.dark", "metal.brass", "metal.steel",
                 "wood.light", "ceramic", "leather", "fabric"]
    ids = []
    for index in range(128):
        dim = round(0.06 + (index % 90) * 0.04, 4)
        zs = round(0.06 + (index % 47) * 0.04, 4)
        spec = {
            "canonicalName": f"Collision Probe {index:03d}",
            "category": categories[index % len(categories)],
            "subtype": None,
            "dimensions": {"x": dim, "y": 0.2, "z": 0.2},
            "parts": [{
                "id": "part_00", "role": "base", "primitive": "box",
                "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0},
                              "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                              "scale": {"x": 0.2, "y": 0.2, "z": zs}},
                "material": materials[index % len(materials)],
            }],
        }
        ids.append(compile_asset_spec(parse_asset_spec(spec, non_throwing=False)).asset_id)
    record("128 distinct specs -> 128 distinct content-addressed ids",
           len(ids) == 128 and len(set(ids)) == 128,
           {"count": len(ids), "unique": len(set(ids))})


def audit_cache_bounds() -> None:
    from app.assets.compiler import COMPILER_VERSION, SCHEMA_VERSION
    from app.assets.generated_cache import CacheBoundError, GeneratedAssetCache
    from app.assets.specs import normalize_spec, parse_asset_spec
    from app.assets.compiler import compile_asset_spec
    from app.assets.catalog import load_catalog_from_repo  # noqa: F401
    from fixtures.asset_specs import (
        ANTIQUE_LETTER_OPENER_SPEC,
        CUSTOM_TROPHY_SPEC,
        DESK_AWARD_SPEC,
        LAB_SAMPLE_RACK_SPEC,
    )

    section("3 — cache bounds + version guard")
    specs = {name: parse_asset_spec(raw, non_throwing=False)
             for name, raw in (
                 ("opener", ANTIQUE_LETTER_OPENER_SPEC), ("rack", LAB_SAMPLE_RACK_SPEC),
                 ("trophy", CUSTOM_TROPHY_SPEC), ("award", DESK_AWARD_SPEC))}

    # LRU eviction at max_entries = 2
    cache = GeneratedAssetCache(max_entries=2)
    for name, spec in specs.items():
        definition = compile_asset_spec(spec)
        cache.put(spec_hash := __import__("app.assets.compiler", fromlist=["spec_hash_for_cache"]).spec_hash_for_cache(spec),
                  definition, spec_bytes=normalize_spec(spec).encode("utf-8"))
    first_key = __import__("app.assets.compiler", fromlist=["spec_hash_for_cache"]).spec_hash_for_cache(specs["opener"])
    second_key = __import__("app.assets.compiler", fromlist=["spec_hash_for_cache"]).spec_hash_for_cache(specs["rack"])
    third_key = __import__("app.assets.compiler", fromlist=["spec_hash_for_cache"]).spec_hash_for_cache(specs["trophy"])
    fourth_key = __import__("app.assets.compiler", fromlist=["spec_hash_for_cache"]).spec_hash_for_cache(specs["award"])
    record("LRU cache bounded at max_entries",
           len(cache) <= 2 and cache.get(first_key) is None and cache.get(third_key) is not None,
           {"len": len(cache), "openerEvicted": cache.get(first_key) is None,
            "trophyPresent": cache.get(third_key) is not None})

    # CacheBoundError on oversized normalized spec
    big_spec = specs["trophy"]
    raises = False
    try:
        cache2 = GeneratedAssetCache(max_entry_bytes=64)
        cache2.put("k", compile_asset_spec(big_spec), spec_bytes=normalize_spec(big_spec).encode("utf-8"))
    except CacheBoundError:
        raises = True
    record("oversized normalized spec -> CacheBoundError (never cached)",
           raises and len(cache2) == 0,
           {"maxEntryBytes": 64, "specBytes": len(normalize_spec(big_spec).encode("utf-8"))})

    # version guard: a stale-version entry is never returned and never cached
    from app.assets.compiler import GeneratedAssetDefinition, GeneratedHitbox, GeneratedPart, GeneratedTransform, GeneratedVec3

    stale = GeneratedAssetDefinition(
        compiler_version=COMPILER_VERSION - 1,
        schema_version=SCHEMA_VERSION,
        asset_id="proc.decor.deadbeef01234567",
        canonical_name="Old",
        category="decor",
        dimensions=GeneratedVec3(x=0.3, y=0.3, z=0.3),
        parts=(),
        hitbox=GeneratedHitbox(scale=GeneratedVec3(x=0.5, y=0.5, z=0.5)),
    )
    cache3 = GeneratedAssetCache()
    put_refused = False
    try:
        cache3.put(third_key, stale)
    except CacheBoundError:
        put_refused = True
    len_after_refused_put = len(cache3)
    # Force a stale entry in anyway (bypass put) to check get's version guard.
    cache3._entries[third_key] = __import__(
        "app.assets.generated_cache", fromlist=["CacheEntry"]
    ).CacheEntry(
        definition=stale,
        metadata=__import__(
            "app.assets.generated_cache", fromlist=["CacheEntryMetadata"]
        ).CacheEntryMetadata(compiler_version=COMPILER_VERSION - 1, schema_version=SCHEMA_VERSION),
    )
    record("stale-version definition refused by put (CacheBoundError)",
           put_refused and len_after_refused_put == 0,
           {"putRefused": put_refused, "lenAfterRefusedPut": len_after_refused_put})
    record("stale-version entry treated as a MISS by get (version guard)",
           cache3.get(third_key) is None and cache3.contains(third_key) is False,
           {"get": cache3.get(third_key), "contains": cache3.contains(third_key)})


def audit_payload_deep_scan_and_leaks() -> None:
    from fastapi.testclient import TestClient
    from fixtures.asset_specs import GOLDEN_SPEC_CONTENT, GOLDEN_UNKNOWN_REQUESTS
    from app.assets.spec_provider import FakeAssetSpecProvider
    from app.assets.generated_cache import GeneratedAssetCache
    from app.core.config import Settings
    from app.persistence.store import Store
    from app.services.generation import GenerationService
    from app.main import create_app

    section("4 — deep scan of proc.* payload surface + truth-leak scan")
    db = Path(tempfile.mkdtemp(prefix="qa_p13_sec_")) / "audit.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    env.pop("ENV_FILE", None)
    env.pop("STATIC_DIR", None)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    settings = Settings(database_url=f"sqlite:///{db.as_posix()}")
    store = Store(settings.database_url)
    service = GenerationService(
        settings=settings, store=store,
        spec_provider=FakeAssetSpecProvider(GOLDEN_SPEC_CONTENT),
        generate_unknown_assets=True, generated_cache=GeneratedAssetCache(),
    )
    session = service.create_anonymous_quota_session()
    started = service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
        difficulty="medium", environment="office",
        unknown_asset_requests=list(GOLDEN_UNKNOWN_REQUESTS),
    )
    payload = json.loads(store.get_published(started.case_id, 1).payload_json)

    # deep scan of the embedded generated definitions in the published payload
    hits: list[str] = []
    for placement in payload["draft"]["world_graph"]["placements"]:
        definition = placement.get("generated_definition")
        if definition:
            _deep_scan(definition, f"payload.placements[{placement['object_id']}].generated_definition", hits)
    record("deep scan: embedded generated definitions carry NO url/path/exec tokens",
           not hits, {"hits": hits[:5], "scanned": sum(
               1 for p in payload["draft"]["world_graph"]["placements"] if p.get("generated_definition"))})

    # deep scan of the live HTTP bootstrap `generated` blocks + truth-leak scan
    app = create_app(settings=settings)
    forbidden_found: list[str] = []
    session_hits: list[str] = []

    def _scan_keys(node, path):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in FORBIDDEN_KEEP_PATHS:
                    session_hits.append(f"{path}.{key}")
                _scan_keys(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                _scan_keys(child, f"{path}[{index}]")

    bootstrap_hits: list[str] = []
    with TestClient(app) as client:
        pt = client.post(
            f"/api/v1/cases/{started.case_id}/versions/1/playthroughs",
            headers={"Authorization": f"Bearer {started.creator_access_token}"},
        ).json()
        boot = client.get(
            f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
            headers={"Authorization": f"Bearer {pt['playthroughAccessToken']}"},
        ).json()
        for obj in boot["scene"]["worldObjects"]:
            if obj["assetId"].startswith("proc."):
                _deep_scan(obj["generated"], f"bootstrap.worldObjects[{obj['objectId']}].generated", bootstrap_hits)
            _scan_keys(obj, "worldObject")
        _scan_keys(boot, "bootstrap")
    record("deep scan: bootstrap `generated` blocks carry NO url/path/exec tokens",
           not bootstrap_hits, {"hits": bootstrap_hits[:5]})
    record(
        "truth-leak scan: ZERO forbidden keys in the live session API surface",
        not session_hits,
        {"matched": session_hits[:5], "worldObjects": len(boot["scene"]["worldObjects"])},
    )


def main() -> int:
    audit_parser_battery()
    audit_hash_collision()
    audit_cache_bounds()
    audit_payload_deep_scan_and_leaks()
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "e2e" / "artifacts" / "qa-phase13-security-audit.json"
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