"""Phase 44 ADV — edge cases: max guidance non-blocking, dedup, inert padding."""
from _bootstrap import (  # noqa: F401
    evaluate, show, replace, playability, pipeline, difficulty_policy,
    GenerationStage, GeneratedDraft, WorldGraphSpec, PlacementSpec, ObjectSpec,
    EvidenceSpec, PropSpec, EvidenceSetSpec, PublicWorldSpec, derive_universes,
    t44, t44p,
)


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


hr("E1 — MAX guidance never blocks publication (legitimately richer case)")
d = t44.hard_at_min()
for i in range(20):
    d = t44._add_inspectable(d, f"extra_{i}", interaction="inspect", anchor="shelf_01")
show("E1 hard + 20 extra inspectables", evaluate(d, "hard"))
show("E1 hard + 20 extra as easy", evaluate(d, "easy"))


hr("E2 — duplicate object_id placements counted ONCE (inspectable dedup)")
base = t44.golden_draft()
dup = tuple(base.world_graph.placements) + tuple(base.world_graph.placements)
d = replace(base, world_graph=replace(base.world_graph, placements=dup))
print("  placements:", len(dup), "| distinct interactive objects:")
print("  base insp:", evaluate(base, "medium").inspectable_count,
      "| dup insp:", evaluate(d, "medium").inspectable_count)


hr("E3 — inert interactive padding cannot satisfy meaningful minimum")
d = t44.easy_below_min()
for i in range(10):
    d = t44._add_inspectable(d, f"inert_{i}", interaction="inspect")
show("E3 easy (10 inert pad)", evaluate(d, "easy"))


hr("E4 — provider interaction token not in allowlist -> not an inspectable")
d = t44.golden_draft()
weird = tuple(replace(p, interaction="loot") for p in d.world_graph.placements)
d = replace(d, world_graph=replace(d.world_graph, placements=weird))
show("E4 non-allowlisted interactions", evaluate(d, "medium"))
attempt = pipeline.AttemptRecord(attempt_id="A", case_id="C", session_id="S")
attempt.draft = d
rep = pipeline.validate_draft(attempt)
print("  Gate1/2 outcome:", rep.outcome, "| safety:", rep.safety_issues[:2])


hr("E5 — same object exposed via 3D + accessibility path counts once")
# (The model has no separate 3D/accessibility placement duality; distinct
# object_id is the unit. Confirmed by _inspected_object_ids dedup.)
print("  dedup unit = distinct object_id of interactive placements (E2).")
