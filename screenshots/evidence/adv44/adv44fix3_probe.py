"""Phase 44 FINAL re-review (fix round 3) - in-process Gate 3 re-attack.

Hermetic, ZERO network. Replicates ADV-44-07 and attempts NEW decoy variants
(WHO / WEAPON / WHEN / mixed / supplementation binding a decoy) against the
CURRENT playability + _ensure_dimension_clues code.
"""
import sys
from pathlib import Path
from dataclasses import replace

REPO = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
for p in (str(REPO / "backend"), str(REPO / "backend" / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.generation import playability, pipeline  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    EvidenceSpec,
    ObjectSpec,
    PlacementSpec,
    PropSpec,
    WorldGraphSpec,
)
from app.domain.solver import solve_case  # noqa: E402
from app.environments.manifests import load_environment  # noqa: E402
from app.services.generation import _ensure_dimension_clues, _winning_fact_for_dimension  # noqa: E402

import test_phase44_playability_gate as t44  # noqa: E402

G = t44.golden_draft
EV = t44.evaluate
PHASE3 = t44.phase3
SCENE = t44.SCENE_LOCATION


def hr(t):
    print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)


def show(label, r):
    print(
        f"[{label}] valid={r.valid} insp={r.inspectable_count} "
        f"mean={r.meaningful_inspectable_count} src={r.distinct_meaningful_source_count} "
        f"rh={r.red_herring_count} reach={r.reachable_critical_dimensions} "
        f"unreach={r.unreachable_critical_dimensions} issues={r.issue_codes}"
    )


def relink(draft, object_id, evidence_id):
    pls = tuple(
        replace(p, evidence_id=evidence_id) if p.object_id == object_id else p
        for p in draft.world_graph.placements
    )
    return replace(draft, world_graph=replace(draft.world_graph, placements=pls))


def reachable_ids(draft):
    return {
        str(p.evidence_id)
        for p in draft.world_graph.placements
        if p.interaction and p.evidence_id is not None
    }


def proof_for(draft):
    pub, ev, truth = PHASE3(draft)
    return solve_case(pub, ev), pub, ev, truth


# --------------------------------------------------------------------------- #
hr("PART A - Replicate ADV-44-07 through in-process Gate 3")
# --------------------------------------------------------------------------- #

base = G()
p, pub, ev, truth = proof_for(base)
print("A0 golden proof winners: who=%s why=%s weapon=%s crit=%s" % (
    p.who.winner, p.why.winner, p.weapon.winner, p.when.critical_evidence_ids))
print("A0 golden reachable ids:", sorted(reachable_ids(base)))
t44.show = show
show("A0 golden/medium", EV(base, "medium"))

# A1: winning WHY unlinked, decoy WHY linked (the exact ADV-44-07 attack).
d = relink(G(), "motive_audit_file", "motive_no_affair_01")
print("A1 winning-WHY reachable:", "motive_audit_01" in reachable_ids(d),
      "| decoy reachable:", "motive_no_affair_01" in reachable_ids(d))
show("A1 decoy-WHY/medium", EV(d, "medium"))

# A2: winning WHY unlinked entirely (no decoy).
d2 = relink(G(), "motive_audit_file", None)
show("A2 stripped-WHY/medium", EV(d2, "medium"))

# --------------------------------------------------------------------------- #
hr("PART B - NEW decoy variants")
# --------------------------------------------------------------------------- #

# B1: decoy WHO (wrong-suspect observation reachable, winner unreachable).
d = relink(G(), "motive_audit_file", "cctv_michael_office_01")
reach = reachable_ids(d)
print("B1 winner thomas reachable by any fact:",
      any(str(getattr(pr, "person_id", "")) == "thomas_reed"
          for f in ev if f.id in reach
          for pr in f.propositions))
show("B1 decoy-WHO/medium", EV(d, "medium"))

# B1b: decoy WHO via a reachable wrong-suspect cctv, winner WHY support kept.
d = G()
d = t44._add_inspectable(d, "decoy_who_obj", evidence_id="cctv_michael_office_01",
                         anchor="shelf_01")
