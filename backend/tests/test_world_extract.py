"""Phase 14 — prompt -> WorldRequirements extractor tests (pure unit).

Deterministic, no network, no DB. Focus: environment selection, object
keyword extraction, unsafe-request safe-fail, bounded relations and the
determinism contract (equal inputs -> equal outputs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.environments.resolver import resolve_environment  # noqa: E402
from app.generation.constraints import LockedConstraints  # noqa: E402
from app.world.extract import (  # noqa: E402
    ENVIRONMENT_FAMILIES,
    KNOWN_OBJECT_TABLE,
    TRIGGER_GAP_MAX,
    UNSAFE_OBJECT_TERMS,
    extract_world_requirements,
    is_base_object_request,
)
from app.world.requirements import RELATION_KINDS  # noqa: E402

from fixtures.world_showcase import SHOWCASE_EXPECTED, SHOWCASE_PROMPTS  # noqa: E402


def _extract(prompt: str, locked: LockedConstraints | None = None):
    return extract_world_requirements(prompt, locked)


# --------------------------------------------------------------------------- #
# environment selection (location changes the environment)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "prompt,hint,environment_id",
    [
        ("A body in a flat by the river.", "apartment", "apartment"),
        ("A condo break-in.", "apartment", "apartment"),
        ("An office heist.", "office", "office"),
        ("The company accountant disappeared.", "office", "office"),
        ("Found dead in the workplace.", "office", "office"),
        ("A hotel poisoning.", "hotel_suite", "hotel_suite"),
        ("Room 312 was a crime scene.", "hotel_suite", "hotel_suite"),
        ("A suite with a view.", "hotel_suite", "hotel_suite"),
        ("A warehouse smuggling job.", "warehouse", "warehouse"),
        ("The depot guard was attacked.", "warehouse", "warehouse"),
        ("Stolen goods in storage.", "warehouse", "warehouse"),
        ("A mansion inheritance feud.", "mansion", "mansion"),
        ("A villa with a secret cellar.", "mansion", "mansion"),
        ("The manor staff are suspects.", "mansion", "mansion"),
    ],
)
def test_prompt_location_changes_environment_selection(prompt, hint, environment_id):
    extracted = _extract(prompt)
    assert extracted.environment_hint == hint
    resolution = resolve_environment(extracted.environment_hint or "apartment")
    assert resolution.environment_id == environment_id


def test_unsupported_locations_never_closest_fit():
    for prompt in (
        "A crime at the beach.",
        "A murder in a castle by the sea.",
        "A boat was the scene of the crime.",
        "An unsupported location: the arena.",
    ):
        extracted = _extract(prompt)
        assert extracted.environment_hint is None
        assert extracted.location_tokens == ()


def test_first_family_keyword_in_document_order_wins():
    extracted = _extract("A hotel, then an office, then a mansion.")
    assert extracted.environment_hint == "hotel_suite"
    assert extracted.location_tokens == ("hotel",)


# --------------------------------------------------------------------------- #
# object keyword extraction (weapon/object prompt changes resolved asset)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "prompt,name",
    [
        ("The killer used a knife.", "kitchen knife"),
        ("A wrench was left behind.", "adjustable wrench"),
        ("A rope was used.", "rope"),
        ("A glass bottle was broken.", "glass bottle"),
        ("Medication was on the shelf.", "medication bottle"),
        ("Someone took the pills.", "medication bottle"),
        ("A watch was found.", "wristwatch"),
        ("The jewelry was missing.", "jewelry box"),
        ("A hammer was under the bed.", "claw hammer"),
        ("Scissors were in the drawer.", "scissors"),
        ("The laptop was open.", "laptop"),
        ("An antique letter opener was displayed.", "Antique Ceremonial Letter Opener"),
        ("A heavy award was on the shelf.", "Custom Trophy"),
        ("A desk award sat on the credenza.", "Distinctive Desk Award"),
        ("A laboratory sample rack was empty.", "Unusual Laboratory Sample Rack"),
    ],
)
def test_weapon_object_prompt_changes_resolved_request(prompt, name):
    extracted = _extract(prompt)
    names = {request.requested_name for request in extracted.objects}
    assert name in names


def test_letter_opener_on_desk_relation_binds_to_award():
    extracted = _extract(
        "A financial crime in a company office. The killer used a letter opener "
        "at the workplace. A heavy award is on the desk."
    )
    relations = {(r.kind, r.target) for r in extracted.relations}
    assert ("on_desk", "custom trophy") in relations


def test_near_the_body_binds_every_object_in_the_sentence():
    extracted = _extract(
        "A murder in a hotel room. A broken glass bottle and medication are near "
        "the body."
    )
    targets = {r.target for r in extracted.relations if r.kind == "near_victim"}
    assert targets == {"glass bottle", "medication bottle"}


def test_antique_phrase_is_not_shadowed_by_letter_opener():
    extracted = _extract(
        "An inheritance dispute in a mansion. A valuable antique ceremonial "
        "letter opener and a watch are in the study."
    )
    names = [request.requested_name for request in extracted.objects]
    assert "Antique Ceremonial Letter Opener" in names
    assert "letter opener" not in names  # the longer phrase wins
    assert "wristwatch" in names


def test_unknown_nouns_are_ignored_never_invented():
    extracted = _extract(
        "A quantum woggle and a zzorp were observed near the evidence."
    )
    assert extracted.objects == ()


# --------------------------------------------------------------------------- #
# unsafe requests fail safely
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("term", ["bomb", "gun", "explosive", "rifle", "pistol"])
def test_unsafe_dangerous_asset_request_fails_safely(term):
    extracted = _extract(f"A {term} was mentioned in the prompt.")
    assert extracted.objects == ()
    assert any("unsafeUnsupported" in note and term in note for note in extracted.unsafe_unsupported)


def test_unsafe_terms_are_documented_and_pinned():
    assert "bomb" in UNSAFE_OBJECT_TERMS
    assert "gun" in UNSAFE_OBJECT_TERMS
    assert "explosive" in UNSAFE_OBJECT_TERMS
    assert KNOWN_OBJECT_TABLE
    assert TRIGGER_GAP_MAX == 2


# --------------------------------------------------------------------------- #
# locked weapon surface
# --------------------------------------------------------------------------- #


def test_locked_weapon_emits_known_request():
    locked = LockedConstraints(weapon="Kitchen knife")
    extracted = _extract("Nothing to see here.", locked)
    names = {request.requested_name for request in extracted.objects}
    assert "kitchen knife" in names


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #


def test_same_input_produces_identical_world_requirements():
    prompt = SHOWCASE_PROMPTS["warehouse"]
    first = _extract(prompt)
    second = _extract(prompt)
    assert first == second
    assert repr(first) == repr(second)
    assert first.objects == second.objects
    assert first.relations == second.relations


def test_showcase_extraction_matches_expected_summaries():
    for environment_id, expectation in SHOWCASE_EXPECTED.items():
        extracted = _extract(SHOWCASE_PROMPTS[environment_id])
        assert extracted.environment_hint == expectation.environment_hint
        names = tuple(request.requested_name for request in extracted.objects)
        assert set(names) == set(expectation.expected_object_names)
        for kind, targets in expectation.relation_targets.items():
            got = {r.target for r in extracted.relations if r.kind == kind}
            assert got == set(targets), (environment_id, kind, got)


def test_base_object_flag_matches_golden_objects():
    assert is_base_object_request("kitchen knife") is True
    assert is_base_object_request("letter opener") is True
    assert is_base_object_request("adjustable wrench") is False
    assert is_base_object_request("Custom Trophy") is False
    assert is_base_object_request("totally unknown thing") is False


def test_environment_families_are_the_five_documented_ones():
    envs = {environment_id for environment_id, _aliases in ENVIRONMENT_FAMILIES}
    assert envs == {"apartment", "office", "hotel_suite", "warehouse", "mansion"}