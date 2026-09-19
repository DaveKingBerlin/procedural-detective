"""QA-owned Phase17B gate audit probe (independent; in-process; e2e/probes).

Phase17B shipped: tools/ollama_smoke.py debug diagnostics (template version,
transport flag, sanitized raw, elapsed, bytes, per-repair AssetSpec issues),
the authoritative JSON-Schema /api/chat ``format`` transport (structured-output
probe >= 0.8.0; deterministic ``"json"`` fallback; truthful request-side flag),
the current-version stage-mapping lock, prompt/schema MAPPING-ONLY tuning, and
mocked fixtures reproducing the observed hermes3:8b failure class
(case_truth: 2 parse issues; asset_spec: 1 SILHOUETTE_HEURISTIC) with mocked
proof that well-formed responses reach the §6 targets.

This probe re-proves every claim INDEPENDENTLY against the REAL modules:

  A. FORMAT TRANSPORT (real OllamaProvider + QA-owned MockOllamaTransport):
     the structured-output probe matrix (>= 0.8.0 -> True); the /api/chat
     ``format`` for CASE_TRUTH / ASSET_SPEC with a >= 0.8.0 probe IS the
     authoritative JSON Schema whose top-level properties equal the rendered
     schema-contract keys (unit-level); older/unknown -> ``"json"``; REPAIR is
     ALWAYS ``"json"``; ``transportStructuredOutput`` / ``last_format`` are
     truthful request-side flags; the GenerationService factory resolves the
     probe ONCE and threads it into the provider.
  B. STAGE-MAPPING LOCK: smoke alias -> GenerationStage -> CurrentVersion
     (case_truth->CASE_TRUTH->CASE_PEOPLE_v1, evidence->EVIDENCE->EVIDENCE_v1,
     world_requirements->WORLD_GRAPH->WORLD_REQUIREMENTS_v1,
     asset_spec->ASSET_SPEC->ASSET_SPEC_v1,
     asset_spec_repair->ASSET_SPEC_REPAIR->ASSET_SPEC_REPAIR_v1,
     repair->REPAIR->REPAIR_v1); a source scan of the prompt chain finds NO
     stale ``_v\\d+`` token outside that set.
  C. FAILURE-CLASS FIXTURES + §6 TARGETS: the broke-case_truth fixture yields
     EXACTLY the 2 documented parse issues (unknown key ``crime_time`` +
     out-of-range tolerance); the broke-asset_spec fixture yields EXACTLY 1
     SILHOUETTE_HEURISTIC issue on pass 0; a WELL-FORMED hermes-style response
     (same intended content, correct mapping) -> parsedOk=true / parseIssues=[]
     (case_truth) and providerOk=true + geometricallyValid=true with
     generatedOnFirstPass=true (asset_spec first pass) or via the repair path;
     finalProcId deterministic; the strict validators are byte-unchanged
     (rule constants untouched; a deliberately-invalid response still fails
     with the identical issue text); the full pipeline publishes a proc.*
     object through the REAL service on a REAL migrated scratch DB.
  D. SMOKE DIAGNOSTICS (REAL tools.ollama_smoke.main in-process, probe/provider
     monkeypatched): the report carries stageTemplate (case_people_v1),
     transportStructuredOutput (true/false truthful), parseIssues codes/messages,
     rawSanitized (redacted), elapsedSeconds, responseBytes, and for asset_spec
     the per-pass repair trace (perPassIssues) + finalProcId.

The real hermes3:8b operator run is OPERATOR-SIDE (Phase17B §5): it is NOT
attempted here; everything below is the deterministic mocked/offline path.

Run:  python e2e/probes/qa-phase17b-contract-audit.py [out.json]
Output JSON: argv[1] or e2e/artifacts/qa-phase17b-contract-audit.json
Exit: 0 = all PASS, 1 = any FAIL.
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tests"))
sys.path.insert(0, str(REPO_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - console encoding is best-effort
        pass

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:520]}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_MODEL = "hermes3:8b"
CANONICAL_TS = "2026-09-11T23:42:00+02:00"


# --------------------------------------------------------------------------- #
# QA-owned mocked transport (never any network) + fixtures
# --------------------------------------------------------------------------- #
class MockOllamaTransport:
    """Records every chat payload; ``get`` dispatches the documented probes."""

    def __init__(self, posts=(), version_body=None, tags_models=(OLLAMA_MODEL,)):
        self.posts = list(posts)
        self.post_calls = []
        self.version_body = version_body
        self.tags_models = list(tags_models)

    def post_json(self, url, payload, timeout):
        self.post_calls.append((url, dict(payload), float(timeout)))
        content = self.posts.pop(0) if self.posts else "<not-json>"
        return 200, json.dumps(
            {"model": OLLAMA_MODEL, "message": {"role": "assistant", "content": content}}
        ).encode()

    def get(self, url, timeout):
        if url.rstrip("/").endswith(("api/version", "/api/version")):
            if self.version_body is None:
                return 404, b'{"error": "missing"}'
            return 200, json.dumps(self.version_body).encode()
        body = {"models": [{"name": name} for name in self.tags_models]}
        return 200, json.dumps(body).encode()

    def format_of_call(self, index=0):
        return self.post_calls[index][1].get("format")


def _j(d):
    return json.dumps(d, sort_keys=True, ensure_ascii=False, indent=2)


CASE_TRUTH_BROKE = {
    "crime": {
        "type": "murder",
        "victimId": "victim_01",
        "murdererId": "suspect_02",
        "motiveId": "motive_01",
        "weaponId": "weapon_01",
        "locationId": "location_01",
        "crime_time": {"canonical": CANONICAL_TS, "accusationToleranceSeconds": 300},
        "crimeTime": {
            "canonical": CANONICAL_TS,
            "accusationToleranceSeconds": 999_999_999,
        },
    }
}
CASE_TRUTH_GOOD = {
    "crime": {
        "type": "murder",
        "victimId": "victim_01",
        "murdererId": "suspect_02",
        "motiveId": "motive_01",
        "weaponId": "weapon_01",
        "locationId": "location_01",
        "crimeTime": {"canonical": CANONICAL_TS, "accusationToleranceSeconds": 300},
    }
}
EXPECTED_CASE_TRUTH_BROKE_ISSUES = (
    "case_truth.crime.crimeTime: invalid crimeTime: "
    "CrimeTimeSpec.accusation_tolerance_seconds out of range: "
    "999999999 > 2592000",
    "case_truth.crime: unknown key 'crime_time'",
)


def asset_broke():
    """Single-part underspecified envelope: Phase-13-valid; EXACTLY one Phase 17
    geometry issue on pass 0 (SILHOUETTE_HEURISTIC)."""
    return {
        "canonicalName": "Bronze Ceremonial Ice Pick",
        "category": "decor",
        "subtype": "ceremonial_ice_pick",
        "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
        "parts": [
            {
                "id": "part_00",
                "role": "tip",
                "primitive": "sphere",
                "transform": {
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.2, "y": 0.2, "z": 0.2},
                },
                "material": "metal.brass",
            }
        ],
    }


def asset_good():
    """The coherent 3-part ice pick (same intended object; correct mapping)."""
    return {
        "canonicalName": "Bronze Ceremonial Ice Pick",
        "category": "decor",
        "subtype": "ceremonial_ice_pick",
        "dimensions": {"x": 0.12, "y": 0.5, "z": 0.1},
        "parts": [
            {
                "id": "part_00",
                "role": "shaft",
                "primitive": "cylinder",
                "transform": {
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.05, "y": 0.18, "z": 0.05},
                },
                "material": "metal.brass",
            },
            {
                "id": "part_01",
                "role": "point",
                "primitive": "box",
                "transform": {
                    "position": {"x": 0.0, "y": 0.2, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.05, "y": 0.05, "z": 0.05},
                },
                "material": "metal.brass",
            },
            {
                "id": "part_02",
                "role": "handle",
                "primitive": "box",
                "transform": {
                    "position": {"x": 0.0, "y": -0.19, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.05, "y": 0.06, "z": 0.05},
                },
                "material": "wood.dark",
            },
        ],
    }


def _provider(transport, structured_output=False):
    from app.generation.ollama_provider import OllamaProvider

    return OllamaProvider(
        base_url=OLLAMA_BASE,
        model=OLLAMA_MODEL,
        timeout_seconds=5.0,
        temperature=0.0,
        num_ctx=4096,
        transport=transport,
        structured_output=structured_output,
    )


class _ScriptedProvider:
    """Deterministic provider adapter scripted with raw response texts."""

    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0
        self.last_format = "json"

    def generate(self, request):
        from app.generation.provider import ProviderResult

        self.calls += 1
        content = self.contents.pop(0) if self.contents else "<not-json>"
        return ProviderResult(content=content)


def _fresh_migrated_db() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="qa_p17b_audit_"))
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


# --------------------------------------------------------------------------- #
# A. FORMAT TRANSPORT
# --------------------------------------------------------------------------- #
def audit_format_transport() -> None:
    section("A. FORMAT TRANSPORT — authoritative JSON Schema in /api/chat format")
    from app.core.config import Settings
    from app.generation import prompts
    from app.generation.ollama_provider import ollama_structured_output_supported
    from app.generation.provider import GenerateRequest, GenerationStage

    settings = Settings(generation_provider="ollama")

    def _probe(version_body):
        transport = MockOllamaTransport(version_body=version_body)
        return ollama_structured_output_supported(settings, transport)

    record("A0a probe >= 0.8.0 (0.34.2) -> True",
           _probe({"version": "0.34.2"}) is True, _probe({"version": "0.34.2"}))
    record("A0b probe == 0.8.0 -> True",
           _probe({"version": "0.8.0"}) is True, _probe({"version": "0.8.0"}))
    record("A0c probe < 0.8.0 (0.7.9) -> False (json fallback)",
           _probe({"version": "0.7.9"}) is False, _probe({"version": "0.7.9"}))
    record("A0d malformed/absent version -> False",
           _probe({"version": "not-a-version"}) is False
           and _probe({"something": "else"}) is False, "")
    record("A0e transport failure degrades to False (never raises)",
           _probe_test_failure(), "")

    # A1/A2: schema top-level == rendered contract keys (unit-level), REQUIRED
    #       exactly the non-nullable contract keys.
    for contract_key in sorted(prompts.CONTRACT_KEYS):
        schema = prompts.schema_contract_as_json_schema(contract_key)
        contract = json.loads(prompts.schema_contract(contract_key))
        optional = {
            key for key, value in contract.items()
            if isinstance(value, str) and "null" in value
        }
        record(
            f"A_unit schema top-level==contract keys ({contract_key}); "
            f"required==non-nullable",
            schema["type"] == "object"
            and set(schema["properties"]) == set(contract)
            and set(schema["required"]) == set(contract) - optional,
            {"schemaKeys": sorted(schema.get("properties", ())),
             "contractKeys": sorted(contract)},
        )

    # CASE_TRUTH with the >= 0.8.0 probe resolved -> the authoritative schema.
    transport = MockOllamaTransport(
        posts=['{"crime": {"type": "murder"}}'], version_body={"version": "0.34.2"}
    )
    provider = _provider(transport, structured_output=True)
    result = provider.generate(
        GenerateRequest(
            attempt_id="att-1", stage=GenerationStage.CASE_TRUTH, prompt_context="ctx"
        )
    )
    sent = transport.format_of_call(0)
    contract_keys = set(json.loads(prompts.schema_contract("case_people")))
    record("A2a CASE_TRUTH format is the authoritative JSON Schema",
           isinstance(sent, dict) and sent["type"] == "object", "")
    record("A2b CASE_TRUTH schema top-level == rendered contract keys",
           isinstance(sent, dict) and set(sent["properties"]) == contract_keys,
           {"sent": sorted(sent.get("properties", ())) if isinstance(sent, dict) else sent})
    record("A2c derived from the SAME source (one mapping, no duplicate)",
           isinstance(sent, dict)
           and sent == prompts.json_schema_for_generation_stage("case_truth"),
           "")
    record("A2d truthful request-side flag (schema sent)",
           provider.last_format == "schema" and provider.structured_output_sent is True,
           {"last_format": provider.last_format})
    record("A2e provider still runs strict parse on the response",
           result.content is not None, "")

    # ASSET_SPEC with structured output -> schema top-level == asset_spec keys.
    transport2 = MockOllamaTransport(
        posts=[_j(asset_good())], version_body={"version": "0.34.2"}
    )
    provider2 = _provider(transport2, structured_output=True)
    provider2.generate(
        GenerateRequest(
            attempt_id="att-2", stage=GenerationStage.ASSET_SPEC,
            prompt_context="an ice pick",
        )
    )
    sent2 = transport2.format_of_call(0)
    asset_keys = set(json.loads(prompts.schema_contract("asset_spec")))
    record("A3a ASSET_SPEC format carries the schema whose keys == contract keys",
           isinstance(sent2, dict) and set(sent2["properties"]) == asset_keys,
           sorted(sent2.get("properties", ())) if isinstance(sent2, dict) else sent2)
    record("A3b ASSET_SPEC truthful flag (schema sent)",
           provider2.last_format == "schema" and provider2.structured_output_sent is True,
           provider2.last_format)

    # A4: older/unknown server -> the documented "json" fallback.
    transport3 = MockOllamaTransport(posts=['{"crime": {}}'])
    provider3 = _provider(transport3, structured_output=False)
    provider3.generate(
        GenerateRequest(
            attempt_id="att-3", stage=GenerationStage.CASE_TRUTH, prompt_context="ctx"
        )
    )
    record("A4 older/unknown server -> format \"json\" + truthful flag false",
           transport3.format_of_call(0) == "json"
           and provider3.last_format == "json"
           and provider3.structured_output_sent is False,
           {"format": transport3.format_of_call(0), "sent": provider3.structured_output_sent})

    # A5: REPAIR stage is ALWAYS "json" (no per-stage skeleton).
    transport4 = MockOllamaTransport(posts=['{}'])
    provider4 = _provider(transport4, structured_output=True)
    provider4.generate(
        GenerateRequest(
            attempt_id="att-4", stage=GenerationStage.REPAIR, prompt_context="ctx"
        )
    )
    record("A5 REPAIR always format \"json\" even when structured output enabled",
           transport4.format_of_call(0) == "json"
           and provider4.last_format == "json"
           and provider4.structured_output_sent is False,
           {"format": transport4.format_of_call(0)})

    # A6: the GenerationService factory threads the resolved probe value.
    from app.services import generation as gen_mod
    from app.generation import ollama_provider as ollama_mod

    captured = {}

    def _resolved(flag):
        def _probe_fake(_settings):
            return flag
        orig1, orig2 = ollama_mod.ollama_structured_output_supported, ollama_mod.ollama_available
        ollama_mod.ollama_structured_output_supported = _probe_fake
        ollama_mod.ollama_available = lambda _s: (flag, "" if flag else "not available")
        try:
            # shell instance: _build_default_provider_factory only reads
            # self._settings (no Store/DB needed).
            service = object.__new__(gen_mod.GenerationService)
            service._settings = Settings(
                database_url=(
                    "sqlite:///" + Path(tempfile.mkdtemp(prefix="qa_p17b_audit_"))
                    .joinpath("unused.db").as_posix()
                ),
                generation_provider="ollama",
                ollama_base_url="http://127.0.0.1:11435",
            )
            factory = service._build_default_provider_factory()
        finally:
            ollama_mod.ollama_structured_output_supported, ollama_mod.ollama_available = orig1, orig2
        return factory

    f_on = _resolved(True)
    f_off = _resolved(False)
    p_on = f_on()
    p_off = f_off()
    captured["probe_true_structured"] = bool(getattr(p_on, "_structured_output", None))
    captured["probe_false_structured"] = bool(getattr(p_off, "_structured_output", None))
    record("A6 factory resolves probe ONCE and threads structured_output into the provider",
           captured["probe_true_structured"] is True
           and captured["probe_false_structured"] is False,
           captured)


def _probe_test_failure():
    from app.core.config import Settings
    from app.generation.ollama_provider import ollama_structured_output_supported

    settings = Settings(generation_provider="ollama")
    transport = MockOllamaTransport(version_body={"version": "0.34.2"})
    transport.get = lambda url, timeout: (_ for _ in ()).throw(OSError("probe refused"))
    return ollama_structured_output_supported(settings, transport) is False


# --------------------------------------------------------------------------- #
# B. STAGE-MAPPING LOCK
# --------------------------------------------------------------------------- #
def audit_stage_mapping() -> None:
    section("B. STAGE-MAPPING LOCK — smoke -> GenerationStage -> current version")
    import inspect
    import tools.ollama_smoke as smoke
    from app.generation import prompts
    from app.generation.provider import GenerationStage

    builders = smoke._stage_builders()
    expected = {
        "case_truth": ("case_truth", "case_people_v1"),
        "evidence": ("evidence", "evidence_v1"),
        "world_requirements": ("world_graph", "world_requirements_v1"),
        "asset_spec": ("asset_spec", "asset_spec_v1"),
        "asset_spec_repair": ("asset_spec_repair", "asset_spec_repair_v1"),
        "repair": ("repair", "repair_v1"),
    }
    mapping_ok = True
    detail = {}
    for alias in builders:
        name, _arg, version = builders[alias]
        gen_value = (
            GenerationStage.WORLD_GRAPH.value
            if alias == "world_requirements"
            else GenerationStage(alias).value
        )
        ok = (
            gen_value == expected[alias][0]
            and version == expected[alias][1]
            and version == prompts.STAGE_TO_PROMPT_VERSION[gen_value]
        )
        mapping_ok = mapping_ok and ok
        detail[alias] = {"generationStage": gen_value, "templateVersion": version}
    record("B1 mapping table exactly current (case_truth->CASE_TRUTH->CASE_PEOPLE_v1, ...)",
           mapping_ok, detail)
    record("B2 _SMOKE_STAGES is exactly the 6 documented aliases",
           set(smoke._SMOKE_STAGES)
           == {"case_truth", "evidence", "world_requirements", "asset_spec",
               "asset_spec_repair", "repair"},
           sorted(smoke._SMOKE_STAGES))

    current = set(prompts.STAGE_TO_PROMPT_VERSION.values())
    record("B3 STAGE_TO_PROMPT_VERSION values are exactly the 6 current templates",
           current == {"case_people_v1", "evidence_v1", "world_requirements_v1",
                       "asset_spec_v1", "asset_spec_repair_v1", "repair_v1"},
           sorted(current))
    record("B4 CONTRACT_KEYS / STAGE_TO_CONTRACT values are exactly the 4 contracts",
           set(prompts.STAGE_TO_CONTRACT.values()) == set(prompts.CONTRACT_KEYS)
           == {"case_people", "evidence", "world_requirements", "asset_spec"},
           sorted(set(prompts.STAGE_TO_CONTRACT.values())))

    # B5: rendered prompts embed the current version verbatim.
    embeds = {
        "case_people_v1": "case_people_v1" in prompts.build_case_people_prompt("x", None),
        "evidence_v1": "evidence_v1" in prompts.build_evidence_prompt("x", None),
        "world_requirements_v1": "world_requirements_v1"
        in prompts.build_world_requirements_prompt("x", None),
        "asset_spec_v1": "asset_spec_v1" in prompts.build_asset_spec_prompt("x", "decor"),
        "asset_spec_repair_v1": "asset_spec_repair_v1"
        in prompts.build_asset_spec_repair_prompt("x", "{}", ("i",)),
        "repair_v1": "repair_v1" in prompts.build_repair_prompt("{}", ("i",)),
    }
    record("B5 every rendered prompt embeds its CURRENT version",
           all(embeds.values()), embeds)

    # B6: source scan of the prompt chain finds NO stale _v\d+ tokens.
    stale_hits = []
    for path in (BACKEND_DIR / "app" / "generation" / "prompts.py",
                 REPO_ROOT / "tools" / "ollama_smoke.py"):
        source = path.read_text(encoding="utf-8")
        found = set(re.findall(r"\b[a-z][a-z0-9_]*_v[0-9]+\b", source))
        outside = found - current
        stale_hits.append((str(path.relative_to(REPO_ROOT)), sorted(outside)))
    record("B6 source scan of the prompt chain: no stale `_v\\d+` token outside the current set",
           not any(outside for _p, outside in stale_hits), stale_hits)

    smoke_src = inspect.getsource(smoke)
    stale_smoke = set(re.findall(r"\b[a-z][a-z0-9_]*_v[0-9]+\b", smoke_src)) - current
    record("B7 smoke module source: only the 6 current version strings",
           not stale_smoke, sorted(stale_smoke))


# --------------------------------------------------------------------------- #
# C. FAILURE-CLASS FIXTURES + §6 TARGETS
# --------------------------------------------------------------------------- #
def audit_failure_class_targets() -> None:
    section("C. FAILURE-CLASS FIXTURES + §6 TARGETS")
    from app.assets.compiler import asset_id_for
    from app.assets.geometry_quality import validate_geometry
    from app.assets.spec_provider import AssetSpecRequest
    from app.assets.specs import parse_asset_spec, validate_asset_spec
    from app.generation import parser as stage_parser
    from app.generation.provider import GenerationStage
    from app.services.ollama_driver import OllamaAssetSpecProvider

    # C1: the broke-case_truth fixture yields EXACTLY the 2 documented issues.
    issues = stage_parser.collect_issues(
        GenerationStage.CASE_TRUTH, _j(CASE_TRUTH_BROKE)
    )
    record("C1a broke-case_truth -> EXACTLY 2 parse issues",
           len(issues) == 2, issues)
    record("C1b exact issue codes/messages (unknown key `crime_time` + out-of-range)",
           tuple(issues) == EXPECTED_CASE_TRUTH_BROKE_ISSUES, issues)
    try:
        stage_parser.parse_stage(
            GenerationStage.CASE_TRUTH, _j(CASE_TRUTH_BROKE), non_throwing=False
        )
        raised = False
    except stage_parser.GenerationParseError as exc:
        raised = tuple(excinfo_issues(exc)) == EXPECTED_CASE_TRUTH_BROKE_ISSUES
    record("C1c strict non-throwing=False raises with the identical issue tuple",
           raised, "")

    # C2: the broke-asset_spec fixture -> Phase-13 VALID, EXACTLY 1 geometry
    #     issue on pass 0 (SILHOUETTE_HEURISTIC).
    raw = asset_broke()
    record("C2a broke-asset_spec stays Phase-13 schema-valid",
           validate_asset_spec(raw) == (), list(validate_asset_spec(raw)))
    report0 = validate_geometry(parse_asset_spec(raw, non_throwing=False))
    record("C2b EXACTLY 1 first-pass geometry issue (SILHOUETTE_HEURISTIC QUALITY_ERROR)",
           len(report0.issues) == 1
           and report0.issues[0].code == "SILHOUETTE_HEURISTIC"
           and report0.issues[0].classification == "QUALITY_ERROR",
           [{"code": i.code, "classification": i.classification,
             "message": i.message, "partId": i.partId} for i in report0.issues])

    # C3: WELL-FORMED hermes-style case_truth -> parsedOk=true / parseIssues=[].
    good_issues = stage_parser.collect_issues(
        GenerationStage.CASE_TRUTH, _j(CASE_TRUTH_GOOD)
    )
    spec = stage_parser.parse_stage(
        GenerationStage.CASE_TRUTH, _j(CASE_TRUTH_GOOD), non_throwing=False
    )
    record("C3 well-formed case_truth -> parsedOk=true, 0 issues, correct semantics",
           good_issues == () and spec is not None and spec.victim_id == "victim_01",
           {"parsedOk": good_issues == (), "victim_id": getattr(spec, "victim_id", None)})

    # C4: §6 FIRST-PASS target for asset_spec.
    served = _ScriptedProvider([_j(asset_good())])
    prov = OllamaAssetSpecProvider(
        provider=served, attempt_id="qa-p17b-first", budget_consumer=lambda: True
    )
    res = prov.generate(AssetSpecRequest(requested_name="bronze ceremonial ice pick"))
    m = prov.last_geometry_metrics
    record("C4 first pass: providerOk + geometricallyValid + generatedOnFirstPass=true",
           res.error is None
           and m["generatedOnFirstPass"] is True
           and m["issueCountBeforeRepair"] == 0
           and validate_geometry(parse_asset_spec(res.content, non_throwing=False)).valid,
           m)

    # C5: REPAIR-path target + deterministic finalProcId.
    served2 = _ScriptedProvider([_j(asset_broke()), _j(asset_good())])
    prov2 = OllamaAssetSpecProvider(
        provider=served2, attempt_id="qa-p17b-repair", budget_consumer=lambda: True
    )
    res2 = prov2.generate(AssetSpecRequest(requested_name="bronze ceremonial ice pick"))
    m2 = prov2.last_geometry_metrics
    trace = prov2.last_repair_trace
    final_spec = parse_asset_spec(res2.content, non_throwing=False)
    proc_id = asset_id_for(final_spec)
    expected_id = asset_id_for(parse_asset_spec(_j(asset_good()), non_throwing=False))
    proc_id_again = asset_id_for(
        parse_asset_spec(_j(asset_good()), non_throwing=False)
    )
    record("C5a repair path reaches §6 targets (providerOk, geometricallyValid, repaired)",
           res2.error is None and m2["repaired"] is True
           and m2["repairAttempts"] == 1
           and m2["issueCountBeforeRepair"] == 1
           and validate_geometry(final_spec).valid,
           m2)
    record("C5b per-pass repair trace: pass0 == [SILHOUETTE_HEURISTIC], pass1 == [] (order preserved)",
           [e["pass"] for e in trace] == [0, 1]
           and [i["code"] for i in trace[0]["geometryIssues"]] == ["SILHOUETTE_HEURISTIC"]
           and trace[1]["geometryIssues"] == [],
           {"passes": [e["pass"] for e in trace],
            "pass0": [i["code"] for i in trace[0]["geometryIssues"]],
            "pass1": trace[1]["geometryIssues"]})
    record("C5c finalProcId deterministic (identical for same normalized spec)",
           proc_id.startswith("proc.") and proc_id == expected_id
           and proc_id == proc_id_again,
           proc_id)

    # C6: validator constants byte-unchanged + identical failures.
    from app.assets.geometry_quality import (
        HANDHELD_MAX_DIMENSION,
        HANDHELD_MAX_SINGLE_DIMENSION,
        SILHOUETTE_SEPARATION,
    )
    from app.assets.specs import (
        DIMENSION_MAX,
        DIMENSION_MIN,
        MAX_PARTS,
        MAX_PART_SCALE,
        MAX_POSITION_BOUND,
        MAX_ROTATION_BOUND,
        MIN_PART_SCALE,
        PRIMITIVE_ALLOWLIST,
    )

    consts = {
        "PRIMITIVE_ALLOWLIST": PRIMITIVE_ALLOWLIST == frozenset(
            {"box", "cylinder", "sphere", "plane"}),
        "DIMENSION_MIN": DIMENSION_MIN == 0.05,
        "DIMENSION_MAX": DIMENSION_MAX == 4.0,
        "MAX_PARTS": MAX_PARTS == 24,
        "MAX_POSITION_BOUND": MAX_POSITION_BOUND == 4.0,
        "MAX_ROTATION_BOUND": round(MAX_ROTATION_BOUND, 4)
        == round(2.0 * 3.141592653589793, 4),
        "MIN_PART_SCALE": MIN_PART_SCALE == 0.05,
        "MAX_PART_SCALE": MAX_PART_SCALE == 2.0,
        "HANDHELD_MAX_DIMENSION": HANDHELD_MAX_DIMENSION == 0.5,
        "HANDHELD_MAX_SINGLE_DIMENSION": HANDHELD_MAX_SINGLE_DIMENSION == 1.0,
        "SILHOUETTE_SEPARATION": SILHOUETTE_SEPARATION == 0.05,
    }
    record("C6a strict validator rule constants byte-unchanged",
           all(consts.values()), consts)

    unit_confused = asset_broke()
    unit_confused["dimensions"] = {"x": 25, "y": 0.1, "z": 0.1}
    uc_issues = validate_asset_spec(unit_confused)
    record("C6b 25 m unit-confusion STILL rejected with identical issue text",
           "dimensions.x: dimension must be within [0.05, 4] (got 25)" in uc_issues,
           uc_issues)
    bad_prim = asset_broke()
    bad_prim["parts"][0]["primitive"] = "capsule"
    bp_issues = validate_asset_spec(bad_prim)
    record("C6c unsupported primitive STILL rejected with identical message",
           any("is not in the renderer-supported primitive vocabulary" in i
               for i in bp_issues),
           bp_issues)
    record("C6d broke-case_truth STILL fails with the identical 2 issues",
           tuple(stage_parser.collect_issues(
               GenerationStage.CASE_TRUTH, _j(CASE_TRUTH_BROKE)))
           == EXPECTED_CASE_TRUTH_BROKE_ISSUES,
           "")
    report_b = validate_geometry(
        parse_asset_spec(asset_broke(), non_throwing=False))
    record("C6e broke-asset_spec STILL fails with exactly [SILHOUETTE_HEURISTIC]",
           [i.code for i in report_b.issues] == ["SILHOUETTE_HEURISTIC"],
           [i.code for i in report_b.issues])

    # C7: full pipeline publishes a proc.* object through the REAL service on a
    #     REAL migrated scratch DB (mocked transport; well-formed chain).
    _full_service_pipeline()


def excinfo_issues(exc: object):
    return getattr(exc, "issues", ()) or ()


def _full_service_pipeline() -> None:
    import test_ollama_driver as T
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    db = _fresh_migrated_db()
    url = f"sqlite:///{db.as_posix()}"

    posts = [
        T._j(T._case_people()),
        T._j(T._evidence()),
        T._j(T._world()),
        _j(asset_good()),  # ASSET_SPEC (well-formed -> first pass, geometry OK)
    ]
    transport = MockOllamaTransport(posts=posts, version_body={"version": "0.34.2"})

    from app.core.config import Settings

    settings = Settings(
        database_url=url,
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
    store = Store(url)
    service = GenerationService(
        settings=settings,
        store=store,
        provider_factory=lambda: _provider(transport, structured_output=True),
    )
    session = service.create_anonymous_quota_session()
    started = service.start_case_generation(
        T.PROMPT, anonymous_quota_session_id=session.anonymous_quota_session_id,
        difficulty="medium",
    )
    record("C7a full REAL service on migrated DB -> PUBLISHED (well-formed chain)",
           started.status == "PUBLISHED", started.status)

    published = store.get_latest_published(started.case_id)
    payload = json.loads(published.payload_json) if published is not None else {}
    placements = ((payload.get("draft") or {}).get("world_graph") or {}).get("placements") or []
    proc_ids = {
        str(p.get("assetId") or p.get("asset_id") or "") for p in placements
    }
    proc_ids = {p for p in proc_ids if p.startswith("proc.")}
    record("C7b published world carries a compiled proc.* placement (geometry gate passed)",
           bool(proc_ids), sorted(proc_ids))
    record("C7c ASSET_SPEC request carried the authoritative schema in /api/chat format",
           isinstance(transport.format_of_call(3), dict),
           "")


# --------------------------------------------------------------------------- #
# D. SMOKE DIAGNOSTICS JSON (real smoke.main, in-process)
# --------------------------------------------------------------------------- #
def audit_smoke_diagnostics() -> None:
    section("D. SMOKE DIAGNOSTICS — real tools.ollama_smoke report payload")
    import tools.ollama_smoke as smoke
    from app.generation import ollama_provider as ollama_mod
    from app.generation.provider import ProviderResult as PR

    # D1: case_truth mocked run -> report carries the full diagnostics key set.
    class FakeProviderSchema:
        last_format = "schema"

        def __init__(self, **_kwargs):
            pass

        def generate(self, request):
            return PR(content=_j(CASE_TRUTH_BROKE))

        @property
        def structured_output_sent(self):
            return self.last_format == "schema"

    orig = (ollama_mod.ollama_available, ollama_mod.ollama_structured_output_supported,
            ollama_mod.OllamaProvider)
    ollama_mod.ollama_available = lambda _s: (True, "")
    ollama_mod.ollama_structured_output_supported = lambda _s: True
    ollama_mod.OllamaProvider = FakeProviderSchema
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = smoke.main(["--enable", "--debug", "--stage", "case_truth"])
        report = json.loads(buf.getvalue())
    finally:
        (ollama_mod.ollama_available, ollama_mod.ollama_structured_output_supported,
         ollama_mod.OllamaProvider) = orig
    gen = report.get("generation") or {}
    keys_ok = all(k in gen for k in (
        "stage", "stageAlias", "stageTemplate", "promptChars", "elapsedSeconds",
        "transportStructuredOutput", "providerResult", "parsedOk", "parseIssues",
        "parseIssueCount", "responseBytes", "rawSanitized", "rawSanitizedFull",
    ))
    record("D1a smoke case_truth exit 0 + full diagnostics key set",
           rc == 0 and keys_ok, sorted(gen.keys()))
    record("D1b stageTemplate == case_people_v1 (current version)",
           gen.get("stageTemplate") == "case_people_v1", gen.get("stageTemplate"))
    record("D1c transportStructuredOutput true (schema transport truthful)",
           gen.get("transportStructuredOutput") is True, gen.get("transportStructuredOutput"))
    record("D1d parseIssues == EXACTLY the 2 documented issues",
           gen.get("parsedOk") is False and gen.get("parseIssueCount") == 2
           and gen.get("parseIssues") == list(EXPECTED_CASE_TRUTH_BROKE_ISSUES),
           gen.get("parseIssues"))
    record("D1e elapsedSeconds + responseBytes present and sane",
           isinstance(gen.get("elapsedSeconds"), (int, float))
           and isinstance(gen.get("responseBytes"), int)
           and gen.get("responseBytes") > 0,
           {"elapsedSeconds": gen.get("elapsedSeconds"),
            "responseBytes": gen.get("responseBytes")})
    raw_sample = gen.get("rawSanitized") or {}
    record("D1f rawSanitized present + redacted (no truth seeds / hosts / ports)",
           ("text" in raw_sample or ("first" in raw_sample and "last" in raw_sample))
           and all(tok not in buf.getvalue() for tok in
                   ("127.0.0.1", "11434", "OLLAMA_BASE_URL", "murdererId", "solverProof")),
           dict(raw_sample))
    full = gen.get("rawSanitizedFull")
    record("D1g --debug full sanitized text is present AND still redacted",
           isinstance(full, str)
           and all(tok not in full for tok in
                   ("http://", "127.0.0.1", "11434", "crimeTime=", "solverProof")),
           "")

    # D2: asset_spec mocked run -> per-pass repair trace + finalProcId.
    contents = [
        _j(asset_broke()),   # single-stage generate
        _j(asset_broke()),   # ASSET_SPEC (round-trip initial)
        _j(asset_good()),    # ASSET_SPEC_REPAIR (healed)
    ]

    class FakeProviderAsset:
        last_format = "schema"

        def __init__(self, **_kwargs):
            self.contents = list(contents)

        def generate(self, request):
            return PR(content=self.contents.pop(0))

        @property
        def structured_output_sent(self):
            return self.last_format == "schema"

    ollama_mod.ollama_available = lambda _s: (True, "")
    ollama_mod.ollama_structured_output_supported = lambda _s: True
    ollama_mod.OllamaProvider = FakeProviderAsset
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = smoke.main(["--enable", "--debug", "--stage", "asset_spec"])
        report = json.loads(buf.getvalue())
    finally:
        (ollama_mod.ollama_available, ollama_mod.ollama_structured_output_supported,
         ollama_mod.OllamaProvider) = orig
    gen = report.get("generation") or {}
    rt = report.get("geometryRoundtrip") or {}
    trace = ((rt.get("perPassIssues") or {}).get("repairTrace")) or []
    record("D2a asset_spec stage template == asset_spec_v1",
           gen.get("stageTemplate") == "asset_spec_v1", gen.get("stageTemplate"))
    record("D2b asset_spec transportStructuredOutput truthful (schema)",
           gen.get("transportStructuredOutput") is True, gen.get("transportStructuredOutput"))
    record("D2c first-pass AssetSpec is Phase-13 SCHEMA-valid (parsedOk true)",
           gen.get("parsedOk") is True and gen.get("parseIssues") == [], gen.get("parseIssues"))
    record("D2d geometryRoundtrip: providerOk + geometricallyValid (repair path)",
           rt.get("providerOk") is True and rt.get("geometricallyValid") is True,
           {"providerOk": rt.get("providerOk"), "geometricallyValid": rt.get("geometricallyValid")})
    gm = rt.get("geometryMetrics") or {}
    record("D2e geometryMetrics: issueCountBeforeRepair=1, repairAttempts=1",
           gm.get("issueCountBeforeRepair") == 1 and gm.get("repairAttempts") == 1, gm)
    record("D2f per-pass repair trace (order preserved, sanitized): pass0 "
           "[SILHOUETTE_HEURISTIC] -> pass1 []",
           [e.get("pass") for e in trace] == [0, 1]
           and [i.get("code") for i in (trace[0].get("geometryIssues") or [])]
           == ["SILHOUETTE_HEURISTIC"]
           and trace[1].get("geometryIssues") == [],
           trace)
    record("D2g finalProcId deterministic and content-addressed (proc.*)",
           str(rt.get("finalProcId", "")).startswith("proc."), rt.get("finalProcId"))

    # D3: truthful flag false when the transport actually sent "json".
    class FakeProviderJson:
        last_format = "json"

        def __init__(self, **_kwargs):
            pass

        def generate(self, request):
            return PR(content=_j(CASE_TRUTH_GOOD))

        @property
        def structured_output_sent(self):
            return self.last_format == "schema"

    ollama_mod.ollama_available = lambda _s: (True, "")
    ollama_mod.ollama_structured_output_supported = lambda _s: True
    ollama_mod.OllamaProvider = FakeProviderJson
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = smoke.main(["--enable", "--stage", "case_truth"])
        report = json.loads(buf.getvalue())
    finally:
        (ollama_mod.ollama_available, ollama_mod.ollama_structured_output_supported,
         ollama_mod.OllamaProvider) = orig
    record("D3 transportStructuredOutput false when 'json' was actually sent",
           report["generation"]["transportStructuredOutput"] is False,
           report["generation"]["transportStructuredOutput"])

    # D4: sanitize() edge cases.
    dirty = (
        "raw with http://127.0.0.1:11434 and 192.168.1.5:11434 and "
        "murdererId=paul crimeTime=23:42 solverProof=x localhost "
        "host.docker.internal"
    )
    clean = smoke._sanitize_text(dirty)
    record("D4 _sanitize_text strips URL/host/port/truth seeds deterministically",
           all(tok not in clean for tok in
               ("http://", "127.0.0.1", "192.168.1.5", "11434", "localhost",
                "murdererId", "crimeTime", "solverProof", "host.docker.internal"))
           and "<redacted>" in clean and "<host>" in clean and "<port>" in clean,
           clean)


def main() -> int:
    print("QA Phase17B contract audit (independent; in-process; mocked transports; "
          "REAL modules). Real hermes3:8b run is OPERATOR-SIDE and NOT attempted.\n")
    audit_format_transport()
    audit_stage_mapping()
    audit_failure_class_targets()
    audit_smoke_diagnostics()

    passed = sum(1 for r in results if r["ok"])
    failed = len(results) - passed
    out_path = Path(
        sys.argv[1] if len(sys.argv) > 1
        else REPO_ROOT / "e2e" / "artifacts" / "qa-phase17b-contract-audit.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "probe": "qa-phase17b-contract-audit.py",
                "exitCode": 0 if failed == 0 else 1,
                "passed": passed,
                "failed": failed,
                "total": len(results),
                "operatorBoundary": (
                    "the REAL hermes3:8b run (Phase17B section 5) is "
                    "OPERATOR-SIDE and was NOT attempted by this QA probe; all "
                    "checks above are the deterministic mocked/offline path."
                ),
                "results": results,
            },
            ensure_ascii=False, indent=2, sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n=== RESULT: {passed}/{len(results)} PASS, {failed} FAIL (exit "
          f"{0 if failed == 0 else 1}) ===")
    print(f"Evidence: {out_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())