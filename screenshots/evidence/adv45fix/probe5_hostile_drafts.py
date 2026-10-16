"""Probe 5 — hostile provider drafts against the repair.

Constructs drafts from the golden docs with hostile mutations and runs the
deterministic repair + Gate 3 + namespace guard.

Run:  python screenshots/evidence/adv45fix/probe5_hostile_drafts.py
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harness import (  # noqa: E402
    _G,
    _dump,
    _ensure_playability_clues,
    _world_graph_namespace_violations,
    GenerationStage,
    GeneratedDraft,
    header,
    load_environment,
    parser,
    phase3,
    placement_tuple,
    playability,
    solve,
)


def draft_from_docs(docs):
    crime = parser.parse_stage(GenerationStage.CASE_TRUTH, _dump(docs[GenerationStage.CASE_TRUTH]))
    pw = parser.parse_stage(GenerationStage.PUBLIC_WORLD, _dump(docs[GenerationStage.PUBLIC_WORLD]))
    ev = parser.parse_stage(GenerationStage.EVIDENCE, _dump(docs[GenerationStage.EVIDENCE]))
    wg = parser.parse_stage(GenerationStage.WORLD_GRAPH, _dump(docs[GenerationStage.WORLD_GRAPH]))
    draft = GeneratedDraft(
        crime=crime,
        persons=pw.persons,
        motives=pw.motives,
        objects=pw.objects,
        locations=pw.locations,
        travel_rules=pw.travel_rules,
        scene=pw.scene,
        evidence=ev.evidence,
        world_graph=wg,
    )
    draft = dataclasses.replace(draft, scene=pw.scene)
    return draft


def base_docs():
    return {s: json.loads(p) for s, p in _G.items()}


def gate3(draft, difficulty="medium"):
    public, evidence, truth = phase3(draft)
    return playability.validate_playability_quality(
        draft=draft, public=public, evidence=evidence, universes=None,
        truth=truth, difficulty=difficulty,
    )


def strip_links(docs):
    for p in docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"]:
        p["evidenceId"] = None
        if not p.get("interaction"):
            p["interaction"] = "inspect"
    return docs


def main():
    header("PROBE 5 — hostile provider drafts")

    # ------------------------------------------------------------------ #
    header("[A] duplicate object-name collision: provider already has dim_clue_who")
    docs = base_docs()
    # add a hostile public object named dim_clue_who and a placement for it
    docs[GenerationStage.PUBLIC_WORLD]["objects"].append(
        {"objectId": "dim_clue_who", "assetId": "PROP_VASE_01", "affordances": ["INSPECTABLE"]}
    )
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    wg["placements"].append(
        {"objectId": "dim_clue_who", "assetId": "PROP_VASE_01",
         "locationId": wg["placements"][0]["locationId"], "anchor": "spawn",
         "interaction": "read", "evidenceId": None}
    )
    strip_links(docs)
    d = draft_from_docs(docs)
    rep = _ensure_playability_clues(d, load_environment("apartment"), "medium")
    obj_ids = [o.object_id for o in rep.objects]
    pl_obj = [p.object_id for p in rep.world_graph.placements]
    print("duplicate object ids:", len(obj_ids) != len(set(obj_ids)),
          "| duplicate placement object ids:", len(pl_obj) != len(set(pl_obj)))
    print("namespace violations:", _world_graph_namespace_violations(rep))
    print("gate3 valid:", gate3(rep, "medium").valid)

    # ------------------------------------------------------------------ #
    header("[B] provider world that already declares decoy_clue_1 / coverage_clue")
    docs = base_docs()
    docs[GenerationStage.PUBLIC_WORLD]["objects"].append(
        {"objectId": "coverage_clue", "assetId": "PROP_VASE_01", "affordances": ["INSPECTABLE"]}
    )
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    wg["placements"].append(
        {"objectId": "coverage_clue", "assetId": "PROP_VASE_01",
         "locationId": wg["placements"][0]["locationId"], "anchor": "spawn",
         "interaction": "read", "evidenceId": None}
    )
    strip_links(docs)
    d = draft_from_docs(docs)
    rep = _ensure_playability_clues(d, load_environment("apartment"), "hard")
    obj_ids = [o.object_id for o in rep.objects]
    pl_obj = [p.object_id for p in rep.world_graph.placements]
    print("duplicate object ids:", len(obj_ids) != len(set(obj_ids)),
          "| dup placements:", len(pl_obj) != len(set(pl_obj)))
    print("coverage-ish ids:", [o for o in obj_ids if o.startswith(("coverage_clue", "dim_clue", "decoy_clue"))])
    print("gate3 valid:", gate3(rep, "hard").valid)

    # ------------------------------------------------------------------ #
    header("[C] anchor exhaustion -> global-allowlist fallback anchors")
    from app.generation import safety as _safety
    kit = load_environment("apartment")
    kit_anchors = {a.anchor_id for a in kit.anchors}
    docs = base_docs()
    # occupy EVERY kit anchor with a placement (so kit anchors are exhausted)
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    loc = wg["placements"][0]["locationId"]
    objs = docs[GenerationStage.PUBLIC_WORLD]["objects"]
    pls = wg["placements"]
    idx = 0
    for anchor_id in sorted(_safety.ANCHOR_ALLOWLIST):
        if anchor_id in {p["anchor"] for p in pls}:
            continue
        oid = f"filler_obj_{idx}"
        idx += 1
        objs.append({"objectId": oid, "assetId": "PROP_VASE_01", "affordances": ["INSPECTABLE"]})
        pls.append({"objectId": oid, "assetId": "PROP_VASE_01", "locationId": loc,
                    "anchor": anchor_id, "interaction": "read", "evidenceId": None})
    strip_links(docs)
    d = draft_from_docs(docs)
    rep = _ensure_playability_clues(d, kit, "medium")
    added = [p for p in rep.world_graph.placements
             if str(p.object_id).startswith(("dim_clue", "decoy_clue", "coverage_clue"))]
    print("added anchors:", [(p.object_id, p.anchor) for p in added])
    print("added anchors NOT in apartment kit:", [p.anchor for p in added if p.anchor not in kit_anchors])
    print("added anchors in global allowlist:",
          all(p.anchor in _safety.ANCHOR_ALLOWLIST for p in added))
    print("namespace violations:", _world_graph_namespace_violations(rep))
    print("gate3 valid:", gate3(rep, "medium").valid)

    # ------------------------------------------------------------------ #
    header("[D] provider world with NO declared locations (repair must skip)")
    docs = base_docs()
    docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["locations"] = []
    strip_links(docs)
    d = draft_from_docs(docs)
    rep = _ensure_playability_clues(d, load_environment("apartment"), "medium")
    print("no-locations repair is no-op:", placement_tuple(rep) == placement_tuple(d))
    print("no-locations gate3 valid:", gate3(rep, "medium").valid)

    # ------------------------------------------------------------------ #
    header("[E] crime location NOT declared -> falls back to first declared location")
    docs = base_docs()
    strip_links(docs)
    crime = docs[GenerationStage.CASE_TRUTH]["crime"]
    crime["locationId"] = "nonexistent_loc_zzz"
    d = draft_from_docs(docs)
    rep = _ensure_playability_clues(d, load_environment("apartment"), "medium")
    added = [p for p in rep.world_graph.placements
             if str(p.object_id).startswith(("dim_clue", "decoy_clue", "coverage_clue"))]
    declared = {l.location_id for l in d.world_graph.locations}
    print("added locations:", sorted({p.location_id for p in added}))
    print("added locations all declared:", all(p.location_id in declared for p in added))

    # ------------------------------------------------------------------ #
    header("[F] coverage loop: only winning facts + no decoy candidates (medium)")
    # Remove every excluded-candidate fact so there are no red-herring candidates,
    # leaving only the 4 winning-ish facts. Then medium's min_red_herrings=1
    # cannot be met -> gate3 must remain invalid (fail closed).
    docs = base_docs()
    strip_links(docs)
    d = draft_from_docs(docs)
    public, evidence, truth = phase3(d)
    proof = solve(d)
    winners = playability._winner_ids(proof)
    keep = []
    from app.generation import playability as _pl
    evidence_by_id = {e.id: e for e in evidence}
    # Drop facts whose subjects include an excluded candidate
    excluded = _pl._excluded_candidate_ids(proof)
    drop = set()
    for e in evidence:
        subs = _pl._subjects_of_facts(evidence_by_id, (e.id,))
        if any(s in excluded and s not in winners for s in subs):
            drop.add(e.id)
    stripped = dataclasses.replace(d, evidence=tuple(e for e in d.evidence if e.id not in drop))
    rep = _ensure_playability_clues(stripped, load_environment("apartment"), "medium")
    g = gate3(rep, "medium")
    print("dropped decoy facts:", len(drop))
    print("gate3 valid (medium, no decoys):", g.valid, "issues:", g.issue_codes,
          "rh:", g.red_herring_count)

    # ------------------------------------------------------------------ #
    header("[G] idempotence across a hostile 2nd call after a partial repair")
    docs = base_docs(); strip_links(docs)
    d = draft_from_docs(docs)
    once = _ensure_playability_clues(d, load_environment("apartment"), "hard")
    twice = _ensure_playability_clues(once, load_environment("apartment"), "hard")
    print("idempotent:", placement_tuple(once) == placement_tuple(twice))

    # ------------------------------------------------------------------ #
    header("[H] 'second solution': solver proof identical before/after repair")
    b = solve(d); a = solve(once)
    same = (b.who.winner, b.why.winner, b.weapon.winner, tuple(b.evidence_ids_used)) == \
           (a.who.winner, a.why.winner, a.weapon.winner, tuple(a.evidence_ids_used))
    print("proof identical:", same)


if __name__ == "__main__":
    main()
