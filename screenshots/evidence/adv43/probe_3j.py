"""ADV-43 probe 3j: over-narrow wire evidence + universe fed before validation."""
from __future__ import annotations
import json, sys
sys.path.insert(0, r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\adv43")
from adv_common import attempt, crime_doc, parse_stage, section
from app.generation import parser as parser_mod, pipeline
from app.generation.provider import GenerationStage

section("ATTACK 3j: victim-only lock -> rendered WIRE schema murdererId enum (FULL LOCK as control)")
for label, prompt in (
    ("FULL lock", "Victim: Laura Stein\nMurderer: Daniel Roth\nMotive: greed\nWeapon: kitchen knife\nWitness: Nina Weber\nLocation: apartment\n"),
    ("victim-only", "Victim: Laura Stein\nMotive: greed\nWeapon: kitchen knife\n"),
    ("murderer-only (no victim)", "Murderer: Daniel Roth\nMotive: greed\nWeapon: kitchen knife\n"),
):
    a = attempt(prompt)
    req = pipeline.build_request(a, GenerationStage.CASE_TRUTH)
    c = req.json_schema["properties"]["crime"]["properties"]
    print(f"{label:26s} victimId={c['victimId'].get('enum')} murdererId={c['murdererId'].get('enum')}")

section("ATTACK 3k: provider output feeds the enum universe before full validation")
a = attempt()
# forged case_truth with an invented murderer id (not locked-validated yet)
forged = json.dumps(crime_doc(murderer="invented_person_qqq"))
pipeline.apply_stage_output(a, GenerationStage.CASE_TRUTH, forged)
print("stored murderer:", a.stage_outputs[GenerationStage.CASE_TRUTH].murderer_id)
# now render the evidence request universe
req = pipeline.build_request(a, GenerationStage.EVIDENCE)
props = req.json_schema["properties"]["evidence"]["items"]["properties"]["propositions"]["items"]["properties"]
print("evidence personId enum:", props["personId"].get("enum"))
print("invented_person_qqq present in enum:", "invented_person_qqq" in (props["personId"].get("enum") or []))
# note: full validation has NOT run yet, and the forged murderer would fail locked constraints
rep = pipeline.validate_draft(a)
print("validation structural:", rep.structural_issues)
print("validation locked:", rep.locked_violations)
