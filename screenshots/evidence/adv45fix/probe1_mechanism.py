"""Probe 1 — repair mechanics attacks (decoy binding, second solution,
namespace, determinism, idempotence, empty/unrepairable, coverage, leak).

Run:  python screenshots/evidence/adv45fix/probe1_mechanism.py
"""
from __future__ import annotations

import copy
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import (  # noqa: E402
    _ensure_playability_clues,
    _world_graph_namespace_violations,
    difficulty_policy,
    empty_placements_script,
    completion_script,
    gate3,
    golden_script,
    header,
    held,
    load_environment,
    object_tuple,
    phase3,
    placement_tuple,
    playability,
    repaired_draft,
    solve,
)


def main():
    header("PROBE 1 — mechanism attacks")

    # ------------------------------------------------------------------ #
    header("[A] reproduction shape: raw invalid -> repaired valid, solver unchanged")
    _c, rec = held(completion_script(), "medium")
    raw = rec.published.draft
    raw_g = gate3(raw, "medium")
    rep = repaired_draft(rec, "medium")
    rep_g = gate3(rep, "medium")
    print("raw.valid=", raw_g.valid, "unreach=", raw_g.unreachable_critical_dimensions,
          "rh=", raw_g.red_herring_count)
    print("rep.valid=", rep_g.valid, "unreach=", rep_g.unreachable_critical_dimensions,
          "reach=", rep_g.reachable_critical_dimensions,
          "insp=", rep_g.inspectable_count, "mean=", rep_g.meaningful_inspectable_count,
          "src=", rep_g.distinct_meaningful_source_count, "rh=", rep_g.red_herring_count)
    b = solve(raw)
    a = solve(rep)
    print("solver who/why/weapon before:", b.who.winner, b.why.winner, b.weapon.winner)
    print("solver who/why/weapon after :", a.who.winner, a.why.winner, a.weapon.winner)
    print("proof evidence_ids_used unchanged:",
          tuple(b.evidence_ids_used) == tuple(a.evidence_ids_used))

    # ------------------------------------------------------------------ #
    header("[B] added placements/objects: declared namespace + anchors")
    declared_objects = {o.object_id for o in rep.objects}
    declared_evidence = {e.id for e in rep.evidence}
    declared_locations = {l.location_id for l in rep.world_graph.locations}
    added = [p for p in rep.world_graph.placements
             if p.object_id not in {o.object_id for o in raw.objects}]
    print("namespace violations:", _world_graph_namespace_violations(rep))
    print("added placements:")
    for p in added:
        print("   ", p.object_id, p.location_id, p.anchor, p.interaction, p.evidence_id,
              "| obj_declared", p.object_id in declared_objects,
              "| loc_declared", p.location_id in declared_locations,
              "| ev_declared", p.evidence_id in declared_evidence)
    from app.generation import safety as _safety
    bad_anchor = [p for p in added if p.anchor not in _safety.ANCHOR_ALLOWLIST]
    print("added anchors NOT in ANCHOR_ALLOWLIST:", [p.anchor for p in bad_anchor])
    # validate_world_graph over the repaired draft
    from app.generation.safety import validate_world_graph
    wg_issues = validate_world_graph(
        rep.world_graph, declared_objects, declared_evidence
    )
    print("validate_world_graph(rep):", wg_issues)

    # does the anchor belong to the SELECTED kit's zones?
    kit = load_environment("apartment")
    kit_anchor_ids = {a.anchor_id for a in kit.anchors}
    foreign = [p.anchor for p in added if p.anchor not in kit_anchor_ids]
    print("added anchors NOT in the selected kit's anchor set:", foreign)

    # ------------------------------------------------------------------ #
    header("[C] determinism + idempotence")
    r1 = _ensure_playability_clues(raw, load_environment("apartment"), "medium")
    r2 = _ensure_playability_clues(raw, load_environment("apartment"), "medium")
    print("two independent repairs equal:", placement_tuple(r1) == placement_tuple(r2))
    rr = _ensure_playability_clues(r1, load_environment("apartment"), "medium")
    print("repair(repair) == repair:", placement_tuple(rr) == placement_tuple(r1),
          "| identity:", rr is r1)
    print("repair objects == rep objects:",
          object_tuple(rr) == object_tuple(r1))
    # deterministic across process ordering changes? (PYTHONHASHSEED tested by
    # re-running; here at least repeated within-process)
    r3 = _ensure_playability_clues(raw, load_environment("apartment"), "high" if False else "medium")
    print("third repair equal:", placement_tuple(r3) == placement_tuple(r1))

    # ------------------------------------------------------------------ #
    header("[D] coverage loop binds ONLY existing, discoverable facts?")

    # Build a case whose only problems are richness (all dimensions reachable)
    # by linking only the winner facts; then observe what coverage_clue binds.
    repaired = repaired_draft(rec, "hard")
    added_ids = [p.object_id for p in repaired.world_graph.placements
                 if p.object_id not in {o.object_id for o in raw.objects}]
    print("hard added object ids:", added_ids)
    # every bound evidence id must be an existing discoverable fact
    disc = {e.id for e in raw.evidence if getattr(e, "discoverable", True) is not False}
    for p in repaired.world_graph.placements:
        if p.object_id in added_ids and p.evidence_id is not None:
            if p.evidence_id not in disc:
                print("  !! bound NON-discoverable/foreign fact", p.evidence_id)
    print("all bound facts discoverable:", all(
        p.evidence_id in disc
        for p in repaired.world_graph.placements
        if p.object_id in added_ids and p.evidence_id is not None
    ))

    # ------------------------------------------------------------------ #
    header("[E] empty placements world fails closed (repair is a no-op)")
    _c2, rec2 = held(empty_placements_script(), "medium")
    raw2 = rec2.published.draft
    rep2 = _ensure_playability_clues(raw2, load_environment("apartment"), "medium")
    print("empty-world repair is no-op:", rep2 is raw2 or placement_tuple(rep2) == placement_tuple(raw2))
    print("empty-world gate3 valid:", gate3(rep2, "medium").valid)

    # ------------------------------------------------------------------ #
    header("[F] unrepairable: placements exist but NO discoverable evidence")
    # Strip every evidence fact to non-discoverable / remove the links.
    raw3 = raw
    stripped = dataclasses.replace(
        raw3,
        evidence=tuple(
            dataclasses.replace(e, discoverable=False) for e in raw3.evidence
        ),
    )
    rep3 = _ensure_playability_clues(stripped, load_environment("apartment"), "medium")
    print("no-discoverable repair changed:",
          placement_tuple(rep3) != placement_tuple(stripped))
    print("no-discoverable gate3 valid:", gate3(rep3, "medium").valid)
    print("no-discoverable issues:", gate3(rep3, "medium").issue_codes)

    # ------------------------------------------------------------------ #
    header("[G] can the repair bind a DECOY as a winning clue?")
    # For each required dim, compare the fact bound by _winning_fact_for_dimension
    # against Gate3's own winner-support predicate.
    from app.services.generation import _winning_fact_for_dimension
    public, evidence, truth = phase3(raw)
    proof = solve(raw)
    reachable_facts_after = []
    linked_ids = {str(p.evidence_id) for p in repaired.world_graph.placements
                  if p.evidence_id is not None}
    ev_by_id = {e.id: e for e in evidence}
    reachable_facts_after = [ev_by_id[i] for i in linked_ids if i in ev_by_id]
    usable_w, usable_f, _all = playability._witness_reachability(public, evidence)
    for dim in ("WHO", "WHY", "WEAPON", "WHEN"):
        wf = _winning_fact_for_dimension(dim, raw.evidence, raw.crime)
        print(f"  {dim}: winning_fact={getattr(wf,'id',None)} "
              f"supported_by_gate3={playability._winner_dimension_supported(dim, reachable_facts_after, proof, usable_w)}")

    # ------------------------------------------------------------------ #
    header("[H] second-solution check: repair never mutates evidence/crime")
    print("evidence id tuples equal:",
          tuple(e.id for e in rep.evidence) == tuple(e.id for e in raw.evidence))
    print("crime equal:", rep.crime == raw.crime)
    print("persons equal:", rep.persons == raw.persons)
    print("motives equal:", rep.motives == raw.motives)
    print("locations equal:", rep.locations == raw.locations)
    b = solve(raw); a = solve(rep)
    print("proof.who.unique before/after:", b.who.unique, a.who.unique)
    print("proof.why.unique before/after:", b.why.unique, a.why.unique)
    print("proof.weapon.unique before/after:", b.weapon.unique, a.weapon.unique)

    # ------------------------------------------------------------------ #
    header("[I] boundedness across difficulty")
    for d in ("easy", "medium", "hard"):
        rp = repaired_draft(rec, d)
        added_n = len(rp.world_graph.placements) - len(raw.world_graph.placements)
        pol = difficulty_policy.policy_for(d)
        print(f"  {d}: added={added_n} min_rh={pol.min_red_herrings} "
              f"missing_dims_bound={4} bound_ok={added_n <= 4 + pol.min_red_herrings}")


if __name__ == "__main__":
    main()
