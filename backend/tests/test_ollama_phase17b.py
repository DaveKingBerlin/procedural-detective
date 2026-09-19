"""Phase17B — hermes3:8b pipeline-mapping regression tests.

Observed real run (documented class): providerResult=true, parsedOk=false,
parseIssues=2, geometry first-pass issueCountBeforeRepair=1, repairAttempts=2,
geometricallyValid=false.

This file encodes the observed failure class as deterministic MOCKED fixtures
and proves the Phase17B tuning (prompt/schema MAPPING only) is reachable:

1. the "broke" fixtures reproduce the EXACT pre-tuning issue classes:
   - case_truth: TWO strict parse issues (divergent stage-schema key name
     ``crime_time`` + an oversized ``accusationToleranceSeconds`` value);
   - asset_spec: ONE first-pass Phase 17 geometry issue (a single-part
     underspecified envelope -> SILHOUETTE_HEURISTIC);
2. the tuned mapping makes the SAME intended content (correctly mapped) parse
   with 0 issues and reach the Phase17B §6 TARGETS (parsedOk=true, geometry
   providerOk=true, geometricallyValid=true), including the repair path;
3. the strict validators are byte-unchanged: rule constants are untouched and
   a deliberately-invalid response still fails with the identical issue.

Also verifies the authoritative JSON-Schema ``format`` transport path and the
smoke stage->template version mapping (no stale strings).
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.assets.geometry_quality import (  # noqa: E402
    HANDHELD_MAX_DIMENSION,
    HANDHELD_MAX_SINGLE_DIMENSION,
    SILHOUETTE_SEPARATION,
    validate_geometry,
)
from app.assets.specs import (  # noqa: E402
    DIMENSION_MAX,
    DIMENSION_MIN,
    MAX_PARTS,
    MAX_PART_SCALE,
    MAX_POSITION_BOUND,
    MAX_ROTATION_BOUND,
    MIN_PART_SCALE,
    PRIMITIVE_ALLOWLIST,
    parse_asset_spec,
    validate_asset_spec,
)
from app.assets.spec_provider import AssetSpecRequest  # noqa: E402
from app.generation import parser as stage_parser  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation.ollama_provider import (  # noqa: E402
    OllamaProvider,
    ollama_structured_output_supported,
)
from app.generation.provider import (  # noqa: E402
    GenerateRequest,
    GenerationStage,
    ProviderResult,
)
from app.services.ollama_driver import OllamaAssetSpecProvider  # noqa: E402

OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_MODEL = "hermes3:8b"

CANONICAL_TS = "2026-09-11T23:42:00+02:00"


# --------------------------------------------------------------------------- #
# deterministic "hermes3:8b-style broke" fixture (observed failure class)
# --------------------------------------------------------------------------- #

HERMES_CASE_TRUTH_BROKE = {
    "crime": {
        "type": "murder",
        "victimId": "victim_01",
        "murdererId": "suspect_02",
        "motiveId": "motive_01",
        "weaponId": "weapon_01",
        "locationId": "location_01",
        # (a) the stage-schema key rendered with a DIVERGENT name:
        "crime_time": {"canonical": CANONICAL_TS, "accusationToleranceSeconds": 300},
        # (b) an OVERSIZED value for the real key (out of the documented bound):
        "crimeTime": {
            "canonical": CANONICAL_TS,
            "accusationToleranceSeconds": 999_999_999,
        },
    }
}

# The SAME intended content with the CORRECT mapping (what the tuned prompt
# ring-fencing asks the model to emit).
HERMES_CASE_TRUTH_GOOD = {
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

# Expected exact pre-tuning parse issues (strict parser output; deterministic).
EXPECTED_CASE_TRUTH_BROKE_ISSUES = (
    "case_truth.crime.crimeTime: invalid crimeTime: "
    "CrimeTimeSpec.accusation_tolerance_seconds out of range: "
    "999999999 > 2592000",
    "case_truth.crime: unknown key 'crime_time'",
)


def _hermes_asset_spec_broke():
    """A single-part underspecified envelope: Phase-13-valid but exactly ONE
    first-pass Phase 17 geometry issue (SILHOUETTE_HEURISTIC)."""
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


def _hermes_asset_spec_good():
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


def _j(d):
    return json.dumps(d, sort_keys=True, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# mocked transport (records payloads; dispatches /api/version + /api/tags)
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
        if url.rstrip("/").endswith(("/api/version", "api/version")):
            if self.version_body is None:
                return 404, b'{"error": "missing"}'
            return 200, json.dumps(self.version_body).encode()
        body = {"models": [{"name": name} for name in self.tags_models]}
        return 200, json.dumps(body).encode()

    @property
    def call_count(self):
        return len(self.post_calls)

    def format_of_call(self, index=0):
        return self.post_calls[index][1].get("format")


def _provider(transport, structured_output=False):
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
    """Tiny deterministic provider adapter scripted with raw spec texts."""

    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0
        self.last_format = "json"

    def generate(self, request) -> ProviderResult:
        self.calls += 1
        content = self.contents.pop(0) if self.contents else "<not-json>"
        return ProviderResult(content=content)


# --------------------------------------------------------------------------- #
# 1. the "broke" fixtures reproduce the OBSERVED issue classes BEFORE tuning
# --------------------------------------------------------------------------- #


def test_observed_case_truth_2_parse_issues_reproduced():
    """Phase17B class (a): exactly TWO strict parse issues — a stage-schema key
    rendered with a divergent name AND an oversized value."""
    content = _j(HERMES_CASE_TRUTH_BROKE)
    issues = stage_parser.collect_issues(GenerationStage.CASE_TRUTH, content)
    assert len(issues) == 2  # the observed parseIssues=2
    assert issues == EXPECTED_CASE_TRUTH_BROKE_ISSUES
    with pytest.raises(stage_parser.GenerationParseError) as excinfo:
        stage_parser.parse_stage(
            GenerationStage.CASE_TRUTH, content, non_throwing=False
        )
    assert tuple(excinfo.value.issues) == EXPECTED_CASE_TRUTH_BROKE_ISSUES


def test_observed_asset_spec_1_geometry_issue_reproduced():
    """Phase17B class (b): exactly ONE first-pass Phase 17 geometry issue on a
    Phase-13-valid spec (single-part underspecified envelope)."""
    raw = _hermes_asset_spec_broke()
    assert validate_asset_spec(raw) == ()  # Phase 13 schema-valid
    report = validate_geometry(parse_asset_spec(raw, non_throwing=False))
    assert len(report.issues) == 1  # the observed first-pass issueCount=1
    issue = report.issues[0]
    assert issue.code == "SILHOUETTE_HEURISTIC"
    assert issue.classification == "QUALITY_ERROR"
    assert "two distinct parts" in issue.message or "TWO distinct parts" in issue.message


# --------------------------------------------------------------------------- #
# 2. after tuning: the SAME intended content with the correct mapping parses
#    with 0 issues and reaches the Phase17B §6 targets
# --------------------------------------------------------------------------- #


def test_wellformed_case_truth_parses_with_zero_issues():
    """parsedOk=true is reachable with a mocked well-formed hermes-style
    response (same intended content, correct key/value mapping)."""
    content = _j(HERMES_CASE_TRUTH_GOOD)
    issues = stage_parser.collect_issues(GenerationStage.CASE_TRUTH, content)
    assert issues == ()
    spec = stage_parser.parse_stage(
        GenerationStage.CASE_TRUTH, content, non_throwing=False
    )
    assert spec is not None and spec.victim_id == "victim_01"


def test_asset_spec_repair_path_reaches_targets():
    """§6 targets via the repair path: first-pass-broken -> repair -> valid.
    providerOk=true, geometricallyValid=true, one repair attempt, and the
    first-pass issue class is exactly SILHOUETTE_HEURISTIC."""
    served = _ScriptedProvider(
        [_j(_hermes_asset_spec_broke()), _j(_hermes_asset_spec_good())]
    )
    provider = OllamaAssetSpecProvider(
        provider=served,
        attempt_id="phase17b-repair",
        budget_consumer=lambda: True,
    )
    result = provider.generate(
        AssetSpecRequest(requested_name="bronze ceremonial ice pick")
    )
    assert result.error is None
    assert served.calls == 2  # initial + ONE repair

    metrics = provider.last_geometry_metrics
    assert metrics is not None
    assert metrics["issueCountBeforeRepair"] == 1
    assert metrics["repairAttempts"] == 1
    assert metrics["generatedOnFirstPass"] is False
    assert metrics["repaired"] is True
    assert metrics["silhouettePassed"] is True

    # the internal trace shows the first-pass issue then a clean repair pass.
    trace = provider.last_repair_trace
    assert [e["pass"] for e in trace] == [0, 1]
    first = trace[0]["geometryIssues"]
    assert len(first) == 1 and first[0]["code"] == "SILHOUETTE_HEURISTIC"
    assert trace[1]["geometryIssues"] == []

    # providerOk / geometricallyValid / compiled proc.* id.
    assert result.error is None  # providerOk
    from app.assets.compiler import asset_id_for
    from app.assets.compiler import compile_asset_spec

    final_spec = parse_asset_spec(result.content, non_throwing=False)
    assert validate_geometry(final_spec).valid  # geometricallyValid
    proc_id = asset_id_for(final_spec)
    assert proc_id.startswith("proc.")
    assert proc_id == asset_id_for(parse_asset_spec(_hermes_asset_spec_good(), non_throwing=False))
    compile_asset_spec(final_spec)  # compiles to a definition


def test_asset_spec_first_pass_reaches_targets():
    """§6 targets on the first pass: a well-formed spec needs no repair
    (generatedOnFirstPass, providerOk, geometricallyValid)."""
    served = _ScriptedProvider([_j(_hermes_asset_spec_good())])
    provider = OllamaAssetSpecProvider(
        provider=served,
        attempt_id="phase17b-first",
        budget_consumer=lambda: True,
    )
    result = provider.generate(
        AssetSpecRequest(requested_name="bronze ceremonial ice pick")
    )
    assert result.error is None  # providerOk
    metrics = provider.last_geometry_metrics
    assert metrics["issueCountBeforeRepair"] == 0
    assert metrics["generatedOnFirstPass"] is True
    assert metrics["repaired"] is False
    final = parse_asset_spec(result.content, non_throwing=False)
    assert validate_geometry(final).valid  # geometricallyValid


def test_phase17b_targets_reachable_through_full_driver():
    """The full OllamaStageDriver pipeline (mocked transport) publishes with the
    coherent asset — the geometry provider path is green end-to-end."""
    from test_ollama_driver import _case_people, _evidence, _j as _jd, _run, _world

    posts = [
        _jd(_case_people()),
        _jd(_evidence()),
        _jd(_world()),
        _j(_hermes_asset_spec_good()),
    ]
    record, transport = _run(posts)
    assert record.state.value == "PUBLISHED"
    published = record.published.draft if record.published else record.draft
    proc_ids = {
        p.asset_id
        for p in published.world_graph.placements
        if p.asset_id.startswith("proc.")
    }
    assert proc_ids  # the coherent asset was compiled + placed (geometry gate passed)


# --------------------------------------------------------------------------- #
# 3. the strict validators are byte-unchanged (rule constants + identical
#    failure on deliberately-invalid responses)
# --------------------------------------------------------------------------- #


def test_strict_validator_rule_constants_byte_unchanged():
    """The authoritative Phase 13 / Phase 17 numeric bounds and vocabularies are
    untouched by the mapping tuning."""
    assert PRIMITIVE_ALLOWLIST == frozenset({"box", "cylinder", "sphere", "plane"})
    assert DIMENSION_MIN == 0.05
    assert DIMENSION_MAX == 4.0
    assert MAX_PARTS == 24
    assert MAX_POSITION_BOUND == 4.0
    assert round(MAX_ROTATION_BOUND, 4) == round(2.0 * 3.141592653589793, 4)
    assert MIN_PART_SCALE == 0.05
    assert MAX_PART_SCALE == 2.0
    assert HANDHELD_MAX_DIMENSION == 0.5
    assert HANDHELD_MAX_SINGLE_DIMENSION == 1.0
    assert SILHOUETTE_SEPARATION == 0.05


def test_deliberately_invalid_responses_still_fail_identically():
    """A deliberately-invalid response (never touched by the tuning) still
    produces the byte-identical issue set through the unchanged strict path."""
    # (a) unit-confusion dims are still rejected by Phase 13 with the same text.
    unit_confused = _hermes_asset_spec_broke()
    unit_confused["dimensions"] = {"x": 25, "y": 0.1, "z": 0.1}
    issues = validate_asset_spec(unit_confused)
    assert "dimensions.x: dimension must be within [0.05, 4] (got 25)" in issues
    assert "dimensions.y: dimension must be within [0.05, 4] (got 0.1)" not in issues
    # (b) an unsupported primitive keeps its exact rejection message.
    bad_primitive = _hermes_asset_spec_broke()
    bad_primitive["parts"][0]["primitive"] = "capsule"
    issues = validate_asset_spec(bad_primitive)
    assert any("is not in the renderer-supported primitive vocabulary" in i for i in issues)
    # (c) the case_truth broke document still fails with the exact 2 issues.
    assert stage_parser.collect_issues(
        GenerationStage.CASE_TRUTH, _j(HERMES_CASE_TRUTH_BROKE)
    ) == EXPECTED_CASE_TRUTH_BROKE_ISSUES
    # (d) the geometry gate still rejects the broke fixture exactly as one issue.
    report = validate_geometry(
        parse_asset_spec(_hermes_asset_spec_broke(), non_throwing=False)
    )
    assert [i.code for i in report.issues] == ["SILHOUETTE_HEURISTIC"]


# --------------------------------------------------------------------------- #
# 4. authoritative JSON-Schema transport (Ollama /api/chat format)
# --------------------------------------------------------------------------- #


def test_transport_structured_output_sends_authoritative_schema():
    """structured_output=True sends the AUTHORITATIVE JSON Schema derived from
    the SAME ``schema_contract`` mapping the prompt embeds (one source, never a
    duplicate); the truthful flag reflects what was ACTUALLY sent."""
    transport = MockOllamaTransport(
        posts=['{"crime": {"type": "murder"}}'], version_body={"version": "0.34.2"}
    )
    provider = _provider(transport, structured_output=True)
    result = provider.generate(
        GenerateRequest(
            attempt_id="att-1",
            stage=GenerationStage.CASE_TRUTH,
            prompt_context="ctx",
        )
    )
    assert result.content is not None
    sent = transport.format_of_call(0)
    assert isinstance(sent, dict)
    assert sent == prompts.json_schema_for_generation_stage("case_truth")
    assert sent["type"] == "object"
    # derived from the SAME contract: the schema top-level keys are the parsed
    # contract keys (never a hand-maintained duplicate).
    contract_text = prompts.schema_contract("case_people")
    assert set(sent["properties"]) == set(json.loads(contract_text))
    assert provider.last_format == "schema"
    assert provider.structured_output_sent is True


def test_transport_structured_output_fallback_json_and_repair_stage():
    """Without structured output, and for the REPAIR stage (no schema contract),
    the documented ``format: "json"`` fallback is what is ACTUALLY sent."""
    transport = MockOllamaTransport(posts=['{"crime": {}}'])
    provider = _provider(transport, structured_output=False)
    provider.generate(
        GenerateRequest(
            attempt_id="att-2",
            stage=GenerationStage.CASE_TRUTH,
            prompt_context="ctx",
        )
    )
    assert transport.format_of_call(0) == "json"
    assert provider.last_format == "json"
    assert provider.structured_output_sent is False

    # REPAIR has no per-stage skeleton -> "json" fallback even when structured
    # output is enabled (never a crashed format build).
    transport2 = MockOllamaTransport(posts=[_j(HERMES_CASE_TRUTH_GOOD)])
    provider2 = _provider(transport2, structured_output=True)
    provider2.generate(
        GenerateRequest(
            attempt_id="att-3",
            stage=GenerationStage.REPAIR,
            prompt_context="ctx",
        )
    )
    assert transport2.format_of_call(0) == "json"
    assert provider2.last_format == "json"


def test_structured_output_probe_matches_documented_ollama_version():
    """The documented capability probe: >= 0.8.0 -> True; older/unknown/absent
    -> the deterministic ``format: "json"`` fallback. Never raises."""
    from app.core.config import Settings

    settings = Settings(generation_provider="ollama")

    def _probe(version_body):
        transport = MockOllamaTransport(version_body=version_body)
        return ollama_structured_output_supported(settings, transport)

    assert _probe({"version": "0.8.0"}) is True
    assert _probe({"version": "0.34.2"}) is True
    assert _probe({"version": "0.7.9"}) is False
    assert _probe({"version": "0.0.0"}) is False
    assert _probe({"version": "not-a-version"}) is False
    assert _probe({"something": "else"}) is False
    # a failing transport degrades to False (never raises).
    def _boom(url, timeout):
        raise OSError("probe refused")
    transport = MockOllamaTransport(version_body={"version": "0.34.2"})
    transport.get = _boom  # type: ignore[assignment]
    assert ollama_structured_output_supported(settings, transport) is False


# --------------------------------------------------------------------------- #
# 5. smoke stage->template mapping (no stale/obsolete version strings)
# --------------------------------------------------------------------------- #


def test_stage_mapping_versions_are_current_no_stale_strings():
    """Phase17B §3: the smoke stage mapping resolves to the CURRENT versioned
    prompt templates; no stale/obsolete version strings remain anywhere in the
    prompt chain."""
    current = set(prompts.STAGE_TO_PROMPT_VERSION.values())
    expected = {
        "case_people_v1",
        "evidence_v1",
        "world_requirements_v1",
        "asset_spec_v1",
        "asset_spec_repair_v1",
        "repair_v1",
    }
    assert current == expected
    assert set(prompts.STAGE_TO_CONTRACT.values()) == set(prompts.CONTRACT_KEYS) == {
        "case_people",
        "evidence",
        "world_requirements",
        "asset_spec",
    }

    # the rendered prompts embed the CURRENT versions verbatim.
    assert "case_people_v1" in prompts.build_case_people_prompt("x", None)
    assert "evidence_v1" in prompts.build_evidence_prompt("x", None)
    assert "world_requirements_v1" in prompts.build_world_requirements_prompt("x", None)
    assert "asset_spec_v1" in prompts.build_asset_spec_prompt("x", "decor")
    assert "asset_spec_repair_v1" in prompts.build_asset_spec_repair_prompt("x", "{}", ("i",))
    assert "repair_v1" in prompts.build_repair_prompt("{}", ("i",))

    # NO stale version tokens anywhere in the module source (lowercase scan —
    # the `_v1` constant NAMES are uppercase and excluded).
    source = inspect.getsource(prompts)
    stale_tokens = set(re.findall(r"\b[a-z][a-z0-9_]*_v[0-9]+\b", source))
    assert stale_tokens == expected, f"stale version strings leaked: {stale_tokens}"
    assert "v0" not in source


def test_json_schema_derived_from_single_contract_source():
    """The transport JSON Schema is derived from the SAME contract mapping the
    prompt renders: the schema's top-level properties equal the rendered
    contract's keys, and required keys are exactly the non-nullable ones."""
    for contract_key in prompts.CONTRACT_KEYS:
        schema = prompts.schema_contract_as_json_schema(contract_key)
        contract = json.loads(prompts.schema_contract(contract_key))
        assert schema["type"] == "object"
        assert set(schema["properties"]) == set(contract), contract_key
        optional = {
            key
            for key, value in contract.items()
            if isinstance(value, str) and "null" in value
        }
        assert set(schema["required"]) == set(contract) - optional, contract_key


