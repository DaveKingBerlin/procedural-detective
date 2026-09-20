"""DEF-081 independent QA retest (Phase 17D Bugfix PART A).

The defect: the procedural render asset id leaked into the player-facing
weapon identity — ``weapon_label_of`` derived the label from the RENDER
``asset_id``, so ``proc.decor.4551660f4a46b2eb`` rendered as the weapon name
"Proc.decor.4551660f4a46b2eb" in the accusation picker and the reveal truth.

Required separation (Phase17DBugfix.md PART A):
  semantic weapon identity:  weaponId = bronze_ceremonial_ice_pick
  player-facing label:       Bronze Ceremonial Ice Pick
  render asset identity:     assetId  = proc.decor.<16hex>

This probe (QA-owned, durable; independent of the developer's test file) :

 1. builds the REAL published ice-pick world through the REAL generation
    driver/stage pipeline (only the Ollama network transport is mocked —
    every stage, validator, Asset Oracle, solver and publisher is the real
    product code);
 2. asserts the accusation-candidates weapon entry carries
    {name: "Bronze Ceremonial Ice Pick", id: bronze_ceremonial_ice_pick,
     assetId: proc.decor.*} — with the render id SEPARATE from the label;
 3. asserts the reveal truth shows weaponName "Bronze Ceremonial Ice Pick"
    while weaponId stays the semantic id (never the render id);
 4. asserts the world-graph placement still carries assetId=proc.* for
    rendering (the fix never touches the render layer);
 5. deep-scans EVERY player-facing string field of the accusation-candidates
    block, the reveal truth labels and the bootstrap world-object labels for
    ANY "proc." (case-insensitive) substring — ZERO allowed outside the
    assetId render layer;
 6. proves the hard ``weapon_label_of`` guard: an assetId-only craft without
    a semantic object id degrades to "Object" (never the render id), and
    passing the object_id yields the canonical human label;
 7. proves reload preserves the mapping (payload round-trip re-derives the
    same candidate entry, the same truth label and the same world asset id);
 8. confirms catalog weapons (kitchen knife / letter opener / scissors) keep
    their exact catalog labels + asset ids (identity separation is additive,
    catalog rendering is byte-identical).

Usage: python e2e/probes/qa-def081-retest.py   (exit 0 = all checks pass)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
TESTS = BACKEND / "tests"
for entry in (str(BACKEND), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)
os.environ.setdefault("ENV_FILE", os.devnull)  # hermetic: no operator dotenv

from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.publication import (  # noqa: E402
    project_world_objects,
    serialize_published_payload,
)
from app.services.reveal import candidate_block_of, truth_labels, weapon_label_of  # noqa: E402
from test_ollama_driver import _run, _staged  # noqa: E402  (hermetic driver harness)

CHECKS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(f"{'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        print("\n".join(CHECKS))
        raise SystemExit(f"DEF-081 probe FAILED at {name}: {detail}")


# --------------------------------------------------------------------------- #
# 1. the REAL published ice-pick world (mocked transport; real pipeline)
# --------------------------------------------------------------------------- #
record, _transport = _run(_staged())
check("driver-run-published", record.state is GenerationState.PUBLISHED, str(record.state))
payload = json.loads(
    serialize_published_payload(
        record.published,
        seed=getattr(record, "seed", None),
        prompt="showcase",
        model="hermes3:8b (mock)",
        title="Showcase",
    )
)

world_objects = payload["draft"]["objects"]
ice = next(o for o in world_objects if o.get("object_id") == "bronze_ceremonial_ice_pick")
asset_id = str(ice["asset_id"])
check("ice-pick-render-asset-id-is-proc", asset_id.startswith("proc.decor."), asset_id)
check("object-id-is-semantic", ice["object_id"] == "bronze_ceremonial_ice_pick")

candidates = candidate_block_of(payload)
weapons = {e["id"]: e for e in candidates["weapons"]}
entry = weapons["bronze_ceremonial_ice_pick"]

# --------------------------------------------------------------------------- #
# 2. accusation candidates weapon entry (identity separation)
# --------------------------------------------------------------------------- #
check("candidate-id-semantic", entry["id"] == "bronze_ceremonial_ice_pick", entry["id"])
check("candidate-assetid-render", str(entry["assetId"]) == asset_id, str(entry["assetId"]))
check("candidate-name-human", entry["name"] == "Bronze Ceremonial Ice Pick", entry["name"])
check("candidate-assetid-ne-name", entry["assetId"] != entry["name"])
check("candidate-name-has-no-proc", "proc." not in entry["name"].lower(), entry["name"])

# --------------------------------------------------------------------------- #
# 3. reveal truth labels (semantic id + canonical human label)
# --------------------------------------------------------------------------- #
labels = truth_labels(payload)
check("truth-weapon-id-semantic", labels["weaponId"] == "bronze_ceremonial_ice_pick", labels["weaponId"])
check("truth-weapon-name-human", labels["weaponName"] == "Bronze Ceremonial Ice Pick", labels["weaponName"])
check("truth-weapon-name-no-proc", "proc." not in labels["weaponName"].lower(), labels["weaponName"])
check("truth-id-ne-asset", labels["weaponId"] != asset_id)

# --------------------------------------------------------------------------- #
# 4. world graph keeps the render id at the RENDER layer
# --------------------------------------------------------------------------- #
world = project_world_objects(payload)
w_ice = next(o for o in world if o["objectId"] == "bronze_ceremonial_ice_pick")
check("world-graph-assetid-proc", str(w_ice["assetId"]).startswith("proc.decor."), str(w_ice["assetId"]))
check("world-graph-generated-present", w_ice.get("generated") is not None)
check("world-graph-canonical-name", w_ice["generated"].get("canonicalName") == "Bronze Ceremonial Ice Pick",
      str(w_ice["generated"].get("canonicalName")))

# --------------------------------------------------------------------------- #
# 5. deep scan: NO "proc." in ANY player-facing string field of the frozen
#    published DTOs (candidates block, reveal truth labels, bootstrap labels).
#    Only the render-layer assetId values may carry proc.* — everything else
#    (any key that is NOT the assetId layer) must be proc-free.
# --------------------------------------------------------------------------- #
PLAYER_BLOBS = {"candidates": candidates, "truth_labels": labels, "world_objects": world}


def scan_proc(node, path: str, hits: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            child = f"{path}.{k}" if path else str(k)
            if k in ("assetId", "asset_id"):
                continue  # render-layer identity (allowed — never rendered)
            scan_proc(v, child, hits)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            scan_proc(v, f"{path}[{i}]", hits)
    elif isinstance(node, str):
        if "proc." in node.lower():
            hits.append((path, node))


for blob_name, blob in PLAYER_BLOBS.items():
    hits: list = []
    scan_proc(blob, blob_name, hits)
    check(f"dto-scan-{blob_name}-zero-proc", not hits,
          "; ".join(f"{p}={v!r}" for p, v in hits[:5]) or "clean")

# --------------------------------------------------------------------------- #
# 6. hard weapon_label_of guard (render id can NEVER become a label)
# --------------------------------------------------------------------------- #
check("guard-proc-without-object-degrades-to-object",
      weapon_label_of("proc.decor.4551660f4a46b2eb") == "Object",
      weapon_label_of("proc.decor.4551660f4a46b2eb"))
check("guard-object-id-wins",
      weapon_label_of("proc.decor.4551660f4a46b2eb", object_id="bronze_ceremonial_ice_pick")
      == "Bronze Ceremonial Ice Pick")
check("guard-catalog-byte-identical",
      weapon_label_of("PROP_KITCHEN_KNIFE_01", object_id="kitchen_knife") == "Kitchen Knife")
check("guard-catalog-no-object-id",
      weapon_label_of("PROP_KITCHEN_KNIFE_01") == "Kitchen Knife")

# --------------------------------------------------------------------------- #
# 7. reload preserves the mapping (store round-trip re-derives identically)
# --------------------------------------------------------------------------- #
round_trip = json.loads(json.dumps(payload, sort_keys=True))
rt_candidates = candidate_block_of(round_trip)
rt_labels = truth_labels(round_trip)
rt_world = project_world_objects(round_trip)
check("reload-candidates-identical", rt_candidates == candidates)
check("reload-truth-identical", rt_labels == labels)
rt_asset = next(o["assetId"] for o in rt_world if o["objectId"] == "bronze_ceremonial_ice_pick")
check("reload-asset-id-identical", str(rt_asset) == asset_id, str(rt_asset))
check("reload-name-still-human",
      next(e for e in rt_candidates["weapons"] if e["id"] == "bronze_ceremonial_ice_pick")["name"]
      == "Bronze Ceremonial Ice Pick")

# --------------------------------------------------------------------------- #
# 8. catalog weapons unchanged
# --------------------------------------------------------------------------- #
check("catalog-knife-unchanged", weapons["kitchen_knife"] == {
    "id": "kitchen_knife", "assetId": "PROP_KITCHEN_KNIFE_01", "name": "Kitchen Knife"},
    json.dumps(weapons["kitchen_knife"]))
check("catalog-letter-opener-unchanged", weapons["letter_opener"] == {
    "id": "letter_opener", "assetId": "PROP_LETTER_OPENER_01", "name": "Letter Opener"},
    json.dumps(weapons["letter_opener"]))
check("catalog-scissors-unchanged", weapons["scissors"] == {
    "id": "scissors", "assetId": "PROP_SCISSORS_01", "name": "Scissors"},
    json.dumps(weapons["scissors"]))
for w in candidates["weapons"]:
    check(f"candidate-label-human-{w['id']}", "proc." not in w["name"].lower(), w["name"])

print("\n".join(CHECKS))
print(f"\nDEF-081 probe: {len(CHECKS)} checks, ALL PASS (exit 0)")