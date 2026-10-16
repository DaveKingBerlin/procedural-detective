"""Phase45 FINAL re-review probe (ADV-45-01 / ADV-45-02 retest).

Hermetic, ZERO network / ZERO paid calls. Read-only w.r.t. product code.
Imports the shipped Phase45 test helpers (same fixtures) and drives the REAL
GenerationService._apply_kit_composition + the REAL publication/savegame
projections.

Savegame documents are written to BOTH this evidence dir (for the real frontend
parser probe) and the temp scratch dir.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from harness import *  # noqa: F401,F403  (repo-root-relative sys.path setup)

import test_phase45_world_compose_ownership as T  # noqa: E402

from app.services.generation import (  # noqa: E402
    _world_graph_namespace_violations,
)
from app.services.publication import serialize_published_payload  # noqa: E402
from app.services.savegame import project_savegame_v1  # noqa: E402
from app.generation import playability  # noqa: E402
from app.generation.state_machine import ValidationOutcome  # noqa: E402
from app.world.composer import KIT_BASE_OBJECT_IDS  # noqa: E402

TMP = Path(r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\pd45adv2")
TMP.mkdir(parents=True, exist_ok=True)
EVID = ROOT / "screenshots" / "evidence" / "adv45"
EVID.mkdir(parents=True, exist_ok=True)

KITS = ["apartment", "office", "hotel_suite", "warehouse", "mansion"]

FAILURES: list[str] = []


def check(label, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    if not cond:
        FAILURES.append(label)
    print(f"    [{mark}] {label}{(' :: ' + detail) if detail else ''}")
    return cond


def build_savegame(record, difficulty="medium"):
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=11, prompt="p", title="T"
        )
    )
    return payload, project_savegame_v1(
        payload, difficulty=difficulty, exported_at="2026-10-16T00:00:00Z"
    )


def foreign_scan(draft):
    obj_ids = {str(o.object_id) for o in draft.objects}
    ev_ids = {str(e.id) for e in draft.evidence}
    loc_ids = {str(l.location_id) for l in draft.world_graph.locations}
    bad = []
    for p in draft.world_graph.placements:
        if str(p.object_id) not in obj_ids:
            bad.append(("objectId", p.object_id))
        if str(p.location_id) not in loc_ids:
            bad.append(("locationId", p.location_id))
        if p.evidence_id is not None and str(p.evidence_id) not in ev_ids:
            bad.append(("evidenceId", p.evidence_id))
    return bad


def late_generation_validation(records):
    return [
        r.pd_fields.get("validationOutcome")
        for r in records
        if getattr(r, "pd_event", None) == "generation.validation.complete"
    ]


# --------------------------------------------------------------------------- #
# 1. ADV-45-01 — provider-authored skip pins the resolved kit scene + round-trip
# --------------------------------------------------------------------------- #
report("1. ADV-45-01 provider-authored SKIP: scene pin + savegame per kit")
for kit in KITS:
    def _one(kit=kit):
        ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
        canonical = record.published.draft
        svc = service()
        with capture() as recs:
            ok = svc._apply_kit_composition(record, kit, decorative(), None)
        evs = events(recs)
        skip = event(recs, "world_compose.skipped")
        reason = skip.pd_fields["reasonCode"]
        outcome = skip.pd_fields["compositionOutcome"]
        mode = skip.pd_fields["compositionMode"]
        scene = record.published.draft.scene
        print(f"\n  [{kit}] ok={ok} reason={reason} outcome={outcome} mode={mode}")
        check(f"{kit}: skip is PROVIDER_AUTHORED_WORLD", reason == "PROVIDER_AUTHORED_WORLD")
        check(
            f"{kit}: scene.environment_id == requested kit",
            getattr(scene, "environment_id", None) == kit,
            f"got={getattr(scene, 'environment_id', None)}",
        )
        check(
            f"{kit}: scene.environment_version >= 1 int",
            isinstance(getattr(scene, "environment_version", None), int)
            and scene.environment_version >= 1,
            f"got={getattr(scene, 'environment_version', None)}",
        )
        # canonical world preserved
        check(
            f"{kit}: canonical placements byte-identical",
            record.published.draft.world_graph.placements
            == canonical.world_graph.placements,
        )
        check(
            f"{kit}: canonical objects/evidence preserved",
            record.published.draft.objects == canonical.objects
            and record.published.draft.evidence == canonical.evidence,
        )
        bad = foreign_scan(record.published.draft)
        check(f"{kit}: no foreign object/evidence/location id", not bad, str(bad))
        check(
            f"{kit}: namespace guard clean",
            _world_graph_namespace_violations(record.published.draft) == (),
        )
        check(
            f"{kit}: no late generation.validation failure",
            all(v == "VALID" for v in late_generation_validation(recs)),
            str(late_generation_validation(recs)),
        )
        # scene location resolves in the declared location universe
        loc_ids = {str(l.location_id) for l in record.published.draft.world_graph.locations}
        check(
            f"{kit}: scene.location_id resolves",
            str(scene.location_id) in loc_ids,
            f"scene.location_id={scene.location_id} declared={sorted(loc_ids)}",
        )
        # Gate 3 over the persisted candidate
        g3 = playability.validate_published_candidate(record.published, "medium")
        check(f"{kit}: Gate 3 valid after skip+pin", g3.valid, str(g3.issue_codes))
        # savegame projection
        payload, sg = build_savegame(record)
        pub_scene = sg["case"]["publicCase"]["scene"]
        boot_scene = sg["case"]["scene"]
        meta = sg["case"]["metadata"]
        check(
            f"{kit}: savegame publicCase.scene.environmentId == kit",
            pub_scene.get("environmentId") == kit,
            f"got={pub_scene.get('environmentId')}",
        )
        check(
            f"{kit}: savegame publicCase.scene.environmentVersion >= 1",
            isinstance(pub_scene.get("environmentVersion"), int)
            and pub_scene["environmentVersion"] >= 1,
            f"got={pub_scene.get('environmentVersion')}",
        )
        check(
            f"{kit}: savegame bootstrap scene.environmentId == kit",
            boot_scene.get("environmentId") == kit,
        )
        check(
            f"{kit}: savegame metadata.environmentId == kit",
            meta.get("environmentId") == kit,
        )
        # persist for the TS parser
        (TMP / f"sg_skip_{kit}.pdcase.json").write_text(
            json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8"
        )
        (EVID / f"sg_skip_{kit}.pdcase.json").write_text(
            json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8"
        )
        return True

    run(f"provider-authored skip [{kit}]", _one)

# --------------------------------------------------------------------------- #
# 2. requested non-default kit reflected (explicit)
# --------------------------------------------------------------------------- #
report("2. requested non-default kit reflected (office/hotel_suite/warehouse/mansion)")
for kit in ["office", "hotel_suite", "warehouse", "mansion"]:
    def _two(kit=kit):
        ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
        svc = service()
        svc._apply_kit_composition(record, kit, decorative(), None)
        scene = record.published.draft.scene
        check(
            f"{kit}: not silently defaulted to apartment",
            scene.environment_id == kit and scene.environment_id != "apartment",
            f"got={scene.environment_id}",
        )
        payload, sg = build_savegame(record)
        check(
            f"{kit}: savegame reflects requested kit",
            sg["case"]["publicCase"]["scene"]["environmentId"] == kit,
        )
        return True

    run(f"requested kit [{kit}]", _two)

# --------------------------------------------------------------------------- #
# 3. namespace-mismatch skip pins the scene
# --------------------------------------------------------------------------- #
report("3. namespace-mismatch skip pins the requested kit scene")


def _three():
    ctrl, record = held(golden_script(), "medium")
    canonical = record.published.draft
    foreign = PlacementSpec(
        object_id="golden_foreign_object",
        asset_id="PROP_KITCHEN_KNIFE_01",
        location_id="foreign_zone_xyz",
        anchor="kitchen_counter",
        interaction="",
        evidence_id="golden_foreign_evidence",
    )
    import app.world.composer as composer_mod

    original = composer_mod.compose_world
    composer_mod.compose_world = T._fake_composition(placements=(foreign,))
    try:
        svc = service()
        with capture() as recs:
            ok = svc._apply_kit_composition(record, "office", decorative(), None)
    finally:
        composer_mod.compose_world = original
    skip = event(recs, "world_compose.skipped")
    scene = record.published.draft.scene
    print(f"  ok={ok} reason={skip.pd_fields['reasonCode']} outcome={skip.pd_fields['compositionOutcome']}")
    check("ns-mismatch: skip reason", skip.pd_fields["reasonCode"] == "COMPOSE_NAMESPACE_MISMATCH")
    check("ns-mismatch: scene pinned to requested office kit", scene.environment_id == "office", f"got={scene.environment_id}")
    check("ns-mismatch: canonical world preserved", record.published.draft.world_graph.placements == canonical.world_graph.placements)
    check("ns-mismatch: no foreign id", not foreign_scan(record.published.draft))
    check("ns-mismatch: no world_compose.validation event", "world_compose.validation.started" not in events(recs))
    check("ns-mismatch: no late generation.validation failure", all(v == "VALID" for v in late_generation_validation(recs)))
    payload, sg = build_savegame(record)
    check("ns-mismatch: savegame publicCase.scene == office", sg["case"]["publicCase"]["scene"]["environmentId"] == "office")
    (TMP / "sg_ns_mismatch_office.pdcase.json").write_text(json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8")
    (EVID / "sg_ns_mismatch_office.pdcase.json").write_text(json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8")
    return True


run("namespace-mismatch skip", _three)

# --------------------------------------------------------------------------- #
# 4. fallback paths pin the scene (COMPOSE_ISSUES, Gate3 regression)
# --------------------------------------------------------------------------- #
report("4. fallback paths pin the scene")


def _four_issues():
    ctrl, record = held(golden_script(), "medium")
    canonical = record.published.draft
    import app.world.composer as composer_mod

    original = composer_mod.compose_world
    composer_mod.compose_world = T._fake_composition(issues=("world.invalid-placement: synthetic",))
    try:
        svc = service()
        with capture() as recs:
            ok = svc._apply_kit_composition(record, "office", decorative(), None)
    finally:
        composer_mod.compose_world = original
    fb = event(recs, "world_compose.fallback")
    scene = record.published.draft.scene
    print(f"  ok={ok} reason={fb.pd_fields['reasonCode']}")
    check("COMPOSE_ISSUES: reason", fb.pd_fields["reasonCode"] == "COMPOSE_ISSUES")
    check("COMPOSE_ISSUES: scene pinned office", scene.environment_id == "office", f"got={scene.environment_id}")
    check("COMPOSE_ISSUES: canonical world preserved", record.published.draft.world_graph.placements == canonical.world_graph.placements)
    check("COMPOSE_ISSUES: no late generation.validation failure", all(v == "VALID" for v in late_generation_validation(recs)))
    return True


run("COMPOSE_ISSUES fallback", _four_issues)


def _four_gate3():
    ctrl, record = held(golden_script(), "medium")
    canonical = record.published.draft
    import app.world.composer as composer_mod

    original = composer_mod.compose_world
    composer_mod.compose_world = T._fake_composition(placements=())
    try:
        svc = service()
        with capture() as recs:
            ok = svc._apply_kit_composition(record, "office", decorative(), None)
    finally:
        composer_mod.compose_world = original
    fb = event(recs, "world_compose.fallback")
    scene = record.published.draft.scene
    print(f"  ok={ok} reason={fb.pd_fields['reasonCode']}")
    check("GATE3_REG: reason", fb.pd_fields["reasonCode"] == "COMPOSE_GATE3_REGRESSION")
    check("GATE3_REG: scene pinned office", scene.environment_id == "office", f"got={scene.environment_id}")
    check("GATE3_REG: canonical world preserved", record.published.draft.world_graph.placements == canonical.world_graph.placements)
    g3 = playability.validate_published_candidate(record.published, "medium")
    check("GATE3_REG: persisted candidate Gate 3 valid", g3.valid, str(g3.issue_codes))
    return True


run("COMPOSE_GATE3_REGRESSION fallback", _four_gate3)

# --------------------------------------------------------------------------- #
# 5. unsupported kit (exception path) — scene pin?
# --------------------------------------------------------------------------- #
report("5. unsupported kit (planet_mars) exception path")


def _five():
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    canonical = record.published.draft
    svc = service()
    with capture() as recs:
        ok = svc._apply_kit_composition(record, "planet_mars", decorative(), None)
    failed = event(recs, "world_compose.failed")
    scene = record.published.draft.scene
    print(f"  ok={ok} reason={failed.pd_fields['reasonCode']}")
    check("unsupported: COMPOSE_EXCEPTION", failed.pd_fields["reasonCode"] == "COMPOSE_EXCEPTION")
    check("unsupported: canonical world restored", record.published.draft.world_graph.placements == canonical.world_graph.placements)
    check("unsupported: no late generation.validation failure", all(v == "VALID" for v in late_generation_validation(recs)))
    print(f"  scene.environment_id={getattr(scene, 'environment_id', None)}")
    payload, sg = build_savegame(record)
    env = sg["case"]["publicCase"]["scene"]["environmentId"]
    print(f"  savegame publicCase.scene.environmentId={env!r}")
    check("unsupported: savegame scene env non-null (production reachability caveat)", env is not None and env != "")
    (TMP / "sg_unsupported.pdcase.json").write_text(json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8")
    (EVID / "sg_unsupported.pdcase.json").write_text(json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8")
    return True


run("unsupported kit", _five)

# --------------------------------------------------------------------------- #
# 6. §43 scenarios 10/15/16/20 + Gate3 / placements / pin edges
# --------------------------------------------------------------------------- #
report("6. §43 scenarios 10/15/16/20 + scene-pin edges")


def _six_scenario10():
    # composed apartment savegame round-trip
    ctrl, record = held(golden_script(), "medium")
    svc = service()
    svc._apply_kit_composition(record, "apartment", decorative(), None)
    payload, sg = build_savegame(record)
    scene = sg["case"]["publicCase"]["scene"]
    print(f"  composed apartment publicCase.scene={json.dumps(scene, sort_keys=True)}")
    check("s10 composed: env apartment", scene["environmentId"] == "apartment")
    check("s10 composed: version >= 1", isinstance(scene["environmentVersion"], int) and scene["environmentVersion"] >= 1)
    check("s10 composed: world objects > 0", len(sg["case"]["scene"]["worldObjects"]) > 0)
    (TMP / "sg_composed_apartment.pdcase.json").write_text(json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8")
    (EVID / "sg_composed_apartment.pdcase.json").write_text(json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8")
    return True


def _six_scenario16():
    # repeated composition idempotent on skip path
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    svc = service()
    svc._apply_kit_composition(record, "office", decorative(), None)
    sig1 = T._placement_signature(record.published.draft)
    svc._apply_kit_composition(record, "office", decorative(), None)
    sig2 = T._placement_signature(record.published.draft)
    check("s16 skip: idempotent placements", sig1 == sig2)
    # composer path idempotent
    ctrl2, record2 = held(golden_script(), "medium")
    svc2 = service()
    svc2._apply_kit_composition(record2, "office", decorative(), None)
    a = T._placement_signature(record2.published.draft)
    svc2._apply_kit_composition(record2, "office", decorative(), None)
    b = T._placement_signature(record2.published.draft)
    check("s16 compose: idempotent placements", a == b)
    return True


def _six_scenario20():
    # all critical dimensions reachable after skip+pin
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    svc = service()
    svc._apply_kit_composition(record, "office", decorative(), None)
    g3 = playability.validate_published_candidate(record.published, "medium")
    check("s20 skip+pin: Gate 3 valid", g3.valid, str(g3.issue_codes))
    check("s20 skip+pin: no unreachable critical dim", not g3.unreachable_critical_dimensions, str(g3.unreachable_critical_dimensions))
    return True


def _six_pin_edges():
    # does the pin reintroduce a foreign location/placement id? change placements?
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    canonical = record.published.draft
    svc = service()
    svc._apply_kit_composition(record, "office", decorative(), None)
    check("pin edge: placements unchanged (identity)", record.published.draft.world_graph.placements is canonical.world_graph.placements or record.published.draft.world_graph.placements == canonical.world_graph.placements)
    check("pin edge: locations unchanged", record.published.draft.world_graph.locations == canonical.world_graph.locations)
    check("pin edge: no foreign ids", not foreign_scan(record.published.draft))
    check("pin edge: scene.location_id unchanged by pin", record.published.draft.scene.location_id == canonical.scene.location_id, f"{record.published.draft.scene.location_id} vs {canonical.scene.location_id}")
    # Gate 3 comparison before vs after
    before = playability.validate_published_candidate(record, "medium") if hasattr(record, "truth") else None
    return True


for name, fn in [
    ("s10 save/reload", _six_scenario10),
    ("s16 repeated composition", _six_scenario16),
    ("s20 critical dimensions reachable", _six_scenario20),
    ("scene-pin edges", _six_pin_edges),
]:
    run(name, fn)

# --------------------------------------------------------------------------- #
# 7. ADV-45-02 classification
# --------------------------------------------------------------------------- #
report("7. ADV-45-02 classification")


def _seven():
    ctrl, record = held(renamed_script(), "medium", prompt=T.PROVIDER_PROMPT)
    svc = service()
    with capture() as recs:
        ok = svc._apply_kit_composition(record, "apartment", T.WorldRequirements(), None)
    skip = event(recs, "world_compose.skipped")
    print(f"  provider-authored apartment, empty reqs: reason={skip.pd_fields['reasonCode']} mode={skip.pd_fields['compositionMode']}")
    check("adv45-02: reason PROVIDER_AUTHORED_WORLD", skip.pd_fields["reasonCode"] == "PROVIDER_AUTHORED_WORLD")
    check("adv45-02: mode PROVIDER_AUTHORED", skip.pd_fields["compositionMode"] == "PROVIDER_AUTHORED")
    # control: golden apartment, empty reqs -> APARTMENT_BYTE_IDENTITY
    ctrl2, record2 = held(golden_script(), "medium")
    svc2 = service()
    with capture() as recs2:
        svc2._apply_kit_composition(record2, "apartment", T.WorldRequirements(), None)
    skip2 = event(recs2, "world_compose.skipped")
    print(f"  golden apartment, empty reqs: reason={skip2.pd_fields['reasonCode']}")
    check("adv45-02 control: golden stays APARTMENT_BYTE_IDENTITY", skip2.pd_fields["reasonCode"] == "APARTMENT_BYTE_IDENTITY")
    return True


run("ADV-45-02 classification", _seven)

# --------------------------------------------------------------------------- #
print("\n" + "=" * 78)
print("SUMMARY")
print("=" * 78)
if FAILURES:
    print(f"FAILURES ({len(FAILURES)}):")
    for f in FAILURES:
        print("  -", f)
else:
    print("ALL CHECKS PASSED")
print(f"savegames written to {TMP} and {EVID}")
