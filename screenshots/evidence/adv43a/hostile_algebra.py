from __future__ import annotations
import copy, dataclasses, itertools, json, random, sys
from pathlib import Path
BACKEND = Path(r"C:\Users\Fujitsu\coding\procedural-detective\backend")
sys.path.insert(0, str(BACKEND)); sys.path.insert(0, str(BACKEND / "tests"))
import test_phase43a_evidence_algebra as T
from app.generation import evidence_algebra as ea
from app.generation import pipeline
from app.generation.provider import GenerationStage
from app.generation.schemas import EvidenceSetSpec, EvidenceSpec, PropSpec, WorldGraphSpec
from app.domain.solver import solve_case
from app.domain.public import PublicCase, PublicPerson
from app.domain.evidence import validate_evidence, EvidenceFact, SourceRef, TypedProposition

def report(name, ok, detail=""):
    print(("PASS " if ok else "ANOM ") + name + (("  :: "+detail) if detail else ""))

print("=== ATTACK 2: CaseTruth immutability ===")
for label, pub, crime in [
    ("homicide", T._public_homicide(), T._crime_homicide()),
    ("suicide", T._public_suicide(), T._crime_suicide()),
    ("witness-murderer", T._public_witness_murderer(), T._crime_homicide(murderer="witness_a")),
]:
    before = dataclasses.asdict(crime)
    before_type = type(crime)
    ea.complete_evidence_if_ambiguous(pub, crime, T._empty_spec())
    after = dataclasses.asdict(crime)
    report("immutable:"+label, before == after and type(crime) is before_type, f"before={before} after={after}")

# attempt-level: CASE_TRUTH stage output object must be untouched
att = T._attempt(T._gen_world_homicide(), T._gen_crime_homicide(), T._empty_spec())
crime_obj = att.stage_outputs[GenerationStage.CASE_TRUTH]
snap = dataclasses.asdict(crime_obj)
pipeline.complete_generic_evidence_if_underconstrained(att)
report("immutable:attempt CASE_TRUTH spec", dataclasses.asdict(crime_obj) == snap)
# public_world may be replaced (travel rules) but the ORIGINAL must not be mutated
_pw = att.stage_outputs[GenerationStage.PUBLIC_WORLD]
pw_before = (len(_pw.persons), len(_pw.travel_rules), len(_pw.locations))

print()
print("=== ATTACK 3: witness-claims-as-premise ===")
pub, crime = T._public_homicide(), T._crime_homicide()
claim_only = EvidenceSetSpec(evidence=(
    EvidenceSpec(id="w1", kind="witness_statement",
        propositions=(PropSpec("WITNESS_CLAIMS", person_id="witness_a"),),
        source_ref={"kind":"record","sourceId":"record_w1"},
        presentation={"title":"Witness statement","description":"claim","speakerName":"Witness A","statement":"I saw X do it"}),
))
proof_before = solve_case(pub, T._facts(claim_only))
report("claim-only WHO not unique", proof_before.who.unique is False)
res = ea.complete_evidence_if_ambiguous(pub, crime, claim_only)
proof_after = solve_case(pub, T._facts(res.evidence))
report("post-completion WHO is TRUE murderer (not the claim target)", proof_after.who.unique and proof_after.who.winner == "murderer_01", str(proof_after.who.winner))
# claim naming an INNOCENT as killer must not flip the solver
claim_innocent = EvidenceSetSpec(evidence=(
    EvidenceSpec(id="w2", kind="witness_statement",
        propositions=(PropSpec("WITNESS_CLAIMS", person_id="suspect_b"),),
        source_ref={"kind":"record","sourceId":"record_w2"},
        presentation={"title":"t","description":"d","speakerName":"Suspect B","statement":"suspect_b did it"}),
))
proof2 = solve_case(pub, T._facts(claim_innocent))
report("claim naming innocent does not make innocent unique", proof2.who.unique is False or proof2.who.winner != "suspect_b", str(proof2.who.winner))

