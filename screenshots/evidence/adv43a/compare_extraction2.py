from __future__ import annotations
import ast, json, sys
from pathlib import Path
BACKEND = Path(r"C:\Users\Fujitsu\coding\procedural-detective\backend")
sys.path.insert(0, str(BACKEND)); sys.path.insert(0, str(BACKEND / "tests"))
from app.domain.public import (PublicCase, PublicLocation, PublicMotive, PublicObject, PublicPerson, PublicScene, PublicTravelRule)
from app.domain.resolution import ResolutionType
from app.domain.truth import Crime, CrimeTime
from app.domain.witness_truth import WitnessStatementTruth
from app.generation import evidence_algebra as ea
from app.generation import trusted_context as tc
from app.generation import parser as gparser
from fixtures.golden_generation import GOLDEN_FULL_DRAFT, assemble_phase3
BASELINE_SRC = Path(r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\adv43a\baseline_ollama_driver.py").read_text(encoding="utf-8-sig")
NAMES = {"_driver_clock_token","_driver_scene_label","_evidence_gap_facts","_canonical_prop","_canonical_evidence_item","_canonical_distant_location"}
def extract(src, names):
    tree = ast.parse(src); out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names: out[node.name]=ast.get_source_segment(src,node)
    return out
srcs = extract(BASELINE_SRC, NAMES)
ns = {"Any": object, "_BASE_SHARP_WEAPON_IDS": tc._BASE_SHARP_WEAPON_IDS, "_DEFAULT_DISTANT_TRAVEL_SECONDS": tc._DEFAULT_DISTANT_TRAVEL_SECONDS, "_evidence_timeline": tc.evidence_timeline}
for k,v in srcs.items(): exec(compile(v,"<b:"+k+">","exec"), ns)
baseline_gap = ns["_evidence_gap_facts"]
class Attempt:
    _witness_truthfulness = ()
def P(pid,name,role,aff): return PublicPerson(person_id=pid,name=name,role=role,public_affordances=frozenset(aff),presented_data={})
def base_public():
    return PublicCase(case_id="CASE-X",case_version=1,
      persons=(P("victim_01","Victim One","victim",("VISIBLE_CHARACTER",)),P("murderer_01","Murderer One","suspect",("SUSPECT_ELIGIBLE","VISIBLE_CHARACTER")),P("suspect_b","Suspect B","suspect",("SUSPECT_ELIGIBLE","VISIBLE_CHARACTER")),P("witness_a","Witness A","witness",("VISIBLE_CHARACTER",))),
      motives=(PublicMotive("motive_a","Motive A",frozenset({"MOTIVE_CANDIDATE"})),PublicMotive("motive_b","Motive B",frozenset({"MOTIVE_CANDIDATE"}))),
      objects=(PublicObject("kitchen_knife","PROP_KITCHEN_KNIFE_01",frozenset({"INSPECTABLE","POTENTIAL_WEAPON","POTENTIAL_SHARP_WEAPON"})),PublicObject("letter_opener","PROP_LETTER_OPENER_01",frozenset({"INSPECTABLE","POTENTIAL_WEAPON","POTENTIAL_SHARP_WEAPON"})),PublicObject("scissors","PROP_SCISSORS_01",frozenset({"INSPECTABLE","POTENTIAL_WEAPON","POTENTIAL_SHARP_WEAPON"}))),
      locations=(PublicLocation("apartment","Apartment"),PublicLocation("office","Office"),PublicLocation("bar","Bar")),
      travel_rules=(PublicTravelRule("office","apartment",1200),PublicTravelRule("bar","apartment",2700)),
      scene=PublicScene("apartment","Apartment"))
CT="2026-09-11T22:17:00+02:00"
def c_hom(m="murderer_01"): return Crime(type="murder",victim_id="victim_01",murderer_id=m,motive_id="motive_a",weapon_id="kitchen_knife",location_id="apartment",crime_time=CrimeTime(CT,120),resolution_type=ResolutionType.HOMICIDE)
def c_sui(): return Crime(type="suicide",victim_id="victim_01",murderer_id=None,motive_id="motive_a",weapon_id="kitchen_knife",location_id="apartment",crime_time=CrimeTime(CT,120),resolution_type=ResolutionType.SUICIDE)
def norm(spec):
    if spec is None: return None
    items=getattr(spec,"evidence",spec)
    return [{"id":f.id,"kind":f.kind,"props":[{"t":p.type,"p":p.person_id,"l":p.location_id,"o":p.object_id,"m":p.motive_id,"at":p.observed_at,"u":p.uncertainty_seconds,"s":dict(p.structured)} for p in f.propositions],"pres":dict(f.presentation)} for f in items]
def rnorm(rs): return [{"f":r.from_location_id,"t":r.to_location_id,"s":r.travel_time_seconds} for r in rs]
def cmp(label, public, crime, wt=()):
    att=Attempt(); att._witness_truthfulness=tuple(wt)
    b_spec,b_rules,_=baseline_gap(att,crime,public,None)
    n_spec,n_rules,_=ea.canonical_evidence_algebra(public,crime,witness_truthfulness=tuple(wt))
    b,n=norm(b_spec),norm(n_spec); diffs=[]
    if b!=n:
        diffs.append("ids b="+str([x["id"] for x in b or []])+" n="+str([x["id"] for x in n or []]))
        for x,y in zip(b or [],n or []):
            if x!=y:
                diffs.append("  b:"+json.dumps(x,sort_keys=True)); diffs.append("  n:"+json.dumps(y,sort_keys=True))
    if rnorm(b_rules)!=rnorm(n_rules): diffs.append("RULES b="+str(rnorm(b_rules))+" n="+str(rnorm(n_rules)))
    print(("[SAME] " if not diffs else "[DIVERGENT] ")+label)
    for d in diffs: print("   ",d)
    return not diffs
def extra_weapon():
    p=base_public()
    return PublicCase(case_id=p.case_id,case_version=1,persons=p.persons,motives=p.motives,objects=p.objects+(PublicObject("baseball_bat","PROP_BASEBALL_BAT_01",frozenset({"INSPECTABLE","POTENTIAL_WEAPON","POTENTIAL_BLUNT_WEAPON"})),),locations=p.locations,travel_rules=p.travel_rules,scene=p.scene)
def short_rule():
    p=base_public()
    return PublicCase(case_id=p.case_id,case_version=1,persons=p.persons,motives=p.motives,objects=p.objects,locations=(PublicLocation("apartment","Apartment"),PublicLocation("hallway","Hallway")),travel_rules=(PublicTravelRule("hallway","apartment",20),PublicTravelRule("apartment","hallway",20)),scene=p.scene)
def wm():
    p=base_public(); persons=tuple(x for x in p.persons if x.person_id!="murderer_01")
    return PublicCase(case_id=p.case_id,case_version=1,persons=persons,motives=p.motives,objects=p.objects,locations=p.locations,travel_rules=p.travel_rules,scene=p.scene)
same=True
same&=cmp("HOMICIDE plain",base_public(),c_hom())
same&=cmp("SUICIDE",base_public(),c_sui())
same&=cmp("WITNESS-MURDERER",wm(),c_hom("witness_a"))
stamp=(WitnessStatementTruth(witness_id="witness_a",question_type="WHO",truthfulness="DECEPTIVE",deception_reason="SELF_PROTECTION"),)
same&=cmp("HOMICIDE+deceptive witness",base_public(),c_hom(),stamp)
same&=cmp("HOMICIDE+extra weapon",extra_weapon(),c_hom())
same&=cmp("HOMICIDE+short travel rule",short_rule(),c_hom())
# Golden Ollama fixture end-to-end
draft=gparser.parse_full_draft(GOLDEN_FULL_DRAFT)
pub,ev,truth=assemble_phase3(draft)
same&=cmp("GOLDEN OLLAMA KIT (homicide)",pub,truth.crime)
# Golden suicide fixture: swap resolution/murderer on the golden crime
gold_crime=truth.crime
sui=Crime(type=gold_crime.type,victim_id=gold_crime.victim_id,murderer_id=None,motive_id=gold_crime.motive_id,weapon_id=gold_crime.weapon_id,location_id=gold_crime.location_id,crime_time=gold_crime.crime_time,resolution_type=ResolutionType.SUICIDE)
same&=cmp("GOLDEN OLLAMA KIT (suicide variant)",pub,sui)
# Golden witness-murderer variant: point murderer at the public witness emily_reed
wmcrime=Crime(type=gold_crime.type,victim_id=gold_crime.victim_id,murderer_id="emily_reed",motive_id=gold_crime.motive_id,weapon_id=gold_crime.weapon_id,location_id=gold_crime.location_id,crime_time=gold_crime.crime_time,resolution_type=ResolutionType.HOMICIDE)
same&=cmp("GOLDEN OLLAMA KIT (witness-murderer variant)",pub,wmcrime)
print("ALL SAME:", same)
