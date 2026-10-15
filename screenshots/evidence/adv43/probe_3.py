"""ADV-43 probe 3: reference-universe narrowing correctness."""
from __future__ import annotations
import json, sys
sys.path.insert(0, r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\adv43")
from adv_common import attempt, parse_stage, section, crime_doc
from app.generation import parser as parser_mod, pipeline, prompts
from app.generation.provider import GenerationStage
from app.generation import trusted_context as tc

section("ATTACK 3a: victim-only lock -> murdererId enum?")
p = "Victim: Laura Stein\nMotive: greed\nWeapon: kitchen knife\n"
a = attempt(p)
print("locked fields:", a.locked.locked_fields())
sch = pipeline.build_request(a, GenerationStage.CASE_TRUTH).json_schema
crime = sch["properties"]["crime"]["properties"]
for f in ("victimId", "murdererId", "motiveId", "weaponId", "locationId"):
    if f in crime:
        print(f"  {f}: enum={crime[f].get('enum')} type={crime[f].get('type')}")
import app.generation.prompts as pr
print("full build_request schema murdererId:", crime.get("murdererId"))

section("ATTACK 3b: murderer-only lock (no victim) -> victimId enum?")
p2 = "Murderer: Daniel Roth\nMotive: greed\nWeapon: kitchen knife\n"
a2 = attempt(p2)
print("locked fields:", a2.locked.locked_fields())
sch2 = pipeline.build_request(a2, GenerationStage.CASE_TRUTH).json_schema
c2 = sch2["properties"]["crime"]["properties"]
for f in ("victimId", "murdererId"):
    print(f"  {f}: enum={c2[f].get('enum')}")

section("ATTACK 3c: no locks at all -> wide schema?")
a3 = attempt("Just a plain sentence with no structured keys at all")
print("locked fields:", a3.locked.locked_fields())
sch3 = pipeline.build_request(a3, GenerationStage.CASE_TRUTH).json_schema
c3 = sch3["properties"]["crime"]["properties"]
for f in ("victimId", "murdererId"):
    print(f"  {f}: enum={c3[f].get('enum')}")

section("ATTACK 3d: can a NEW (unlocked) murderer be expressed at case_truth?")
# locked victim+motive+weapon but no murderer -> strict provider must emit a person
# id for the murderer; is any fresh id admissible?
enums = c3.get("murdererId", {}).get("enum")
print("victim-only murdererId enum =", crime["murdererId"].get("enum"))
print("=> a provider constrained by this enum can only emit the victim id or null (both invalid for HOMICIDE)")

section("ATTACK 3e: _reference_id_tuple with duplicates/empty/nulls/non-list")
from app.generation.prompts import _reference_id_tuple
print("dupes:", _reference_id_tuple({"personIds": ["a", "b", "a", ""]}, "personIds"))
print("nulls:", _reference_id_tuple({"personIds": ["a", None, "b"]}, "personIds"))
print("string-not-list:", _reference_id_tuple({"personIds": "a"}, "personIds"))
print("absent:", _reference_id_tuple({}, "personIds"))
print("empty list:", _reference_id_tuple({"personIds": []}, "personIds"))
print("set:", _reference_id_tuple({"personIds": {"a", "b"}}, "personIds"))
print("non-str items:", _reference_id_tuple({"personIds": [1, 2, "a"]}, "personIds"))

section("ATTACK 3f: narrow_stage_output_schema mutation safety deep copy?")
base = prompts.json_schema_for_stage_output("case_truth")
before = json.dumps(base, sort_keys=True)
_ = prompts.narrow_stage_output_schema("case_truth", reference_universe={"personIds": ["zzz"]})
after = json.dumps(prompts.json_schema_for_stage_output("case_truth"), sort_keys=True)
print("source schema unmutated:", before == after)
n = prompts.narrow_stage_output_schema("case_truth", reference_universe={"personIds": ["zzz"]})
print("narrowed victimId enum:", n["properties"]["crime"]["properties"]["victimId"].get("enum"))
print("narrowed murdererId enum:", n["properties"]["crime"]["properties"]["murdererId"].get("enum"))

section("ATTACK 3g: unknown stage -> None? partial universe -> partial enums")
print("unknown stage:", prompts.narrow_stage_output_schema("bogus_stage", reference_universe={"personIds": ["a"]}))
print("empty universe mapping:", prompts.narrow_stage_output_schema("case_truth", reference_universe={}))
print("non-mapping:", prompts.narrow_stage_output_schema("case_truth", reference_universe=None))

section("ATTACK 3h: REPAIR schema reference narrowing completeness")
a4 = attempt()
import dataclasses
from fixtures.golden_generation import GOLDEN_FULL_DRAFT
a4.draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
req = pipeline.build_request(a4, GenerationStage.REPAIR)
sch = req.json_schema
crime = sch["properties"]["crime"]["properties"]
print("repair crime.murdererId enum set:", crime["murdererId"].get("enum"))
wg = sch["properties"]["worldGraph"]["properties"]
place = wg["placements"]["items"]["properties"]
print("repair worldGraph placements objectId enum:", place["objectId"].get("enum"))
print("repair worldGraph placements locationId enum:", place["locationId"].get("enum"))
print("repair worldGraph placements assetId enum:", place["assetId"].get("enum"))
print("repair worldGraph placements anchor enum:", place["anchor"].get("enum"))
print("repair worldGraph locations locationId enum:", wg["locations"]["items"]["properties"]["locationId"].get("enum"))
ev = sch["properties"]["evidence"]["items"]["properties"]["propositions"]["items"]["properties"]
print("repair evidence props personId enum:", ev["personId"].get("enum"))
print("repair evidence props objectId enum:", ev["objectId"].get("enum"))

section("ATTACK 3i: declaration ids never enum-locked (red herrings allowed)?")
a5 = attempt()
a5.stage_outputs[GenerationStage.CASE_TRUTH] = parse_stage(GenerationStage.CASE_TRUTH, json.dumps(crime_doc()))
req5 = pipeline.build_request(a5, GenerationStage.PUBLIC_WORLD)
pw = req5.json_schema["properties"]
print("public_world persons[].personId enum:", pw["persons"]["items"]["properties"]["personId"].get("enum"))
print("public_world locations[].locationId enum:", pw["locations"]["items"]["properties"]["locationId"].get("enum"))
print("public_world motives[].motiveId enum:", pw["motives"]["items"]["properties"]["motiveId"].get("enum"))
print("public_world scene.locationId enum:", pw["scene"]["properties"]["locationId"].get("enum"))
