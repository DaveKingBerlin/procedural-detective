"""Phase43 — Frontier rendered-request hermetic contract tests (WAVE B).

These tests render the ACTUAL Frontier requests through ``pipeline.build_request``
and ``FrontierProvider.generate`` (with the module ``httpx`` seam mocked) and
assert the FINAL rendered request contents/schemas — not helper behavior.

Every external provider is a mock: zero network, zero paid calls, fully
hermetic and deterministic.
"""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.resolution import ResolutionType  # noqa: E402
from app.generation import parser as parser_mod  # noqa: E402
from app.generation import pipeline, prompts  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.frontier_provider import FrontierProvider  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_STAGE_PAYLOADS,
)

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "deepseek/deepseek-v4.1-flash"

_LOCKED_PROMPT = (
    "Victim: Laura Stein\n"
    "Murderer: Daniel Roth\n"
    "Motive: financial gain\n"
    "Weapon: kitchen knife\n"
    "Time: 20:15\n"
    "Witness: Nina Weber\n"
    "Location: apartment\n"
)

_CANONICAL_IDS = (
    "laura_stein",
    "daniel_roth",
    "financial_gain",
    "kitchen_knife",
    "apartment",
    "nina_weber",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


class _Wire:
    """Deterministic httpx.post stand-in recording every Frontier request body."""

    def __init__(self, content: str) -> None:
        from app.generation import frontier_provider as fp_mod

        self._fp_mod = fp_mod
        self._content = content
        self.bodies: list[dict] = []

    def install(self, monkeypatch) -> "_Wire":
        content = self._content

        class _Response:
            status_code = 200

            def iter_bytes(self, chunk_size: int):
                yield content.encode("utf-8")

        def _post(url, json=None, headers=None, timeout=None):
            self.bodies.append(dict(json or {}))
            return _Response()

        monkeypatch.setattr(self._fp_mod.httpx, "post", _post)
        return self

    @property
    def last(self) -> dict:
        return self.bodies[-1]


def _attempt(prompt: str = _LOCKED_PROMPT) -> pipeline.AttemptRecord:
    locked, note = pipeline.normalize_prompt(prompt, max_chars=4000)
    return pipeline.AttemptRecord(
        attempt_id="GA-p43",
        case_id="CASE-p43",
        session_id="QUOTA-p43",
        prompt=prompt,
        prompt_note=note,
        locked=locked,
    )


def _crime_doc(
    *,
    victim: str = "laura_stein",
    murderer: str | None = "daniel_roth",
    motive: str = "financial_gain",
    weapon: str = "kitchen_knife",
    location: str = "apartment",
    resolution: str = "HOMICIDE",
) -> dict:
    return {
        "crime": {
            "type": "murder",
            "victimId": victim,
            "murdererId": murderer,
            "motiveId": motive,
            "weaponId": weapon,
            "locationId": location,
            "crimeTime": {
                "canonical": "2026-09-11T20:15:00+02:00",
                "accusationToleranceSeconds": 300,
            },
            "resolutionType": resolution,
        }
    }


def _parse_stage(stage: GenerationStage, doc: str):
    parsed = parser_mod.parse_stage(stage, doc, non_throwing=False)
    assert parsed is not None
    return parsed


def _render(attempt, stage, wire: _Wire, *, diagnostics=()):
    request = pipeline.build_request(attempt, stage, diagnostics=diagnostics)
    provider = FrontierProvider(
        endpoint_url=ENDPOINT,
        api_key="P43-KEY",
        model=MODEL,
        timeout_seconds=30,
        clock=ManualClock(1_000_000.0),
        structured_output_mode="openai_json_schema",
    )
    result = provider.generate(request)
    assert result.content is not None
    return request, wire.last


def _message(body: dict) -> str:
    return body["messages"][0]["content"]


def _wire_schema(body: dict) -> dict:
    return body["response_format"]["json_schema"]["schema"]


# --------------------------------------------------------------------------- #
# P43-FR-03 / AC-01..03 — case_truth authoritative locked identity
# --------------------------------------------------------------------------- #


def test_case_truth_rendered_request_carries_canonical_locked_ids(monkeypatch):
    wire = _Wire("{}").install(monkeypatch)
    request, body = _render(_attempt(), GenerationStage.CASE_TRUTH, wire)
    content = _message(body)
    for canonical in _CANONICAL_IDS:
        assert f"canonicalId: {canonical}" in content, canonical
    assert "displayName" in content
    assert "never a legal id" in content

    # The transport schema narrows the crime reference ids to the canonical
    # locked ids — NEVER a display name.
    crime = _wire_schema(body)["properties"]["crime"]["properties"]
    assert "laura_stein" in crime["victimId"]["enum"]
    assert "Laura Stein" not in crime["victimId"]["enum"]
    assert "daniel_roth" in crime["murdererId"]["enum"]
    assert "Daniel Roth" not in crime["murdererId"]["enum"]
    assert None in crime["murdererId"]["enum"]  # SUICIDE null branch kept
    # request-facing diagnostic is the resolved canonical kit id.
    assert request.selected_environment_kit == "apartment"


def test_location_lock_renders_canonical_location_id(monkeypatch):
    wire = _Wire("{}").install(monkeypatch)
    request, body = _render(_attempt(), GenerationStage.CASE_TRUTH, wire)
    content = _message(body)
    assert "location:" in content
    assert "canonicalId: apartment" in content
    crime = _wire_schema(body)["properties"]["crime"]["properties"]
    assert crime["locationId"]["enum"] == ["apartment"]
    assert pipeline.selected_environment_kit(_attempt()) == "apartment"


# --------------------------------------------------------------------------- #
# DEF-062 / P43-FR-04 — case_truth narrows ONLY the roles that are locked
# --------------------------------------------------------------------------- #


def test_case_truth_fully_locked_enums_all_five(monkeypatch):
    wire = _Wire("{}").install(monkeypatch)
    _request, body = _render(_attempt(), GenerationStage.CASE_TRUTH, wire)
    crime = _wire_schema(body)["properties"]["crime"]["properties"]
    assert crime["victimId"]["enum"] == ["laura_stein"]
    assert crime["murdererId"]["enum"] == ["daniel_roth", None]
    assert crime["motiveId"]["enum"] == ["financial_gain"]
    assert crime["weaponId"]["enum"] == ["kitchen_knife"]
    assert crime["locationId"]["enum"] == ["apartment"]


def test_case_truth_victim_only_leaves_murderer_unconstrained(monkeypatch):
    """A ``Victim:``-only prompt must leave ``murdererId`` FREE so a freshly
    generated (unlocked) murderer is representable by a conforming provider."""
    attempt = _attempt(
        "Victim: Laura Stein\nMotive: financial gain\nWeapon: kitchen knife\n"
    )
    wire = _Wire("{}").install(monkeypatch)
    _request, body = _render(attempt, GenerationStage.CASE_TRUTH, wire)
    crime = _wire_schema(body)["properties"]["crime"]["properties"]
    assert crime["victimId"]["enum"] == ["laura_stein"]
    # murderer / location are NOT locked -> no enum (free string).
    assert "enum" not in crime.get("murdererId", {})
    assert "enum" not in crime.get("locationId", {})
    # locked motive/weapon are constrained; the unlocked murderer can be any id.
    assert crime["motiveId"]["enum"] == ["financial_gain"]
    assert crime["weaponId"]["enum"] == ["kitchen_knife"]
    assert crime["murdererId"]["type"] == ["string", "null"]


def test_case_truth_murderer_only_leaves_victim_unconstrained(monkeypatch):
    """A ``Murderer:``-only prompt must leave ``victimId`` FREE (no
    victim==murderer forcing)."""
    attempt = _attempt("Murderer: Daniel Roth\nMotive: financial gain\n")
    wire = _Wire("{}").install(monkeypatch)
    _request, body = _render(attempt, GenerationStage.CASE_TRUTH, wire)
    crime = _wire_schema(body)["properties"]["crime"]["properties"]
    assert "enum" not in crime.get("victimId", {})
    assert crime["murdererId"]["enum"] == ["daniel_roth", None]
    assert crime["motiveId"]["enum"] == ["financial_gain"]


# --------------------------------------------------------------------------- #
# P43-FR-03 — public_world reuses the immutable crime ids
# --------------------------------------------------------------------------- #


def test_public_world_reuses_exact_crime_ids(monkeypatch):
    attempt = _attempt()
    attempt.stage_outputs[GenerationStage.CASE_TRUTH] = _parse_stage(
        GenerationStage.CASE_TRUTH, json.dumps(_crime_doc())
    )
    wire = _Wire(GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD]).install(monkeypatch)
    _request, body = _render(attempt, GenerationStage.PUBLIC_WORLD, wire)
    content = _message(body)
    for line in (
        "victimId: laura_stein",
        "murdererId: daniel_roth",
        "motiveId: financial_gain",
        "weaponId: kitchen_knife",
        "locationId: apartment",
        "witnessId: nina_weber",
    ):
        assert line in content, line
    # No display-name-as-id substitution in the immutable block.
    assert "victimId: Laura Stein" not in content


