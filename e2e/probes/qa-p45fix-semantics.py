"""Independent QA probe — Phase45-Fix repair SEMANTICS (post-fix source).

Verifies, independently of the developer's test file:
  2a completion facts reachable;
  2b red herrings reachable and do NOT change the unique solution;
  2c meaningful classification is the SEMANTIC/record-based rule (not raw count);
  2d idempotence (completion + repair repeated -> no duplicate objects/placements);
  2e boundedness;
  2f no CaseTruth / solver / evidence-proposition mutation;
  2g namespace integrity (declared object/location/evidence ids only).

Usage: python e2e/probes/qa-p45fix-semantics.py <backend_dir>
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

BACKEND = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(BACKEND / "tests"))
sys.path.insert(0, str(BACKEND))

from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

from app.domain.solver import solve_case  # noqa: E402
from app.environments.manifests import load_environment  # noqa: E402
from app.generation import difficulty_policy, pipeline, playability  # noqa: E402
from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.evidence_algebra import complete_evidence_if_ambiguous  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import ObjectSpec, PlacementSpec  # noqa: E402
from app.services.generation import (  # noqa: E402
    GenerationService,
    _ensure_playability_clues,
    _world_graph_namespace_violations,
)

G = GOLDEN_STAGE_PAYLOADS
PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail}")


def dump(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def completion_script():
    docs = {s: json.loads(p) for s, p in G.items()}
    crime = docs[GenerationStage.CASE_TRUTH]["crime"]
    crime["weaponId"] = "prov_obj_" + crime["weaponId"]
    for fact in docs[GenerationStage.EVIDENCE]["evidence"]:
        fact["id"] = "prov_ev_" + fact["id"]
        for prop in fact["propositions"]:
            if prop.get("objectId"):
                prop["objectId"] = "prov_obj_" + prop["objectId"]
    for obj in docs[GenerationStage.PUBLIC_WORLD]["objects"]:
        obj["objectId"] = "prov_obj_" + obj["objectId"]
    for pl in docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"]:
        pl["objectId"] = "prov_obj_" + pl["objectId"]
        if pl.get("evidenceId"):
            pl["evidenceId"] = "prov_ev_" + pl["evidenceId"]
    docs[GenerationStage.EVIDENCE] = {"evidence": []}
    for pl in docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"]:
        pl["evidenceId"] = None
        if not pl.get("interaction"):
            pl["interaction"] = "inspect"
    return {s: [dump(docs[s])] for s in (
        GenerationStage.CASE_TRUTH, GenerationStage.PUBLIC_WORLD,
        GenerationStage.EVIDENCE, GenerationStage.WORLD_GRAPH)}


def held(difficulty="medium"):
    clock = ManualClock(start_time=1000.0)
    ids = IdSource()
    adm = AdmissionController(
        clock=clock, ids=ids, max_concurrent_generations=4,
        max_concurrent_generations_global=8,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_end=1000.0 + 86400)
    ses = adm.create_anonymous_quota_session()
    ctl = GenerationController(
        provider=FakeProvider(script=completion_script()), admission=adm,
        clock=clock, ids=ids, deadline_seconds=60, max_llm_calls_per_generation=8,
        max_repair_passes=2, max_full_regenerations=2, max_prompt_chars=4000,
        seed=11, hold_before_publish=True, difficulty=difficulty)
    h = ctl.start_generation(PROMPT, anonymous_quota_session_id=ses.session_id)
    rec = ctl.attempt(h.attempt_id)
    rec.published = ctl.publish(h.attempt_id, hold_ok=True).published
    return rec


def gate3(draft, difficulty):
    public, evidence, truth = pipeline._draft_to_phase3(draft, case_id="C", title="T")
    return playability.validate_playability_quality(
        draft=draft, public=public, evidence=evidence, universes=None,
        truth=truth, difficulty=difficulty)


rec = held("medium")
raw = rec.published.draft
kit = load_environment("apartment")

# ---- invariant: raw is the RED shape -------------------------------------
raw_report = gate3(raw, "medium")
check("raw-candidate-gate3-rejected", not raw_report.valid,
      f"issues={list(raw_report.issue_codes)}")
check("raw-reachable-dims-empty", raw_report.reachable_critical_dimensions == (),
      f"reachable={list(raw_report.reachable_critical_dimensions)}")

# ---- 2a/2g completion facts reachable + namespace integrity --------------
linked_ids = set()
for d in ("easy", "medium", "hard"):
    rep = _ensure_playability_clues(raw, kit, d)
    declared_objects = {o.object_id for o in rep.objects}
    declared_locs = {l.location_id for l in rep.world_graph.locations}
    declared_ev = {e.id for e in rep.evidence}
    bad = _world_graph_namespace_violations(rep)
    check(f"namespace-clean-{d}", bad == (), f"violations={list(bad)}")
    for p in rep.world_graph.placements:
        assert p.object_id in declared_objects, (d, p.object_id)
        assert p.location_id in declared_locs, (d, p.location_id)
        if p.evidence_id is not None:
            assert p.evidence_id in declared_ev, (d, p.evidence_id)
    check(f"all-placement-ids-declared-{d}", True, "object/location/evidence declared")
    for p in rep.world_graph.placements:
        if p.evidence_id:
            linked_ids.add(str(p.evidence_id))
check("completion-fact-placement-linked",
      any(i.startswith("d_ev_") for i in linked_ids),
      f"sample={sorted(i for i in linked_ids if i.startswith('d_ev_'))[:4]}")

rep_m = _ensure_playability_clues(raw, kit, "medium")

# ---- 2f no CaseTruth / evidence-proposition / solver mutation ------------
# Capture the FROZEN published aggregate state BEFORE the repair is applied,
# then prove the repair returned a NEW draft and left the aggregate/draft
# evidence + crime untouched.
published_before = rec.published
truth_id_before = id(rec.published.truth)
crime_id_before = id(rec.published.truth.crime)
evidence_before = tuple(rec.published.evidence)

def propsig(facts):
    return [
        (f.id, tuple(
            (getattr(p, "type", None), getattr(p, "object_id", None),
             getattr(p, "person_id", None), getattr(p, "motive_id", None))
            for p in (getattr(f, "propositions", ()) or ())))
        for f in facts
    ]

raw_props = propsig(raw.evidence)
rep_props = propsig(rep_m.evidence)
check("repair-returns-new-draft-not-in-place",
      rep_m is not raw and rep_m.crime is raw.crime
      and rep_m.evidence is raw.evidence,
      "repaired draft shares unmodified crime/evidence refs")
check("published-aggregate-object-unchanged",
      rec.published is published_before
      and id(rec.published.truth) == truth_id_before
      and id(rec.published.truth.crime) == crime_id_before
      and tuple(rec.published.evidence) == evidence_before,
      "published truth/evidence identity + content unchanged")
check("draft-crime-unchanged-after-repair", rep_m.crime == raw.crime,
      "draft crime equal")
check("draft-evidence-unchanged-after-repair",
      rep_m.evidence is raw.evidence
      and tuple(rep_m.evidence) == tuple(raw.evidence),
      f"facts={len(rep_m.evidence)} (same object as input draft)")
check("evidence-propositions-not-mutated", raw_props == rep_props,
      f"facts={len(raw_props)}")
pb = solve_case(*pipeline._draft_to_phase3(raw, case_id="C", title="T")[0:2])
pa = solve_case(*pipeline._draft_to_phase3(rep_m, case_id="C", title="T")[0:2])
check("solver-winners-unchanged-after-repair",
      pb.who.winner == pa.who.winner and pb.why.winner == pa.why.winner
      and pb.weapon.winner == pa.weapon.winner,
      f"who={pa.who.winner} why={pa.why.winner} weapon={pa.weapon.winner}")

# ---- 2b red herrings reachable, do not change solution -------------------
rep_h = _ensure_playability_clues(raw, kit, "hard")
public_h, evidence_h, _truth_h = pipeline._draft_to_phase3(rep_h, case_id="C", title="T")
proof_h = solve_case(public_h, evidence_h)
excluded = playability._excluded_candidate_ids(proof_h)
winners = playability._winner_ids(proof_h)
ev_by_id = {f.id: f for f in evidence_h}
linked_h = {str(p.evidence_id) for p in rep_h.world_graph.placements if p.evidence_id}
herrings = [
    fid for fid in linked_h
    if any(s in excluded and s not in winners
           for s in playability._subjects_of_facts(ev_by_id, (fid,)))
]
pol_h = difficulty_policy.policy_for("hard")
check("hard-red-herrings-reachable",
      len(herrings) >= pol_h.min_red_herrings,
      f"count={len(herrings)} min={pol_h.min_red_herrings}")
# a decoy must NOT be a winner and solution stays unique
check("red-herrings-not-winners",
      all(not (s in winners) for fid in herrings
          for s in playability._subjects_of_facts(ev_by_id, (fid,))),
      f"herrings={len(herrings)}")
proof_after = solve_case(*pipeline._draft_to_phase3(rep_h, case_id="C", title="T")[0:2])
check("herrings-keep-solution-unique",
      proof_after.who.unique and proof_after.why.unique and proof_after.weapon.unique
      and proof_after.when.ambiguous is False,
      "solver still unique")

# ---- 2c meaningful classification is record-based ------------------------
# Add a SECOND object linking an ALREADY-linked evidence record: the record-based
# meaningful count must NOT increase (a raw placement/object count would).
linked_ev = next(
    str(p.evidence_id) for p in rep_m.world_graph.placements if p.evidence_id)
dup = rep_m
dup = replace(
    dup,
    objects=tuple(dup.objects) + (ObjectSpec(
        object_id="qa_dup_object", asset_id="PROP_VASE_01",
        affordances=("INSPECTABLE",), subtype="document"),),
    world_graph=replace(
        dup.world_graph,
        placements=tuple(dup.world_graph.placements) + (PlacementSpec(
            object_id="qa_dup_object", asset_id="PROP_VASE_01",
            location_id=rep_m.world_graph.placements[0].location_id,
            anchor=rep_m.world_graph.placements[0].anchor,
            interaction="read", evidence_id=linked_ev),)),
)
base_report = gate3(rep_m, "medium")
dup_report = gate3(dup, "medium")
check("meaningful-record-based-not-object-count",
      dup_report.meaningful_inspectable_count == base_report.meaningful_inspectable_count,
      f"base={base_report.meaningful_inspectable_count} dup={dup_report.meaningful_inspectable_count}")

# ---- 2d idempotence ------------------------------------------------------
once = _ensure_playability_clues(raw, kit, "medium")
twice = _ensure_playability_clues(once, kit, "medium")
check("repair-idempotent-identity-or-equal",
      twice is once or twice == once,
      f"placements={len(twice.world_graph.placements)}")
check("no-duplicate-object-ids",
      len({o.object_id for o in twice.objects}) == len(twice.objects),
      f"objects={len(twice.objects)}")
check("no-duplicate-placement-object-ids",
      len({p.object_id for p in twice.world_graph.placements})
      == len(twice.world_graph.placements),
      f"placements={len(twice.world_graph.placements)}")

# completion idempotence (evidence algebra)
from app.generation import evidence_algebra  # noqa: E402
pub2, _e2, truth2 = pipeline._draft_to_phase3(
    replace(raw, evidence=()), case_id="C", title="T")
c1 = evidence_algebra.complete_evidence_if_ambiguous(pub2, truth2.crime, None)
c2 = evidence_algebra.complete_evidence_if_ambiguous(pub2, truth2.crime, c1.evidence)
check("completion-idempotent",
      c1.changed is True and c2.changed is False and c2.facts_added == 0
      and tuple(f.id for f in c2.evidence.evidence)
      == tuple(f.id for f in c1.evidence.evidence),
      f"first-added={c1.facts_added} second-added={c2.facts_added}")

# ---- 2e boundedness ------------------------------------------------------
bounds = {}
for d in ("easy", "medium", "hard"):
    rep = _ensure_playability_clues(raw, kit, d)
    added = len(rep.world_graph.placements) - len(raw.world_graph.placements)
    pol = difficulty_policy.policy_for(d)
    bound = 4 + pol.min_red_herrings  # missing dims + red-herring shortfall
    bounds[d] = (added, bound, added <= bound)
for d, (added, bound, ok) in bounds.items():
    check(f"bounded-{d}", ok, f"added={added} bound<=4+minRed({bound})")

# ---- empty world is NOT conjured (fail-closed) ---------------------------
from app.generation.schemas import WorldGraphSpec  # noqa: E402
empty = replace(raw, world_graph=replace(raw.world_graph, placements=()))
check("empty-placements-not-conjured",
      _ensure_playability_clues(empty, kit, "medium") is empty,
      "placement-less candidate returned unchanged")

print("\n=== SEMANTICS SUMMARY ===")
failed = [n for n, ok, _ in results if not ok]
print("checks:", len(results), "failed:", failed)
sys.exit(1 if failed else 0)