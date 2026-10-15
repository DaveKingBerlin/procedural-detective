"""ADV-43B probe 8 — provider-level deep/large instance bombs (bounded?).

Hermetic (mocked httpx). Run from repo root:
  python screenshots/evidence/adv43b/probe8_bombs.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation import frontier_provider as fp  # noqa: E402
from app.generation.provider import GenerateRequest, GenerationStage  # noqa: E402


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def iter_bytes(self, chunk):
        yield self._body


_BODIES = []


def _post(url, json=None, headers=None, timeout=None):
    return _Resp(_BODIES.pop(0))


fp.httpx.post = _post

p = fp.FrontierProvider(
    endpoint_url="https://openrouter.ai/api/v1/chat/completions",
    api_key="K", model="deepseek/deepseek-v4.1-flash",
    timeout_seconds=30, structured_output_mode="openai_json_schema",
    provider_id="openrouter",
)
schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
req = GenerateRequest(attempt_id="G", stage=GenerationStage.EVIDENCE,
                      prompt_context="c", json_schema=schema, schema_id="S")

cases = [
    ("deep arrays 100000", "[" * 100000 + "]" * 100000),
    ("deep arrays 500", "[" * 500 + "]" * 500),
    ("deep mixed 50000", "[" * 50000 + "]" * 50000),
    ("huge keys 200000", json.dumps({f"k{i}": i for i in range(200000)})),
    ("flat long string", json.dumps({"a": "x" * 250000})),
]
for label, body in cases:
    _BODIES.append(body)
    t0 = time.perf_counter()
    try:
        r = p.generate(req)
        out = (f"extracted={p.last_content_extracted} decoded={p.last_json_decoded} "
               f"tsv={p.last_transport_schema_valid} len={len(r.content)}")
    except Exception as exc:  # noqa: BLE001
        out = f"RAISED {type(exc).__name__}: {str(exc)[:70]}"
    print(f"  {label:22s} {time.perf_counter()-t0:.3f}s {out}")

# oversized response cap
_BODIES.append("x" * (fp.MAX_FRONTIER_RESPONSE_BYTES + 1000))
t0 = time.perf_counter()
r = p.generate(req)
print(f"  oversized > cap         {time.perf_counter()-t0:.3f}s "
      f"len={len(r.content)} error={r.error} timed_out={r.timed_out}")

print("\nDONE")