show("B1b decoy-WHO-plus-winner/medium", EV(d, "medium"))

# B2: decoy WEAPON - negative match on a NON-winner reachable, winner positive
# unreachable.
d = relink(G(), "kitchen_knife", "forensic_letter_opener_01")
show("B2 decoy-WEAPON(non-winner)/medium", EV(d, "medium"))

# B2b: negative forensic match ON THE WINNER object reachable, positive absent.
neg = EvidenceSpec(
    id="forensic_knife_match_false_01",
    kind="forensic",
    propositions=(PropSpec(type="FORENSIC_WEAPON_MATCH", object_id="kitchen_knife",
                           structured={"match": False}),),
)
d = replace(G(), evidence=tuple(G().evidence) + (neg,))
d = relink(d, "kitchen_knife", "forensic_knife_match_false_01")
show("B2b decoy-WEAPON(neg-on-winner)/medium", EV(d, "medium"))

# B3: decoy WHEN - non-critical time fact reachable, critical unreachable.
d = relink(G(), "victim_body_placeholder", "alibi_claim_thomas_01")
print("B3 crit ids:", proof_for(d)[0].when.critical_evidence_ids)
print("B3 reachable:", sorted(reachable_ids(d)))
show("B3 decoy-WHEN/medium", EV(d, "medium"))

# B3b: EMPTY critical set + a reachable non-critical WHO/WHEN-type fact.
time_ids = {"last_seen_01", "body_found_01", "noise_heard_01"}
ev_minus = tuple(f for f in G().evidence if f.id not in time_ids)
d = replace(G(), evidence=ev_minus)
d = relink(d, "victim_body_placeholder", "alibi_claim_thomas_01")
try:
    pr = proof_for(d)[0]
    print("B3b crit ids (empty expected?):", pr.when.critical_evidence_ids,
          "ambiguous:", pr.when.ambiguous, "overconstrained:", pr.when.overconstrained)
except Exception as exc:  # noqa: BLE001
    print("B3b proof raised:", type(exc).__name__, exc)
try:
    show("B3b empty-crit+alibi/medium", EV(d, "medium"))
except Exception as exc:  # noqa: BLE001
    print("B3b evaluate raised:", type(exc).__name__, exc)

# B4: mixed decoys across ALL dimensions.
d = G()
d = relink(d, "motive_audit_file", "motive_no_affair_01")
d = relink(d, "kitchen_knife", "forensic_letter_opener_01")
d = relink(d, "victim_body_placeholder", "alibi_claim_thomas_01")
show("B4 mixed-decoys/medium", EV(d, "medium"))

# --------------------------------------------------------------------------- #
hr("PART C - supplementation binding (must bind the WINNER, never a decoy)")
# --------------------------------------------------------------------------- #

kit = load_environment("apartment")

d = relink(G(), "motive_audit_file", "motive_no_affair_01")
out = _ensure_dimension_clues(d, kit)
added = [
    (str(p.object_id), str(p.evidence_id), str(p.location_id))
    for p in out.world_graph.placements
    if str(getattr(p, "object_id", "")).startswith("dim_clue_")
]
print("C1 supplement added:", added)
print("C1 binds winner motive_audit_01:", any(e == "motive_audit_01" for _, e, _ in added))
print("C1 binds decoy motive_no_affair_01:", any(e == "motive_no_affair_01" for _, e, _ in added))
show("C1 supplemented/medium", EV(out, "medium"))

d = relink(G(), "kitchen_knife", "forensic_letter_opener_01")
out = _ensure_dimension_clues(d, kit)
added = [
    (str(p.object_id), str(p.evidence_id))
    for p in out.world_graph.placements
    if str(getattr(p, "object_id", "")).startswith("dim_clue_")
]
print("C2 supplement added:", added)
print("C2 binds winner positive forensic_knife_match_01:",
      any(e == "forensic_knife_match_01" for _, e in added))
show("C2 supplemented/medium", EV(out, "medium"))

