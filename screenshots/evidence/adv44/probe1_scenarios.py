"""Phase 44 ADV — §36 scenarios 1-10 + §38 Q1/Q4.

Hermetic. Uses the real GenerationController + real _publish_if_ready guard
(stubbed store/publication) with FakeProvider scripts, plus the Gate 3 validator.
"""
from _bootstrap import (  # noqa: F401
    evaluate, show, replace, playability, pipeline, difficulty_policy,
    GenerationStage, GeneratedDraft, WorldGraphSpec, PlacementSpec, ObjectSpec,
    EvidenceSpec, PropSpec, EvidenceSetSpec, PublicWorldSpec, derive_universes,
    t44, t44p,
)

G = t44p._G


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


# --------------------------------------------------------------------------- #
hr("SCENARIO 1 — empty placements via REAL controller + REAL publication guard")
controller, handle, record = t44p._run(t44p._empty_world_script(), "medium")
print("stage_done:", sorted(s.value for s in record.stage_done))
print("WORLD_GRAPH trusted:", GenerationStage.WORLD_GRAPH in record.stage_done)
rep = pipeline.validate_draft(record)
print("Gate1/2 validate_draft outcome:", rep.outcome)
print("state:", record.state)
result = controller.publish(handle.attempt_id, hold_ok=True)
print("controller.publish success:", result.success)
record.published = result.published
stub = t44p._make_guard_stub()
status = t44p._publish_via_guard(stub, record, "medium")
print("guard status:", status, "| publish_transactionally calls:", stub._publication.calls)
print("final state:", record.state)
show("S1 gate3", playability.validate_published_candidate(record.published, "medium"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 2 — one decorative object only")
d = t44.golden_draft()
d = t44._drop_objects(d, {"letter_opener", "scissors", "apartment_laptop",
                          "victim_body_placeholder"})
d = replace(d, world_graph=replace(d.world_graph,
    placements=tuple(replace(p, interaction="") for p in d.world_graph.placements)))
show("S2 easy", evaluate(d, "easy"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 3 — many decorative objects, zero meaningful clues")
d = t44.easy_at_min()
for i in range(8):
    d = t44._add_inspectable(d, f"deco_{i}", interaction="", anchor="shelf_01")
show("S3 easy", evaluate(d, "easy"))
show("S3 medium", evaluate(d, "medium"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 4 — many evidence records, one inspectable object")
d = t44.easy_at_min()
# Collapse to a single meaningful object: keep only kitchen_knife placement.
d = t44._drop_objects(d, {"apartment_laptop", "victim_body_placeholder"})
# Pad with non-meaningful interactive objects so inspectable min is met.
for i in range(4):
    d = t44._add_inspectable(d, f"plain_{i}", interaction="inspect")
show("S4 medium", evaluate(d, "medium"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 5 — all solver-critical evidence unreachable (no placements)")
d = replace(t44.golden_draft(), world_graph=WorldGraphSpec())
show("S5 easy", evaluate(d, "easy"))
print("NOTE: unreachable dims =", evaluate(d, "easy").unreachable_critical_dimensions,
      "(empty == reachability 'satisfied' via entity surface?)")


# --------------------------------------------------------------------------- #
hr("SCENARIO 5b — entity-surface bypass: remove knife placement, fact refs person")
d = t44.golden_draft()
placements = tuple(p for p in d.world_graph.placements if p.object_id != "kitchen_knife")
d = replace(d, world_graph=replace(d.world_graph, placements=placements))
show("S5b golden -knife placement", evaluate(d, "easy"))
# Now attach a person reference to the knife forensic fact so entity surface hits
knife_facts = []
for f in d.evidence:
    if f.id == "forensic_knife_match_01":
        props = tuple(f.propositions) + (PropSpec(type="ALIBI_TIME_CLAIM",
                  person_id="thomas_reed", observed_at="2026-09-11T22:16:00+02:00",
                  uncertainty_seconds=60),)
        f = replace(f, propositions=props)
    knife_facts.append(f)
d2 = replace(d, evidence=tuple(knife_facts))
show("S5b +person-ref on knife fact", evaluate(d2, "easy"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 6 — WHO reachable, WHY unreachable")
d = t44.golden_draft()
# Drop all motive-linked facts and all person-referencing facts' local context?
# Simpler: remove every fact whose propositions reference the motive id.
ev = tuple(
    f for f in d.evidence
    if not any(getattr(p, "motive_id", None) for p in f.propositions)
)
d = replace(d, evidence=ev)
show("S6 medium", evaluate(d, "medium"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 7 — weapon evidence exists but no weapon object/interactable clue")
d = t44.golden_draft()
# Remove the kitchen_knife placement entirely (weapon object not inspectable).
placements = tuple(p for p in d.world_graph.placements if p.object_id != "kitchen_knife")
d = replace(d, world_graph=replace(d.world_graph, placements=placements))
show("S7 easy", evaluate(d, "easy"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 8 — Medium with 2 meaningful inspectables")
show("S8 easy_at_min as medium", evaluate(t44.easy_at_min(), "medium"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 9 — Hard with Easy-level richness")
show("S9 golden as hard", evaluate(t44.golden_draft(), "hard"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 10 — fake red herring that is actually solver-critical")
# Take golden, verify the letter_opener (excluded weapon) counts as red herring.
show("S10 golden medium", evaluate(t44.golden_draft(), "medium"))
