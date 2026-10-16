"""Probe 6 — determinism across processes + suicide variant repair.

Run three times with different PYTHONHASHSEED; compare the digest.
Also run a SUICIDE completion case through the repair.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harness import (  # noqa: E402
    _G,
    _dump,
    _ensure_playability_clues,
    GenerationStage,
    GeneratedDraft,
    header,
    load_environment,
    parser,
    phase3,
    playability,
    solve,
)


def draft_from_docs(docs):
    crime = parser.parse_stage(GenerationStage.CASE_TRUTH, _dump(docs[GenerationStage.CASE_TRUTH]))
    pw = parser.parse_stage(GenerationStage.PUBLIC_WORLD, _dump(docs[GenerationStage.PUBLIC_WORLD]))
    ev = parser.parse_stage(GenerationStage.EVIDENCE, _dump(docs[GenerationStage.EVIDENCE]))
    wg = parser.parse_stage(GenerationStage.WORLD_GRAPH, _dump(docs[GenerationStage.WORLD_GRAPH]))
    return GeneratedDraft(
        crime=crime, persons=pw.persons, motives=pw.motives, objects=pw.objects,
        locations=pw.locations, travel_rules=pw.travel_rules, scene=pw.scene,
        evidence=ev.evidence, world_graph=wg,
    )


def base_docs():
    return {s: json.loads(p) for s, p in _G.items()}


def strip_links(docs):
    for p in docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"]:
        p["evidenceId"] = None
        if not p.get("interaction"):
            p["interaction"] = "inspect"
    return docs


def digest(draft):
    parts = []
    for p in draft.world_graph.placements:
        parts.append((p.object_id, p.location_id, p.anchor, p.interaction, p.evidence_id))
    for o in draft.objects:
        parts.append((o.object_id, o.asset_id, tuple(o.affordances), o.subtype))
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def main():
    header("PROBE 6 — determinism + suicide")

    docs = strip_links(base_docs())
    d = draft_from_docs(docs)
    rep = _ensure_playability_clues(d, load_environment("apartment"), "hard")
    print("REPAIR_DIGEST", digest(rep))

    # suicide variant: resolutionType SUICIDE, murdererId removed
    sdocs = strip_links(base_docs())
    crime = sdocs[GenerationStage.CASE_TRUTH]["crime"]
    crime["resolutionType"] = "SUICIDE"
    crime.pop("murdererId", None)
    # remove murderer-pointing evidence facts so the algebra/repair is suicide-shaped
    sd = draft_from_docs(sdocs)
    srep = _ensure_playability_clues(sd, load_environment("apartment"), "medium")
    public, evidence, truth = phase3(srep)
    g = playability.validate_playability_quality(
        draft=srep, public=public, evidence=evidence, universes=None,
        truth=truth, difficulty="medium",
    )
    print("suicide required dims:", playability._required_dimensions(truth))
    print("suicide repair valid:", g.valid, "issues:", g.issue_codes,
          "unreach:", g.unreachable_critical_dimensions,
          "reach:", g.reachable_critical_dimensions)
    proof = solve(srep)
    print("suicide proof who/why/weapon:", proof.who.winner, proof.why.winner, proof.weapon.winner)
    print("SUICIDE_DIGEST", digest(srep))


if __name__ == "__main__":
    main()