d = relink(G(), "victim_body_placeholder", "alibi_claim_thomas_01")
out = _ensure_dimension_clues(d, kit)
added = [
    (str(p.object_id), str(p.evidence_id))
    for p in out.world_graph.placements
    if str(getattr(p, "object_id", "")).startswith("dim_clue_")
]
print("C3 supplement added:", added)
show("C3 supplemented/medium", EV(out, "medium"))

crime = G().crime
for dim in ("WHO", "WHY", "WEAPON", "WHEN"):
    f = _winning_fact_for_dimension(dim, G().evidence, crime)
    print(f"C4 {dim}: winning fact -> {getattr(f, 'id', None)}")

# --------------------------------------------------------------------------- #
hr("PART D - ADV-44-01 / ADV-44-03 re-confirm")
# --------------------------------------------------------------------------- #

d = replace(G(), world_graph=replace(
    G().world_graph,
    placements=tuple(replace(p, evidence_id=None, interaction="inspect")
                     for p in G().world_graph.placements)))
show("D1 all-links-stripped/medium", EV(d, "medium"))

d = G()
extra_objs, extra_pls = [], []
for i in range(3):
    oid = f"share_obj_{i}"
    extra_objs.append(ObjectSpec(object_id=oid, asset_id="PROP_VASE_01",
                                 affordances=("INSPECTABLE",)))
    extra_pls.append(PlacementSpec(object_id=oid, asset_id="PROP_VASE_01",
                                   location_id=SCENE, anchor="shelf_01",
                                   interaction="read",
                                   evidence_id="forensic_knife_match_01"))
d2 = replace(d, objects=tuple(d.objects) + tuple(extra_objs),
             world_graph=replace(d.world_graph,
                                 placements=tuple(d.world_graph.placements) + tuple(extra_pls)))
a = EV(d, "medium")
b = EV(d2, "medium")
print(f"D2 base mean={a.meaningful_inspectable_count} src={a.distinct_meaningful_source_count}; "
      f"+3 share mean={b.meaningful_inspectable_count} src={b.distinct_meaningful_source_count} "
      f"-> inflation mean={b.meaningful_inspectable_count - a.meaningful_inspectable_count} "
      f"src={b.distinct_meaningful_source_count - a.distinct_meaningful_source_count}")

# --------------------------------------------------------------------------- #
hr("PART E - _ensure_dimension_clues abuse re-attack")
# --------------------------------------------------------------------------- #

d = relink(G(), "motive_audit_file", None)
o1 = _ensure_dimension_clues(d, kit)
o2 = _ensure_dimension_clues(d, kit)
print("E1 deterministic:", o1 == o2)
ids = [o.object_id for o in o1.objects]
print("E2 object ids unique:", len(ids) == len(set(ids)))
declared = {l.location_id for l in o1.world_graph.locations}
placed = {p.location_id for p in o1.world_graph.placements}
print("E3 undeclared placement locations:", sorted(placed - declared))
from app.generation import safety as _safety  # noqa: E402
issues = _safety.validate_world_graph(
    o1.world_graph, {o.object_id for o in o1.objects}, {e.id for e in o1.evidence})
print("E4 safety issues:", issues)
print("E5 evidence count unchanged:", len(o1.evidence) == len(d.evidence))
added_links = {str(p.evidence_id) for p in o1.world_graph.placements if p.evidence_id} - \
              {str(p.evidence_id) for p in d.world_graph.placements if p.evidence_id}
print("E6 added links all resolve to existing facts:",
      added_links <= {e.id for e in o1.evidence}, "|", added_links)
added_objs = {o.object_id for o in o1.objects} - {o.object_id for o in d.objects}
leak = []
for o in o1.objects:
    if o.object_id in added_objs:
        for tok in ("thomas_reed", "cover_up_embezzlement", "kitchen_knife"):
            if tok in repr(o):
                leak.append((o.object_id, tok))
print("E7 added-object truth-token hits:", leak)
print("E8 no-op for golden:", _ensure_dimension_clues(G(), kit) == G())
print("E9 added placements:", len(o1.world_graph.placements) - len(d.world_graph.placements))

print("\nDONE")