# --------------------------------------------------------------------------- #
# 6. smoke tool: sanitization + offline behavior (no network/server)
# --------------------------------------------------------------------------- #


def test_smoke_sanitize_strips_secrets_urls_and_truth_seeds():
    import tools.ollama_smoke as smoke

    dirty = (
        "raw sample with http://127.0.0.1:11434 and 192.168.1.5:11434 "
        "and murdererId=paul crimeTime=23:42 solverProof=x localhost"
    )
    clean = smoke._sanitize_text(dirty)
    for token in ("http://", "127.0.0.1", "192.168.1.5", "11434", "localhost"):
        assert token not in clean, token
    for seed in ("murdererId", "crimeTime", "solverProof"):
        assert seed not in clean, seed
    assert "<redacted>" in clean and "<host>" in clean and "<port>" in clean


def test_smoke_offline_no_server_exits_zero_sanitized(monkeypatch, capsys):
    """The no-server smoke invocation prints a sanitized 'probe false' JSON and
    exits 0 (no generation, no traceback, no network)."""
    import tools.ollama_smoke as smoke
    from app.generation import ollama_provider as ollama_mod

    # Deterministic offline simulation: the availability probe says no server.
    monkeypatch.setattr(
        ollama_mod, "ollama_available", lambda _settings: (False, "not available")
    )
    rc = smoke.main(["--enable", "--debug", "--stage", "case_truth"])
    captured = capsys.readouterr()
    assert rc == 0
    report = json.loads(captured.out)
    assert report["probeAvailable"] is False
    assert report["generation"] is None
    assert report["structuredOutputProbe"]["supported"] is False
    assert "errorSanitized" not in report
    # no prompts / base URL / host leaks into the report.
    blob = captured.out
    for token in ("127.0.0.1", "11434", "http:", "OLLAMA", "solverProof"):
        assert token not in blob, token


