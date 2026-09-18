"""QA-owned Phase 14_5 CONTRACT AUDIT (independent; e2e/probes series).

Proves the Phase 14_5 gate task 2 (a-d) against the REAL repo modules with a
REAL migrated scratch SQLite DB + TestClient + in-process probes:

  2a. UNSEEN-ABSENCE — the three genuinely unseen phrases ("bronze ceremonial
      ice pick", "unusual forensic sample press", "carved ivory desk seal")
      exist in NO production lookup: repo-grep classification (every occurrence
      is inside fixtures / tests-that-assert-absence / docs / QA-owned files),
      strict ZERO in assets/catalog, KNOWN_OBJECT_TABLE, app.world.composer's
      dev-provider specs, tests/fixtures/asset_specs.py, and the noun-head
      vocabulary contains none of the full phrases; the catalog resolver
      returns an explicit FALLBACK for them.

  2b. FLOW — each unseen phrase -> ObjectRequirement (FULL phrase
      requestedName; criticality required|decorative by the documented
      weapon-context rule) -> catalog FALLBACK -> AssetSpecProvider call ->
      valid compile -> content-addressed proc.* id -> deterministic placement
      -> published world (service + wire). A KNOWN/alias name (wrench, kitchen
      knife) -> ZERO provider calls. cache hit -> zero additional calls.
      8 distinct unseen names -> EXACTLY 6 provider calls (documented budget)
      with the rest recorded as world.unresolved-object issues, no crash.

  2c. NO-SUBSTITUTION — a REQUIRED request whose only catalog answer is a
      LOW-CONFIDENCE semantic tag tie escalates to the provider (never the
      wrong catalog object); forced provider failure + repair budget -> repair
      -> PUBLISHED with the locked bytes unchanged; provider failure with NO
      repair -> FAILED and get_published() is None; an invalid (30-part)
      AssetSpec blocks the critical publication the same way.

  2d. BOUNDARIES — CaseTruth never reaches the provider (the recorded
      provider requests carry ONLY the bounded {requested_name, category_hint,
      tags} surface, zero truth/golden tokens); AssetSpec material never
      reaches the solver inputs (PublicCase + evidence facts deep-scan clean —
      the proc.* id may appear, the generatedDefinition document never does);
      the published generatedDefinition is declarative only (exact key
      surface, no executable/URL/path tokens, finite bounded numerics); the
      wire bootstrap carries the proc.* object with its generated block and
      the discoverable fingerprint evidence on an already-excluded suspect,
      and the solver stays all_true.

Run:  python e2e/probes/qa-phase145-contract-audit.py [out.json]
Output JSON: argv[1] or e2e/artifacts/qa-phase145-contract-audit.json
Exit: 0 = all PASS, 1 = any FAIL.
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


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #

UNSEEN_PHRASES = (
    "bronze ceremonial ice pick",
    "unusual forensic sample press",
    "carved ivory desk seal",
)


def _fresh_migrated_db() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="qa_p145_audit_"))
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


def _unseen_provider():
    from app.assets.spec_provider import FakeAssetSpecProvider

    from fixtures.asset_specs_unseen import UNSEEN_SPEC_CONTENT

    return FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT)


def _kits():
    from app.environments.manifests import load_all_environments

    return {k.environment_id: k for k in load_all_environments()}


def _catalog():
    from app.assets.catalog import load_catalog_from_repo

    return load_catalog_from_repo()


def _service_ctx(url, spec_provider=None, world_repair_provider=None):
    from app.assets.generated_cache import GeneratedAssetCache
    from app.persistence.store import Store

    from app.services.generation import GenerationService
    from conftest import upgrade_db
    from phase5_helpers import _phase5_settings_for

    upgrade_db(url)
    store = Store(url)
    settings = _phase5_settings_for(url)
    service = GenerationService(
        settings=settings,
        store=store,
        spec_provider=spec_provider,
        world_repair_provider=world_repair_provider,
        generated_cache=GeneratedAssetCache(),
    )
    return store, service


def _run_via_service(service, prompt, session="SESS-U"):
    from app.persistence.timebase import EpochClock

    from phase5_helpers import seed_session

    clock = EpochClock()
    seed_session(service._store, session, clock)
    return service.start_case_generation(prompt, anonymous_quota_session_id=session)


def _payload(store, case_id, version=1):
    return json.loads(store.get_published(case_id, version).payload_json)


def _pid(placement):
    return placement.get("assetId") or placement.get("asset_id") or ""


def _proc_placements(payload):
    return [
        p for p in payload["draft"]["world_graph"]["placements"]
        if str(_pid(p)).startswith("proc.")
    ]


def _draft_from_payload(payload):
    """Reconstruct the typed GeneratedDraft from the stored payload JSON."""
    from app.generation.schemas import (
        CrimeSpec,
        CrimeTimeSpec,
        EvidenceSpec,
        GeneratedDraft,
        LocationSpec,
        MotiveSpec,
        ObjectSpec,
        PersonSpec,
        PropSpec,
        SceneSpec,
        TravelRuleSpec,
        WorldGraphSpec,
    )

    d = payload["draft"]
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
                         discoverable=e.get("discoverable", True),
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


def _solve(payload):
    from app.domain.solver import solve_case

    from app.generation.pipeline import _draft_to_phase3

    draft = _draft_from_payload(payload)
    public, facts, truth = _draft_to_phase3(draft, case_id="CASE-X", title="T")
    return public, facts, truth, solve_case(public, facts)


def _compose(prompt, provider, environment_id="office", cache=None):
    from app.assets.generated_cache import GeneratedAssetCache
    from app.environments.resolver import resolve_environment

    from app.world.composer import compose_world
    from app.world.extract import extract_world_requirements

    world_reqs = extract_world_requirements(prompt, None)
    kit = _kits()[environment_id]
    return compose_world(
        world_reqs,
        env_resolver=resolve_environment,
        spec_provider=provider,
        evidence_placements=(),
        catalog=_catalog(),
        kit=kit,
        cache=cache if cache is not None else GeneratedAssetCache(),
    )


# --------------------------------------------------------------------------- #
# docstring/comment position helpers (used by the 2a repo-grep)
# --------------------------------------------------------------------------- #
def _docstring_hit_positions(text: str, phrase: str) -> list[int]:
    lower = text.casefold()
    needle = phrase.casefold()
    out = []
    start = 0
    while True:
        idx = lower.find(needle, start)
        if idx < 0:
            break
        out.append(idx)
        start = idx + len(needle)
    return out


def _triple_region(text: str, pos: int, marker: str) -> bool:
    depth = 0
    idx = 0
    while True:
        found = text.find(marker, idx)
        if found < 0:
            break
        if pos < found:
            # the state BEFORE this marker decides: pos sits inside an odd
            # (open) triple-quoted region when depth is 1.
            return depth == 1
        depth ^= 1
        idx = found + len(marker)
    return False


def _in_doc_or_comment(text: str, pos: int) -> bool:
    # positions inside a triple-quoted string OR a # comment line
    line_start = text.rfind("\n", 0, pos) + 1
    prefix = text[line_start:pos]
    if prefix.lstrip().startswith("#"):
        return True
    return _triple_region(text, pos, '"""') or _triple_region(text, pos, "'''")


