"""PHASE 48 QA RETEST (after FIX-READY) — independent backend probe.

Independently probes the ADV-48-01 backend fix (``parse_prompt`` strips ONE
trailing Phase-47 victim-profile parenthetical from the VICTIM lock only) and
the ADV-48-02 frontend location alignment (each of the 16 curated names must
resolve UNAMBIGUOUSLY to exactly one supported environment kit).

This file is QA-owned evidence tooling; it never imports the developer's test
files. It builds its OWN minimal ``GeneratedDraft`` fixtures.

Run from the ``backend`` directory:
    python ../e2e/probes/qa_p48_retest_backend.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# backend/ is the CWD when run per the docstring; make imports explicit anyway.
_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.environments.resolver import resolve_environment  # noqa: E402
from app.generation.constraints import LockedConstraints  # noqa: E402
from app.generation.prompt import parse_prompt  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    CrimeSpec,
    CrimeTimeSpec,
    GeneratedDraft,
    PersonSpec,
    SceneSpec,
)
from app.generation.trusted_context import (  # noqa: E402
    identity_slug,
    locked_identities,
    render_locked_identity_sheet,
)
from app.services.generation import _resolve_environment_for_generation  # noqa: E402
from app.world.environment import canonicalize_environment_hint  # noqa: E402
from app.world.extract import extract_world_requirements  # noqa: E402

CANONICAL = "2026-09-11T22:17:00+02:00"

# The 16 curated LOCATIONS exactly as frontend/src/journey/randomCrimeData.ts
# declares them (Phase 48 F1). Kept as a literal so this probe is independent of
# the TS source; the QA retest cross-checks the count/names separately.
LOCATIONS_16 = (
    "apartment",
    "apartment kitchen",
    "apartment lounge",
    "apartment bedroom",
    "private office",
    "hotel suite",
    "warehouse",
    "office boardroom",
    "office archive",
    "office reception",
    "hotel corridor",
    "hotel restaurant kitchen",
    "warehouse loading bay",
    "hotel ballroom",
    "mansion study",
    "mansion library",
)

results: dict = {"backend_parse": {}, "locations": {}, "failures": []}


def _draft(victim_id: str, victim_name: str) -> GeneratedDraft:
    """Minimal HOMICIDE draft with a single victim person (no test fixtures)."""
    return GeneratedDraft(
        crime=CrimeSpec(
            type="HOMICIDE",
            victim_id=victim_id,
            murderer_id="m1",
            motive_id="motive_x",
            weapon_id="weapon_x",
            location_id="loc_x",
            crime_time=CrimeTimeSpec(
                canonical=CANONICAL, accusation_tolerance_seconds=120
            ),
        ),
        persons=(PersonSpec(person_id=victim_id, name=victim_name, role="victim"),),
    )


def _check(name: str, condition: bool, detail: object) -> None:
    results["backend_parse"][name] = {"pass": bool(condition), "detail": detail}
    if not condition:
        results["failures"].append(name)


# --------------------------------------------------------------------------- #
# 1. the headline parse case
# --------------------------------------------------------------------------- #
locked, note = parse_prompt("Victim: Charlotte Hayes (Female, 42, Art curator)")
_check("victim_equals_name", locked.victim == "Charlotte Hayes", locked.victim)
_check("locked_is_name_only", locked == LockedConstraints(victim="Charlotte Hayes"), repr(locked))
_check("identity_slug_is_name_slug", identity_slug(locked.victim) == "charlotte_hayes", identity_slug(locked.victim))
_check("note_lists_victim", "victim" in note, note)

# canonical id (the ONE authoritative sheet token) is the NAME slug
ids = locked_identities(locked)
_check(
    "locked_identities_name_slug",
    len(ids) == 1 and ids[0].canonical_id == "charlotte_hayes" and ids[0].display_name == "Charlotte Hayes",
    [(i.field, i.canonical_id, i.display_name) for i in ids],
)
sheet = render_locked_identity_sheet(locked)
_check(
    "sheet_carries_name_slug_not_polluted",
    "canonicalId: charlotte_hayes" in sheet and "charlotte_hayes_female_42_art_curator" not in sheet,
    sheet.replace("\n", " | "),
)

# --------------------------------------------------------------------------- #
# 2. a name-only draft PASSES the stripped lock; the parenthetical lock FAILS
# --------------------------------------------------------------------------- #
draft_name_id = _draft("charlotte_hayes", "Charlotte Hayes")
draft_person_name_only = _draft("v1", "Charlotte Hayes")

stripped_violations_id = locked.violations_against(draft_name_id)
stripped_violations_name = locked.violations_against(draft_person_name_only)
polluted = LockedConstraints(victim="Charlotte Hayes (Female, 42, Art curator)")
polluted_violations = polluted.violations_against(draft_name_id)

_check("name_id_draft_passes", stripped_violations_id == (), stripped_violations_id)
_check("person_name_draft_passes", stripped_violations_name == (), stripped_violations_name)
_check(
    "parenthetical_lock_fails",
    any("victim" in issue for issue in polluted_violations),
    polluted_violations,
)

# --------------------------------------------------------------------------- #
# 3. non-victim fields stay VERBATIM
# --------------------------------------------------------------------------- #
prompt_mixed = (
    "Victim: Charlotte Hayes (Female, 42, Art curator)\n"
    "Murderer: John (the butler)\n"
    "Witness: Jane (female, 39, Chef)\n"
    "Weapon: Knife (large)\n"
    "Motive: money (lots)\n"
    "Location: apartment (2nd floor)"
)
mixed, mixed_note = parse_prompt(prompt_mixed)
expected_mixed = {
    "victim": "Charlotte Hayes",
    "murderer": "John (the butler)",
    "witness": "Jane (female, 39, Chef)",
    "weapon": "Knife (large)",
    "motive": "money (lots)",
    "location": "apartment (2nd floor)",
}
actual_mixed = {
    "victim": mixed.victim,
    "murderer": mixed.murderer,
    "witness": mixed.witness,
    "weapon": mixed.weapon,
    "motive": mixed.motive,
    "location": mixed.location,
}
_check("non_victim_verbatim", actual_mixed == expected_mixed, actual_mixed)
_check(
    "note_lists_all_keys",
    mixed_note
    == "recognized structured keys: victim, murderer, witness, weapon, motive, location",
    mixed_note,
)

# raw prompt string is unchanged for the caller (parse_prompt returns only
# (constraints, note); it never mutates or returns the raw string)
raw_before = str(prompt_mixed)
parse_prompt(prompt_mixed)
_check("raw_prompt_unchanged", prompt_mixed == raw_before, prompt_mixed.splitlines()[0])

# a profile-shaped parenthetical on a NON-victim line must NOT be stripped
non_victim, _ = parse_prompt("Murderer: John (Male, 40, Chef)\nWitness: Ann (Female, 30, Nurse)")
_check(
    "non_victim_profile_shape_verbatim",
    non_victim.murderer == "John (Male, 40, Chef)" and non_victim.witness == "Ann (Female, 30, Nurse)",
    {"murderer": non_victim.murderer, "witness": non_victim.witness},
)

# --------------------------------------------------------------------------- #
# 4. edge cases (task-mandated + adversarial extras)
# --------------------------------------------------------------------------- #
edge_expected_verbatim = (
    "John (the butler)",
    "Sarah Miller",
    "(Female, 42, x)",
    "Jane (female, 39)",
    "Jane (female, 999, Chef)",
    "Jane (female, 0, Chef)",
    "Jane (robot, 39, Chef)",
    "Charlotte Hayes (Female, 42, )",
    "Jane (female, 42, Chef",  # unclosed
    "Jane female, 42, Chef)",  # no opening paren
    "Jane (female, 42, Chef) trailing",
    "Jane (Female, 42, Chef",  # unclosed with valid head
)
edge_stripped = {
    "(unknown, 30, Chef)": "Charlotte Hayes",
    "(other, 20, Artist)": "Charlotte Hayes",
    "(male, 42, Art curator)": "Charlotte Hayes",
    "(MALE, 42, Art curator)": "Charlotte Hayes",
    "(female, 1, Chef)": "Charlotte Hayes",
    "(female, 120, Chef)": "Charlotte Hayes",
    "(female, 042, Chef)": "Charlotte Hayes",
}

edge_report = {}
for value in edge_expected_verbatim:
    got, _ = parse_prompt(f"Victim: {value}")
    edge_report[value] = {"expected": "VERBATIM", "actual": got.victim}
    if got.victim != value:
        results["failures"].append(f"edge_verbatim:{value}")
for suffix, name in edge_stripped.items():
    got, _ = parse_prompt(f"Victim: {name} {suffix}")
    edge_report[f"{name} {suffix}"] = {"expected": name, "actual": got.victim}
    if got.victim != name:
        results["failures"].append(f"edge_strip:{suffix}")

# whitespace padding is tolerated
padded, _ = parse_prompt("Victim:   Charlotte Hayes   (Female, 42, Art curator)   ")
if padded.victim != "Charlotte Hayes":
    results["failures"].append("edge_whitespace_padding")
edge_report["padded whitespace"] = {"expected": "Charlotte Hayes", "actual": padded.victim}

# honorific is NOT dropped by parse_prompt (only by identity_slug)
honorific, _ = parse_prompt("Victim: Dr. Anna Weiss (Female, 42, Doctor)")
if honorific.victim != "Dr. Anna Weiss":
    results["failures"].append("edge_honorific")
edge_report["honorific"] = {"expected": "Dr. Anna Weiss", "actual": honorific.victim}
if identity_slug(honorific.victim) != "anna_weiss":
    results["failures"].append("edge_honorific_slug")

results["backend_parse"]["edge_cases"] = edge_report

# --------------------------------------------------------------------------- #
# 5. the 16 curated locations resolve UNAMBIGUOUSLY to one kit
# --------------------------------------------------------------------------- #
loc_rows = []
for name in LOCATIONS_16:
    res = resolve_environment(name)
    loc_rows.append(
        {
            "name": name,
            "environment_id": res.environment_id,
            "provenance": res.provenance.value,
            "ambiguous": res.ambiguous,
            "resolved": res.resolved,
            "candidates": list(res.candidates),
        }
    )
    if res.ambiguous or not res.resolved or not res.environment_id:
        results["failures"].append(f"location_ambiguous:{name}")
results["locations"] = {"count": len(LOCATIONS_16), "rows": loc_rows}
results["locations"]["all_unique_resolved"] = len({r["environment_id"] for r in loc_rows}) >= 1 and all(
    r["resolved"] and not r["ambiguous"] and r["environment_id"] for r in loc_rows
)

# --------------------------------------------------------------------------- #
# 6. the ACTUAL generation environment binding path (not the raw resolver):
#    extract_world_requirements() family keyword -> _resolve_environment_for_generation()
#    and the strict canonicalize_environment_hint() for transparency.
# --------------------------------------------------------------------------- #
gen_rows = []
for name in LOCATIONS_16:
    world_reqs = extract_world_requirements(f"Victim: X\nLocation: {name}\n")
    hint = world_reqs.environment_hint
    kit, diag = _resolve_environment_for_generation(hint)
    strict_kit, strict_issues = canonicalize_environment_hint(name)
    gen_rows.append(
        {
            "name": name,
            "family_hint": hint,
            "generation_kit": kit,
            "generation_provenance": diag.get("provenance"),
            "strict_canonicalize_kit": strict_kit,
            "strict_issues": list(strict_issues),
        }
    )
    # The generation binding must land on a supported kit (never fallback for a
    # curated name).
    if kit not in ("apartment", "office", "hotel_suite", "warehouse", "mansion"):
        results["failures"].append(f"generation_kit_unresolved:{name}")
results["generation_binding"] = {
    "rows": gen_rows,
    "all_resolved_to_supported_kit": all(
        r["generation_kit"] in ("apartment", "office", "hotel_suite", "warehouse", "mansion")
        for r in gen_rows
    ),
    "strict_canonicalize_resolvable_count": sum(
        1 for r in gen_rows if r["strict_canonicalize_kit"] is not None
    ),
}

# --------------------------------------------------------------------------- #
# 7. the LOCATION lock: a realistic draft whose scene carries the resolved kit
#    and whose crime.location_id is the locked location's canonical slug.
# --------------------------------------------------------------------------- #
lock_rows = []
for name in LOCATIONS_16:
    kit = _resolve_environment_for_generation(
        extract_world_requirements(f"Location: {name}").environment_hint
    )[0]
    slug = identity_slug(name)
    draft = GeneratedDraft(
        crime=CrimeSpec(
            type="HOMICIDE",
            victim_id="v1",
            murderer_id="m1",
            motive_id="motive_x",
            weapon_id="weapon_x",
            location_id=slug,
            crime_time=CrimeTimeSpec(canonical=CANONICAL, accusation_tolerance_seconds=120),
        ),
        persons=(PersonSpec(person_id="v1", name="X", role="victim"),),
        # A realistic provider scene that follows the authoritative id sheet:
        # location_id = the locked location's canonical slug; the display name is
        # the resolved kit's name (NOT the compound location string), so the lock
        # can ONLY pass through the canonical slug.
        scene=SceneSpec(location_id=slug, name=kit, environment_id=kit),
    )
    locked_loc = LockedConstraints(location=name)
    violations = locked_loc.violations_against(draft)
    lock_rows.append(
        {"name": name, "slug": slug, "kit": kit, "violations": list(violations)}
    )
    if violations:
        results["failures"].append(f"location_lock_fails:{name}")
results["location_lock"] = {
    "rows": lock_rows,
    "all_pass_with_canonical_slug": all(not r["violations"] for r in lock_rows),
}

results["result"] = "PASS" if not results["failures"] else "FAIL"

print(json.dumps(results, indent=2, ensure_ascii=False))
raise SystemExit(0 if results["result"] == "PASS" else 1)
