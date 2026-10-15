"""ADV-43 probe 4i/10: real repair owner scope + enum-agnostic test insensitivity."""
from __future__ import annotations
import copy, dataclasses, json, sys
sys.path.insert(0, r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\adv43")
from adv_common import attempt, section
from app.generation import parser as parser_mod, pipeline
from app.generation.provider import GenerationStage
from fixtures.golden_generation import GOLDEN_FULL_DRAFT

GOLDEN_PROMPT = ("Victim: sarah_miller\nMurderer: thomas_reed\n"
                 "Motive: cover_up_embezzlement\nWeapon: kitchen_knife\n"
                 "Witness: emily_reed\n")

def churn_allowed(draft, section_name, new_id):
    a = attempt(GOLDEN_PROMPT)
    a.draft = draft
    a._repair_pre_ids = pipeline.current_declaration_ids(draft)
    return a

section("ATTACK 4i-real: owner scope from a REAL quality-broken report")
base = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
persons = tuple(dataclasses.replace(p, role="murderer") if p.person_id == "thomas_reed" else p for p in base.persons)
leak = dataclasses.replace(base, persons=persons)
a = attempt(GOLDEN_PROMPT); a.draft = leak
rep = pipeline.validate_draft(a)
print("outcome:", rep.outcome)
print("quality:", rep.quality_issues)
print("authorized sections:", pipeline._authorized_repair_sections(a))
# Now apply a rebuild_request-like snapshot and rename an OBJECT id (not authorized)
a._repair_pre_ids = pipeline.current_declaration_ids(leak)
a._repair_authorized_sections = pipeline._authorized_repair_sections(a)
raw = json.loads(GOLDEN_FULL_DRAFT)
for o in raw["objects"]:
    if o["objectId"] == "letter_opener":
        o["objectId"] = "renamed_object_x"
print("rename object under quality(CASE_PEOPLE) scope:", pipeline.apply_repair(a, json.dumps(raw)))

section("ATTACK 4i-real: owner scope from a REAL structural report")
# structural: crime.murdererId dangling
bad_crime = dataclasses.replace(base.crime, murderer_id="missing_person_zzz")
structural = dataclasses.replace(base, crime=bad_crime)
a2 = attempt(GOLDEN_PROMPT); a2.draft = structural
rep2 = pipeline.validate_draft(a2)
print("outcome:", rep2.outcome)
print("structural:", rep2.structural_issues[:2])
print("authorized sections (structural):", pipeline._authorized_repair_sections(a2))
a2._repair_pre_ids = pipeline.current_declaration_ids(structural)
a2._repair_authorized_sections = pipeline._authorized_repair_sections(a2)
raw2 = json.loads(GOLDEN_FULL_DRAFT)
for o in raw2["objects"]:
    if o["objectId"] == "letter_opener":
        o["objectId"] = "renamed_object_x"
res2 = pipeline.apply_repair(a2, json.dumps(raw2))
print("rename unrelated object id under STRUCTURAL (UNKNOWN) scope:", res2)

section("ATTACK 10: _enum_agnostic insensitivity")

def _enum_agnostic(node):
    if isinstance(node, dict):
        return {k: _enum_agnostic(v) for k, v in node.items() if k != "enum"}
    if isinstance(node, list):
        return [_enum_agnostic(item) for item in node]
    return node

from app.generation import prompts
expected = prompts.json_schema_for_stage_output("case_truth")
sabotaged = copy.deepcopy(expected)
# drop the canonical resolutionType enum (a real regression) and corrupt labels
sabotaged["properties"]["crime"]["properties"]["resolutionType"].pop("enum", None)
sabotaged["properties"]["crime"]["properties"]["victimId"]["enum"] = ["WRONG_DISPLAY_NAME"]
print("raw equality detects drop:", sabotaged == expected)
print("_enum_agnostic equality hides it:", _enum_agnostic(sabotaged) == _enum_agnostic(expected))

# affordance enum drop
expected_e = prompts.json_schema_for_stage_output("evidence")
sab_e = copy.deepcopy(expected_e)
# remove all enums anywhere (simulate a regression that drops canonical enums)
def strip(node):
    if isinstance(node, dict):
        node.pop("enum", None)
        for v in node.values(): strip(v)
    elif isinstance(node, list):
        for v in node: strip(v)
strip(sab_e)
print("evidence: enum_agnostic hides full enum removal:", _enum_agnostic(sab_e) == _enum_agnostic(expected_e))
print("evidence: raw equality would detect:", sab_e == expected_e)

section("ATTACK 10b: the adapted test compares wire vs NO-universe expected")
print("wire-with-universe vs expected-no-universe, enum-stripped equal?",
      _enum_agnostic(prompts.narrow_stage_output_schema("case_truth", reference_universe={"personIds":["x"]}))
      == _enum_agnostic(expected))
