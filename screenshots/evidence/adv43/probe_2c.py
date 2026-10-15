"""ADV-43 probe 2c/2d: display-name-as-id canonical acceptance + enum guard."""
from __future__ import annotations
import json, sys
sys.path.insert(0, r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\adv43")
from adv_common import attempt, section
from app.generation import pipeline, parser as parser_mod
from app.generation.provider import GenerationStage
from app.generation.schemas import (
    CrimeSpec, CrimeTimeSpec, GeneratedDraft, PersonSpec, LocationSpec, SceneSpec,
    MotiveSpec, ObjectSpec,
)

section("ATTACK 2c: display-name used as BOTH declaration id and reference")
crime = CrimeSpec(type="murder", victim_id="laura_stein", murderer_id="Daniel Roth",
                  motive_id="financial_gain", weapon_id="kitchen_knife",
                  location_id="apartment",
                  crime_time=CrimeTimeSpec(canonical="2026-09-11T20:15:00+02:00",
                                           accusation_tolerance_seconds=300))
draft = GeneratedDraft(
    crime=crime,
    persons=(
        PersonSpec(person_id="laura_stein", name="Laura Stein", role="victim",
                   affordances=("VISIBLE_CHARACTER",)),
        PersonSpec(person_id="Daniel Roth", name="Daniel Roth", role="suspect",
                   affordances=("SUSPECT_ELIGIBLE",)),
        PersonSpec(person_id="nina_weber", name="Nina Weber", role="witness",
                   affordances=("VISIBLE_CHARACTER",)),
    ),
    motives=(MotiveSpec(motive_id="financial_gain", label="Financial gain",
                        affordances=("MOTIVE_CANDIDATE",)),),
    objects=(ObjectSpec(object_id="kitchen_knife", asset_id="PROP_KNIFE_01",
                        affordances=("INSPECTABLE", "POTENTIAL_WEAPON")),),
    locations=(LocationSpec(location_id="apartment", name="Apartment"),),
    scene=SceneSpec(location_id="apartment", name="Apartment", environment_id="apartment"),
)
a = attempt()
print("crime_resolution_issues:", pipeline._crime_resolution_issues(draft))
print("locked violations:", a.locked.violations_against(draft))
print("=> display-name murdererId ACCEPTED by structural+locked checks?",
      pipeline._crime_resolution_issues(draft) == () and a.locked.violations_against(draft) == ())
a2 = attempt(); a2.draft = draft
rep = pipeline.validate_draft(a2)
print("validate outcome:", rep.outcome)
print("structural:", rep.structural_issues)
print("safety:", rep.safety_issues)
print("quality:", rep.quality_issues)
pub, ev, truth, _ = pipeline.assemble(a2)
print("assembled truth murderer_id:", truth.crime.murderer_id)
print("assembled public person ids:", [p.person_id for p in pub.persons])

section("ATTACK 2d: request-local enum forbids display name at case_truth?")
a3 = attempt()
sch = pipeline.build_request(a3, GenerationStage.CASE_TRUTH).json_schema
c = sch["properties"]["crime"]["properties"]
print("case_truth murdererId enum:", c["murdererId"].get("enum"))
print("'Daniel Roth' in enum:", "Daniel Roth" in (c["murdererId"].get("enum") or []))
print("'daniel_roth' in enum:", "daniel_roth" in (c["murdererId"].get("enum") or []))
