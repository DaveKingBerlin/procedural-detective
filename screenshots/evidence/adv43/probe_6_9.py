"""ADV-43 probe: valid suicide path + Location lock abuse."""
from __future__ import annotations
import json, sys
sys.path.insert(0, r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\adv43")
from adv_common import attempt, crime_doc, section
from app.generation import parser as parser_mod, pipeline
from app.generation.constraints import LockedConstraints
from app.generation.provider import GenerationStage
from app.generation import trusted_context as tc
from app.generation.schemas import GeneratedDraft, LocationSpec

section("ATTACK 6b: valid suicide through full apply + validate")
a6 = attempt()
a6.stage_outputs[GenerationStage.CASE_TRUTH] = parser_mod.parse_stage(
    GenerationStage.CASE_TRUTH, json.dumps(crime_doc(murderer=None, resolution="SUICIDE")),
    non_throwing=False)
rep6 = pipeline.validate_draft(a6)
print("outcome:", rep6.outcome, "universe:", rep6.universe_issues, "structural:", rep6.structural_issues)
print("assembled suicide murderer:", pipeline.assemble(a6)[2].crime.murderer_id)

section("ATTACK 9: Location lock abuse")
locked, note = pipeline.normalize_prompt("Victim: Laura Stein\nLocation: \n", max_chars=4000)
print("empty Location -> location=", repr(locked.location))
ids = tc.locked_identities(locked)
print("empty Location identities:", [(i.field, repr(i.canonical_id), repr(i.display_name)) for i in ids])
print("empty Location authoritative sheet:", repr(tc.render_locked_identity_sheet(locked)))
a9 = attempt("Victim: Laura Stein\nLocation: \n")
sch9 = pipeline.build_request(a9, GenerationStage.CASE_TRUTH).json_schema
print("empty-location locationId enum:", sch9["properties"]["crime"]["properties"]["locationId"].get("enum"))
print("empty-location selected_environment_kit:", pipeline.selected_environment_kit(a9))

try:
    pipeline.normalize_prompt("Location: apartment\nLocation: office\n", max_chars=4000)
    print("duplicate Location: NO ERROR (BUG?)")
except Exception as e:
    print("duplicate Location raised:", type(e).__name__, e)

from fixtures.golden_generation import GOLDEN_FULL_DRAFT
draft2 = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
print("golden scene:", draft2.scene, "crime.loc:", draft2.crime.location_id)
print("lock('nonexistent_place') violations:", LockedConstraints(location="nonexistent_place").violations_against(draft2))
print("lock('office') violations:", LockedConstraints(location="office").violations_against(draft2))

mask = GeneratedDraft(
    crime=draft2.crime,
    persons=draft2.persons, motives=draft2.motives, objects=draft2.objects,
    locations=draft2.locations + (LocationSpec(location_id="apartment", name="Apartment"),),
    travel_rules=draft2.travel_rules, scene=draft2.scene,
    evidence=draft2.evidence, world_graph=draft2.world_graph,
)
print("MASK lock('apartment') violations (scene kitchen + red-herring 'apartment'):",
      LockedConstraints(location="apartment").violations_against(mask))
print("MASK lock('miller_apartment_kitchen') violations:",
      LockedConstraints(location="miller_apartment_kitchen").violations_against(mask))
