"""Phase 44 ADV — strongest reachability/meaningfulness bypass + publication path.

Attack: remove the evidence LINK from every interactive placement (so NO
evidence is discoverable through any object interaction), yet keep the objects
(which are still 'meaningful' only because evidence facts mention them). Gate 3's
entity-surface approximation should still declare every dimension reachable.
"""
from _bootstrap import (  # noqa: F401
    evaluate, show, replace, playability, pipeline, difficulty_policy,
    GenerationStage, GeneratedDraft, WorldGraphSpec, PlacementSpec, ObjectSpec,
    EvidenceSpec, PropSpec, EvidenceSetSpec, PublicWorldSpec, derive_universes,
    t44, t44p,
)
import json


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


# --------------------------------------------------------------------------- #
hr("A) golden draft: strip evidence_id from EVERY placement (no discoverable link)")
d = t44.golden_draft()
stripped = tuple(replace(p, evidence_id=None) for p in d.world_graph.placements)
d = replace(d, world_graph=replace(d.world_graph, placements=stripped))
print("placements with evidence_id:", sum(1 for p in d.world_graph.placements if p.evidence_id))
show("A medium", evaluate(d, "medium"))
show("A easy", evaluate(d, "easy"))

# Does Gate 1/2 still pass (so publication is reachable)?
attempt = pipeline.AttemptRecord(attempt_id="A", case_id="C", session_id="S")
attempt.draft = d
rep = pipeline.validate_draft(attempt)
print("Gate1/2 validate_draft outcome:", rep.outcome)
print("  structural:", rep.structural_issues)
print("  safety:", rep.safety_issues)
print("  universe:", rep.universe_issues)
print("  quality:", rep.quality_issues)


# --------------------------------------------------------------------------- #
hr("B) end-to-end REAL controller: golden world graph with every evidence_id removed")
def stripped_script():
    wg = json.loads(t44p._G[GenerationStage.WORLD_GRAPH])
    for pl in wg["worldGraph"]["placements"]:
        pl.pop("evidenceId", None)
    script = t44p._golden_script()
    script[GenerationStage.WORLD_GRAPH] = [json.dumps(wg)]
    return script


controller, handle, record = t44p._run(stripped_script(), "medium")
print("stage_done:", sorted(s.value for s in record.stage_done))
print("WORLD_GRAPH trusted:", GenerationStage.WORLD_GRAPH in record.stage_done)
rep = pipeline.validate_draft(record)
print("Gate1/2 outcome:", rep.outcome)
result = controller.publish(handle.attempt_id, hold_ok=True)
print("controller.publish success:", result.success)
record.published = result.published
stub = t44p._make_guard_stub()
status = t44p._publish_via_guard(stub, record, "medium")
print("guard status:", status, "| publish calls:", stub._publication.calls)
show("B gate3 over frozen candidate", playability.validate_published_candidate(record.published, "medium"))
print("evidence linked to any placement:",
      sorted({p.evidence_id for p in record.published.draft.world_graph.placements if p.evidence_id}))


# --------------------------------------------------------------------------- #
hr("C) minimal: only 'weapon-affordance' objects placed, ZERO evidence-linked placements")
from app.domain.solver import solve_case
d = t44.golden_draft()
stripped = tuple(replace(p, evidence_id=None) for p in d.world_graph.placements)
# also drop the objects that are only meaningful via evidence reference:
d = replace(d, world_graph=replace(d.world_graph, placements=stripped))
pub, ev, truth = t44.phase3(d)
proof = solve_case(pub, ev)
print("solver winners:", proof.who.winner, proof.why.winner, proof.weapon.winner)
print("solver evidence_ids_used:", proof.evidence_ids_used)