def test_smoke_stage_case_truth_mocked_end_to_end(monkeypatch, capsys):
    """smoke.main --stage case_truth with a mocked provider: the report shows
    the current template version, the truthful transport flag, parse issues on
    the broke fixture, and the sanitized raw sample (never the prompt/base URL)."""
    import tools.ollama_smoke as smoke
    from app.generation import ollama_provider as ollama_mod
    from app.generation.provider import ProviderResult as PR

    class FakeProvider:
        last_format = "schema"

        def __init__(self, **_kwargs):
            pass

        def generate(self, request) -> PR:
            return PR(content=_j(HERMES_CASE_TRUTH_BROKE))

        @property
        def structured_output_sent(self):
            return self.last_format == "schema"

    monkeypatch.setattr(ollama_mod, "ollama_available", lambda _s: (True, ""))
    monkeypatch.setattr(ollama_mod, "ollama_structured_output_supported", lambda _s: True)
    monkeypatch.setattr(ollama_mod, "OllamaProvider", FakeProvider)

    rc = smoke.main(["--enable", "--stage", "case_truth"])
    captured = capsys.readouterr()
    assert rc == 0
    report = json.loads(captured.out)
    gen = report["generation"]
    assert gen["stageAlias"] == "case_truth"
    assert gen["stageTemplate"] == "case_people_v1"
    assert gen["transportStructuredOutput"] is True
    assert gen["providerResult"] is True
    assert gen["parsedOk"] is False
    assert gen["parseIssueCount"] == 2
    assert gen["parseIssues"] == list(EXPECTED_CASE_TRUTH_BROKE_ISSUES)
    assert "responseBytes" in gen and "elapsedSeconds" in gen
    sample = gen["rawSanitized"]
    assert "text" in sample or ("first" in sample and "last" in sample)
    blob = captured.out
    for token in ("127.0.0.1", "11434", "OLLAMA_BASE_URL", "prompt_context"):
        assert token not in blob, token


