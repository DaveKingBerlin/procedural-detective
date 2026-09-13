"""Shared Phase 3 test fixtures.

Deterministic builders for a golden public case + evidence set resembling the
Sarah/Thomas scenario (REQUIREMENTS 7.2 / 48 / 65.2): apartment scene, ~6
persons including the victim, three SUSPECT_ELIGIBLE suspects (Thomas + 2
alternatives), three MOTIVE_CANDIDATE motives, three POTENTIAL_WEAPON world
objects. Two different CaseTruth variants over the SAME public + evidence
(differing murdererId / motiveId / weaponId / crime time) are provided for the
truth-independence tests.

Everything is deterministic; there is no randomness.
"""

from __future__ import annotations

from typing import Any

from app.domain.evidence import (
    ALIBI_TIME_CLAIM,
    BODY_FIRST_FOUND_AT,
    FORENSIC_WEAPON_MATCH,
    MOTIVE_FACT_CONTRADICTED,
    MOTIVE_LINKED_TO_PERSON,
    NOISE_HEARD_AT,
    OBJECT_CONTAINS_FINGERPRINT,
    OTHER,
    PERSON_OBSERVED_AT_LOCATION,
    WITNESS_CLAIMS,
    EvidenceFact,
    Reliability,
    SourceRef,
    TypedProposition,
    VICTIM_LAST_SEEN_ALIVE_AT,
)
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

CASE_ID = "CASE-001"
CASE_VERSION = 1
TITLE = "The Missing €240,000"

SCENE_LOCATION = "miller_apartment_kitchen"
SCENE_NAME = "Miller Apartment - Kitchen"
OFFICE = "miller_consulting_office"
BAR = "harbor_view_bar"

# -- persons (public) --------------------------------------------------------

PERSONS: tuple[PublicPerson, ...] = (
    PublicPerson(
        person_id="sarah_miller",
        name="Sarah Miller",
        role="victim",
        public_affordances=frozenset({"VISIBLE_CHARACTER"}),
        presented_data={"age": 39, "occupation": "CEO, Miller Consulting"},
    ),
    PublicPerson(
        person_id="thomas_reed",
        name="Thomas Reed",
        role="suspect",
        public_affordances=frozenset({"SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"}),
        presented_data={"age": 42, "occupation": "Finance Director"},
    ),
    PublicPerson(
        person_id="michael_carter",
        name="Michael Carter",
        role="suspect",
        public_affordances=frozenset({"SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"}),
        presented_data={"age": 45, "occupation": "Accountant"},
    ),
    PublicPerson(
        person_id="anna_karlsson",
        name="Anna Karlsson",
        role="suspect",
        public_affordances=frozenset({"SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER"}),
        presented_data={"age": 38, "occupation": "Auditor"},
    ),
    PublicPerson(
        person_id="emily_reed",
        name="Emily Reed",
        role="witness",
        public_affordances=frozenset({"VISIBLE_CHARACTER"}),
        presented_data={"age": 36, "occupation": "Neighbour"},
    ),
    PublicPerson(
        person_id="david_kim",
        name="David Kim",
        role="family",
        public_affordances=frozenset({"VISIBLE_CHARACTER"}),
        presented_data={"age": 51, "occupation": "Brother of Sarah"},
    ),
)

MOTIVES: tuple[PublicMotive, ...] = (
    PublicMotive(
        motive_id="cover_up_embezzlement",
        label="Cover up the €240,000 embezzlement",
        public_affordances=frozenset({"MOTIVE_CANDIDATE"}),
    ),
    PublicMotive(
        motive_id="revenge_for_affair",
        label="Revenge for a suspected affair",
        public_affordances=frozenset({"MOTIVE_CANDIDATE"}),
    ),
    PublicMotive(
        motive_id="robbery_gone_wrong",
        label="Robbery gone wrong",
        public_affordances=frozenset({"MOTIVE_CANDIDATE"}),
    ),
    PublicMotive(
        motive_id="inheritance_early",
        label="Early inheritance",
        public_affordances=frozenset(),
    ),
)