# --------------------------------------------------------------------------- #
# 2a. UNSEEN-ABSENCE
# --------------------------------------------------------------------------- #
def audit_absence() -> None:
    section("2a. UNSEEN-ABSENCE (repo-grep classification + ZERO in production lookups)")

    def walk_text_files(root: Path):
        skipped_dirs = {
            ".git", "node_modules", ".venv", "dist", "__pycache__", ".pytest_cache",
            "artifacts", "screenshots", ".rad", ".codex", ".opencode", ".cursor",
            ".claude", ".idea", ".vscode", "test-results",
        }
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in skipped_dirs and not d.endswith(".egg-info")
            )
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".db", ".sqlite",
                                           ".pyc", ".exe", ".woff", ".woff2", ".ttf", ".map"):
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                if b"\x00" in data[:4096]:
                    continue
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if len(text) > 2_000_000:
                    continue
                yield path, text

    ALLOWED_ARTIFACT_PREFIXES = (
        BACKEND_DIR / "tests",  # fixtures + defensive tests (absence assertions)
        REPO_ROOT / "e2e",  # QA-owned probes + browser specs
        REPO_ROOT / "docs",
    )
    ALLOWED_DOC_FILES = {
        REPO_ROOT / "Phase14_5.md",
        REPO_ROOT / "Phase13.md",
        REPO_ROOT / "Phase14.md",
        REPO_ROOT / "DEFECTS.md",
        REPO_ROOT / "README.md",
        REPO_ROOT / "PACKAGE_CONTENTS.md",
        REPO_ROOT / "SUBMISSION.md",
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "DECISIONS.md",
        REPO_ROOT / "Phase_POST_MVP_ROADMAP.md",
        REPO_ROOT / "BENCHMARKS.md",
    }

    hits: dict[str, list[int]] = {}
    file_texts: dict[str, str] = {}
    product_source_hits: list[str] = []
    for path, text in walk_text_files(REPO_ROOT):
        lower = text.casefold()
        rel = str(path.relative_to(REPO_ROOT))
        file_texts[rel] = text
        for phrase in UNSEEN_PHRASES:
            needle = phrase.casefold()
            start = 0
            while True:
                idx = lower.find(needle, start)
                if idx < 0:
                    break
                hits.setdefault(rel, []).append(idx)
                start = idx + len(needle)
