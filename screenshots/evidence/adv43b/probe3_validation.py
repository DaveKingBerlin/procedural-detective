"""ADV-43B probe 3 — local validation bypass + bounded validator (FR-11/12/13).

Hermetic: monkeypatches frontier_provider.httpx.post. ZERO network.
Run from repo root: python screenshots/evidence/adv43b/probe3_validation.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation import frontier_provider as fp  # noqa: E402
from app.generation import schema_adapters as sa  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation import transport_schema as ts  # noqa: E402
from app.assets import depthguard  # noqa: E402
from app.generation.provider import GenerateRequest, GenerationStage  # noqa: E402


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def iter_bytes(self, chunk_size):
        yield self._body


_BODIES = []


def _post(url, json=None, headers=None, timeout=None):
    return _Resp(_BODIES.pop(0) if _BODIES else "<not-json>")


fp.httpx.post = _post


def provider():
    return fp.FrontierProvider(
        endpoint_url="https://openrouter.ai/api/v1/chat/completions",
        api_key="K", model="deepseek/deepseek-v4.1-flash",
        timeout_seconds=30, structured_output_mode="openai_json_schema",
        provider_id="openrouter",
    )


def req(schema):
    return GenerateRequest(
        attempt_id="GA-adv", stage=GenerationStage.EVIDENCE,
        prompt_context="ctx", json_schema=schema, schema_id="S_v1",
    )


SIMPLE = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def sect(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def probe_body(label, body, schema=SIMPLE):
    _BODIES.clear()
    _BODIES.append(body)
    p = provider()
    _BODIES.clear()
    _BODIES.append(body)
    try:
        r = p.generate(req(schema))
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:38s} RAISED {type(exc).__name__}: {exc}")
        return
    print(f"  {label:38s} extracted={p.last_content_extracted} "
          f"decoded={p.last_json_decoded} tsv={p.last_transport_schema_valid} "
          f"content_len={len(r.content or '')}")


sect("ATTACK 3a — HTTP 200 bodies that decode but violate schema -> NOT structured success")
probe_body("wrong type a=123", '{"a":123}')
probe_body("missing required a", '{}')
probe_body("extra key", '{"a":"x","zzz":1}')
probe_body("context echo", json.dumps({"caseTruth": {}, "lockedConstraints": {},
                                       "prompt": "...", "promptNote": "..."}))
probe_body("valid", '{"a":"x"}')

sect("ATTACK 3b — valid JSON but not an object")
probe_body("array [1,2,3]", '[1,2,3]')
probe_body("string \"hi\"", '"hi"')
probe_body("number 123", '123')
probe_body("null", 'null')
probe_body("bool true", 'true')

sect("ATTACK 3c — non-finite numbers (json.loads accepts NaN/Infinity by default)")
probe_body("a=NaN", '{"a":NaN}')
probe_body("a=Infinity", '{"a":Infinity}')
probe_body("a=-Infinity", '{"a":-Infinity}')
probe_body("nested NaN", '{"a":"x","b":{"c":NaN}}')

sect("ATTACK 3d — malformed / truncated / empty")
probe_body("empty string", "")
probe_body("whitespace", "   ")
probe_body("truncated", '{"a":')
probe_body("not-json", "<not-json>")
probe_body("envelope with content=json-invalid",
           json.dumps({"choices": [{"message": {"content": "{\"a\":123}"}}]}))

sect("ATTACK 3e — direct validator: pathological schemas/instances (bounded?)")
# a) schema with deeply nested items
def deep_items(n):
    node = {"type": "string"}
    for _ in range(n):
        node = {"type": "array", "items": node}
    return {"type": "object", "properties": {"x": node}, "required": ["x"]}

def deep_instance(n):
    v = "s"
    for _ in range(n):
        v = [v]
    return {"x": v}

for n in (5, depthguard.MAX_STRUCT_NESTING + 20, 5000):
    schema = deep_items(n)
    inst = deep_instance(n)
    t0 = time.perf_counter()
    try:
        issues = ts.validate_transport_schema(inst, schema)
        out = f"issues={len(issues)} sample={issues[:1]}"
    except Exception as exc:  # noqa: BLE001
        out = f"RAISED {type(exc).__name__}: {exc}"
    print(f"  n={n:6d} elapsed={time.perf_counter()-t0:.3f}s {out}")

# b) schema with cyclic reference (Python object cycle)
cyclic = {}
cyclic["type"] = "object"
cyclic["properties"] = {"self": cyclic}
inst = {}
inst["self"] = inst
t0 = time.perf_counter()
try:
    issues = ts.validate_transport_schema(inst, cyclic)
    out = f"issues={len(issues)}"
except Exception as exc:  # noqa: BLE001
    out = f"RAISED {type(exc).__name__}: {exc}"
print(f"  cyclic schema+instance elapsed={time.perf_counter()-t0:.3f}s {out}")

# c) huge flat instance vs node budget
big = {"type": "object", "properties": {"xs": {"type": "array", "items": {"type": "object", "properties": {"k": {"type": "string"}}}}}, "required": ["xs"]}
inst = {"xs": [{"k": "v"} for _ in range(200000)]}
t0 = time.perf_counter()
issues = ts.validate_transport_schema(inst, big)
print(f"  node-budget huge array elapsed={time.perf_counter()-t0:.3f}s issues={issues[:2]}")
print("  MAX_TRANSPORT_VALIDATION_NODES =", ts.MAX_TRANSPORT_VALIDATION_NODES,
      "MAX_STRUCT_NESTING =", depthguard.MAX_STRUCT_NESTING)

sect("ATTACK 3f — closure / strict-compat on pathological schemas (recursion bound)")
for label, schema in [
    ("deep_items", deep_items(depthguard.MAX_STRUCT_NESTING + 20)),
    ("cyclic", cyclic),
    ("non-mapping", "nope"),
]:
    for fn in (sa.close_transport_modeled_objects, sa.openai_strict_schema_is_compatible):
        t0 = time.perf_counter()
        try:
            res = fn(schema)
            out = type(res).__name__
        except Exception as exc:  # noqa: BLE001
            out = f"RAISED {type(exc).__name__}: {exc}"
        print(f"  {label:14s} {fn.__name__:38s} elapsed={time.perf_counter()-t0:.3f}s -> {out}")

sect("ATTACK 3g — invalid pattern in schema is skipped, never a false rejection")
schema = {"type": "object", "properties": {"a": {"type": "string", "pattern": "([unclosed"}}, "required": ["a"]}
print("  invalid pattern:", ts.validate_transport_schema({"a": "x"}, schema))

sect("ATTACK 3h — bool is not integer/number (JSON semantics)")
schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
print("  n=True rejected:", bool(ts.validate_transport_schema({"n": True}, schema)))
print("  n=1.0 accepted:", ts.validate_transport_schema({"n": 1.0}, schema) == ())
print("  n=1.5 rejected:", bool(ts.validate_transport_schema({"n": 1.5}, schema)))

print("\nDONE")
