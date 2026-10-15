"""Phase43 Wave A — provider-neutral trusted context + crime-schema alignment.

Hermetic (ZERO provider calls). Covers the Wave A foundation contracts:

- P43-FR-01: the canonical locked-identity sheet carries both the canonical id
  and the display value, and NEVER presents a display name as an id value;
- P43-FR-02: the compact ``Location:`` lock is recognized and appears in the
  trusted locked-identity sheet as a canonical id;
- P43-FR-05: the ``case_truth`` (parser-shaped) schema exposes a
  ``resolutionType`` enum and a null-valid ``murdererId``;
- P43-FR-06: the shared Phase 41A quality block is exported and wired into the
  persons-bearing templates.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation import parser  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation import trusted_context as tc  # noqa: E402
from app.generation.constraints import LockedConstraints  # noqa: E402
from app.generation.prompt import parse_prompt  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    CrimeSpec,
    CrimeTimeSpec,
    GeneratedDraft,
    LocationSpec,
    SceneSpec,
)

# The Phase43 §17.1 compact prompt (Location: included).
_PROMPT = (
    "Victim: Laura Stein\n"
    "Murderer: Daniel Roth\n"
    "Motive: financial gain\n"
    "Weapon: kitchen knife\n"
    "Time: 20:15\n"
    "Witness: Nina Weber\n"
    "Location: apartment\n"
)

_CANONICAL = "2026-09-11T23:42:00+02:00"


class _Attempt:
    def __init__(self, locked):
        self.locked = locked


# --------------------------------------------------------------------------- #
# P43-FR-02 — the Location lock is recognized
# --------------------------------------------------------------------------- #


def test_location_lock_is_recognized():
    locked, note = parse_prompt(_PROMPT)
    assert locked.location == "apartment"
    assert "location" in note


def test_location_lock_absent_is_a_noop():
    locked, _note = parse_prompt("Victim: Laura Stein\n")
    assert locked.location is None
    # No location lock -> no location_id token (an absent lock is a no-op).
    assert "location_id" not in tc.locked_id_sheet(_Attempt(locked))
    assert tc.locked_id_sheet(_Attempt(None)) == ""


def test_locked_location_matches_declared_scene_environment():
    crime = CrimeSpec(
        type="murder",
        victim_id="anna_weiss",
        murderer_id="paul_becker",
        motive_id="stolen",
        weapon_id="kitchen_knife",
        location_id="konsortium_office",
        crime_time=CrimeTimeSpec(
            canonical=_CANONICAL, accusation_tolerance_seconds=120
        ),
    )
    draft = GeneratedDraft(
        crime=crime,
        locations=(
            LocationSpec(location_id="konsortium_office", name="Konsortium Office"),
        ),
        scene=SceneSpec(
            location_id="konsortium_office",
            name="Konsortium Office",
            environment_id="office",
        ),
    )
    assert LockedConstraints(location="office").violations_against(draft) == ()
    # A genuinely different location lock is still a violation.
    assert LockedConstraints(location="apartment").violations_against(draft)


def test_location_lock_binds_scene_not_a_red_herring_location():
    """DEF-068 (ADV-43-09) — ``Location: apartment`` binds the SCENE /
    environment, so an unrelated declared red-herring location named
    ``apartment`` cannot mask a scene mismatch."""
    crime = CrimeSpec(
        type="murder",
        victim_id="anna_weiss",
        murderer_id="paul_becker",
        motive_id="stolen",
        weapon_id="kitchen_knife",
        location_id="miller_apartment_kitchen",
        crime_time=CrimeTimeSpec(
            canonical=_CANONICAL, accusation_tolerance_seconds=120
        ),
    )
    draft = GeneratedDraft(
        crime=crime,
        locations=(
            # An unrelated red-herring location named exactly like the lock.
            LocationSpec(location_id="apartment", name="apartment"),
        ),
        scene=SceneSpec(
            location_id="miller_apartment_kitchen",
            name="Kitchen",
            environment_id="kitchen",
        ),
    )
    violations = LockedConstraints(location="apartment").violations_against(draft)
    assert violations, "the red-herring location must NOT satisfy the lock"
    # The matching scene environment still satisfies the lock.
    matching = GeneratedDraft(
        crime=crime,
        locations=draft.locations,
        scene=SceneSpec(
            location_id="miller_apartment_kitchen",
            name="Kitchen",
            environment_id="apartment",
        ),
    )
    assert LockedConstraints(location="apartment").violations_against(matching) == ()


def test_empty_locked_value_is_treated_as_absent():
    """DEF-067 (ADV-43-08) — an empty/whitespace locked value is absent: no empty
    ``canonicalId``/``displayName`` enters the authoritative sheet."""
    locked, note = parse_prompt("Victim: Laura Stein\nLocation:   \n")
    assert "location" in note  # the key was recognized...
    assert locked.location is None  # ...but an empty value is ABSENT
    assert all(
        identity.field != "location" for identity in tc.locked_identities(locked)
    )
    sheet = tc.locked_id_sheet(_Attempt(locked))
    assert "- location_id:" not in sheet
    rendered = tc.render_locked_identity_sheet(locked)
    assert "canonicalId: \n" not in rendered
    assert "canonicalId:  \n" not in rendered
    # Defensive: a directly-constructed empty value is skipped too.
    assert all(
        identity.field != "location"
        for identity in tc.locked_identities(LockedConstraints(location=""))
    )


# --------------------------------------------------------------------------- #
# P43-FR-01 — canonical identity + display value; display names are never ids
# --------------------------------------------------------------------------- #


def test_locked_identities_carry_canonical_id_and_display_name():
    locked, _note = parse_prompt(_PROMPT)
    identities = {identity.field: identity for identity in tc.locked_identities(locked)}
    assert identities["victim"].canonical_id == "laura_stein"
    assert identities["victim"].display_name == "Laura Stein"
    assert identities["murderer"].canonical_id == "daniel_roth"
    assert identities["location"].canonical_id == "apartment"
    assert identities["location"].display_name == "apartment"
    assert identities["weapon"].canonical_id == tc.semantic_object_id("kitchen knife")


def test_locked_id_sheet_includes_canonical_location_id():
    locked, _note = parse_prompt(_PROMPT)
    sheet = tc.locked_id_sheet(_Attempt(locked))
    assert "- location_id: apartment" in sheet
    assert "- victim_id: laura_stein" in sheet
    assert sheet  # non-empty
    # ``None`` locked constraints -> empty sheet.
    assert tc.locked_id_sheet(_Attempt(None)) == ""


def test_id_only_sheet_never_lists_a_display_name():
    """The authoritative id-only sheet lists canonical ids, never raw display
    names (a display name must never look like a legal id token)."""
    locked, _note = parse_prompt(_PROMPT)
    sheet = tc.locked_id_sheet(_Attempt(locked))
    assert "Laura Stein" not in sheet
    assert "Daniel Roth" not in sheet
    assert "Nina Weber" not in sheet


def test_render_locked_identity_sheet_separates_id_from_display_name():
    locked, _note = parse_prompt(_PROMPT)
    rendered = tc.render_locked_identity_sheet(locked)
    # every entity carries a canonicalId and a clearly-separated displayName.
    assert "canonicalId: laura_stein" in rendered
    assert "displayName (human-readable context ONLY, never a legal id): Laura Stein" in rendered
    # and the rule is stated explicitly.
    assert "NEVER put a displayName in an *Id field" in rendered
    # the location lock is represented in the authoritative sheet.
    assert "canonicalId: apartment" in rendered


# --------------------------------------------------------------------------- #
# P43-FR-05 — crime schema alignment (resolutionType + nullable murdererId)
# --------------------------------------------------------------------------- #


def test_case_truth_schema_exposes_resolution_type_and_null_murderer():
    schema = prompts.json_schema_for_stage_output("case_truth")
    crime = schema["properties"]["crime"]
    props = crime["properties"]
    assert props["resolutionType"] == {
        "type": "string",
        "enum": ["HOMICIDE", "SUICIDE"],
    }
    # resolutionType is REQUIRED (its hint deliberately avoids the "null" token).
    assert "resolutionType" in crime["required"]
    # murdererId is OPTIONAL and admits a JSON null (SUICIDE) or a string.
    assert "murdererId" not in crime["required"]
    assert props["murdererId"]["type"] == ["string", "null"]
    # the top-level parser-shaped wrapping is unchanged.
    assert set(schema["properties"]) == {"crime"}


def test_case_truth_schema_accepted_shape_superset_of_parser():
    """The schema admits BOTH a homicide string murderer and a suicide null;
    the canonical parser accepts both (validators are not weakened)."""
    base = {
        "type": "murder",
        "victimId": "anna_weiss",
        "motiveId": "stolen",
        "weaponId": "kitchen_knife",
        "locationId": "office",
        "crimeTime": {
            "canonical": _CANONICAL,
            "accusationToleranceSeconds": 120,
        },
    }
    suicide = dict(base, resolutionType="SUICIDE", murdererId=None)
    assert parser.parse_stage(
        GenerationStage.CASE_TRUTH,
        json.dumps({"crime": suicide}),
        non_throwing=False,
    )
    homicide = dict(base, resolutionType="HOMICIDE", murdererId="paul_becker")
    assert parser.parse_stage(
        GenerationStage.CASE_TRUTH,
        json.dumps({"crime": homicide}),
        non_throwing=False,
    )


# --------------------------------------------------------------------------- #
# P43-FR-06 — shared Phase 41A quality rules
# --------------------------------------------------------------------------- #


def test_phase41a_quality_rules_exported_and_wired():
    rules = prompts.PHASE41A_QUALITY_RULES
    assert rules is tc.PHASE41A_QUALITY_RULES
    lowered = rules.casefold()
    assert "suspect_eligible" in lowered
    assert "witness" in lowered
    assert "who/why/weapon" in lowered
    assert "server-owned" in lowered
    for template in (
        prompts.CASE_PEOPLE_PROMPT_v1,
        prompts.EVIDENCE_PROMPT_v1,
        prompts.REPAIR_PROMPT_v1,
    ):
        assert rules in template