# strict ZERO inside the shipped source trees (assets/ + backend/app/)
    if path.is_relative_to(REPO_ROOT / "assets") or path.is_relative_to(BACKEND_DIR / "app"):
        for phrase in UNSEEN_PHRASES:
            if phrase.casefold() in lower:
                # only documentation occurrences (module/comment docstrings)
                # are permitted in app source — verify each occurrence sits
                # inside a triple-quoted block or a # comment region.
                for idx in _docstring_hit_positions(text, phrase):
                    if not _in_doc_or_comment(text, idx):
                        product_source_hits.append(f"{path.relative_to(REPO_ROOT)}:{idx}")

    disallowed = []
    for rel, positions in hits.items():
        path = REPO_ROOT / rel
        walker_text = file_texts.get(rel, "")
        if path.is_relative_to(REPO_ROOT / "assets") or path.is_relative_to(BACKEND_DIR / "app"):
            source_hits = [
                idx for idx in positions
                if not _in_doc_or_comment(walker_text, idx)
            ]
            if source_hits:
                disallowed.append(f"{rel} ({len(source_hits)} non-doc occurrences)")
        elif (
            not path.is_relative_to(ALLOWED_ARTIFACT_PREFIXES[0])
            and not path.is_relative_to(ALLOWED_ARTIFACT_PREFIXES[1])
            and path not in ALLOWED_DOC_FILES
        ):
            disallowed.append(rel)

    record(
        "2a repo-grep: every occurrence is a fixture/test/doc/QA artifact, none in production data",
        not disallowed and not product_source_hits,
        {"occurrences": {rel: len(pos) for rel, pos in sorted(hits.items())},
         "unexpected": disallowed[:20], "productSourceHits": product_source_hits[:20]},
    )

    # programmatic ZERO checks
    from app.assets.catalog import load_catalog_from_repo
    from app.assets.resolver import AssetRequest, Provenance, resolve
    from app.world.composer import KnownObjectSpecProvider, _KNOWN_PROCEDURAL_SPECS
    from app.world.extract import KNOWN_OBJECT_TABLE, NOUN_HEAD_VOCABULARY

    table_text = " ".join(
        [e.requested_name for e in KNOWN_OBJECT_TABLE]
        + [t for e in KNOWN_OBJECT_TABLE for t in e.triggers]
    ).casefold()
    catalog_text = (REPO_ROOT / "assets" / "catalog" / "catalog.json").read_text(encoding="utf-8").casefold()
    phase13_fixtures = (BACKEND_DIR / "tests" / "fixtures" / "asset_specs.py").read_text(encoding="utf-8").casefold()
    composer_specs = " ".join(
        list(_KNOWN_PROCEDURAL_SPECS.keys()) + list(_KNOWN_PROCEDURAL_SPECS.values())
    ).casefold()
    vocab_text = " ".join(sorted(NOUN_HEAD_VOCABULARY)).casefold()
    zeros: dict[str, bool] = {}
    for phrase in UNSEEN_PHRASES:
        needle = phrase.casefold()
        zeros[f"{phrase} not in KNOWN_OBJECT_TABLE"] = needle not in table_text
        zeros[f"{phrase} not in assets/catalog/catalog.json"] = needle not in catalog_text
        zeros[f"{phrase} not in phase-13 fixtures"] = needle not in phase13_fixtures
        zeros[f"{phrase} not in composer dev-provider specs"] = needle not in composer_specs
        zeros[f"{phrase} not in noun-head vocabulary (full phrase)"] = needle not in vocab_text
    record(
        "2a programmatic ZERO: catalog / KNOWN_OBJECT_TABLE / phase-13 fixtures / dev provider / vocabulary",
        all(zeros.values()),
        zeros,
    )

    # the resolver answers an explicit FALLBACK for the unseen names
    fallbacks = {}
    for phrase in UNSEEN_PHRASES:
        fallbacks[phrase] = resolve(AssetRequest(requested_name=phrase)).provenance.name
    record(
        "2a the unseen names are a catalog MISS (explicit FALLBACK, never an alias)",
        all(v == "FALLBACK" for v in fallbacks.values()),
        fallbacks,
    )

    # extraction treats them as UNSEEN ObjectRequirements with the FULL phrase
    from app.world.extract import extract_world_requirements

    extracted = extract_world_requirements(
        "A carved ivory desk seal and an unusual forensic sample press were found "
        "in the office near the body."
    )
    names = {o.requested_name for o in extracted.objects}
    record(
        "2a extraction keeps the FULL phrase as requestedName (never a catalog alias)",
        "carved ivory desk seal" in names and "unusual forensic sample press" in names,
        {"names": sorted(names)},
    )


