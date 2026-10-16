"""Phase 44 fix-round re-review probe (hermetic, zero network).

Covers:
  PART 1 - ADV-44-01 variants (reachability bypass attempts)
  PART 2 - ADV-44-03 record-based accounting (inflation attempts)
  PART 3 - NEW attacks on services.generation._ensure_dimension_clues
  PART 4 - Gate 3 semantic gap (decoy covers a winning dimension)
"""
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
for p in (str(REPO / "backend"), str(REPO / "backend" / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from dataclasses import replace  # noqa: E402

from app.generation import playability, pipeline, safety  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    EvidenceSpec,
    ObjectSpec,
    PlacementSpec,
    PropSpec,
    WorldGraphSpec,
    GeneratedDraft,
)
from app.domain.solver import solve_case  # noqa: E402
from app.environments.manifests import load_environment  # noqa: E402
from app.services.generation import _ensure_dimension_clues  # noqa: E402

import test_phase44_playability_gate as t44  # noqa: E402

G = t44.golden_draft
EV = t44.evaluate
PHASE3 = t44.phase3
SCENE = t44.SCENE_LOCATION


def hr(t):
    print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)


def show(label, r):
    print(f"[{label}] valid={r.valid} insp={r.inspectable_count} mean={r.meaningful_inspectable_count} "
          f"src={r.distinct_meaningful_source_count} rh={r.red_herring_count} "
          f"reach={r.reachable_critical_dimensions} unreach={r.unreachable_critical_dimensions} "
          f"issues={r.issue_codes}")


t44.show = show


def strip_evidence(draft):
    return replace(
        draft,
        world_graph=replace(
            draft.world_graph,
            placements=tuple(replace(p, evidence_id=None) for p in draft.world_graph.placements),
        ),
    )


# --------------------------------------------------------------------------- #
hr("PART 1 - ADV-44-01 variants")
# --------------------------------------------------------------------------- #

d = strip_evidence(G())
t44.show("P1.A all-stripped/gate3", EV(d, "medium"))

d = G()
pls = tuple(
    replace(p, evidence_id=None) if p.object_id == "motive_audit_file" else p
    for p in d.world_graph.placements
)
d = replace(d, world_graph=replace(d.world_graph, placements=pls))
t44.show("P1.B winning-WHY-stripped/gate3", EV(d, "medium"))

base = t44.easy_at_min()
for i in range(6):
    base = t44._add_inspectable(base, f"inert_{i}", interaction="inspect", anchor="shelf_01")
t44.show("P1.C +inert-interactives/gate3(easy)", EV(base, "easy"))

d = strip_evidence(G())
t44.show("P1.D witness-only/gate3", EV(d, "medium"))

d = replace(G(), world_graph=WorldGraphSpec())
t44.show("P1.E empty-world/gate3", EV(d, "medium"))

# --------------------------------------------------------------------------- #
hr("PART 2 - ADV-44-03 record-based accounting")
# --------------------------------------------------------------------------- #

d = G()
extra_objs = []
extra_pls = []
for i in range(3):
    oid = f"share_obj_{i}"
    extra_objs.append(ObjectSpec(object_id=oid, asset_id="PROP_VASE_01", affordances=("INSPECTABLE",)))
    extra_pls.append(
        PlacementSpec(
            object_id=oid, asset_id="PROP_VASE_01", location_id=SCENE,
            anchor="shelf_01", interaction="read",
            evidence_id="forensic_knife_match_01",
        )
    )
d2 = replace(
    d,
    objects=tuple(d.objects) + tuple(extra_objs),
    world_graph=replace(d.world_graph, placements=tuple(d.world_graph.placements) + tuple(extra_pls)),
)
a = EV(d, "medium")
b = EV(d2, "medium")
print(f"P2a base      insp={a.inspectable_count} mean={a.meaningful_inspectable_count} src={a.distinct_meaningful_source_count} rh={a.red_herring_count}")
print(f"P2a +3 share  insp={b.inspectable_count} mean={b.meaningful_inspectable_count} src={b.distinct_meaningful_source_count} rh={b.red_herring_count}")
print(f"P2a meaningful inflation = {b.meaningful_inspectable_count - a.meaningful_inspectable_count} (EXPECT 0)")

extra_ev = (
    EvidenceSpec(id="extra_fact_x", kind="object", propositions=(PropSpec(type="OTHER", object_id="kitchen_knife"),)),
)
pl = list(d.world_graph.placements)
pl.append(
    PlacementSpec(
        object_id="kitchen_knife", asset_id="PROP_KITCHEN_KNIFE_01", location_id=SCENE,
        anchor="kitchen_counter", interaction="inspect", evidence_id="extra_fact_x",
    )
)
d3 = replace(d, evidence=tuple(d.evidence) + extra_ev, world_graph=replace(d.world_graph, placements=tuple(pl)))
c = EV(d3, "medium")
print(f"P2b +1 distinct rec on existing obj  mean={c.meaningful_inspectable_count} src={c.distinct_meaningful_source_count}")

# --------------------------------------------------------------------------- #
hr("PART 3 - NEW attack: _ensure_dimension_clues")
# --------------------------------------------------------------------------- #

kit = load_environment("apartment")

