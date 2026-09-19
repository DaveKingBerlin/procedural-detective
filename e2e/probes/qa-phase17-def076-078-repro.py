"""QA retest probe for ADV-201/202/203 -> DEF-076/077/078 (phase-17 gate).

In-process, REAL repo modules, zero product modification. This probe carries
the ORIGINAL reproduction scenarios (fullwidth part id, 3.9m single-part
plinth, "Ritual Blade" knife) and now VERIFIES the FIXED behaviors reported
FIX READY by the developer:

  DEF-076 (ADV-201): `part_０１` is REJECTED cleanly at Phase 13 parse with an
    ASCII message (never validated), direct AssetSpecPart construction raises,
    the compiler count ALWAYS equals the spec count (a monkeypatched guard
    raises the typed AssetSpecCompileError with "refused to drop"), and the
    driver/spec-provider PUBLISHED 3->2-drop scenario is IMPOSSIBLE (the
    candidate is structurally invalid -> repair path, never partial geometry).
  DEF-077 (ADV-202): the single-part {3.9^3} plinth FAILS the geometry gate
    with COMPOSITE_BOUNDS_MISMATCH (single-part axes messages), the driver
    repairs -> PUBLISHED (repaired true), a legitimate single-part object
    (declared ~1.5x visible span) PASSES with a bounded clickable hitbox in
    [HITBOX_MIN, visible_span*HITBOX_VISIBLE_MAX_RATIO], and even a bypassed
    gate clamps the compiled hitbox to <= visible_span*2.0.
  DEF-078 (ADV-203): "Ritual Blade" now trips the hand-held gate
    (is_handheld/silhouette_relevant true; a single-sphere blade fails
    SILHOUETTE_HEURISTIC and repairs through the driver), "One-Axis Stick"
    still passes, and "cleaver" normalizes to (decor, kitchen_knife).

Run:  python e2e/probes/qa-phase17-def076-078-repro.py
Exit: 0 = ALL FIXED BEHAVIORS VERIFIED, 1 = any verification failed / probe error.
Evidence: e2e/artifacts/qa-phase17-def076-078-repro.json
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "backend" / "tests"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

RESULT: dict[str, object] = {}
CHECKS: list[tuple[str, bool]] = []


def check(label: str, ok: bool) -> None:
    CHECKS.append((label, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")


def dump(label: str, obj: object) -> None:
    RESULT[label] = obj
    print(f"\n===== {label} =====")
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _verdict(label: str, obj: object) -> None:
    RESULT[label] = obj


# --------------------------------------------------------------------------- #
# ADV-201 / DEF-076
# --------------------------------------------------------------------------- #
def spir() -> dict:
    return {
        "canonicalName": "Bronze Ceremonial Ice Pick",
        "category": "decor",
        "subtype": "ceremonial_ice_pick",
        "dimensions": {"x": 0.12, "y": 0.5, "z": 0.1},
        "parts": [
            {"id": "part_00", "role": "shaft", "primitive": "cylinder",
             "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.05, "y": 0.18, "z": 0.05}},
             "material": "metal.brass"},
            {"id": "part_\uff10\uff11", "role": "point", "primitive": "box",
             "transform": {"position": {"x": 0.0, "y": 0.2, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.05, "y": 0.05, "z": 0.05}},
             "material": "metal.brass"},
            {"id": "part_02", "role": "handle", "primitive": "box",
             "transform": {"position": {"x": 0.0, "y": -0.19, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.05, "y": 0.06, "z": 0.05}},
             "material": "wood.dark"},
        ],
    }


def spir_fixed() -> dict:
    """The corrected repair candidate (ASCII part_01) for the DEF-076 driver
    round-trip: same geometry, id `part_01`."""
    fixed = copy.deepcopy(spir())
    fixed["parts"][1]["id"] = "part_01"
    return fixed


def repro_adv201() -> None:
    from app.assets.specs import (
        AssetSpecPart,
        AssetSpecError,
        SpecTransform,
        parse_asset_spec,
        validate_asset_spec,
    )
    from app.assets.geometry_quality import validate_geometry
    from app.assets.compiler import compile_asset_spec, AssetSpecCompileError
    import app.assets.compiler as compiler_mod
    from app.assets.spec_provider import AssetSpecRequest
    from app.assets.spec_provider import AssetSpecResponse
    from app.services.ollama_driver import OllamaAssetSpecProvider
    from app.generation.provider import ProviderResult

    # ---- 1. Phase 13 clean rejection (ASCII message) ----------------------
    raw = spir()
    phase13 = validate_asset_spec(raw)
    dump("DEF-076 Phase13 issues (expect rejection w/ ASCII message)",
         list(phase13))
    check("DEF-076 Phase13 non-empty rejection",
          bool(phase13) and any("ASCII" in i for i in phase13))

    all_rejected_ids = ("part_\uff10\uff11", "part_0\uff11", "part_\uff11" "0")
    for bad_id in ("part_\uff10\uff11", "part_0\uff11", "part_\uff11" "0"):
        variant = copy.deepcopy(raw)
        variant["parts"][1]["id"] = bad_id
        issues = validate_asset_spec(variant)
        try:
            parse_asset_spec(variant, non_throwing=False)
            raises = False
        except AssetSpecError as exc:
            raises = True
            msg = str(exc)
        check(
            f"DEF-076 {bad_id!r} parse rejection (ASCII message)",
            bool(issues) and raises and "ASCII" in msg,
        )
    parse_none = parse_asset_spec(raw, non_throwing=True)
    check("DEF-076 non_throwing parse returns None", parse_none is None)
    try:
        parse_asset_spec(raw, non_throwing=False)
        raises_forced = False
    except AssetSpecError as exc:
        raises_forced = True
        forced_msg = str(exc)
    check(
        "DEF-076 non_throwing=False raises AssetSpecError (clean ASCII message)",
        raises_forced and "ASCII" in forced_msg,
    )
    _verdict(
        "DEF-076 parse rejection message",
        forced_msg if raises_forced else None,
    )

    # ---- 2. Direct AssetSpecPart construction raises -----------------------
    try:
        AssetSpecPart(
            id="part_\uff10\uff11",
            role="shaft",
            primitive="cylinder",
            transform=SpecTransform(
                position=(0.0, 0.0, 0.0),
                rotation=(0.0, 0.0, 0.0),
                scale=(0.05, 0.18, 0.05),
            ),
            material="metal.brass",
        )
        direct_raises = False
    except AssetSpecError as exc:
        direct_raises = True
        direct_msg = str(exc)
    check(
        "DEF-076 direct AssetSpecPart(id=fullwidth) raises AssetSpecError",
        direct_raises and "ASCII" in direct_msg,
    )

    # ---- 3. Compile count ALWAYS equals spec count (monkeypatched guard) ---
    good_raw = spir_fixed()
    good = parse_asset_spec(good_raw, non_throwing=False)
    compiled_good = compile_asset_spec(good)
    check(
        "DEF-076 valid spec compiles 3==3 parts",
        len(compiled_good.parts) == len(good.parts) == 3,
    )

    # Forge the (now-unreachable-via-parse) collision: the fixed ASCII sequence
    # is missing part_05 while the validated spec declares it.
    six = copy.deepcopy(spir_fixed())
    for i in range(3, 6):
        six["parts"].append(copy.deepcopy(six["parts"][2]))
        six["parts"][-1]["id"] = f"part_{i:02d}"
        six["parts"][-1]["transform"]["position"]["y"] = -0.19 - 0.1 * (i - 3)
    orig_ids = compiler_mod._PART_IDS
    compiler_mod._PART_IDS = tuple(
        pid for pid in orig_ids if pid != "part_05"
    )
    try:
        spec6 = parse_asset_spec(six, non_throwing=False)
        assert validate_asset_spec(six) == ()
        guard_raises = False
        guard_msg = ""
        guard_is_subclass = False
        try:
            compile_asset_spec(spec6)
        except AssetSpecCompileError as exc:
            guard_raises = True
            guard_msg = str(exc)
            guard_is_subclass = isinstance(exc, AssetSpecError)
        check(
            "DEF-076 compiler count guard raises typed AssetSpecCompileError "
            "('refused to drop')",
            guard_raises
            and "refused to drop" in guard_msg
            and "unmappable" in guard_msg,
        )
        check("DEF-076 guard error is an AssetSpecError subclass",
              guard_is_subclass)
        _verdict("DEF-076 compile guard message",
                 guard_msg if guard_raises else None)
    finally:
        compiler_mod._PART_IDS = orig_ids

    # ---- 4. Driver/spec-provider: PUBLISHED 3->2-drop is IMPOSSIBLE --------
    # The fullwidth candidate is structurally invalid, so the provider enters
    # the bounded repair path (never publishes partial geometry). Serving the
    # corrected spec as the repair content yields a REPAIRED accept with all
    # three parts.
    class Scripted:
        def __init__(self, contents):
            self.contents = list(contents)
            self.calls = 0

        def generate(self, request):
            self.calls += 1
            content = self.contents.pop(0) if self.contents else "<not-json>"
            return ProviderResult(content=content)

    served = Scripted([
        json.dumps(spir(), ensure_ascii=False),
        json.dumps(spir_fixed(), ensure_ascii=False),
    ])
    provider = OllamaAssetSpecProvider(
        provider=served, attempt_id="qa-def076", budget_consumer=lambda: True)
    result = provider.generate(
        AssetSpecRequest(requested_name="bronze ceremonial ice pick",
                         category_hint="decor"))
    outcome: dict[str, object] = {
        "error": result.error,
        "providerCalls": served.calls,
        "repaired": provider.last_geometry_metrics.get("repaired"),
        "issueCountBeforeRepair": provider.last_geometry_metrics.get("issueCountBeforeRepair"),
        "finalPartCount": provider.last_geometry_metrics.get("finalPartCount"),
    }
    if result.error is None and result.content is not None:
        accepted = parse_asset_spec(result.content, non_throwing=False)
        cd = compile_asset_spec(accepted)
        outcome.update({
            "assetId": cd.asset_id,
            "validatedParts": len(accepted.parts),
            "compiledPartIds": [p.id for p in cd.parts],
        })
    dump("DEF-076 driver (spec provider) outcome", outcome)
    # The drop scenario (validated 3 -> compiled 2) is impossible: the bad
    # spec never reaches Phase 17 (structurally invalid), so the provider must
    # repair (served corrected content) and every compiled count == validated.
    check(
        "DEF-076 every parsed->compiled count matches (no silent drop)",
        result.error is None
        and outcome["validatedParts"] == 3
        and len(outcome["compiledPartIds"]) == 3,
    )
    check(
        "DEF-076 drop scenario repaired via bounded ASSET_SPEC_REPAIR",
        outcome["repaired"] is True,
    )

    # Full REAL driver/controller path: with the fullwidth spec as the ASSET_SPEC
    # output, the run either repairs or fails safely — it can NEVER publish a
    # 2-part definition behind a 3-part validated geometry.
    from app.generation.clock import ManualClock
    from app.generation.controller import GenerationController
    from app.generation.ids import IdSource
    from app.generation.admission import AdmissionController
    from app.generation.ollama_provider import OllamaProvider
    from app.services.ollama_driver import OllamaStageDriver
    import test_ollama_driver as T

    class FullTransport:
        def __init__(self, posts):
            self.posts = list(posts)
            self.post_calls = []

        def post_json(self, url, payload, timeout):
            self.post_calls.append((url, payload))
            content = self.posts.pop(0) if self.posts else "<not-json>"
            return 200, json.dumps(
                {"model": "llama3.2:3b",
                 "message": {"role": "assistant", "content": content}}
            ).encode()

        def get(self, url, timeout):
            return 200, json.dumps({"models": [{"name": "llama3.2:3b"}]}).encode()

        @property
        def call_count(self):
            return len(self.post_calls)

    transport2 = FullTransport([
        T._j(T._case_people()), T._j(T._evidence()), T._j(T._world()),
        json.dumps(spir(), ensure_ascii=False),
        json.dumps(spir_fixed(), ensure_ascii=False),
    ])
    clock, ids = ManualClock(), IdSource()
    admission = AdmissionController(
        clock=clock, ids=ids, max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()

    def factory():
        return OllamaProvider(base_url="http://127.0.0.1:11434",
                              model="llama3.2:3b", timeout_seconds=5,
                              transport=transport2)

    driver = OllamaStageDriver(
        settings=__import__("app.core.config", fromlist=["Settings"]).Settings(),
        provider_factory=factory)
    controller = GenerationController(
        provider=factory(), admission=admission, clock=clock, ids=ids,
        stage_driver=driver, deadline_seconds=60, max_llm_calls_per_generation=8,
        max_repair_passes=2, max_full_regenerations=1, max_prompt_chars=4000, seed=11)
    handle = controller.start_generation(T.PROMPT, anonymous_quota_session_id=session.session_id)
    rec = controller.attempt(handle.attempt_id)
    full_outcome: dict[str, object] = {"state": rec.state.value}
    if rec.state.value == "PUBLISHED":
        placement = next(
            p for p in rec.published.draft.world_graph.placements
            if p.asset_id.startswith("proc.")
        )
        gd = placement.generated_definition
        parts_json = None
        if gd is not None:
            gd_mapping = dict(gd) if hasattr(gd, "items") else gd
            parts_json = gd_mapping.get("parts") if isinstance(gd_mapping, dict) else None
        full_outcome.update({
            "transportCalls": transport2.call_count,
            "repairPrompts": sum(1 for _, p in transport2.post_calls
                                 if "asset_spec_repair_v1" in str(p)),
            "placementAssetId": placement.asset_id,
            "publishedPartIds":
                [pt.get("id") for pt in parts_json] if parts_json is not None else None,
            "publishedPartCount": len(parts_json) if parts_json is not None else None,
        })
    dump("DEF-076 full driver PUBLISHED (drop impossible)", full_outcome)
    check(
        "DEF-076 full driver: never publishes 3->2 (state PUBLISHED with 3 parts)",
        full_outcome.get("state") == "PUBLISHED"
        and full_outcome.get("publishedPartCount") == 3,
    )


# --------------------------------------------------------------------------- #
# ADV-202 / DEF-077
# --------------------------------------------------------------------------- #
def plinth() -> dict:
    return {
        "canonicalName": "Rough Plinth",
        "category": "decor",
        "subtype": None,
        "dimensions": {"x": 3.9, "y": 3.9, "z": 3.9},
        "parts": [
            {"id": "part_00", "role": "block", "primitive": "box",
             "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.5, "y": 0.5, "z": 0.5}},
             "material": "metal.steel"},
        ],
    }


def repro_adv202() -> None:
    from app.assets.specs import parse_asset_spec, validate_asset_spec
    from app.assets.geometry_quality import validate_geometry
    from app.assets.compiler import (
        compile_asset_spec,
        HITBOX_MIN,
        HITBOX_MAX,
        HITBOX_VISIBLE_MAX_RATIO,
    )
    from app.assets.spec_provider import AssetSpecRequest
    from app.services.ollama_driver import OllamaAssetSpecProvider
    from app.generation.provider import ProviderResult

    raw = plinth()
    phase13 = validate_asset_spec(raw)
    parsed = parse_asset_spec(raw, non_throwing=False)
    geom = validate_geometry(parsed)
    dump("DEF-077 Phase13 issues (expect [], still schema-valid)",
         list(phase13))
    check("DEF-077 plinth stays Phase-13 valid", not phase13)
    dump("DEF-077 geometry report",
         {"valid": geom.valid, "issues": [i.code for i in geom.issues],
          "span": list(geom.metrics.span)})
    check(
        "DEF-077 single-part plinth NOT valid (COMPOSITE_BOUNDS_MISMATCH)",
        not geom.valid
        and all(i.code == "COMPOSITE_BOUNDS_MISMATCH" for i in geom.issues)
        and len(geom.issues) >= 3,
    )
    cm = [i for i in geom.issues if i.code == "COMPOSITE_BOUNDS_MISMATCH"]
    check(
        "DEF-077 COMPOSITE_BOUNDS_MISMATCH carries single-part axes messages",
        bool(cm) and any("single-part" in i.message and "3.9m" in i.message for i in cm),
    )

    # Compiled hitbox for the bypassed-gate case: clamped to visible_span*2.0.
    compiled = compile_asset_spec(parsed)
    hit = compiled.hitbox.scale
    vis = parsed.parts[0].transform.scale
    hit_v = [hit.x, hit.y, hit.z]
    vis_v = [vis[0], vis[1], vis[2]]
    per_axis = [hit_v[i] / vis_v[i] for i in range(3)]
    cap_per_axis = [s * HITBOX_VISIBLE_MAX_RATIO for s in vis_v]
    dump("DEF-077 compiled hitbox (bypassed gate, clamped)",
         {"visibleMeshScale": vis_v,
          "compiledHitboxScale": hit_v,
          "hitboxOverMeshPerAxis": per_axis,
          "hitboxClampPerAxis (visible*2.0)": cap_per_axis})
    check(
        "DEF-077 compiled hitbox clamped to <= visible_span*2.0 (never 3.9)",
        all(hit_v[i] <= vis_v[i] * HITBOX_VISIBLE_MAX_RATIO + 1e-9 for i in range(3))
        and all(HITBOX_MIN <= hit_v[i] <= HITBOX_MAX for i in range(3))
        and max(hit_v) < 3.9,
    )

    # ---- Driver: repairs -> PUBLISHED (repaired true) ------------------------
    coherent = {
        "canonicalName": "Bronze Ceremonial Ice Pick",
        "category": "decor",
        "subtype": "ceremonial_ice_pick",
        "dimensions": {"x": 0.12, "y": 0.5, "z": 0.1},
        "parts": [
            {"id": "part_00", "role": "shaft", "primitive": "cylinder",
             "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.05, "y": 0.18, "z": 0.05}},
             "material": "metal.brass"},
            {"id": "part_01", "role": "point", "primitive": "box",
             "transform": {"position": {"x": 0.0, "y": 0.2, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.05, "y": 0.05, "z": 0.05}},
             "material": "metal.brass"},
            {"id": "part_02", "role": "handle", "primitive": "box",
             "transform": {"position": {"x": 0.0, "y": -0.19, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.05, "y": 0.06, "z": 0.05}},
             "material": "wood.dark"},
        ],
    }

    class Scripted2:
        def __init__(self, contents):
            self.contents = list(contents)
            self.calls = 0

        def generate(self, request):
            self.calls += 1
            content = self.contents.pop(0) if self.contents else "<not-json>"
            return ProviderResult(content=content)

    served = Scripted2([json.dumps(plinth(), ensure_ascii=False),
                        json.dumps(coherent, ensure_ascii=False)])
    provider = OllamaAssetSpecProvider(
        provider=served, attempt_id="qa-def077", budget_consumer=lambda: True)
    result = provider.generate(
        AssetSpecRequest(requested_name="rough plinth", category_hint="decor"))
    metrics = provider.last_geometry_metrics or {}
    dout: dict[str, object] = {
        "error": result.error,
        "providerCalls": served.calls,
        "repaired": metrics.get("repaired"),
        "issueCountBeforeRepair": metrics.get("issueCountBeforeRepair"),
        "finalPartCount": metrics.get("finalPartCount"),
        "finalBoundingBox": metrics.get("finalBoundingBox"),
    }
    dump("DEF-077 driver (spec provider) repaired outcome", dout)
    check(
        "DEF-077 plinth driver repairs (repaired true, issueCount>=1)",
        result.error is None and metrics.get("repaired") is True
        and (metrics.get("issueCountBeforeRepair") or 0) >= 1,
    )

    # ---- Legitimate single-part (0.6 declared vs 0.4 visible) passes --------
    legit = {
        "canonicalName": "Carved Block",
        "category": "decor",
        "subtype": None,
        "dimensions": {"x": 0.6, "y": 0.6, "z": 0.6},
        "parts": [
            {"id": "part_00", "role": "block", "primitive": "box",
             "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.4, "y": 0.4, "z": 0.4}},
             "material": "wood.dark"},
        ],
    }
    legit_parsed = parse_asset_spec(legit, non_throwing=False)
    legit_geom = validate_geometry(legit_parsed)
    legit_compiled = compile_asset_spec(legit_parsed)
    lh = legit_compiled.hitbox.scale
    lv = [lh.x, lh.y, lh.z]
    lspan = list(legit_geom.metrics.span)
    dump("DEF-077 legitimate single-part outcome",
         {"geometryValid": legit_geom.valid,
          "issues": [i.code for i in legit_geom.issues],
          "hitboxScale": lv,
          "visibleSpan": lspan})
    check(
        "DEF-077 legitimate single-part (0.6 vs 0.4) passes w/ bounded hitbox",
        legit_geom.valid
        and all(HITBOX_MIN <= v <= 0.8 for v in lv)
        and all(lv[i] <= lspan[i] * HITBOX_VISIBLE_MAX_RATIO + 1e-9
                for i in range(3)),
    )


# --------------------------------------------------------------------------- #
# ADV-203 / DEF-078
# --------------------------------------------------------------------------- #
def repair_reporter(provider):
    return provider.last_geometry_metrics or {}


def repro_adv203() -> None:
    from app.assets.geometry_quality import (
        CATEGORY_SUBTYPE_NORMALIZATION,
        HANDHELD_CANONICAL_TERMS,
        is_handheld_object,
        normalized_category_subtype,
        silhouette_relevant,
        validate_geometry,
    )
    from app.assets.specs import parse_asset_spec, validate_asset_spec
    from app.assets.compiler import compile_asset_spec
    from app.assets.spec_provider import AssetSpecRequest
    from app.services.ollama_driver import OllamaAssetSpecProvider
    from app.generation.provider import ProviderResult

    dump("DEF-078 HANDHELD_CANONICAL_TERMS", list(HANDHELD_CANONICAL_TERMS))
    check("DEF-078 'blade' in HANDHELD_CANONICAL_TERMS", "blade" in HANDHELD_CANONICAL_TERMS)
    check("DEF-078 'cleaver' in HANDHELD_CANONICAL_TERMS", "cleaver" in HANDHELD_CANONICAL_TERMS)

    # The UNFIXED original: a two-part knife "Ritual Blade".
    dunk = {
        "canonicalName": "Ritual Blade",
        "category": "decor",
        "subtype": "ceremonial_knife",
        "dimensions": {"x": 0.35, "y": 0.5, "z": 0.15},
        "parts": [
            {"id": "part_00", "role": "blade", "primitive": "box",
             "transform": {"position": {"x": 0.0, "y": 0.12, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.06, "y": 0.28, "z": 0.05}},
             "material": "metal.steel"},
            {"id": "part_01", "role": "grip", "primitive": "cylinder",
             "transform": {"position": {"x": 0.0, "y": -0.16, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.05, "y": 0.14, "z": 0.05}},
             "material": "wood.dark"},
        ],
    }
    parsed = parse_asset_spec(dunk, non_throwing=False)
    handled = is_handheld_object(parsed)
    silrel = silhouette_relevant(parsed)
    dump("DEF-078 'Ritual Blade' inspection",
         {"bladeInTerms": "blade" in HANDHELD_CANONICAL_TERMS,
          "ritualBladeHandheld": handled,
          "ritualBladeSilhouetteRelevant": silrel})
    check("DEF-078 two-part 'Ritual Blade' is now hand-held + silhouette-relevant",
          handled and silrel)

    # The single-sphere "Ritual Blade": hand-held gate reached -> fails
    # SILHOUETTE_HEURISTIC -> repairs through the driver.
    sphere_blade = {
        "canonicalName": "Ritual Blade",
        "category": "decor",
        "subtype": None,
        "dimensions": {"x": 0.12, "y": 0.5, "z": 0.1},
        "parts": [
            {"id": "part_00", "role": "blade", "primitive": "sphere",
             "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},
             "material": "metal.steel"},
        ],
    }
    sb = parse_asset_spec(sphere_blade, non_throwing=False)
    sb_geom = validate_geometry(sb)
    dump("DEF-078 single-sphere Ritual Blade geometry gate",
         {"geometryValid": sb_geom.valid,
          "issues": [i.code for i in sb_geom.issues],
          "silhouetteApplicable": sb_geom.metrics.silhouette_heuristic_applicable,
          "silhouettePassed": sb_geom.metrics.silhouette_heuristic_passed})
    check(
        "DEF-078 single-sphere 'Ritual Blade' reaches hand-held gate and fails "
        "SILHOUETTE_HEURISTIC",
        not sb_geom.valid
        and any(i.code == "SILHOUETTE_HEURISTIC" for i in sb_geom.issues),
    )

    # --- Driver repair for the single-sphere Ritual Blade ----------------------
    coherent = {
        "canonicalName": "Bronze Ceremonial Ice Pick",
        "category": "decor",
        "subtype": "ceremonial_ice_pick",
        "dimensions": {"x": 0.12, "y": 0.5, "z": 0.1},
        "parts": [
            {"id": "part_00", "role": "shaft", "primitive": "cylinder",
             "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.05, "y": 0.18, "z": 0.05}},
             "material": "metal.brass"},
            {"id": "part_01", "role": "point", "primitive": "box",
             "transform": {"position": {"x": 0.0, "y": 0.2, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.05, "y": 0.05, "z": 0.05}},
             "material": "metal.brass"},
            {"id": "part_02", "role": "handle", "primitive": "box",
             "transform": {"position": {"x": 0.0, "y": -0.19, "z": 0.0},
                           "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                           "scale": {"x": 0.05, "y": 0.06, "z": 0.05}},
             "material": "wood.dark"},
        ],
    }

    class Scripted3:
        def __init__(self, contents):
            self.contents = list(contents)
            self.calls = 0

        def generate(self, request):
            self.calls += 1
            content = self.contents.pop(0) if self.contents else "<not-json>"
            return ProviderResult(content=content)

    served = Scripted3([json.dumps(sphere_blade, ensure_ascii=False),
                        json.dumps(coherent, ensure_ascii=False)])
    provider = OllamaAssetSpecProvider(
        provider=served, attempt_id="qa-def078", budget_consumer=lambda: True)
    result = provider.generate(
        AssetSpecRequest(requested_name="Ritual Blade", category_hint="decor"))
    metrics = provider.last_geometry_metrics or {}
    dout = {"error": result.error, "providerCalls": served.calls,
            "repaired": metrics.get("repaired"),
            "issueCountBeforeRepair": metrics.get("issueCountBeforeRepair"),
            "finalPartCount": metrics.get("finalPartCount")}
    dump("DEF-078 driver (spec provider) repaired outcome", dout)
    check(
        "DEF-078 gated 'Ritual Blade' repairs through the bounded driver",
        result.error is None and metrics.get("repaired") is True,
    )

    # ---- controls --------------------------------------------------------------
    controls = {
        "Wooden Kitchen Knife": True,
        "Kitchen Knife": True,
        "Bronze Ceremonial Ice Pick": True,
        "Ritual Blade": True,   # now caught (term gap closed)
        "One-Axis Stick": False,
    }
    obs: dict[str, object] = {}
    for name, expect_handheld in controls.items():
        sp = copy.deepcopy(dunk)
        sp["canonicalName"] = name
        if name == "One-Axis Stick":
            sp["subtype"] = None
            sp["dimensions"] = {"x": 0.5, "y": 0.05, "z": 0.05}
            sp["parts"] = [copy.deepcopy(dunk["parts"][0])]
            sp["parts"][0]["transform"]["scale"] = {"x": 0.5, "y": 0.05, "z": 0.05}
            sp["parts"][0]["transform"]["position"] = {"x": 0.0, "y": 0.0, "z": 0.0}
        q = parse_asset_spec(sp, non_throwing=False)
        gr = validate_geometry(q)
        obs[name] = {
            "isHandheld": is_handheld_object(q),
            "silhouetteRelevant": silhouette_relevant(q),
            "geometryValid": gr.valid,
            "issues": sorted({i.code for i in gr.issues}),
        }
    dump("DEF-078 controls (hand-held axis per name)", obs)
    check(
        "DEF-078 'One-Axis Stick' still passes (not hand-held, geometry valid)",
        obs["One-Axis Stick"]["isHandheld"] is False
        and obs["One-Axis Stick"]["geometryValid"] is True
        and "SILHOUETTE_HEURISTIC" not in obs["One-Axis Stick"]["issues"],
    )
    check(
        "DEF-078 existing knife/ice-pick controls unchanged (hand-held caught)",
        all(obs[name]["isHandheld"] is True
            for name in ("Wooden Kitchen Knife", "Kitchen Knife",
                         "Bronze Ceremonial Ice Pick")),
    )
    check(
        "DEF-078 'Ritual Blade' control now hand-held",
        obs["Ritual Blade"]["isHandheld"] is True,
    )
    dump("DEF-078 cleaver normalization",
         {"normalized_category_subtype('cleaver')": normalized_category_subtype("cleaver"),
          "hasCleaverKey": "cleaver" in CATEGORY_SUBTYPE_NORMALIZATION})
    check(
        "DEF-078 'cleaver' normalizes to (decor, kitchen_knife)",
        normalized_category_subtype("cleaver") == ("decor", "kitchen_knife")
        and normalized_category_subtype("cleavers") == ("decor", "kitchen_knife")
        and normalized_category_subtype("kitchen knife") == ("decor", "kitchen_knife"),
    )


# --------------------------------------------------------------------------- #
# FIXTURE SCAN — confirm zero real fixtures carry these shapes (no latent break)
# --------------------------------------------------------------------------- #
def scan_fixtures() -> None:
    from app.assets.specs import parse_asset_spec, validate_asset_spec
    from app.assets.geometry_quality import is_handheld_object, silhouette_relevant, validate_geometry
    from app.assets.compiler import compile_asset_spec

    raw_specs: dict[str, str] = {}

    import fixtures.asset_specs as F1
    for name, raw in F1.GOLDEN_SPEC_CONTENT.items():
        raw_specs[f"asset_specs.py:{name}"] = raw
    GOLDEN_SPEC_CONTENT = F1.GOLDEN_SPEC_CONTENT
    GOLDEN_SPEC_NAMES = F1.GOLDEN_SPEC_NAMES

    import fixtures.asset_specs_unseen as F2
    for attr in ("BRONZE_ICE_PICK_SPEC", "FORENSIC_SAMPLE_PRESS_SPEC",
                 "CARVED_IVORY_DESK_SEAL_SPEC"):
        raw_specs[f"asset_specs_unseen.py:{attr}"] = getattr(F2, attr)

    import app.world.composer as C
    for name, raw in C._KNOWN_PROCEDURAL_SPECS.items():
        raw_specs[f"composer.py:{name}"] = raw

    import json as _json
    catalog = _json.loads(
        (REPO / "assets" / "catalog" / "catalog.json").read_text(encoding="utf-8"))

    import re as _re
    fullwidth_part_ids: list[str] = []
    blade_canonical_names: list[str] = []
    near_max_single_part: list[str] = []
    mismatch_validated_vs_compiled: list[str] = []
    geometry_invalid_golden: list[str] = []
    all_handheld_detection: dict[str, object] = {}

    for label, raw in raw_specs.items():
        if _re.search(r"part_[^\x00-\x7f]", raw):
            fullwidth_part_ids.append(label)
        parsed = parse_asset_spec(raw, non_throwing=False)
        if any(ord(ch) > 127 for p in parsed.parts for ch in p.id):
            fullwidth_part_ids.append(label)
        lower = parsed.canonical_name.casefold()
        if "blade" in lower:
            blade_canonical_names.append(f"{label} -> {parsed.canonical_name!r}")
        if len(parsed.parts) == 1 and any(
            float(d) > 1.0 for d in parsed.dimensions
        ):
            near_max_single_part.append(f"{label} -> dims {list(parsed.dimensions)}")
        compiled = compile_asset_spec(parsed)
        if len(compiled.parts) != len(parsed.parts):
            mismatch_validated_vs_compiled.append(
                f"{label}: validated {len(parsed.parts)} compiled {len(compiled.parts)}")
        all_handheld_detection[label] = {
            "isHandheld": is_handheld_object(parsed),
            "silhouetteRelevant": silhouette_relevant(parsed),
            "canonicalName": parsed.canonical_name,
        }

    # The geometry-validity regression gate is scoped to the GOLDEN Phase 13
    # fixtures exactly as the shipped suite declares
    # (test_geometry_quality.py::test_golden_fixtures_pass_geometry_zero_issues
    # parametrizes GOLDEN_SPEC_NAMES — the Phase 14_5 unseen trio is
    # repairable-by-design and is NOT part of that contract; its pre-existing
    # 4.4a/4.6/4.7 failures are documented Phase 17 repair showcase inputs).
    for name in GOLDEN_SPEC_NAMES:
        raw = GOLDEN_SPEC_CONTENT[name.casefold().strip()]
        gr = validate_geometry(parse_asset_spec(raw, non_throwing=False))
        if not gr.valid:
            geometry_invalid_golden.append(
                f"{name} -> {sorted({i.code for i in gr.issues})}")

    catalog_blade_names: list[str] = []
    for asset in catalog.get("assets", []):
        canon = str(asset.get("canonicalName", ""))
        if "blade" in canon.casefold():
            catalog_blade_names.append(canon)

    dump("FIXTURE SCAN (no latent break)",
         {"specFixturesParsed": len(raw_specs),
          "fullwidthPartIdHits": fullwidth_part_ids,
          "bladeCanonicalNameHits": blade_canonical_names,
          "singlePartNearMaxDimHits": near_max_single_part,
          "validatedVsCompiledMismatch": mismatch_validated_vs_compiled,
          "geometryInvalidGoldenFixtures": geometry_invalid_golden,
          "catalogCanonicalNameBladeHits": catalog_blade_names,
          "handheldDetectionOnFixtures": all_handheld_detection})
    check("FIXTURE SCAN zero fullwidth part ids", not fullwidth_part_ids)
    check("FIXTURE SCAN zero validated-vs-compiled mismatches",
          not mismatch_validated_vs_compiled)
    check("FIXTURE SCAN all GOLDEN Phase 13 fixtures still geometry-valid",
          not geometry_invalid_golden)


def main() -> int:
    repro_adv201()
    repro_adv202()
    repro_adv203()
    scan_fixtures()
    out = REPO / "e2e" / "artifacts" / "qa-phase17-def076-078-repro.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "checks": [{"label": label, "pass": ok} for label, ok in CHECKS],
        "passed": sum(1 for _, ok in CHECKS if ok),
        "failed": sum(1 for _, ok in CHECKS if not ok),
        "allPassed": all(ok for _, ok in CHECKS),
    }
    RESULT["_CHECK_SUMMARY"] = summary
    out.write_text(json.dumps(RESULT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nCHECK SUMMARY: {summary['passed']}/{len(CHECKS)} PASSED, "
          f"{summary['failed']} FAILED")
    print(f"VERIFICATION COMPLETE -> {out}")
    return 0 if summary["allPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())