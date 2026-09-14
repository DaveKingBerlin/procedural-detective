"""DEF-054 — generic locked-constraint normalization/equivalence layer.

Regression: the REQUIREMENTS 3.1 / 48 human-readable demo prompt

    Victim: Sarah Miller
    Murderer: Thomas Reed
    Motive: €240,000 embezzlement
    Weapon: Kitchen knife
    Time: 22:17
    Witness: Emily Reed

deterministically FAILED generation: ``prompt.parse_prompt`` locked the six raw
human values verbatim and ``LockedConstraints.violations_against`` compared them
against the golden snake_case draft ids with only whitespace/case folding
("Thomas Reed" != "thomas_reed", "Kitchen knife" != "kitchen_knife",
"€240,000 embezzlement" != "cover_up_embezzlement", "22:17" was not a parseable
ISO timestamp, "Emily Reed" != "emily_reed") -> 6/6 locked violations ->
TERMINAL_FAILURE -> status FAILED every time. The identical values in canonical
id form published, so the browser demo entries ("Try Demo Case" / "Use example
prompt") shipped a prompt the backend was guaranteed to reject.

Fix (``app.generation.constraints``): comparison-operator-level generic
equivalence — documented normalization rules:

1. Identity fields (victim / murderer / weapon / witness): ``normalize_identity``
   = Unicode ``casefold`` + keep ONLY ASCII letters + digits; EXACT equality.
   "Thomas Reed" == "thomas_reed" == "THOMAS REED!" -> ``thomasreed``. A
   witness lock also matches the witness person's NAME; a victim/murderer lock
   also matches the linked draft person's NAME.
2. Motive: ``normalize_motive_text`` = casefold + keep ASCII letters/digits AND
   currency symbols (``$ £ ¥ €``); the locked value matches the draft's winning
   motive id/label when the normalized locked text is a CONTIGUOUS substring of
   the normalized id/label — or the reverse (either direction). Defensive floor
   (DEF-055): a normalized locked motive shorter than ``MIN_MOTIVE_NORM_LEN``
   (4) is a VIOLATION — degenerate 1-3 char locks ("e", "€", "o", "hi", "XYZ")
   can never be verified and must not implicit-match.
3. crime_time: ``parse_locked_time_to_epoch`` — the locked value and draft
   canonical are compared as the SAME UTC epoch tick; bare "H:MM[:SS]" /
   "HH:MM[:SS]" (optionally with date/offset) is anchored to the draft
   canonical crime DATE + timezone offset.
4. Unknown values stay exact: a lock that matches NOTHING after normalization
   is still a TERMINAL violation.

Locked here:
(a) the exact §48 human prompt POSTs through the real API flow with the default
    fake dev-mode provider and reaches PUBLISHED with zero locked violations;
(b) every equivalence rule is unit-tested (name<->id, motive substring with
    currency/punctuation, bare-time vs full-ISO equality);
(c) a genuinely-mismatched lock ("Dave Smith" as murderer) STILL fails TERMINAL;
(d) the pre-existing canonical-id locked fixture stays green.
(DEF-055) plus degenerate "Motive: e"-style locks FAIL (unit + live API), the
"+" meaningful motives still match, a short-but-meaningful 4-char motive id
still matches at the floor, and identity/time controls are unchanged.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_LOCKED,
)

from app.domain.time_interval import (  # noqa: E402
    parse_iso8601_to_epoch,
    parse_locked_time_to_epoch,
)
from app.generation.constraints import (  # noqa: E402
    MIN_MOTIVE_NORM_LEN,
    LockedConstraints,
    normalize_identity,
    normalize_motive_text,
)
from app.generation.parser import parse_full_draft  # noqa: E402
from app.generation.prompt import parse_prompt  # noqa: E402
from app.generation.report import ValidationReport  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    CrimeSpec,
    CrimeTimeSpec,
    GeneratedDraft,
    MotiveSpec,
)
from app.generation.state_machine import ValidationOutcome  # noqa: E402

# The verbatim REQUIREMENTS 3.1 / 48 Case A example the browser ships
# ("Try Demo Case" / "Use example prompt").
CASE_A_HUMAN_PROMPT = (
    "Victim: Sarah Miller\n"
    "Murderer: Thomas Reed\n"
    "Motive: \u20ac240,000 embezzlement\n"
    "Weapon: Kitchen knife\n"
    "Time: 22:17\n"
    "Witness: Emily Reed"
)

# The same golden values in canonical id form (the pre-existing green flow).
CASE_A_CANONICAL_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed"
)

_CANONICAL = "2026-09-11T22:17:00+02:00"


def _golden_draft() -> GeneratedDraft:
    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    assert draft is not None
    return draft


def _draft_time(canonical: str) -> GeneratedDraft:
    draft = _golden_draft()
    return GeneratedDraft(
        crime=CrimeSpec(
            type=draft.crime.type,
            victim_id=draft.crime.victim_id,
            murderer_id=draft.crime.murderer_id,
            motive_id=draft.crime.motive_id,
            weapon_id=draft.crime.weapon_id,
            location_id=draft.crime.location_id,
            crime_time=CrimeTimeSpec(
                canonical=canonical, accusation_tolerance_seconds=120
            ),
        ),
        persons=draft.persons,
        motives=draft.motives,
        objects=draft.objects,
        locations=draft.locations,
        travel_rules=draft.travel_rules,
        scene=draft.scene,
        evidence=draft.evidence,
        world_graph=draft.world_graph,
    )


def _draft_with_motive(motive_id: str, label: str) -> GeneratedDraft:
    """Golden draft whose WINNING motive is swapped to ``(motive_id, label)``
    (the MotiveSpec collection stays consistent with the crime)."""
    draft = _golden_draft()
    motives = tuple(
        replace(m, motive_id=motive_id, label=label)
        if m.motive_id == draft.crime.motive_id
        else m
        for m in draft.motives
    )
    return GeneratedDraft(
        crime=replace(draft.crime, motive_id=motive_id),
        persons=draft.persons,
        motives=motives,
        objects=draft.objects,
        locations=draft.locations,
        travel_rules=draft.travel_rules,
        scene=draft.scene,
        evidence=draft.evidence,
        world_graph=draft.world_graph,
    )


# ---------------------------------------------------------------------------
# (a) the full demo path: prompt -> locked -> real API generation -> PUBLISHED
# ---------------------------------------------------------------------------


def test_human_prompt_publishes_through_api_with_zero_locked_violations(
    phase5_migrated_client,
):
    """The exact §48 human prompt reaches PUBLISHED through POST /api/v1/cases
    (real API flow, default fake dev-mode provider) — the browser "Try Demo
    Case" / "Use example prompt" journey works end-to-end."""
    client = phase5_migrated_client
    session = client.post("/api/v1/sessions/anonymous")
    assert session.status_code == 201
    token = session.json()["anonymousSessionToken"]

    created = client.post(
        "/api/v1/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": CASE_A_HUMAN_PROMPT, "difficulty": "medium"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "PUBLISHED", body

    # The same prompt locks exactly the six human values, and the golden draft
    # respects all six locks under the equivalence layer.
    locked, note = parse_prompt(CASE_A_HUMAN_PROMPT)
    assert locked.victim == "Sarah Miller"
    assert locked.murderer == "Thomas Reed"
    assert locked.motive == "\u20ac240,000 embezzlement"
    assert locked.weapon == "Kitchen knife"
    assert locked.crime_time == "22:17"
    assert locked.witness == "Emily Reed"
    assert locked.violations_against(_golden_draft()) == ()


def test_canonical_id_prompt_still_publishes_and_matches_golden_locked(
    phase5_migrated_client,
):
    """The canonical id-form prompt (pre-existing green flow) keeps working
    and still locks exactly GOLDEN_LOCKED (DEF-054 requirement 5)."""
    locked, _note = parse_prompt(CASE_A_CANONICAL_PROMPT)
    assert locked == GOLDEN_LOCKED
    assert locked.violations_against(_golden_draft()) == ()


# ---------------------------------------------------------------------------
# (b) equivalence rules — unit level
# ---------------------------------------------------------------------------


def test_name_id_equivalence_every_identity_field():
    draft = _golden_draft()
    # Human display names match the snake_case ids ("Thomas Reed" -> thomasreed).
    assert LockedConstraints(victim="Sarah Miller").violations_against(draft) == ()
    assert LockedConstraints(murderer="Thomas Reed").violations_against(draft) == ()
    assert LockedConstraints(weapon="Kitchen knife").violations_against(draft) == ()
    assert LockedConstraints(witness="Emily Reed").violations_against(draft) == ()
    # Punctuation / case / extra whitespace are all dropped deterministically.
    assert LockedConstraints(murderer="THOMAS REED!").violations_against(draft) == ()
    assert LockedConstraints(weapon="  kitchen-knife ").violations_against(draft) == ()
    assert LockedConstraints(victim="Sarah_Miller").violations_against(draft) == ()


def test_normalize_identity_rules():
    assert normalize_identity("Thomas Reed") == "thomasreed"
    assert normalize_identity("thomas_reed") == "thomasreed"
    assert normalize_identity("THOMAS REED! \u20ac2,000") == "thomasreed2000"
    assert normalize_identity("") == ""


def test_locked_name_matches_linked_person_display_name():
    """A locked human name matches even when the draft id is NOT the name id
    (generic name<->id equivalence, not hardcoded Sarah/Thomas trivia)."""
    draft = _golden_draft()
    murderer = next(p for p in draft.persons if p.person_id == "thomas_reed")
    renamed_persons = tuple(
        replace(murderer, person_id="culprit_x") if p.person_id == "thomas_reed" else p
        for p in draft.persons
    )
    renamed = GeneratedDraft(
        crime=CrimeSpec(
            type=draft.crime.type,
            victim_id=draft.crime.victim_id,
            murderer_id="culprit_x",
            motive_id=draft.crime.motive_id,
            weapon_id=draft.crime.weapon_id,
            location_id=draft.crime.location_id,
            crime_time=draft.crime.crime_time,
        ),
        persons=renamed_persons,
        motives=draft.motives,
        objects=draft.objects,
        locations=draft.locations,
        travel_rules=draft.travel_rules,
        scene=draft.scene,
        evidence=draft.evidence,
        world_graph=draft.world_graph,
    )
    assert LockedConstraints(murderer="Thomas Reed").violations_against(renamed) == ()
    # The id-based lock still works against the renamed person too.
    assert LockedConstraints(murderer="culprit_x").violations_against(renamed) == ()


def test_motive_substring_currency_and_punctuation():
    draft = _golden_draft()
    # "€240,000 embezzlement" -> €240000embezzlement, a contiguous substring of
    # "Cover up the €240,000 embezzlement" -> coverupthe€240000embezzlement.
    assert (
        LockedConstraints(motive="\u20ac240,000 embezzlement").violations_against(draft)
        == ()
    )
    # Plain substring, either direction, normalized.
    assert LockedConstraints(motive="EMBEZZLEMENT").violations_against(draft) == ()
    assert (
        LockedConstraints(motive="cover up the \u20ac240,000 embezzlement").violations_against(
            draft
        )
        == ()
    )
    # A motive that matches nothing still fails (immutability).
    issues = LockedConstraints(motive="blackmail").violations_against(draft)
    assert any("motive" in issue for issue in issues)


def test_normalize_motive_text_rules():
    assert normalize_motive_text("\u20ac240,000 embezzlement") == "\u20ac240000embezzlement"
    assert (
        normalize_motive_text("Cover up the \u20ac240,000 embezzlement")
        == "coverupthe\u20ac240000embezzlement"
    )
    assert (
        normalize_motive_text("\u20ac240000embezzlement")
        in normalize_motive_text("Cover up the \u20ac240,000 embezzlement")
    )


def test_bare_time_matches_full_iso_exactly_on_the_same_tick():
    draft = _golden_draft()
    assert LockedConstraints(crime_time="22:17").violations_against(draft) == ()
    assert LockedConstraints(crime_time="22:17:00").violations_against(draft) == ()
    assert (
        LockedConstraints(crime_time="2026-09-11T22:17:00+02:00").violations_against(draft)
        == ()
    )
    assert LockedConstraints(crime_time="20:17Z").violations_against(draft) == ()
    assert (
        LockedConstraints(crime_time="2026-09-11 22:17").violations_against(draft) == ()
    )


def test_bare_time_different_tick_still_violates():
    draft = _draft_time("2026-09-11T22:17:01+02:00")
    issues = LockedConstraints(crime_time="22:17").violations_against(draft)
    assert any("crime_time" in issue for issue in issues)


def test_parse_locked_time_single_digit_hour_and_forms():
    # H:MM / H:MM:SS / HH:MM:SS / full ISO all reduce to the same tick.
    anchor = _CANONICAL
    expected = parse_locked_time_to_epoch("2026-09-11T02:17:00+02:00", anchor)
    assert parse_locked_time_to_epoch("2:17", anchor) == expected
    assert parse_locked_time_to_epoch("2:17:00", anchor) == expected
    assert parse_locked_time_to_epoch("02:17:00", anchor) == expected
    assert parse_locked_time_to_epoch("2026-09-11 2:17", anchor) == expected
    # Explicit offsets are honored, dates default to the anchor's date.
    assert (
        parse_locked_time_to_epoch("22:17+02:00", anchor)
        == parse_iso8601_to_epoch(anchor)
    )
    assert (
        parse_locked_time_to_epoch("20:17Z", anchor)
        == parse_iso8601_to_epoch(anchor)
    )
    # Exactly the same tick the golden canonical parses to.
    assert parse_locked_time_to_epoch("22:17", anchor) == parse_iso8601_to_epoch(anchor)


def test_unparseable_locked_time_is_a_violation():
    draft = _golden_draft()
    issues = LockedConstraints(crime_time="22:17:60").violations_against(draft)
    assert any("crime_time" in issue for issue in issues)
    issues = LockedConstraints(crime_time="sometime").violations_against(draft)
    assert any("crime_time" in issue for issue in issues)


# ---------------------------------------------------------------------------
# (c) genuinely-mismatched locked values stay TERMINAL
# ---------------------------------------------------------------------------


def test_mismatched_locked_murderer_remains_terminal():
    draft = _golden_draft()
    locked = LockedConstraints(murderer="Dave Smith")
    issues = locked.violations_against(draft)
    assert any("murderer" in issue for issue in issues)
    report = ValidationReport(locked_violations=tuple(issues))
    assert report.outcome is ValidationOutcome.TERMINAL_FAILURE


def test_mismatched_locked_victim_and_witness_remain_terminal():
    draft = _golden_draft()
    locked = LockedConstraints(victim="Dave Smith", witness="Jane Doe")
    report = ValidationReport(locked_violations=locked.violations_against(draft))
    assert report.outcome is ValidationOutcome.TERMINAL_FAILURE


def test_out_of_universe_canonical_id_murderer_is_still_a_violation():
    """Rule 4: a LOCKED value must match—even in canonical id form a wrong id
    ('anna_karlsson' is not the golden murderer) must still fail."""
    draft = _golden_draft()
    issues = LockedConstraints(murderer="anna_karlsson").violations_against(draft)
    assert any("murderer" in issue for issue in issues)


# ---------------------------------------------------------------------------
# (d) pre-existing canonical-id locked flows stay green
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    (
        "2026-09-11T20:17:00+00:00",
        "2026-09-11T20:17:00Z",
        "2026-09-11T21:17:00+01:00",
        "2026-09-11T19:17:00-01:00",
    ),
)
def test_golden_locked_tick_exact_across_timezones(spelling):
    draft = _draft_time(spelling)
    assert GOLDEN_LOCKED.violations_against(draft) == ()


