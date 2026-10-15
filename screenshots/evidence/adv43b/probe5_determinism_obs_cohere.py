"""ADV-43B probe 5 — determinism/bounds + observability/secret safety + Cohere regression.

Hermetic. Run from repo root:
  python screenshots/evidence/adv43b/probe5_determinism_obs_cohere.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from app.generation import prompts  # noqa: E402
from app.generation import schema_adapters as sa  # noqa: E402
from app.generation import transport_schema as ts  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402


def sect(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


# --------------------------------------------------------------------------- #
# ATTACK 8 — determinism of schema projection + validation across processes
# --------------------------------------------------------------------------- #
STAGES = ("case_truth", "public_world", "evidence", "world_graph", "repair")


def _digest() -> str:
    h = hashlib.sha256()
    for st in STAGES:
        canon = prompts.json_schema_for_stage_output(st)
        closed = sa.close_transport_modeled_objects(canon)
        h.update(json.dumps(closed, sort_keys=True).encode())
        h.update(b"|")
        coh = sa.adapt_schema_for_transport(
            canon, adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
        )
        h.update(json.dumps(coh, sort_keys=True).encode())
        h.update(b"|")
        # a validation outcome across a fixed hostile instance
        issues = ts.validate_transport_schema(
            {"persons": [{"description": "x"}], "zzz": 1}, closed
        )
        h.update(repr(issues).encode())
        h.update(b"|")
    return h.hexdigest()


sect("ATTACK 8a — schema projection + validation digest across processes/hash seeds")
if os.environ.get("ADV43B_CHILD") == "1":
    print("  child digest:", _digest())
    sys.exit(0)

digests = []
for seed in ("0", "12345", "random"):
    env = dict(os.environ, PYTHONHASHSEED=seed, ADV43B_CHILD="1")
    out = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )
    line = [l for l in out.stdout.splitlines() if "child digest" in l]
    digests.append(line[0].split(":", 1)[1].strip() if line else f"ERR {out.stderr[-200:]}")
for seed, d in zip(("0", "12345", "random"), digests):
    print(f"  PYTHONHASHSEED={seed:7s} {d}")
print("  all identical:", len(set(digests)) == 1)

# --------------------------------------------------------------------------- #
# ATTACK 6 — semantic breadth: prove NO byte-size heuristic is consulted
# --------------------------------------------------------------------------- #
sect("ATTACK 6 — semantic gate consults meaning, not size")
import inspect  # noqa: E402
from app.generation import pipeline  # noqa: E402

src = inspect.getsource(pipeline.public_world_semantic_breadth_issues)
import ast  # noqa: E402
tree = ast.parse(src)
code_names = {
    n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
} | {
    n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
} | {
    n.value.id for n in ast.walk(tree)
    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
}
size_tokens = sorted(
    t for t in code_names
    if t in ("len", "bytes", "byte_size", "size", "byteLength")
    or "size" in t.casefold() or "byte" in t.casefold()
)
print("  size/byte-related NAMES in function CODE (not docstring):",
      size_tokens or "NONE")
print("  params of semantic function:", list(
    inspect.signature(pipeline.public_world_semantic_breadth_issues).parameters))

# --------------------------------------------------------------------------- #
# ATTACK 10 — Cohere wire schema vs PRE-Phase43B baseline
# --------------------------------------------------------------------------- #
sect("ATTACK 10 — Cohere wire schema vs pre-Phase43B baseline (per stage)")

baseline_src = subprocess.run(
    ["git", "show", "HEAD:backend/app/generation/prompts.py"],
    capture_output=True, cwd=str(ROOT),
)
bpath = ROOT / "screenshots" / "evidence" / "adv43b" / "_baseline_prompts.py"
bpath.write_text(baseline_src.stdout.decode("utf-8"), encoding="utf-8")
spec = importlib.util.spec_from_file_location("adv43b_baseline_prompts", bpath)
baseline_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline_mod)

for st in STAGES:
    cur_canon = prompts.json_schema_for_stage_output(st)
    base_canon = baseline_mod.json_schema_for_stage_output(st)
    cur_wire = sa.adapt_schema_for_transport(
        cur_canon, adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
    )
    base_wire = sa.adapt_schema_for_transport(
        base_canon, adapter=sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
    )
    canon_same = json.dumps(cur_canon, sort_keys=True) == json.dumps(base_canon, sort_keys=True)
    wire_same = json.dumps(cur_wire, sort_keys=True) == json.dumps(base_wire, sort_keys=True)
    extra = sorted(set(cur_canon.get("properties", {})) - set(base_canon.get("properties", {})))
    # object-node required-property rule (Cohere Track A): every object node
    # must carry >= 1 required property
    def obj_nodes(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for v in node.values():
                yield from obj_nodes(v)
        elif isinstance(node, list):
            for v in node:
                yield from obj_nodes(v)
    weak = [n for n in obj_nodes(cur_wire) if not n.get("required")]
    print(f"  stage={st:12s} canonical_same={canon_same} cohere_wire_same={wire_same} "
          f"new_top_keys={extra} weak_objects={len(weak)}")

# --------------------------------------------------------------------------- #
# ATTACK 9 — observability + secret safety (controller run)
# --------------------------------------------------------------------------- #
sect("ATTACK 9 — observability events contain no secret/raw content/hidden truth")
os.environ["ADV43B_CHILD"] = "0"
from _pytest.monkeypatch import MonkeyPatch  # noqa: E402
from test_phase43b_structured_output import (  # noqa: E402
    _PROMPT, _Recorder, _run_frontier,
)
from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS as G  # noqa: E402

queue = [
    G[GenerationStage.CASE_TRUTH],
    G[GenerationStage.PUBLIC_WORLD],
    json.dumps({"caseTruth": {}, "lockedConstraints": {}, "prompt": "RAWSECRETPROMPT", "promptNote": "n"}),
    "malformed",
    "malformed",
]
mp = MonkeyPatch()
try:
    _controller, _rec, _handle, events = _run_frontier(mp, queue)
finally:
    mp.undo()

ALLOWED_NEW = {
    "transportSchemaValid", "contentExtracted", "jsonDecoded", "strictRequested",
    "requireParametersRequested", "providerCapabilityMode", "stageAccepted",
    "canonicalParseValid", "semanticBreadthValid",
}
seen_new = set()
for e in events:
    seen_new |= (set(e) & ALLOWED_NEW)
print("  new provenance fields observed:", sorted(seen_new))
raw = json.dumps(events)
for token in ("P43B-KEY", "SECRET", "Authorization", "Bearer", "apiKey", "api_key",
              "RAWSECRETPROMPT", "caseTruth", "lockedConstraints"):
    print(f"  events contain {token!r}: {token in raw}")
# hidden truth: cruelest check — murderer id/name must not appear in events
for token in ("thomas_reed", "sarah_miller", "cover_up_embezzlement", "kitchen_knife"):
    print(f"  events contain hidden-truth token {token!r}: {token in raw}")
# the raw provider content object (context echo) shapes
for token in ("evidence", "propositions"):
    # these may appear as stage names/ids; report but not fatal
    pass
# events that mention context echo key names (raw content leak)
for token in ("promptNote", "caseTruth"):
    print(f"  events contain raw-content key {token!r}: {token in raw}")

print("\nDONE")
