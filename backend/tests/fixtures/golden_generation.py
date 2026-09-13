"""Deterministic Phase 4 golden generation fixtures (REUSE of Phase 3 golden).

These fixtures serialize the Phase 3 golden public case (``fixtures.golden``),
its evidence facts and the truth.A (Thomas variant) crime into the Phase 4
stage JSON shapes, with fixed deterministic JSON (``indent=2``,
``ensure_ascii=False``, sorted dicts).

Golden properties:

- (a) EXACTLY respect the locked constraints from REQUIREMENTS §48 Case A
  (victim Sarah Miller -> ``sarah_miller``, murderer -> ``thomas_reed``,
  motive -> ``cover_up_embezzlement``, weapon -> ``kitchen_knife``,
  time -> ``2026-09-11T22:17:00+02:00``, witness -> ``emily_reed``);
- (b) solve under the Phase 3 solver exactly (single unique survivor per
  dimension, single connected time interval containing 22:17 within tolerance
  120 seconds).

``assemble_phase3`` converts a parsed ``GeneratedDraft`` back into EXACTLY the
Phase 3 golden ``PublicCase`` + ``EvidenceFact`` set + ``CaseTruth`` (truth.A),
which is what the round-trip test proves end-to-end.
"""

from __future__ import annotations

import json
from typing import Any

from app.domain.evidence import EvidenceFact, SourceRef, TypedProposition
from app.domain.public import (
    PublicCase,
    PublicLocation,
    PublicMotive,
    PublicObject,
    PublicPerson,
    PublicScene,
    PublicTravelRule,
)
from app.domain.truth import CaseTruth, Crime, CrimeTime
from app.generation.constraints import LockedConstraints
from app.generation.provider import GenerationStage
from app.generation.schemas import GeneratedDraft

from fixtures.golden import (  # type: ignore[import-not-found]
    BAR,
    CASE_ID,
    CASE_VERSION,
    OFFICE,
    SCENE_LOCATION,
    SCENE_NAME,
    TITLE,
    golden_evidence,
    golden_public,
    truth_variant_a,
)

# ---------------------------------------------------------------------------
# serializers (public model -> stage JSON shapes)
# ---------------------------------------------------------------------------


def _person_to_json(person: PublicPerson) -> dict[str, Any]:
    out: dict[str, Any] = {
        "personId": person.person_id,
        "name": person.name,
        "role": person.role,
        "affordances": sorted(person.public_affordances),
    }
    if person.presented_data:
        out["presentedData"] = dict(person.presented_data)
    return out


def _motive_to_json(motive: PublicMotive) -> dict[str, Any]:
    return {
        "motiveId": motive.motive_id,
        "label": motive.label,
        "affordances": sorted(motive.public_affordances),
    }


def _object_to_json(obj: PublicObject) -> dict[str, Any]:
    out: dict[str, Any] = {
        "objectId": obj.object_id,
        "assetId": obj.asset_id,
        "affordances": sorted(obj.public_affordances),
    }
    if obj.subtype is not None:
        out["subtype"] = obj.subtype
    return out


def _location_to_json(location: PublicLocation) -> dict[str, Any]:
    return {"locationId": location.location_id, "name": location.name}


def _travel_rule_to_json(rule: PublicTravelRule) -> dict[str, Any]:
    return {
        "fromLocationId": rule.from_location_id,
        "toLocationId": rule.to_location_id,
        "travelTimeSeconds": rule.travel_time_seconds,
    }


def _scene_to_json(scene: PublicScene) -> dict[str, Any]:
    return {"locationId": scene.location_id, "name": scene.name}


def _proposition_to_json(prop: TypedProposition) -> dict[str, Any]:
    out: dict[str, Any] = {"type": prop.type}
    for key, value in (
        ("personId", prop.person_id),
        ("locationId", prop.location_id),
        ("objectId", prop.object_id),
        ("motiveId", prop.motive_id),
        ("observedAt", prop.observed_at),
    ):
        if value is not None:
            out[key] = value
    out["uncertaintySeconds"] = prop.uncertainty_seconds
    if prop.structured:
        out["structured"] = dict(prop.structured)
    return out


def _evidence_to_json(fact: EvidenceFact) -> dict[str, Any]:
    return {
        "id": fact.id,
        "kind": fact.kind,
        "reliability": fact.reliability.value,
        "discoverable": fact.discoverable,
        "sourceRef": {
            "kind": fact.source_ref.kind,
            "sourceId": fact.source_ref.source_id,
        },
        "propositions": [_proposition_to_json(p) for p in fact.propositions],
        "presentation": dict(fact.presentation),
    }