d = strip_evidence(G())
out = _ensure_dimension_clues(d, kit)
added_objs = [o.object_id for o in out.objects if o.object_id not in {x.object_id for x in d.objects}]
added_pls = [
    (p.object_id, p.anchor, p.interaction, p.evidence_id)
    for p in out.world_graph.placements
    if p not in d.world_graph.placements
]
print("T1 added objects:", added_objs)
print("T1 added placements:", added_pls)
t44.show("T1 supplemented/gate3", EV(out, "medium"))
print("T1 no-op for golden:", _ensure_dimension_clues(G(), kit) == G())

o1 = _ensure_dimension_clues(strip_evidence(G()), kit)
o2 = _ensure_dimension_clues(strip_evidence(G()), kit)
print("T2 deterministic:", o1 == o2)

d = strip_evidence(G())
d = replace(d, objects=tuple(d.objects) + (
    ObjectSpec(object_id="dim_clue_why", asset_id="PROP_VASE_01", affordances=("INSPECTABLE",)),
))
out = _ensure_dimension_clues(d, kit)
ids = [o.object_id for o in out.objects]
print("T3 object ids unique:", len(ids) == len(set(ids)), "| dim_clue ids:", [i for i in ids if i.startswith("dim_clue")])

out = _ensure_dimension_clues(strip_evidence(G()), kit)
n_added = len(out.world_graph.placements) - len(G().world_graph.placements)
print("T4 placements added:", n_added, "(<=4 expected)")

anchors = [p.anchor for p in out.world_graph.placements if p.interaction in safety.INTERACTION_ALLOWLIST]
print("T5 interactive anchors unique:", len(anchors) == len(set(anchors)), "| anchors:", anchors)

truth_tokens = ("thomas_reed", "sarah_miller", "cover_up_embezzlement", "kitchen_knife", "miller_apartment_kitchen")
leak = []
for o in out.objects:
    s = repr(o)
    for tok in truth_tokens:
        if tok in s:
            leak.append(("object", o.object_id, tok))
for p in out.world_graph.placements:
    s = repr(p)
    for tok in truth_tokens:
        if tok in s:
            leak.append(("placement", p.object_id, tok))
print("T6 added-value truth-token hits:", leak[:6])

all_fact_ids = {e.id for e in out.evidence}
added_ev = {str(p.evidence_id) for p in out.world_graph.placements if p.evidence_id} - {str(p.evidence_id) for p in d.world_graph.placements if p.evidence_id}
print("T7 added links resolve to existing facts:", added_ev <= all_fact_ids, "|", added_ev)
print("T7 evidence count unchanged:", len(out.evidence) == len(d.evidence))

# --------------------------------------------------------------------------- #
hr("PART 4 - Gate 3 semantic gap: decoy covers a winning dimension")
# --------------------------------------------------------------------------- #

d = G()
pls = [p for p in d.world_graph.placements if p.object_id != "motive_audit_file"]
pls.append(
    PlacementSpec(
        object_id="decoy_motive_obj", asset_id="PROP_VASE_01", location_id=SCENE,
        anchor="shelf_01", interaction="read", evidence_id="motive_no_affair_01",
    )
)
objs = tuple(d.objects) + (
    ObjectSpec(object_id="decoy_motive_obj", asset_id="PROP_VASE_01", affordances=("INSPECTABLE",)),
)
d = replace(d, objects=objs, world_graph=replace(d.world_graph, placements=tuple(pls)))
pub, ev, truth = PHASE3(d)
reached = {str(getattr(p, "evidence_id")) for p in d.world_graph.placements if getattr(p, "evidence_id", None)}
proof = solve_case(pub, ev)
print("P4 winning WHY fact 'motive_audit_01' reachable:", "motive_audit_01" in reached)
print("P4 decoy 'motive_no_affair_01' reachable:", "motive_no_affair_01" in reached)
print("P4 proof why winner:", proof.why.winner)
print("P4 proof evidence_ids_used includes winning WHY:", "motive_audit_01" in proof.evidence_ids_used)
print("P4 proof intersect reachable nonempty:", bool(set(proof.evidence_ids_used) & reached))
print("P4 proof reachable fact ids:", sorted(set(proof.evidence_ids_used) & reached))
t44.show("P4 decoy-covers-WHY/gate3", EV(d, "medium"))

# --------------------------------------------------------------------------- #
hr("PART 5 - _ensure_dimension_clues location-id integrity (legacy path)")
# --------------------------------------------------------------------------- #

d = strip_evidence(G())
out = _ensure_dimension_clues(d, kit)
declared = {l.location_id for l in out.world_graph.locations}
placed_locs = {p.location_id for p in out.world_graph.placements}
print("P5 declared locations:", sorted(declared))
print("P5 placement location_ids:", sorted(placed_locs))
print("P5 UNDECLARED placement locations:", sorted(placed_locs - declared))

object_ids = {o.object_id for o in out.objects}
evidence_ids = {e.id for e in out.evidence}
issues = safety.validate_world_graph(out.world_graph, object_ids, evidence_ids)
print("P5 safety issues on supplemented legacy graph:", issues)

# --------------------------------------------------------------------------- #
hr("PART 6 - boundedness / abuse ceiling")
# --------------------------------------------------------------------------- #

d = strip_evidence(G())
kit_anchors = [a for a in kit.anchors if not getattr(a, "exclusive", False)]
print("P6 free kit anchors:", [a.anchor_id for a in kit_anchors])