# --------------------------------------------------------------------------- #
# P43-FR-04 / AC-05/06 — request-local reference enums
# --------------------------------------------------------------------------- #


def _attempt_with_prior_stages() -> pipeline.AttemptRecord:
    attempt = _attempt()
    attempt.stage_outputs[GenerationStage.CASE_TRUTH] = _parse_stage(
        GenerationStage.CASE_TRUTH, json.dumps(_crime_doc())
    )
    attempt.stage_outputs[GenerationStage.PUBLIC_WORLD] = _parse_stage(
        GenerationStage.PUBLIC_WORLD,
        GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
    )
    return attempt


def test_evidence_schema_enumerates_current_ids(monkeypatch):
    attempt = _attempt_with_prior_stages()
    public = attempt.stage_outputs[GenerationStage.PUBLIC_WORLD]
    request = pipeline.build_request(attempt, GenerationStage.EVIDENCE)
    props = request.json_schema["properties"]["evidence"]["items"]["properties"][
        "propositions"
    ]["items"]["properties"]
    person_ids = sorted({p.person_id for p in public.persons})
    location_ids = sorted({loc.location_id for loc in public.locations})
    object_ids = sorted({o.object_id for o in public.objects})
    assert set(props["personId"]["enum"]) == set(person_ids) | {
        "laura_stein",
        "daniel_roth",
        "nina_weber",
    }
    assert set(props["locationId"]["enum"]) >= set(location_ids)
    assert set(props["objectId"]["enum"]) >= set(object_ids)


