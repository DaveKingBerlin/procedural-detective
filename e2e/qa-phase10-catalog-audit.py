"""QA-owned, independent Phase 10 CATALOG/RESOLVER CONTRACT AUDIT
(QA role, .rad/roles/qa.md; .rad/policies/evidence.md; .rad/policies/deterministic-testing.md).

Independent verification of the Asset Oracle (Phase10.md):

 2a  load the REAL catalog (assets/catalog/catalog.json): 9 golden assetIds +
     fallback present, catalogVersion == 1, no duplicate assetIds / no
     conflicting normalized aliases, every dev_mode_case placement assetId
     resolves CATALOG_EXACT, `resolve_placements_provenance` for the golden is
     all CATALOG_EXACT.
 2b  resolver behavior verified INDEPENDENTLY:
       - exact logical id  -> CATALOG_EXACT (resolved, not ambiguous)
       - canonical names ("kitchen knife", "KITCHEN KNIFE", "K\\u00eftchen knife")
         -> knife (CATALOG_EXACT, NFKD+casefold normalization)
       - aliases ("chef knife", legacy "apartment.laptop.basic")
         -> CATALOG_ALIAS with the verbatim matchedAlias
       - semantic request "weapon sharp blade" (categoryHint evidence,
         subtypeHint sharp, tags weapon/sharp/blade) -> knife SEMANTIC_MATCH,
         UNIQUE top score (confidence 9.0 > the 6.0 runner-ups)
       - a CRAFTED TIE (two assets with equal top scores) -> ambiguous=True,
         resolved=False, candidates listed deterministically, NO winner
       - a 3-asset tie in the real catalog -> ambiguous, 3 candidates, no winner
       - unknown name -> explicit FALLBACK (fallbackAsset, provenance FALLBACK)
       - a below-threshold semantic -> FALLBACK (never silent nonsense)
 2b-security  hostile raw requests (14+): URL schemes (http/https/data/file/
     javascript), path traversal, absolute paths (POSIX + Windows drive),
     control characters (incl. NUL), oversized strings, oversized arrays
     (tags/capabilities), forbidden executable tokens, non-string types,
     missing requestedName, URL smuggled into hints/tags. Each must be rejected
     at the VALIDATION layer (non-empty sorted issue tuple); the resolve()
     gateway must raise AssetRequestValidationError (clean, before any
     resolution).
 2c  published-case stability: run the dev-provider publish path through the
     PUBLIC API on a REAL temporary migrated (alembic head 0004) SQLite file;
     capture the stored published payload bytes, the public case DTO and the
     investigation bootstrap. Mutate a catalog descriptor IN MEMORY (knife
     blade color -> #ff0000 + dimension change). Assert:
       - published payload bytes byte-identical,
       - public case DTO byte-identical,
       - investigation bootstrap unchanged (modulo the per-playthrough id),
       - provenance is NOT present in any public DTO (deep recursive key scan
         + raw byte scan over bootstrap + public case for every
         provenance/enum/diagnostic key).

Evidence: printed transcript + e2e/artifacts/qa-phase10-catalog-audit.json
(transient output; evidence policy). This script NEVER modifies product source.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(REPO))

from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.assets import (  # noqa: E402
    AssetRequest,
    AssetRequestValidationError,
    Catalog,
    load_catalog,
    load_catalog_from_repo,
    normalize,
    resolve,
    resolve_placements_provenance,
    validate_asset_request,
)
from app.assets.resolver import AssetResolver, Provenance  # noqa: E402

MANIFEST_PATH = REPO / "assets" / "catalog" / "catalog.json"
DEV_MODE_CASE = BACKEND / "app" / "services" / "dev_mode_case.json"

GOLDEN_ASSET_IDS = (
    "PROP_KITCHEN_KNIFE_01",
    "PROP_LETTER_OPENER_01",
    "PROP_SCISSORS_01",
    "PROP_VASE_01",
    "PROP_LAPTOP_01",
    "PROP_TABLE_01",
    "DOOR_APARTMENT_01",
    "PROP_LAMP_01",
    "PROP_BODY_PLACEHOLDER_01",
)

# Every provenance/resolver-related key that must NEVER appear in a public DTO.
# NOTE: the plain key "candidates" is the DOCUMENTED public Phase 7
# AccusationCandidatesDTO (suspects/motives/weapons) — a public contract field
# that predates the resolver. It is checked SEPARATELY below (its value must be
# the public candidate-universe shape, never a resolver ambiguity tuple).
PROVENANCE_FORBIDDEN_KEYS = {
    "provenance", "matchedAlias", "catalogVersion", "confidence", "ambiguous",
    "resolved", "CATALOG_EXACT", "CATALOG_ALIAS", "SEMANTIC_MATCH",
    "PARAMETRIC_VARIANT", "PROCEDURAL_GENERATED", "STATIC_GENERATED", "FALLBACK",
    "_last_publish_provenance", "provenances", "resolutionDiagnostics",
    "assetResolution", "resolution", "resolver",
}

# The resolver ambiguity `candidates` values observed in artifact (assetId
# tuples) must never appear as a top-level tuple; "candidates" occurrences
# must be the public accusation-universe dict with EXACTLY these keys.
PUBLIC_CANDIDATES_KEYS = {"suspects", "motives", "weapons"}

REPORT: dict = {"sections": {}}
ARTIFACT_DIR = REPO / "e2e" / "artifacts"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_PATH = ARTIFACT_DIR / "qa-phase10-catalog-audit.json"

FAILED: list[str] = []


def section(name: str) -> None:
    print(f"\n=== {name} ===")
    REPORT["sections"][name] = {"status": "RUNNING"}


def ok(name: str, detail: str = "") -> None:
    key = next((k for k in REPORT["sections"] if k.startswith(name)), name)
    print(f"  PASS  {key} {detail}")
    REPORT["sections"].setdefault(key, {})["status"] = "PASS"
    if detail:
        REPORT["sections"][key]["detail"] = detail


def fail(name: str, detail: str) -> None:
    key = next((k for k in REPORT["sections"] if k.startswith(name)), name)
    print(f"  FAIL  {key}: {detail}")
    REPORT["sections"].setdefault(key, {})["status"] = "FAIL"
    REPORT["sections"][key]["detail"] = detail
    FAILED.append(detail)


def check(cond: bool, name: str, detail: str) -> None:
    if cond:
        ok(name, detail)
    else:
        fail(name, detail)


# --------------------------------------------------------------------------- #
# deep key-path scan (provenance leak over public DTOs)
# --------------------------------------------------------------------------- #


def deep_paths(node, prefix: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield (path, value)
            yield from deep_paths(value, path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            path = f"{prefix}[{index}]"
            yield (path, value)
            yield from deep_paths(value, path)


def scan_provenance_keys(*payloads: object) -> list[str]:
    hits: list[str] = []
    for payload in payloads:
        for path, _value in deep_paths(payload):
            leaf = path.rsplit(".", 1)[-1]
            if leaf in PROVENANCE_FORBIDDEN_KEYS:
                hits.append(path)
    return hits


def scan_candidates_shape(*payloads: object) -> list[str]:
    """Every 'candidates' key must be the public accusation-universe dict."""
    bad: list[str] = []
    for payload in payloads:
        for path, value in deep_paths(payload):
            leaf = path.rsplit(".", 1)[-1]
            if leaf != "candidates":
                continue
            if not isinstance(value, dict) or set(value.keys()) != PUBLIC_CANDIDATES_KEYS:
                bad.append(f"{path} -> {json.dumps(value)[:120]!r} is not the public AccusationCandidatesDTO")
    return bad


# --------------------------------------------------------------------------- #
# golden placements
# --------------------------------------------------------------------------- #


def golden_placements() -> list[dict]:
    raw = json.loads(DEV_MODE_CASE.read_text(encoding="utf-8"))
    return json.loads(raw["world_graph"][0])["worldGraph"]["placements"]


# --------------------------------------------------------------------------- #
# 2a — real catalog load contract
# --------------------------------------------------------------------------- #

def audit_2a(catalog: Catalog) -> None:
    section("2a real catalog load contract")

    ids = {a.asset_id for a in catalog.assets}
    expected = set(GOLDEN_ASSET_IDS) | {"PROP_FALLBACK_01"}
    # Phase 12 amendment: the catalog grows to ~101 entries. The Phase 10/11
    # contract that must never weaken is that the golden ids + fallback remain
    # present (subset) and the catalog stays duplicate-free; an EXACT-set match
    # is no longer required (Phase 12 / REQUIREMENTS roadmap deliberately adds
    # the showcase library). The self-resolution invariants are re-checked
    # below and in the resolver suite.
    missing = expected - ids
    check(
        not missing,
        "2a",
        f"golden {len(GOLDEN_ASSET_IDS)} ids + fallback still present "
        f"({len(ids)} assets; missing={sorted(missing)})",
    )
    check(catalog.catalog_version == 1, "2a", "catalogVersion == 1")
    check(catalog.fallback_asset == "PROP_FALLBACK_01", "2a",
          "fallbackAsset == PROP_FALLBACK_01")
    check(catalog.by_id["PROP_FALLBACK_01"] is not None, "2a",
          "fallback asset present in by_id index")

    check(len(ids) == len(catalog.assets), "2a", "no duplicate assetIds")

    # no duplicate/conflicting normalized aliases across assets
    alias_claims: dict[str, list[tuple[str, str]]] = {}
    for asset in catalog.assets:
        for raw_alias in asset.aliases:
            alias_claims.setdefault(normalize(raw_alias), []).append(
                (asset.asset_id, raw_alias)
            )
    collisions = {
        norm: claims for norm, claims in alias_claims.items()
        if len({asset_id for asset_id, _raw in claims}) > 1
    }
    check(not collisions, "2a",
          f"no cross-asset normalized alias collisions ({len(alias_claims)} distinct normalized aliases)")

    canon_norms = {normalize(a.canonical_name): a.asset_id for a in catalog.assets}
    check(len(canon_norms) == len(catalog.assets), "2a",
          "no normalized canonical-name collisions")

    placements = golden_placements()
    check(len(placements) == 9, "2a", "dev case has 9 placements")
    exact_resolutions = []
    for placement in placements:
        aid = placement["assetId"]
        r = resolve({"requestedName": aid}, catalog=catalog)
        exact_resolutions.append((placement["objectId"], aid, r.provenance.value))
    check(
        all(prov == "CATALOG_EXACT" for _, _, prov in exact_resolutions),
        "2a",
        f"every dev placement assetId resolves CATALOG_EXACT: {exact_resolutions}",
    )

    prov_map = resolve_placements_provenance(placements, catalog=catalog)
    check(
        set(prov_map.values()) == {"CATALOG_EXACT"} and len(prov_map) == 9,
        "2a",
        f"resolve_placements_provenance(golden) all CATALOG_EXACT: {prov_map}",
    )


# --------------------------------------------------------------------------- #
# 2b — resolver behavior matrix
# --------------------------------------------------------------------------- #

def audit_2b(catalog: Catalog) -> None:
    section("2b resolver behavior matrix")

    r = resolve({"requestedName": "PROP_KITCHEN_KNIFE_01"}, catalog=catalog)
    check(
        r.asset_id == "PROP_KITCHEN_KNIFE_01" and r.provenance is Provenance.CATALOG_EXACT
        and r.resolved and not r.ambiguous,
        "2b",
        "exact id PROP_KITCHEN_KNIFE_01 -> CATALOG_EXACT",
    )

    for name in ("kitchen knife", "KITCHEN KNIFE", "K\u00eftchen knife"):
        r = resolve({"requestedName": name}, catalog=catalog)
        check(
            r.asset_id == "PROP_KITCHEN_KNIFE_01" and r.provenance is Provenance.CATALOG_EXACT,
            "2b",
            f"canonical name {name!r} -> knife CATALOG_EXACT",
        )

    r = resolve({"requestedName": "chef knife"}, catalog=catalog)
    check(
        r.asset_id == "PROP_KITCHEN_KNIFE_01" and r.provenance is Provenance.CATALOG_ALIAS
        and r.matched_alias == "chef knife",
        "2b",
        "alias 'chef knife' -> CATALOG_ALIAS matchedAlias='chef knife'",
    )

    r = resolve({"requestedName": "apartment.laptop.basic"}, catalog=catalog)
    check(
        r.asset_id == "PROP_LAPTOP_01" and r.provenance is Provenance.CATALOG_ALIAS
        and r.matched_alias == "apartment.laptop.basic",
        "2b",
        "legacy dot alias 'apartment.laptop.basic' -> LAPTOP CATALOG_ALIAS verbatim matchedAlias",
    )

    r = resolve(
        {
            "requestedName": "weapon sharp blade",
            "categoryHint": "evidence",
            "subtypeHint": "sharp",
            "tags": ["weapon", "sharp", "blade"],
        },
        catalog=catalog,
    )
    check(
        r.asset_id == "PROP_KITCHEN_KNIFE_01" and r.provenance is Provenance.SEMANTIC_MATCH
        and r.resolved and not r.ambiguous and r.confidence == 9.0,
        "2b",
        f"semantic 'weapon sharp blade' -> knife SEMANTIC_MATCH confidence={r.confidence}",
    )
    scorer = AssetResolver(catalog)._semantic_candidates(
        AssetRequest(
            requested_name="weapon sharp blade",
            category_hint="evidence",
            subtype_hint="sharp",
            tags=("weapon", "sharp", "blade"),
        )
    )
    top_score = scorer[0][1]
    unique = [d for d, _s in scorer if _s == top_score]
    runners = ", ".join(f"{d.asset_id}={s:g}" for d, s in scorer if s < top_score)
    check(
        len(unique) == 1 and unique[0].asset_id == "PROP_KITCHEN_KNIFE_01",
        "2b",
        f"unique top score set = {{knife}} ({top_score:g}); runner-ups: {runners}",
    )

    # CRAFTED TWO-ASSET TIE (equal top scores) -> ambiguous, no winner.
    tie_catalog = Catalog(
        catalog_version=catalog.catalog_version,
        fallback_asset=catalog.fallback_asset,
        assets=tuple(
            a for a in catalog.assets
            if a.asset_id in ("PROP_KITCHEN_KNIFE_01", "PROP_LETTER_OPENER_01",
                              "PROP_FALLBACK_01")
        ),
    )
    r_tie = AssetResolver(tie_catalog).resolve_request(
        AssetRequest(
            requested_name="sharp thing",
            category_hint="evidence",
            subtype_hint="sharp",
            tags=("blade",),
        )
    )
    check(
        r_tie.ambiguous is True and r_tie.resolved is False and r_tie.asset_id == ""
        and r_tie.candidates == ("PROP_KITCHEN_KNIFE_01", "PROP_LETTER_OPENER_01")
        and r_tie.confidence == 6.0,
        "2b",
        f"crafted 2-asset tie -> ambiguous=True resolved=False NO winner candidates={r_tie.candidates} confidence={r_tie.confidence}",
    )

    r3 = resolve(
        {
            "requestedName": "sharp thing",
            "categoryHint": "evidence",
            "subtypeHint": "sharp",
            "tags": ["blade"],
        },
        catalog=catalog,
    )
    check(
        r3.ambiguous and not r3.resolved and r3.asset_id == ""
        and r3.candidates == ("PROP_KITCHEN_KNIFE_01", "PROP_LETTER_OPENER_01",
                              "PROP_SCISSORS_01")
        and r3.confidence == 6.0,
        "2b",
        f"3-asset tie in full catalog -> ambiguous, candidates={r3.candidates}, NO winner",
    )

    rb = resolve({"requestedName": "a magical crystal dragon orb"}, catalog=catalog)
    check(
        rb.asset_id == "PROP_FALLBACK_01" and rb.provenance is Provenance.FALLBACK
        and rb.resolved and not rb.ambiguous,
        "2b",
        "unknown name -> FALLBACK (fallbackAsset PROP_FALLBACK_01, explicit)",
    )

    rlow = resolve({"requestedName": "some decor", "categoryHint": "decor"}, catalog=catalog)
    check(
        rlow.asset_id == "PROP_FALLBACK_01" and rlow.provenance is Provenance.FALLBACK,
        "2b",
        "below-threshold semantic (categoryHint 'decor') -> explicit FALLBACK",
    )

    REPORT["sections"]["2b resolver behavior matrix"]["matrix"] = {
        "PROP_KITCHEN_KNIFE_01": "CATALOG_EXACT",
        "kitchen knife": "CATALOG_EXACT",
        "KITCHEN KNIFE": "CATALOG_EXACT",
        "K\\u00eftchen knife": "CATALOG_EXACT",
        "chef knife": "CATALOG_ALIAS (matchedAlias='chef knife')",
        "apartment.laptop.basic": "CATALOG_ALIAS (matchedAlias='apartment.laptop.basic')",
        "weapon sharp blade": "SEMANTIC_MATCH (knife, confidence=9.0, unique top)",
        "crafted 2-asset tie": "AMBIGUOUS (no winner, 2 candidates)",
        "full-catalog 3-asset tie": "AMBIGUOUS (no winner, 3 candidates)",
        "unknown name": "FALLBACK (PROP_FALLBACK_01)",
        "below-threshold": "FALLBACK (PROP_FALLBACK_01)",
    }


# --------------------------------------------------------------------------- #
# 2b-security — hostile raw requests at the validation layer
# --------------------------------------------------------------------------- #

def audit_security() -> None:
    section("2b-security hostile raw request validation")

    hostile: list[tuple[str, dict, str]] = [
        ("http url", {"requestedName": "http://evil.example/asset.glb"}, "URL scheme 'http:'"),
        ("https url", {"requestedName": "https://evil.example/x"}, "URL scheme 'https:'"),
        ("data url", {"requestedName": "data:text/html;base64,PHN0eWxl"}, "URL scheme 'data:'"),
        ("javascript url", {"requestedName": "javascript:alert(1)"}, "URL scheme 'javascript:'"),
        ("file url", {"requestedName": "file:///etc/passwd"}, "URL scheme 'file:'"),
        ("path traversal posix", {"requestedName": "../../etc/passwd"}, "path traversal '..'"),
        ("path traversal backslash", {"requestedName": "..\\..\\x"}, "path traversal '..'"),
        ("absolute posix", {"requestedName": "/usr/bin/sh"}, "absolute path"),
        ("absolute windows", {"requestedName": "C:\\evil\\x"}, "absolute path"),
        ("control chars (NUL)", {"requestedName": "bad\x00name"}, "control character"),
        ("forbidden token eval", {"requestedName": "my eval thing"}, "forbidden token 'eval'"),
        ("forbidden token script", {"requestedName": "smart script thing"}, "forbidden token 'script'"),
        ("oversized string", {"requestedName": "x" * 121}, "exceeds 120 characters"),
        ("oversized tags array", {"requestedName": "knife", "tags": [f"t{i}" for i in range(17)]},
         "exceeds the maximum of 16 entries"),
        ("oversized capabilities", {"requestedName": "knife", "requiredEvidenceCapabilities": [f"c{i}" for i in range(9)]},
         "exceeds the maximum of 8 entries"),
        ("non-string name", {"requestedName": 12345}, "non-empty string"),
        ("non-string tag entry", {"requestedName": "knife", "tags": ["ok", 7]}, "non-empty string"),
        ("url in tag", {"requestedName": "knife", "tags": ["ok", "data:text/html,oops"]}, "URL scheme 'data:'"),
        ("url in subtype hint", {"requestedName": "knife", "subtypeHint": "javascript:void(0)"}, "URL scheme 'javascript:'"),
        ("missing requestedName", {"categoryHint": "evidence"}, "required field is missing"),
        ("empty requestedName", {"requestedName": "   "}, "non-empty string"),
    ]

    rejected = 0
    for label, payload, expected_fragment in hostile:
        issues = validate_asset_request(payload)
        if issues:
            joined = "\n".join(issues)
            matched = expected_fragment in joined
            if matched:
                rejected += 1
                ok("2b-security", f"{label}: rejected, issue {expected_fragment!r} present ({len(issues)} issues)")
            else:
                fail("2b-security", f"{label}: rejected but {expected_fragment!r} NOT among issues: {issues}")
        else:
            fail("2b-security", f"{label}: payload NOT rejected (no issues)")

    check(
        rejected == len(hostile),
        "2b-security",
        f"{rejected}/{len(hostile)} hostile requests rejected at the validation layer with the expected issue",
    )

    gate_failures = 0
    for label, payload, _frag in hostile:
        try:
            resolve(payload)
            gate_failures += 1
            fail("2b-security", f"{label}: resolve() ACCEPTED the hostile payload")
        except AssetRequestValidationError:
            pass
        except Exception as exc:  # noqa: BLE001
            gate_failures += 1
            fail("2b-security", f"{label}: resolve() raised non-typed {type(exc).__name__}: {exc}")
    check(gate_failures == 0, "2b-security",
          "resolve() raised AssetRequestValidationError (typed, clean) for every hostile payload")

    try:
        resolve({"requestedName": "http://evil.example/x"})
        fail("2b-security", "resolve accepted a URL request (control)")
    except AssetRequestValidationError as exc:
        ok("2b-security", f"control: resolve() URL payload -> AssetRequestValidationError issues={list(exc.issues)[:2]}")

    typed = resolve(AssetRequest(requested_name="chef knife"))
    ok("2b-security",
       f"typed AssetRequest path stays trusted (internal pipeline): {typed.provenance.value}")


# --------------------------------------------------------------------------- #
# 2c — published-case stability under in-memory catalog mutation
# --------------------------------------------------------------------------- #

def _upgrade(db_url: str) -> None:
    cfg = AlembicConfig(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


def audit_2c() -> None:
    from app.core.config import Settings
    from app.main import create_app

    section("2c published-case stability (fresh migrated tmp DB, head 0004)")
    scratch = Path(tempfile.mkdtemp(prefix="qa-p10-", dir=Path(os.environ["TEMP"])))
    db_path = scratch / "audit.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    _upgrade(db_url)

    app = create_app(
        Settings(
            database_url=db_url,
            cors_allowed_origins=["http://localhost:5173", "http://localhost:4173"],
            max_concurrent_generations=4,
            max_generations_per_session_per_window=8,
        )
    )
    REPORT["2c"] = {"db": str(db_path)}
    try:
        c = TestClient(app)
        res = c.post("/api/v1/sessions/anonymous")
        assert res.status_code == 201, res.text
        session_token = res.json()["anonymousSessionToken"]
        res = c.post("/api/v1/cases",
                     headers={"Authorization": f"Bearer {session_token}"},
                     json={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n",
                           "difficulty": "medium"})
        assert res.status_code == 201, res.text
        case = res.json()
        case_id, creator = case["caseId"], case["creatorAccessToken"]
        assert case["status"] == "PUBLISHED", case
        ok("2c", f"dev-provider publish through the public API: case {case_id} PUBLISHED")

        public0 = c.get(f"/api/v1/cases/{case_id}?version=1",
                        headers={"Authorization": f"Bearer {creator}"})
        assert public0.status_code == 200, public0.text
        public_dto = public0.json()

        res = c.post(f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                     headers={"Authorization": f"Bearer {creator}"})
        assert res.status_code == 201, res.text
        pt = res.json()
        pt_id, pt_token = pt["playthroughId"], pt["playthroughAccessToken"]

        b0 = c.get(f"/api/v1/playthroughs/{pt_id}/investigation",
                   headers={"Authorization": f"Bearer {pt_token}"})
        assert b0.status_code == 200, b0.text
        bootstrap0 = b0.json()

        payload_bytes = app.state.store.get_published(case_id, 1).payload_json
        ok("2c", "captured stored payload bytes, public case DTO and bootstrap (pre-mutation)")

        # ---- mutate a catalog descriptor IN MEMORY (color + dimension) ------
        base = load_catalog(MANIFEST_PATH)
        knife = base.by_id["PROP_KITCHEN_KNIFE_01"]
        mutated_colors = dict(knife.colors)
        mutated_colors["blade"] = "#ff0000"
        assert knife.colors["blade"] == "#c8ccd4", "original must stay untouched"
        mutated = dataclasses.replace(
            knife,
            colors=mutated_colors,
            dimensions=dataclasses.replace(knife.dimensions, x=99.0),
        )
        ok("2c", "in-memory mutated knife: blade #c8ccd4 -> #ff0000, dim x 0.24 -> 99.0; original untouched")

        mutated_catalog = dataclasses.replace(
            base,
            assets=tuple(
                a if a.asset_id != mutated.asset_id else mutated for a in base.assets
            ),
        )
        probe = AssetResolver(mutated_catalog).resolve_request(
            AssetRequest(requested_name="PROP_KITCHEN_KNIFE_01")
        )
        check(
            probe.asset_id == "PROP_KITCHEN_KNIFE_01" and probe.provenance.value == "CATALOG_EXACT",
            "2c",
            "mutated-catalog resolution of the published id stays CATALOG_EXACT (identity = logical id, never render metadata)",
        )

        # ---- re-read the SAME published data after the mutation -------------
        payload_bytes_after = app.state.store.get_published(case_id, 1).payload_json
        check(payload_bytes_after == payload_bytes, "2c",
              "stored published payload bytes byte-identical after in-memory catalog mutation")

        public_after = c.get(f"/api/v1/cases/{case_id}?version=1",
                             headers={"Authorization": f"Bearer {creator}"}).json()
        check(public_after == public_dto, "2c",
              "public case DTO byte-identical after catalog mutation")

        res = c.post(f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                     headers={"Authorization": f"Bearer {creator}"})
        pt2 = res.json()
        b_after = c.get(f"/api/v1/playthroughs/{pt2['playthroughId']}/investigation",
                        headers={"Authorization": f"Bearer {pt2['playthroughAccessToken']}"}).json()
        stripped0 = {k: v for k, v in bootstrap0.items() if k != "playthroughId"}
        stripped_after = {k: v for k, v in b_after.items() if k != "playthroughId"}
        check(stripped0 == stripped_after, "2c",
              "investigation bootstrap unchanged after catalog mutation (only the per-playthrough id differs)")

        # ---- provenance must NOT appear anywhere in the public DTOs ---------
        hits = scan_provenance_keys(public_dto, bootstrap0, public_after, b_after)
        check(not hits, "2c",
              f"deep key scan: no provenance/diagnostic key in any public DTO (hits={hits})")

        # every "candidates" occurrence is the PUBLIC accusation universe
        cand_bad = scan_candidates_shape(public_dto, bootstrap0, public_after, b_after)
        check(not cand_bad, "2c",
              f"'candidates' occurrences are all the public AccusationCandidatesDTO (suspects/motives/weapons) "
              f"{'(unexpected: ' + '; '.join(cand_bad) + ')' if cand_bad else ''}")

        blob = json.dumps([public_dto, bootstrap0, b_after]).encode("utf-8").decode("utf-8")
        byte_hits = [key for key in PROVENANCE_FORBIDDEN_KEYS if key in blob]
        # resolver ambiguity tuples never appear as values (provenance enum
        # values absent as bare strings too)
        for enum_token in ("CATALOG_EXACT", "CATALOG_ALIAS", "SEMANTIC_MATCH",
                           "PARAMETRIC_VARIANT", "PROCEDURAL_GENERATED",
                           "STATIC_GENERATED", "FALLBACK"):
            if f"\"{enum_token}\"" in blob:
                byte_hits.append(enum_token)
        check(not byte_hits, "2c",
              f"raw byte scan: no provenance/resolver token in public DTO bytes (hits={byte_hits})")

        REPORT["2c"]["assetIdsInWorldGraph"] = sorted(
            {p["assetId"] for p in public_dto["worldGraph"]["placements"]}
        )
    finally:
        app.state.engine.dispose()
        try:
            app.state.store.dispose()
        except Exception:
            pass
        try:
            db_path.unlink(missing_ok=True)
            scratch.rmdir()
        except OSError:
            pass


def main() -> int:
    catalog = load_catalog_from_repo()
    audit_2a(catalog)
    audit_2b(catalog)
    audit_security()
    audit_2c()
    REPORT["summary"] = {
        "status": "PASS" if not FAILED else "FAIL",
        "failures": FAILED,
        "sections": {k: v["status"] for k, v in REPORT["sections"].items()},
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(REPORT, fh, indent=2, default=str)
    print(f"\n{'ALL SECTIONS PASS' if not FAILED else 'FAILURES: ' + '; '.join(FAILED)}")
    print(f"Report written to {REPORT_PATH}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())