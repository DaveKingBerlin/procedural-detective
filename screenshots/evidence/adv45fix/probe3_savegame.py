"""Probe 3 — savegame / reveal / accusation round-trip after a repaired case.

Run:  python screenshots/evidence/adv45fix/probe3_savegame.py
Then: & frontend/node_modules/.bin/vite-node.cmd screenshots/evidence/adv45fix/sg_parse.ts
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harness import (  # noqa: E402
    completion_script,
    golden_script,
    guard_stub,
    header,
    held,
    load_environment,
    playability,
    publish_via_guard,
    service,
    WorldRequirements,
)

from app.services.generation import (  # noqa: E402
    _ensure_playability_clues,
    _world_graph_namespace_violations,
)
from app.services.publication import (  # noqa: E402
    project_discovery,
    project_world_objects,
    serialize_published_payload,
    visible_placement_for_object,
)
from app.services.savegame import project_savegame_v1  # noqa: E402


def build_savegame(published, difficulty="medium"):
    payload = json.loads(
        serialize_published_payload(published, seed=11, prompt="p", title="T")
    )
    sg = project_savegame_v1(
        payload, difficulty=difficulty, exported_at="2026-10-16T00:00:00Z"
    )
    return payload, sg


def main():
    header("PROBE 3 — savegame/reveal/accusation after repair")

    # Guard-repaired candidate
    _c, rec = held(completion_script(), "medium")
    stub = guard_stub()
    assert publish_via_guard(stub, rec, "medium") == "PUBLISHED"
    pub = stub._publication.published
    print("published gate3:", playability.validate_published_candidate(pub, "medium").valid)

    payload, sg = build_savegame(pub, "medium")
    (HERE / "sg_repaired_medium.pdcase.json").write_text(
        json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("savegame keys:", list(sg.keys()))
    pc = sg["case"]["publicCase"]
    print("publicCase.scene:", pc.get("scene"))
    wos = pc.get("worldObjects")
    print("publicCase.worldObjects count:", len(wos) if wos else 0)
    if wos:
        print("sample added world objects:",
              [w for w in wos if str(w.get("objectId", "")).startswith(("dim_clue", "decoy_clue", "coverage_clue"))][:6])
    meta = sg["case"].get("metadata", {})
    print("metadata:", {k: meta.get(k) for k in ("environmentId", "difficulty")})

    # -> write payload for the frontend parser too
    (HERE / "payload_repaired_medium.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    # Discovery: can the player discover the linked added clues?
    print("\n-- discovery of added clues --")
    added = [p for p in pub.draft.world_graph.placements
             if str(p.object_id).startswith(("dim_clue", "decoy_clue", "coverage_clue"))]
    for p in added:
        vis = visible_placement_for_object(payload, p.object_id)
        disc = project_discovery(payload, p.evidence_id) if p.evidence_id else None
        print(f"   {p.object_id}: visible={vis is not None} "
              f"discov_title={(disc or {}).get('title')!r} "
              f"interaction={(disc or {}).get('interaction')!r}")

    # Project world objects (as the bootstrap would, pre-discovery)
    projected = project_world_objects(payload)
    printed = [o for o in projected if str(o["objectId"]).startswith(
        ("dim_clue", "decoy_clue", "coverage_clue"))]
    print("projected added objects:", [(o["objectId"], o["locationId"], o["anchor"],
                                        o["interaction"], o["evidenceId"])
                                       for o in printed])

    # golden (unrepaired) control for comparison
    _c2, rec2 = held(golden_script(), "medium")
    svc = service()
    svc._apply_kit_composition(rec2, "apartment", WorldRequirements(), None)
    payload2, sg2 = build_savegame(rec2.published, "medium")
    (HERE / "sg_golden_medium.pdcase.json").write_text(
        json.dumps(sg2, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("\ngolden control namespace violations:",
          _world_graph_namespace_violations(rec2.published.draft))

    # easy / hard repaired savegames
    for d in ("easy", "hard"):
        _cc, rr = held(completion_script(), d)
        st = guard_stub()
        assert publish_via_guard(st, rr, d) == "PUBLISHED"
        _p, s = build_savegame(st._publication.published, d)
        (HERE / f"sg_repaired_{d}.pdcase.json").write_text(
            json.dumps(s, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"{d} savegame written; publicCase.scene={s['case']['publicCase'].get('scene')}")


if __name__ == "__main__":
    main()