def test_world_graph_schema_enumerates_current_ids(monkeypatch):
    attempt = _attempt_with_prior_stages()
    attempt.stage_outputs[GenerationStage.EVIDENCE] = _parse_stage(
        GenerationStage.EVIDENCE, GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE]
    )
    public = attempt.stage_outputs[GenerationStage.PUBLIC_WORLD]
    request = pipeline.build_request(attempt, GenerationStage.WORLD_GRAPH)
    placements = request.json_schema["properties"]["worldGraph"]["properties"][
        "placements"
    ]["items"]["properties"]
    object_ids = sorted({o.object_id for o in public.objects})
    assert set(placements["objectId"]["enum"]) == set(object_ids)
    evidence_ids = [e.id for e in attempt.stage_outputs[GenerationStage.EVIDENCE].evidence]
    assert None in placements["evidenceId"]["enum"]
    assert set(placements["evidenceId"]["enum"]) - {None} == set(evidence_ids)


def test_cross_universe_reference_excluded_before_canonical_application():
    attempt = _attempt_with_prior_stages()
    request = pipeline.build_request(attempt, GenerationStage.EVIDENCE)
    props = request.json_schema["properties"]["evidence"]["items"]["properties"][
        "propositions"
    ]["items"]["properties"]
    # An invented / cross-universe reference is NOT a legal enum value, so the
    # strict provider grammar rejects it BEFORE canonical parsing.
    assert "unknown_person_xyz" not in props["personId"]["enum"]
    assert "unknown_location_xyz" not in props["locationId"]["enum"]


# --------------------------------------------------------------------------- #
# P43-FR-07/08 / AC-08..10 — repair serialization + id preservation
# --------------------------------------------------------------------------- #


def test_repair_context_uses_camelcase_and_marks_locked_ids_immutable(monkeypatch):
    attempt = _attempt()
    attempt.draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
    assert attempt.draft is not None
    wire = _Wire(GOLDEN_FULL_DRAFT).install(monkeypatch)
    _request, body = _render(
        attempt, GenerationStage.REPAIR, wire, diagnostics=("WORLD_ASSET_UNRESOLVED",)
    )
    content = _message(body)
    for camel in ("personId", "motiveId", "objectId", "locationId", "worldGraph"):
        assert camel in content, camel
    for snake in ("person_id", "motive_id", "object_id", "location_id", "world_graph"):
        assert snake not in content, snake
    assert "IMMUTABLE" in content
    assert "AUTHORITATIVE LOCKED IDS" in content


def test_repair_renaming_unrelated_id_is_rejected():
    attempt = _attempt()
    draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    attempt.draft = draft
    # Snapshot exactly what build_request records, then authorize ONLY evidence.
    attempt._repair_pre_ids = pipeline.current_declaration_ids(draft)
    attempt._repair_authorized_sections = frozenset({"evidence"})

    raw = json.loads(GOLDEN_FULL_DRAFT)
    # A NON-locked declaration id in a section outside the authorized scope.
    victim = next(o for o in raw["objects"] if o["objectId"] == "letter_opener")
    victim["objectId"] = "renamed_unrelated_object"
    issues = pipeline.apply_repair(attempt, json.dumps(raw))

    assert any(issue.startswith("repair id churn:") for issue in issues), issues
    # The unauthorized rename was REJECTED — the pre-existing draft is kept.
    assert attempt.draft is draft
    assert any(o.object_id == "letter_opener" for o in attempt.draft.objects)


def test_repair_fixing_only_the_defect_still_applies():
    """A repair that changes NO declaration id applies exactly as before."""
    attempt = _attempt()
    draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    attempt.draft = draft
    attempt._repair_pre_ids = pipeline.current_declaration_ids(draft)
    attempt._repair_authorized_sections = frozenset({"evidence"})

    issues = pipeline.apply_repair(attempt, GOLDEN_FULL_DRAFT)
    assert issues == ()
    assert attempt.draft is not draft  # replaced by the (identical-id) repair
    assert pipeline.current_declaration_ids(attempt.draft) == attempt._repair_pre_ids


# --------------------------------------------------------------------------- #
# PROVEN live Easy-case regression — a repair that ADDS the missing entities
# must APPLY (rejecting additions discarded every repair pass: repairs stayed
# UNCHANGED forever -> REPAIR_BUDGET_EXHAUSTED).
# --------------------------------------------------------------------------- #


