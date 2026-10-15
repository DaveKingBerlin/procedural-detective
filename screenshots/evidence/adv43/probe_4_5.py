"""ADV-43 probe 4/5: repair churn + regeneration."""
from __future__ import annotations
import json, sys
sys.path.insert(0, r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\adv43")
from adv_common import attempt, parse_stage, section, crime_doc
from app.generation import parser as parser_mod, pipeline
from app.generation.provider import GenerationStage
from fixtures.golden_generation import GOLDEN_FULL_DRAFT

GOLDEN_PROMPT = ("Victim: sarah_miller\nMurderer: thomas_reed\n"
                 "Motive: cover_up_embezzlement\nWeapon: kitchen_knife\n"
                 "Witness: emily_reed\n")

section("ATTACK 4 setup: locked ids overlap golden draft")
a = attempt(GOLDEN_PROMPT)
print("locked canonical ids:", pipeline._locked_canonical_ids(a))
draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
a.draft = draft
print("pre ids:", pipeline.current_declaration_ids(draft))

def try_repair(label, mutate, authorized, pre_ids=None):
    aa = attempt(GOLDEN_PROMPT)
    aa.draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
    aa._repair_pre_ids = pre_ids if pre_ids is not None else pipeline.current_declaration_ids(aa.draft)
    aa._repair_authorized_sections = authorized
    raw = json.loads(GOLDEN_FULL_DRAFT)
    mutate(raw)
    before = aa.draft
    issues = pipeline.apply_repair(aa, json.dumps(raw))
    applied = aa.draft is not before
    print(f"{label:52s} issues={issues[:2]} applied={applied}")
    return issues, applied

# a. rename a LOCKED id (thomas_reed -> thomas_changed)
def m_locked(raw):
    for p in raw["persons"]:
        if p["personId"] == "thomas_reed":
            p["personId"] = "thomas_changed"
            p["name"] = "Thomas Changed"
print("--- a) rename LOCKED person id (thomas_reed) ---")
try_repair("locked id rename", m_locked, frozenset({"evidence"}))

# b. rename unrelated id (letter_opener) with evidence-only scope
def m_unrelated(raw):
    for o in raw["objects"]:
        if o["objectId"] == "letter_opener":
            o["objectId"] = "renamed_unrelated_object"
print("--- b) rename unrelated declaration id, authorized=evidence ---")
try_repair("unrelated id rename", m_unrelated, frozenset({"evidence"}))

# c. drop a whole section
def m_drop(raw):
    del raw["objects"]
print("--- c) drop objects section ---")
try_repair("drop objects section", m_drop, None)

# d. add a fabricated id
def m_add(raw):
    raw["objects"].append({"objectId": "fabricated_object_zzz", "assetId": "kitchen_knife",
                           "affordances": ["INSPECTABLE"], "subtype": None})
print("--- d) add fabricated object id ---")
try_repair("add fabricated id", m_add, frozenset({"evidence"}))

# e. rename id diagnostics did not authorize (persons not authorized, evidence is)
def m_person(raw):
    for p in raw["persons"]:
        if p["personId"] == "michael_carter":
            p["personId"] = "michael_carter_v2"
print("--- e) rename person id, authorized=evidence ---")
try_repair("unauthorized person rename", m_person, frozenset({"evidence"}))

# f. legitimate single-defect repair: no id change
print("--- f) legitimate no-id-change repair ---")
try_repair("no id change", lambda raw: None, frozenset({"evidence"}))

# g. authorized section rename (persons authorized) -> allowed
print("--- g) authorized persons rename (michael_carter) ---")
try_repair("authorized person rename", m_person, frozenset({"persons"}))

# h. no snapshot -> fail open
print("--- h) no snapshot (fail-open) ---")
aa = attempt(GOLDEN_PROMPT)
aa.draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
aa._repair_pre_ids = None
issues = pipeline.apply_repair(aa, GOLDEN_FULL_DRAFT)
print("no-snapshot issues:", issues)

section("ATTACK 4i: _authorized_repair_sections mapping from reports")
from app.generation.report import ValidationReport
from app.generation import repair_routing
for codes in ([], ["EVIDENCE_PROPOSITION_UNKNOWN_PERSON"], ["WORLD_ASSET_UNRESOLVED"],
              ["WITNESS_STATEMENT_MISSING"], ["PUBLIC_ROLE_TRUTH_LEAK"], ["candidate_universe_empty"]):
    rep = ValidationReport(quality_issues=tuple(codes), structural_issues=tuple(codes))
    a2 = attempt(GOLDEN_PROMPT); a2.last_validation = rep
    # need draft for plan? _authorized_repair_sections only reads report
    try:
        print(f"codes={codes} -> authorized={pipeline._authorized_repair_sections(a2)}")
    except Exception as e:
        print(f"codes={codes} -> ERROR {type(e).__name__}: {e}")

section("ATTACK 5: regeneration preserves locked namespace")
from app.generation.controller import GenerationController
import types
a3 = attempt()
first = pipeline.build_request(a3, GenerationStage.CASE_TRUTH)
a3.stage_outputs[GenerationStage.CASE_TRUTH] = parse_stage(GenerationStage.CASE_TRUTH, json.dumps(crime_doc()))
a3.draft = parser_mod.parse_full_draft(GOLDEN_FULL_DRAFT)
a3._repair_pre_ids = {"persons": ("x",)}
GenerationController._reset_for_regeneration(types.SimpleNamespace(_driver=None), a3)
second = pipeline.build_request(a3, GenerationStage.CASE_TRUTH)
print("message identical after reset:", first.prompt_context == second.prompt_context)
print("locked preserved:", a3.locked.locked_fields())
print("pre_ids cleared:", a3._repair_pre_ids)
print("stage_outputs cleared:", a3.stage_outputs)
print("draft cleared:", a3.draft)
for c in ("laura_stein","daniel_roth","nina_weber"):
    print("  canonical in regen sheet:", c, f"canonicalId: {c}" in second.prompt_context)
