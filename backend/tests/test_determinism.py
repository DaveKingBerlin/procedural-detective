"""Determinism / property tests (Phase3 test G, REQUIREMENTS 43).

The same structured input always produces identical normalized outputs; the
ordering of logically unordered input collections (evidence facts, persons,
motives, objects, locations, travel rules) must not change the result.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import (  # noqa: E402
    LOCATIONS,
    MOTIVES,
    OBJECTS,
    PERSONS,
    TRAVEL_RULES,
    golden_evidence,
    golden_public,
)

from app.domain.public import (  # noqa: E402
    PublicCase,
    PublicLocation,
    PublicMotive,
    PublicObject,
    PublicPerson,
    PublicTravelRule,
)
from app.domain.solver import solve_case  # noqa: E402


def test_same_input_always_produces_identical_proof():
    public = golden_public()
    evidence = golden_evidence()
    first = solve_case(public, evidence)
    for _ in range(3):
        assert solve_case(public, evidence) == first


def _shuffled_evidence(seed: int) -> list:
    import random as _random

    items = list(golden_evidence())
    _random.Random(seed).shuffle(items)
    return items


def _shuffled_public(seed: int) -> PublicCase:
    rng = random.Random(seed)
    persons = list(PERSONS)
    motives = list(MOTIVES)
    objects = list(OBJECTS)
    locations = list(LOCATIONS)
    travel = list(TRAVEL_RULES)
    for collection in (persons, motives, objects, locations, travel):
        rng.shuffle(collection)
    return PublicCase(
        case_id=golden_public().case_id,
        case_version=golden_public().case_version,
        persons=tuple(persons),
        motives=tuple(motives),
        objects=tuple(objects),
        locations=tuple(locations),
        travel_rules=tuple(travel),
        scene=golden_public().scene,
    )


def test_shuffled_evidence_order_produces_identical_result():
    public = golden_public()
    reference = solve_case(public, golden_evidence())
    for seed in (1, 2, 7, 42):
        shuffled = solve_case(public, _shuffled_evidence(seed))
        assert shuffled == reference, f"seed {seed} changed the proof"
        assert shuffled.evidence_ids_used == reference.evidence_ids_used


def test_shuffled_collection_order_produces_identical_result():
    evidence = golden_evidence()
    reference = solve_case(golden_public(), evidence)
    for seed in (3, 13, 99):
        shuffled_public = _shuffled_public(seed)
        result = solve_case(shuffled_public, evidence)
        assert result == reference, f"seed {seed} changed the proof"
        assert result.eligibility_snapshot == reference.eligibility_snapshot


def test_shuffled_everything_produces_identical_result():
    reference = solve_case(golden_public(), golden_evidence())
    for seed in (5, 6):
        result = solve_case(_shuffled_public(seed), _shuffled_evidence(seed))
        assert result == reference
        text_a = result.to_dict()
        text_b = reference.to_dict()
        assert text_a == text_b


def test_reordering_candidate_collections_does_not_change_universes():
    rng = random.Random(11)
    persons = list(PERSONS)
    rng.shuffle(persons)
    public = PublicCase(
        case_id="CASE-001",
        case_version=1,
        persons=tuple(persons),
        motives=MOTIVES,
        objects=OBJECTS,
        locations=LOCATIONS,
        travel_rules=TRAVEL_RULES,
        scene=golden_public().scene,
    )
    assert solve_case(public, golden_evidence()).eligibility_snapshot == solve_case(
        golden_public(), golden_evidence()
    ).eligibility_snapshot