def _easy_partial_draft_doc() -> dict:
    """A full draft that PARSES but declares only the victim person.

    The locked murderer has no public person, so the derived defect set is
    exactly the live Easy-case failure: ``FINAL_TARGET_UNIVERSE_EMPTY`` (no
    final homicide target) plus ``STRUCTURED_OUTPUT_INVALID`` (the crime
    murdererId does not resolve) plus ``VALIDATION_FAILED``.
    """
    return {
        "crime": _crime_doc()["crime"],
        "persons": [
            {
                "personId": "laura_stein",
                "name": "Laura Stein",
                "role": "victim",
                "affordances": ["VISIBLE_CHARACTER"],
            },
        ],
        "motives": [
            {
                "motiveId": "financial_gain",
                "label": "Financial gain",
                "affordances": ["MOTIVE_CANDIDATE"],
            },
        ],
        "objects": [
            {
                "objectId": "kitchen_knife",
                "assetId": "PROP_KITCHEN_KNIFE_01",
                "affordances": ["POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"],
            },
        ],
        "locations": [{"locationId": "apartment", "name": "Apartment"}],
        "travelRules": [
            {
                "fromLocationId": "apartment",
                "toLocationId": "apartment",
                "travelTimeSeconds": 0,
            },
        ],
        "scene": {"locationId": "apartment", "name": "Apartment"},
        "evidence": [
            {
                "id": "forensic_knife_01",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "object", "sourceId": "kitchen_knife"},
                "propositions": [
                    {
                        "type": "FORENSIC_WEAPON_MATCH",
                        "objectId": "kitchen_knife",
                        "structured": {"match": True},
                        "uncertaintySeconds": 0,
                    },
                ],
                "presentation": {
                    "title": "Forensic knife report",
                    "description": "Knife match.",
                },
            },
        ],
        "worldGraph": {
            "locations": [
                {
                    "locationId": "apartment",
                    "template": "apartment",
                    "rooms": ["bedroom"],
                },
            ],
            "placements": [
                {
                    "objectId": "kitchen_knife",
                    "assetId": "PROP_KITCHEN_KNIFE_01",
                    "locationId": "apartment",
                    "anchor": "kitchen_counter",
                    "interaction": "inspect",
                    "evidenceId": "forensic_knife_01",
                },
            ],
        },
    }


def _easy_corrected_draft_doc() -> dict:
    """The SAME draft after a legitimate full-draft repair that ADDS the missing
    locked murderer person, a red-herring suspect and the required
    witness-statement evidence — WITHOUT removing or renaming any existing
    declaration id."""
    doc = _easy_partial_draft_doc()
    doc["persons"] = doc["persons"] + [
        {
            "personId": "daniel_roth",
            "name": "Daniel Roth",
            "role": "suspect",
            "affordances": ["VISIBLE_CHARACTER", "SUSPECT_ELIGIBLE"],
        },
        {
            "personId": "michael_chen",
            "name": "Michael Chen",
            "role": "suspect",
            "affordances": ["VISIBLE_CHARACTER", "SUSPECT_ELIGIBLE"],
        },
        {
            "personId": "nina_weber",
            "name": "Nina Weber",
            "role": "witness",
            "affordances": ["VISIBLE_CHARACTER"],
        },
    ]
    doc["evidence"] = doc["evidence"] + [
        {
            "id": "witness_statement_nina_01",
            "kind": "witness_statement",
            "reliability": "medium",
            "discoverable": True,
            "sourceRef": {"kind": "person", "sourceId": "nina_weber"},
            "propositions": [
                {
                    "type": "WITNESS_CLAIMS",
                    "personId": "nina_weber",
                    "structured": {"statement": "I saw someone leave at 20:10."},
                    "uncertaintySeconds": 0,
                },
            ],
            "presentation": {
                "title": "Nina Weber statement",
                "description": "I saw someone leave.",
                "speakerName": "Nina Weber",
                "statement": "I saw someone leave at 20:10.",
            },
        },
    ]
    return doc


def test_repair_adding_missing_declarations_is_applied_not_rejected():
    """PROVEN live Easy-case failure class (hermetic).

    The full draft parses but has no final homicide target and an unresolved
    crime murdererId. A legitimate full-draft repair that ADDS the missing
    entities (no rename/removal) must APPLY. Rejecting additions discarded the
    ENTIRE repair draft on every pass, so repairs were classified UNCHANGED
    forever and the attempt ended REPAIR_BUDGET_EXHAUSTED.
    """
    from app.generation import validation_codes

    attempt = _attempt()
    broken = parser_mod.parse_full_draft(json.dumps(_easy_partial_draft_doc()))
    assert broken is not None
    attempt.draft = broken

    report = pipeline.validate_draft(attempt)
    assert validation_codes.validation_failure_codes(report) == (
        "FINAL_TARGET_UNIVERSE_EMPTY",
        "STRUCTURED_OUTPUT_INVALID",
        "VALIDATION_FAILED",
    ), validation_codes.validation_failure_codes(report)
    assert "FINAL_TARGET_UNIVERSE_EMPTY" in report.quality_issues
    assert report.outcome is not None

    # Controller-equivalent scope snapshot: a structural defect derives the
    # UNKNOWN full-rerun owner, so NO declaration section is "authorized".
    pipeline.build_request(attempt, GenerationStage.REPAIR)
    assert attempt._repair_authorized_sections == frozenset()

    issues = pipeline.apply_repair(attempt, json.dumps(_easy_corrected_draft_doc()))
    assert issues == (), issues
    # The repair APPLIED: the draft was replaced and now declares the added
    # entities. Rejecting the additions kept the broken draft (UNCHANGED).
    assert attempt.draft is not broken
    person_ids = {p.person_id for p in attempt.draft.persons}
    assert {"daniel_roth", "michael_chen", "nina_weber"} <= person_ids
    evidence_ids = {e.id for e in attempt.draft.evidence}
    assert "witness_statement_nina_01" in evidence_ids


