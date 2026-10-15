from __future__ import annotations
import dataclasses, json, sys
from pathlib import Path
BACKEND = Path(r"C:\Users\Fujitsu\coding\procedural-detective\backend")
sys.path.insert(0, str(BACKEND)); sys.path.insert(0, str(BACKEND / "tests"))
import test_phase43a_evidence_algebra as T
from app.generation import pipeline
from app.generation.provider import GenerationStage
from app.generation.schemas import (EvidenceSetSpec, EvidenceSpec, PropSpec, WorldGraphSpec, PlacementSpec)
from app.generation.constraints import LockedConstraints
from app.generation import prompts

def run(label, attempt, expect_complete):
    ev_before = attempt.stage_outputs.get(GenerationStage.EVIDENCE)
    got = pipeline.complete_generic_evidence_if_underconstrained(attempt)
    ev_after = attempt.stage_outputs.get(GenerationStage.EVIDENCE)
    ok = (got is expect_complete)
    # when refused, the evidence must be untouched
    if not expect_complete:
        ok = ok and (ev_after is ev_before)
    print(("PASS " if ok else "ANOM ") + f"{label}: completed={got} expected={expect_complete} ev_changed={ev_after is not ev_before}")

def base_attempt(**kw):
    return T._attempt(T._gen_world_homicide(**kw), T._gen_crime_homicide(), T._empty_spec())

EMPTY = T._empty_spec()
CAN = T._CANONICAL

def bad_person():
    ev = EvidenceSetSpec(evidence=(EvidenceSpec(id="e1", kind="cctv",
        propositions=(PropSpec("PERSON_OBSERVED_AT_LOCATION", person_id="ghost", location_id="bar", observed_at=CAN, uncertainty_seconds=30),),
        source_ref={"kind":"record","sourceId":"r1"}, presentation={"title":"t","description":"d"}),))
    return T._attempt(T._gen_world_homicide(), T._gen_crime_homicide(), ev)
def bad_object():
    ev = EvidenceSetSpec(evidence=(EvidenceSpec(id="e1", kind="forensic",
        propositions=(PropSpec("FORENSIC_WEAPON_MATCH", object_id="ghost", structured={"match": True}),),
        source_ref={"kind":"record","sourceId":"r1"}, presentation={"title":"t","description":"d"}),))
    return T._attempt(T._gen_world_homicide(), T._gen_crime_homicide(), ev)
def bad_location():
    ev = EvidenceSetSpec(evidence=(EvidenceSpec(id="e1", kind="cctv",
        propositions=(PropSpec("PERSON_OBSERVED_AT_LOCATION", person_id="witness_a", location_id="ghost", observed_at=CAN, uncertainty_seconds=30),),
        source_ref={"kind":"record","sourceId":"r1"}, presentation={"title":"t","description":"d"}),))
    return T._attempt(T._gen_world_homicide(), T._gen_crime_homicide(), ev)
def bad_motive():
    ev = EvidenceSetSpec(evidence=(EvidenceSpec(id="e1", kind="email",
        propositions=(PropSpec("MOTIVE_FACT_CONTRADICTED", motive_id="ghost"),),
        source_ref={"kind":"record","sourceId":"r1"}, presentation={"title":"t","description":"d"}),))
    return T._attempt(T._gen_world_homicide(), T._gen_crime_homicide(), ev)
def bad_world_ref():
    wg = WorldGraphSpec(placements=(PlacementSpec(object_id="ghost_object", asset_id="PROP_KITCHEN_KNIFE_01", location_id="apartment", anchor="x", interaction=""),))
    return T._attempt(T._gen_world_homicide(), T._gen_crime_homicide(), EMPTY, wg)
def role_truth_leak():
    pw = T._gen_world_homicide()
    persons = tuple(dataclasses.replace(p, role="murderer") if p.person_id=="victim_01" else p for p in pw.persons)
    pw2 = dataclasses.replace(pw, persons=persons)
    return T._attempt(pw2, T._gen_crime_homicide(), EMPTY)
def locked_mismatch():
    a = base_attempt()
    a.locked = LockedConstraints(victim="victim_01", murderer="murderer_01", motive="motive_a", weapon="kitchen_knife", crime_time=CAN, witness="ghost_witness", location="apartment")
    return a
def empty_final_targets():
    pw = T._gen_world_homicide()
    persons = tuple(p for p in pw.persons if p.role not in ("suspect","witness"))
    pw2 = dataclasses.replace(pw, persons=persons)
    return T._attempt(pw2, T._gen_crime_homicide(), EMPTY)
