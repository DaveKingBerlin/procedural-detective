"""Probe 7 — guard repair on a candidate it cannot fully fix: does it emit an
authoritative `generation.validation.complete VALID` for a candidate that is
then discarded (ADR-005 D4 consistency), and does it restore state?

Run:  python screenshots/evidence/adv45fix/probe7_late_validation.py
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harness import (  # noqa: E402
    capture_events,
    completion_script,
    events,
    guard_stub,
    header,
    held,
    load_environment,
    phase3,
    playability,
    publish_via_guard,
    solve,
)

from app.generation import playability as _pl  # noqa: E402


def strip_decoy_candidates(record):
    """Remove every fact whose subject is an excluded non-winner candidate so
    the red-herring requirement cannot be satisfied by the repair."""
    draft = record.published.draft
    public, evidence, truth = phase3(record.published.draft)
    proof = solve(draft)
    winners = _pl._winner_ids(proof)
    excluded = _pl._excluded_candidate_ids(proof)
    evidence_by_id = {e.id: e for e in evidence}
    drop = set()
    for e in draft.evidence:
        subs = _pl._subjects_of_facts(evidence_by_id, (e.id,))
        if any(s in excluded and s not in winners for s in subs):
            drop.add(e.id)
    new_draft = dataclasses.replace(
        draft,
        evidence=tuple(e for e in draft.evidence if e.id not in drop),
        # keep the world link-less so reachability is also missing
        world_graph=dataclasses.replace(
            draft.world_graph,
            placements=tuple(
                dataclasses.replace(p, evidence_id=None) for p in draft.world_graph.placements
            ),
        ),
    )
    return new_draft, drop


def main():
    header("PROBE 7 — guard repair on an unfixable candidate")

    _c, rec = held(completion_script(), "medium")
    new_draft, drop = strip_decoy_candidates(rec)
    print("dropped decoy facts:", len(drop))
    # Replace the frozen candidate with the stripped draft (as the guard sees it)
    rec.draft = new_draft
    rec._phase3_cache = None
    rec.published = dataclasses.replace(rec.published, draft=new_draft)
    raw_g = playability.validate_published_candidate(rec.published, "medium")
    print("raw frozen gate3.valid:", raw_g.valid, "issues:", raw_g.issue_codes)
    pre_proof = rec.solver_proof
    pre_state = rec.state

    stub = guard_stub()
    with capture_events() as records:
        status = publish_via_guard(stub, rec, "medium")
    evs = events(records)
    print("status:", status, "| publish calls:", stub._publication.calls)
    print("events:", evs)
    for r in records:
        nm = getattr(r, "pd_event", None)
        if nm in ("generation.validation.complete", "generation.validation.started",
                  "playability.validation.failed", "playability.validation.repaired"):
            print("  ", nm, dict(r.pd_fields))
    print("state after:", rec.state, "| solver_proof restored:", rec.solver_proof is pre_proof)
    print("draft restored:", rec.draft == new_draft)
    print("published restored:", rec.published.draft == new_draft)


if __name__ == "__main__":
    main()