# --------------------------------------------------------------------------- #
# PROVEN live Easy-case guidance gaps — the generic Frontier contexts must
# carry the canonical case-quality / affordance guidance the Ollama path has.
# --------------------------------------------------------------------------- #


def test_rendered_evidence_context_requires_witness_statement():
    """The live Easy case failed pass 0 on WITNESS_STATEMENT_MISSING because the
    generic evidence context never told the model each public witness needs a
    witness_statement evidence item. The rendered request MUST carry that
    canonical rule."""
    attempt = _attempt()
    attempt.stage_outputs[GenerationStage.CASE_TRUTH] = _parse_stage(
        GenerationStage.CASE_TRUTH, json.dumps(_crime_doc())
    )
    attempt.stage_outputs[GenerationStage.PUBLIC_WORLD] = _parse_stage(
        GenerationStage.PUBLIC_WORLD,
        json.dumps(
            {
                "persons": [
                    {"personId": "laura_stein", "name": "Laura Stein",
                     "role": "victim", "affordances": ["VISIBLE_CHARACTER"]},
                    {"personId": "daniel_roth", "name": "Daniel Roth",
                     "role": "suspect",
                     "affordances": ["VISIBLE_CHARACTER", "SUSPECT_ELIGIBLE"]},
                    {"personId": "nina_weber", "name": "Nina Weber",
                     "role": "witness", "affordances": ["VISIBLE_CHARACTER"]},
                ],
                "motives": [
                    {"motiveId": "financial_gain", "label": "Financial gain",
                     "affordances": ["MOTIVE_CANDIDATE"]},
                ],
                "objects": [
                    {"objectId": "kitchen_knife",
                     "assetId": "PROP_KITCHEN_KNIFE_01",
                     "affordances": ["POTENTIAL_WEAPON"]},
                ],
                "locations": [{"locationId": "apartment", "name": "Apartment"}],
                "travelRules": [],
                "scene": {"locationId": "apartment", "name": "Apartment"},
            }
        ),
    )
    request = pipeline.build_request(attempt, GenerationStage.EVIDENCE)
    context = request.prompt_context
    assert "witness_statement" in context
    assert "WITNESS_CLAIMS proposition referencing that witness" in context


def test_rendered_public_world_context_requires_affordance_contract():
    """A captured live sample emitted motives/objects with only
    VISIBLE_CHARACTER (motive/weapon universe empty). The rendered public_world
    context MUST state the exact affordance contract the universes derive from."""
    attempt = _attempt()
    attempt.stage_outputs[GenerationStage.CASE_TRUTH] = _parse_stage(
        GenerationStage.CASE_TRUTH, json.dumps(_crime_doc())
    )
    request = pipeline.build_request(attempt, GenerationStage.PUBLIC_WORLD)
    context = request.prompt_context
    assert "AFFORDANCE CONTRACT" in context
    assert "MOTIVE_CANDIDATE" in context
    assert "POTENTIAL_WEAPON" in context


# --------------------------------------------------------------------------- #
# P43-FR-09 / AC-11 — regeneration preserves locked identity
# --------------------------------------------------------------------------- #


def test_regeneration_rerenders_same_locked_ids(monkeypatch):
    from app.generation.controller import GenerationController

    wire = _Wire("{}").install(monkeypatch)
    attempt = _attempt()
    first_request, first_body = _render(attempt, GenerationStage.CASE_TRUTH, wire)

    # Populate generated state + a stale repair snapshot, then run the REAL
    # controller regeneration reset.
    attempt.stage_outputs[GenerationStage.CASE_TRUTH] = _parse_stage(
        GenerationStage.CASE_TRUTH, json.dumps(_crime_doc())
    )
    attempt.stage_done = {GenerationStage.CASE_TRUTH}
    attempt.draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
    attempt._repair_pre_ids = {"persons": ("x",)}
    attempt._repair_authorized_sections = frozenset({"persons"})
    GenerationController._reset_for_regeneration(
        types.SimpleNamespace(_driver=None), attempt
    )
    assert attempt._repair_pre_ids is None
    assert attempt._repair_authorized_sections is None

    second_request, second_body = _render(attempt, GenerationStage.CASE_TRUTH, wire)
    assert first_body["response_format"] == second_body["response_format"]
    assert _message(first_body) == _message(second_body)
    assert first_request.selected_environment_kit == second_request.selected_environment_kit
    for canonical in _CANONICAL_IDS:
        assert f"canonicalId: {canonical}" in _message(second_body)


