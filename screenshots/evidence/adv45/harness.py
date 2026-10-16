"""Phase45 adversarial harness — imports the shipped Phase45 test helpers.

ZERO network / ZERO paid calls. Read-only w.r.t. product code.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))
if str(ROOT / "backend" / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend" / "tests"))

import test_phase45_world_compose_ownership as T  # noqa: E402

from app.generation import pipeline, playability, safety  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.state_machine import ValidationOutcome  # noqa: E402
from app.generation.schemas import PlacementSpec  # noqa: E402
from app.world.composer import KIT_BASE_OBJECT_IDS  # noqa: E402

held = T._held
service = T._service
renamed_script = T._renamed_script
renamed_docs = T._renamed_docs
golden_script = T._golden_script
capture = T._capture_events
events = T._events
event = T._event
decorative = T._decorative_reqs
guard_stub = T._guard_stub
publish_via_guard = T._publish_via_guard
Placement = T.PlacementSpec


def dump(obj):
    return json.dumps(obj, indent=2, default=str, sort_keys=True)


def script_from_docs(docs):
    return {stage: [T._dump(doc)] for stage, doc in docs.items()}


def report(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def run(label, fn):
    print(f"\n--- {label} ---")
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        print(f"EXCEPTION: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return None
