"""ADV-43 probe 7/8: world kit scoping + diagnostics safety."""
from __future__ import annotations
import json, sys
sys.path.insert(0, r"C:\Users\Fujitsu\AppData\Local\Temp\opencode\adv43")
from adv_common import attempt, parse_stage, section, crime_doc, attempt_with_prior
from app.generation import parser as parser_mod, pipeline, prompts, safety
from app.generation.provider import GenerationStage
from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS

section("ATTACK 7a: full world_graph prompt context - cross-kit anchors offered?")
a = attempt()
a.stage_outputs[GenerationStage.CASE_TRUTH] = parse_stage(GenerationStage.CASE_TRUTH, json.dumps(crime_doc()))
a.stage_outputs[GenerationStage.PUBLIC_WORLD] = parse_stage(
    GenerationStage.PUBLIC_WORLD, GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD])
ctx = pipeline._stage_context(a, GenerationStage.WORLD_GRAPH)
print("selected kit:", pipeline.selected_environment_kit(a))
table = dict(prompts._kit_anchor_table())
apt = set(table.get("apartment", ()))
office_only = [x for x in table.get("office", ()) if x not in apt]
wh_only = [x for x in table.get("warehouse", ()) if x not in apt]
print("kit ids:", list(table))
print("apartment anchors:", sorted(apt))
print("office-exclusive anchors present in FULL ctx?",
      [x for x in office_only if x in ctx])
print("warehouse-exclusive anchors present in FULL ctx?",
      [x for x in wh_only if x in ctx])
for line in ctx.splitlines():
    if line.strip().startswith("kit "):
        print("   CTX KIT LINE:", line.strip()[:120])
print("authoritative block selectedEnvironmentKit line present:",
      "selectedEnvironmentKit: apartment" in ctx)

section("ATTACK 7b: request anchor enum only selected-kit anchors?")
req = pipeline.build_request(a, GenerationStage.WORLD_GRAPH)
place = req.json_schema["properties"]["worldGraph"]["properties"]["placements"]["items"]["properties"]
print("anchor enum:", place["anchor"].get("enum"))
print("assetId enum size:", len(place["assetId"].get("enum") or []))
print("objectId enum:", place["objectId"].get("enum"))
print("selected_environment_kit field:", req.selected_environment_kit)

section("ATTACK 7c: no kit resolved -> wide anchors? unknown asset/anchor?")
a2 = attempt("Victim: Laura Stein\nMurderer: Daniel Roth\nWeapon: kitchen knife\n")
a2.stage_outputs[GenerationStage.CASE_TRUTH] = parse_stage(GenerationStage.CASE_TRUTH, json.dumps(crime_doc()))
print("selected kit (no location):", pipeline.selected_environment_kit(a2))
req2 = pipeline.build_request(a2, GenerationStage.WORLD_GRAPH)
p2 = req2.json_schema["properties"]["worldGraph"]["properties"]["placements"]["items"]["properties"]
print("anchor enum (unresolved kit):", p2["anchor"].get("enum"))
print("assetId enum (unresolved kit) size:", len(p2["assetId"].get("enum") or []))
blk = pipeline._authoritative_world_graph_block(a2)
print("unresolved-kit line present:", "(must be selected" in blk)

section("ATTACK 7d: world graph referencing unknown object/location/evidence -> canonical rejection")
raw = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH])
print("world graph sample:", json.dumps(raw)[:200])
# Insert an unknown reference into the world graph and validate
a3 = attempt()
a3.stage_outputs[GenerationStage.CASE_TRUTH] = parse_stage(GenerationStage.CASE_TRUTH, json.dumps(crime_doc()))
a3.stage_outputs[GenerationStage.PUBLIC_WORLD] = parse_stage(
    GenerationStage.PUBLIC_WORLD, GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD])
a3.stage_outputs[GenerationStage.EVIDENCE] = parse_stage(
    GenerationStage.EVIDENCE, GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE])
wg = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH])
try:
    wg["worldGraph"]["placements"][0]["objectId"] = "unknown_object_zzz"
except Exception as e:
    print("no placements?", e)
p3 = parser_mod.parse_stage(GenerationStage.WORLD_GRAPH, json.dumps(wg), non_throwing=True)
print("parsed world graph:", p3 is not None)
if p3 is not None:
    a3.stage_outputs[GenerationStage.WORLD_GRAPH] = p3
    from app.generation.schemas import GeneratedDraft
    from app.generation import pipeline as pl
    d = pl.current_draft(a3)
    issues = pl._world_graph_resolution_issues(d) if d else "no draft"
    # also canonical safety
    wg_safety = safety.validate_world_graph(d.world_graph, {o.object_id for o in d.objects}, {e.id for e in d.evidence}) if d else ()
    print("world graph resolution issues:", issues)
    print("world graph safety issues:", wg_safety)

section("ATTACK 8: diagnostics fields for secrets")
import app.generation.controller as ctrl_mod
captured = []
orig = ctrl_mod.emit_event
ctrl_mod.emit_event = lambda name, **kw: captured.append((name, kw))
try:
    from app.generation.frontier_provider import FrontierProvider
    from app.generation.clock import ManualClock
    # Monkeypatch the httpx seam used by FrontierProvider
    import app.generation.frontier_provider as fp_mod
    class _Resp:
        status_code = 200
        def iter_bytes(self, chunk_size):
            yield b'{}'
    def _post(url, json=None, headers=None, timeout=None):
        return _Resp()
    fp_mod.httpx.post = _post
    provider = FrontierProvider(endpoint_url="https://openrouter.ai/api/v1/chat/completions",
                                api_key="SECRET-API-KEY-XYZ", model="m",
                                timeout_seconds=30, clock=ManualClock(1000.0),
                                structured_output_mode="openai_json_schema")
    # Minimal controller + attempt
    from app.generation.budgets import BudgetTracker
    from app.generation.ids import IdSource
    a4 = attempt()
    a4.budget = BudgetTracker(ManualClock(1000.0), deadline_seconds=600, max_calls=20,
                              max_repairs=3, max_regenerations=2)
    class _Adm:
        def release_generation(self, sid): pass
    ctrl = ctrl_mod.GenerationController(provider=provider, admission=_Adm(),
        clock=ManualClock(1000.0), ids=IdSource(), deadline_seconds=600,
        max_llm_calls_per_generation=20, max_repair_passes=3, max_full_regenerations=2,
        max_prompt_chars=4000, provider_name="frontier", provider_model="m")
    ctrl._attempts[a4.attempt_id] = a4
    ctrl._current_attempt_id = a4.attempt_id
    a4.state = ctrl_mod.GenerationState.GENERATING
    ok = ctrl._invoke_provider(a4, GenerationStage.CASE_TRUTH)
    print("invoke ok:", ok)
finally:
    ctrl_mod.emit_event = orig

names = [n for n, _ in captured]
print("events:", names)
for name, kw in captured:
    if name == "provider.call.complete":
        print("provider.call.complete fields:", sorted(kw))
        blob = json.dumps({k: (v if isinstance(v, (int, float, bool, str, list, type(None))) else str(v)) for k, v in kw.items()}, default=str)
        for secret in ("SECRET-API-KEY-XYZ", "Authorization", "Bearer", "api_key", "SECRET"):
            print(f"   secret {secret!r} present:", secret in blob)
        print("   requestPromptBytes:", kw.get("requestPromptBytes"), type(kw.get("requestPromptBytes")).__name__)
        print("   selectedEnvironmentKit:", kw.get("selectedEnvironmentKit"))
        print("   repairAuthorizedSections:", kw.get("repairAuthorizedSections"))