OBJECTS: tuple[PublicObject, ...] = (
    PublicObject(
        object_id="kitchen_knife",
        asset_id="PROP_KITCHEN_KNIFE_01",
        public_affordances=frozenset({"INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"}),
        subtype="sharp_weapon",
    ),
    PublicObject(
        object_id="letter_opener",
        asset_id="PROP_LETTER_OPENER_01",
        public_affordances=frozenset({"INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"}),
        subtype="sharp_weapon",
    ),
    PublicObject(
        object_id="scissors",
        asset_id="PROP_SCISSORS_01",
        public_affordances=frozenset({"INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"}),
        subtype="sharp_weapon",
    ),
    PublicObject(
        object_id="vase_01",
        asset_id="PROP_VASE_01",
        public_affordances=frozenset({"INSPECTABLE"}),
        subtype=None,
    ),
    # -- Phase 6 investigation scene (Milestone-1 golden scene assets) --------
    # Shell objects + evidence-laden objects. Only INSPECTABLE: they NEVER
    # enter the weapon universe, so the solver result is unchanged.
    PublicObject(
        object_id="apartment_laptop",
        asset_id="PROP_LAPTOP_01",
        public_affordances=frozenset({"INSPECTABLE"}),
        subtype="electronics",
    ),
    PublicObject(
        object_id="apartment_table",
        asset_id="PROP_TABLE_01",
        public_affordances=frozenset({"INSPECTABLE"}),
        subtype="furniture",
    ),
    PublicObject(
        object_id="apartment_door",
        asset_id="DOOR_APARTMENT_01",
        public_affordances=frozenset({"INSPECTABLE"}),
        subtype="door",
    ),
    PublicObject(
        object_id="apartment_lamp",
        asset_id="PROP_LAMP_01",
        public_affordances=frozenset({"INSPECTABLE"}),
        subtype="light",
    ),
    PublicObject(
        object_id="victim_body_placeholder",
        asset_id="PROP_BODY_PLACEHOLDER_01",
        public_affordances=frozenset({"INSPECTABLE"}),
        subtype="victim_body",
    ),
)

LOCATIONS: tuple[PublicLocation, ...] = (
    PublicLocation(location_id=SCENE_LOCATION, name=SCENE_NAME),
    PublicLocation(location_id=OFFICE, name="Miller Consulting Office"),
    PublicLocation(location_id=BAR, name="Harbor View Bar"),
)

TRAVEL_RULES: tuple[PublicTravelRule, ...] = (
    PublicTravelRule(from_location_id=OFFICE, to_location_id=SCENE_LOCATION, travel_time_seconds=1200),
    PublicTravelRule(from_location_id=BAR, to_location_id=SCENE_LOCATION, travel_time_seconds=2700),
)

SCENE = PublicScene(location_id=SCENE_LOCATION, name=SCENE_NAME)


def golden_public() -> PublicCase:
    return PublicCase(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        persons=PERSONS,
        motives=MOTIVES,
        objects=OBJECTS,
        locations=LOCATIONS,
        travel_rules=TRAVEL_RULES,
        scene=SCENE,
    )


# -- evidence ----------------------------------------------------------------

def _fact(
    evidence_id: str,
    kind: str,
    propositions: tuple[TypedProposition, ...],
    reliability: Reliability | str = Reliability.HIGH,
    source_kind: str = "record",
    title: str | None = None,
    discoverable: bool = True,
) -> EvidenceFact:
    return EvidenceFact(
        id=evidence_id,
        kind=kind,
        propositions=propositions,
        source_ref=SourceRef(kind=source_kind, source_id=f"{source_kind}_{evidence_id}"),
        reliability=reliability,
        presentation={"title": title or evidence_id, "description": "Structured evidence fact."},
        discoverable=discoverable,
    )