def test_golden_locked_against_published_golden_draft():
    assert GOLDEN_LOCKED.violations_against(_golden_draft()) == ()


# ---------------------------------------------------------------------------
# DEF-055 — minimum-relevance floor for normalized motive locks
#
# A 1-3 char normalized lock ("e", "€", "o", "hi", "XYZ", "!!!") is a
# substring of nearly any motive id/label, so the DEF-054 contiguous-substring
# rule would PUBLISH "Motive: e" against the totally unrelated winning motive
# (cover_up_embezzlement). Degenerate locks shorter than MIN_MOTIVE_NORM_LEN
# are now deterministic VIOLATIONS; everything at/above the floor keeps its
# exact DEF-054 behavior.
# ---------------------------------------------------------------------------


def test_motive_floor_constant_is_documented_and_sized():
    # The floor: 4 normalized characters. Rejects every degenerate 1-3 char
    # token; accepts the golden material and every plausible real motive id
    # (shortest meaningful single-token ids like spite/envy/greed/hate >= 4).
    assert MIN_MOTIVE_NORM_LEN == 4
    assert len(normalize_motive_text("embezzlement")) >= MIN_MOTIVE_NORM_LEN
    assert len(normalize_motive_text("\u20ac240,000 embezzlement")) >= MIN_MOTIVE_NORM_LEN
    for degenerate in ("e", "\u20ac", "o", "hi", "XYZ"):
        assert len(normalize_motive_text(degenerate)) < MIN_MOTIVE_NORM_LEN, degenerate


