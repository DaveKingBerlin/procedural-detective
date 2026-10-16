"""ADV-45FIX-01 re-verification (Phase45 Fix adversarial, 2nd pass).

Hermetic, ZERO network. Drives the REAL GenerationService._publish_if_ready
safety net on a frozen candidate whose scene.environmentId is None (the latent
ADV-45-03 shape), forcing every resolvable kit via _last_environment_resolution,
then checks:
  - the published candidate carries a non-null pinned kit scene;
  - the exported savegame -> project_savegame_v1 document has a non-empty
    environmentId + positive environmentVersion (fed to the REAL frontend
    parseSavegameV1 by the companion TS probe);
  - unpinnable variants FAIL CLOSED (no publish, no payload, FAILED).

NOTE: the review request specified a temp dir outside this workspace; the
runtime edit policy permits only screenshots/evidence/*, so the probes live
durably under screenshots/evidence/adv45fix/adv2/ (adversary-owned evidence).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADV = HERE.parent
REPO = ADV.parents[2]
sys.path.insert(0, str(ADV))
OUT = HERE

from harness import (  # noqa: E402
    capture_events,
    completion_script,
    events,
    golden_script,
    guard_stub,
    held,
    playability,
    publish_via_guard,
    service,
    WorldRequirements,
)

from app.environments.manifests import load_all_environments  # noqa: E402
from app.generation.schemas import SceneSpec  # noqa: E402
from app.services.publication import serialize_published_payload  # noqa: E402
from app.services.savegame import project_savegame_v1  # noqa: E402

KITS = [k.environment_id for k in load_all_environments()]
print("kits:", KITS)

results: dict[str, bool] = {}


def record(name: str, ok: bool, detail: str = "") -> None:
    results[name] = ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def scene_env(published):
    sc = getattr(getattr(published, "draft", None), "scene", None)
    return (
        getattr(sc, "environment_id", None),
        getattr(sc, "environment_version", None),
        getattr(sc, "location_id", None),
        getattr(sc, "name", None),
    )


def build_sg_document(published, difficulty):
    payload = json.loads(
        serialize_published_payload(published, seed=11, prompt="p", title="T")
    )
    return payload, project_savegame_v1(
        payload, difficulty=difficulty, exported_at="2026-10-16T00:00:00Z"
    )


def main() -> int:
    print("=" * 78)
    print("ADV-45FIX-01 — kit scene pin (all kits) + fail-closed unpinnable variants")
    print("=" * 78)

    sg_files: list[str] = []
    for kit in KITS:
        _c, rec = held(completion_script(), "medium")
        raw_env = getattr(rec.published.draft.scene, "environment_id", "MISSING")
        if raw_env is not None:
            record(f"setup:{kit}:frozen-scene-null", False, f"raw_env={raw_env!r}")
            continue
        stub = guard_stub()
        stub._last_environment_resolution = {"environmentId": kit}
        with capture_events() as records:
            status = publish_via_guard(stub, rec, "medium")
        evs = events(records)
        env_id, env_ver, loc_id, name = scene_env(stub._publication.published)
        pinned_ok = (
            status == "PUBLISHED"
            and stub._publication.calls == 1
            and env_id == kit
            and isinstance(env_ver, int)
            and env_ver >= 1
        )
        record(
            f"pin:{kit}",
            pinned_ok,
            f"status={status} env={env_id!r} ver={env_ver!r} loc={loc_id!r} "
            f"repaired_event={'playability.validation.repaired' in evs}",
        )
        g3 = playability.validate_published_candidate(
            stub._publication.published, "medium"
        )
        record(f"pin:{kit}:gate3-valid", g3.valid, f"issues={g3.issue_codes}")
        _payload, sg = build_sg_document(stub._publication.published, "medium")
        public_scene = sg["case"]["publicCase"]["scene"]
        ok_scene = (
            isinstance(public_scene, dict)
            and isinstance(public_scene.get("environmentId"), str)
            and public_scene["environmentId"] != ""
            and isinstance(public_scene.get("environmentVersion"), int)
            and public_scene["environmentVersion"] >= 1
        )
        record(f"pin:{kit}:savegame-contract", ok_scene, f"scene={public_scene}")
        fname = f"sg_adv2_{kit}.pdcase.json"
        (OUT / fname).write_text(
            json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8"
        )
        sg_files.append(fname)

    # ---- unpinnable variants -------------------------------------------------
    import app.environments.compose as compose

    def _unpinnable(mode):
        _c, rec = held(completion_script(), "medium")
        stub = guard_stub()
        original = compose.scene_for_kit

        if mode == "raise":

            def boom(*_a, **_k):
                raise RuntimeError("scene cannot be resolved")

            compose.scene_for_kit = boom
        elif mode == "null-env":
            compose.scene_for_kit = lambda kit, cur: SceneSpec(
                location_id="miller_apartment_kitchen",
                name="x",
                environment_id=None,
                environment_version=1,
            )
        elif mode == "null-ver":
            compose.scene_for_kit = lambda kit, cur: SceneSpec(
                location_id="miller_apartment_kitchen",
                name="x",
                environment_id="apartment",
                environment_version=None,
            )
        elif mode == "kit-unresolvable":
            import app.environments.manifests as manifests

            original_load = manifests.load_environment

            def boom_load(*_a, **_k):
                raise RuntimeError("no kit")

            manifests.load_environment = boom_load
        try:
            with capture_events() as records:
                status = publish_via_guard(stub, rec, "medium")
        finally:
            compose.scene_for_kit = original
            if mode == "kit-unresolvable":
                manifests.load_environment = original_load
        evs = events(records)
        closed = (
            status is None
            and stub._publication.calls == 0
            and stub._publication.published is None
            and rec.state.name == "FAILED"
            and "playability.validation.failed" in evs
            and "publication.started" not in evs
        )
        record(
            f"fail-closed:{mode}",
            closed,
            f"status={status} calls={stub._publication.calls} state={rec.state.name} "
            f"events={evs}",
        )

    for mode in ("raise", "null-env", "null-ver", "kit-unresolvable"):
        _unpinnable(mode)

    # ---- control: a normal (non-null scene) authoritative run still works ----
    # Mirrors the REAL non-driver production path: generation -> _apply_kit_
    # composition (scene pin) -> publication guard.
    _c, rec = held(golden_script(), "medium")
    svc = service()
    svc._apply_kit_composition(rec, "apartment", WorldRequirements(), None)
    stub = guard_stub()
    status = publish_via_guard(stub, rec, "medium")
    env_id, env_ver, _loc, _name = scene_env(stub._publication.published)
    record(
        "control:golden-composed-publishes-with-kit-scene",
        status == "PUBLISHED" and env_id is not None,
        f"status={status} env={env_id!r} ver={env_ver!r}",
    )

    # ---- pre-existing non-repair seam (out of scope, expected unchanged) -----
    # A Gate-3-VALID frozen candidate with a null scene never enters the safety
    # net (the net only runs on Gate 3 failure), so the guard publishes it
    # unpinned. Production never reaches this shape: the non-driver path always
    # runs _apply_kit_composition (which pins the scene); the driver path pins
    # scene_for_kit inside the controller. Demonstrated to confirm the residual
    # boundary is the repair path only.
    _c, rec = held(golden_script(), "medium")
    g3 = playability.validate_published_candidate(rec.published, "medium")
    stub = guard_stub()
    status = publish_via_guard(stub, rec, "medium")
    env_id, _ver, _loc, _name = scene_env(stub._publication.published)
    record(
        "boundary:gate3-valid-null-scene-not-repaired(preexisting,nonproduction)",
        status == "PUBLISHED" and env_id is None and g3.valid,
        f"status={status} env={env_id!r} gate3.valid={g3.valid}",
    )

    passed = sum(1 for v in results.values() if v)
    print(f"\nADV-45FIX-01: {passed}/{len(results)} checks passed")
    print("savegame files written:", sg_files)
    (OUT / "sg_adv2_manifest.json").write_text(
        json.dumps(sg_files), encoding="utf-8"
    )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())