def test_smoke_stage_asset_spec_mocked_end_to_end(monkeypatch, capsys):
    """smoke.main --stage asset_spec with a mocked provider exercises the FULL
    Phase 17 diagnostics: first-pass issues, each repair attempt (order
    preserved), repair count and the final compiled proc.* id."""
    import tools.ollama_smoke as smoke
    from app.generation import ollama_provider as ollama_mod
    from app.generation.provider import ProviderResult as PR

    contents = [
        _j(_hermes_asset_spec_broke()),  # single-stage generate
        _j(_hermes_asset_spec_broke()),  # ASSET_SPEC (round-trip initial)
        _j(_hermes_asset_spec_good()),   # ASSET_SPEC_REPAIR (healed)
    ]

    class FakeProvider:
        last_format = "schema"

        def __init__(self, **_kwargs):
            self.contents = list(contents)

        def generate(self, request) -> PR:
            return PR(content=self.contents.pop(0))

        @property
        def structured_output_sent(self):
            return self.last_format == "schema"

    monkeypatch.setattr(ollama_mod, "ollama_available", lambda _s: (True, ""))
    monkeypatch.setattr(ollama_mod, "ollama_structured_output_supported", lambda _s: True)
    monkeypatch.setattr(ollama_mod, "OllamaProvider", FakeProvider)

    rc = smoke.main(["--enable", "--debug", "--stage", "asset_spec"])
    captured = capsys.readouterr()
    assert rc == 0
    report = json.loads(captured.out)
    gen = report["generation"]
    assert gen["stageTemplate"] == "asset_spec_v1"
    assert gen["transportStructuredOutput"] is True
    # the broke fixture is Phase-13 SCHEMA-valid (the failure lives in the
    # Phase 17 geometry gate, reported under geometryRoundtrip).
    assert gen["parsedOk"] is True
    assert gen["parseIssues"] == []
    rt = report["geometryRoundtrip"]
    assert rt["providerOk"] is True
    assert rt["geometricallyValid"] is True
    assert rt["geometryMetrics"]["issueCountBeforeRepair"] == 1
    assert rt["geometryMetrics"]["repairAttempts"] == 1
    trace = rt["perPassIssues"]["repairTrace"]
    assert [e["pass"] for e in trace] == [0, 1]
    assert [i["code"] for i in trace[0]["geometryIssues"]] == ["SILHOUETTE_HEURISTIC"]
    assert trace[1]["geometryIssues"] == []
    assert rt["finalProcId"].startswith("proc.")


@pytest.fixture(autouse=True)
def _network_block():
    """Enforce zero real network/process/port use while these tests run."""
    import socket

    original = socket.socket

    def _deny(*args, **kwargs):
        raise RuntimeError("network access blocked during phase17b tests")

    socket.socket = _deny
    yield
    socket.socket = original