@pytest.mark.parametrize("degenerate", ("e", "\u20ac", "o", "hi", "XYZ", "!!!"))
def test_degenerate_short_motive_locks_are_terminal_violations(degenerate):
    draft = _golden_draft()
    locked = LockedConstraints(motive=degenerate)
    issues = locked.violations_against(draft)
    assert any("motive" in issue for issue in issues), degenerate
    report = ValidationReport(locked_violations=tuple(issues))
    assert report.outcome is ValidationOutcome.TERMINAL_FAILURE, degenerate


def test_degenerate_motive_prompt_fails_through_the_api(phase5_migrated_client):
    """Live API control: POST /cases with 'Motive: e' -> status FAILED (the
    DEF-055 poison input) while the golden human prompt PUBLISHES."""
    client = phase5_migrated_client
    token = client.post("/api/v1/sessions/anonymous").json()["anonymousSessionToken"]

    degenerate = client.post(
        "/api/v1/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": "Motive: e", "difficulty": "medium"},
    )
    assert degenerate.status_code == 201, degenerate.text
    assert degenerate.json()["status"] == "FAILED", degenerate.text

    meaningful = client.post(
        "/api/v1/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": CASE_A_HUMAN_PROMPT, "difficulty": "medium"},
    )
    assert meaningful.json()["status"] == "PUBLISHED", meaningful.text


