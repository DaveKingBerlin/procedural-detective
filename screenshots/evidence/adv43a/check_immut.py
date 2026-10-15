from __future__ import annotations
import json, sys
from pathlib import Path
BACKEND = Path(r"C:\Users\Fujitsu\coding\procedural-detective\backend")
sys.path.insert(0, str(BACKEND)); sys.path.insert(0, str(BACKEND / "tests"))
import test_phase43a_evidence_algebra as T
from app.generation import pipeline
from app.generation.provider import GenerationStage
from app.domain.eligibility import final_homicide_target_ids, suspect_universe

# witness-murderer generic attempt
pw = T._gen_world_homicide()
# point the hidden murderer at the witness
import dataclasses as _dc; _c = T._gen_crime_homicide(); _c = _dc.replace(_c, murderer_id="witness_a"); att = T._attempt(pw, _c, T._empty_spec())
pw_before = att.stage_outputs[GenerationStage.PUBLIC_WORLD]
persons_before = pw_before.persons
motives_before = pw_before.motives
objects_before = pw_before.objects
rules_before = pw_before.travel_rules
ok = pipeline.complete_generic_evidence_if_underconstrained(att)
pw_after = att.stage_outputs[GenerationStage.PUBLIC_WORLD]
print("completed:", ok)
print("persons tuple identical:", pw_after.persons is persons_before)
print("motives tuple identical:", pw_after.motives is motives_before)
print("objects tuple identical:", pw_after.objects is objects_before)
print("travel_rules identical:", pw_after.travel_rules is rules_before)
w = next(p for p in pw_after.persons if p.person_id == "witness_a")
print("witness role:", w.role, "affordances:", sorted(w.affordances))
# Build the domain public to derive targets
from app.generation.pipeline import _draft_to_phase3
draft = pipeline.current_draft(att)
pub, ev, truth = _draft_to_phase3(draft, case_id=att.case_id, title="t")
print("witness in final targets:", "witness_a" in final_homicide_target_ids(pub))
print("witness in suspect universe:", "witness_a" in suspect_universe(pub))
print("solver WHO:", att.solver_proof.who.unique if att.solver_proof else None, att.solver_proof.who.winner if att.solver_proof else None)
# ensure no SUSPECT_ELIGIBLE was added anywhere
print("any SUSPECT_ELIGIBLE on witness:", any("SUSPECT_ELIGIBLE" in p.affordances for p in pw_after.persons if p.person_id=="witness_a"))