print()
print("=== ATTACK 4: idempotence + ordering/permutation ===")
rng = random.Random(1234)
fixtures = [
    ("homicide-empty", T._public_homicide(), T._crime_homicide(), T._empty_spec()),
    ("suicide-empty", T._public_suicide(), T._crime_suicide(), T._empty_spec()),
    ("witness-murderer-empty", T._public_witness_murderer(), T._crime_homicide(murderer="witness_a"), T._empty_spec()),
]
full = T._full(T._public_homicide(), T._crime_homicide())
fixtures.append(("homicide-full", T._public_homicide(), T._crime_homicide(), full))
partial = T._without(full, {f.id for f in full.evidence if f.id.startswith("d_ev_opp_")})
fixtures.append(("homicide-partial", T._public_homicide(), T._crime_homicide(), partial))
contra = EvidenceSetSpec(evidence=full.evidence + (EvidenceSpec(id="z", kind="cctv",
    propositions=(PropSpec("TIME_WINDOW_EXCLUSION", observed_at=T._CANONICAL, uncertainty_seconds=0),),
    source_ref={"kind":"record","sourceId":"record_z"}, presentation={"title":"t","description":"d"}),))
fixtures.append(("homicide-contradictory", T._public_homicide(), T._crime_homicide(), contra))
for label, pub, crime, ev in fixtures:
    first = ea.complete_evidence_if_ambiguous(pub, crime, ev)
    second = ea.complete_evidence_if_ambiguous(pub, crime, first.evidence)
    ids1 = tuple(f.id for f in first.evidence.evidence)
    ids2 = tuple(f.id for f in second.evidence.evidence)
    dup = len(ids1) != len(set(ids1))
    same_proof = solve_case(pub, T._facts(first.evidence)) == solve_case(pub, T._facts(second.evidence))
    report(f"idempotent:{label}", first.idempotent and second.changed is False and second.facts_added == 0 and ids1 == ids2 and not dup and same_proof,
           f"changed={first.changed} second.changed={second.changed} dup={dup} idempotent={first.idempotent}")

# permuted AMBIGUOUS input must yield the SAME canonical (generated) output.
pub, crime = T._public_homicide(), T._crime_homicide()
base = T._empty_spec()
res_base = ea.complete_evidence_if_ambiguous(pub, crime, base)
order_ok = True
for seed in range(10):
    r = random.Random(seed)
    perm = list(partial.evidence); r.shuffle(perm)
    ev = EvidenceSetSpec(evidence=tuple(perm))
    res = ea.complete_evidence_if_ambiguous(pub, crime, ev)
    if tuple(f.id for f in res.evidence.evidence) != tuple(f.id for f in res_base.evidence.evidence):
        order_ok = False; print("   seed", seed, "diff")
report("permuted AMBIGUOUS input order -> identical canonical output", order_ok)
# NOTE: a permuted ALREADY-SOLVABLE input is returned UNCHANGED (AC-09 no-op), so it
# preserves the input order by design; recorded as a nuance, not a determinism defect.
full_perm = EvidenceSetSpec(evidence=tuple(reversed(full.evidence)))
res_full_perm = ea.complete_evidence_if_ambiguous(pub, crime, full_perm)
report("already-solvable input returned unchanged (ordering preserved by design)",
       res_full_perm.changed is False and res_full_perm.evidence is full_perm)
# permuted PUBLIC ordering
for seed in range(8):
    r = random.Random(seed)
    persons = list(pub.persons); r.shuffle(persons)
    locs = list(pub.locations); r.shuffle(locs)
    objs = list(pub.objects); r.shuffle(objs)
    mts = list(pub.motives); r.shuffle(mts)
    pubs = PublicCase(case_id=pub.case_id, case_version=1, persons=tuple(persons), motives=tuple(mts), objects=tuple(objs), locations=tuple(locs), travel_rules=pub.travel_rules, scene=pub.scene)
    res = ea.complete_evidence_if_ambiguous(pubs, crime, base)
    if tuple(f.id for f in res.evidence.evidence) != tuple(f.id for f in res_base.evidence.evidence):
        report(f"permuted public order seed={seed}", False, "output order changed"); break
