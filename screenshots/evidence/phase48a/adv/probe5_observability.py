r"""ADV-48.1 probe 5 - observability injection / bounds on the new event.

Run from backend/:
    python ..\screenshots\evidence\phase48a\adv\probe5_observability.py
"""
import io
import json
import logging
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="backslashreplace")
_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO / "backend"))
sys.path.insert(0, str(_REPO / "backend" / "tests"))

from app.services.ollama_driver import parse_world_requirements  # noqa: E402


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


logger = logging.getLogger("procedural-detective")
handler = Capture()
logger.addHandler(handler)
logger.setLevel(logging.INFO)


def world(kind):
    return json.dumps({
        "environmentHint": "office",
        "locationTokens": ["office"],
        "objects": [{"name": "bronze ceremonial ice pick", "criticality": "required"}],
        "relations": [{"kind": kind, "target": "bronze ceremonial ice pick"}],
        "unsafeUnsupported": [],
    })


HOSTILE = [
    "on_kitchen_counter",
    "On Kitchen Counter",
    "on_table\nSECRET_API_KEY=abc",
    "on_table\x01\x02\x03",
    "\u202e on_table \u202c",
    "on" + "_" * 100 + "table",
    "on_table" + "\x00" * 30,
    "on\tkitchen\ncounter",
    "on_table<script>alert(1)</script>",
    "on_table\"}]}}",
    "\uff4f\uff4e\uff3f\uff4b\uff49\uff54\uff43\uff48\uff45\uff4e\uff3f\uff43\uff4f\uff55\uff4e\uff54\uff45\uff52",
]

print("=== hostile raw kind -> event field ===")
for kind in HOSTILE:
    handler.records.clear()
    try:
        reqs = parse_world_requirements(world(kind), case_id="CASE-x", attempt_id="GA-x")
        parsed = tuple(r.kind for r in reqs.relations)
    except ValueError as exc:
        parsed = f"REJECTED: {str(exc)[:60]}"
    events = [
        (getattr(r, "pd_event", None), getattr(r, "pd_fields", {}))
        for r in handler.records
        if getattr(r, "pd_event", None) == "world.relation_kind.canonicalized"
    ]
    for _ev, fields in events:
        raw = fields.get("rawRelationKind")
        print(
            f"in={kind[:40]!r:44} parsed={parsed!s:16} "
            f"raw_len={len(raw) if raw is not None else None} "
            f"raw_repr={raw!r} canonical={fields.get('canonicalRelationKind')!r} "
            f"keys={sorted(fields)}"
        )
    if not events:
        print(f"in={kind[:40]!r:44} parsed={parsed!s:16} (no canonicalized event)")

print()
print("=== JSON log line cannot be broken by the field ===")
handler.records.clear()
try:
    parse_world_requirements(world("on_table\x01SECRET=abc"), case_id="C", attempt_id="A")
except ValueError as exc:
    print("rejected:", str(exc)[:70])
rec = [r for r in handler.records if getattr(r, "pd_event", None) == "world.relation_kind.canonicalized"]
if rec:
    fields = getattr(rec[0], "pd_fields", {})
    line = json.dumps(fields)
    print("serialized fields:", line)
    print("contains newline:", "\n" in line, "| contains SECRET:", "SECRET" in line)

print()
print("=== does any event leak the prompt / full raw provider output? ===")
handler.records.clear()
big = world("on_kitchen_counter")
big = big.replace("bronze ceremonial ice pick", "PROMPTSECRET_XYZ")
parse_world_requirements(big, case_id="C", attempt_id="A")
allfields = json.dumps([getattr(r, "pd_fields", {}) for r in handler.records])
print("PROMPTSECRET_XYZ in event fields:", "PROMPTSECRET_XYZ" in allfields)
print("records:", [(getattr(r, "pd_event", None), getattr(r, "pd_fields", {})) for r in handler.records])
