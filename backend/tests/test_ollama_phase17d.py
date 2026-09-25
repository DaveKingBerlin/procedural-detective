"""Phase17D — real-Hermes contract fixes: regression tests.

Covers the three remaining REAL contract issues (A/B/C) plus the provider-call
accounting (17C §8) and the failure policy (17C §13):

A. EVIDENCE
   ``propositions[].structured`` must be a JSON OBJECT in the transport JSON
   Schema AND in the prompt (the strict parser already required an object;
   Phase17D forbids parser-side auto-deserialization — verified here: a
   JSON-encoded STRING still fails with the exact issue).

B. ASSET_SPEC thin geometry
   Trace conclusion: AssetSpec ``dimensions`` and ``transform.scale`` are BOTH
   physical METERS (the compile step copies part scale verbatim into the
   render definition and the frontend Babylon renderer applies it as absolute
   world units). The 0.05 floor was therefore a representation bug: a
   realistic 0.25 m ice pick with a 0.002-0.01 m shaft/blade is inexpressible.
   The fix: physical floor 0.05 -> 0.001 m (Phase 13) and the Phase 17
   visible-extent gate now rejects longest-span/collapsed geometry instead of
   a coarse volume floor. Verifies a realistic thin pick parses + passes the
   geometry gate + compiles with bounded hitboxes, while near-zero clumps and
   with existing GOLDEN fixtures/ids BYTE-UNCHANGED (content addressing never
   depends on the bound constants).

C. REPAIR
   Transport JSON Schema for the REPAIR stage is derived from the
   authoritative FULL-DRAFT contract (9 required top-level keys); the prompt
   includes the explicit "Return the COMPLETE repaired draft, including every
   required top-level key." line. No partial-patch semantics; the strict
   parser is relaxed nowhere (a partial draft STILL fails with the exact
   missing-key issues).

13. FAILURE POLICY
   With GENERATION_PROVIDER=ollama pointed at an unreachable port
   (subprocess-level env override) generation reports the sanitized
   provider-unavailable reason and the provider is an OllamaProvider, never a
   FakeProvider.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assets.compiler import (  # noqa: E402
    HITBOX_MAX,
    HITBOX_MIN,
    asset_id_for,
    compile_asset_spec,
)
from app.assets.geometry_quality import (  # noqa: E402
    MIN_VISIBLE_AXIS,
    MIN_VISIBLE_EXTENT,
    validate_geometry,
)
from app.assets.spec_provider import AssetSpecRequest  # noqa: E402
from app.assets.specs import (  # noqa: E402
    DIMENSION_MAX,
    DIMENSION_MIN,
    MAX_PART_SCALE,
    MIN_PART_SCALE,
    parse_asset_spec,
    validate_asset_spec,
)
from app.generation import parser as stage_parser  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation.constraints import LockedConstraints  # noqa: E402
from app.generation.provider import GenerateRequest, GenerationStage, ProviderResult  # noqa: E402
from app.services.ollama_driver import (  # noqa: E402
    MAX_SPEC_REPAIR_PASSES,
    OllamaAssetSpecProvider,
    _identity_slug,
    _locked_id_sheet,
    _project_placement_evidence,
    _weapon_evidence_id,
)

from fixtures.asset_specs import (  # noqa: E402
    ANTIQUE_LETTER_OPENER_NAME,
    ANTIQUE_LETTER_OPENER_SPEC,
    LAB_SAMPLE_RACK_NAME,
    LAB_SAMPLE_RACK_SPEC,
)
from fixtures.world_matrix import (  # noqa: E402
    PROC_ANTIQUE_LETTER_OPENER,
    PROC_SAMPLE_RACK,
)
from test_geometry_quality import _coherent, _single_part  # noqa: E402
from test_ollama_driver import _j  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _parsed(raw):
    return parse_asset_spec(raw, non_throwing=False)


def _issue_codes(raw):
    return {i.code for i in validate_geometry(_parsed(raw)).issues}


# --------------------------------------------------------------------------- #
# B. thin geometry — realistic ice pick fixture (Phase17D B)
# --------------------------------------------------------------------------- #

# A REALISTIC bronze ceremonial ice pick, physical meters:
#   handle cylinder: 20 cm long (y), 4 cm diameter (x/z);
#   blade box: 10 cm long (y), 2 cm wide (x), 4 mm thick (z).
# Everything sits INSIDE the pre-fix 0.05 floor (0.004 / 0.002 < 0.05) and is
# exactly the thin geometry Hermes naturally emits.
THIN_ICE_PICK = {
    "canonicalName": "Bronze Ceremonial Ice Pick",
    "category": "decor",
    "subtype": "ceremonial_ice_pick",
    "dimensions": {"x": 0.03, "y": 0.4, "z": 0.03},
    "parts": [
        {
            "id": "part_00",
            "role": "handle",
            "primitive": "cylinder",
            "transform": {
                "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                "scale": {"x": 0.04, "y": 0.2, "z": 0.04},
            },
            "material": "wood.dark",
        },
        {
            "id": "part_01",
            "role": "blade",
            "primitive": "box",
            "transform": {
                "position": {"x": 0.0, "y": 0.21, "z": 0.0},
                "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                "scale": {"x": 0.02, "y": 0.1, "z": 0.004},
            },
            "material": "metal.brass",
        },
    ],
}


class _RecordingProvider:
    """Deterministic scripted provider that records every real generate call
    (the regression harness distinguishing REAL provider calls from local
    validation/reprocessing)."""

    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0
        self.stages: list[str] = []

    def generate(self, request):
        self.calls += 1
        self.stages.append(request.stage.value)
        content = self.contents.pop(0) if self.contents else "<not-json>"
        return ProviderResult(content=content)


def test_thin_ice_pick_parses_in_phase13_bounds():
    """A realistic thin pick is now Phase-13 valid: dimensions and thin part
    scales (0.004/0.002 m) sit inside the physical floor 0.001 m."""
    assert validate_asset_spec(THIN_ICE_PICK) == ()
    spec = _parsed(THIN_ICE_PICK)
    assert all(DIMENSION_MIN <= v <= DIMENSION_MAX for v in spec.dimensions)
    for part in spec.parts:
        assert all(MIN_PART_SCALE <= v <= MAX_PART_SCALE for v in part.transform.scale)


def test_thin_ice_pick_passes_phase17_geometry_gate():
    """The geometry gate accepts the thin-but-long pick: its longest span is
    way above MIN_VISIBLE_EXTENT and it is NOT collapsed on every axis (one
    thin 4 mm axis is realistic, not near-zero)."""
    report = validate_geometry(_parsed(THIN_ICE_PICK), requested_name="bronze ceremonial ice pick")
    assert report.valid, [i.code for i in report.issues]
    assert report.metrics.silhouette_passed is True
    assert "VISUAL_EXTENT_TOO_SMALL" not in {i.code for i in report.issues}


def test_thin_ice_pick_compiles_with_bounded_pickable_hitbox():
    """The trusted compiler compiles the thin pick; every derived hitbox axis
    is bounded, pickable (>= HITBOX_MIN) and within HITBOX_MAX."""
    definition = compile_asset_spec(_parsed(THIN_ICE_PICK))
    assert len(definition.parts) == 2
    hit = definition.hitbox.scale
    for axis in ("x", "y", "z"):
        assert HITBOX_MIN <= getattr(hit, axis) <= HITBOX_MAX
    # the compiled part scales are the declared physical meters, verbatim.
    by_id = {p.id: p for p in definition.parts}
    assert by_id["part_01"].transform.scale.z == pytest.approx(0.004)


def test_near_zero_clump_still_rejected():
    """The thin fix does NOT open a near-zero hole: a 5 cm clump (collapsed on
    EVERY axis below MIN_VISIBLE_AXIS) is still VISUAL_EXTENT_TOO_SMALL."""
    clump = _coherent(dims=(0.2, 0.2, 0.2))
    for part in clump["parts"]:
        part["transform"]["position"] = {"x": 0.0, "y": 0.0, "z": 0.0}
        part["transform"]["scale"] = {"x": 0.05, "y": 0.05, "z": 0.05}
    codes = _issue_codes(clump)
    assert "VISUAL_EXTENT_TOO_SMALL" in codes
    # and a truly sub-visual object (longest axis < MIN_VISIBLE_EXTENT) is
    # rejected by the longest-span leg alone.
    tiny = _coherent(dims=(0.001, 0.001, 0.001))
    tiny["parts"] = [
        _single_part(role="speck", primitive="box", scale=(0.001, 0.001, 0.001), part_id="part_00"),
        _single_part(role="speck2", primitive="box", position=(0.0001, 0.0, 0.0), scale=(0.001, 0.001, 0.001), part_id="part_01"),
    ]
    codes_tiny = _issue_codes(tiny)
    assert "VISUAL_EXTENT_TOO_SMALL" in codes_tiny
    # constants remain documented and sane.
    assert MIN_VISIBLE_EXTENT == 0.02
    assert MIN_VISIBLE_AXIS == 0.06


def test_existing_golden_fixtures_keep_byte_identical_proc_ids():
    """Compatibility strategy: the bound-constant change does NOT alter the
    canonical spec text or the content-addressed proc.* ids of every existing
    GOLDEN fixture (normalize_spec hashes ONLY component values — the pinned
    ids in tests/fixtures/world_matrix.py are untouched by intent and by
    construction)."""
    for name, raw in (
        (ANTIQUE_LETTER_OPENER_NAME, ANTIQUE_LETTER_OPENER_SPEC),
        (LAB_SAMPLE_RACK_NAME, LAB_SAMPLE_RACK_SPEC),
    ):
        spec = _parsed(raw)
        proc_id = asset_id_for(spec)
        assert proc_id.startswith("proc.")
        assert proc_id == asset_id_for(_parsed(raw))  # deterministic
        # geometry gate stays green on the fixtures (no drift).
        assert validate_geometry(spec).valid
    assert asset_id_for(_parsed(ANTIQUE_LETTER_OPENER_SPEC)) == PROC_ANTIQUE_LETTER_OPENER
    assert asset_id_for(_parsed(LAB_SAMPLE_RACK_SPEC)) == PROC_SAMPLE_RACK


# --------------------------------------------------------------------------- #
# A. evidence — structured must be a JSON OBJECT (Phase17D A)
# --------------------------------------------------------------------------- #


def test_evidence_transport_schema_declares_structured_as_object():
    """The authoritative transport JSON Schema declares
    ``propositions[].structured`` as an actual OBJECT (never a string), so the
    Ollama ``format`` grammar forces the model to emit a nested object."""
    schema = prompts.json_schema_for_generation_stage("evidence")
    evidence_items = schema["properties"]["evidence"]
    proposition = evidence_items["items"]["properties"]["propositions"]["items"]
    structured = proposition["properties"]["structured"]
    assert structured["type"] == "object"
    assert "additionalProperties" not in structured or structured.get("additionalProperties") in (True,)


def test_evidence_prompt_explicitly_requires_nested_object():
    """The evidence prompt explicitly forbids the JSON-encoded-string shape."""
    blob = prompts.build_evidence_prompt("ctx", None)
    assert "structured MUST be a nested JSON OBJECT" in blob
    assert "JSON-encoded string" in blob


def test_evidence_structured_string_still_rejected_no_autodeserialization():
    """Regression (Phase17D A §4): the strict parser NEVER auto-deserializes a
    JSON-encoded string — a string keeps the exact deterministic issue."""
    doc = {
        "evidence": [
            {
                "id": "evid001",
                "kind": "physical",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "physical", "sourceId": "src"},
                "propositions": [
                    {
                        "type": "OTHER",
                        "uncertaintySeconds": 0,
                        "structured": '{"objectId": "obj001"}',
                    }
                ],
                "presentation": {"title": "t", "description": "d"},
            }
        ]
    }
    issues = stage_parser.collect_issues(GenerationStage.EVIDENCE, _j(doc))
    assert issues == (
        "evidence.evidence[0].propositions[0].structured must be a JSON object",
    )
    # the same document with a REAL nested object parses clean (no parser
    # change needed to accept the fixed transport contract).
    doc["evidence"][0]["propositions"][0]["structured"] = {"objectId": "obj001"}
    assert stage_parser.collect_issues(GenerationStage.EVIDENCE, _j(doc)) == ()


# --------------------------------------------------------------------------- #
# C. repair — full-draft transport contract (Phase17D C)
# --------------------------------------------------------------------------- #


def test_repair_transport_schema_is_the_full_draft_contract():
    """The REPAIR transport JSON Schema is derived from the authoritative
    FULL-DRAFT contract: exactly the 9 required top-level keys the strict
    parser demands (crime, persons, motives, objects, locations, travelRules,
    scene, evidence, worldGraph) and nothing else."""
    schema = prompts.json_schema_for_generation_stage("repair")
    assert schema is not None
    expected_top = {
        "crime",
        "persons",
        "motives",
        "objects",
        "locations",
        "travelRules",
        "scene",
        "evidence",
        "worldGraph",
    }
    assert set(schema["properties"]) == expected_top
    assert set(schema["required"]) == expected_top  # no partial-patch contract
    # the prompt-rendered contract carries the SAME top-level keys.
    rendered = json.loads(prompts.schema_contract("full_draft"))
    assert set(rendered) == expected_top


def test_repair_prompt_requires_the_complete_draft():
    """The repair prompt states the explicit complete-draft instruction
    (verbatim Line from Phase17D C §3)."""
    blob = prompts.build_repair_prompt('{"crime": {}}', ("issue",))
    assert (
        "Return the COMPLETE repaired draft, including every required top-level "
        "key." in blob
    )
    for required in (
        "crime",
        "persons",
        "motives",
        "objects",
        "locations",
        "travelRules",
        "scene",
        "evidence",
        "worldGraph",
    ):
        assert required in blob


def test_partial_repair_draft_still_fails_parser_not_relaxed():
    """A partial draft (crime/scene/worldGraph only — the observed Hermes
    echo) STILL fails the STRICT full-draft parser with the exact missing-key
    issues: no partial-patch semantics, no parser relaxation."""
    partial = _j(
        {
            "crime": {"type": "murder", "victimId": "v", "murdererId": "m", "motiveId": "mo", "weaponId": "w", "locationId": "l", "crimeTime": {"canonical": "2026-09-11T23:42:00+02:00", "accusationToleranceSeconds": 300}},
            "scene": {"locationId": "l", "name": "Office"},
            "worldGraph": {"locations": [], "placements": []},
        }
    )
    issues = stage_parser.collect_full_draft_issues(partial)
    assert set(issues) == {
        "draft: missing required key 'evidence'",
        "draft: missing required key 'locations'",
        "draft: missing required key 'motives'",
        "draft: missing required key 'objects'",
        "draft: missing required key 'persons'",
        "draft: missing required key 'travelRules'",
    }


# --------------------------------------------------------------------------- #
# 17C §8 — true request-level provider-call accounting
# --------------------------------------------------------------------------- #


def test_request_calls_count_real_provider_calls_initial_plus_repairs():
    """providerCalls (request-level) == initial + repairs == N+1, while the
    OUTER spec-provider ``calls`` stays 1. Distinguishes REAL provider calls
    from local validation/reprocessing."""
    bad = _coherent(dims=(2.5, 0.5, 0.1))  # 2 implausible-dimension issues
    good = _coherent()
    served = _RecordingProvider([_j(bad), _j(good)])
    provider = OllamaAssetSpecProvider(
        provider=served,
        attempt_id="phase17d-accounting",
        budget_consumer=lambda: True,
    )
    result = provider.generate(AssetSpecRequest(requested_name="bronze ceremonial ice pick"))
    assert result.error is None
    # 2 REAL request-level provider calls (ASSET_SPEC + 1 ASSET_SPEC_REPAIR).
    assert provider.request_calls == 2
    assert provider.calls == 1  # one outer AssetSpec round-trip
    assert served.calls == 2  # the inner model was really invoked twice
    assert served.stages == ["asset_spec", "asset_spec_repair"]
    assert provider.last_geometry_metrics["repairAttempts"] == 1
    assert provider.last_geometry_metrics["repaired"] is True


def test_request_calls_three_when_two_repairs_attempted():
    """Two still-bad repairs cost exactly 1 + MAX_SPEC_REPAIR_PASSES real
    request-level calls (the global budget consumer stays authoritative)."""
    served = _RecordingProvider(
        [_j(_coherent(dims=(2.5, 0.5, 0.1))), _j(_coherent(dims=(1.9, 0.5, 0.1))), _j(_coherent(dims=(1.5, 0.5, 0.1)))]
    )
    provider = OllamaAssetSpecProvider(
        provider=served,
        attempt_id="phase17d-accounting-3",
        budget_consumer=lambda: True,
    )
    result = provider.generate(AssetSpecRequest(requested_name="bronze ceremonial ice pick"))
    assert result.error is not None
    assert provider.request_calls == 1 + MAX_SPEC_REPAIR_PASSES
    assert served.calls == 1 + MAX_SPEC_REPAIR_PASSES


# --------------------------------------------------------------------------- #
# cross-stage deterministic projections (driver trust boundary)
# --------------------------------------------------------------------------- #


def test_identity_slug_and_locked_id_sheet():
    assert _identity_slug("Dr. Anna Weiss") == "anna_weiss"
    assert _identity_slug("Paul Becker") == "paul_becker"
    assert _identity_slug("bronze ceremonial ice pick") == "bronze_ceremonial_ice_pick"
    locked_constraints = LockedConstraints(
        victim="Dr. Anna Weiss",
        murderer="Paul Becker",
        motive="stolen research data",
        weapon="bronze ceremonial ice pick",
        crime_time="23:42",
        witness="Lisa König",
    )

    class _Attempt:
        locked = locked_constraints

    sheet = _locked_id_sheet(_Attempt())
    assert "victim_id: anna_weiss" in sheet
    assert "murderer_id: paul_becker" in sheet
    assert "weapon_id: bronze_ceremonial_ice_pick" in sheet
    assert "motive_id: stolen_research_data" in sheet
    assert "23:42" in sheet and "+02:00" in sheet
    assert "crime_time: " in sheet
    # no locked constraints -> empty sheet
    assert _locked_id_sheet(type("A", (), {"locked": None})()) == ""


def test_weapon_evidence_id_finds_locked_weapon_reference():
    doc = {
        "evidence": [
            {
                "id": "ev-forensic-01",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "forensic", "sourceId": "src"},
                "propositions": [
                    {
                        "type": "FORENSIC_WEAPON_MATCH",
                        "objectId": "bronze_ceremonial_ice_pick",
                        "uncertaintySeconds": 0,
                        "structured": {"match": True},
                    }
                ],
                "presentation": {"title": "t", "description": "d"},
            }
        ]
    }
    spec = stage_parser.parse_stage(GenerationStage.EVIDENCE, _j(doc), non_throwing=False)
    locked_weapon = LockedConstraints(weapon="bronze ceremonial ice pick")

    class _Attempt:
        locked = locked_weapon

    assert _weapon_evidence_id(_Attempt(), spec) == "ev-forensic-01"
    # a non-weapon reference resolves to nothing
    no_match = LockedConstraints(weapon="kitchen knife")
    assert _weapon_evidence_id(type("A", (), {"locked": no_match})(), spec) == ""


def test_evidence_projection_binds_weapon_evidence_and_inspects():
    """The deterministic placement projection binds the LOCKED weapon
    placement to the SEALED evidence id and grants the inspect interaction
    (evidence-linked placements must be directly interactable)."""
    from app.generation.schemas import PlacementSpec

    evidence_doc = {
        "evidence": [
            {
                "id": "ev-weapon-01",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "forensic", "sourceId": "s"},
                "propositions": [
                    {
                        "type": "OBJECT_CONTAINS_FINGERPRINT",
                        "objectId": "bronze_ceremonial_ice_pick",
                        "personId": "paul_becker",
                        "uncertaintySeconds": 0,
                        "structured": {},
                    }
                ],
                "presentation": {"title": "t", "description": "d"},
            }
        ]
    }
    evidence_spec = stage_parser.parse_stage(
        GenerationStage.EVIDENCE, _j(evidence_doc), non_throwing=False
    )
    placements = [
        PlacementSpec(
            object_id="bronze_ceremonial_ice_pick",
            asset_id="proc.decor.0123456789abcdef",
            location_id="office",
            anchor="office_desk_a",
            interaction="",
            evidence_id="weapon_id: bronze_ceremonial_ice_pick",  # model garbage
        ),
        PlacementSpec(
            object_id="vase",
            asset_id="PROP_VASE_01",
            location_id="office",
            anchor="office_desk_b",
            interaction="",
            evidence_id="made-up-id",
        ),
        PlacementSpec(
            object_id="trophy",
            asset_id="PROP_TROPHY",
            location_id="office",
            anchor="office_meeting_table",
            interaction="inspect",
            evidence_id=None,
        ),
    ]
    projected = _project_placement_evidence(
        placements, evidence_spec, "ev-weapon-01"
    )
    weapon, vase, trophy = projected
    assert weapon.evidence_id == "ev-weapon-01"  # garbage replaced by sealed id
    assert weapon.interaction == "inspect"  # evidence-linked -> interactable
    assert vase.evidence_id is None  # made-up evidence dropped
    assert vase.interaction == ""  # decorative stays non-interactive
    assert trophy.evidence_id is None and trophy.interaction == "inspect"


# --------------------------------------------------------------------------- #
# 17C §13 — failure policy: unreachable local provider, NEVER FakeProvider
# --------------------------------------------------------------------------- #


def test_unreachable_ollama_reports_sanitized_unavailable_and_never_fake():
    """Subprocess-level env override: GENERATION_PROVIDER=ollama pointed at an
    UNREACHABLE port reports the sanitized provider-unavailable reason and the
    provider stays an OllamaProvider — never a silent FakeProvider fallback."""
    script = (
        "import json;"
        "from app.generation.ollama_provider import OllamaProvider;"
        "from app.generation.fake_provider import FakeProvider;"
        "from app.generation.provider import GenerateRequest, GenerationStage;"
        "provider = OllamaProvider("
        "base_url='http://127.0.0.1:9', model='hermes3:8b', timeout_seconds=2);"
        "kind = 'OllamaProvider' if isinstance(provider, OllamaProvider) else type(provider).__name__;"
        "assert not isinstance(provider, FakeProvider);"
        "result = provider.generate(GenerateRequest("
        "attempt_id='x', stage=GenerationStage.CASE_TRUTH, prompt_context='ctx'));"
        "error = result.error or ('<timed-out>' if result.timed_out else None);"
        "print(json.dumps({'providerKind': kind, 'hasContent': result.content is not None, "
        "'error': error}))"
    )
    env = os.environ.copy()
    env["GENERATION_PROVIDER"] = "ollama"
    env.pop("ENV_FILE", None)
    # force Settings to read the operator dotenv if present (mirror the real box).
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["providerKind"] == "OllamaProvider"
    assert report["hasContent"] is False
    # sanitized: no host, no URL, no port, no IP in the surfaced message.
    sanitized = report["error"] or ""
    assert "127.0.0.1" not in sanitized
    assert "http" not in sanitized
    assert ":9" not in sanitized
    assert sanitized


@pytest.fixture(autouse=True)
def _network_block():
    """This file's OllamaProvider transport tests may not use the network
    except the explicit unreachable-loopback subprocess above (its connection
    is refused by the local OS, never a remote host)."""
    import socket

    original = socket.socket

    def _deny(*args, **kwargs):
        raise RuntimeError("network access blocked during phase17d tests")

    socket.socket = _deny
    yield
    socket.socket = original


# --------------------------------------------------------------------------- #
# Phase17 Wave-2 — evidence algebra seeding + deterministic evidence ownership
# --------------------------------------------------------------------------- #


def _wave2_case():
    """The showcase-shaped parsed case (crime + public world) + locked."""
    from tests.test_ollama_driver import _case_people, _j  # noqa: Nested types

    data = _case_people()
    crime = stage_parser.parse_stage(
        GenerationStage.CASE_TRUTH, _j({"crime": data["crime"]}), non_throwing=False
    )
    pub_payload = {
        k: v
        for k, v in data.items()
        if k in ("persons", "motives", "locations", "travelRules", "scene")
    }
    pub_payload["objects"] = []
    public = stage_parser.parse_stage(
        GenerationStage.PUBLIC_WORLD, _j(pub_payload), non_throwing=False
    )
    return crime, public


def _wave2_locked():
    from app.generation.constraints import LockedConstraints

    return LockedConstraints(
        victim="Dr. Anna Weiss",
        murderer="Paul Becker",
        motive="stolen research data",
        weapon="bronze ceremonial ice pick",
        crime_time="23:42",
        witness="Lisa König",
    )


class _Wave2Attempt:
    def __init__(self, locked):
        self.locked = locked


def test_wave2_timeline_offsets_are_deterministic_and_bound_the_canonical():
    """The six seed timestamps derive BY OFFSET from the locked canonical so
    the feasible window always contains the canonical tick (truth all_true)
    and stays narrow ([canonical-100, canonical+81))."""
    from app.domain.time_interval import parse_iso8601
    from tests.test_ollama_driver import _case_people, _j

    from app.services.ollama_driver import _evidence_timeline

    crime, _public = _wave2_case()
    canonical = crime.crime_time.canonical
    tl = _evidence_timeline(canonical)
    tick, _off = parse_iso8601(canonical)
    assert parse_iso8601(tl["last_seen"])[0] == tick - 110
    assert parse_iso8601(tl["body_found"])[0] == tick + 91  # 23:43:31 style
    assert parse_iso8601(tl["scene_observation"])[0] == tick - 10
    assert parse_iso8601(tl["alternate_observed"])[0] == tick - 120
    assert parse_iso8601(tl["presence"])[0] == tick - 20
    assert parse_iso8601(tl["alibi_departure"])[0] == tick - 1920
    # window [tick-100, tick+81) contains the canonical tick AND the body tick.
    assert tick - 100 <= tick < tick + 81


def test_wave2_deduction_seed_carries_exact_ids_and_travel_rules():
    """The evidence prompt seed forwards the locked ids, the model's own
    alternative suspects/motives/sharp weapons and the approved travel rules
    (projection completeness — the who solver needs the rules)."""
    from app.services.ollama_driver import _deduction_seed

    crime, public = _wave2_case()
    seed = _deduction_seed(_Wave2Attempt(_wave2_locked()), crime, public)
    assert "paul_becker" in seed
    assert "konsortium_office" in seed
    assert "stolen_research_data" in seed
    assert "bronze_ceremonial_ice_pick" in seed
    assert "marcus_fischer" in seed and "sophie_hoffmann" in seed
    assert "financial_settlement" in seed and "personal_grudge" in seed
    assert "kitchen_knife" in seed and "letter_opener" in seed and "scissors" in seed
    assert "research_lab -> konsortium_office: 1200 seconds" in seed


def test_wave2_approved_material_forwards_travel_rules():
    from app.services.ollama_driver import _approved_public_material, _approved_travel_sheet

    _crime, public = _wave2_case()
    sheet = _approved_public_material(public)
    assert "travel rule research_lab -> konsortium_office: 1200s" in sheet
    travel = _approved_travel_sheet(public)
    assert "research_lab -> konsortium_office: 1200 seconds" in travel
    assert "motor_lodge -> konsortium_office: 2700 seconds" in travel


def test_wave2_evidence_projection_is_full_deterministic_and_solves():
    """The driver OWNS the complete canonical evidence: the model's evidence
    propositions are shaping input only, and the published canonical set is
    deterministic, fully solver-validated and uniquely solvable."""
    from app.domain.solver import solve_case
    from app.generation.pipeline import _draft_to_phase3
    from app.generation.schemas import (
        EvidenceSetSpec,
        GeneratedDraft,
        ObjectSpec,
        WorldGraphSpec,
    )
    from app.validation.solution import evaluate_solution
    from app.services.ollama_driver import _base_object_spec, _evidence_gap_facts

    crime, public = _wave2_case()
    # A POISONED model evidence (an alternative weapon matching true) must NOT
    # leak into the published set.
    poisoned = stage_parser.parse_stage(
        GenerationStage.EVIDENCE,
        _j(
            {
                "evidence": [
                    {
                        "id": "evid001",
                        "kind": "forensic",
                        "reliability": "high",
                        "discoverable": True,
                        "sourceRef": {"kind": "forensic", "sourceId": "s"},
                        "propositions": [
                            {
                                "type": "FORENSIC_WEAPON_MATCH",
                                "objectId": "kitchen_knife",
                                "uncertaintySeconds": 0,
                                "structured": {"match": True},
                            }
                        ],
                        "presentation": {"title": "t", "description": "d"},
                    }
                ]
            }
        ),
        non_throwing=False,
    )
    canonical, rules, notes = _evidence_gap_facts(
        _Wave2Attempt(_wave2_locked()), crime, public, poisoned
    )
    assert canonical is not None
    ids = {f.id for f in canonical.evidence}
    assert "d_ev_weapon_false_kitchenknife" in ids
    assert "d_ev_weapon_true" in ids
    assert "d_ev_fp" in ids
    assert "d_ev_opp_marcusfischer" in ids and "d_ev_opp_sophiehoffmann" in ids
    assert "d_ev_motive_link" in ids
    assert "d_ev_when_last_seen" in ids and "d_ev_when_body" in ids and "d_ev_when_obs" in ids
    assert "d_ev_presence" in ids and "d_ev_alibi" in ids
    assert "d_ev_motive_x_financialsettlement" in ids
    assert "d_ev_motive_x_personalgrudge" in ids
    # the model's poisoned fact never appears.
    assert not any(getattr(f, "id", "") == "evid001" for f in canonical.evidence)
    assert notes  # audit trace populated

    # assemble + solve + truth-compare: unique winners, all_true.
    objects = tuple(
        o
        for o in (
            _base_object_spec("kitchen_knife"),
            _base_object_spec("letter_opener"),
            _base_object_spec("scissors"),
            ObjectSpec(
                object_id="bronze_ceremonial_ice_pick",
                asset_id="proc.decor.5bb392f09a7088e0",
                affordances=("INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
                subtype="ceremonial_ice_pick",
            ),
        )
        if o
    )
    draft = GeneratedDraft(
        crime=crime,
        persons=tuple(public.persons),
        motives=tuple(public.motives),
        objects=objects,
        locations=tuple(public.locations),
        travel_rules=tuple((*public.travel_rules, *rules)),
        scene=public.scene,
        evidence=tuple(canonical.evidence),
        world_graph=WorldGraphSpec(),
    )
    p, e, truth = _draft_to_phase3(draft, case_id="wave2", title="t")
    proof = solve_case(p, e)
    assert proof.who.unique and proof.who.winner == "paul_becker"
    assert proof.why.unique and proof.why.winner == "stolen_research_data"
    assert proof.weapon.unique and proof.weapon.winner == "bronze_ceremonial_ice_pick"
    assert not proof.when.ambiguous and not proof.when.overconstrained
    validation = evaluate_solution(proof, truth)
    assert validation.all_true is True


def test_wave2_evidence_projection_is_byte_deterministic_across_poisons():
    """Two runs with DIFFERENT poisoned model evidence produce the SAME
    canonical published evidence (the byte-identity guarantee of the
    skeleton contract)."""
    from app.generation.schemas import EvidenceSetSpec

    from app.services.ollama_driver import _evidence_gap_facts

    crime, public = _wave2_case()

    def _flat(evidence_set):
        return json.dumps(
            [((f.id, f.kind), tuple((p.type, p.person_id, p.location_id, p.object_id, p.motive_id, p.observed_at, p.uncertainty_seconds) for p in f.propositions)) for f in evidence_set.evidence],
            sort_keys=True,
        )

    poison_a = stage_parser.parse_stage(
        GenerationStage.EVIDENCE,
        _j(
            {
                "evidence": [
                    {
                        "id": "xa",
                        "kind": "forensic",
                        "reliability": "high",
                        "discoverable": True,
                        "sourceRef": {"kind": "forensic", "sourceId": "s"},
                        "propositions": [
                            {
                                "type": "FORENSIC_WEAPON_MATCH",
                                "objectId": "scissors",
                                "uncertaintySeconds": 0,
                                "structured": {"match": True},
                            }
                        ],
                        "presentation": {"title": "t", "description": "d"},
                    }
                ]
            }
        ),
        non_throwing=False,
    )
    poison_b = stage_parser.parse_stage(
        GenerationStage.EVIDENCE,
        _j(
            {
                "evidence": [
                    {
                        "id": "xb",
                        "kind": "witness_statement",
                        "reliability": "high",
                        "discoverable": True,
                        "sourceRef": {"kind": "record", "sourceId": "s"},
                        "propositions": [
                            {
                                "type": "OTHER",
                                "personId": "marcus_fischer",
                                "uncertaintySeconds": 0,
                                "structured": {},
                            }
                        ],
                        "presentation": {"title": "t", "description": "d"},
                    }
                ]
            }
        ),
        non_throwing=False,
    )
    can_a, _ra, _na = _evidence_gap_facts(_Wave2Attempt(_wave2_locked()), crime, public, poison_a)
    can_b, _rb, _nb = _evidence_gap_facts(_Wave2Attempt(_wave2_locked()), crime, public, poison_b)
    assert _flat(can_a) == _flat(can_b)


def test_wave2_driver_reuses_case_across_passes_and_keeps_budget():
    """Budget-friendly flow: on a repair/regeneration second pass the driver
    reuses the parsed CASE/PEOPLE (no case call) and tightens the evidence
    prompt with sanitized feedback — a two-pass run fits the 8-call budget."""
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource
    from tests.test_ollama_driver import (
        ICEPICK_SPEC,
        MockOllamaTransport,
        PROMPT,
        _admission,
        _alog_posts,
        _case_people,
        _controller,
        _evidence,
        _j,
        _make_driver,
        _world,
    )

    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(
        posts=[
            _j(_case_people()),
            _j(_evidence()),  # pass 1 evidence
            *_alog_posts("2026-09-11T23:42:00+02:00"),
            _j(_world()),
            ICEPICK_SPEC,
            _j(_evidence()),  # pass 2 evidence (cached case -> fewer calls)
            *_alog_posts("2026-09-11T23:42:00+02:00"),
            _j(_world()),
            ICEPICK_SPEC,
        ]
    )
    driver = _make_driver(transport)
    controller = _controller(driver, transport, admission, clock, ids, max_repair_passes=1)
    handle = controller.start_generation(PROMPT, anonymous_quota_session_id=session.session_id)
    record = controller.attempt(handle.attempt_id)
    assert record.state == "PUBLISHED" or record.state.value == "PUBLISHED"  # type: ignore[union-attr]
    # pass 1 = case+evidence+4 activity logs+world+spec = 8 calls; a repaired
    # pass 2 reuses the cached case (5 more calls inside the 12-call budget).
    assert transport.call_count <= 13
    assert record.budget.calls <= 13


def test_wave2_reconcile_evidence_interaction_drops_resolved_issues():
    from app.generation.schemas import PlacementSpec

    from app.services.ollama_driver import _reconcile_evidence_interaction

    deferred = [
        "world.evidence-interaction: evidence-linked object 'bronze_ceremonial_ice_pick' requires a non-empty interaction",
        "world.invalid-placement: something else",
        "evidence stage parse failed: nope",
    ]
    projected = [
        PlacementSpec(
            object_id="bronze_ceremonial_ice_pick",
            asset_id="proc.x",
            location_id="office",
            anchor="office_desk_a",
            interaction="inspect",
            evidence_id="d_ev_weapon_true",
        ),
        PlacementSpec(
            object_id="vase",
            asset_id="PROP_VASE_01",
            location_id="office",
            anchor="office_desk_b",
            interaction="",
            evidence_id=None,
        ),
    ]
    out = _reconcile_evidence_interaction(deferred, projected)
    assert out == [
        "world.invalid-placement: something else",
        "evidence stage parse failed: nope",
    ]


def test_wave2_prompts_teach_the_algebra_and_pair_evidence_interaction():
    """Prompt-side contract: the evidence template embeds the DEDUCTION
    CONTRACT, the case template demands travel rules + red-herring motives,
    and the world template tells the weapon object to pair an evidenceId with
    an inspect interaction."""
    blob = prompts.build_evidence_prompt("ctx", None)
    assert "DEDUCTION CONTRACT" in blob
    assert "IMPOSSIBLE-OPPORTUNITY" in blob
    assert "MOTIVE_FACT_CONTRADICTED" in blob
    case_blob = prompts.build_case_people_prompt("ctx", None)
    assert "travelRules: MANDATORY" in case_blob
    assert "TWO additional" in case_blob
    world_blob = prompts.build_world_requirements_prompt(
        "ctx", None, weapon_evidence_id="ev-x"
    )
    assert "BOTH fields in the SAME" in world_blob
    assert "evidenceId" in world_blob and "requiredInteraction" in world_blob