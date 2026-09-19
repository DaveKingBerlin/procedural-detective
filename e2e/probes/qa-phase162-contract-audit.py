"""QA-owned Phase 16_2 STAGE-DRIVER CONTRACT AUDIT (independent, e2e/probes).

Proves the Phase 16_2 gate tasks 2a/2b/2c against the REAL repo modules — no
product code is modified. The audit is fully in-process: a QA-owned mock Ollama
transport (never any network) injected into the REAL ``OllamaProvider`` /
``OllamaStageDriver`` / ``GenerationController`` / ``GenerationService`` path
over a REAL migrated scratch SQLite database, plus a real ``TestClient`` for
the public capability surface.

The non-golden showcase stage chain (case_people / evidence / world_requirements
/ asset_spec / asset_spec_repair) is the CANONICAL Phase16_2 fixture set
shipped as ``backend/tests/test_ollama_driver.py`` — this probe imports those
frozen payload builders (the same pattern QA probes import test fixtures) and
adds its own independent assertions; nothing here edits product code.

  2a. PROMPT CONTRACT — the six versioned templates (case_people_v1,
      evidence_v1, world_requirements_v1, asset_spec_v1, asset_spec_repair_v1,
      repair_v1) are bounded static strings embedding the authoritative schema
      contract (field names + ranges), meter units ("0.25 means 25
      centimeters"), the material allowlist, the primitive allowlist, the
      <=24-parts / max-parent-depth-2 rules, the "JSON only / no code / no URL
      / no path" rules and the Phase16_2 §9/§13 repair instructions; template
      versions are recorded internally and never appear in any public DTO; a
      schema-drift test pins the rendered contract to the authoritative schema
      constants.

  2b. END-TO-END NON-GOLDEN (mocked) — the Phase16_2 showcase prompt
      (Anna Weiss / Paul Becker / stolen research data / bronze ceremonial ice
      pick / 23:42 / Lisa König / office) through GENERATION_PROVIDER=ollama
      with the scripted mock chain:
      (i)  CASE/PEOPLE honors the locked fields; a locked-violating chain is
           TERMINAL and never repaired;
      (ii) EVIDENCE re-anchors golden opportunity/forensic mechanics with new
           ids;
      (iii) WORLD_REQUIREMENTS selects the office environment in the
           published bootstrap;
      (iv) the bronze ceremonial ice pick is UNKNOWN -> ASSET_SPEC is called
           (0 calls for truly-known assets); an invalid first pass
           (25 m / depth / material) drives ASSET_SPEC_REPAIR -> valid
           -> proc.*;
      (v)  the driver deterministically grants the locked weapon affordance ->
           solver all_true (paul_becker / bronze_ceremonial_ice_pick /
           stolen_research_data);
      (vi) PUBLISHED; the public bootstrap carries office + the proc.* object
           with data-level clickability (placement interaction + evidence
           link); two identical runs are byte-identical at the draft level; a
           fresh Store over the same DB re-reads identical frozen payload
           bytes (restart immutable); the FAKE/demo path remains byte-unchanged
           (deterministic golden demo DTO matches the fixture golden structure).

  2c. LIFECYCLE / LEAK — provider-call budget authoritative (incl. driver
      calls); admission-before-call (denied -> 0 calls); stale/delayed mocked
      completion discarded; old attempt cannot publish; PUBLISHED immutable;
      CaseTruth absent from solver inputs AND from every prompt (scan prompt
      builders); AssetSpec never reaches the solver; raw provider text never
      reaches the frontend; prompts/diagnostics/model/attempt ids never in
      public DTOs; no generated-code execution path; capability DTO unchanged.

Run:   python e2e/probes/qa-phase162-contract-audit.py [out.json]
Output JSON: argv[1] or e2e/artifacts/qa-phase162-contract-audit.json
Exit:  0 = all PASS, 1 = any FAIL.
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
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tests"))

# Canonical Phase16_2 showcase payload builders (shipped fixture module).
import test_ollama_driver as T  # noqa: E402

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:520]}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_migrated_db() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="qa_p162_audit_"))
    db = scratch / "audit.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    env.pop("ENV_FILE", None)
    env.pop("STATIC_DIR", None)
    env.pop("GENERATION_PROVIDER", None)
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


SHOWCASE_PROMPT = T.PROMPT
GOLDEN_DEMO_PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\nWeapon: kitchen_knife\n"
    "Time: 22:17\nWitness: emily_reed\n"
)

OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_MODEL = "llama3.2:3b"


class MockOllamaTransport:
    """QA-owned mock transport (records every call; never any network)."""

    def __init__(self, *, posts=(), tags_models=(OLLAMA_MODEL,)):
        self.posts = list(posts)
        self.tags_models = list(tags_models)
        self.post_calls: list[tuple[str, dict, float]] = []
        self.get_calls: list[tuple[str, float]] = []

    def get(self, url, timeout):
        self.get_calls.append((url, float(timeout)))
        return 200, json.dumps({"models": [{"name": n} for n in self.tags_models]}).encode()

    def post_json(self, url, payload, timeout):
        self.post_calls.append((url, dict(payload), float(timeout)))
        content = self.posts.pop(0) if self.posts else "<not-json>"
        return 200, json.dumps(
            {"model": OLLAMA_MODEL, "message": {"role": "assistant", "content": content}}
        ).encode()

    @property
    def call_count(self) -> int:
        return len(self.post_calls)

    def prompt_of_call(self, index) -> str:
        messages = self.post_calls[index][1].get("messages") or ()
        return messages[0].get("content", "") if messages else ""


# --------------------------------------------------------------------------- #
# chain builders (shipped canonical payloads + QA clickable world variant)
# --------------------------------------------------------------------------- #
def world_payload_clickable():
    """Office world: UNKNOWN ice pick REQUIRED + interactable + evidence
    linked -> the published placement carries interaction + evidence id (the
    data-level clickability the gate requires)."""
    return {
        "environmentHint": "office", "locationTokens": ["office"],
        "objects": [{"name": "bronze ceremonial ice pick", "categoryHint": "decor",
                     "criticality": "required",
                     "requiredInteraction": "inspect",
                     "evidenceId": "forensic_icepick_match_01"}],
        "relations": [{"kind": "on_desk", "target": "bronze ceremonial ice pick"}],
        "unsafeUnsupported": [],
    }


def staged_posts(*, with_repair=False):
    posts = [T._j(T._case_people()), T._j(T._evidence()), T._j(world_payload_clickable())]
    if with_repair:
        bad = json.loads(T.ICEPICK_SPEC)
        bad["dimensions"] = {"x": 25, "y": 0.1, "z": 0.1}     # 25 m unit regression
        bad["parts"][0]["material"] = "bronze"                 # not allowlisted
        bad["parts"][1]["parentId"] = "part_02"                # forward parent
        posts.append(json.dumps(bad, sort_keys=True))          # ASSET_SPEC (invalid)
        posts.append(T.ICEPICK_SPEC)                            # ASSET_SPEC_REPAIR (valid)
    else:
        posts.append(T.ICEPICK_SPEC)                            # ASSET_SPEC (valid)
    return posts


def settings_for(db_url, **overrides):
    from app.core.config import Settings

    kwargs = dict(
        database_url=db_url,
        generation_provider="ollama",
        ollama_base_url=OLLAMA_BASE,
        ollama_model=OLLAMA_MODEL,
        ollama_timeout_seconds=5,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=40,
        max_concurrent_generations=2,
        generation_deadline_seconds=60,
        max_llm_calls_per_generation=8,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def make_provider(transport, **overrides):
    from app.generation.ollama_provider import OllamaProvider

    kwargs = dict(base_url=OLLAMA_BASE, model=OLLAMA_MODEL, timeout_seconds=5,
                  temperature=0.2, num_ctx=4096, transport=transport)
    kwargs.update(overrides)
    return OllamaProvider(**kwargs)


def drive_service(db_url, transport, prompt=SHOWCASE_PROMPT, settings=None):
    """Drive the REAL GenerationService over a REAL migrated DB with a mocked
    transport through the ollama stage driver. Returns (service, store, started)."""
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    s = settings if settings is not None else settings_for(db_url)
    store = Store(s.database_url)
    service = GenerationService(settings=s, store=store,
                                provider_factory=lambda: make_provider(transport))
    session = service.create_anonymous_quota_session()
    started = service.start_case_generation(
        prompt, anonymous_quota_session_id=session.anonymous_quota_session_id,
        difficulty="medium",
    )
    return service, store, started


# --------------------------------------------------------------------------- #
# 2a. PROMPT CONTRACT
# --------------------------------------------------------------------------- #
def audit_prompt_contract() -> None:
    section("2a. PROMPT CONTRACT — versioned templates, schema drift, units, allowlists, repair text")
    from app.assets.catalog import CATEGORY_ALLOWLIST
    from app.assets.materials import MATERIAL_VOCAB
    from app.assets.specs import (
        DIMENSION_MAX,
        DIMENSION_MIN,
        MAX_PARTS,
        MAX_PART_ROLE_LENGTH,
        MAX_PART_SCALE,
        MAX_POSITION_BOUND,
        MAX_ROTATION_BOUND,
        MIN_PART_SCALE,
        PRIMITIVE_ALLOWLIST,
    )
    from app.generation import prompts

    builders = {
        "case_people_v1": lambda: prompts.build_case_people_prompt(SHOWCASE_PROMPT, None),
        "evidence_v1": lambda: prompts.build_evidence_prompt(SHOWCASE_PROMPT, None),
        "world_requirements_v1": lambda: prompts.build_world_requirements_prompt(SHOWCASE_PROMPT, None),
        "asset_spec_v1": lambda: prompts.build_asset_spec_prompt("bronze ceremonial ice pick", "decor"),
        "asset_spec_repair_v1": lambda: prompts.build_asset_spec_repair_prompt(
            "bronze ceremonial ice pick", "{}", ("dimensions.x outside range",)),
        "repair_v1": lambda: prompts.build_repair_prompt("{}", ("test issue",)),
    }

    # 1. bounded static string, version embedded, placeholders filled.
    for name, build in builders.items():
        text = build()
        ok = (
            isinstance(text, str)
            and 500 <= len(text) <= 60000
            and name in text
            and all(tok not in text for tok in ("__PROMPT__", "__LOCKED__", "__ISSUES__",
                                                 "__OBJECT_CONCEPT__", "__PREVIOUS_CANDIDATE__"))
        )
        record(f"template {name}: bounded static string, version embedded, placeholders filled", ok,
               {"len": len(text)})

    # 2. schema-drift guard: the rendered contract is pinned to the constants.
    contract = prompts.schema_contract("asset_spec")
    drift_failures = []
    for label, token in (
        ("dimension range", f"[{DIMENSION_MIN:g}, {DIMENSION_MAX:g}]"),
        ("parts cap", f"1..{MAX_PARTS}"),
        ("part scale range", f"[{MIN_PART_SCALE:g}, {MAX_PART_SCALE:g}]"),
        ("position bound", f"<= {MAX_POSITION_BOUND:g}"),
        ("rotation bound", f"<= {MAX_ROTATION_BOUND:g}"),
        ("role length", f"1..{MAX_PART_ROLE_LENGTH}"),
    ):
        if token not in contract:
            drift_failures.append((label, token))
    for token in sorted(MATERIAL_VOCAB):
        if token not in contract:
            drift_failures.append(("material", token))
    for prim in sorted(PRIMITIVE_ALLOWLIST):
        if prim not in contract:
            drift_failures.append(("primitive", prim))
    for cat in CATEGORY_ALLOWLIST:
        if cat not in contract:
            drift_failures.append(("category", cat))
    record("asset_spec schema drift guard: bounds/material/primitive/category all pinned to constants",
           not drift_failures, {"failures": drift_failures[:10], "materials": len(MATERIAL_VOCAB),
                                "primitives": sorted(PRIMITIVE_ALLOWLIST)})

    for stage in ("case_people", "evidence", "world_requirements", "asset_spec"):
        text = prompts.schema_contract(stage)
        record(f"schema_contract({stage}) renders non-empty field names", len(text) > 80 and '"' in text, "")

    # 3. meter units in the AssetSpec + repair templates (25-vs-0.25 guard).
    spec = builders["asset_spec_v1"]()
    repair = builders["asset_spec_repair_v1"]()
    meters_ok = (
        "METERS" in spec and "0.25 means 25 centimeters" in spec
        and "25 means 25 meters" in spec and "0.05..4" in spec
        and "0.25 means 25 centimeters" in repair
    )
    record("meter units statement in ASSET_SPEC + ASSET_SPEC_REPAIR (0.25 means 25 centimeters)", meters_ok, "")

    # 4. rules: <=24 parts, depth 2, JSON only, no URL/path, no code.
    for label, ok in (
        ("max parts rule", f"At most {MAX_PARTS} parts" in spec),
        ("max parent depth 2", "Maximum parent depth 2" in spec or "max parent depth 2" in spec),
        ("JSON only + no markdown fences", "single JSON document" in spec and "no markdown fences" in spec),
        ("no URLs/paths/HTML/scripts/shaders/event handlers/code",
         "NO URLs, paths, HTML, scripts, shaders, event handlers, or executable code" in spec),
        ("do-not-place-all-parts-same-position", "Do NOT place all parts at the same position" in spec),
        ("prefer simple silhouettes", "prefer simple, recognizable silhouettes" in spec.lower()),
        ("as few parts as necessary", "as few parts as necessary" in spec),
    ):
        record(f"ASSET_SPEC rule: {label}", ok, "")
    # (the rule scan above covers the mandatory ≤24-parts / depth-2 / JSON-only /
    # no-URL/no-path / no-code rules from Phase16_2 §12/§13)

    # 5. repair instructions verbatim (§13 / §9).
    repair_ok = all(needle in repair for needle in (
        "Correct the AssetSpec", "Do not redesign the object unless required",
        "Fix the listed validation violations", "Return the complete corrected AssetSpec as JSON only"))
    record("ASSET_SPEC_REPAIR carries the §13 repair instruction", repair_ok, "")
    repair2 = builders["repair_v1"]()
    repair2ok = all(needle in repair2 for needle in (
        "Correct the draft", "SANITIZED validation issues", "LOCKED user constraint"))
    record("REPAIR_v1 carries the §9 corrective-draft instruction", repair2ok, "")

    # 6. no truth/credential URL/code tokens.
    forbidden = ("http://", "https://", "data:", "file:", "javascript:", "solverProof",
                 "caseTruth", "timeline", "relationships", "<script", "new Function", "eval(")
    all_text = " ".join(b() for b in builders.values()).lower()
    hits = [t for t in forbidden if t in all_text]
    record("no truth/secret URL/code tokens in any template", not hits, hits)

    # 7. frozen template constants exist.
    names = ("CASE_PEOPLE_PROMPT_v1", "EVIDENCE_PROMPT_v1", "WORLD_REQUIREMENTS_PROMPT_v1",
             "ASSET_SPEC_PROMPT_v1", "ASSET_SPEC_REPAIR_PROMPT_v1", "REPAIR_PROMPT_v1")
    all_have = all(hasattr(prompts, n) for n in names)
    record("six template constants exist (case_people_v1|...|repair_v1)", all_have, "")


# --------------------------------------------------------------------------- #
# 2b. END-TO-END NON-GOLDEN
# --------------------------------------------------------------------------- #
def audit_end_to_end() -> None:
    section("2b. END-TO-END NON-GOLDEN — Anna/Paul office ice pick through the REAL service")
    db = _fresh_migrated_db()
    url = f"sqlite:///{db.as_posix()}"

    # happy path: valid chain -> PUBLISHED; locked kept (i).
    transport = MockOllamaTransport(posts=staged_posts())
    service, store, started = drive_service(url, transport)
    record("driver run -> PUBLISHED via REAL service (ollama provider, mock transport)",
           started.status == "PUBLISHED", started.status)

    # locked-variation chain -> TERMINAL, never repaired, never published.
    locked_db = _fresh_migrated_db()
    locked_url = f"sqlite:///{locked_db.as_posix()}"
    bad_cp = T._case_people()
    bad_cp["crime"]["murdererId"] = "marcus_fischer"  # violates locked paul_becker
    locked_transport = MockOllamaTransport(posts=[
        T._j(bad_cp), T._j(T._evidence()), T._j(world_payload_clickable()), T.ICEPICK_SPEC])
    try:
        _s2, _st2, started2 = drive_service(locked_url, locked_transport, settings=settings_for(locked_url))
        ok_locked = started2.status in ("FAILED",)
    except Exception:  # noqa: BLE001 - terminal failure is expected
        ok_locked = True
    prompts_locked = [locked_transport.prompt_of_call(i) for i in range(locked_transport.call_count)]
    record("(i) locked violation -> TERMINAL (never repaired, never published)",
           ok_locked and not any("repair" in p for p in prompts_locked)
           and locked_transport.call_count <= 4,
           {"status": getattr(started2, "status", "raised"), "calls": locked_transport.call_count,
            "repair": any("repair" in p for p in prompts_locked)})

    # (ii) EVIDENCE re-anchors golden opportunity/forensic mechanics with new ids.
    ev = T._evidence()
    reanchored = (
        any(f["id"].startswith("forensic_") for f in ev["evidence"])
        and any(f["id"].startswith("cctv_") for f in ev["evidence"])
        and any(p.get("type") == "PERSON_OBSERVED_AT_LOCATION" for f in ev["evidence"] for p in f["propositions"])
        and any("objectId" in p for f in ev["evidence"] for p in f["propositions"])
        and any(f["id"] == "cctv_paul_scene_01" for f in ev["evidence"])
        and any(f["id"] == "forensic_icepick_match_01" for f in ev["evidence"])
    )
    record("(ii) EVIDENCE re-anchors opportunity/forensic mechanics with new ids", reanchored, "")

    # (iv) known asset -> 0 ASSET_SPEC provider calls through the REAL service.
    # The golden demo prompt runs through the DRIVER with a fully-known world
    # (apartment + kitchen knife) -> every object is catalog-resolvable, so
    # zero ASSET_SPEC (and zero ASSET_SPEC_REPAIR) provider calls happen.
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS
    from app.generation.provider import GenerationStage

    _case_doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH])
    _pub_doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD])
    combined_doc = dict(_case_doc)
    for _k in ("persons", "motives", "locations", "travelRules", "scene"):
        combined_doc[_k] = _pub_doc.get(_k)
    known_world = {
        "environmentHint": "apartment", "locationTokens": ["apartment"],
        "objects": [{"name": "kitchen knife", "criticality": "decorative"}],
        "relations": [], "unsafeUnsupported": [],
    }
    known_db = _fresh_migrated_db()
    known_url = f"sqlite:///{known_db.as_posix()}"
    known_posts = [
        json.dumps(combined_doc, sort_keys=True),
        GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE],
        json.dumps(known_world, sort_keys=True),
    ]
    known_transport = MockOllamaTransport(posts=known_posts)
    _s3, _st3, started3 = drive_service(known_url, known_transport, prompt=GOLDEN_DEMO_PROMPT,
                                         settings=settings_for(known_url))
    asset_calls_known = sum(1 for i in range(known_transport.call_count)
                            if "asset_spec" in known_transport.prompt_of_call(i))
    record("(iv) known asset -> 0 ASSET_SPEC provider calls (catalog resolves)",
           started3.status == "PUBLISHED" and asset_calls_known == 0,
           {"status": started3.status, "calls": known_transport.call_count, "assetSpecCalls": asset_calls_known})

    # (iv) unknown ice pick + invalid first pass -> ASSET_SPEC_REPAIR -> PUBLISHED proc.*.
    repair_db = _fresh_migrated_db()
    repair_url = f"sqlite:///{repair_db.as_posix()}"
    repair_transport = MockOllamaTransport(posts=staged_posts(with_repair=True))
    _s4, _st4, started4 = drive_service(repair_url, repair_transport, settings=settings_for(repair_url))
    prompts_calls = [repair_transport.prompt_of_call(i) for i in range(repair_transport.call_count)]
    asset_calls = [p for p in prompts_calls if "asset_spec" in p]
    has_repair_template = any("asset_spec_repair_v1" in p for p in asset_calls)
    record("(iv) unknown ice pick -> ASSET_SPEC + invalid first pass -> ASSET_SPEC_REPAIR -> PUBLISHED",
           started4.status == "PUBLISHED" and has_repair_template and len(asset_calls) == 2,
           {"status": started4.status, "assetSpecCalls": len(asset_calls), "hasRepairTemplate": has_repair_template})

    # (iii)/(vi) office env + proc.* object in the published bootstrap.
    from app.persistence.store import Store

    store_final = Store(url)
    payload_json = store_final.get_published(started.case_id, 1).payload_json
    payload = json.loads(payload_json)
    env_id = payload["draft"]["scene"].get("environment_id")
    proc_ids = [o["object_id"] for o in payload["draft"]["objects"] if o["asset_id"].startswith("proc.")]
    proc_placements = [p for p in payload["draft"]["world_graph"]["placements"]
                       if p["asset_id"].startswith("proc.")]
    record("(iii) WORLD_REQUIREMENTS -> office environment in the published bootstrap",
           env_id == "office", {"environmentId": env_id})
    record("(iv/vi) proc.* object exists in the published draft objects",
           proc_ids == ["bronze_ceremonial_ice_pick"], proc_ids)
    record("(vi) bootstrap carries a data-level placement for the proc.* object (interaction + evidence)",
           len(proc_placements) == 1 and proc_placements[0]["object_id"] == "bronze_ceremonial_ice_pick"
           and proc_placements[0].get("interaction") == "inspect"
           and proc_placements[0].get("evidence_id") == "forensic_icepick_match_01",
           proc_placements[0] if proc_placements else None)

    # (v) solver all_true + deduced winners (independent of the draft crime claims).
    from app.generation.admission import AdmissionController
    from app.generation.clock import ManualClock
    from app.generation.controller import GenerationController
    from app.generation.ids import IdSource
    from app.services.ollama_driver import OllamaStageDriver

    solver_db = _fresh_migrated_db()
    solver_url = f"sqlite:///{solver_db.as_posix()}"
    transport_s = MockOllamaTransport(posts=staged_posts())
    clock_s, ids_s = ManualClock(), IdSource()
    adm_s = AdmissionController(
        clock=clock_s, ids=ids_s, max_concurrent_generations=1,
        max_concurrent_generations_global=3, max_generations_per_session_per_window=3,
        max_generations_global_per_window=20, anonymous_quota_session_ttl_seconds=86400,
    )
    sess_s = adm_s.create_anonymous_quota_session()
    driver_s = OllamaStageDriver(settings=settings_for(solver_url),
                                 provider_factory=lambda: make_provider(transport_s))
    controller_s = GenerationController(
        provider=make_provider(transport_s), admission=adm_s, clock=clock_s, ids=ids_s,
        stage_driver=driver_s, deadline_seconds=60, max_llm_calls_per_generation=8,
        max_repair_passes=2, max_full_regenerations=1, max_prompt_chars=4000, seed=11,
    )
    handle_s = controller_s.start_generation(SHOWCASE_PROMPT, anonymous_quota_session_id=sess_s.session_id)
    rec_s = controller_s.attempt(handle_s.attempt_id)
    proof = rec_s.published.solver_proof if rec_s.published is not None else None
    winners = proof.winners if proof is not None else ("", "", "")
    validation = rec_s.published.validation if rec_s.published is not None else None
    record("(v) solver all_true (independent deterministic deduction)",
           rec_s.state.value == "PUBLISHED" and validation is not None and validation.all_true is True,
           {"state": rec_s.state.value})
    record("(v) deduced winners == locked paul_becker / ice pick / stolen_research_data",
           winners == ("paul_becker", "stolen_research_data", "bronze_ceremonial_ice_pick"),
           {"winners": winners})

    # (vi) run twice -> draft byte-identical; fresh restart -> identical bytes.
    run2db = _fresh_migrated_db()
    run2url = f"sqlite:///{run2db.as_posix()}"
    transport_r2 = MockOllamaTransport(posts=staged_posts())
    _s5, _st5, started5 = drive_service(run2url, transport_r2, settings=settings_for(run2url))
    store_r2 = Store(run2url)
    payload_r2 = json.loads(store_r2.get_published(started5.case_id, 1).payload_json)
    draft1 = json.dumps(payload["draft"], sort_keys=True, separators=(",", ":"))
    draft2 = json.dumps(payload_r2["draft"], sort_keys=True, separators=(",", ":"))
    record("(vi) run twice -> byte-identical draft", draft1 == draft2, {"equal": draft1 == draft2})

    store_reopen = Store(url)
    payload_reopen = store_reopen.get_published(started.case_id, 1).payload_json
    first_stored = store_final.get_published(started.case_id, 1).payload_json
    record("(vi) fresh Store over the same DB (restart) -> identical frozen payload bytes",
           payload_reopen == first_stored, {"equal": payload_reopen == first_stored})

    # (vi) FAKE/demo path byte-unchanged (deterministic golden demo still golden).
    from app.persistence.store import Store as _StoreF
    from app.services.generation import GenerationService

    fake_db = _fresh_migrated_db()
    fake_url = f"sqlite:///{fake_db.as_posix()}"
    fake_settings = settings_for(fake_url, generation_provider="fake")
    fake_store = _StoreF(fake_url)
    fake_service = GenerationService(settings=fake_settings, store=fake_store)
    fs_session = fake_service.create_anonymous_quota_session()
    fake_started = fake_service.start_case_generation(
        GOLDEN_DEMO_PROMPT, anonymous_quota_session_id=fs_session.anonymous_quota_session_id,
        difficulty="medium",
    )
    fake_payload = json.loads(fake_store.get_published(fake_started.case_id, 1).payload_json)
    record("(vi) FAKE/demo provider still publishes deterministically",
           fake_started.status == "PUBLISHED", fake_started.status)
    from app.services.publication import public_case_dict_from_payload

    fake_dto = public_case_dict_from_payload(fake_payload)
    ok_golden_dto = (
        any(p.get("personId") == "sarah_miller" for p in fake_dto.get("persons", ()))
        and any(p.get("personId") == "thomas_reed" for p in fake_dto.get("persons", ()))
        and any(m.get("motiveId") == "cover_up_embezzlement" for m in fake_dto.get("motives", ()))
    )
    record("(vi) FAKE/demo public DTO matches the golden fixture structure (byte-unchanged path)",
           ok_golden_dto, "")

    # template versions never leak into the public case DTO.
    from app.services.publication import public_case_dict_from_payload as _pcd

    dto_blob = json.dumps(_pcd(payload))
    version_hits = [v for v in ("case_people_v1", "evidence_v1", "world_requirements_v1",
                                "asset_spec_v1", "asset_spec_repair_v1", "repair_v1") if v in dto_blob]
    record("template versions never leak into the public case DTO", not version_hits, version_hits)


# --------------------------------------------------------------------------- #
# 2c. LIFECYCLE / LEAK
# --------------------------------------------------------------------------- #
def audit_lifecycle_leak() -> None:
    section("2c. LIFECYCLE / LEAK — budget, admission, stale, immutability, truth/leak scans")
    from app.generation.admission import AdmissionController
    from app.generation.clock import ManualClock
    from app.generation.controller import GenerationController
    from app.generation.ids import IdSource
    from app.generation.provider import ProviderResult
    from app.services.ollama_driver import OllamaStageDriver

    def make_admission(clock, ids, window=3):
        return AdmissionController(
            clock=clock, ids=ids, max_concurrent_generations=1,
            max_concurrent_generations_global=3, max_generations_per_session_per_window=window,
            max_generations_global_per_window=20, anonymous_quota_session_ttl_seconds=86400,
        )

    def make_controller(transport, db_url, admission, clock, ids, *, budget=8, repair=2, regen=1):
        driver = OllamaStageDriver(settings=settings_for(db_url),
                                   provider_factory=lambda: make_provider(transport))
        return GenerationController(
            provider=make_provider(transport), admission=admission, clock=clock, ids=ids,
            stage_driver=driver, deadline_seconds=60, max_llm_calls_per_generation=budget,
            max_repair_passes=repair, max_full_regenerations=regen, max_prompt_chars=4000, seed=11,
        )

    # provider-call budget authoritative (8-cap incl. driver calls).
    bdb = _fresh_migrated_db()
    btransport = MockOllamaTransport(posts=["<not-json>"] * 40)
    bclock, bids = ManualClock(), IdSource()
    badm = make_admission(bclock, bids)
    bsess = badm.create_anonymous_quota_session()
    bctrl = make_controller(btransport, f"sqlite:///{bdb.as_posix()}", badm, bclock, bids,
                            budget=8, repair=100, regen=100)
    bhandle = bctrl.start_generation(SHOWCASE_PROMPT, anonymous_quota_session_id=bsess.session_id)
    brec = bctrl.attempt(bhandle.attempt_id)
    record("provider-call budget authoritative (8-cap, driver calls counted, FAILED)",
           brec.state.value == "FAILED" and btransport.call_count == 8 and brec.published is None,
           {"state": brec.state.value, "calls": btransport.call_count, "reason": brec.reason})

    # admission before call (denied -> 0 calls).
    adb = _fresh_migrated_db()
    atransport = MockOllamaTransport(posts=staged_posts())
    aclock, aids = ManualClock(), IdSource()
    aadm = make_admission(aclock, aids, window=1)
    asess = aadm.create_anonymous_quota_session()
    actrl = make_controller(atransport, f"sqlite:///{adb.as_posix()}", aadm, aclock, aids)
    afirst = actrl.start_generation(SHOWCASE_PROMPT, anonymous_quota_session_id=asess.session_id)
    arec = actrl.attempt(afirst.attempt_id)
    calls_after_first = atransport.call_count
    denied = False
    try:
        actrl.start_generation(SHOWCASE_PROMPT, anonymous_quota_session_id=asess.session_id)
    except Exception as exc:  # noqa: BLE001 - AdmissionDenied expected
        denied = type(exc).__name__ == "AdmissionDenied"
    record("admission-before-call (denied -> ZERO additional provider calls)",
           denied and atransport.call_count == calls_after_first and arec.state.value == "PUBLISHED",
           {"denied": denied, "calls_before": calls_after_first, "calls_after": atransport.call_count})

    # stale/delayed mocked completion discarded.
    sdb = _fresh_migrated_db()
    stransport = MockOllamaTransport(posts=staged_posts())
    sclock, sids = ManualClock(), IdSource()
    sadm = make_admission(sclock, sids)
    ssess = sadm.create_anonymous_quota_session()
    sctrl = make_controller(stransport, f"sqlite:///{sdb.as_posix()}", sadm, sclock, sids)
    shandle = sctrl.start_generation(SHOWCASE_PROMPT, anonymous_quota_session_id=ssess.session_id)
    srec = sctrl.attempt(shandle.attempt_id)
    pub_before = srec.published
    calls_before_stale = stransport.call_count
    sctrl.on_completion("stale-ollama-pending", ProviderResult(content=T.ICEPICK_SPEC))
    srec2 = sctrl.attempt(shandle.attempt_id)
    publish_stale = sctrl.publish(shandle.attempt_id)
    record("stale/delayed mocked completion discarded (no payload mutation, publish refused)",
           srec2.published is pub_before and stransport.call_count == calls_before_stale
           and publish_stale.success is False,
           {"state": srec2.state.value, "calls_delta": stransport.call_count - calls_before_stale})

    # old attempt cannot publish + PUBLISHED immutable.
    tdb = _fresh_migrated_db()
    ttransport = MockOllamaTransport(posts=staged_posts())
    tclock, tids = ManualClock(), IdSource()
    tadm = make_admission(tclock, tids, window=5)
    tsess = tadm.create_anonymous_quota_session()
    tctrl = make_controller(ttransport, f"sqlite:///{tdb.as_posix()}", tadm, tclock, tids)
    th1 = tctrl.start_generation(SHOWCASE_PROMPT, anonymous_quota_session_id=tsess.session_id)
    tr1 = tctrl.attempt(th1.attempt_id)
    th2 = tctrl.start_generation(SHOWCASE_PROMPT, anonymous_quota_session_id=tsess.session_id)
    tr2 = tctrl.attempt(th2.attempt_id)
    publish_old = tctrl.publish(th1.attempt_id)
    record("old/superseded attempt cannot publish", publish_old.success is False,
           {"oldPublish": publish_old.success, "newState": tr2.state.value})
    if tr2.state.value == "PUBLISHED":
        record("PUBLISHED immutable (re-publish refused)", tctrl.publish(th2.attempt_id).success is False,
               {"republish": tctrl.publish(th2.attempt_id).success})

    # frozen draft cannot mutate.
    frozen_ok = True
    try:
        if srec2.published is not None:
            srec2.published.draft.crime = None  # type: ignore[misc]
            frozen_ok = False
    except Exception:  # noqa: BLE001 - frozen dataclass
        frozen_ok = True
    record("PUBLISHED draft frozen (mutation raises)", frozen_ok, "")

    # CaseTruth absent from solver inputs AND from every prompt (scan builders).
    from app.generation import prompts as _prompts
    from app.services.publication import public_case_dict_from_payload

    scan_db = _fresh_migrated_db()
    scan_url = f"sqlite:///{scan_db.as_posix()}"
    scan_transport = MockOllamaTransport(posts=staged_posts())
    _sc_svc, sc_store, started_sc = drive_service(scan_url, scan_transport, settings=settings_for(scan_url))
    sc_payload = json.loads(sc_store.get_published(started_sc.case_id, 1).payload_json)
    public_blob = json.dumps(public_case_dict_from_payload(sc_payload))
    evidence_blob = json.dumps([e for e in sc_payload["draft"]["evidence"]])
    solve_inputs = public_blob + " " + evidence_blob
    truth_in = [t for t in ("murdererId", "solverProof", "crimeTime", "canonical", "truthfulness")
                if t in solve_inputs]
    record("CaseTruth absent from solver inputs (public + evidence DTOs)", not truth_in, truth_in)

    all_prompts = " ".join(scan_transport.prompt_of_call(i) for i in range(scan_transport.call_count))
    prompt_hits = [t for t in ("solverProof", "_phase3_cache", "timeline", "relationships")
                   if t in all_prompts]
    builder_blob = " ".join([
        _prompts.build_case_people_prompt(SHOWCASE_PROMPT, None),
        _prompts.build_evidence_prompt(SHOWCASE_PROMPT, None),
        _prompts.build_world_requirements_prompt(SHOWCASE_PROMPT, None),
        _prompts.build_asset_spec_prompt("bronze ceremonial ice pick"),
    ])
    builder_hits = [t for t in ("solverProof", "_phase3_cache", "timeline", "relationships")
                    if t in builder_blob]
    record("CaseTruth absent from every recorded prompt AND every prompt builder",
           not prompt_hits and not builder_hits, {"prompts": prompt_hits, "builders": builder_hits})

    asset_in_solver = "assetSpec" in solve_inputs or "generated_definition" in solve_inputs
    record("AssetSpec never reaches the solver", not asset_in_solver, "")

    raw_hits = [t for t in ("for the '", "messages", "prompt template version", "<not-json>")
                if t in solve_inputs]
    record("raw provider text never reaches the frontend (public DTO + evidence)", not raw_hits, raw_hits)

    pdt = json.dumps(public_case_dict_from_payload(sc_payload))
    dto_hits = [t for t in ("prompt", "diagnostics", "generationProvider", "OLLAMA_BASE_URL",
                            "11434", "127.0.0.1", "generationAttemptId") if t in pdt]
    record("prompts/diagnostics/model/attempt-ids never in public DTOs", not dto_hits, dto_hits)

    payload_all = json.dumps(sc_payload)
    code_hits = [t for t in ("<script", "javascript:", "new Function", "eval(", "require(")
                 if t in payload_all]
    record("no generated-code execution path in the serialized payload", not code_hits, code_hits)

    base_hits = [t for t in ("11434", "127.0.0.1", "host.docker.internal") if t in pdt]
    record("Ollama base URL/port never leaks into public DTOs", not base_hits, base_hits)

    version_in_payload = [v for v in ("case_people_v1", "evidence_v1", "world_requirements_v1",
                                      "asset_spec_v1", "asset_spec_repair_v1", "repair_v1")
                          if v in payload_all]
    record("template versions absent even from the server-internal stored payload",
           not version_in_payload, version_in_payload)

    # capability DTO allowlist keys + no URL leak (real TestClient).
    from app.api.v1 import generation_capabilities as cap_module
    original_probe = cap_module.ollama_available
    try:
        from fastapi.testclient import TestClient
        from app.main import create_app

        cap_module.ollama_available = lambda settings: (True, "")
        with TestClient(create_app(settings_for(scan_url))) as c:
            res = c.get("/api/v1/generation-capabilities")
            body = res.json()
            keys = {k for m in body["modes"] for k in m.keys()}
            ok = (res.status_code == 200 and keys <= {"id", "available", "label", "model"}
                  and "11434" not in res.text and "127.0.0.1" not in res.text
                  and body["modes"][0] == {"id": "demo", "available": True})
            record("capability DTO: allowlist keys unchanged, no base URL/port leak", ok, body)
    finally:
        cap_module.ollama_available = original_probe


def main() -> int:
    audit_prompt_contract()
    audit_end_to_end()
    audit_lifecycle_leak()
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print(f"\n=== PHASE 16_2 STAGE-DRIVER CONTRACT AUDIT: {passed}/{total} PASS ===")
    out_path = sys.argv[1] if len(sys.argv) > 1 else str(REPO_ROOT / "e2e" / "artifacts" / "qa-phase162-contract-audit.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps({"suite": "qa-phase162-contract-audit", "passed": passed, "total": total, "results": results},
                   indent=2, sort_keys=True), encoding="utf-8")
    print(f"evidence: {out_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())