def _crime_to_json(crime: Crime) -> dict[str, Any]:
    return {
        "type": crime.type,
        "victimId": crime.victim_id,
        "murdererId": crime.murderer_id,
        "motiveId": crime.motive_id,
        "weaponId": crime.weapon_id,
        "locationId": crime.location_id,
        "crimeTime": {
            "canonical": crime.crime_time.canonical,
            "accusationToleranceSeconds": crime.crime_time.accusation_tolerance_seconds,
        },
    }


# ---------------------------------------------------------------------------
# stage payload documents (built from the Phase 3 golden)
# ---------------------------------------------------------------------------

_public = golden_public()
_truth_a = truth_variant_a()
_evidence = golden_evidence()

_CASE_TRUTH_DOC: dict[str, Any] = {"crime": _crime_to_json(_truth_a.crime)}

_PUBLIC_WORLD_DOC: dict[str, Any] = {
    "persons": [_person_to_json(p) for p in _public.persons],
    "motives": [_motive_to_json(m) for m in _public.motives],
    "objects": [_object_to_json(o) for o in _public.objects],
    "locations": [_location_to_json(l) for l in _public.locations],
    "travelRules": [_travel_rule_to_json(r) for r in _public.travel_rules],
    "scene": _scene_to_json(_public.scene),
}

_EVIDENCE_DOC: dict[str, Any] = {
    "evidence": [_evidence_to_json(f) for f in _evidence]
}

_WORLD_GRAPH_DOC: dict[str, Any] = {
    "worldGraph": {
        "locations": [
            {"locationId": SCENE_LOCATION, "template": "kitchen_template", "rooms": ["kitchen"]},
            {"locationId": OFFICE, "template": "office_template", "rooms": ["office"]},
            {"locationId": BAR, "template": "bar_template", "rooms": ["bar"]},
        ],
        "placements": [
            {
                "objectId": "kitchen_knife",
                "assetId": "PROP_KITCHEN_KNIFE_01",
                "locationId": SCENE_LOCATION,
                "anchor": "kitchen_counter",
                "interaction": "inspect",
                "evidenceId": "forensic_knife_match_01",
            },
            {
                "objectId": "letter_opener",
                "assetId": "PROP_LETTER_OPENER_01",
                "locationId": OFFICE,
                "anchor": "office_desk_01",
                "interaction": "inspect",
                "evidenceId": "forensic_letter_opener_01",
            },
            {
                "objectId": "scissors",
                "assetId": "PROP_SCISSORS_01",
                "locationId": OFFICE,
                "anchor": "bedside_table",
                "interaction": "inspect",
                "evidenceId": "forensic_scissors_01",
            },
            {
                "objectId": "vase_01",
                "assetId": "PROP_VASE_01",
                "locationId": SCENE_LOCATION,
                "anchor": "dining_table",
                "interaction": "inspect",
                "evidenceId": None,
            },
            # -- Phase 6 Milestone-1 investigation scene -----------------------
            # laptop (links the golden email), shell objects and the victim
            # body placeholder. All shell/victim objects are INSPECTABLE-only
            # so the candidate universes and the solver proof stay identical.
            {
                "objectId": "apartment_laptop",
                "assetId": "PROP_LAPTOP_01",
                "locationId": SCENE_LOCATION,
                "anchor": "desk_main",
                "interaction": "read",
                "evidenceId": "email_thomas_01",
            },
            {
                "objectId": "apartment_table",
                "assetId": "PROP_TABLE_01",
                "locationId": SCENE_LOCATION,
                "anchor": "dining_table",
                "interaction": "inspect",
                "evidenceId": None,
            },
            {
                "objectId": "apartment_door",
                "assetId": "DOOR_APARTMENT_01",
                "locationId": SCENE_LOCATION,
                "anchor": "hall_wall_01",
                "interaction": "inspect",
                "evidenceId": None,
            },
            {
                "objectId": "apartment_lamp",
                "assetId": "PROP_LAMP_01",
                "locationId": SCENE_LOCATION,
                "anchor": "shelf_01",
                "interaction": "inspect",
                "evidenceId": None,
            },
            {
                "objectId": "victim_body_placeholder",
                "assetId": "PROP_BODY_PLACEHOLDER_01",
                "locationId": SCENE_LOCATION,
                "anchor": "floor_body_position",
                "interaction": "inspect",
                "evidenceId": None,
            },
        ],
    }
}

_FULL_DRAFT_DOC: dict[str, Any] = {
    "crime": _CASE_TRUTH_DOC["crime"],
    "persons": _PUBLIC_WORLD_DOC["persons"],
    "motives": _PUBLIC_WORLD_DOC["motives"],
    "objects": _PUBLIC_WORLD_DOC["objects"],
    "locations": _PUBLIC_WORLD_DOC["locations"],
    "travelRules": _PUBLIC_WORLD_DOC["travelRules"],
    "scene": _PUBLIC_WORLD_DOC["scene"],
    "evidence": _EVIDENCE_DOC["evidence"],
    "worldGraph": _WORLD_GRAPH_DOC["worldGraph"],
}


