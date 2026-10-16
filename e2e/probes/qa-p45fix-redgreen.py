"""Independent QA probe — Phase45-Fix RED->GREEN (playability reachability).

Runs the REAL GenerationController + REAL GenerationService publication guard
against a provider-authored, no-evidence-link world whose deterministic
evidence completion makes the solver unique.

It is source-version agnostic: the SAME file runs against the pre-fix HEAD
worktree and the post-fix working tree, so the two printed reports are directly
comparable. It captures the exact Gate 3 before/after metrics, the publication
outcome, and the structured event sequence.

Usage:
    python e2e/probes/qa-p45fix-redgreen.py <backend_dir> [difficulty]
"""
from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path

BACKEND = Path(sys.argv[1]).resolve()
DIFFICULTY = sys.argv[2] if len(sys.argv) > 2 else "medium"
# ``fixtures`` lives under backend/tests (pytest prepends the test dir); ``app``
# lives under backend. Put the target backend FIRST so pre-fix/post-fix runs
# import the intended source tree.
sys.path.insert(0, str(BACKEND / "tests"))
sys.path.insert(0, str(BACKEND))

from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

from app.generation import pipeline, playability  # noqa: E402
from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.generation import GenerationService  # noqa: E402

_G = GOLDEN_STAGE_PAYLOADS

PROVIDER_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

LOGGER = "procedural-detective"


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        if getattr(record, "pd_event", None):
            self.records.append(record)


@contextlib.contextmanager
def capture():
    lg = logging.getLogger(LOGGER)
    h = _Cap()
    prev_level, prev_disabled = lg.level, lg.disabled
    lg.addHandler(h)
    lg.setLevel(logging.DEBUG)
    lg.disabled = False
    try:
        yield h.records
    finally:
        lg.removeHandler(h)
        lg.setLevel(prev_level)
        lg.disabled = prev_disabled


def dump(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)


def completion_docs():
    """Golden renamed to a disjoint provider namespace; EMPTY evidence; NO links."""
    docs = {stage: json.loads(payload) for stage, payload in _G.items()}
    crime = docs[GenerationStage.CASE_TRUTH]["crime"]
    crime["weaponId"] = "prov_obj_" + crime["weaponId"]
    for fact in docs[GenerationStage.EVIDENCE]["evidence"]:
        fact["id"] = "prov_ev_" + fact["id"]
        for prop in fact["propositions"]:
            if prop.get("objectId"):
                prop["objectId"] = "prov_obj_" + prop["objectId"]
    for obj in docs[GenerationStage.PUBLIC_WORLD]["objects"]:
        obj["objectId"] = "prov_obj_" + obj["objectId"]
    for placement in docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"]:
        placement["objectId"] = "prov_obj_" + placement["objectId"]
        if placement.get("evidenceId"):
            placement["evidenceId"] = "prov_ev_" + placement["evidenceId"]
    docs[GenerationStage.EVIDENCE] = {"evidence": []}
    for placement in docs[GenerationStage.WORLD_GRAPH]["worldGraph"]["placements"]:
        placement["evidenceId"] = None
        if not placement.get("interaction"):
            placement["interaction"] = "inspect"
    return docs


def script():
    docs = completion_docs()
    return {
        stage: [dump(docs[stage])]
        for stage in (
            GenerationStage.CASE_TRUTH,
            GenerationStage.PUBLIC_WORLD,
            GenerationStage.EVIDENCE,
            GenerationStage.WORLD_GRAPH,
        )
    }


