"""Shared harness for Phase43 adversarial probes (hermetic, zero network)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(r"C:\Users\Fujitsu\coding\procedural-detective\backend")
for p in (str(BACKEND), str(BACKEND / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.generation import parser as parser_mod
from app.generation import pipeline, prompts
from app.generation import trusted_context as tc
from app.generation.constraints import LockedConstraints
from app.generation.provider import GenerationStage
from fixtures.golden_generation import (
    GOLDEN_FULL_DRAFT,
    GOLDEN_STAGE_PAYLOADS,
)

LOCKED_PROMPT = (
    "Victim: Laura Stein\n"
    "Murderer: Daniel Roth\n"
    "Motive: financial gain\n"
    "Weapon: kitchen knife\n"
    "Time: 20:15\n"
    "Witness: Nina Weber\n"
    "Location: apartment\n"
)

CANONICAL_IDS = (
    "laura_stein",
    "daniel_roth",
    "financial_gain",
    "kitchen_knife",
    "apartment",
    "nina_weber",
)


def attempt(prompt: str = LOCKED_PROMPT):
    locked, note = pipeline.normalize_prompt(prompt, max_chars=4000)
    return pipeline.AttemptRecord(
        attempt_id="GA-adv43",
        case_id="CASE-adv43",
        session_id="QUOTA-adv43",
        prompt=prompt,
        prompt_note=note,
        locked=locked,
    )


def crime_doc(*, victim="laura_stein", murderer="daniel_roth", motive="financial_gain",
              weapon="kitchen_knife", location="apartment", resolution="HOMICIDE",
              canonical="2026-09-11T20:15:00+02:00"):
    return {
        "crime": {
            "type": "murder",
            "victimId": victim,
            "murdererId": murderer,
            "motiveId": motive,
            "weaponId": weapon,
            "locationId": location,
            "crimeTime": {"canonical": canonical, "accusationToleranceSeconds": 300},
            "resolutionType": resolution,
        }
    }


def parse_stage(stage, doc):
    return parser_mod.parse_stage(stage, doc, non_throwing=False)


def attempt_with_prior():
    a = attempt()
    a.stage_outputs[GenerationStage.CASE_TRUTH] = parse_stage(
        GenerationStage.CASE_TRUTH, json.dumps(crime_doc())
    )
    a.stage_outputs[GenerationStage.PUBLIC_WORLD] = parse_stage(
        GenerationStage.PUBLIC_WORLD, GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD]
    )
    return a


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)
