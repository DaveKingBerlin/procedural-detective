"""ADV-43B probe 7 — transport closure vs the strict parser allowlists (P43B-FR-07/AC-07).

Read-only. Run from repo root:
  python screenshots/evidence/adv43b/probe7_keys.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation import prompts  # noqa: E402
from app.generation import schema_adapters as sa  # noqa: E402
from app.generation import parser  # noqa: E402


def closed(st):
    return sa.close_transport_modeled_objects(prompts.json_schema_for_stage_output(st))


def props(node):
    return set((node or {}).get("properties", {}) or {})


def items_props(node):
    return props((node or {}).get("items", {}))


def report(label, parser_keys, schema_props):
    missing = sorted(set(parser_keys) - set(schema_props))
    extra = sorted(set(schema_props) - set(parser_keys))
    print(f"  {label:42s} parser_keys={len(parser_keys):2d} "
          f"schema_props={len(schema_props):2d} "
          f"PARSER_ONLY(missing from schema)={missing} SCHEMA_ONLY={extra}")


print("=" * 74)
print("ATTACK 2i — parser-accepted keys must be representable in the closed schema")
print("=" * 74)

# case_truth crime
c = closed("case_truth")
report("case_truth crime", parser._CRIME_KEYS, props(c["properties"]["crime"]))
report("case_truth crime.crimeTime", parser._CRIME_TIME_KEYS,
       props(c["properties"]["crime"]["properties"].get("crimeTime")))

# public_world
p = closed("public_world")
report("public_world top", parser._PUBLIC_WORLD_TOP, props(p))
report("public_world persons[]", parser._PERSON_KEYS, items_props(p["properties"]["persons"]))
report("public_world motives[]", parser._MOTIVE_KEYS, items_props(p["properties"]["motives"]))
report("public_world objects[]", parser._OBJECT_KEYS, items_props(p["properties"]["objects"]))
report("public_world locations[]", parser._LOCATION_KEYS, items_props(p["properties"]["locations"]))
report("public_world travelRules[]", parser._TRAVEL_RULE_KEYS, items_props(p["properties"]["travelRules"]))
report("public_world scene", parser._SCENE_KEYS, props(p["properties"]["scene"]))

# evidence
e = closed("evidence")
report("evidence top", parser._EVIDENCE_TOP, props(e))
report("evidence evidence[]", parser._EVIDENCE_KEYS, items_props(e["properties"]["evidence"]))
evp = e["properties"]["evidence"]["items"]["properties"]
report("evidence propositions[]", parser._PROPOSITION_KEYS, items_props(evp["propositions"]))
report("evidence sourceRef", parser._SOURCE_REF_KEYS, props(evp["sourceRef"]))
# presentation is intentionally OPEN (additionalProperties default true)
pres = evp.get("presentation", {})
pres_open = pres.get("additionalProperties", True) is True
report("evidence presentation", parser._PRESENTATION_KEYS, props(pres))
print("    presentation node is OPEN (arbitrary keys accepted):", pres_open)

# world_graph
w = closed("world_graph")
wg = w["properties"]["worldGraph"]
report("world_graph top", parser._WORLD_GRAPH_KEYS, props(wg))
report("world_graph locations[]", parser._WG_LOCATION_KEYS, items_props(wg["properties"]["locations"]))
report("world_graph placements[]", parser._PLACEMENT_KEYS, items_props(wg["properties"]["placements"]))

# repair full draft
r = closed("repair")
report("repair top (FULL_DRAFT_TOP)", parser._FULL_DRAFT_TOP, props(r))

print("=" * 74)
print("ATTACK 2j — closed schema required keys must be parser-accepted (no forced unknown)")
print("=" * 74)


def required(node):
    return set((node or {}).get("required", []) or [])


for label, node, pkeys in [
    ("public_world top", p, parser._PUBLIC_WORLD_TOP),
    ("public_world persons[]", p["properties"]["persons"]["items"], parser._PERSON_KEYS),
    ("public_world scene", p["properties"]["scene"], parser._SCENE_KEYS),
    ("evidence evidence[]", e["properties"]["evidence"]["items"], parser._EVIDENCE_KEYS),
]:
    req = required(node)
    print(f"  {label:32s} required={sorted(req)} required_not_in_parser={sorted(req - pkeys)}")

print("\nDONE")
