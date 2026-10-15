from __future__ import annotations
import hashlib, json, os, sys
from pathlib import Path
BACKEND = Path(r"C:\Users\Fujitsu\coding\procedural-detective\backend")
sys.path.insert(0, str(BACKEND)); sys.path.insert(0, str(BACKEND / "tests"))
import test_phase43a_evidence_algebra as T
from app.generation import evidence_algebra as ea
from app.domain.solver import solve_case

def payload(pub, crime, ev):
    r = ea.complete_evidence_if_ambiguous(pub, crime, ev)
    a = ea.analyze_evidence_gaps(pub, crime, ev)
    proof = solve_case(pub, T._facts(r.evidence))
    doc = {
        "ids": [f.id for f in r.evidence.evidence],
        "facts_added": r.facts_added,
        "dims_before": list(r.dimensions_before),
        "dims_after": list(r.dimensions_after),
        "out_before": r.solver_outcome_before,
        "out_after": r.solver_outcome_after,
        "idempotent": r.idempotent,
        "classification": a.classification,
        "closable": a.closable,
        "proof_who": (proof.who.unique, proof.who.winner),
        "proof_why": (proof.why.unique, proof.why.winner),
        "proof_weapon": (proof.weapon.unique, proof.weapon.winner),
        "proof_when": (proof.when.ambiguous, proof.when.overconstrained),
        "rules": [(x.from_location_id, x.to_location_id, x.travel_time_seconds) for x in r.extra_travel_rules],
        "props": [[(p.type, p.person_id, p.object_id, p.motive_id, p.location_id, p.observed_at, tuple(sorted(dict(p.structured).items()))) for p in f.propositions] for f in r.evidence.evidence],
    }
    return json.dumps(doc, sort_keys=True)

full = T._full(T._public_homicide(), T._crime_homicide())
partial = T._without(full, {f.id for f in full.evidence if f.id.startswith("d_ev_opp_")})
cases = {
    "homicide_empty": (T._public_homicide(), T._crime_homicide(), T._empty_spec()),
    "homicide_partial": (T._public_homicide(), T._crime_homicide(), partial),
    "homicide_full": (T._public_homicide(), T._crime_homicide(), full),
    "suicide_empty": (T._public_suicide(), T._crime_suicide(), T._empty_spec()),
    "witness_murderer": (T._public_witness_murderer(), T._crime_homicide(murderer="witness_a"), T._empty_spec()),
}
out = {k: hashlib.sha256(payload(*v).encode()).hexdigest() for k, v in cases.items()}
for k in sorted(out): print(k, out[k])
print("HASHLIB", hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest())
