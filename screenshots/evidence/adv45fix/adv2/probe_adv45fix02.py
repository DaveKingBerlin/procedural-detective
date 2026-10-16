"""ADV-45FIX-02 re-verification (Phase45 Fix adversarial, 2nd pass).

Hermetic, ZERO network. Confirms:
  - a DISCARDED repaired candidate revalidates under the non-authoritative
    ``playability.repair.validation.*`` prefix and emits NO
    ``generation.validation.*`` events;
  - a SUCCESSFUL repaired candidate still emits no authoritative
    ``generation.validation.*`` from the guard (the repair prefix is additive);
  - the normal authoritative lifecycle (the controller run) still emits
    ``generation.validation.started``/``.complete``.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADV = HERE.parent
sys.path.insert(0, str(ADV))

from harness import (  # noqa: E402
    capture_events,
    completion_script,
    events,
    guard_stub,
    held,
    phase3,
    playability,
    publish_via_guard,
    solve,
)

from app.generation import playability as _pl  # noqa: E402

results: dict[str, bool] = {}


def record(name: str, ok: bool, detail: str = "") -> None:
    results[name] = ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def strip_decoy_candidates(record_obj):
    draft = record_obj.published.draft
    public, evidence, _truth = phase3(draft)
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
        world_graph=dataclasses.replace(
            draft.world_graph,
            placements=tuple(
                dataclasses.replace(p, evidence_id=None)
                for p in draft.world_graph.placements
            ),
        ),
    )
    return new_draft, drop


def main() -> int:
    print("=" * 78)
    print("ADV-45FIX-02 — non-authoritative repair revalidation prefix")
    print("=" * 78)

    # A) discarded repair ------------------------------------------------------
    _c, rec = held(completion_script(), "medium")
    new_draft, drop = strip_decoy_candidates(rec)
    rec.draft = new_draft
    rec._phase3_cache = None
    rec.published = dataclasses.replace(rec.published, draft=new_draft)
    raw = playability.validate_published_candidate(rec.published, "medium")
    stub = guard_stub()
    with capture_events() as records:
        status = publish_via_guard(stub, rec, "medium")
    evs = events(records)
    non_auth_ok = (
        "playability.repair.validation.started" in evs
        and "playability.repair.validation.complete" in evs
    )
    no_auth = (
        "generation.validation.started" not in evs
        and "generation.validation.complete" not in evs
    )
    record(
        "discarded:repair-prefix",
        non_auth_ok,
        f"events={evs}",
    )
    record(
        "discarded:no-authoritative-prefix",
        no_auth,
        f"generation.validation present={'generation.validation.complete' in evs}",
    )
    record(
        "discarded:fail-closed",
        status is None
        and stub._publication.calls == 0
        and rec.state.name == "FAILED"
        and raw.valid is False,
        f"status={status} calls={stub._publication.calls} state={rec.state.name} "
        f"raw.valid={raw.valid}",
    )
    # state restored to the caller's (stripped) candidate
    record(
        "discarded:state-restored",
        rec.published.draft == new_draft,
        f"published.draft is stripped={rec.published.draft == new_draft}",
    )

    # B) successful repair — guard capture only --------------------------------
    _c2, rec2 = held(completion_script(), "medium")
    stub2 = guard_stub()
    with capture_events() as records2:
        status2 = publish_via_guard(stub2, rec2, "medium")
    evs2 = events(records2)
    record(
        "success:repair-prefix",
        "playability.repair.validation.started" in evs2
        and "playability.repair.validation.complete" in evs2,
        f"events={evs2}",
    )
    record(
        "success:no-authoritative-prefix-in-guard",
        "generation.validation.started" not in evs2
        and "generation.validation.complete" not in evs2,
        f"generation.validation present={'generation.validation.complete' in evs2}",
    )
    record(
        "success:lifecycle-complete",
        "playability.validation.repaired" in evs2
        and "playability.validation.complete" in evs2
        and status2 == "PUBLISHED",
        f"status={status2} events={evs2}",
    )

    # C) normal authoritative lifecycle still emits generation.validation.* ----
    with capture_events() as records3:
        _c3, _rec3 = held(completion_script(), "medium")
    evs3 = events(records3)
    record(
        "normal-lifecycle:authoritative-prefix",
        "generation.validation.started" in evs3
        and "generation.validation.complete" in evs3,
        f"authoritative events="
        f"{[e for e in evs3 if e and e.startswith('generation.validation')]}",
    )

    passed = sum(1 for v in results.values() if v)
    print(f"\nADV-45FIX-02: {passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())