def _dump(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


GOLDEN_STAGE_PAYLOADS: dict[GenerationStage, str] = {
    GenerationStage.CASE_TRUTH: _dump(_CASE_TRUTH_DOC),
    GenerationStage.PUBLIC_WORLD: _dump(_PUBLIC_WORLD_DOC),
    GenerationStage.EVIDENCE: _dump(_EVIDENCE_DOC),
    GenerationStage.WORLD_GRAPH: _dump(_WORLD_GRAPH_DOC),
}

GOLDEN_FULL_DRAFT: str = _dump(_FULL_DRAFT_DOC)

# REQUIREMENTS §48 Case A locked constraints in canonical/id form (the
# documented golden encoding: "Sarah Miller" -> sarah_miller, "22:17" ->
# 2026-09-11T22:17:00+02:00, ...).
GOLDEN_LOCKED: LockedConstraints = LockedConstraints(
    victim="sarah_miller",
    murderer="thomas_reed",
    motive="cover_up_embezzlement",
    weapon="kitchen_knife",
    crime_time="2026-09-11T22:17:00+02:00",
    witness="emily_reed",
)


# ---------------------------------------------------------------------------
# phase-3 reassembly (DRAFT -> EXACT Phase 3 golden objects)
# ---------------------------------------------------------------------------


def assemble_phase3(
    parsed: GeneratedDraft,
) -> tuple[PublicCase, tuple[EvidenceFact, ...], CaseTruth]:
    """Reassemble a parsed draft into EXACTLY the Phase 3 golden objects.

    Returns ``(PublicCase, tuple[EvidenceFact, ...], CaseTruth)`` for the
    truth.A (Thomas) crime. Used by lifecycle tests and the round-trip proof.
    """
    persons = tuple(
        PublicPerson(
            person_id=s.person_id,
            name=s.name,
            role=s.role,
            public_affordances=frozenset(s.affordances),
            presented_data=dict(s.presented_data) if s.presented_data else {},
        )
        for s in parsed.persons
    )
    motives = tuple(
        PublicMotive(
            motive_id=m.motive_id,
            label=m.label,
            public_affordances=frozenset(m.affordances),
        )
        for m in parsed.motives
    )
    objects = tuple(
        PublicObject(
            object_id=o.object_id,
            asset_id=o.asset_id,
            public_affordances=frozenset(o.affordances),
            subtype=o.subtype,
        )
        for o in parsed.objects
    )
    locations = tuple(
        PublicLocation(location_id=l.location_id, name=l.name) for l in parsed.locations
    )
    travel_rules = tuple(
        PublicTravelRule(
            from_location_id=t.from_location_id,
            to_location_id=t.to_location_id,
            travel_time_seconds=t.travel_time_seconds,
        )
        for t in parsed.travel_rules
    )
    scene = (
        None
        if parsed.scene is None
        else PublicScene(
            location_id=parsed.scene.location_id, name=parsed.scene.name
        )
    )
    public = PublicCase(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        persons=persons,
        motives=motives,
        objects=objects,
        locations=locations,
        travel_rules=travel_rules,
        scene=scene,
    )

    evidence = tuple(
        EvidenceFact(
            id=f.id,
            kind=f.kind,
            propositions=tuple(p._as_typed_proposition() for p in f.propositions),
            source_ref=(
                SourceRef(kind=f.source_ref["kind"], source_id=f.source_ref["sourceId"])
                if f.source_ref is not None
                else None
            ),
            reliability=f.reliability,
            presentation=dict(f.presentation) if f.presentation else {},
            discoverable=f.discoverable,
        )
        for f in parsed.evidence
    )

    truth = CaseTruth(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        title=TITLE,
        crime=Crime(
            type=parsed.crime.type,
            victim_id=parsed.crime.victim_id,
            murderer_id=parsed.crime.murderer_id,
            motive_id=parsed.crime.motive_id,
            weapon_id=parsed.crime.weapon_id,
            location_id=parsed.crime.location_id,
            crime_time=CrimeTime(
                canonical=parsed.crime.crime_time.canonical,
                accusation_tolerance_seconds=parsed.crime.crime_time.accusation_tolerance_seconds,
            ),
        ),
        timeline=(),
        persons=(),
        relationships=(),
        facts=(),
    )
    return public, evidence, truth


__all__: list[str] = [
    "GOLDEN_LOCKED",
    "GOLDEN_STAGE_PAYLOADS",
    "GOLDEN_FULL_DRAFT",
    "assemble_phase3",
]