@pytest.mark.parametrize(
    "meaningful",
    (
        "\u20ac240,000 embezzlement",  # DEF-054 "+" human motive (currency)
        "cover_up_embezzlement",  # canonical id
        "EMBEZZLEMENT",  # plain substring of the id/label
        "cover up the \u20ac240,000 embezzlement",  # full label (reverse rule)
    ),
)
def test_meaningful_motive_locks_still_match(meaningful):
    draft = _golden_draft()
    assert LockedConstraints(motive=meaningful).violations_against(draft) == (), meaningful


def test_motive_floor_boundary_exact_four_chars_matches():
    # "envy" is EXACTLY 4 normalized chars — AT the floor and fully meaningful:
    # a lock equal to its own short motive id must still match.
    draft = _draft_with_motive("envy", "Envy")
    assert LockedConstraints(motive="envy").violations_against(draft) == ()


def test_short_full_label_lock_still_matches_at_floor():
    # DEF-055 requirement: a single-token lock that is the FULL normalized
    # label/id still matches at/above the floor. "greed" (5) on a "Greed"
    # winning motive matches via the reverse rule (id == needle).
    draft = _draft_with_motive("greed", "Greed")
    assert LockedConstraints(motive="greed").violations_against(draft) == ()
    # A 3-char id (e.g. "axe") is BELOW the floor: the floor refuses to guess,
    # even against an exactly-matching motive — the documented tradeoff for
    # rejecting degenerate locks (the golden and public motive vocabulary never
    # use < 4-char ids; the parser requires non-empty strings only, so the
    # floor is the meaning guard).
    below = _draft_with_motive("axe", "Axe")
    issues = LockedConstraints(motive="axe").violations_against(below)
    assert any("motive" in issue for issue in issues)