else:
    report("permuted public tuple order -> identical output", True)

print()
print("=== ATTACK 5: suicide ===")
pub, crime = T._public_suicide(), T._crime_suicide()
res = ea.complete_evidence_if_ambiguous(pub, crime, T._empty_spec())
ids = [f.id for f in res.evidence.evidence]
report("suicide: no presence/alibi/fp", not any(x in ids for x in ("d_ev_presence","d_ev_alibi","d_ev_fp")), str(ids))
link = next(f for f in res.evidence.evidence if f.id == "d_ev_motive_link")
report("suicide: motive victim-linked", any(p.type=="MOTIVE_LINKED_TO_PERSON" and p.person_id=="victim_01" for p in link.propositions))
# domain guard: Crime itself forbids murderer_id on SUICIDE.
try:
    T._crime_suicide().__class__(type="suicide", victim_id="victim_01", murderer_id="murderer_01",
        motive_id="motive_a", weapon_id="kitchen_knife", location_id="apartment",
        crime_time=T._crime_suicide().crime_time, resolution_type=T._crime_suicide().resolution_type)
    report("domain guard: SUICIDE+murderer_id rejected", False, "constructed!")
except ValueError:
    report("domain guard: SUICIDE+murderer_id rejected", True)
# duck-typed probe: a string resolution_type SUICIDE with a stray murderer_id.
class Duck:
    def __init__(self, **kw): self.__dict__.update(kw)
base_crime = T._crime_suicide()
duck = Duck(type="suicide", victim_id="victim_01", murderer_id="murderer_01", motive_id="motive_a",
            weapon_id="kitchen_knife", location_id="apartment", resolution_type="SUICIDE",
            crime_time=base_crime.crime_time)
res2 = ea.complete_evidence_if_ambiguous(pub, duck, T._empty_spec())
ids2 = [f.id for f in res2.evidence.evidence]
report("duck string-SUICIDE + murderer_id: no guilt facts", not any(x in ids2 for x in ("d_ev_presence","d_ev_alibi","d_ev_fp")), str(ids2))
# unknown resolution token with a murderer -> is it treated as HOMICIDE?
unknown = Duck(type="accident", victim_id="victim_01", murderer_id="murderer_01", motive_id="motive_a",
               weapon_id="kitchen_knife", location_id="apartment", resolution_type="ACCIDENT",
               crime_time=base_crime.crime_time)
res3 = ea.complete_evidence_if_ambiguous(pub, unknown, T._empty_spec())
ids3 = [f.id for f in res3.evidence.evidence]
report("UNKNOWN resolution token not silently completed as homicide", "d_ev_presence" not in ids3, f"ids={ids3} classification={ea.analyze_evidence_gaps(pub, unknown, T._empty_spec()).classification}")

print()
print("=== ATTACK 6: witness-murderer ===")
pub, crime = T._public_witness_murderer(), T._crime_homicide(murderer="witness_a")
res = ea.complete_evidence_if_ambiguous(pub, crime, T._empty_spec())
proof = solve_case(pub, T._facts(res.evidence))
w = next(p for p in pub.persons if p.person_id == "witness_a")
report("witness stays role=witness, no SUSPECT_ELIGIBLE", w.role == "witness" and "SUSPECT_ELIGIBLE" not in w.public_affordances)
report("WHO unique = witness_a", proof.who.unique and proof.who.winner == "witness_a", str(proof.who.winner))
from app.domain.eligibility import final_homicide_target_ids, suspect_universe
report("witness remains a final target", "witness_a" in final_homicide_target_ids(pub))
report("witness not in suspect universe", "witness_a" not in suspect_universe(pub))
print()
print("DONE")