def golden_evidence() -> list[EvidenceFact]:
    return [
        _fact(
            "last_seen_01",
            "witness_observation",
            (
                TypedProposition(
                    type=VICTIM_LAST_SEEN_ALIVE_AT,
                    person_id="sarah_miller",
                    location_id=SCENE_LOCATION,
                    observed_at="2026-09-11T22:15:10+02:00",
                ),
            ),
            reliability=Reliability.HIGH,
            title="Neighbour saw Sarah alive at 22:15",
        ),
        _fact(
            "body_found_01",
            "witness_observation",
            (
                TypedProposition(
                    type=BODY_FIRST_FOUND_AT,
                    location_id=SCENE_LOCATION,
                    observed_at="2026-09-11T22:18:31+02:00",
                ),
            ),
            reliability=Reliability.HIGH,
            title="Body found in the kitchen at 22:18",
        ),
        _fact(
            "noise_heard_01",
            "witness_observation",
            (
                TypedProposition(
                    type=NOISE_HEARD_AT,
                    location_id=SCENE_LOCATION,
                    observed_at="2026-09-11T22:16:50+02:00",
                    uncertainty_seconds=90,
                ),
            ),
            reliability=Reliability.MEDIUM,
            title="Neighbour heard a struggle at about 22:17",
        ),
        _fact(
            "cctv_thomas_scene_01",
            "cctv_observation",
            (
                TypedProposition(
                    type=PERSON_OBSERVED_AT_LOCATION,
                    person_id="thomas_reed",
                    location_id=SCENE_LOCATION,
                    observed_at="2026-09-11T22:16:40+02:00",
                    uncertainty_seconds=60,
                ),
            ),
            reliability=Reliability.HIGH,
            title="Kitchen CCTV shows Thomas at 22:16:40",
        ),
        _fact(
            "alibi_claim_thomas_01",
            "suspect_statement",
            (
                TypedProposition(
                    type=ALIBI_TIME_CLAIM,
                    person_id="thomas_reed",
                    structured={"claimedDeparture": "2026-09-11T21:45:00+02:00"},
                ),
            ),
            reliability=Reliability.LOW,
            title="Thomas claims he left the apartment before 21:45",
        ),
        _fact(
            "cctv_michael_office_01",
            "cctv_observation",
            (
                TypedProposition(
                    type=PERSON_OBSERVED_AT_LOCATION,
                    person_id="michael_carter",
                    location_id=OFFICE,
                    observed_at="2026-09-11T22:15:00+02:00",
                    uncertainty_seconds=30,
                ),
            ),
            reliability=Reliability.HIGH,
            title="Office CCTV shows Michael at 22:15",
        ),
        _fact(
            "cctv_anna_bar_01",
            "cctv_observation",
            (
                TypedProposition(
                    type=PERSON_OBSERVED_AT_LOCATION,
                    person_id="anna_karlsson",
                    location_id=BAR,
                    observed_at="2026-09-11T22:14:30+02:00",
                    uncertainty_seconds=30,
                ),
            ),
            reliability=Reliability.HIGH,
            title="Bar CCTV shows Anna at 22:14:30",
        ),
        _fact(
            "motive_audit_01",
            "financial",
            (
                TypedProposition(
                    type=MOTIVE_LINKED_TO_PERSON,
                    person_id="thomas_reed",
                    motive_id="cover_up_embezzlement",
                ),
            ),
            reliability=Reliability.HIGH,
            title="Audit report links Thomas to the missing €240,000",
        ),
        _fact(
            "motive_no_affair_01",
            "digital",
            (
                TypedProposition(
                    type=MOTIVE_FACT_CONTRADICTED,
                    motive_id="revenge_for_affair",
                ),
            ),
            reliability=Reliability.HIGH,
            title="Phone records show Sarah had no affair",
        ),
        _fact(
            "motive_no_robbery_01",
            "physical",
            (
                TypedProposition(
                    type=MOTIVE_FACT_CONTRADICTED,
                    motive_id="robbery_gone_wrong",
                ),
            ),
            reliability=Reliability.HIGH,
            title="Valuables and cash are still in the apartment",
        ),
        _fact(
            "forensic_knife_match_01",
            "forensic",
            (
                TypedProposition(
                    type=FORENSIC_WEAPON_MATCH,
                    object_id="kitchen_knife",
                    structured={"match": True},
                ),
            ),
            reliability=Reliability.HIGH,
            title="Blood on the kitchen knife matches the victim",
        ),
        _fact(
            "fingerprint_knife_01",
            "forensic",
            (
                TypedProposition(
                    type=OBJECT_CONTAINS_FINGERPRINT,
                    object_id="kitchen_knife",
                    person_id="thomas_reed",
                ),
            ),
            reliability=Reliability.HIGH,
            title="Thomas's fingerprints on the kitchen knife",
        ),
        _fact(
            "forensic_letter_opener_01",
            "forensic",
            (
                TypedProposition(
                    type=FORENSIC_WEAPON_MATCH,
                    object_id="letter_opener",
                    structured={"match": False},
                ),
            ),
            reliability=Reliability.HIGH,
            title="Letter opener does not match the wound",
        ),
        _fact(
            "forensic_scissors_01",
            "forensic",
            (
                TypedProposition(
                    type=FORENSIC_WEAPON_MATCH,
                    object_id="scissors",
                    structured={"match": False},
                ),
            ),
            reliability=Reliability.HIGH,
            title="Scissors do not match the wound",
        ),
        # -- Phase 6 scene evidence facts --------------------------------------
        # Solver-neutral by construction: the propositions below (OTHER /
        # WITNESS_CLAIMS) are NOT consumed by any Phase 3 deduction rule, so
        # the golden solver proof and all_true result are unchanged. The
        # presentation carries ONLY public typed fields (the player-read
        # allowlist contract).
        golden_email_fact(),
        golden_witness_statement_fact(),
    ]