def test_giraffe_motive_still_fails():
    """Control: a meaningful-length lock that matches NOTHING still fails."""
    draft = _golden_draft()
    locked = LockedConstraints(motive="blackmail for the giraffe")
    issues = locked.violations_against(draft)
    assert any("motive" in issue for issue in issues)
    report = ValidationReport(locked_violations=tuple(issues))
    assert report.outcome is ValidationOutcome.TERMINAL_FAILURE


def test_def055_does_not_weaken_identity_or_time_controls():
    draft = _golden_draft()
    # A degenerate motive lock cannot mask an exact-equality identity mismatch.
    combined = LockedConstraints(motive="e", murderer="Dave Smith")
    issues = combined.violations_against(draft)
    assert any("motive" in issue for issue in issues)
    assert any("murderer" in issue for issue in issues)
    report = ValidationReport(locked_violations=tuple(issues))
    assert report.outcome is ValidationOutcome.TERMINAL_FAILURE

    # Time control unchanged: a short motive lock plus a wrong tick still flags
    # the crime_time (the equivalence layer only relaxes the MOTIVE operator).
    timed = LockedConstraints(motive="e", crime_time="22:17").violations_against(
        _draft_time("2026-09-11T23:00:00+02:00")
    )
    assert any("motive" in issue for issue in timed)
    assert any("crime_time" in issue for issue in timed)