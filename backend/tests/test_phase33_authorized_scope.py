"""Phase33 RAD-2 — focused POSITIVE proofs that the now-supplied registry ids,
allowlisted anchors and the locked-witness contract are sufficient to pass the
(byte-identical) validators, and that the model-facing contexts actually carry
the authoritative scope.

Complements ``test_phase33_content_failures.py`` (the RAD-1 red regressions):
every rejection-semantics test there stays green unchanged; these tests prove
the *authoritative trusted context* is now present and sufficient. Deterministic
fixtures only — no provider bodies, no CaseTruth.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation import pipeline, prompts  # noqa: E402
from app.generation.constraints import LockedConstraints  # noqa: E402
from app.generation.parser import parse_full_draft  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.safety import (  # noqa: E402
    ANCHOR_ALLOWLIST,
    INTERACTION_ALLOWLIST,
    AssetRegistry,
    validate_asset_reference,
    validate_world_graph,
)
from app.generation.schemas import (  # noqa: E402
    PlacementSpec,
    WorldGraphSpec,
    WorldGraphLocationSpec,
)

_CANONICAL_TIME = "2026-09-11T22:17:00+02:00"


def _draft_doc(*, asset_id: str = "PROP_KITCHEN_KNIFE_01", anchor: str = "desk_main") -> dict:
    return {
        "crime": {
            "type": "murder",
            "victimId": "victim_sarah",
            "murdererId": "suspect_thomas",
            "motiveId": "motive_greed",
            "weaponId": "obj_knife",
            "locationId": "loc_office",
            "crimeTime": {"canonical": _CANONICAL_TIME, "accusationToleranceSeconds": 120},
        },
        "persons": [
            {
                "personId": "victim_sarah",
                "name": "Sarah Miller",
                "role": "victim",
                "affordances": ["VISIBLE_CHARACTER", "INSPECTABLE"],
            },
            {
                "personId": "suspect_thomas",
                "name": "Thomas Reed",
                "role": "suspect",
                "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER", "INSPECTABLE"],
            },
            {
                "personId": "suspect_gina",
                "name": "Gina Costa",
                "role": "suspect",
                "affordances": ["SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER", "INSPECTABLE"],
            },
        ],
        "motives": [
            {"motiveId": "motive_greed", "label": "Greed", "affordances": ["MOTIVE_CANDIDATE"]},
            {"motiveId": "motive_revenge", "label": "Revenge", "affordances": ["MOTIVE_CANDIDATE"]},
        ],
        "objects": [
            {
                "objectId": "obj_knife",
                "assetId": asset_id,
                "affordances": ["POTENTIAL_WEAPON", "INSPECTABLE"],
            }
        ],
        "locations": [
            {"locationId": "loc_office", "name": "Office"},
            {"locationId": "loc_lobby", "name": "Lobby"},
        ],
        "travelRules": [
            {
                "fromLocationId": "loc_lobby",
                "toLocationId": "loc_office",
                "travelTimeSeconds": 1800,
            }
        ],
        "scene": {"locationId": "loc_office", "name": "Office"},
        "evidence": [],
        "worldGraph": {
            "locations": [
                {"locationId": "loc_office", "template": "kitchen_template", "rooms": ["kitchen"]}
            ],
            "placements": [
                {
                    "objectId": "obj_knife",
                    "assetId": asset_id,
                    "locationId": "loc_office",
                    "anchor": anchor,
                    "interaction": "inspect",
                    "evidenceId": None,
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# registry-backed identifiers
# ---------------------------------------------------------------------------


def test_phase33_registry_asset_ids_are_valid_references():
    """Every id in ``AssetRegistry.ASSET_IDS`` (the vocabulary now supplied to
    the model) is accepted by the authoritative reference validator."""
    assert AssetRegistry.ASSET_IDS
    for asset_id in sorted(AssetRegistry.ASSET_IDS):
        assert validate_asset_reference(asset_id) == (), asset_id


def test_phase33_registry_asset_ids_pass_world_graph():
    """A placement using each registry asset id plus a legal anchor passes
    ``validate_world_graph`` without any registry/anchor issue."""
    for asset_id in sorted(AssetRegistry.ASSET_IDS):
        draft = parse_full_draft(json.dumps(_draft_doc(asset_id=asset_id), sort_keys=True))
        issues = validate_world_graph(
            draft.world_graph,
            {o.object_id for o in draft.objects},
            {e.id for e in draft.evidence},
        )
        assert not any("AssetRegistry" in i or "asset" in i for i in issues), (asset_id, issues)


def test_phase33_allowlisted_anchors_pass_world_graph():
    """Every anchor in ``ANCHOR_ALLOWLIST`` (now stated in model-facing content)
    passes the world-graph anchor check."""
    placements = tuple(
        PlacementSpec(
            object_id=f"obj_{anchor}",
            asset_id="PROP_KITCHEN_KNIFE_01",
            location_id="l1",
            anchor=anchor,
            interaction="",
        )
        for anchor in ANCHOR_ALLOWLIST
    )
    wg = WorldGraphSpec(
        locations=(
            WorldGraphLocationSpec(location_id="l1", template="kitchen_template", rooms=("kitchen",)),
        ),
        placements=placements,
    )
    issues = validate_world_graph(
        wg, {p.object_id for p in placements}, set()
    )
    assert not any("anchor" in i for i in issues), issues


def test_phase33_interaction_allowlist_passes_world_graph():
    """Interactions from ``INTERACTION_ALLOWLIST`` (stated in the scope) are
    accepted; the empty string (decorative) stays legal for unlinked places."""
    for index, interaction in enumerate(("",) + INTERACTION_ALLOWLIST):
        placement = PlacementSpec(
            object_id=f"obj_{index}",
            asset_id="PROP_KITCHEN_KNIFE_01",
            location_id="l1",
            anchor="desk_main",
            interaction=interaction,
        )
        wg = WorldGraphSpec(
            locations=(
                WorldGraphLocationSpec(location_id="l1", template="kitchen_template", rooms=("kitchen",)),
            ),
            placements=(placement,),
        )
        issues = validate_world_graph(wg, {f"obj_{index}"}, set())
        assert not any("interaction" in i for i in issues), (interaction, issues)


# ---------------------------------------------------------------------------
# locked-witness contract
# ---------------------------------------------------------------------------


def test_phase33_locked_witness_matching_person_passes():
    """A draft that realizes the locked Witness as a role-witness person whose
    id OR name matches it (DEF-054 normalization) satisfies the lock."""
    locked = LockedConstraints(witness="emily_reed")

    # id match: person_id == emily_reed, role == witness
    doc_id = _draft_doc()
    doc_id["persons"].append(
        {
            "personId": "emily_reed",
            "name": "Emily Reed",
            "role": "witness",
            "affordances": ["VISIBLE_CHARACTER", "INSPECTABLE"],
        }
    )
    assert locked.violations_against(parse_full_draft(json.dumps(doc_id, sort_keys=True))) == ()

    # name match with a non-name id (DEF-054 rule 1 second clause)
    doc_name = _draft_doc()
    doc_name["persons"].append(
        {
            "personId": "WITNESS_01",
            "name": "Emily Reed",
            "role": "witness",
            "affordances": ["VISIBLE_CHARACTER", "INSPECTABLE"],
        }
    )
    assert locked.violations_against(parse_full_draft(json.dumps(doc_name, sort_keys=True))) == ()

    # normalization-insensitive spelling ("EMILY REED" == "emily_reed")
    doc_ci = _draft_doc()
    doc_ci["persons"].append(
        {
            "personId": "WITNESS_02",
            "name": "EMILY REED!",
            "role": "witness",
            "affordances": ["VISIBLE_CHARACTER", "INSPECTABLE"],
        }
    )
    assert locked.violations_against(parse_full_draft(json.dumps(doc_ci, sort_keys=True))) == ()


# ---------------------------------------------------------------------------
# model-facing contexts now carry the authoritative scope
# ---------------------------------------------------------------------------


def _request_context(attempt: pipeline.AttemptRecord, stage: GenerationStage) -> str:
    request = pipeline.build_request(attempt, stage)
    return request.prompt_context


def _minimal_attempt() -> pipeline.AttemptRecord:
    attempt = pipeline.AttemptRecord(
        attempt_id="GA-phase33-scope",
        case_id="CASE-phase33",
        session_id="QUOTA-phase33",
    )
    attempt.draft = parse_full_draft(json.dumps(_draft_doc(), sort_keys=True))
    return attempt


def test_phase33_repair_context_carries_scope_and_witness_contract():
    attempt = _minimal_attempt()
    context = _request_context(attempt, GenerationStage.REPAIR)
    for asset_id in sorted(AssetRegistry.ASSET_IDS):
        assert asset_id in context
    for anchor in sorted(ANCHOR_ALLOWLIST):
        assert anchor in context
    assert "LOCKED WITNESS CONTRACT" in context
    assert "AUTHORIZED GENERATION SCOPE" in context


def test_phase33_world_graph_context_carries_asset_and_anchor_scope():
    attempt = _minimal_attempt()
    context = _request_context(attempt, GenerationStage.WORLD_GRAPH)
    assert "AUTHORIZED GENERATION SCOPE" in context
    for asset_id in sorted(AssetRegistry.ASSET_IDS):
        assert asset_id in context
    for anchor in sorted(ANCHOR_ALLOWLIST):
        assert anchor in context


def test_phase33_public_world_context_carries_witness_contract():
    attempt = _minimal_attempt()
    context = _request_context(attempt, GenerationStage.PUBLIC_WORLD)
    assert "LOCKED WITNESS CONTRACT" in context
    assert "AUTHORIZED GENERATION SCOPE" in context


def test_phase33_scope_is_deterministic_and_bounded():
    attempt = _minimal_attempt()
    first = _request_context(attempt, GenerationStage.REPAIR)
    second = _request_context(attempt, GenerationStage.REPAIR)
    assert first == second
    # Bounded: the scope adds a bounded vocabulary block, never the catalog.
    assert len(first) < 20000


def test_phase33_scope_never_in_core_stages_unchanged_semantics():
    """CASE_TRUTH and EVIDENCE contexts are NOT scope-enlarged (bounded change:
    only the persons/world/repair stages need the registries)."""
    attempt = _minimal_attempt()
    for stage in (GenerationStage.CASE_TRUTH, GenerationStage.EVIDENCE):
        context = _request_context(attempt, stage)
        assert "AUTHORIZED GENERATION SCOPE" not in context, stage.value


def test_phase33_prompt_templates_teach_the_scope():
    """The Ollama templates (REPAIR / WORLD_REQUIREMENTS / CASE_PEOPLE) embed
    the same authoritative vocabulary the frontier contexts carry."""
    repair = prompts.build_repair_prompt('{"crime": {}}', ("asset issue",))
    for asset_id in sorted(AssetRegistry.ASSET_IDS):
        assert asset_id in repair
    assert "LOCKED WITNESS CONTRACT" in repair
    assert "AUTHORIZED GENERATION SCOPE" in repair

    world = prompts.build_world_requirements_prompt("ctx", None)
    assert "AUTHORIZED GENERATION SCOPE" in world
    for anchor in sorted(ANCHOR_ALLOWLIST):
        assert anchor in world

    case = prompts.build_case_people_prompt("ctx", None)
    assert "LOCKED WITNESS CONTRACT" in case or "witness" in case.casefold()