# --------------------------------------------------------------------------- #
# P43-FR-05 / AC-13 — SUICIDE representable + accepted by the full parse path
# --------------------------------------------------------------------------- #


def test_suicide_resolution_accepted_by_full_parse_path():
    doc = json.dumps(_crime_doc(murderer=None, resolution="SUICIDE"))
    parsed = _parse_stage(GenerationStage.CASE_TRUTH, doc)
    assert parsed.resolution_type is ResolutionType.SUICIDE
    assert parsed.murderer_id is None
    assert parser_mod.collect_issues(GenerationStage.CASE_TRUTH, doc) == ()

    # The rendered transport schema can express it natively.
    schema = prompts.json_schema_for_stage_output("case_truth")
    crime = schema["properties"]["crime"]["properties"]
    assert "null" in crime["murdererId"]["type"]
    assert set(crime["resolutionType"]["enum"]) == {"HOMICIDE", "SUICIDE"}

    # The full stage-application + validation path does not crash and the
    # suicide crime is accepted structurally.
    attempt = _attempt()
    pipeline.apply_stage_output(attempt, GenerationStage.CASE_TRUTH, doc)
    report = pipeline.validate_draft(attempt)
    assert report is not None
    assert attempt.stage_outputs[GenerationStage.CASE_TRUTH].resolution_type is (
        ResolutionType.SUICIDE
    )


# --------------------------------------------------------------------------- #
# P43-FR-10 / AC-07 — exactly ONE selected kit + kit-scoped anchors
# --------------------------------------------------------------------------- #


def _has_id_token(text: str, token: str) -> bool:
    """True when ``token`` appears as a standalone id token (not a substring of
    a longer id such as ``office_document_01``)."""
    return re.search(
        r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", text
    ) is not None


def test_world_graph_renders_one_kit_and_kit_scoped_anchors(monkeypatch):
    attempt = _attempt()
    attempt.stage_outputs[GenerationStage.CASE_TRUTH] = _parse_stage(
        GenerationStage.CASE_TRUTH, json.dumps(_crime_doc())
    )
    wire = _Wire("{}").install(monkeypatch)
    request, body = _render(attempt, GenerationStage.WORLD_GRAPH, wire)

    assert pipeline.selected_environment_kit(attempt) == "apartment"
    assert request.selected_environment_kit == "apartment"
    block = pipeline._authoritative_world_graph_block(attempt)
    assert "selectedEnvironmentKit: apartment" in block
    apartment_anchors = dict(prompts._kit_anchor_table())["apartment"]
    for anchor in apartment_anchors:
        assert anchor in block, anchor

    # DEF-063 (ADV-43-05) — the FULL rendered request message (not only the
    # authoritative block) contains the selected kit's anchors and NO anchor
    # exclusive to a different kit.
    content = _message(body)
    assert "SELECTED ENVIRONMENT KIT" in content
    for anchor in apartment_anchors:
        assert _has_id_token(content, anchor), anchor
    apartment_set = set(apartment_anchors)
    for kit_id, kit_anchors in prompts._kit_anchor_table():
        if kit_id == "apartment":
            continue
        for anchor in kit_anchors:
            if anchor not in apartment_set:
                assert not _has_id_token(content, anchor), (kit_id, anchor)
    assert "kit 'office'" not in content
    assert "kit 'warehouse'" not in content


# --------------------------------------------------------------------------- #
# DEF-060 / ADV-43-10 — closed canonical enums cannot silently regress
# --------------------------------------------------------------------------- #


def test_rendered_schemas_assert_closed_canonical_enums(monkeypatch):
    """An enum-only regression (a dropped/incorrect canonical vocabulary) must
    fail loudly: assert the closed enums explicitly on the RENDERED schemas."""
    wire = _Wire("{}").install(monkeypatch)

    # case_truth — resolutionType.
    _request, body = _render(_attempt(), GenerationStage.CASE_TRUTH, wire)
    crime = _wire_schema(body)["properties"]["crime"]["properties"]
    assert set(crime["resolutionType"]["enum"]) == {"HOMICIDE", "SUICIDE"}

    # public_world — affordance vocabulary + scene.locationId reference enum.
    attempt = _attempt_with_prior_stages()
    _request, body = _render(attempt, GenerationStage.PUBLIC_WORLD, wire)
    props = _wire_schema(body)["properties"]
    affordances = props["persons"]["items"]["properties"]["affordances"]["items"]["enum"]
    assert set(affordances) == set(prompts.AFFORDANCE_VOCABULARY)
    scene_location_ids = props["scene"]["properties"]["locationId"]["enum"]
    assert scene_location_ids
    assert "apartment" in scene_location_ids

    # evidence — reliability + proposition type closed vocabularies.
    _request, body = _render(attempt, GenerationStage.EVIDENCE, wire)
    ev_props = _wire_schema(body)["properties"]["evidence"]["items"]["properties"]
    assert set(ev_props["reliability"]["enum"]) == set(prompts.RELIABILITY_VOCABULARY)
    proposition = ev_props["propositions"]["items"]["properties"]
    assert set(proposition["type"]["enum"]) == set(prompts.PROPOSITION_TYPES)
    assert proposition["personId"]["enum"]

    # world_graph — anchor + assetId closed enums.
    attempt.stage_outputs[GenerationStage.EVIDENCE] = _parse_stage(
        GenerationStage.EVIDENCE, GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE]
    )
    _request, body = _render(attempt, GenerationStage.WORLD_GRAPH, wire)
    placements = _wire_schema(body)["properties"]["worldGraph"]["properties"][
        "placements"
    ]["items"]["properties"]
    assert set(placements["anchor"]["enum"]) == set(
        dict(prompts._kit_anchor_table())["apartment"]
    )
    assert set(placements["assetId"]["enum"]) == set(prompts._composition_asset_ids())


