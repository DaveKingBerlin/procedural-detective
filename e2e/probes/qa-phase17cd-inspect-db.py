"""QA-owned inspection probe: dump the published world of the latest published
case in a scratch DB (transient evidence; never served by the product).

Sanitized output: public world-graph material ONLY (object ids / asset ids /
environment + compositionNotes). Never truth/hidden/proof material.
"""

from __future__ import annotations

import json
import sqlite3
import sys


def main() -> int:
    db = sys.argv[1]
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    print("tables:", tables)
    if "published_versions" not in tables:
        print("no published_versions table (nothing published to this DB)")
        return 0
    cur.execute("SELECT case_id, case_version, payload_json FROM published_versions ORDER BY rowid DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        print("no published case rows")
        return 0
    case_id, version, payload_json = row
    payload = json.loads(payload_json)
    draft = payload.get("draft", {})
    scene = draft.get("scene", {})
    objects = draft.get("objects", [])
    world = draft.get("world_graph", {})
    placements = world.get("placements", [])
    ice = next((p for p in placements if str(p.get("asset_id", "")).startswith("proc.")), None)
    print(json.dumps({
        "caseId": case_id,
        "caseVersion": version,
        "environmentId": scene.get("environment_id"),
        "objectCount": len(objects),
        "objects": sorted(
            (o.get("object_id"), o.get("asset_id"), o.get("subtype")) for o in objects
        ),
        "placementCount": len(placements),
        "placements": sorted(
            (p.get("object_id"), p.get("asset_id"), p.get("interaction"), p.get("evidence_id"))
            for p in placements
        ),
        "procPlacementKeys": sorted(ice.keys()) if ice else None,
        "procPlacementHasGeneratedDefinition": bool(ice and ice.get("generated_definition")),
        "procDefinitionSample": (
            {
                "dimensions": ice["generated_definition"].get("dimensions"),
                "partSchemas": [
                    {"id": p.get("id"), "scale": p.get("transform", {}).get("scale")}
                    for p in ice["generated_definition"].get("parts", [])
                ],
                "hitbox": (ice["generated_definition"].get("hitbox") or {}).get("scale"),
            }
            if ice and ice.get("generated_definition")
            else None
        ),
        "compositionNotes": draft.get("composition_notes") or payload.get("compositionNotes"),
    }, indent=2, sort_keys=True))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())