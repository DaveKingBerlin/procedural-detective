"""ADV-43B probe 2 — closure correctness (FR-07/08/09).

Hermetic, read-only. Run from repo root:
  python screenshots/evidence/adv43b/probe2_closure.py
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation import prompts  # noqa: E402
from app.generation import schema_adapters as sa  # noqa: E402
from app.generation import transport_schema as ts  # noqa: E402
from app.generation.parser import collect_issues, parse_stage, collect_full_draft_issues  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402

sys.path.insert(0, str(ROOT / "backend" / "tests"))
from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

G = GOLDEN_STAGE_PAYLOADS


def sect(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def closed(st):
    return sa.close_transport_modeled_objects(prompts.json_schema_for_stage_output(st))


sect("ATTACK 2a — DEF-072 unknown fields rejected at each nesting level (public_world)")
base = json.loads(G[GenerationStage.PUBLIC_WORLD])
c = closed("public_world")
variants = {
    "top.locationId": ("locationId", "x", None),
    "top.description": ("description", "x", None),
    "person.description": ("persons[0].description", "x", None),
    "person.name": ("persons[0].name", "x", None),
    "motive.name": ("motives[0].name", "x", None),
    "object.locationId": ("objects[0].locationId", "x", None),
    "scene.extra": ("scene.extra", 1, None),
    "travel.extra": ("travelRules[0].extra", 1, None),
}
for label, (path, val, _) in variants.items():
    doc = copy.deepcopy(base)
    cur = doc
    parts = path.replace("]", "").split(".")
    # handle [0]
    import re
    tokens = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    for tok in tokens[:-1]:
        if tok.startswith("["):
            cur = cur[int(tok[1:-1])]
        else:
            cur = cur[tok]
    last = tokens[-1]
    if last.startswith("["):
        cur[int(last[1:-1])] = val
    else:
        cur[last] = val
    issues = ts.validate_transport_schema(doc, c)
    print(f"  {label:24s} rejected={bool(issues)}  canonical_parse_invalid="
          f"{bool(collect_issues(GenerationStage.PUBLIC_WORLD, json.dumps(doc)))}")

sect("ATTACK 2b — golden public_world stays transport-valid AND canonically valid")
print("  transport valid:", ts.validate_transport_schema(base, c) == ())
print("  canonical issues:", collect_issues(GenerationStage.PUBLIC_WORLD, json.dumps(base)))

sect("ATTACK 2c — legitimate optional canonical fields remain representable")
# presentedData (person, optional open mapping)
doc = copy.deepcopy(base)
doc["persons"][0]["presentedData"] = {"any": [1, {"deep": True}], "null": None}
print("  person.presentedData open object valid:", ts.validate_transport_schema(doc, c) == ())
print("  canonical parse of presentedData:", collect_issues(GenerationStage.PUBLIC_WORLD, json.dumps(doc)))
# environmentId (scene, optional nullable string)
doc2 = copy.deepcopy(base)
doc2["scene"]["environmentId"] = "apartment"
print("  scene.environmentId valid:", ts.validate_transport_schema(doc2, c) == ())
doc2b = copy.deepcopy(base)
doc2b["scene"].pop("environmentId", None)
print("  scene without environmentId valid:", ts.validate_transport_schema(doc2b, c) == ())
doc2c = copy.deepcopy(base)
doc2c["scene"]["environmentId"] = None
print("  scene.environmentId=null valid:", ts.validate_transport_schema(doc2c, c) == ())
# nullable murdererId
ct = json.loads(G[GenerationStage.CASE_TRUTH])
ctc = closed("case_truth")
print("  case_truth murdererId top-level keys:", sorted(ct["crime"].keys()))
for mid in ("thomas_reed", None):
    d = copy.deepcopy(ct)
    d["crime"]["murdererId"] = mid
    print(f"  crime.murdererId={mid!r:14s} transport valid:",
          ts.validate_transport_schema(d, ctc) == ())

sect("ATTACK 2d — open evidence structured accepts arbitrary keys; presentation open")
ev = json.loads(G[GenerationStage.EVIDENCE])
evc = closed("evidence")
doc = copy.deepcopy(ev)
doc["evidence"][0]["propositions"][0].setdefault("structured", {})
doc["evidence"][0]["propositions"][0]["structured"] = {"anything": [1, {"deep": True}], "x": None}
print("  arbitrary structured valid:", ts.validate_transport_schema(doc, evc) == ())
# presentation arbitrary keys
doc2 = copy.deepcopy(ev)
pr = doc2["evidence"][0].setdefault("presentation", {})
pr["madeUpKey"] = {"nested": True}
print("  presentation arbitrary key valid:", ts.validate_transport_schema(doc2, evc) == ())
print("  presentation node open?:",
      json.dumps(evc["properties"]["evidence"]["items"]["properties"].get("presentation")))

sect("ATTACK 2e — request-local enums survive closure + Cohere adaptation")
universe = {
    "personIds": ("p1", "p2"), "locationIds": ("loc1",), "objectIds": ("obj1",),
    "motiveIds": ("m1",), "evidenceIds": ("e1",), "assetIds": ("a1",),
}
for st in ("case_truth", "public_world", "evidence", "world_graph", "repair"):
    narrowed = prompts.narrow_stage_output_schema(st, reference_universe=universe)
    if narrowed is None:
        print(f"  stage={st}: narrowed=None")
        continue
    cl = sa.close_transport_modeled_objects(narrowed)
    coh = sa.adapt_schema_for_transport(cl, adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA)
    def enum_map(node, path=""):
        out = {}
        if isinstance(node, dict):
            if isinstance(node.get("enum"), list):
                out[path] = node["enum"]
            for k, v in node.items():
                if k in ("properties",):
                    for pk, pv in v.items():
                        out.update(enum_map(pv, f"{path}.{pk}"))
                elif k == "items" and isinstance(v, dict):
                    out.update(enum_map(v, f"{path}[]"))
        return out
    before = enum_map(narrowed)
    after_cl = enum_map(cl)
    after_coh = enum_map(coh)
    missing_cl = [k for k in before if k not in after_cl]
    dropped_coh = [k for k in before if before[k] != after_coh.get(k)]
    print(f"  stage={st:12s} enums={len(before)} "
          f"missing_after_closure={missing_cl} changed_by_cohere={dropped_coh}")

sect("ATTACK 2f — Cohere synthetic-key adaptation not broken by closure projection")
cohere_wire_canonical = sa.adapt_schema_for_transport(
    prompts.json_schema_for_stage_output("evidence"),
    adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA,
)
# what closure WOULD do if (wrongly) applied before cohere adapt:
closure_then_cohere = sa.adapt_schema_for_transport(
    closed("evidence"), adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA,
)
print("  provider path for cohere == adapter(canonical):",
      cohere_wire_canonical == sa.adapt_schema_for_transport(
          prompts.json_schema_for_stage_output("evidence"),
          adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA))
print("  closure-then-cohere differs from cohere(canonical):",
      closure_then_cohere != cohere_wire_canonical)
# synthetic-key marker present?
text = json.dumps(cohere_wire_canonical)
print("  cohere wire contains synthetic open-object marker '",
      sa.OPEN_OBJECT_SYNTHETIC_PROPERTY, "':", sa.OPEN_OBJECT_SYNTHETIC_PROPERTY in text)

sect("ATTACK 2g — closure never mutates input (deep-copy guarantee)")
for st in ("case_truth", "public_world", "evidence", "world_graph", "repair"):
    canon = prompts.json_schema_for_stage_output(st)
    before = json.dumps(canon, sort_keys=True)
    sa.close_transport_modeled_objects(canon)
    print(f"  {st:12s} input unchanged:", json.dumps(canon, sort_keys=True) == before)

sect("ATTACK 2h — closed top-level objects still enforce required")
pc = closed("public_world")
print("  public_world missing persons rejected:",
      any(i.startswith("missing_required") for i in ts.validate_transport_schema({}, pc)))
print("  public_world unknown top key rejected:",
      any(i.startswith("additional_property") for i in ts.validate_transport_schema(
          {"persons": [], "motives": [], "objects": [], "locations": [],
           "travelRules": [], "scene": {"locationId": "x", "name": "y"}, "zzz": 1}, pc)))

print("\nDONE")
