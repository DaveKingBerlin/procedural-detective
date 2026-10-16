"""Phase45 FINAL retest (QA-2) — INDEPENDENT re-confirmation of DEF-076
(red->green) and the namespace / foreign-ID ownership guard.

ZERO network. Read-only w.r.t. product source.

Run against the PRE-FIX worktree (HEAD, Phase44) and the POST-FIX working tree
by setting ``PD_ROOT``. Reports the observed composition behavior; the caller
interprets RED vs GREEN.

Findings:
  - event names emitted by ``_apply_kit_composition``
  - the authoritative ``record.last_validation.outcome``
  - the published object/evidence ids and any FOREIGN (kit) ids
  - any late ``generation.validation.complete`` that is not VALID
  - whether the D1 namespace guard exists and reports clean for the result
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ.get("PD_ROOT", r"C:\Users\Fujitsu\coding\procedural-detective"))
for _p in (ROOT / "backend", ROOT / "backend" / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.services.generation import GenerationService  # noqa: E402
from app.world.requirements import ObjectRequest, WorldRequirements  # noqa: E402

PROVIDER_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

_LINES: list[str] = []


def log(msg: str) -> None:
    print(msg)
    _LINES.append(msg)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list = []

    def emit(self, record) -> None:  # noqa: ANN001
        if getattr(record, "pd_event", None):
            self.records.append(record)


@contextlib.contextmanager
def _capture():
    logger = logging.getLogger("procedural-detective")
    handler = _Capture()
    prev_level, prev_disabled = logger.level, logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.disabled = False
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)
        logger.disabled = prev_disabled


def _events(records):
    return [getattr(r, "pd_event", None) for r in records]


def _dump(doc) -> str:
    return json.dumps(doc, sort_keys=True)


def _renamed_script(prefix_obj="prov_obj_", prefix_ev="prov_ev_") -> dict:
    docs = {stage: json.loads(payload) for stage, payload in GOLDEN_STAGE_PAYLOADS.items()}
    crime = docs[GenerationStage.CASE_TRUTH].get("crime") or {}
    if crime.get("weaponId"):
        crime["weaponId"] = prefix_obj + crime["weaponId"]
    for fact in docs[GenerationStage.EVIDENCE].get("evidence") or ():
        fact["id"] = prefix_ev + fact["id"]
        for prop in fact.get("propositions") or ():
            if prop.get("objectId"):
                prop["objectId"] = prefix_obj + prop["objectId"]
    for obj in docs[GenerationStage.PUBLIC_WORLD].get("objects") or ():
        obj["objectId"] = prefix_obj + obj["objectId"]
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    for placement in wg["placements"]:
        placement["objectId"] = prefix_obj + placement["objectId"]
        if placement.get("evidenceId"):
            placement["evidenceId"] = prefix_ev + placement["evidenceId"]
    return {stage: [_dump(doc)] for stage, doc in docs.items()}


def _golden_script() -> dict:
    return {stage: [payload] for stage, payload in GOLDEN_STAGE_PAYLOADS.items()}


def _single_renamed_script(which: str = "kitchen_knife") -> dict:
    """Golden script with EXACTLY ONE kit-base object (and its evidence fact)
    renamed into a disjoint provider namespace. This is the DEF-076 red shape:
    the composer sees most base ids declared, tries to compose, and (pre-fix)
    injects the golden kit ids for the renamed slot."""
    prefix_obj = "prov_obj_"
    prefix_ev = "prov_ev_"
    docs = {stage: json.loads(payload) for stage, payload in GOLDEN_STAGE_PAYLOADS.items()}

    def _obj(oid):
        return prefix_obj + oid if oid == which else oid

    def _ev(eid):
        return (
            prefix_ev + eid
            if which == "kitchen_knife" and eid == "forensic_knife_match_01"
            else eid
        )

    crime = docs[GenerationStage.CASE_TRUTH].get("crime") or {}
    if crime.get("weaponId"):
        crime["weaponId"] = _obj(crime["weaponId"])
    for fact in docs[GenerationStage.EVIDENCE].get("evidence") or ():
        fact["id"] = _ev(fact.get("id"))
        for prop in fact.get("propositions") or ():
            if prop.get("objectId"):
                prop["objectId"] = _obj(prop["objectId"])
    for obj in docs[GenerationStage.PUBLIC_WORLD].get("objects") or ():
        obj["objectId"] = _obj(obj["objectId"])
    wg = docs[GenerationStage.WORLD_GRAPH]["worldGraph"]
    for placement in wg["placements"]:
        placement["objectId"] = _obj(placement["objectId"])
        if placement.get("evidenceId"):
            placement["evidenceId"] = _ev(placement["evidenceId"])
    return {stage: [_dump(doc)] for stage, doc in docs.items()}


def _held(script: dict):
    clock = ManualClock(start_time=1000.0)
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=4,
        max_concurrent_generations_global=8,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_end=1000.0 + 86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=FakeProvider(script=script),
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=2,
        max_prompt_chars=4000,
        seed=11,
        hold_before_publish=True,
        difficulty="medium",
    )
    handle = controller.start_generation(
        PROVIDER_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    result = controller.publish(handle.attempt_id, hold_ok=True)
    assert result.success, result.reason
    record.published = result.published
    return controller, record


def _service() -> GenerationService:
    stub = object.__new__(GenerationService)
    stub._settings = SimpleNamespace(max_world_objects_per_kit=32)
    stub._spec_provider = None
    stub._generate_unknown_assets = False
    stub._generated_cache = None
    stub._world_repair_provider = None
    stub._max_world_repair_passes = 2
    stub._last_generated_composition_failed = None
    stub._last_environment_resolution = None
    return stub


def _bypass_scenario() -> bool:
    """Force the composer to return a FOREIGN placement; the D1 namespace guard
    must reject it as ``COMPOSE_NAMESPACE_MISMATCH`` and persist NO foreign id."""
    import unittest.mock as mock

    from app.generation.schemas import PlacementSpec
    from app.world.composer import WorldComposition

    _controller, record = _held(_golden_script())
    foreign = PlacementSpec(
        object_id="golden_foreign_object",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="foreign_zone_xyz",
        anchor="kitchen_counter",
        interaction="",
        evidence_id="golden_foreign_evidence",
    )

    def _fake(*_a, **_k):
        return WorldComposition(
            environment_id="apartment",
            kit_version=1,
            environment_provenance="EXACT",
            placements=(foreign,),
            new_objects=(),
            provenance_by_object_id={},
            relation_satisfied={},
            relation_notes=(),
            resolution_record={"resolved": {}},
            issues=(),
            composition_notes=(),
        )

    svc = _service()
    reqs = WorldRequirements(objects=(ObjectRequest(requested_name="mystery gadget"),))
    with mock.patch("app.world.composer.compose_world", _fake):
        with _capture() as records:
            ok = svc._apply_kit_composition(record, "apartment", reqs, None)

    events = _events(records)
    skip = next(
        (r for r in records if getattr(r, "pd_event", None) == "world_compose.skipped"),
        None,
    )
    reason = skip.pd_fields.get("reasonCode") if skip else None
    outcome = skip.pd_fields.get("compositionOutcome") if skip else None
    pub = record.published.draft
    pub_ids = {p.object_id for p in pub.world_graph.placements} | {
        p.evidence_id for p in pub.world_graph.placements if p.evidence_id
    }
    leaked = sorted(pub_ids & {"golden_foreign_object", "golden_foreign_evidence"})
    candidate_validated = any(e and e.startswith("world_compose.validation") for e in events)
    passed = (
        ok is True
        and reason == "COMPOSE_NAMESPACE_MISMATCH"
        and outcome == "SKIPPED_NAMESPACE_MISMATCH"
        and not leaked
        and not candidate_validated
    )
    log("")
    log(f"[QA2 DEF076-BYPASS] returned={ok}")
    log(f"[QA2 DEF076-BYPASS] events={events}")
    log(f"[QA2 DEF076-BYPASS] reasonCode={reason} compositionOutcome={outcome}")
    log(f"[QA2 DEF076-BYPASS] leaked foreign ids={leaked}")
    log(f"[QA2 DEF076-BYPASS] candidate validation events present={candidate_validated}")
    log(f"[QA2 DEF076-BYPASS] VERDICT: {'PASS' if passed else 'FAIL'}")
    return passed


def _adv4502_scenario() -> bool:
    """ADV-45-02 — a provider-authored apartment world with NO new object tokens
    (EMPTY WorldRequirements) must classify PROVIDER_AUTHORED_WORLD, not
    APARTMENT_BYTE_IDENTITY."""
    _controller, record = _held(_renamed_script())
    svc = _service()
    with _capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", WorldRequirements(), None)
    skip = next(
        (r for r in records if getattr(r, "pd_event", None) == "world_compose.skipped"),
        None,
    )
    reason = skip.pd_fields.get("reasonCode") if skip else None
    mode = skip.pd_fields.get("compositionMode") if skip else None
    scene = record.published.draft.scene
    passed = (
        ok is True
        and reason == "PROVIDER_AUTHORED_WORLD"
        and mode == "PROVIDER_AUTHORED"
        and scene is not None
        and scene.environment_id == "apartment"
        and isinstance(scene.environment_version, int)
        and scene.environment_version >= 1
    )
    log("")
    log(f"[QA2 ADV-45-02] returned={ok} reasonCode={reason} compositionMode={mode}")
    log(f"[QA2 ADV-45-02] scene.environment_id={getattr(scene, 'environment_id', None)!r} "
        f"version={getattr(scene, 'environment_version', None)!r}")
    log(f"[QA2 ADV-45-02] VERDICT: {'PASS' if passed else 'FAIL'}")
    return passed


def main() -> int:
    log(f"[QA2 DEF076] PD_ROOT={ROOT}")
    _controller, record = _held(_single_renamed_script("kitchen_knife"))
    canonical_objects = {o.object_id for o in record.published.draft.objects}
    canonical_evidence = {e.id for e in record.published.draft.evidence}
    log(f"[QA2 DEF076] canonical object_ids sample={sorted(canonical_objects)[:4]} "
        f"count={len(canonical_objects)}")
    log(f"[QA2 DEF076] canonical evidence_ids sample={sorted(canonical_evidence)[:4]} "
        f"count={len(canonical_evidence)}")

    svc = _service()
    reqs = WorldRequirements(objects=(ObjectRequest(requested_name="mystery gadget"),))
    with _capture() as records:
        ok = svc._apply_kit_composition(record, "apartment", reqs, None)

    published = record.published.draft
    obj_ids = [o.object_id for o in published.objects]
    ev_ids = [e.id for e in published.evidence]
    # foreign ids are what a composed candidate would inject into PLACEMENTS
    pub_place_obj = {p.object_id for p in published.world_graph.placements}
    pub_place_ev = {p.evidence_id for p in published.world_graph.placements if p.evidence_id}
    foreign_obj = sorted(pub_place_obj - canonical_objects)
    foreign_ev = sorted(pub_place_ev - canonical_evidence)
    # the authoritative attempt draft (what a discarded composed candidate must
    # never leak into)
    att = record.draft
    att_place_obj = {p.object_id for p in att.world_graph.placements}
    att_place_ev = {p.evidence_id for p in att.world_graph.placements if p.evidence_id}
    foreign_att_obj = sorted(att_place_obj - canonical_objects)
    foreign_att_ev = sorted(att_place_ev - canonical_evidence)
    late_nonvalid = [
        r.pd_fields.get("validationOutcome")
        for r in records
        if getattr(r, "pd_event", None) == "generation.validation.complete"
        and r.pd_fields.get("validationOutcome") != "VALID"
    ]

    log(f"[QA2 DEF076] returned={ok}")
    log(f"[QA2 DEF076] events={_events(records)}")
    log(f"[QA2 DEF076] record.last_validation.outcome="
        f"{getattr(record.last_validation, 'outcome', None)}")
    log(f"[QA2 DEF076] published object_ids={obj_ids}")
    log(f"[QA2 DEF076] published evidence_ids={ev_ids}")
    log(f"[QA2 DEF076] FOREIGN object ids={foreign_obj}")
    log(f"[QA2 DEF076] FOREIGN evidence ids={foreign_ev}")
    log(f"[QA2 DEF076] attempt.draft FOREIGN object ids={foreign_att_obj}")
    log(f"[QA2 DEF076] attempt.draft FOREIGN evidence ids={foreign_att_ev}")
    log(f"[QA2 DEF076] late generation.validation.complete non-VALID={late_nonvalid}")

    # guard presence / clean result
    try:
        from app.services.generation import _world_graph_namespace_violations

        violations = _world_graph_namespace_violations(published)
        log(f"[QA2 DEF076] D1 guard present; violations on result={list(violations)}")
        guard_present = True
    except Exception as exc:  # noqa: BLE001
        log(f"[QA2 DEF076] D1 guard absent ({type(exc).__name__})")
        guard_present = False

    if foreign_obj or foreign_ev or foreign_att_obj or foreign_att_ev or late_nonvalid:
        verdict = "RED (foreign ids or late non-VALID generation.validation)"
    elif not guard_present:
        verdict = "GREEN-but-no-guard (pre-fix code path: skip semantics differ)"
    else:
        verdict = "GREEN"
    log(f"[QA2 DEF076] VERDICT: {verdict}")

    # independent forced-composer foreign-id bypass attempt (D1 guard)
    if guard_present:
        _bypass_scenario()
        _adv4502_scenario()
    else:
        log("[QA2 DEF076-BYPASS] skipped (guard absent: pre-fix checkout)")

    out = Path(os.environ.get("PD_QA2_OUT", Path(__file__).resolve().parent / "qa2_def076_probe_output.txt"))
    out.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
