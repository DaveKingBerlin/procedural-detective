"""Phase45 FINAL retest (QA-2) — INDEPENDENT verification of ADV-45-01 / DEF-109.

ZERO network / ZERO paid calls. Read-only w.r.t. product source.

This probe is authored by QA (NOT a copy of the shipped Phase45 test). It drives
the REAL ``GenerationController`` (FakeProvider golden + a renamed-namespace
provider script), the REAL ``GenerationService._apply_kit_composition``, the REAL
``serialize_published_payload`` / ``project_savegame_v1`` projections, and
asserts on the raw published/savegame data (no reliance on the implementer's
test assertions).

Set ``PD_ROOT`` to point at a different checkout (e.g. a PRE-FIX worktree) to
reproduce the red baseline. Defaults to this repo.

Outputs (into the same directory as this file, or ``--out``):
  - ``qa2_def109_evidence.txt``      human-readable evidence
  - ``qa2_skipped_<env>.pdcase.json`` real exported savegames for the frontend
                                      parser round-trip probe
"""
from __future__ import annotations

import argparse
import dataclasses
import json
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
from app.services.publication import serialize_published_payload  # noqa: E402
from app.services.savegame import project_savegame_v1  # noqa: E402
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


def _dump(doc) -> str:
    return json.dumps(doc, sort_keys=True)


def _renamed_script(prefix_obj: str = "prov_obj_", prefix_ev: str = "prov_ev_") -> dict:
    """Golden provider stages with a DISJOINT object/evidence namespace."""
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


def _held(script: dict, difficulty: str = "medium"):
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
        difficulty=difficulty,
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


def _decorative() -> WorldRequirements:
    return WorldRequirements(objects=(ObjectRequest(requested_name="mystery gadget"),))


def _fail(env: str, msg: str) -> None:
    log(f"[FAIL] env={env}: {msg}")


def run_environment(env: str, out_dir: Path) -> bool:
    log("")
    log("=" * 78)
    log(f"[QA-2 DEF-109] provider-authored SKIP with requested kit = {env!r}")
    log("=" * 78)
    ok = True
    _ctrl, record = _held(_renamed_script(), "medium")
    before_objects = tuple(o.object_id for o in record.published.draft.objects)
    before_evidence = tuple(e.id for e in record.published.draft.evidence)
    before_placements = record.published.draft.world_graph.placements

    svc = _service()
    composed = svc._apply_kit_composition(record, env, _decorative(), None)

    scene = record.published.draft.scene
    log(f"  apply_kit_composition returned: {composed}")
    log(f"  PUBLISHED draft.scene.environment_id={scene.environment_id!r} "
        f"environment_version={scene.environment_version!r} name={scene.name!r}")

    if scene.environment_id != env:
        _fail(env, f"scene.environment_id {scene.environment_id!r} != {env!r}")
        ok = False
    if not isinstance(scene.environment_version, int) or scene.environment_version < 1:
        _fail(env, f"scene.environment_version not a positive int: {scene.environment_version!r}")
        ok = False

    # no foreign ids / canonical world preserved
    if tuple(o.object_id for o in record.published.draft.objects) != before_objects:
        _fail(env, "objects changed on skip")
        ok = False
    if tuple(e.id for e in record.published.draft.evidence) != before_evidence:
        _fail(env, "evidence changed on skip")
        ok = False
    if record.published.draft.world_graph.placements != before_placements:
        _fail(env, "placements changed on skip")
        ok = False
    if not all(o.startswith("prov_obj_") for o in before_objects):
        _fail(env, "provider namespace not as expected")
        ok = False

    # PUBLISHED payload (the exact serialized wire document)
    payload = json.loads(
        serialize_published_payload(record.published, seed=11, prompt="p", title="QA2")
    )
    pscene = payload["draft"]["scene"]
    log(f"  PUBLISHED payload draft.scene="
        f"{json.dumps({k: pscene.get(k) for k in ('environment_id','environment_version','name')}, sort_keys=True)}")
    if pscene.get("environment_id") != env:
        _fail(env, f"payload scene.environment_id {pscene.get('environment_id')!r} != {env!r}")
        ok = False

    # exported savegame (the real handler projection)
    doc = project_savegame_v1(
        payload, difficulty="medium", exported_at="2026-10-16T00:00:00Z"
    )
    pub_scene = doc["case"]["publicCase"]["scene"]
    boot_scene = doc["case"]["scene"]
    meta_env = doc["case"]["metadata"].get("environmentId")
    log(f"  SAVEGAME publicCase.scene={json.dumps(pub_scene, sort_keys=True)}")
    log(f"  SAVEGAME case.scene.environmentId={boot_scene.get('environmentId')!r} "
        f"environmentVersion={boot_scene.get('environmentVersion')!r}")
    log(f"  SAVEGAME case.metadata.environmentId={meta_env!r}")

    # mirrors frontend requireBoundedString / requireBoundedInteger
    if not (isinstance(pub_scene.get("environmentId"), str) and pub_scene["environmentId"]):
        _fail(env, f"publicCase.scene.environmentId not a non-empty string: {pub_scene.get('environmentId')!r}")
        ok = False
    if not (isinstance(pub_scene.get("environmentVersion"), int) and pub_scene["environmentVersion"] >= 1):
        _fail(env, f"publicCase.scene.environmentVersion not positive int: {pub_scene.get('environmentVersion')!r}")
        ok = False

    out = out_dir / f"qa2_skipped_{env}.pdcase.json"
    out.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
    log(f"  wrote savegame evidence: {out}")
    log(f"  RESULT env={env}: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    out_dir = Path(args.out) if args.out else Path(__file__).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"[QA-2 DEF-109] PD_ROOT={ROOT}")
    log(f"[QA-2 DEF-109] python={sys.version.split()[0]}")

    results = {
        env: run_environment(env, out_dir)
        for env in ("apartment", "office", "mansion", "warehouse", "hotel_suite")
    }

    log("")
    log("=" * 78)
    log("[QA-2 DEF-109] SUMMARY")
    log("=" * 78)
    for env, ok in results.items():
        log(f"  {env:12s}: {'PASS' if ok else 'FAIL'}")
    all_ok = all(results.values())
    log(f"[QA-2 DEF-109] OVERALL: {'PASS' if all_ok else 'FAIL'}")

    (out_dir / "qa2_def109_evidence.txt").write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