# --------------------------------------------------------------------------- #
# DEF-064 / ADV-43-02 — repair schema narrows the known reference universes
# --------------------------------------------------------------------------- #


def test_repair_schema_narrows_known_reference_fields():
    attempt = _attempt()
    attempt.draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
    request = pipeline.build_request(attempt, GenerationStage.REPAIR)
    props = request.json_schema["properties"]

    placements = props["worldGraph"]["properties"]["placements"]["items"]["properties"]
    for field in ("objectId", "locationId", "assetId", "anchor"):
        assert placements[field]["enum"], field
    assert None in placements["evidenceId"]["enum"]

    wg_locations = props["worldGraph"]["properties"]["locations"]["items"]["properties"]
    assert wg_locations["locationId"]["enum"]

    proposition = props["evidence"]["items"]["properties"]["propositions"]["items"][
        "properties"
    ]
    for field in ("personId", "locationId", "objectId", "motiveId"):
        assert proposition[field]["enum"], field

    # Declaration-defining arrays stay FREE (red-herrings remain creatable).
    assert "enum" not in props["persons"]["items"]["properties"]["personId"]
    assert "enum" not in props["motives"]["items"]["properties"]["motiveId"]
    assert "enum" not in props["objects"]["items"]["properties"]["objectId"]
    assert "enum" not in props["locations"]["items"]["properties"]["locationId"]
    assert "enum" not in props["evidence"]["items"]["properties"]["id"]


# --------------------------------------------------------------------------- #
# DEF-061 — the generic REPAIR context carries the Phase 41A rules
# --------------------------------------------------------------------------- #


def test_repair_context_contains_phase41a_quality_rules(monkeypatch):
    attempt = _attempt()
    attempt.draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
    wire = _Wire(GOLDEN_FULL_DRAFT).install(monkeypatch)
    _request, body = _render(
        attempt,
        GenerationStage.REPAIR,
        wire,
        diagnostics=("WORLD_ASSET_UNRESOLVED",),
    )
    content = _message(body)
    assert "PHASE 41A QUALITY RULES" in content
    for marker in ("SUSPECT_ELIGIBLE", "WITNESS_CLAIMS", "server-owned"):
        assert marker in content, marker


# --------------------------------------------------------------------------- #
# DEF-065 / DEF-066 / ADV-43-03/04 — repair id churn (remove + add)
# --------------------------------------------------------------------------- #


def test_structural_unknown_owner_repair_cannot_rename_declaration():
    """A REAL structural report derives an EMPTY authorized scope (UNKNOWN
    owner), so renaming an unrelated declaration id is rejected."""
    attempt = _attempt()
    raw = json.loads(GOLDEN_FULL_DRAFT)
    raw["crime"]["murdererId"] = "missing_person_zzz"
    broken = parser_mod.parse_full_draft(json.dumps(raw, sort_keys=True))
    assert broken is not None
    attempt.draft = broken

    report = pipeline.validate_draft(attempt)
    assert report.structural_issues, "expected a real structural defect"
    # The scope is DERIVED from the real report, not hand-set.
    assert pipeline._authorized_repair_sections(attempt) == frozenset()

    pipeline.build_request(attempt, GenerationStage.REPAIR)
    assert attempt._repair_authorized_sections == frozenset()

    target = next(o for o in raw["objects"] if o["objectId"] == "letter_opener")
    target["objectId"] = "renamed_unrelated_object"
    issues = pipeline.apply_repair(attempt, json.dumps(raw, sort_keys=True))
    assert any(issue.startswith("repair id churn:") for issue in issues), issues
    assert attempt.draft is broken
    assert any(o.object_id == "letter_opener" for o in attempt.draft.objects)


