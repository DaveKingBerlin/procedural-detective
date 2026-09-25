"""Phase 19C — WHEN-derivability-from-DISCOVERABLE-evidence regression suite.

Covers the accepted ADV-222/ADV-223/ADV-224 fix contract (Phase 19C gate):

ADV-222 (MEDIUM): the canonical WHEN must be deterministically derivable from
DISCOVERABLE evidence, exactly like WHO/WHY/WEAPON. Every example world now
carries at least one placed, interactable object whose discovery returns a
time-bearing evidence fact:

- EASY  (golden apartment): the victim BODY is evidence-linked to the golden
  ``body_found_01`` (BODY_FIRST_FOUND_AT) record — interacting with the body
  discovers a WHEN fact;
- MEDIUM (driver hotel_suite kitchen-knife): the universally-placed LAPTOP is
  the scene activity-log device — its discovery returns ``d_ev_when_obs``
  (CRIME_SCENE_OBSERVATION_AT);
- HARD   (driver office locked ice-pick): the SAME general laptop/device-log
  rule applies to the locked-prompt office world.

Each test proves the fact is REACHABLE (placement-linked), not merely present
in the fact set, and that a player following ONLY the world's reachable facts
derives a WHEN feasible interval that CONTAINS the canonical WHEN (via the real
deterministic solver over the restricted evidence set).

ADV-223 (MEDIUM): ``_first_evidence_referencing_object`` picks the fact whose
kind/role best matches the object's published role (documented priority list:
FORENSIC_WEAPON_MATCH > OBJECT_CONTAINS_FINGERPRINT > forensic kind > other;
tie-break id order) instead of the id-sorted first; the choice is stable across
runs, evidence-ORDER independent, and the locked-weapon sealed path
(``d_ev_weapon_true``) still preempts.

ADV-224 (LOW-MEDIUM): the resolve branch may only rebound/upgrade objects that
were AUTHORED as evidence-bearing (composer association that did not survive
the projection, or the locked weapon). A NEVER-authored informational object
that a canonical fact references stays informational (interaction unchanged,
discovery null).
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.solver import solve_case  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.services.ollama_driver import (  # noqa: E402
    _first_evidence_referencing_object,
    _project_placement_evidence,
)
from app.services.publication import (  # noqa: E402
    placements_for_evidence,
)
from phase6_helpers import (  # noqa: E402
    BODY_OBJECT,
    case_for,
    interact,
    playthrough,
)
from test_phase7_helpers import assert_no_pre_reveal_material  # noqa: E402
from test_ollama_driver import _case_people, _evidence, _j, _run, _staged, _world, _alog_posts  # noqa: E402
from test_generation_roundtrip import _draft_from_stages  # noqa: E402


def _published_payload(app, case_id, version=1):
    row = app.state.store.get_published(case_id, version)
    assert row is not None
    return json.loads(row.payload_json)


def _reachable_evidence_ids(payload: dict) -> tuple[str, ...]:
    """The evidence ids a player can ACTUALLY discover (placement-linked)."""
    from app.services.publication import evidence_ids_of

    return tuple(
        sorted(
            eid
            for eid in evidence_ids_of(payload)
            if placements_for_evidence(payload, eid)
        )
    )


def _when_contains_canonical(
    draft: object,
    reachable_ids: tuple[str, ...],
    canonical: str,
    *,
    case_id: str,
    title: str,
) -> bool:
    """True when the DETERMINISTIC when-solver derives, from ONLY the facts a
    player can discover (``reachable_ids``), a feasible interval that CONTAINS
    the canonical WHEN. This is the ADV-222 derivability claim: the player does
    not need the hidden/non-placement-linked facts."""
    from app.domain.time_interval import parse_iso8601_to_epoch
    from app.generation.pipeline import _draft_to_phase3

    kept = tuple(f for f in draft.evidence if f.id in reachable_ids)
    assert kept, "the player-reachable evidence set must be non-empty"
    restricted = dataclasses.replace(draft, evidence=kept)
    public, evidence, _truth = _draft_to_phase3(
        restricted, case_id=case_id, title=title
    )
    proof = solve_case(public, evidence)
    tick = parse_iso8601_to_epoch(canonical)
    return proof.when.feasible.contains(tick)


def _discoverable_ids(payload: dict) -> set[str]:
    return {
        f["id"]
        for f in payload["draft"]["evidence"]
        if f.get("discoverable") is not False
    }


# --------------------------------------------------------------------------- #
# ADV-222 — EASY: the golden apartment world
# --------------------------------------------------------------------------- #


def test_adv222_easy_golden_body_when_fact_is_reachable_and_derives_when(phase5_app):
    """The golden EASY world carries a discoverable, PLACEMENT-REACHED
    time-bearing fact (body_found_01 on the victim body) and a player solving
    with ONLY the reachable facts derives an interval containing 22:17."""
    case_id, creator = case_for(phase5_app)
    payload = _published_payload(phase5_app, case_id, 1)

    # The body placement links the canonical time-bearing fact (reachable,
    # not merely present in the fact set).
    body_placements = placements_for_evidence(payload, "body_found_01")
    assert body_placements, "body_found_01 is not placement-reachable (ADV-222)"
    assert [str(p.get("object_id")) for p in body_placements] == [BODY_OBJECT]
    facts = {f["id"]: f for f in payload["draft"]["evidence"]}
    assert facts["body_found_01"].get("discoverable") is not False
    assert "body_found_01" in _discoverable_ids(payload)

    # Real API: interacting with the BODY discovers the WHEN fact.
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = interact(phase5_app, pt_id, pt_token, BODY_OBJECT, "inspect")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["evidenceId"] == "body_found_01"
    assert body["discovery"]["kind"] == "witness_observation"
    assert body["discovery"]["state"] == "discovered"
    assert_no_pre_reveal_material(body, canonical_time="2026-09-11T22:17:00+02:00")

    # Player-derivability: solve WITH ONLY the reachable facts -> 22:17 inside.
    reachable = _reachable_evidence_ids(payload)
    assert "body_found_01" in reachable
    assert _when_contains_canonical(
        _draft_from_stages(),
        reachable,
        "2026-09-11T22:17:00+02:00",
        case_id=case_id,
        title="when-easy",
    )


# --------------------------------------------------------------------------- #
# ADV-222 — MEDIUM: driver hotel_suite kitchen-knife world
# --------------------------------------------------------------------------- #


def _medium_hotel_payload():
    cp = _case_people(weapon="kitchen_knife")
    cp["crime"]["crimeTime"] = {
        "canonical": "2026-09-11T21:18:00+02:00",
        "accusationToleranceSeconds": 300,
    }
    posts = [
        _j(cp),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T21:18:00+02:00'),
        _j(_world("kitchen knife")),
    ]
    prompt = (
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
        "Weapon: kitchen knife\nTime: 21:18\nWitness: Lisa K\u00f6nig\n"
        "Location: hotel suite\n"
    )
    record, _transport = _run(posts, prompt=prompt)
    assert record.state is GenerationState.PUBLISHED, record.deferred_structural
    from app.services.publication import serialize_published_payload

    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="medium", model="mock", title="Medium"
        )
    )
    return record, payload


def test_adv222_medium_hotel_laptop_when_fact_reachable_and_derives_when():
    """MEDIUM (driver hotel_suite): the laptop is the activity-log device whose
    discovery returns the time-bearing d_ev_when_obs fact; a player solving
    with ONLY the driver world's reachable facts derives an interval that
    contains 21:18."""
    record, payload = _medium_hotel_payload()

    wp_placements = placements_for_evidence(payload, "d_ev_when_obs")
    assert wp_placements, "d_ev_when_obs is not placement-reachable (ADV-222)"
    objects = {str(p.get("object_id")) for p in wp_placements}
    assert objects == {"apartment_laptop"}
    facts = {f["id"]: f for f in payload["draft"]["evidence"]}
    assert facts["d_ev_when_obs"].get("discoverable") is not False
    assert "d_ev_when_obs" in _discoverable_ids(payload)

    reachable = _reachable_evidence_ids(payload)
    assert "d_ev_when_obs" in reachable
    assert _when_contains_canonical(
        record.published.draft,
        reachable,
        "2026-09-11T21:18:00+02:00",
        case_id="medium",
        title="Medium",
    )


# --------------------------------------------------------------------------- #
# ADV-222 — HARD: driver office locked ice-pick world
# --------------------------------------------------------------------------- #


def test_adv222_hard_office_laptop_when_fact_reachable_and_derives_when():
    """HARD (driver office, locked bronze-ice-pick prompt): the laptop is the
    activity-log device whose discovery returns d_ev_when_obs; a player solving
    with ONLY the reachable facts derives an interval containing 23:42."""
    from app.services.publication import serialize_published_payload

    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="hard", model="mock", title="Hard"
        )
    )

    wp_placements = placements_for_evidence(payload, "d_ev_when_obs")
    assert wp_placements, "d_ev_when_obs is not placement-reachable (ADV-222)"
    assert {str(p.get("object_id")) for p in wp_placements} == {"apartment_laptop"}
    facts = {f["id"]: f for f in payload["draft"]["evidence"]}
    assert facts["d_ev_when_obs"].get("discoverable") is not False

    reachable = _reachable_evidence_ids(payload)
    assert "d_ev_when_obs" in reachable
    assert _when_contains_canonical(
        record.published.draft,
        reachable,
        "2026-09-11T23:42:00+02:00",
        case_id="hard",
        title="Hard",
    )


def test_adv222_every_driver_kit_carries_a_reachable_when_fact():
    """The device/log anchor is the GENERAL rule, not a two-kit showcase: every
    driver kit (apartment/office/hotel_suite/warehouse/mansion) publishes the
    laptop bound to the canonical activity-log record so a time-bearing fact is
    player-reachable on every world."""
    from app.services.publication import serialize_published_payload

    for env_hint, world_req, weapon_obj in (
        ("apartment", _world("kitchen knife"), "kitchen_knife"),
        ("office", _world("kitchen knife"), "kitchen_knife"),
        ("hotel suite", _world("kitchen knife"), "kitchen_knife"),
        ("warehouse", _world("kitchen knife"), "kitchen_knife"),
        ("mansion", _world("kitchen knife"), "kitchen_knife"),
    ):
        cp = _case_people(weapon=weapon_obj)
        cp["crime"]["crimeTime"] = {
            "canonical": "2026-09-11T21:18:00+02:00",
            "accusationToleranceSeconds": 300,
        }
        prompt = (
            "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
            f"Weapon: {weapon_obj.replace('_', ' ')}\nTime: 21:18\n"
            f"Witness: Lisa K\u00f6nig\nLocation: {env_hint}\n"
        )
        posts = [
            _j(cp),
            _j(_evidence(weapon_obj=weapon_obj, murderer="paul_becker")),
            *_alog_posts('2026-09-11T21:18:00+02:00'),
            _j(world_req),
        ]
        record, _transport = _run(posts, prompt=prompt)
        assert record.state is GenerationState.PUBLISHED, (
            env_hint,
            record.deferred_structural,
        )
        payload = json.loads(
            serialize_published_payload(
                record.published, seed=1, prompt=env_hint, model="mock", title=env_hint
            )
        )
        assert placements_for_evidence(payload, "d_ev_when_obs"), env_hint


# --------------------------------------------------------------------------- #
# ADV-223 — semantic (role-matched), deterministic evidence rebind
# --------------------------------------------------------------------------- #

# generator-only reusable snippet: NOT a test.
def _two_fact_spec(*, match_first: bool):
    """An EvidenceSetSpec whose TWO canonical facts both reference
    ``kitchen_knife``: a FORENSIC_WEAPON_MATCH record (the object's published
    role) and a fingerprint-role record. ``match_first`` swaps the order so the
    tests prove evidence-ORDER independence."""
    from app.generation.parser import parse_stage

    doc = {
        "evidence": [
            {
                "id": "d_ev_fp_extra",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "record", "sourceId": "r1"},
                "propositions": [
                    {
                        "type": "OBJECT_CONTAINS_FINGERPRINT",
                        "objectId": "kitchen_knife",
                        "personId": "paul_becker",
                        "uncertaintySeconds": 0,
                        "structured": {},
                    }
                ],
                "presentation": {"title": "t", "description": "d"},
            },
            {
                "id": "d_ev_weapon_false_kitchenknife",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "record", "sourceId": "r2"},
                "propositions": [
                    {
                        "type": "FORENSIC_WEAPON_MATCH",
                        "objectId": "kitchen_knife",
                        "uncertaintySeconds": 0,
                        "structured": {"match": False},
                    }
                ],
                "presentation": {"title": "t", "description": "d"},
            },
        ]
    }
    facts = doc["evidence"]
    if match_first:
        facts = [facts[1], facts[0]]
    doc["evidence"] = facts
    return parse_stage(GenerationStage.EVIDENCE, _j(doc), non_throwing=False)


def test_adv223_semantic_not_alphabetical_and_stable():
    """ADV-223: when two canonical facts reference one object the resolve rule
    picks the fact whose KIND/ROLE matches the object's published role — here
    the FORENSIC_WEAPON_MATCH record — NOT the id-sorted first
    (``d_ev_fp_extra`` < ``d_ev_weapon_false_kitchenknife``)."""
    spec = _two_fact_spec(match_first=True)
    winner = _first_evidence_referencing_object(spec, "kitchen_knife")
    assert winner == "d_ev_weapon_false_kitchenknife", winner
    # id-sorted-first is the fingerprint fact — the choice must NOT be
    # alphabetical.
    assert sorted(("d_ev_fp_extra", "d_ev_weapon_false_kitchenknife"))[0] == (
        "d_ev_fp_extra"
    )
    assert winner != "d_ev_fp_extra"
    # stable across runs AND evidence-ORDER independent.
    for _ in range(20):
        assert _first_evidence_referencing_object(spec, "kitchen_knife") == winner
    assert (
        _first_evidence_referencing_object(_two_fact_spec(match_first=False), "kitchen_knife")
        == winner
    )


def test_adv223_locked_weapon_path_still_preempts():
    """ADV-223: the locked-weapon SEALED path (d_ev_weapon_true) still binds
    the locked weapon placement even when a semantic alternative fact also
    references the same object."""
    from app.generation.parser import parse_stage
    from app.generation.schemas import PlacementSpec

    doc = {
        "evidence": [
            {
                "id": "d_ev_weapon_true",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "record", "sourceId": "r1"},
                "propositions": [
                    {
                        "type": "FORENSIC_WEAPON_MATCH",
                        "objectId": "bronze_ceremonial_ice_pick",
                        "uncertaintySeconds": 0,
                        "structured": {"match": True},
                    }
                ],
                "presentation": {"title": "t", "description": "d"},
            },
            {
                "id": "d_ev_fp_extra",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "record", "sourceId": "r2"},
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
            },
        ]
    }
    evidence_spec = parse_stage(GenerationStage.EVIDENCE, _j(doc), non_throwing=False)
    placements = [
        PlacementSpec(
            object_id="bronze_ceremonial_ice_pick",
            asset_id="proc.decor.0123456789abcdef",
            location_id="office",
            anchor="office_desk_a",
            interaction="",
            evidence_id="weapon_id: bronze_ceremonial_ice_pick",  # model garbage
        )
    ]
    weapon, = _project_placement_evidence(placements, evidence_spec, "d_ev_weapon_true")
    assert weapon.evidence_id == "d_ev_weapon_true", weapon.evidence_id
    assert weapon.interaction == "inspect"


# --------------------------------------------------------------------------- #
# ADV-224 — only AUTHORED evidence-bearing objects may be rebound/upgraded
# --------------------------------------------------------------------------- #


def test_adv224_informational_object_stays_informational_never_upgraded():
    """ADV-224: a NEVER-authored informational object that a canonical fact
    references stays informational — interaction unchanged, discovery null —
    while an AUTHORED (composer association that did not survive) object is
    rebound to the real canonical evidence."""
    from app.generation.parser import parse_stage
    from app.generation.schemas import PlacementSpec

    doc = {
        "evidence": [
            {
                "id": "d_ev_weapon_false_kitchenknife",
                "kind": "forensic",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "record", "sourceId": "r1"},
                "propositions": [
                    {
                        "type": "FORENSIC_WEAPON_MATCH",
                        "objectId": "kitchen_knife",
                        "uncertaintySeconds": 0,
                        "structured": {"match": False},
                    }
                ],
                "presentation": {"title": "t", "description": "d"},
            },
            {
                "id": "d_ev_extra",
                "kind": "cctv",
                "reliability": "high",
                "discoverable": True,
                "sourceRef": {"kind": "record", "sourceId": "r2"},
                "propositions": [
                    {
                        "type": "OTHER",
                        "objectId": "info_statue_01",
                        "uncertaintySeconds": 0,
                        "structured": {},
                    }
                ],
                "presentation": {"title": "t", "description": "d"},
            },
        ]
    }
    evidence_spec = parse_stage(GenerationStage.EVIDENCE, _j(doc), non_throwing=False)
    placements = [
        # NEVER authored as evidence-bearing, but referenced by a canonical
        # fact: must STAY informational (interaction unchanged, discovery null).
        PlacementSpec(
            object_id="info_statue_01",
            asset_id="PROP_STATUE_01",
            location_id="office",
            anchor="office_meeting_table",
            interaction="inspect",
            evidence_id=None,
        ),
        # AUTHORED as evidence-bearing (composer association that does not
        # survive): IS rebound to the real canonical evidence record.
        PlacementSpec(
            object_id="kitchen_knife",
            asset_id="PROP_KITCHEN_KNIFE_01",
            location_id="office",
            anchor="office_desk_a",
            interaction="inspect",
            evidence_id="forensic_knife_match_01",
        ),
    ]
    statue, knife = _project_placement_evidence(placements, evidence_spec, "")
    # informational object: never upgraded to an evidence object.
    assert statue.evidence_id is None
    assert statue.interaction == "inspect"
    # authored-evidence object: rebound to the real canonical record.
    assert knife.evidence_id == "d_ev_weapon_false_kitchenknife"
    assert knife.interaction == "inspect"


__all__ = []  # pytest module: no accidental public names