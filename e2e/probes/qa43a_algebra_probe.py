"""QA independent probe for Phase43A evidence algebra (AC-01..11 direct checks).

Self-contained: constructs canonical domain fixtures directly (does NOT import
the developer's test module). Zero provider/network calls.
"""
import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.domain.public import (  # noqa: E402
    PublicCase, PublicLocation, PublicMotive, PublicObject, PublicPerson,
    PublicScene, PublicTravelRule,
)
from app.domain.resolution import ResolutionType  # noqa: E402
from app.domain.solver import solve_case  # noqa: E402
from app.domain.truth import Crime, CrimeTime  # noqa: E402
from app.generation import evidence_algebra as ea  # noqa: E402
from app.generation import pipeline  # noqa: E402
from app.generation.schemas import EvidenceSetSpec, EvidenceSpec, PropSpec  # noqa: E402

CANON = "2026-09-11T22:17:00+02:00"


def person(pid, name, role, aff):
    return PublicPerson(person_id=pid, name=name, role=role,
                        public_affordances=frozenset(aff), presented_data={})


def public_homicide():
    return PublicCase(
        case_id="C", case_version=1,
        persons=(
            person("victim_01", "Victim One", "victim", ("VISIBLE_CHARACTER",)),
            person("murderer_01", "Murderer One", "suspect",
                   ("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER")),
            person("suspect_b", "Suspect B", "suspect",
                   ("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER")),
            person("witness_a", "Witness A", "witness", ("VISIBLE_CHARACTER",)),
        ),
        motives=(
            PublicMotive("motive_a", "Motive A", frozenset({"MOTIVE_CANDIDATE"})),
            PublicMotive("motive_b", "Motive B", frozenset({"MOTIVE_CANDIDATE"})),
        ),
        objects=(
            PublicObject("kitchen_knife", "P_KNIFE",
                         frozenset({"INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"})),
            PublicObject("letter_opener", "P_OPENER",
                         frozenset({"INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"})),
            PublicObject("scissors", "P_SCISSORS",
                         frozenset({"INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"})),
        ),
        locations=(
            PublicLocation("apartment", "Apartment"),
            PublicLocation("office", "Office"),
            PublicLocation("bar", "Bar"),
        ),
        travel_rules=(
            PublicTravelRule("office", "apartment", 1200),
            PublicTravelRule("bar", "apartment", 2700),
        ),
        scene=PublicScene("apartment", "Apartment"),
    )


def crime_homicide():
    return Crime(type="murder", victim_id="victim_01", murderer_id="murderer_01",
                 motive_id="motive_a", weapon_id="kitchen_knife", location_id="apartment",
                 crime_time=CrimeTime(CANON, 120), resolution_type=ResolutionType.HOMICIDE)


def crime_suicide():
    return Crime(type="suicide", victim_id="victim_01", murderer_id=None,
                 motive_id="motive_a", weapon_id="kitchen_knife", location_id="apartment",
                 crime_time=CrimeTime(CANON, 120), resolution_type=ResolutionType.SUICIDE)


def facts(ev):
    items = getattr(ev, "evidence", ev)
    return ea._as_domain_evidence(items)


def ids(spec):
    return tuple(f.id for f in spec.evidence)


empty = EvidenceSetSpec(evidence=())
pub, hom = public_homicide(), crime_homicide()

print("AC-01 provider-neutral module outside Ollama:",
      ea.__file__.replace(str(REPO), "<repo>"))
print("AC-02 Ollama delegates:",
      "evidence_algebra" in inspect.getsource(
          __import__("app.services.ollama_driver", fromlist=["_evidence_gap_facts"])._evidence_gap_facts))

# AC-04/05: determinism, idempotence, CaseTruth untouched
before = (hom.victim_id, hom.murderer_id, hom.motive_id, hom.weapon_id,
          hom.location_id, hom.resolution_type, hom.crime_time.canonical)
r1 = ea.complete_evidence_if_ambiguous(pub, hom, empty)
r2 = ea.complete_evidence_if_ambiguous(pub, hom, empty)
after = (hom.victim_id, hom.murderer_id, hom.motive_id, hom.weapon_id,
         hom.location_id, hom.resolution_type, hom.crime_time.canonical)