def _email_presentation() -> dict[str, Any]:
    """Public typed presentation of the golden email (read contract keys)."""
    return {
        "title": "Re: the missing funds",
        "description": "A short email Thomas sent the evening before the murder.",
        "fromPersonId": "thomas_reed",
        "toPersonIds": ["sarah_miller"],
        "subject": "We need to talk tonight",
        "body": (
            "Sarah, I reviewed the accounts again. I think we need to talk "
            "tonight before the board meeting, in person. Please do not "
            "involve the auditors until then. -Thomas"
        ),
        "timestamp": "2026-09-11T21:04:00+02:00",
    }


def _witness_statement_presentation() -> dict[str, Any]:
    """Public typed presentation of the golden witness statement."""
    return {
        "title": "Emily Reed's statement",
        "description": "Neighbour Emily Reed's account of the evening.",
        "speakerName": "Emily Reed",
        "statement": (
            "I heard shouting from the apartment around 22:10 and saw Thomas "
            "leave the kitchen around 22:20."
        ),
    }


def golden_email_fact() -> EvidenceFact:
    """The golden email evidence fact (with its full typed presentation)."""
    return EvidenceFact(
        id="email_thomas_01",
        kind="email",
        propositions=(TypedProposition(type=OTHER),),
        source_ref=SourceRef(kind="record", source_id="record_email_thomas_01"),
        reliability=Reliability.HIGH,
        presentation=_email_presentation(),
        discoverable=True,
    )


def golden_witness_statement_fact() -> EvidenceFact:
    """The golden witness-statement evidence fact (typed presentation)."""
    return EvidenceFact(
        id="witness_statement_emily_01",
        kind="witness_statement",
        propositions=(
            TypedProposition(type=WITNESS_CLAIMS, person_id="emily_reed"),
        ),
        source_ref=SourceRef(
            kind="record", source_id="record_witness_statement_emily_01"
        ),
        reliability=Reliability.MEDIUM,
        presentation=_witness_statement_presentation(),
        discoverable=True,
    )


# -- CaseTruth variants (hidden; used ONLY by truth-aware validation/tests) --

def _truth(
    murderer_id: str,
    motive_id: str,
    weapon_id: str,
    canonical: str,
    tolerance_seconds: int = 300,
) -> CaseTruth:
    return CaseTruth(
        case_id=CASE_ID,
        case_version=CASE_VERSION,
        title=TITLE,
        crime=Crime(
            type="murder",
            victim_id="sarah_miller",
            murderer_id=murderer_id,
            motive_id=motive_id,
            weapon_id=weapon_id,
            location_id=SCENE_LOCATION,
            crime_time=CrimeTime(
                canonical=canonical,
                accusation_tolerance_seconds=tolerance_seconds,
            ),
        ),
        timeline=(),
        persons=(),
        relationships=(),
        facts=(),
    )


def truth_variant_a() -> CaseTruth:
    """Canonical golden truth (Thomas / embezzlement / kitchen knife / 22:17)."""
    return _truth(
        murderer_id="thomas_reed",
        motive_id="cover_up_embezzlement",
        weapon_id="kitchen_knife",
        canonical="2026-09-11T22:17:00+02:00",
    )


def truth_variant_b() -> CaseTruth:
    """Different hidden truth over the SAME public + evidence.

    Different murderer/motive/weapon and a different (still feasible) crime
    time. The solver must produce an identical SolverProof for A and B.
    """
    return _truth(
        murderer_id="anna_karlsson",
        motive_id="robbery_gone_wrong",
        weapon_id="letter_opener",
        canonical="2026-09-11T22:16:30+02:00",
    )


__all__: list[str] = [
    "CASE_ID",
    "CASE_VERSION",
    "TITLE",
    "SCENE_LOCATION",
    "SCENE_NAME",
    "OFFICE",
    "BAR",
    "PERSONS",
    "MOTIVES",
    "OBJECTS",
    "LOCATIONS",
    "TRAVEL_RULES",
    "SCENE",
    "golden_public",
    "golden_evidence",
    "golden_email_fact",
    "golden_witness_statement_fact",
    "truth_variant_a",
    "truth_variant_b",
]