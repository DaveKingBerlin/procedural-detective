"""Phase 15 — the TEN deterministic showcase-matrix prompts and their expected
``WorldRequirements`` / published-world summaries.

Two prompts per kit (apartment / office / hotel_suite / warehouse / mansion).
Every prompt runs through the Phase 14 / 14_5 DEV pipeline (``extract`` ->
``composer`` -> published world graph) deterministically: each row publishes a
DISTINCT world (distinct environment + distinct object set, verified by the
matrix test), at least one row per kit is "most rows" procedural (a ``proc.*``
asset via the app-owned builtin declarative spec provider), and the FROZEN
golden truth is NEVER altered — every prompt normalizes to the golden locked
constraints and the solver proof is ``thomas_reed`` /
``cover_up_embezzlement`` / ``kitchen_knife`` with ``all_true``.

Variation surface (Phase15 "Vary: victim names, murderer, motive, weapon/object
types, time, witness, environment details"):

- the six structured lock fields keep their GOLDEN equivalence across rows but
  are spelled visibly differently (normalization-equivalent: "Sarah Miller" /
  "sarah_miller" / "SARAH MILLER"; "22:17" / "22:17:00" / "20:17Z" /
  "2026-09-11 22:17:00"; motive wording "€240,000 embezzlement" /
  "embezzlement" / "the €240,000" / "€240000" / "cover up" / "cover up the
  €240,000 embezzlement") — the ``variations`` map records each row's exact
  wording for the harness report;
- the NARRATIVE varies the environment detail, the object/weapon types that
  enter the world (catalog evidence classes AND the four builtin procedural
  showcases) and the placement relations.

The pinned ``proc.*`` asset ids below are content-addressed (compiler hash of
the app-owned spec); a spec change is a world change and SHOULD break these
pins loudly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.world.extract import extract_world_requirements

# --------------------------------------------------------------------------- #
# the ten prompts (deterministic; 2 per kit; golden-equivalent lock fields)
# --------------------------------------------------------------------------- #

# Canonical lock block (variant A): human spellings.
_LOCK_A = (
    "Victim: Sarah Miller\n"
    "Murderer: Thomas Reed\n"
    "Motive: \u20ac240,000 embezzlement\n"
    "Weapon: kitchen knife\n"
    "Time: 22:17\n"
    "Witness: Emily Reed\n"
)

# Canonical lock block (variant B): id-style spellings.
_LOCK_B = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 22:17:00\n"
    "Witness: emily_reed\n"
)


def _lock(
    victim: str,
    murderer: str,
    motive: str,
    weapon: str,
    time_: str,
    witness: str,
) -> str:
    return (
        f"Victim: {victim}\n"
        f"Murderer: {murderer}\n"
        f"Motive: {motive}\n"
        f"Weapon: {weapon}\n"
        f"Time: {time_}\n"
        f"Witness: {witness}\n"
    )


MATRIX_PROMPTS: dict[str, str] = {}

MATRIX_PROMPTS["apartment_a"] = _LOCK_A + (
    "A murder in the apartment. A claw hammer lies near the body, "
    "and a watch lies on the floor."
)
MATRIX_PROMPTS["apartment_b"] = _LOCK_B + (
    "A murder in a flat. A distinctive desk award sits on the desk, "
    "and a rope coils on the floor."
)
MATRIX_PROMPTS["office_a"] = _lock(
    "Sarah Miller", "Thomas Reed", "the \u20ac240,000", "kitchen knife",
    "20:17Z", "Emily Reed",
) + (
    "A financial crime in a company office. The killer used a letter opener "
    "at the workplace. A heavy award is on the desk."
)
MATRIX_PROMPTS["office_b"] = _lock(
    "SARAH MILLER", "THOMAS REED", "\u20ac240000", "Kitchen knife",
    "2026-09-11 22:17:00", "EMILY REED",
) + (
    "A crime in the office. An unusual laboratory sample rack is near the "
    "body, and a claw hammer rests on the desk."
)
MATRIX_PROMPTS["hotel_a"] = _LOCK_A + (
    "A murder in a hotel room. A broken glass bottle and medication are near "
    "the body, and a claw hammer is on the floor."
)
MATRIX_PROMPTS["hotel_b"] = _lock(
    "Sarah Miller", "Thomas Reed", "cover up", "kitchen knife",
    "22:17", "Emily Reed",
) + (
    "A murder in a hotel suite. An unusual laboratory sample rack was left "
    "near the body, and a rope and a watch are near the body."
)
MATRIX_PROMPTS["warehouse_a"] = _lock(
    "sarah_miller", "thomas_reed", "cover up the \u20ac240,000 embezzlement",
    "kitchen_knife", "20:17Z", "emily_reed",
) + (
    "A smuggling dispute in a storage depot. A wrench and a jewelry box "
    "were found on the floor."
)
MATRIX_PROMPTS["warehouse_b"] = _lock(
    "Sarah Miller", "Thomas Reed", "embezzlement", "kitchen knife",
    "22:17:00", "Emily Reed",
) + (
    "A theft at the warehouse depot. A distinctive desk award and a watch "
    "were found on the floor."
)
MATRIX_PROMPTS["mansion_a"] = _LOCK_A + (
    "An inheritance dispute in a mansion. A valuable antique ceremonial "
    "letter opener and a watch are in the study."
)
MATRIX_PROMPTS["mansion_b"] = _lock(
    "Sarah Miller", "Thomas Reed", "embezzlement", "kitchen knife",
    "22:17:00", "Emily Reed",
) + (
    "An inheritance dispute at the manor. An unusual laboratory sample rack "
    "sits on the desk, and a jewelry box and a rope are on the floor."
)

# Deterministic document order (row id ordering for the matrix report).
MATRIX_ORDER: tuple[str, ...] = (
    "apartment_a",
    "apartment_b",
    "office_a",
    "office_b",
    "hotel_a",
    "hotel_b",
    "warehouse_a",
    "warehouse_b",
    "mansion_a",
    "mansion_b",
)

# The pined content-addressed procedural asset ids (Phase 13 compiler).
PROC_DESK_AWARD = "proc.decor.4d402d9108486a56"
PROC_CUSTOM_TROPHY = "proc.decor.1d169cf74462fdf4"
PROC_SAMPLE_RACK = "proc.utility.41bd92f58c068c25"
PROC_ANTIQUE_LETTER_OPENER = "proc.decor.77ff70dc4f50b4f9"

# The golden base assets that must appear in EVERY row's world.
GOLDEN_BASE_ASSET_IDS: tuple[str, ...] = (
    "PROP_KITCHEN_KNIFE_01",
    "PROP_LAPTOP_01",
)


@dataclass(frozen=True)
class MatrixExpectation:
    """The deterministic expected summary of ONE matrix prompt."""

    row_id: str
    kit: str  # expected environmentId (the published scene environment)
    environment_hint: str | None
    # The EXACT extracted ObjectRequest requested-names of this prompt.
    expected_object_names: tuple[str, ...]
    # Resolved asset ids that MUST appear in the composed world graph
    # (golden base assets + the prompt-specific supporting objects).
    required_in_world: tuple[str, ...]
    # The prompt-specific object ids (world-graph placements beyond the kit
    # base set) the fixtures assert — the diversity-proof ids.
    prompt_object_ids: tuple[str, ...] = ()
    proc_assets_expected: bool = False
    relation_targets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Human-readable wording of the varied fields (harness report only).
    variations: dict[str, str] = field(default_factory=dict)


MATRIX_EXPECTED: dict[str, MatrixExpectation] = {
    "apartment_a": MatrixExpectation(
        row_id="apartment_a",
        kit="apartment",
        environment_hint="apartment",
        expected_object_names=("kitchen knife", "claw hammer", "wristwatch"),
        required_in_world=(*GOLDEN_BASE_ASSET_IDS, "PROP_HAMMER_01", "PROP_WATCH_01"),
        prompt_object_ids=("claw_hammer", "wristwatch"),
        relation_targets={
            "near_victim": ("claw hammer",),
            "floor_area": ("claw hammer", "wristwatch"),
        },
        variations={
            "victim": "Sarah Miller",
            "murderer": "Thomas Reed",
            "motive": "\u20ac240,000 embezzlement",
            "weapon": "kitchen knife",
            "time": "22:17",
            "witness": "Emily Reed",
            "detail": "apartment; claw hammer near the body; watch on the floor",
        },
    ),
    "apartment_b": MatrixExpectation(
        row_id="apartment_b",
        kit="apartment",
        environment_hint="apartment",
        expected_object_names=("kitchen knife", "Distinctive Desk Award", "rope"),
        required_in_world=(*GOLDEN_BASE_ASSET_IDS, "PROP_ROPE_01", PROC_DESK_AWARD),
        prompt_object_ids=("distinctive_desk_award", "rope"),
        proc_assets_expected=True,
        relation_targets={
            "on_desk": ("distinctive desk award",),
            "floor_area": ("distinctive desk award", "rope"),
        },
        variations={
            "victim": "sarah_miller",
            "murderer": "thomas_reed",
            "motive": "embezzlement",
            "weapon": "kitchen_knife",
            "time": "22:17:00",
            "witness": "emily_reed",
            "detail": "flat; desk award on the desk; rope on the floor",
        },
    ),
    "office_a": MatrixExpectation(
        row_id="office_a",
        kit="office",
        environment_hint="office",
        expected_object_names=("kitchen knife", "letter opener", "Custom Trophy"),
        required_in_world=(*GOLDEN_BASE_ASSET_IDS, "PROP_LETTER_OPENER_01", PROC_CUSTOM_TROPHY),
        prompt_object_ids=("custom_trophy",),
        proc_assets_expected=True,
        relation_targets={"on_desk": ("custom trophy",)},
        variations={
            "victim": "Sarah Miller",
            "murderer": "Thomas Reed",
            "motive": "the \u20ac240,000",
            "weapon": "kitchen knife",
            "time": "20:17Z",
            "witness": "Emily Reed",
            "detail": "company office; letter opener at the workplace; heavy award on the desk",
        },
    ),
    "office_b": MatrixExpectation(
        row_id="office_b",
        kit="office",
        environment_hint="office",
        expected_object_names=(
            "kitchen knife",
            "Unusual Laboratory Sample Rack",
            "claw hammer",
        ),
        required_in_world=(*GOLDEN_BASE_ASSET_IDS, "PROP_HAMMER_01", PROC_SAMPLE_RACK),
        prompt_object_ids=("unusual_laboratory_sample_rack", "claw_hammer"),
        proc_assets_expected=True,
        relation_targets={
            "near_victim": ("unusual laboratory sample rack",),
            "on_desk": ("claw hammer", "unusual laboratory sample rack"),
        },
        variations={
            "victim": "SARAH MILLER",
            "murderer": "THOMAS REED",
            "motive": "\u20ac240000",
            "weapon": "Kitchen knife",
            "time": "2026-09-11 22:17:00",
            "witness": "EMILY REED",
            "detail": "office; sample rack near the body; claw hammer on the desk",
        },
    ),
    "hotel_a": MatrixExpectation(
        row_id="hotel_a",
        kit="hotel_suite",
        environment_hint="hotel_suite",
        expected_object_names=(
            "kitchen knife",
            "glass bottle",
            "medication bottle",
            "claw hammer",
        ),
        required_in_world=(
            *GOLDEN_BASE_ASSET_IDS,
            "PROP_GLASS_BOTTLE_01",
            "PROP_MEDICATION_BOTTLE_01",
            "PROP_HAMMER_01",
        ),
        prompt_object_ids=("glass_bottle", "medication_bottle", "claw_hammer"),
        relation_targets={
            "near_victim": ("glass bottle", "medication bottle"),
            "floor_area": ("glass bottle", "medication bottle", "claw hammer"),
        },
        variations={
            "victim": "Sarah Miller",
            "murderer": "Thomas Reed",
            "motive": "\u20ac240,000 embezzlement",
            "weapon": "kitchen knife",
            "time": "22:17",
            "witness": "Emily Reed",
            "detail": "hotel room; broken glass bottle + medication near the body; hammer on the floor",
        },
    ),
    "hotel_b": MatrixExpectation(
        row_id="hotel_b",
        kit="hotel_suite",
        environment_hint="hotel_suite",
        expected_object_names=(
            "kitchen knife",
            "Unusual Laboratory Sample Rack",
            "rope",
            "wristwatch",
        ),
        required_in_world=(*GOLDEN_BASE_ASSET_IDS, "PROP_ROPE_01", "PROP_WATCH_01", PROC_SAMPLE_RACK),
        prompt_object_ids=("unusual_laboratory_sample_rack", "rope", "wristwatch"),
        proc_assets_expected=True,
        relation_targets={
            "near_victim": ("unusual laboratory sample rack", "rope", "wristwatch"),
        },
        variations={
            "victim": "Sarah Miller",
            "murderer": "Thomas Reed",
            "motive": "cover up",
            "weapon": "kitchen knife",
            "time": "22:17",
            "witness": "Emily Reed",
            "detail": "hotel suite; sample rack + rope + watch near the body",
        },
    ),
    "warehouse_a": MatrixExpectation(
        row_id="warehouse_a",
        kit="warehouse",
        environment_hint="warehouse",
        expected_object_names=("kitchen knife", "adjustable wrench", "jewelry box"),
        required_in_world=(*GOLDEN_BASE_ASSET_IDS, "PROP_WRENCH_01", "PROP_JEWELRY_BOX_01"),
        prompt_object_ids=("adjustable_wrench", "jewelry_box"),
        relation_targets={"floor_area": ("adjustable wrench", "jewelry box")},
        variations={
            "victim": "sarah_miller",
            "murderer": "thomas_reed",
            "motive": "cover up the \u20ac240,000 embezzlement",
            "weapon": "kitchen_knife",
            "time": "20:17Z",
            "witness": "emily_reed",
            "detail": "storage depot; wrench + jewelry box on the floor",
        },
    ),
    "warehouse_b": MatrixExpectation(
        row_id="warehouse_b",
        kit="warehouse",
        environment_hint="warehouse",
        expected_object_names=("kitchen knife", "Distinctive Desk Award", "wristwatch"),
        required_in_world=(*GOLDEN_BASE_ASSET_IDS, "PROP_WATCH_01", PROC_DESK_AWARD),
        prompt_object_ids=("distinctive_desk_award", "wristwatch"),
        proc_assets_expected=True,
        relation_targets={"floor_area": ("distinctive desk award", "wristwatch")},
        variations={
            "victim": "Sarah Miller",
            "murderer": "Thomas Reed",
            "motive": "embezzlement",
            "weapon": "kitchen knife",
            "time": "22:17:00",
            "witness": "Emily Reed",
            "detail": "warehouse depot; desk award + watch on the floor",
        },
    ),
    "mansion_a": MatrixExpectation(
        row_id="mansion_a",
        kit="mansion",
        environment_hint="mansion",
        expected_object_names=(
            "kitchen knife",
            "Antique Ceremonial Letter Opener",
            "wristwatch",
        ),
        required_in_world=(*GOLDEN_BASE_ASSET_IDS, "PROP_WATCH_01", PROC_ANTIQUE_LETTER_OPENER),
        prompt_object_ids=("antique_ceremonial_letter_opener", "wristwatch"),
        proc_assets_expected=True,
        relation_targets={},
        variations={
            "victim": "Sarah Miller",
            "murderer": "Thomas Reed",
            "motive": "\u20ac240,000 embezzlement",
            "weapon": "kitchen knife",
            "time": "22:17",
            "witness": "Emily Reed",
            "detail": "mansion study; antique ceremonial letter opener + watch",
        },
    ),
    "mansion_b": MatrixExpectation(
        row_id="mansion_b",
        kit="mansion",
        environment_hint="mansion",
        expected_object_names=(
            "kitchen knife",
            "Unusual Laboratory Sample Rack",
            "jewelry box",
            "rope",
        ),
        required_in_world=(*GOLDEN_BASE_ASSET_IDS, "PROP_JEWELRY_BOX_01", "PROP_ROPE_01", PROC_SAMPLE_RACK),
        prompt_object_ids=("unusual_laboratory_sample_rack", "jewelry_box", "rope"),
        proc_assets_expected=True,
        relation_targets={
            "on_desk": ("unusual laboratory sample rack",),
            "floor_area": ("unusual laboratory sample rack", "jewelry box", "rope"),
        },
        variations={
            "victim": "Sarah Miller",
            "murderer": "Thomas Reed",
            "motive": "embezzlement",
            "weapon": "kitchen knife",
            "time": "22:17:00",
            "witness": "Emily Reed",
            "detail": "manor; sample rack on the desk; jewelry box + rope on the floor",
        },
    ),
}


MATRIX_VERSION = 1

# The five documented kits the matrix spans (all delivered by the rows).
MATRIX_KITS: tuple[str, ...] = (
    "apartment",
    "office",
    "hotel_suite",
    "warehouse",
    "mansion",
)


def matrix_prompt(row_id: str) -> str:
    """The deterministic prompt of one matrix row."""
    if row_id not in MATRIX_PROMPTS:
        raise KeyError(f"unknown matrix row {row_id!r}")
    return MATRIX_PROMPTS[row_id]


def matrix_expectation(row_id: str) -> MatrixExpectation:
    """The deterministic expected summary of one matrix row."""
    if row_id not in MATRIX_EXPECTED:
        raise KeyError(f"unknown matrix row {row_id!r}")
    return MATRIX_EXPECTED[row_id]


def matrix_requirements(row_id: str):
    """The deterministic extracted WorldRequirements of one matrix row."""
    return extract_world_requirements(matrix_prompt(row_id))


__all__ = [
    "GOLDEN_BASE_ASSET_IDS",
    "MATRIX_EXPECTED",
    "MATRIX_KITS",
    "MATRIX_ORDER",
    "MATRIX_PROMPTS",
    "MATRIX_VERSION",
    "MatrixExpectation",
    "PROC_ANTIQUE_LETTER_OPENER",
    "PROC_CUSTOM_TROPHY",
    "PROC_DESK_AWARD",
    "PROC_SAMPLE_RACK",
    "matrix_expectation",
    "matrix_prompt",
    "matrix_requirements",
]