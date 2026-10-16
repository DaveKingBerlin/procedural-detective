"""Probe 2 — publication-boundary attacks: desync, override, fail-closed,
event/truth leakage, case_version consistency.

Run:  python screenshots/evidence/adv45fix/probe2_guard.py
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import (  # noqa: E402
    GenerationState,
    capture_events,
    completion_script,
    empty_placements_script,
    events,
    golden_script,
    guard_stub,
    header,
    held,
    load_environment,
    phase3,
    placement_tuple,
    playability,
    publish_via_guard,
    service,
    solve,
    ValidationOutcome,
    pipeline,
    WorldRequirements,
)


def main():
    header("PROBE 2 — publication boundary")

    # ------------------------------------------------------------------ #
    header("[A] guard repair: published candidate == what Gate3 validated")
    _c, rec = held(completion_script(), "medium")
    stub = guard_stub()
    captured = {}
    orig = stub._publication.publish_transactionally

    def cap(published, **kwargs):
        captured["published"] = published
        captured["kwargs"] = kwargs
        return orig(published, **kwargs)

    stub._publication.publish_transactionally = cap
    status = publish_via_guard(stub, rec, "medium")
    pub = captured.get("published")
    print("status:", status, "calls:", stub._publication.calls)
    print("record.published is published:", rec.published is pub)
    print("record.published == published:", rec.published == pub)
    g = playability.validate_published_candidate(pub, "medium")
    print("published gate3.valid:", g.valid, "unreach:", g.unreachable_critical_dimensions)
    print("case_version published:", getattr(pub, "case_version", None))
    print("draft is repaired (placements > raw):",
          len(pub.draft.world_graph.placements),
          "vs raw", len(rec.stage_outputs is not None and [] or []))
    # re-score the EXACT persisted draft captured at the seam
    g2 = playability.validate_published_candidate(pub, "medium")
    print("re-score identical:", g == g2)

    # Compare the persisted draft to record.draft (the repaired one)
    print("persisted draft == record.draft:", pub.draft == rec.draft)

    # ------------------------------------------------------------------ #
    header("[B] does the guard repair leave the evidence/crime/solver untouched?")
    print("evidence ids unchanged:",
          tuple(e.id for e in pub.evidence) ==
          tuple(e.id for e in rec.stage_outputs[__import__('app.generation.provider', fromlist=['GenerationStage']).GenerationStage.EVIDENCE].evidence)
          if False else "n/a")
    print("crime unchanged:",
          pub.draft.crime == rec.draft.crime)
    print("truth is published.truth:", pub.truth is rec.published.truth or pub.truth == rec.published.truth)
    proof_before = solve(rec.draft)
    proof_after = solve(pub.draft)
    print("who/why/weapon before:", proof_before.who.winner, proof_before.why.winner, proof_before.weapon.winner)
    print("who/why/weapon after :", proof_after.who.winner, proof_after.why.winner, proof_after.weapon.winner)

    # ------------------------------------------------------------------ #
    header("[C] empty placements world: guard fails closed, no publish call")
    _c2, rec2 = held(empty_placements_script(), "medium")
    stub2 = guard_stub()
    status2 = publish_via_guard(stub2, rec2, "medium")
    print("status:", status2, "calls:", stub2._publication.calls,
          "state:", rec2.state, "failure_code:", getattr(rec2, "failure_code", None))
    print("durable states:", stub2._store.case_version_states)

    # ------------------------------------------------------------------ #
    header("[D] witness-content-unreachable case: repair must NOT mask it")
    # Build a draft with a published witness that has no usable statement by
    # removing the witness facts' presentation text.
    _c3, rec3 = held(completion_script(), "medium")
    raw = rec3.published.draft
    stripped = dataclasses.replace(
        raw,
        evidence=tuple(
            dataclasses.replace(e, presentation={})
            if "witness" in (e.kind or "").lower() or "STATEMENT" in (e.kind or "")
            else e
            for e in raw.evidence
        ),
    )
    from app.services.generation import _ensure_playability_clues
    repaired3 = _ensure_playability_clues(stripped, load_environment("apartment"), "medium")
    from harness import gate3
    print("witness-stripped repaired gate3.valid:", gate3(repaired3, "medium").valid)
    print("witness-stripped issues:", gate3(repaired3, "medium").issue_codes)

    # ------------------------------------------------------------------ #
    header("[E] event leakage: playability.validation.repaired fields")
    _c4, rec4 = held(completion_script(), "medium")
    stub4 = guard_stub()
    with capture_events() as records:
        publish_via_guard(stub4, rec4, "medium")
    evs = events(records)
    print("events:", evs)
    for r in records:
        nm = getattr(r, "pd_event", None)
        if nm and nm.startswith("playability"):
            print(" ", nm, dict(r.pd_fields))
    # scan all captured text for hidden-truth tokens
    truth_tokens = ["thomas_reed", "sarah_miller", "cover_up_embezzlement",
                    "kitchen_knife", "emily_reed"]
    blob = "\n".join(str(dict(getattr(r, "pd_fields", {}))) for r in records)
    hits = [t for t in truth_tokens if t in blob]
    print("hidden-truth tokens in events:", hits)

    # ------------------------------------------------------------------ #
    header("[F] golden hard: does the repair now pad the demo fixture to pass?")
    _c5, rec5 = held(golden_script(), "hard")
    raw5 = rec5.published.draft
    pre = playability.validate_published_candidate(rec5.published, "hard")
    print("golden hard RAW (pre-service) gate3.valid:", pre.valid,
          "issues:", pre.issue_codes)
    svc = service()
    with capture_events() as records5:
        svc._apply_kit_composition(rec5, "apartment", WorldRequirements(), None)
    post = playability.validate_published_candidate(rec5.published, "hard")
    print("golden hard AFTER legacy composition gate3.valid:", post.valid,
          "issues:", post.issue_codes,
          "insp/mean/src/rh:", post.inspectable_count,
          post.meaningful_inspectable_count, post.distinct_meaningful_source_count,
          post.red_herring_count)
    print("compose events:", [e for e in events(records5)])

    # and easy/medium golden are no-ops?
    for d in ("easy", "medium"):
        _cc, rr = held(golden_script(), d)
        before = placement_tuple(rr.published.draft)
        svc2 = service()
        svc2._apply_kit_composition(rr, "apartment", WorldRequirements(), None)
        after = placement_tuple(rr.published.draft)
        print(f"golden {d}: placements unchanged by legacy repair:",
              before == after)


if __name__ == "__main__":
    main()