# --------------------------------------------------------------------------- #
# 2b. FLOW
# --------------------------------------------------------------------------- #
def audit_flow() -> None:
    section("2b. FLOW (unseen phrase -> ObjectRequirement -> FALLBACK -> provider -> proc.* -> placed -> published)")

    from app.assets.compiler import COMPILER_VERSION, asset_id_for
    from app.assets.spec_provider import (
        MAX_SPEC_PROVIDER_CALLS_PER_GENERATION,
        AssetSpecRequest,
        AssetSpecResponse,
        CountingSpecProvider,
        FakeAssetSpecProvider,
    )
    from app.assets.specs import parse_asset_spec
    from app.world.extract import extract_world_requirements
    from app.world.requirements import CRITICALITY_REQUIRED

    from fixtures.asset_specs_unseen import (
        BRONZE_ICE_PICK_NAME,
        CARVED_IVORY_DESK_SEAL_NAME,
        FORENSIC_SAMPLE_PRESS_NAME,
        UNSEEN_SPEC_CONTENT,
    )

    REQUIRED_PROMPTS = {
        BRONZE_ICE_PICK_NAME: (
            "A murder in an office. The killer used a bronze ceremonial ice pick "
            "to stab the victim near the body."
        ),
        FORENSIC_SAMPLE_PRESS_NAME: (
            "The murderer used an unusual forensic sample press to crush the "
            "evidence in the office."
        ),
        CARVED_IVORY_DESK_SEAL_NAME: (
            "The killer used a carved ivory desk seal to mark the letters near "
            "the body in the office."
        ),
    }
    DECORATIVE_PROMPTS = {
        FORENSIC_SAMPLE_PRESS_NAME: "An unusual forensic sample press was found in the office.",
        CARVED_IVORY_DESK_SEAL_NAME: "A carved ivory desk seal was left on the desk.",
        BRONZE_ICE_PICK_NAME: "A bronze ceremonial ice pick was placed on the shelf.",
    }

    # (1) parse-level: full phrase requestedName + criticality by the rule
    req_detail = {}
    for name, prompt in REQUIRED_PROMPTS.items():
        reqs = extract_world_requirements(prompt)
        found = next((o for o in reqs.objects if o.requested_name == name), None)
        req_detail[name] = {
            "requestedName": found.requested_name if found else None,
            "criticality": found.criticality if found else None,
        }
    for name, prompt in DECORATIVE_PROMPTS.items():
        reqs = extract_world_requirements(prompt)
        found = next((o for o in reqs.objects if o.requested_name == name), None)
        if found is not None:
            req_detail[f"{name} (decorative prompt)"] = {
                "requestedName": found.requested_name,
                "criticality": found.criticality,
            }
    record(
        "2b each unseen phrase -> ObjectRequirement with FULL requestedName + documented criticality",
        all(
            d.get("requestedName") == expected
            for d, expected in zip(
                (req_detail[n] for n in REQUIRED_PROMPTS),
                REQUIRED_PROMPTS,
            )
        )
        and all(req_detail[n]["criticality"] == CRITICALITY_REQUIRED for n in REQUIRED_PROMPTS)
        and all(
            req_detail[f"{n} (decorative prompt)"]["criticality"] == "decorative"
            for n in DECORATIVE_PROMPTS
        )
        and all(
            req_detail[f"{n} (decorative prompt)"]["requestedName"] == n
            for n in DECORATIVE_PROMPTS
        ),
        req_detail,
    )

    # (2) provider call -> proc.* content-addressed id -> placement for each
    # (per-phrase kit: ice pick -> office, sample press -> warehouse, desk seal
    # -> office; every phrase is REQUIRED and each kit can host the compiled
    # asset category)
    KIT_FOR_PHRASE = {
        BRONZE_ICE_PICK_NAME: "office",
        FORENSIC_SAMPLE_PRESS_NAME: "warehouse",
        CARVED_IVORY_DESK_SEAL_NAME: "office",
    }
    flow_detail = {}
    for name, prompt in REQUIRED_PROMPTS.items():
        provider = FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT)
        counting = CountingSpecProvider(provider)
        composition = _compose(prompt, counting, KIT_FOR_PHRASE[name])
        proc = [p for p in composition.placements if p.asset_id.startswith("proc.")]
        expected_id = asset_id_for(
            parse_asset_spec(UNSEEN_SPEC_CONTENT[name.casefold().strip()], non_throwing=False),
            compiler_version=COMPILER_VERSION,
        )
        ok = (
            composition.issues == ()
            and [r.requested_name for r in counting.call_log] == [name]
            and len(proc) == 1
            and proc[0].asset_id == expected_id
            and composition.provenance_by_object_id[proc[0].object_id] == "PROCEDURAL_GENERATED"
            and proc[0].generated_definition is not None
            and proc[0].generated_definition["assetId"] == expected_id
        )
        flow_detail[name] = {
            "kit": KIT_FOR_PHRASE[name],
            "providerCalls": [r.requested_name for r in counting.call_log],
            "procId": proc[0].asset_id if proc else None,
            "expectedId": expected_id,
            "objectId": proc[0].object_id if proc else None,
            "provenance": composition.provenance_by_object_id.get(proc[0].object_id)
            if proc else None,
            "issues": list(composition.issues),
        }
        if not ok:
            flow_detail[name]["ok"] = False
    record(
        "2b each unseen REQUIRED phrase: provider called once -> content-addressed proc.* id -> placed",
        all(flow_detail[n].get("ok", True) for n in REQUIRED_PROMPTS),
        flow_detail,
    )

    # (2b-extra) the evidence-category sample press cannot be placed on the
    # OFFICE kit (its evidence anchors are fully occupied by the golden base
    # set; the placer reports invalid-placement -> the world composer records
    # the honest issue and the lifecycle FAILS publication safely — NEVER a
    # wrong substitution, NEVER a crash). Same object on the warehouse kit is
    # placed and publishes (the service-level proof below).
    office_comp = _compose(REQUIRED_PROMPTS[FORENSIC_SAMPLE_PRESS_NAME], _unseen_provider(), "office")
    record(
        "2b (extra) evidence-category press on the office kit -> honest world.invalid-placement (safe fail, no substitution)",
        any("world.invalid-placement" in i for i in office_comp.issues)
        and not any(p.asset_id.startswith("proc.") for p in office_comp.placements),
        {"issues": list(office_comp.issues)[:4],
         "procPlacements": [p.asset_id for p in office_comp.placements if p.asset_id.startswith("proc.")],
         "catalogAssetSubstituted": sorted({p.asset_id for p in office_comp.placements})[:12]},
    )

    # (3) KNOWN/alias name -> ZERO provider calls
    from app.world.extract import extract_world_requirements as _extract

    known_names = ("wrench", "kitchen knife")
    known_detail = {}
    for name in known_names:
        counting = CountingSpecProvider(_unseen_provider())
        prompt = f"A {name} was used near the body in the warehouse."
        if "kitchen" in name:
            prompt = "A kitchen knife was used near the body in the apartment."
        composition = _compose(prompt, counting, "warehouse" if "wrench" in name else "apartment")
        known_detail[name] = {
            "providerCalls": counting.call_count,
            "issues": list(composition.issues),
            "assets": sorted({p.asset_id for p in composition.placements}),
        }
    record(
        "2b known/alias names resolve with ZERO provider calls",
        all(known_detail[n]["providerCalls"] == 0 and known_detail[n]["issues"] == []
            for n in known_names)
        and "PROP_WRENCH_01" in known_detail["wrench"]["assets"]
        and "PROP_KITCHEN_KNIFE_01" in known_detail["kitchen knife"]["assets"],
        known_detail,
    )

    # (4) cache hit -> zero additional provider calls within one oracle
    from app.assets.generated_cache import GeneratedAssetCache
    from app.assets.oracle import GeneratedAssetOracle
    from app.assets.resolver import AssetRequest

    oracle = GeneratedAssetOracle(catalog=_catalog(), cache=GeneratedAssetCache())
    counting = CountingSpecProvider(_unseen_provider())
    first = oracle.resolve_or_generate(
        AssetRequest(requested_name=BRONZE_ICE_PICK_NAME),
        spec_provider=counting,
        force_generate=True,
    )
    second = oracle.resolve_or_generate(
        AssetRequest(requested_name=BRONZE_ICE_PICK_NAME),
        spec_provider=counting,
        force_generate=True,
    )
    record(
        "2b cache hit -> zero additional provider calls (same id)",
        first.generated is not None and second.generated is not None
        and first.generated.asset_id == second.generated.asset_id
        and counting.call_count == 1,
        {"callCount": counting.call_count,
         "assetId": first.generated.asset_id if first.generated else None},
    )

    # (5) 8 distinct unseen names -> exactly MAX budget provider calls, rest as
    # world.unresolved-object issues, no crash
    class _EagerProvider:
        def __init__(self):
            from app.assets.spec_provider import FakeAssetSpecProvider

            self.calls: list[AssetSpecRequest] = []
            self._inner = FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT)

        def generate(self, request: AssetSpecRequest) -> AssetSpecResponse:
            self.calls.append(request)
            return AssetSpecResponse(
                content=UNSEEN_SPEC_CONTENT[CARVED_IVORY_DESK_SEAL_NAME.casefold()]
            )

    from app.assets.generated_cache import GeneratedAssetCache as _GAC
    from app.environments.resolver import resolve_environment
    from app.world.composer import compose_world
    from app.world.requirements import ObjectRequest, WorldRequirements

    provider = _EagerProvider()
    requests = tuple(
        ObjectRequest(requested_name=f"carved ivory desk seal {index}")
        for index in range(8)
    )
    composition = compose_world(
        WorldRequirements(objects=requests),
        env_resolver=resolve_environment,
        spec_provider=provider,
        evidence_placements=(),
        catalog=_catalog(),
        kit=_kits()["office"],
        cache=_GAC(),
    )
    record(
        "2b 8 distinct unseen names -> exactly %d provider calls; remainder recorded as issues, no crash"
        % MAX_SPEC_PROVIDER_CALLS_PER_GENERATION,
        len(provider.calls) == MAX_SPEC_PROVIDER_CALLS_PER_GENERATION
        and composition.issues
        and sum("world.unresolved-object" in i for i in composition.issues) >= 1,
        {"providerCalls": len(provider.calls),
         "issues": list(composition.issues)[:8],
         "placements": len(composition.placements)},
    )

    # (6) service publish (real DB) + byte-identical two runs + solver all_true
    from fixtures.world_showcase import UNSEEN_WEAPON_PROMPT

    db = _fresh_migrated_db()
    url = f"sqlite:///{db.as_posix()}"
    store_a, service_a = _service_ctx(url, spec_provider=_unseen_provider())
    started_a = _run_via_service(service_a, UNSEEN_WEAPON_PROMPT, "SESS-A")
    payload_a = _payload(store_a, started_a.case_id)
    proc_a = _proc_placements(payload_a)
    public_a, facts_a, truth_a, proof_a = _solve(payload_a)
    from app.validation.solution import evaluate_solution

    store_b, service_b = _service_ctx(url, spec_provider=_unseen_provider())
    started_b = _run_via_service(service_b, UNSEEN_WEAPON_PROMPT, "SESS-B")
    payload_b = _payload(store_b, started_b.case_id)
    proc_b = _proc_placements(payload_b)
    detail = {
        "statusA": started_a.status,
        "statusB": started_b.status,
        "procA": [(_pid(p), p.get("objectId") or p.get("object_id")) for p in proc_a],
        "idA": _pid(proc_a[0]) if proc_a else None,
        "idB": _pid(proc_b[0]) if proc_b else None,
        "sameDraft": payload_a["draft"] == payload_b["draft"],
        "allTrue": bool(evaluate_solution(proof_a, truth_a).all_true),
        "winner": getattr(proof_a.weapon, "winner", None),
        "sceneEnv": payload_a["draft"]["scene"].get("environment_id"),
    }
    record(
        "2b service: unseen weapon -> PUBLISHED, proc.* placed, identical across runs, solver all_true",
        started_a.status == "PUBLISHED" and started_b.status == "PUBLISHED"
        and bool(proc_a) and bool(proc_b)
        and detail["idA"] == detail["idB"]
        and detail["sameDraft"] is True
        and detail["allTrue"] is True
        and detail["winner"] == "kitchen_knife"
        and detail["sceneEnv"] == "office",
        detail,
    )