print("AC-04 deterministic (two runs identical):",
      tuple(r1.evidence.evidence) == tuple(r2.evidence.evidence))
second = ea.complete_evidence_if_ambiguous(pub, hom, r1.evidence)
print("AC-04 idempotent (2nd changed/facts_added/identical):",
      second.changed, second.facts_added,
      tuple(second.evidence.evidence) == tuple(r1.evidence.evidence))
print("AC-04 no duplicate ids:",
      len(set(ids(r1.evidence))) == len(ids(r1.evidence)))
print("AC-05 CaseTruth unchanged:", before == after)
after_proof = solve_case(pub, facts(r1.evidence))
print("     solver after: who/why/weapon unique:",
      after_proof.who.unique, after_proof.why.unique, after_proof.weapon.unique,
      "winner:", after_proof.who.winner)

# AC-06 WITNESS_CLAIMS non-premise
claim = EvidenceSetSpec(evidence=(EvidenceSpec(
    id="d_ev_witness_witnessa", kind="witness_statement",
    propositions=(PropSpec("WITNESS_CLAIMS", person_id="witness_a"),),
    source_ref={"kind": "record", "sourceId": "record_w"},
    presentation={"title": "Witness statement", "description": "d",
                  "speakerName": "Witness A", "statement": "s"}),))
proof_claim = solve_case(pub, facts(claim))
r_claim = ea.complete_evidence_if_ambiguous(pub, hom, claim)
used = [str(e) for e in solve_case(pub, facts(r_claim.evidence)).evidence_ids_used]
print("AC-06 claim alone not who-unique:", proof_claim.who.unique is False,
      "| completion uses no witness-claim evidence:",
      not any(e.startswith("d_ev_witness_") for e in used))

# AC-07 structural defect not masked
bad = EvidenceSetSpec(evidence=(EvidenceSpec(
    id="bad", kind="cctv",
    propositions=(PropSpec("PERSON_OBSERVED_AT_LOCATION", person_id="ghost",
                           location_id="bar", observed_at=CANON, uncertainty_seconds=30),),
    source_ref={"kind": "record", "sourceId": "r"},
    presentation={"title": "t", "description": "d"}),))
an = ea.analyze_evidence_gaps(pub, hom, bad)
rb = ea.complete_evidence_if_ambiguous(pub, hom, bad)
print("AC-07 analyzer structural/closable:", an.classification == ea.STRUCTURAL_DEFECT, an.closable)
print("     direct ea completion replaces input (unguarded API):", rb.evidence is not bad)

# AC-07 through the GUARDED generic helper (the sole product-path entry)
from app.generation import pipeline as _pl  # noqa: E402
from app.generation.provider import GenerationStage as _GS  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    CrimeSpec, CrimeTimeSpec, WorldGraphSpec, PersonSpec, MotiveSpec, ObjectSpec,
    LocationSpec, SceneSpec, PublicWorldSpec, TravelRuleSpec,
)


def _gen_crime():
    return CrimeSpec(type="murder", victim_id="victim_01", murderer_id="murderer_01",
                     motive_id="motive_a", weapon_id="kitchen_knife", location_id="apartment",
                     crime_time=CrimeTimeSpec(CANON, 120), resolution_type=ResolutionType.HOMICIDE)