def held():
    clock = ManualClock(start_time=1000.0)
    ids = IdSource()
    admission = AdmissionController(
        clock=clock, ids=ids,
        max_concurrent_generations=4, max_concurrent_generations_global=8,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_end=1000.0 + 86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=FakeProvider(script=script()),
        admission=admission, clock=clock, ids=ids,
        deadline_seconds=60, max_llm_calls_per_generation=8,
        max_repair_passes=2, max_full_regenerations=2, max_prompt_chars=4000,
        seed=11, hold_before_publish=True, difficulty=DIFFICULTY,
    )
    handle = controller.start_generation(
        PROVIDER_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    result = controller.publish(handle.attempt_id, hold_ok=True)
    record.published = result.published
    return controller, handle, record, result


class _Store:
    def __init__(self):
        self.case_version_states = []
        self.attempt_states = []

    def update_case_version_state(self, cid, ver, state, state_reason=None):
        self.case_version_states.append((cid, ver, state, state_reason))

    def upsert_generation_attempt(self, **kw):
        self.attempt_states.append(kw)


class _Pub:
    def __init__(self):
        self.calls = 0
        self.published = None

    def publish_transactionally(self, published, **kw):
        self.calls += 1
        self.published = published


def guard_stub():
    s = object.__new__(GenerationService)
    s._store = _Store()
    s._publication = _Pub()
    s._last_publish_provenance = None
    s._last_environment_resolution = None
    return s


def guard(stub, record):
    return GenerationService._publish_if_ready(
        stub, record, "title", object(), 1000.0, record.case_id, 1,
        model=None, difficulty=DIFFICULTY,
    )


def metrics(rep):
    return {
        "valid": rep.valid,
        "inspectableCount": rep.inspectable_count,
        "meaningfulInspectableCount": rep.meaningful_inspectable_count,
        "distinctMeaningfulSourceCount": rep.distinct_meaningful_source_count,
        "redHerringCount": rep.red_herring_count,
        "reachableCriticalDimensions": list(rep.reachable_critical_dimensions),
        "unreachableCriticalDimensions": list(rep.unreachable_critical_dimensions),
        "issueCodes": list(rep.issue_codes),
    }


def main():
    print(f"=== QA PROBE backend={BACKEND} difficulty={DIFFICULTY} ===")
    has_fix = hasattr(GenerationService, "_repair_playability_candidate")
    print("FIX_PRESENT:", has_fix)

    _controller, _handle, record, result = held()
    report = pipeline.validate_draft(record)
    print("gate12_outcome:", report.outcome.value)
    proof = record.solver_proof
    print("solver_proof_present:", proof is not None)
    if proof is not None:
        print("solver_who_unique:", proof.who.unique, "winner:", proof.who.winner)
        print("solver_why_unique:", proof.why.unique, "winner:", proof.why.winner)
        print("solver_weapon_unique:", proof.weapon.unique)
        print("solver_when_ambiguous:", proof.when.ambiguous)
    print("evidence_completed:", getattr(record, "_evidence_completed", None))
    ev = record.stage_outputs.get(GenerationStage.EVIDENCE)
    ids = [e.id for e in ev.evidence] if ev is not None else []
    dev = [i for i in ids if i.startswith("d_ev_")]
    print("d_ev_facts_count:", len(dev))
    print("d_ev_facts_sample:", dev[:6])
    print("world_graph_stage_done:", GenerationStage.WORLD_GRAPH in record.stage_done)
    print("controller_publish_success:", result.success)
    print("state_after_controller_publish:", record.state.value)

    raw = playability.validate_published_candidate(record.published, DIFFICULTY)
    print("RAW_GATE3_BEFORE:", json.dumps(metrics(raw), sort_keys=True))

    stub = guard_stub()
    with capture() as recs:
        status = guard(stub, record)
    evseq = [getattr(r, "pd_event", None) for r in recs]
    print("guard_status:", status)
    print("publication_calls:", stub._publication.calls)
    print("record_state_after_guard:", record.state.value)
    print("guard_events:", evseq)
    if stub._publication.published is not None:
        pg = playability.validate_published_candidate(
            stub._publication.published, DIFFICULTY
        )
        print("PUBLISHED_GATE3_AFTER:", json.dumps(metrics(pg), sort_keys=True))
    else:
        print("PUBLISHED_GATE3_AFTER: null")


if __name__ == "__main__":
    main()