# --------------------------------------------------------------------------- #
# 2c. NO-SUBSTITUTION
# --------------------------------------------------------------------------- #
def audit_no_substitution() -> None:
    section("2c. NO-SUBSTITUTION (low-confidence semantic escalation + repair/FAILED)")

    from app.assets.spec_provider import CountingSpecProvider, FakeAssetSpecProvider
    from app.world.requirements import CRITICALITY_REQUIRED, ObjectRequest, WorldRequirements

    from fixtures.asset_specs_unseen import BRONZE_ICE_PICK_NAME, UNSEEN_SPEC_CONTENT

    from app.assets.generated_cache import GeneratedAssetCache
    from app.environments.resolver import resolve_environment
    from app.world.composer import compose_world

    def _compose_request(request, provider, environment_id="office"):
        return compose_world(
            WorldRequirements(objects=(request,)),
            env_resolver=resolve_environment,
            spec_provider=provider,
            evidence_placements=(),
            catalog=_catalog(),
            kit=_kits()[environment_id],
            cache=GeneratedAssetCache(),
        )

    # (1) REQUIRED + weapon tag (best semantic tie is the knife at 3.0 < 6.0)
    counting = CountingSpecProvider(_unseen_provider())
    request = ObjectRequest(
        requested_name=BRONZE_ICE_PICK_NAME,
        tags=("weapon",),
        criticality=CRITICALITY_REQUIRED,
    )
    composition = _compose_request(request, counting)
    record_name = "2c REQUIRED + lossy semantic tie -> escalates to provider, never the knife"
    knives = [p for p in composition.placements if p.asset_id == "PROP_KITCHEN_KNIFE_01"]
    rec = composition.resolution_record["resolved"].get(BRONZE_ICE_PICK_NAME, {})
    record(
        record_name,
        composition.issues == ()
        and [r.requested_name for r in counting.call_log] == [BRONZE_ICE_PICK_NAME]
        and str(rec.get("assetId", "")).startswith("proc.decor.")
        and rec.get("provenance") == "PROCEDURAL_GENERATED"
        and len(knives) == 1,  # only the golden base knife, never a substitution
        {"callLog": [r.requested_name for r in counting.call_log],
         "resolved": rec, "knifePlacements": len(knives)},
    )

    # (2) control: DECORATIVE + same lossy tag does NOT escalate (documented).
    #     The below-threshold ambiguous semantic (resolved=False) is NEVER
    #     substituted either: the request becomes an honest world.unresolved-
    #     object safe-fail note, zero provider calls, and the base world stays
    #     byte-plain (the golden knife placement is the ONLY knife).
    deco = CountingSpecProvider(FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT))
    deco_composition = _compose_request(
        ObjectRequest(requested_name=BRONZE_ICE_PICK_NAME, tags=("weapon",), criticality="decorative"),
        deco,
    )
    deco_knives = [p for p in deco_composition.placements if p.asset_id == "PROP_KITCHEN_KNIFE_01"]
    deco_resolved = deco_composition.resolution_record["resolved"].get(BRONZE_ICE_PICK_NAME)
    record(
        "2c control: DECORATIVE lossy-tie requests never escalate, never substitute (unresolved safe-fail)",
        deco.call_count == 0
        and deco_resolved == {"assetId": None, "provenance": "UNRESOLVED"}
        and any("world.unresolved-object" in i for i in deco_composition.issues)
        and len(deco_knives) == 1
        and not any(p.asset_id.startswith("proc.") for p in deco_composition.placements),
        {"callCount": deco.call_count,
         "resolved": deco_resolved,
         "issues": list(deco_composition.issues)[:3],
         "knifePlacements": len(deco_knives),
         "procPlacements": [p.asset_id for p in deco_composition.placements if p.asset_id.startswith("proc.")]},
    )

    # (3) forced provider failure + repair budget -> repair -> PUBLISHED (locked unchanged)
    from app.generation.pipeline import normalize_prompt
    from app.world.extract import extract_world_requirements

    from fixtures.world_showcase import UNSEEN_WEAPON_PROMPT

    class FlakyOnceSpecProvider:
        def __init__(self, name, spec):
            self.name = name.casefold().strip()
            self.spec = spec
            self.calls = 0

        def generate(self, request):
            from app.assets.spec_provider import AssetSpecResponse

            self.calls += 1
            if request.requested_name.casefold().strip() != self.name:
                return AssetSpecResponse(content=None)
            if self.calls == 1:
                return AssetSpecResponse(content=None)  # first attempt fails
            return AssetSpecResponse(content=self.spec)

    class KeepReqsRepair:
        def __init__(self, world_reqs):
            self.calls: list[tuple[str, ...]] = []
            self.world_reqs = world_reqs

        def __call__(self, diagnostics):
            self.calls.append(tuple(sorted(diagnostics or ())))
            return self.world_reqs

    locked, world_reqs = normalize_prompt(UNSEEN_WEAPON_PROMPT, max_chars=4000)
    locked_before = dict(locked.locked_fields())
    flaky = FlakyOnceSpecProvider(BRONZE_ICE_PICK_NAME, UNSEEN_SPEC_CONTENT[BRONZE_ICE_PICK_NAME])
    repair = KeepReqsRepair(extract_world_requirements(UNSEEN_WEAPON_PROMPT, locked))
    db = _fresh_migrated_db()
    url = f"sqlite:///{db.as_posix()}"
    store, service = _service_ctx(url, spec_provider=flaky, world_repair_provider=repair)
    started = _run_via_service(service, UNSEEN_WEAPON_PROMPT, "SESS-R")
    payload = _payload(store, started.case_id)
    locked_after = {key: value for key, value in _locked_of(payload).items()}
    proc = _proc_placements(payload)
    record(
        "2c forced provider failure + repair -> repair -> PUBLISHED, locked bytes unchanged",
        started.status == "PUBLISHED"
        and flaky.calls >= 2
        and bool(repair.calls)
        and any("world.unresolved-object" in i for i in repair.calls[0])
        and bool(proc)
        and locked_after == locked_before,
        {"status": started.status, "flakyCalls": flaky.calls,
         "repairConsulted": bool(repair.calls),
         "repairDiagnostics": list(repair.calls[0]) if repair.calls else [],
         "procObjectId": (proc[0].get("objectId") or proc[0].get("object_id")) if proc else None,
         "procAssetId": _pid(proc[0]) if proc else None,
         "lockedUnchanged": locked_after == locked_before},
    )

    # (4) provider failure + NO repair -> FAILED, never published
    class FailingSpecProvider:
        def __init__(self):
            self.calls = 0
            self.call_log = []

        def generate(self, request):
            from app.assets.spec_provider import AssetSpecResponse

            self.calls += 1
            self.call_log.append(request)
            return AssetSpecResponse(content=None)

    failing = FailingSpecProvider()
    db2 = _fresh_migrated_db()
    store_f, service_f = _service_ctx(db2_url := f"sqlite:///{db2.as_posix()}",
                                       spec_provider=failing, world_repair_provider=None)
    started_f = _run_via_service(service_f, UNSEEN_WEAPON_PROMPT, "SESS-F")
    record(
        "2c provider failure + no repair -> FAILED, never published",
        started_f.status == "FAILED"
        and store_f.get_published(started_f.case_id, 1) is None
        and failing.calls >= 1
        and failing.call_log[0].requested_name == BRONZE_ICE_PICK_NAME,
        {"status": started_f.status, "published": store_f.get_published(started_f.case_id, 1),
         "providerCalls": failing.calls,
         "firstRequestedName": failing.call_log[0].requested_name if failing.call_log else None},
    )

    # (5) invalid 30-part spec for the REQUIRED object -> FAILED never published
    class InvalidSpecProvider:
        def __init__(self):
            self.calls = 0

        def generate(self, request):
            from app.assets.spec_provider import AssetSpecResponse

            self.calls += 1
            parts = ",".join(
                '{"id": "part_%02d", "role": "block", "primitive": "box",'
                ' "transform": {"position": {"x": 0, "y": 0, "z": 0},'
                ' "rotation": {"x": 0, "y": 0, "z": 0},'
                ' "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},'
                ' "material": "plastic"}' % index
                for index in range(30)
            )
            return AssetSpecResponse(
                content='{"canonicalName": "Too Many Blocks", "category": "evidence",'
                ' "subtype": "block", "dimensions": {"x": 0.5, "y": 0.5, "z": 0.5},'
                ' "parts": [%s]}' % parts
            )

    invalid = InvalidSpecProvider()
    db3 = _fresh_migrated_db()
    store_i, service_i = _service_ctx(f"sqlite:///{db3.as_posix()}",
                                       spec_provider=invalid, world_repair_provider=None)
    started_i = _run_via_service(service_i, UNSEEN_WEAPON_PROMPT, "SESS-I")
    record(
        "2c invalid AssetSpec (30 parts) blocks the critical publication -> FAILED",
        started_i.status == "FAILED"
        and store_i.get_published(started_i.case_id, 1) is None
        and invalid.calls >= 1,
        {"status": started_i.status, "published": store_i.get_published(started_i.case_id, 1),
         "providerCalls": invalid.calls},
    )


