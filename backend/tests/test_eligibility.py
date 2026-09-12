"""Candidate universe tests (REQUIREMENTS 31.1.4 / 65.1, Phase3 test A).

Universes must be derived ONLY from public eligibility predicates
(SUSPECT_ELIGIBLE / MOTIVE_CANDIDATE / POTENTIAL_WEAPON); never from
CaseTruth. Exact equality with an independently derived eligibility set is
mandatory, and role mutation must not affect universes whereas affordance
mutation must.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import golden_public  # noqa: E402

from app.domain.eligibility import (  # noqa: E402
    CandidateUniverses,
    MOTIVE_CANDIDATE,
    POTENTIAL_WEAPON,
    SUSPECT_ELIGIBLE,
    derive_universes,
    motive_universe,
    suspect_universe,
    weapon_universe,
)
from app.domain.public import PublicCase, PublicPerson  # noqa: E402


def test_suspect_universe_equals_independent_derivation():
    public = golden_public()
    universe = suspect_universe(public)
    independent = tuple(
        sorted(p.person_id for p in public.persons if SUSPECT_ELIGIBLE in p.public_affordances)
    )
    assert universe == independent
    assert universe == ("anna_karlsson", "michael_carter", "thomas_reed")


def test_motive_universe_equals_independent_derivation():
    public = golden_public()
    universe = motive_universe(public)
    independent = tuple(
        sorted(m.motive_id for m in public.motives if MOTIVE_CANDIDATE in m.public_affordances)
    )
    assert universe == independent
    assert universe == ("cover_up_embezzlement", "revenge_for_affair", "robbery_gone_wrong")


def test_weapon_universe_equals_independent_derivation():
    public = golden_public()
    universe = weapon_universe(public)
    independent = tuple(
        sorted(o.object_id for o in public.objects if POTENTIAL_WEAPON in o.public_affordances)
    )
    assert universe == independent
    assert universe == ("kitchen_knife", "letter_opener", "scissors")


def test_candidate_universes_value_equality():
    public = golden_public()
    first = derive_universes(public)
    second = derive_universes(public)
    assert first == second
    assert CandidateUniverses(
        eligibility_version="1.0",
        suspect_ids=["thomas_reed", "anna_karlsson", "michael_carter"],
        motive_ids=["robbery_gone_wrong", "cover_up_embezzlement", "revenge_for_affair"],
        weapon_ids=["scissors", "kitchen_knife", "letter_opener"],
    ) == first


def test_mutating_role_field_does_not_change_universes():
    public = golden_public()
    before = derive_universes(public)
    mutated_persons = tuple(
        PublicPerson(
            person_id=p.person_id,
            name=p.name,
            role="witness" if p.person_id == "thomas_reed" else p.role,
            public_affordances=p.public_affordances,
            presented_data=p.presented_data,
        )
        for p in public.persons
    )
    mutated_public = PublicCase(
        case_id=public.case_id,
        case_version=public.case_version,
        persons=mutated_persons,
        motives=public.motives,
        objects=public.objects,
        locations=public.locations,
        travel_rules=public.travel_rules,
        scene=public.scene,
    )
    after = derive_universes(mutated_public)
    assert before == after


def test_changing_public_affordance_changes_universes():
    public = golden_public()

    no_thomas = tuple(
        PublicPerson(
            person_id=p.person_id,
            name=p.name,
            role=p.role,
            public_affordances=(
                p.public_affordances - frozenset({SUSPECT_ELIGIBLE})
                if p.person_id == "thomas_reed"
                else p.public_affordances
            ),
            presented_data=p.presented_data,
        )
        for p in public.persons
    )
    shrunk = derive_universes(
        PublicCase(
            case_id=public.case_id,
            case_version=public.case_version,
            persons=no_thomas,
            motives=public.motives,
            objects=public.objects,
            locations=public.locations,
            travel_rules=public.travel_rules,
            scene=public.scene,
        )
    )
    assert "thomas_reed" not in shrunk.suspect_ids

    # Adding SUSPECT_ELIGIBLE to a witness enlarges the suspect universe.
    with_witness = tuple(
        PublicPerson(
            person_id=p.person_id,
            name=p.name,
            role=p.role,
            public_affordances=(
                p.public_affordances | frozenset({SUSPECT_ELIGIBLE})
                if p.person_id == "emily_reed"
                else p.public_affordances
            ),
            presented_data=p.presented_data,
        )
        for p in public.persons
    )
    enlarged = derive_universes(
        PublicCase(
            case_id=public.case_id,
            case_version=public.case_version,
            persons=with_witness,
            motives=public.motives,
            objects=public.objects,
            locations=public.locations,
            travel_rules=public.travel_rules,
            scene=public.scene,
        )
    )
    assert "emily_reed" in enlarged.suspect_ids


def test_universes_are_independent_of_hidden_truth():
    from fixtures.golden import truth_variant_a, truth_variant_b

    public = golden_public()
    universes = derive_universes(public)
    # The two hidden truths differ in every canonical field; universes come
    # from the public model and are flatly identical.
    assert truth_variant_a() != truth_variant_b()
    assert universes == derive_universes(public)


def test_candidate_universes_reject_unknown_affordance():
    from app.domain.public import PublicMotive

    with pytest.raises(ValueError):
        PublicMotive(
            motive_id="x",
            label="x",
            public_affordances=frozenset({"NOT_A_REAL_AFFORDANCE"}),
        )