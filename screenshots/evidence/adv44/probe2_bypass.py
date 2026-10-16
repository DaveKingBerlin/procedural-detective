"""Phase 44 ADV — §36 scenarios 3 (correct), 5b, 6, 7, 10 + entity-surface bypass."""
from _bootstrap import (  # noqa: F401
    evaluate, show, replace, playability, pipeline, difficulty_policy,
    GenerationStage, GeneratedDraft, WorldGraphSpec, PlacementSpec, ObjectSpec,
    EvidenceSpec, PropSpec, EvidenceSetSpec, PublicWorldSpec, derive_universes,
    t44, t44p,
)
from app.domain.evidence import OTHER, PERSON_OBSERVED_AT_LOCATION


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def bare_object(d, oid, interaction="inspect"):
    spec = ObjectSpec(object_id=oid, asset_id="PROP_VASE_01", affordances=())
    placement = PlacementSpec(object_id=oid, asset_id="PROP_VASE_01",
                              location_id=t44.SCENE_LOCATION, anchor="shelf_01",
                              interaction=interaction, evidence_id=None)
    return replace(d, objects=tuple(d.objects) + (spec,),
                   world_graph=replace(d.world_graph,
                       placements=tuple(d.world_graph.placements) + (placement,)))


# --------------------------------------------------------------------------- #
hr("SCENARIO 3 (correct) — many decorative/inert interactive objects, ZERO meaningful")
base = t44.easy_at_min()
# strip ALL evidence so nothing can be 'meaningful' via linkage, and drop the
# weapon-affordance objects; keep only inert interactive objects.
d = t44._drop_objects(base, {"kitchen_knife", "apartment_laptop", "victim_body_placeholder"})
d = replace(d, evidence=())
for i in range(6):
    d = bare_object(d, f"inert_{i}", interaction="inspect")
show("S3 easy", evaluate(d, "easy"))
show("S3 medium", evaluate(d, "medium"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 5b — ENTITY-SURFACE BYPASS: solver-critical fact not placement-linked")
d = t44.golden_draft()
placements = tuple(p for p in d.world_graph.placements if p.object_id != "kitchen_knife")
d = replace(d, world_graph=replace(d.world_graph, placements=placements))
show("S5b-0 golden minus knife placement (baseline)", evaluate(d, "easy"))


def add_person_ref(draft, fact_id, person_id):
    out = []
    for f in draft.evidence:
        if f.id == fact_id:
            prop = PropSpec(type=PERSON_OBSERVED_AT_LOCATION, person_id=person_id,
                            location_id=t44.SCENE_LOCATION,
                            observed_at="2026-09-11T22:16:00+02:00",
                            uncertainty_seconds=60)
            f = replace(f, propositions=tuple(f.propositions) + (prop,))
        out.append(f)
    return replace(draft, evidence=tuple(out))


d2 = add_person_ref(d, "forensic_knife_match_01", "thomas_reed")
show("S5b-1 +person-ref on the unreachable knife fact", evaluate(d2, "easy"))

# Prove the object is still NOT placed (no interaction path to the knife)
placed = {p.object_id for p in d2.world_graph.placements}
print("kitchen_knife placed? ", "kitchen_knife" in placed)


# --------------------------------------------------------------------------- #
hr("SCENARIO 6 — WHO reachable, WHY unreachable")
d = t44.golden_draft()
ev = tuple(f for f in d.evidence
           if not any(getattr(p, "motive_id", None) for p in f.propositions))
d = replace(d, evidence=ev)
show("S6 medium", evaluate(d, "medium"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 7 — weapon evidence exists but no weapon object/interactable clue")
d = t44.golden_draft()
placements = tuple(p for p in d.world_graph.placements if p.object_id != "kitchen_knife")
d = replace(d, world_graph=replace(d.world_graph, placements=placements))
show("S7 easy", evaluate(d, "easy"))
show("S7 medium", evaluate(d, "medium"))


# --------------------------------------------------------------------------- #
hr("SCENARIO 10 — fake red herring actually solver-critical (winner object)")
# Make the winner weapon itself the 'excluded' candidate shape? Instead check the
# winner's object never counts. Count subjects of the knife-linked facts.
d = t44.golden_draft()
pub, ev, truth = t44.phase3(d)
from app.domain.solver import solve_case
proof = solve_case(pub, ev)
print("winners:", proof.who.winner, proof.why.winner, proof.weapon.winner)
print("evidence_ids_used:", proof.evidence_ids_used)
show("S10 medium", evaluate(d, "medium"))
