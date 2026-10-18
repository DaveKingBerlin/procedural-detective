"""ADV-48 lock-normalization evidence (adversary evidence; NOT product code).

Shows the exact mechanism by which the Phase-48 parenthetical Victim line and
the curated Location line break the trusted locked-constraint comparison.

Run from the repo root:
    python screenshots/evidence/adv48/adv48_lock_repro.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve()
REPO = HERE
while not (REPO / "backend").is_dir():
    REPO = REPO.parent
sys.path.insert(0, str(REPO / "backend"))

from app.generation.constraints import normalize_identity  # noqa: E402
from app.generation.prompt import parse_prompt  # noqa: E402
from app.generation.trusted_context import identity_slug  # noqa: E402
from app.world.environment import canonicalize_environment_hint  # noqa: E402


def make_draft():
    crime = SimpleNamespace(
        victim_id="sarah_miller",
        murderer_id="thomas_reed",
        weapon_id="kitchen_knife",
        motive_id="cover_up_embezzlement",
        location_id="miller_apartment_kitchen",
        crime_time=SimpleNamespace(canonical="2026-09-11T22:17:00+02:00"),
    )
    persons = [
        SimpleNamespace(person_id="sarah_miller", name="Sarah Miller", role="victim"),
        SimpleNamespace(person_id="thomas_reed", name="Thomas Reed", role="murderer"),
        SimpleNamespace(person_id="emily_reed", name="Emily Reed", role="witness"),
    ]
    motives = [SimpleNamespace(motive_id="cover_up_embezzlement", label="Cover up embezzlement")]
    scene = SimpleNamespace(
        location_id="miller_apartment_kitchen",
        name="Apartment Kitchen",
        environment_id="apartment",
    )
    return SimpleNamespace(crime=crime, persons=persons, motives=motives, scene=scene)


def main() -> int:
    out: dict = {}

    parenthetical = "Sarah Miller (Female, 42, accountant)"
    plain = "Sarah Miller"
    locked_paren, _ = parse_prompt(f"Victim: {parenthetical}\nMurderer: Thomas Reed\n")
    locked_plain, _ = parse_prompt(f"Victim: {plain}\nMurderer: Thomas Reed\n")

    draft = make_draft()

    out["raw_locked_value_with_parens"] = locked_paren.victim
    out["normalize_identity(raw_paren)"] = normalize_identity(locked_paren.victim or "")
    out["identity_slug(raw_paren)"] = identity_slug(locked_paren.victim)
    out["normalize_identity(plain)"] = normalize_identity(plain)
    out["normalize_identity(draft victim_id)"] = normalize_identity("sarah_miller")

    out["violations_with_parenthetical"] = locked_paren.violations_against(draft)
    out["violations_with_plain_name"] = locked_plain.violations_against(draft)

    # If the provider complies with the authoritative sheet, the victim id must
    # be the parenthetical slug for the lock to pass.
    slug = identity_slug(locked_paren.victim)
    draft.crime.victim_id = slug
    draft.persons[0].person_id = slug
    out["slug_victim_id"] = slug
    out["violations_when_provider_uses_sheet_slug"] = locked_paren.violations_against(draft)

    # Location lock vs the fake scene.
    locked_loc, _ = parse_prompt("Victim: sarah_miller\nLocation: apartment\n")
    out["violations_location_apartment_vs_miller_kitchen"] = locked_loc.violations_against(
        make_draft()
    )

    # Curated locations vs the closed environment vocabulary.
    locs = [
        "apartment",
        "private office",
        "hotel suite",
        "warehouse",
        "office conference room",
        "apartment kitchen",
        "hotel corridor",
        "apartment living room",
        "office reception",
        "mansion study",
        "warehouse loading bay",
        "office archive room",
        "mansion library",
        "hotel restaurant kitchen",
        "mansion gallery",
        "warehouse office",
    ]
    out["curated_location_environment_resolution"] = {
        loc: canonicalize_environment_hint(loc)[0] for loc in locs
    }
    out["resolvable_location_count"] = sum(
        1 for v in out["curated_location_environment_resolution"].values() if v is not None
    )

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
