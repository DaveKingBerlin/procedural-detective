"""Phase 14 — the five deterministic showcase prompts and their expected
``WorldRequirements`` / composition summaries.

One prompt per environment kit (apartment / office / hotel_suite / warehouse /
mansion). Every prompt is DETERMINISTIC and maps through
``app.world.extract`` + ``app.world.composer`` to a valid, playable world that
publishes with the golden CaseTruth + the golden evidence set (solver all_true).

Expected summaries (``SHOWCASE_EXPECTED``) pin:
- ``environment_hint`` — the prompt-derived hint (None when the prompt names
  no location word, e.g. the apartment showcase falls back to the default kit);
- ``environment_id`` — the resolved kit;
- ``expected_object_names`` — the ObjectRequests the extractor MUST produce;
- ``required_in_world`` — RESOLVED asset ids that MUST appear in the composed
  world graph (prompt-specific supporting objects + the critical evidence);
- ``proc_assets_expected`` — True when a procedural (proc.*) asset is required;
- ``relation_targets`` — relation kinds -> bound object requested names;
- ``bootstrap_prompt_specific`` — the object ids the FIXTURE assertions use to
  prove prompt-specific objects appear in the bootstrap (the catalog/（proc.)
  ids are project-pinned by the Phase 13 fixtures).

NOTE (documented deviation): the mansion prompt's "in the study" is NOT part
of the bounded relation phrase vocabulary (on_desk / on_table / near_victim /
inside_cabinet / floor_area / on_wall) — NO relation is fabricated for it; the
watch is placed deterministically on a kit table anchor by the composer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.world.extract import extract_world_requirements

# --------------------------------------------------------------------------- #
# the five prompts (deterministic, one per kit)
# --------------------------------------------------------------------------- #

APARTMENT_PROMPT = (
    "Victim: Sarah Miller\n"
    "Murderer: Thomas Reed\n"
    "Motive: \u20ac240,000 embezzlement\n"
    "Weapon: Kitchen knife\n"
    "Time: 22:17\n"
    "Witness: Emily Reed"
)

OFFICE_PROMPT = (
    "A financial crime in a company office. The killer used a letter opener "
    "at the workplace. A heavy award is on the desk."
)

HOTEL_PROMPT = (
    "A murder in a hotel room. A broken glass bottle and medication are near "
    "the body."
)

WAREHOUSE_PROMPT = (
    "A smuggling dispute in a storage depot. A wrench and a rope were used."
)

MANSION_PROMPT = (
    "An inheritance dispute in a mansion. A valuable antique ceremonial "
    "letter opener and a watch are in the study."
)

# The DEFAULT GOLDEN PROMPT (byte-identical apartment when no explicit
# environment / object tokens are present).
GOLDEN_DEFAULT_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

SHOWCASE_PROMPTS: dict[str, str] = {
    "apartment": APARTMENT_PROMPT,
    "office": OFFICE_PROMPT,
    "hotel_suite": HOTEL_PROMPT,
    "warehouse": WAREHOUSE_PROMPT,
    "mansion": MANSION_PROMPT,
}


@dataclass(frozen=True)
class ShowcaseExpectation:
    """The deterministic expected summary of one showcase prompt."""

    environment_hint: str | None
    environment_id: str
    expected_object_names: tuple[str, ...]
    required_in_world: tuple[str, ...]
    proc_assets_expected: bool = False
    relation_targets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Object ids whose presence in the investigation bootstrap the fixtures
    # assert (prompt-specific supporting objects AND critical evidence).
    bootstrap_assert_ids: tuple[str, ...] = ()


SHOWCASE_EXPECTED: dict[str, ShowcaseExpectation] = {
    "apartment": ShowcaseExpectation(
        environment_hint=None,  # no location word -> default (apartment) fallback
        environment_id="apartment",
        expected_object_names=("kitchen knife",),
        # the golden base set stays exactly as the provider produced it
        required_in_world=(
            "PROP_KITCHEN_KNIFE_01",
            "PROP_LAPTOP_01",
            "PROP_LETTER_OPENER_01",
            "PROP_SCISSORS_01",
        ),
        bootstrap_assert_ids=("kitchen_knife", "apartment_laptop"),
    ),
    "office": ShowcaseExpectation(
        environment_hint="office",
        environment_id="office",
        expected_object_names=("letter opener", "Custom Trophy"),
        required_in_world=(
            "PROP_LETTER_OPENER_01",
            "PROP_KITCHEN_KNIFE_01",
            "PROP_LAPTOP_01",
        ),
        proc_assets_expected=True,  # the custom trophy
        relation_targets={"on_desk": ("custom trophy",)},
        bootstrap_assert_ids=("custom_trophy", "kitchen_knife", "apartment_laptop"),
    ),
    "hotel_suite": ShowcaseExpectation(
        environment_hint="hotel_suite",
        environment_id="hotel_suite",
        expected_object_names=("glass bottle", "medication bottle"),
        required_in_world=(
            "PROP_GLASS_BOTTLE_01",
            "PROP_MEDICATION_BOTTLE_01",
            "PROP_KITCHEN_KNIFE_01",
            "PROP_LAPTOP_01",
        ),
        relation_targets={
            "near_victim": ("glass bottle", "medication bottle"),
        },
        bootstrap_assert_ids=("glass_bottle", "medication_bottle", "kitchen_knife"),
    ),
    "warehouse": ShowcaseExpectation(
        environment_hint="warehouse",
        environment_id="warehouse",
        expected_object_names=("adjustable wrench", "rope"),
        required_in_world=(
            "PROP_WRENCH_01",
            "PROP_ROPE_01",
            "PROP_KITCHEN_KNIFE_01",
            "PROP_LAPTOP_01",
        ),
        bootstrap_assert_ids=("adjustable_wrench", "rope", "kitchen_knife"),
    ),
    "mansion": ShowcaseExpectation(
        environment_hint="mansion",
        environment_id="mansion",
        expected_object_names=("Antique Ceremonial Letter Opener", "wristwatch"),
        required_in_world=(
            "PROP_WATCH_01",
            "PROP_KITCHEN_KNIFE_01",
            "PROP_LAPTOP_01",
        ),
        proc_assets_expected=True,  # the antique ceremonial letter opener
        # NOTE: the prompt's "in the study" is NOT a bounded relation phrase;
        # no relation is fabricated (documented deviation).
        relation_targets={},
        bootstrap_assert_ids=("wristwatch", "kitchen_knife"),
    ),
}


def showcase_requirements(prompt: str):
    """The deterministic extracted WorldRequirements of one showcase prompt."""
    return extract_world_requirements(prompt)


__all__ = [
    "APARTMENT_PROMPT",
    "GOLDEN_DEFAULT_PROMPT",
    "HOTEL_PROMPT",
    "MANSION_PROMPT",
    "OFFICE_PROMPT",
    "SHOWCASE_EXPECTED",
    "SHOWCASE_PROMPTS",
    "ShowcaseExpectation",
    "WAREHOUSE_PROMPT",
    "showcase_requirements",
]