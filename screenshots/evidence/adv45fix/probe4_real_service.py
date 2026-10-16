"""Probe 4 — REAL durable GenerationService end-to-end on the completion case.

Confirms the production-reachable repaired candidate: scene pin, durable row,
savegame round-trip, and Gate 3 validity.

Run:  python screenshots/evidence/adv45fix/probe4_real_service.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
BACKEND = REPO / "backend"
TESTS = BACKEND / "tests"
HERE = Path(__file__).resolve().parent
for p in (str(BACKEND), str(TESTS), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import conftest  # noqa: E402
from app.persistence.store import Store  # noqa: E402
from app.services.generation import GenerationService  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation import playability  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.services.publication import serialize_published_payload  # noqa: E402
from app.services.savegame import project_savegame_v1  # noqa: E402

from harness import completion_script, empty_placements_script, golden_script  # noqa: E402


def run_case(script, difficulty, label, prompt):
    print("\n" + "=" * 78 + f"\n{label}\n" + "=" * 78)
    db = Path(tempfile.gettempdir()) / f"pd45fixadv_{uuid.uuid4().hex}.sqlite"
    url = f"sqlite:///{db.as_posix()}"
    conftest.upgrade_db(url)
    store = Store(url)
    try:
        svc = GenerationService(
            settings=conftest.make_phase5_settings(url),
            store=store,
            provider_factory=lambda: FakeProvider(script=script),
        )
        sess = svc.create_anonymous_quota_session()
        res = svc.start_case_generation(
            prompt,
            anonymous_quota_session_id=sess.anonymous_quota_session_id,
            difficulty=difficulty,
        )
        print("  status:", res.status, "| failure_code:", res.failure_code)
        cv = store.get_case_version(res.case_id, 1)
        print("  durable case_version state:", getattr(cv, "state", None),
              "| reason:", getattr(cv, "state_reason", None))
        pub = store.get_published(res.case_id, 1)
        print("  durable published row present:", pub is not None)
        return res, pub, store
    finally:
        store.dispose()
        try:
            os.remove(db)
        except OSError:
            pass


PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)


def examine(pub, tag, difficulty="medium"):
    payload = pub.payload if hasattr(pub, "payload") else None
    if payload is None:
        print("  no payload")
        return
    if isinstance(payload, str):
        payload = json.loads(payload)
    # build savegame
    sg = project_savegame_v1(payload, difficulty=difficulty,
                             exported_at="2026-10-16T00:00:00Z")
    (HERE / f"sg_real_{tag}.pdcase.json").write_text(
        json.dumps(sg, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("  publicCase.scene:", sg["case"]["publicCase"].get("scene"))
    draft = payload.get("draft") or {}
    scene = draft.get("scene")
    print("  payload draft.scene:", scene)
    placements = (draft.get("world_graph") or {}).get("placements") or []
    added = [p for p in placements
             if str(p.get("object_id", "")).startswith(("dim_clue", "decoy_clue", "coverage_clue"))]
    print("  placement count:", len(placements), "| added repair clues:", len(added))
    print("  added evidence links:", sorted({p.get("evidence_id") for p in added}))
    print("  payload top-level keys:", sorted(payload.keys()))


for d in ("easy", "medium", "hard"):
    res, pub, store = run_case(
        completion_script(), d,
        f"REAL SERVICE: completion script ({d})", PROMPT
    )
    if pub is not None:
        examine(pub, d, d)

# control: golden
res, pub, store = run_case(golden_script(), "medium",
                           "REAL SERVICE: golden control (medium)", PROMPT)
if pub is not None:
    examine(pub, "golden", "medium")

# empty world must fail closed
res, pub, store = run_case(empty_placements_script(), "medium",
                           "REAL SERVICE: empty world (medium)", PROMPT)
print("  empty-world published row is None:", pub is None)