def witness_eligible():
    return T._attempt(T._gen_world_homicide(witness_eligible=True), T._gen_crime_homicide(), EMPTY)

print("=== ATTACK 1: structural/domain defects must REFUSE ===")
run("unknown person id", bad_person(), False)
run("unknown object id", bad_object(), False)
run("unknown location id", bad_location(), False)
run("unknown motive id", bad_motive(), False)
run("broken world-graph reference", bad_world_ref(), False)
run("public role truth leak", role_truth_leak(), False)
run("locked witness mismatch", locked_mismatch(), False)
run("empty final-target universe", empty_final_targets(), False)
run("witness carries SUSPECT_ELIGIBLE", witness_eligible(), False)
run("pure evidence ambiguity (control) -> completes", base_attempt(), True)

print()
print("=== ATTACK 1b: EVIDENCE parse failure ownership ===")
# EVIDENCE malformed alone -> is treated as algebra-owned and completes
a = T._attempt_with_deferred_evidence_failure()
run("malformed EVIDENCE alone -> completes (behaviour change)", a, True)
print("   note: deferred_by_stage[EVIDENCE] after:", GenerationStage.EVIDENCE in a.deferred_by_stage, "union:", a.deferred_structural)

# malformed EVIDENCE + a DIFFERENT stage deferred issue (must refuse)
for stage in (GenerationStage.CASE_TRUTH, GenerationStage.PUBLIC_WORLD, GenerationStage.WORLD_GRAPH):
    a = T._attempt_with_deferred_evidence_failure()
    pipeline._record_deferred_issues(a, stage, (f"{stage.value}: distinct-issue",))
    run(f"malformed EVIDENCE + {stage.value} deferred issue", a, False)

# malformed EVIDENCE + validate_evidence structural defect (unknown id)
a = T._attempt_with_deferred_evidence_failure()
a.stage_outputs[GenerationStage.EVIDENCE] = None  # ensure empty
# add an EVIDENCE-stage deferred failure AND an evidence unknown-id via a parse-success path is impossible;
# instead put the unknown-id defect in the WORLD_GRAPH (safety) +
pipeline._record_deferred_issues(a, GenerationStage.WORLD_GRAPH, ("world_graph: unknown reference",))
run("malformed EVIDENCE + WORLD_GRAPH distinct issue", a, False)

print()
print("=== ATTACK 1c: same-message collision probe (latent) ===")
# Two stages record the IDENTICAL generic JSON error string.
a = T._attempt(T._gen_world_homicide(), T._gen_crime_homicide(), EMPTY)
pipeline.apply_stage_output(a, GenerationStage.EVIDENCE, "not-json")
pipeline.apply_stage_output(a, GenerationStage.WORLD_GRAPH, "not-json")
ev_deferred = a.deferred_by_stage.get(GenerationStage.EVIDENCE, ())
wg_deferred = a.deferred_by_stage.get(GenerationStage.WORLD_GRAPH, ())
print("   EVIDENCE deferred:", ev_deferred)
print("   WORLD_GRAPH deferred:", wg_deferred)
print("   identical strings:", set(ev_deferred) & set(wg_deferred) == set(ev_deferred) and bool(ev_deferred))
got = pipeline.complete_generic_evidence_if_underconstrained(a)
print("   completed despite WORLD_GRAPH parse failure:", got)
print("   WORLD_GRAPH deferred still present after:", GenerationStage.WORLD_GRAPH in a.deferred_by_stage)
print("   union still contains WG msg:", any("not valid JSON" in x for x in a.deferred_structural))
print("   stage EVIDENCE now set:", GenerationStage.EVIDENCE in a.stage_outputs)

print()
print("=== ATTACK 7: breadth rules are guidance-only (present, but no validator bypass) ===")
att = T._attempt(T._gen_world_homicide(), T._gen_crime_homicide(), EMPTY)
ctx = pipeline._authoritative_public_world_block(att)
print("   breadth rules in public_world context:", prompts.PUBLIC_WORLD_BREADTH_RULES in ctx)
print("   breadth rules in repair context:", prompts.PUBLIC_WORLD_BREADTH_RULES in pipeline._authoritative_repair_block(att))
print("   breadth rules mention validator bypass:", any(t in prompts.PUBLIC_WORLD_BREADTH_RULES.lower() for t in ("skip validation","bypass","ignore validator","already valid")), "(expect False)")
print()
print("DONE")
