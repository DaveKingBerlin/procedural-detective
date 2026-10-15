"""ADV-43B probe 9 — semantic gate coverage: empty location universe / unresolved scene.

Hermetic (mocked httpx). Run from repo root:
  python screenshots/evidence/adv43b/probe9_semantic_gap.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

import logging  # noqa: E402
logging.getLogger().setLevel(logging.CRITICAL)

from _pytest.monkeypatch import MonkeyPatch  # noqa: E402
from test_phase43b_structured_output import (  # noqa: E402
    _run_frontier, _started_stages, GOLDEN_STAGE_PAYLOADS as G,
)
from app.generation.provider import GenerationStage as S  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402


def pw(d):
    d = json.loads(d)
    d["locations"] = []
    return json.dumps(d)


queue = [
    G[S.CASE_TRUTH],
    pw(G[S.PUBLIC_WORLD]),   # canonically parseable, empty location universe
    G[S.EVIDENCE],
    G[S.WORLD_GRAPH],
]
mp = MonkeyPatch()
try:
    controller, rec, handle, events = _run_frontier(mp, queue)
finally:
    mp.undo()
record = controller.attempt(handle.attempt_id)
seq = _started_stages(events)
print("stage sequence:", seq)
print("evidence started:", "evidence" in seq)
print("world_graph started:", "world_graph" in seq)
print("state:", record.state)
print("PUBLIC_WORLD trusted:", S.PUBLIC_WORLD in record.stage_done)
print("last validation structural:",
      (record.last_validation.structural_issues if record.last_validation else None))
print("\nDONE")
