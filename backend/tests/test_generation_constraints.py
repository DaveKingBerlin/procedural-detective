"""Locked-constraints + prompt normalization tests (REQUIREMENTS 7.2 / 33 / 48)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_LOCKED,
)

from app.generation.constraints import LockedConstraints  # noqa: E402
from app.generation.parser import parse_full_draft  # noqa: E402
from app.generation.prompt import PromptError, parse_prompt  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    CrimeSpec,
    CrimeTimeSpec,
    GeneratedDraft,
)

# REQUIREMENTS §48 Case A in canonical/id form. The fixture documents the
# mapping surface forms -> canonical ids (Sarah Miller -> sarah_miller,
# 22:17 -> 2026-09-11T22:17:00+02:00, ...).
CASE_A_CANONICAL_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed"
)

# REQUIREMENTS §48 Case A in its natural-language surface form.
CASE_A_NATURAL_PROMPT = (
    "Victim: Sarah Miller\n"
    "Murderer: Thomas Reed\n"
    "Motive: \u20ac240,000 embezzlement\n"
    "Weapon: Kitchen knife\n"
    "Time: 22:17\n"
    "Witness: Emily Reed"
)


def _golden_draft() -> GeneratedDraft:
    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    return draft


# ---------------------------------------------------------------------------
# parse_prompt
# ---------------------------------------------------------------------------


def test_case_a_prompt_yields_exactly_golden_locked():
    locked, note = parse_prompt(CASE_A_CANONICAL_PROMPT)
    assert locked == GOLDEN_LOCKED
    assert "victim" in note and "witness" in note


def test_natural_language_case_a_values_are_verbatim():
    # Values are identity strings: trim/case only, never smart-normalized.
    locked, _note = parse_prompt(CASE_A_NATURAL_PROMPT)
    assert locked.victim == "Sarah Miller"
    assert locked.murderer == "Thomas Reed"
    assert locked.motive == "\u20ac240,000 embezzlement"
    assert locked.weapon == "Kitchen knife"
    assert locked.crime_time == "22:17"
    assert locked.witness == "Emily Reed"


def test_unknown_keys_ignored_with_note():
    locked, note = parse_prompt("Victim: sarah_miller\nDifficulty: Hard\nWeather: rainy")
    assert locked == LockedConstraints(victim="sarah_miller")
    assert "weather" not in note
    assert "victim" in note


def test_duplicate_key_raises():
    with pytest.raises(PromptError, match="duplicate"):
        parse_prompt("Victim: a\nVictim: b")
    # "Time:" and "Crime time:" normalize to the same key -> duplicate.
    with pytest.raises(PromptError, match="duplicate"):
        parse_prompt("Time: 22:17\nCrime time: 22:18")


def test_prompt_too_long_raises():
    with pytest.raises(PromptError, match="too long"):
        parse_prompt("Victim: " + "x" * 100, max_chars=50)


def test_empty_prompt_returns_empty_constraints():
    locked, note = parse_prompt("")
    assert locked == LockedConstraints()
    assert note


def test_malformed_prompt_returns_empty_constraints():
    locked, note = parse_prompt("this has no colons at all")
    assert locked == LockedConstraints()
    assert note == "no structured constraints recognized"


def test_case_insensitive_keys_and_whitespace():
    locked, _note = parse_prompt(
        "  VICTIM: sarah_miller \n  MURDERER  : thomas_reed \n"
    )
    assert locked.victim == "sarah_miller"
    assert locked.murderer == "thomas_reed"


def test_crime_time_key_alias():
    locked, _note = parse_prompt("Crime time: 2026-09-11T22:17:00+02:00")
    assert locked.crime_time == "2026-09-11T22:17:00+02:00"


def test_none_of_the_keys_present_note():
    _locked, note = parse_prompt("Create a murder mystery in a hotel.")
    assert note == "no structured constraints recognized"


# ---------------------------------------------------------------------------
# violations_against
# ---------------------------------------------------------------------------


def test_golden_locked_has_no_violations():
    assert GOLDEN_LOCKED.violations_against(_golden_draft()) == ()


def _mutated_draft(**crime_overrides) -> GeneratedDraft:
    draft = _golden_draft()
    crime = draft.crime
    new_crime = CrimeSpec(
        type=crime_overrides.get("type", crime.type),
        victim_id=crime_overrides.get("victim_id", crime.victim_id),
        murderer_id=crime_overrides.get("murderer_id", crime.murderer_id),
        motive_id=crime_overrides.get("motive_id", crime.motive_id),
        weapon_id=crime_overrides.get("weapon_id", crime.weapon_id),
        location_id=crime_overrides.get("location_id", crime.location_id),
        crime_time=crime_overrides.get("crime_time", crime.crime_time),
    )
    return GeneratedDraft(
        crime=new_crime,
        persons=draft.persons,
        motives=draft.motives,
        objects=draft.objects,
        locations=draft.locations,
        travel_rules=draft.travel_rules,
        scene=draft.scene,
        evidence=draft.evidence,
        world_graph=draft.world_graph,
    )


def test_changed_murderer_weapon_time_flagged():
    draft = _mutated_draft(
        murderer_id="anna_karlsson",
        weapon_id="letter_opener",
        crime_time=CrimeTimeSpec(
            canonical="2026-09-11T22:30:00+02:00", accusation_tolerance_seconds=120
        ),
    )
    issues = GOLDEN_LOCKED.violations_against(draft)
    assert any("murderer" in message for message in issues)
    assert any("weapon" in message for message in issues)
    assert any("crime_time" in message for message in issues)


def test_ids_compared_case_insensitive():
    draft = _mutated_draft(murderer_id="Thomas_Reed")
    assert GOLDEN_LOCKED.violations_against(draft) == ()


def test_crime_time_compare_is_tick_exact_across_timezones():
    # 22:17+02:00 == 20:17 UTC == 21:17+01:00 == 19:17-01:00 — same tick.
    for spelling in (
        "2026-09-11T20:17:00+00:00",
        "2026-09-11T20:17:00Z",
        "2026-09-11T21:17:00+01:00",
        "2026-09-11T19:17:00-01:00",
    ):
        draft = _mutated_draft(
            crime_time=CrimeTimeSpec(canonical=spelling, accusation_tolerance_seconds=120)
        )
        assert GOLDEN_LOCKED.violations_against(draft) == (), spelling


def test_crime_time_different_tick_flagged():
    draft = _mutated_draft(
        crime_time=CrimeTimeSpec(
            canonical="2026-09-11T22:17:01+02:00", accusation_tolerance_seconds=120
        )
    )
    issues = GOLDEN_LOCKED.violations_against(draft)
    assert any("crime_time" in message for message in issues)


def test_witness_person_existence_checked():
    draft = _golden_draft()
    filtered = GeneratedDraft(
        crime=draft.crime,
        persons=tuple(p for p in draft.persons if p.person_id != "emily_reed"),
        motives=draft.motives,
        objects=draft.objects,
        locations=draft.locations,
        travel_rules=draft.travel_rules,
        scene=draft.scene,
        evidence=draft.evidence,
        world_graph=draft.world_graph,
    )
    issues = GOLDEN_LOCKED.violations_against(filtered)
    assert any("witness" in message for message in issues)


def test_unlocked_fields_are_ignored():
    empty = LockedConstraints()
    draft = _mutated_draft(
        murderer_id="anna_karlsson",
        weapon_id="letter_opener",
        crime_time=CrimeTimeSpec(
            canonical="2026-09-11T22:30:00+02:00", accusation_tolerance_seconds=120
        ),
    )
    assert empty.violations_against(draft) == ()