def _locked_of(payload) -> dict:
    locked = payload.get("locked")
    if isinstance(locked, dict):
        return locked
    return {}


# --------------------------------------------------------------------------- #
# 2d. BOUNDARIES
# --------------------------------------------------------------------------- #
def audit_boundaries() -> None:
    section("2d. BOUNDARIES (CaseTruth / solver / declarative frontend surface)")

    from app.assets.spec_provider import AssetSpecRequest, AssetSpecResponse
    from app.services.generation import GenerationService

    from fixtures.asset_specs_unseen import UNSEEN_SPEC_CONTENT
    from fixtures.world_showcase import UNSEEN_WEAPON_PROMPT

    # (1) CaseTruth never reaches the provider: recorded requests carry ONLY
    # the bounded surface and zero truth/golden tokens
    seen: list[AssetSpecRequest] = []

    class RecordingProvider:
        def generate(self, request: AssetSpecRequest) -> AssetSpecResponse:
            seen.append(request)
            return AssetSpecResponse(
                content=UNSEEN_SPEC_CONTENT.get(request.requested_name.casefold())
            )

    composition = _compose(UNSEEN_WEAPON_PROMPT, RecordingProvider(), "office")
    truth_tokens = (
        "thomas_reed", "sarah_miller", "cover_up_embezzlement", "kitchen_knife",
        "2026-09-11", "22:17", "murdererId", "victimId", "weaponId",
        "crimeTime", "motiveId", "solutionProof", "truth",
    )
    leaks = []
    for request in seen:
        serialized = " ".join(
            str(part) for part in (request.requested_name, request.category_hint, request.tags)
            if part is not None
        )
        for token in truth_tokens:
            if token in serialized.casefold():
                leaks.append((token, serialized))
    fields_ok = all(set(vars(r)) == {"requested_name", "category_hint", "tags"} for r in seen)
    record(
        "2d CaseTruth never reaches the provider (bounded surface + zero truth tokens)",
        bool(seen) and fields_ok and not leaks and composition.issues == (),
        {"requests": [{k: v for k, v in vars(r).items()} for r in seen],
         "leaks": leaks,
         "compositionIssues": list(composition.issues)},
    )

    # (2) AssetSpec never reaches the solver inputs
    db = _fresh_migrated_db()
    url = f"sqlite:///{db.as_posix()}"
    store, service = _service_ctx(url, spec_provider=RecordingProvider())
    started = _run_via_service(service, UNSEEN_WEAPON_PROMPT, "SESS-B1")
    payload = _payload(store, started.case_id)
    public, facts, _truth, proof = _solve(payload)
    from app.validation.solution import evaluate_solution

    _SPEC_TOKENS = (
        "canonicalName", "primitive", "material", "dimensions", "hitbox", "parts",
        "transform", "generated_definition", "generatedDefinition", "compilerVersion",
        "schemaVersion", "sourceColor", "parentId",
    )

    def _to_plain(obj):
        import dataclasses

        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return {f.name: _to_plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
        if isinstance(obj, dict):
            return {str(k): _to_plain(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_to_plain(v) for v in obj]
        if isinstance(obj, (frozenset, set)):
            return sorted(_to_plain(v) for v in obj)
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        return str(obj)

    solver_input_text = json.dumps(
        {"public": _to_plain(public), "evidence": [_to_plain(f) for f in facts]},
        sort_keys=True,
    )
    spec_tokens_found = [t for t in _SPEC_TOKENS if t in solver_input_text]
    record(
        "2d AssetSpec material never reaches the solver inputs (proc.* id allowed, definition not)",
        evaluate_solution(proof, _truth).all_true is True
        and getattr(proof.weapon, "winner", None) == "kitchen_knife"
        and not spec_tokens_found
        and "proc.decor." in solver_input_text,
        {"allTrue": bool(evaluate_solution(proof, _truth).all_true),
         "specTokensFound": spec_tokens_found,
         "procIdPresent": "proc.decor." in solver_input_text,
         "weaponWinner": getattr(proof.weapon, "winner", None)},
    )

    # (3) published generatedDefinition is declarative only (data-scan)
    from app.services.publication import project_world_objects

    world_objects = project_world_objects(payload)
    (ice_pick,) = [item for item in world_objects if item["assetId"].startswith("proc.")]
    generated = ice_pick["generated"]
    key_surface_ok = set(generated) == {
        "compilerVersion", "schemaVersion", "assetId", "canonicalName", "category",
        "subtype", "dimensions", "parts", "hitbox",
    }
    text = json.dumps(generated)
    markers = [
        "<script", "eval(", "new Function", "javascript:", "data:", "file://",
        "http://", "https://", "onclick=", "onload=", "onerror=", "import(",
        "exec(", "shader", "../", "..\\", "fetch(", "WebSocket", "<img",
    ]
    marker_hits = [m for m in markers if m in text]
    finite_ok = True
    for part in generated["parts"]:
        transform = part["transform"]
        for axis in ("x", "y", "z"):
            for vec in ("position", "rotation", "scale"):
                value = transform[vec][axis]
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    finite_ok = False
    from app.assets.compiler import validate_embedded_definition

    validator_ok = validate_embedded_definition(ice_pick["assetId"], generated) is not None
    record(
        "2d published generatedDefinition is declarative (exact key surface, no executable/URL/path, finite)",
        key_surface_ok and not marker_hits and finite_ok and validator_ok,
        {"keySurface": sorted(generated), "markerHits": marker_hits,
         "finite": finite_ok, "validatorAccepted": validator_ok},
    )

    # (4) the unseen-evidence E2E seam is in the published payload/data layer:
    # discoverable fingerprint fact on the already-excluded suspect, placement
    # interaction + evidenceId, evidence-capable anchor, solver all_true intact
    from app.environments.placer import EVIDENCE_CAPABLE_TYPES
    from app.services.publication import project_discovery

    placement = next(
        p for p in payload["draft"]["world_graph"]["placements"]
        if (p.get("objectId") or p.get("object_id")) == ice_pick["objectId"]
    )
    kit = _kits()["office"]
    anchor = kit.by_id.get(placement.get("anchor") or placement.get("anchor"))
    evidence_ids = {f["id"] for f in payload["draft"]["evidence"]}
    discovery = project_discovery(payload, "bronze_ceremonial_ice_pick_fp_01")
    persons = {p["person_id"] for p in payload["draft"]["persons"]}
    record(
        "2d unseen-evidence seam: discoverable fingerprint fact on already-excluded suspect, all_true intact",
        ice_pick["interaction"] in ("inspect", "read")
        and ice_pick["evidenceId"] == "bronze_ceremonial_ice_pick_fp_01"
        and "bronze_ceremonial_ice_pick_fp_01" in evidence_ids
        and anchor is not None and anchor.type in EVIDENCE_CAPABLE_TYPES
        and discovery is not None and discovery["kind"] == "forensic"
        and "fingerprint" in str(discovery["title"]).casefold()
        and "michael_carter" in persons
        and bool(evaluate_solution(proof, _truth).all_true)
        and "bronze_ceremonial_ice_pick" not in tuple(sorted(proof.weapon.universe)),
        {"interaction": ice_pick["interaction"], "evidenceId": ice_pick["evidenceId"],
         "evidenceFactPresent": "bronze_ceremonial_ice_pick_fp_01" in evidence_ids,
         "anchorType": anchor.type if anchor else None,
         "discoveryKind": discovery["kind"] if discovery else None,
         "title": discovery["title"] if discovery else None,
         "weaponUniverse": tuple(sorted(proof.weapon.universe))},
    )

    # (5) WIRE: real FastAPI + TestClient over the migrated scratch DB with the
    # fixture-scripted provider: POST /cases -> 201 PUBLISHED, bootstrap carries
    # the proc.* object + generated block + interaction + evidence link, and the
    # public case DTO + investigation responses leak-scan clean (frozen keys).
    from fastapi.testclient import TestClient

    from app.assets.generated_cache import GeneratedAssetCache
    from app.assets.spec_provider import FakeAssetSpecProvider
    from app.core.config import Settings
    from app.main import create_app
    from conftest import DEFAULT_CORS, upgrade_db

    db_w = _fresh_migrated_db()
    url_w = f"sqlite:///{db_w.as_posix()}"
    upgrade_db(url_w)
    application = create_app(Settings(database_url=url_w, cors_allowed_origins=list(DEFAULT_CORS)))
    application.state.generation_service = GenerationService(
        settings=application.state.settings,
        store=application.state.store,
        clock=application.state.clock,
        publication=application.state.publication_service,
        spec_provider=FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT),
        generated_cache=GeneratedAssetCache(),
    )
    with TestClient(application) as client:
        token = client.post("/api/v1/sessions/anonymous").json()["anonymousSessionToken"]
        res = client.post(
            "/api/v1/cases",
            json={"prompt": UNSEEN_WEAPON_PROMPT},
            headers={"Authorization": f"Bearer {token}"},
        )
        created = res.json()
        pt = client.post(
            f"/api/v1/cases/{created['caseId']}/versions/1/playthroughs",
            headers={"Authorization": f"Bearer {created['creatorAccessToken']}"},
        ).json()
        boot = client.get(
            f"/api/v1/playthroughs/{pt['playthroughId']}/investigation",
            headers={"Authorization": f"Bearer {pt['playthroughAccessToken']}"},
        ).json()
        wire_objects = boot["scene"]["worldObjects"]
        wire_proc = [o for o in wire_objects if o["assetId"].startswith("proc.")]
        dto = client.get(
            f"/api/v1/cases/{created['caseId']}?version=1",
            headers={"Authorization": f"Bearer {created['creatorAccessToken']}"},
        ).json()
        dto_proc = [o for o in dto.get("objects", []) if o["assetId"].startswith("proc.")]
        wire_ok = (
            res.status_code == 201 and created["status"] == "PUBLISHED"
            and len(wire_proc) == 1
            and wire_proc[0]["generated"] is not None
            and wire_proc[0]["interaction"] in ("inspect", "read")
            and wire_proc[0]["evidenceId"] == "bronze_ceremonial_ice_pick_fp_01"
            and len(dto_proc) == 1
        )
        record(
            "2d wire: public API POST /cases -> 201 PUBLISHED; bootstrap carries proc.* with generated block",
            wire_ok,
            {"status": created.get("status"), "caseId": created.get("caseId"),
             "wireProcObjects": [
                 {"objectId": o["objectId"], "assetId": o["assetId"],
                  "generated": o.get("generated") is not None,
                  "interaction": o.get("interaction"), "evidenceId": o.get("evidenceId")}
                 for o in wire_proc
             ],
             "dtoProcCount": len(dto_proc)},
        )
        application.state.engine.dispose()
        application.state.store.dispose()


def main() -> int:
    audit_absence()
    audit_flow()
    audit_no_substitution()
    audit_boundaries()
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "e2e" / "artifacts" / "qa-phase145-contract-audit.json"
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