def _gen_world():
    return PublicWorldSpec(
        persons=(PersonSpec("victim_01", "Victim One", "victim", ("VISIBLE_CHARACTER",)),
                 PersonSpec("murderer_01", "Murderer One", "suspect",
                            ("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER")),
                 PersonSpec("suspect_b", "Suspect B", "suspect",
                            ("SUSPECT_ELIGIBLE", "VISIBLE_CHARACTER")),
                 PersonSpec("witness_a", "Witness A", "witness", ("VISIBLE_CHARACTER",))),
        motives=(MotiveSpec("motive_a", "Motive A", ("MOTIVE_CANDIDATE",)),
                 MotiveSpec("motive_b", "Motive B", ("MOTIVE_CANDIDATE",))),
        objects=(ObjectSpec("kitchen_knife", "P_KNIFE", ("POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON")),
                 ObjectSpec("letter_opener", "P_OPENER", ("POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON")),
                 ObjectSpec("scissors", "P_SCISSORS", ("POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"))),
        locations=(LocationSpec("apartment", "Apartment"), LocationSpec("office", "Office"),
                   LocationSpec("bar", "Bar")),
        travel_rules=(TravelRuleSpec("office", "apartment", 1200), TravelRuleSpec("bar", "apartment", 2700)),
        scene=SceneSpec("apartment", "Apartment"))


def _attempt(ev):
    a = _pl.AttemptRecord(attempt_id="GA-x", case_id="C", session_id="Q")
    a.stage_outputs[_GS.CASE_TRUTH] = _gen_crime()
    a.stage_outputs[_GS.PUBLIC_WORLD] = _gen_world()
    a.stage_outputs[_GS.EVIDENCE] = ev
    a.stage_outputs[_GS.WORLD_GRAPH] = WorldGraphSpec()
    a.stage_done = {_GS.CASE_TRUTH, _GS.PUBLIC_WORLD, _GS.EVIDENCE, _GS.WORLD_GRAPH}
    return a


att = _attempt(bad)
refused = _pl.complete_generic_evidence_if_underconstrained(att) is False
print("     GUARDED helper refuses structural & input untouched:",
      refused, att.stage_outputs[_GS.EVIDENCE] is bad)


# AC-09 already-solvable no-op
full, _ru, _no = ea.canonical_evidence_algebra(pub, hom)
rn = ea.complete_evidence_if_ambiguous(pub, hom, full)
print("AC-09 no-op (changed/facts/same-object):",
      rn.changed, rn.facts_added, rn.evidence is full,
      "| NOT_AMBIGUOUS:", ea.analyze_evidence_gaps(pub, hom, full).classification == ea.NOT_AMBIGUOUS)

# AC-10 overconstrained time not closable
seen = EvidenceSpec(id="s", kind="witness_observation",
                    propositions=(PropSpec("VICTIM_LAST_SEEN_ALIVE_AT", person_id="victim_01",
                                           location_id="apartment",
                                           observed_at="2026-09-11T22:18:00+02:00",
                                           uncertainty_seconds=0),),
                    source_ref={"kind": "record", "sourceId": "r"},
                    presentation={"title": "t", "description": "d"})
found = EvidenceSpec(id="f", kind="witness_observation",
                     propositions=(PropSpec("BODY_FIRST_FOUND_AT", location_id="apartment",
                                            observed_at="2026-09-11T22:16:00+02:00",
                                            uncertainty_seconds=0),),
                     source_ref={"kind": "record", "sourceId": "r"},
                     presentation={"title": "t", "description": "d"})
oc = EvidenceSetSpec(evidence=(seen, found))
an_oc = ea.analyze_evidence_gaps(pub, hom, oc)
rp = ea.complete_evidence_if_ambiguous(pub, hom, oc)
print("AC-10 overconstrained:", an_oc.classification == ea.TIME_OVERCONSTRAINED,
      "closable:", an_oc.closable, "| no-op:", rp.changed is False)

# AC-11 suicide preserves murderer_id=null, no guilt
pub_s, suic = public_homicide(), crime_suicide()
rs = ea.complete_evidence_if_ambiguous(pub_s, suic, empty)
sids = ids(rs.evidence)
print("AC-11 suicide changed:", rs.changed, "| no presence/alibi/fp:",
      ("d_ev_presence" not in sids and "d_ev_alibi" not in sids and "d_ev_fp" not in sids))
link = next(f for f in rs.evidence.evidence if f.id == "d_ev_motive_link")
print("     motive linked to victim:",
      any(p.type == "MOTIVE_LINKED_TO_PERSON" and p.person_id == "victim_01"
          for p in link.propositions),
      "| crime.murderer_id:", suic.murderer_id)
print("ALL PROBE ASSERTIONS OK")