def test_repair_added_fabricated_declaration_id_is_rejected():
    """DEF-066 — an ADDED declaration id outside the authorized scope is churn,
    even when nothing was removed."""
    attempt = _attempt()
    draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    attempt.draft = draft
    attempt._repair_pre_ids = pipeline.current_declaration_ids(draft)
    attempt._repair_authorized_sections = frozenset({"evidence"})

    raw = json.loads(GOLDEN_FULL_DRAFT)
    fabricated = dict(raw["objects"][0])
    fabricated["objectId"] = "fabricated_object_zzz"
    raw["objects"].append(fabricated)
    issues = pipeline.apply_repair(attempt, json.dumps(raw, sort_keys=True))

    assert any(
        issue.startswith("repair id churn:") and "fabricated_object_zzz" in issue
        for issue in issues
    ), issues
    # Rejected: the pre-existing draft is kept unchanged.
    assert attempt.draft is draft
    assert all(o.object_id != "fabricated_object_zzz" for o in attempt.draft.objects)


# --------------------------------------------------------------------------- #
# Phase43A — generic PUBLIC_WORLD breadth + WORLD_GRAPH placement discipline
# --------------------------------------------------------------------------- #


def test_public_world_breadth_rules_constant_has_the_explicit_contract():
    """The provider-neutral constant teaches the exact world cardinality the
    Ollama ``_CASE_FIELD_RULES`` already carries (and which the live Easy
    PUBLIC_WORLD pass under-provisioned)."""
    block = prompts.PUBLIC_WORLD_BREADTH_RULES
    assert "PUBLIC WORLD BREADTH RULES" in block
    assert "at least TWO RED-HERRING suspects" in block
    assert "SUSPECT_ELIGIBLE" in block
    assert "witness" in block
    assert "at least TWO additional" in block  # red-herring motives
    assert "MOTIVE_CANDIDATE" in block
    assert "at least TWO OTHER" in block  # other locations
    assert "travelRules: MANDATORY" in block
    assert "travelTimeSeconds >= 1200" in block
    assert "FINAL HOMICIDE TARGET" in block


def test_rendered_public_world_context_requires_world_breadth_rules():
    """The rendered generic public_world context (the ACTUAL model-visible
    request) carries the breadth rules — not just the constant."""
    attempt = _attempt()
    attempt.stage_outputs[GenerationStage.CASE_TRUTH] = _parse_stage(
        GenerationStage.CASE_TRUTH, json.dumps(_crime_doc())
    )
    request = pipeline.build_request(attempt, GenerationStage.PUBLIC_WORLD)
    context = request.prompt_context
    assert prompts.PUBLIC_WORLD_BREADTH_RULES in context
    assert "PUBLIC WORLD BREADTH RULES" in context
    assert "at least TWO RED-HERRING suspects" in context
    assert "at least TWO additional" in context
    assert "at least TWO OTHER" in context
    assert "travelRules: MANDATORY" in context
    assert "travelTimeSeconds >= 1200" in context


def test_public_world_context_rendering_is_deterministic():
    """Rendering the same attempt twice yields byte-identical context (the
    breadth rules add no time/randomness)."""
    attempt = _attempt()
    attempt.stage_outputs[GenerationStage.CASE_TRUTH] = _parse_stage(
        GenerationStage.CASE_TRUTH, json.dumps(_crime_doc())
    )
    first = pipeline._authoritative_public_world_block(attempt)
    second = pipeline._authoritative_public_world_block(attempt)
    assert first == second
    assert prompts.PUBLIC_WORLD_BREADTH_RULES in first


def test_repair_context_carries_world_breadth_rules():
    """A full-draft REPAIR must be able to restore an under-provisioned world,
    so the repair context carries the SAME breadth rules."""
    attempt = _attempt()
    attempt.draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
    request = pipeline.build_request(attempt, GenerationStage.REPAIR)
    assert prompts.PUBLIC_WORLD_BREADTH_RULES in request.prompt_context


def test_rendered_world_graph_context_requires_placement_discipline():
    """The live Easy WORLD_GRAPH emitted duplicate objectId placements. The
    rendered generic world_graph context MUST state the placement discipline."""
    attempt = _attempt_with_prior_stages()
    attempt.stage_outputs[GenerationStage.EVIDENCE] = _parse_stage(
        GenerationStage.EVIDENCE, GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE]
    )
    request = pipeline.build_request(attempt, GenerationStage.WORLD_GRAPH)
    context = request.prompt_context
    block = pipeline._authoritative_world_graph_block(attempt)
    for text in (context, block):
        assert "PLACEMENT DISCIPLINE" in text
        assert "AT MOST ONCE" in text
        assert "MINIMAL" in text
        assert "duplicate" in text
        assert "ONLY the objects the case declares" in text


def test_world_graph_placement_discipline_is_deterministic():
    attempt = _attempt_with_prior_stages()
    attempt.stage_outputs[GenerationStage.EVIDENCE] = _parse_stage(
        GenerationStage.EVIDENCE, GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE]
    )
    first = pipeline._authoritative_world_graph_block(attempt)
    second = pipeline._authoritative_world_graph_block(attempt)
    assert first == second
    assert "PLACEMENT DISCIPLINE" in first