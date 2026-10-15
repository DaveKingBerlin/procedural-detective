"""ADV-43B probe 1 — capability over-claim / routing (FR-01..06).

Hermetic: monkeypatches frontier_provider.httpx.post. ZERO network.
Run from repo root:  python screenshots/evidence/adv43b/probe1_capability.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.generation import frontier_provider as fp  # noqa: E402
from app.generation import schema_adapters as sa  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation.frontier_registry import (  # noqa: E402
    FRONTIER_PROVIDER_REGISTRY,
    frontier_provider_capabilities,
)
from app.generation.provider import GenerateRequest, GenerationStage  # noqa: E402


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def iter_bytes(self, chunk_size):
        yield self._body


_CAPTURED = []


def _post(url, json=None, headers=None, timeout=None):
    _CAPTURED.append({"url": url, "body": json, "headers": headers})
    return _Resp('{"a":"x"}')


fp.httpx.post = _post


def make(provider_id, *, model="deepseek/deepseek-v4.1-flash",
         endpoint="https://openrouter.ai/api/v1/chat/completions"):
    return fp.FrontierProvider(
        endpoint_url=endpoint,
        api_key="SECRET-API-KEY-XYZ",
        model=model,
        timeout_seconds=30,
        structured_output_mode="openai_json_schema",
        provider_id=provider_id,
    )


def req(schema, stage=GenerationStage.EVIDENCE, schema_id="S_v1"):
    return GenerateRequest(
        attempt_id="GA-adv", stage=stage, prompt_context="ctx",
        json_schema=schema, schema_id=schema_id,
    )


SIMPLE = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def sect(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


sect("ATTACK 1a — every real stage schema: strict-compatible? (closed projection)")
for st in ("case_truth", "evidence", "public_world", "world_graph", "repair"):
    canon = prompts.json_schema_for_stage_output(st)
    closed = sa.close_transport_modeled_objects(canon)
    compat = sa.openai_strict_schema_is_compatible(closed)
    print(f"  stage={st:12s} strict_compatible={compat}")

sect("ATTACK 1b — model-level vs provider-level strict (FR-02)")
for model in ("deepseek/deepseek-v4.1-flash", "cohere/command-a-plus",
              "some/unknown-model-v0", "anthropic/claude-3.5-sonnet"):
    p = make("openrouter", model=model)
    _CAPTURED.clear()
    p.generate(req(SIMPLE))
    body = _CAPTURED[-1]["body"]
    jb = body["response_format"]["json_schema"]
    print(f"  model={model:35s} strict={jb.get('strict')!r} "
          f"provider={body.get('provider')!r} "
          f"caps_native={p._capabilities.supports_native_strict_json_schema}")

sect("ATTACK 1c — non-OpenRouter request must never receive OpenRouter provider object")
for d in FRONTIER_PROVIDER_REGISTRY:
    p = make(d.provider_id, endpoint=d.endpoint)
    _CAPTURED.clear()
    p.generate(req(SIMPLE))
    body = _CAPTURED[-1]["body"]
    print(f"  provider={d.provider_id:11s} provider_obj={body.get('provider')!r} "
          f"strict={body['response_format']['json_schema'].get('strict')!r}")

sect("ATTACK 1d — direct-construction MISMATCH: provider_id=openrouter + foreign endpoint")
p = make("openrouter", model="deepseek/deepseek-v4.1-flash",
         endpoint="https://api.openai.com/v1/chat/completions")
_CAPTURED.clear()
p.generate(req(SIMPLE))
body = _CAPTURED[-1]["body"]
print(f"  endpoint=api.openai.com provider_id=openrouter -> provider_obj={body.get('provider')!r}")
print(f"  strict={body['response_format']['json_schema'].get('strict')!r}")

sect("ATTACK 1e — direct-construction MISMATCH: provider_id=None/unknown, endpoint=openrouter")
for pid in (None, "", "does-not-exist"):
    p = make(pid)
    _CAPTURED.clear()
    p.generate(req(SIMPLE))
    body = _CAPTURED[-1]["body"]
    print(f"  provider_id={pid!r:16s} provider_obj={body.get('provider')!r} "
          f"strict={body['response_format']['json_schema'].get('strict')!r}")

sect("ATTACK 1f — capability helper fail-closed checks")
for pid in (None, "", "openrouter", "openai", "does-not-exist", 123):
    caps = frontier_provider_capabilities(pid)
    print(f"  {str(pid)!r:16s} -> accepts={caps.accepts_response_format} "
          f"jschema={caps.supports_json_schema} "
          f"strict={caps.supports_native_strict_json_schema} "
          f"gate={caps.supports_openrouter_parameter_gating}")

sect("ATTACK 1g — response_format acceptance interpreted as strict? (registry booleans)")
overclaims = [
    d.provider_id for d in FRONTIER_PROVIDER_REGISTRY
    if d.accepts_response_format and d.supports_native_strict_json_schema
    and d.provider_id != "openrouter"
]
print("  non-openrouter overclaim:", overclaims or "NONE")

sect("ATTACK 1h — adapter reads server-owned capability value only")
print("  openrouter _capabilities =", make("openrouter")._capabilities)
print("  FAIL_CLOSED id='' ->", frontier_provider_capabilities(None).provider_id)

sect("ATTACK 1i — key/header secret safety in captured bodies")
p = make("openrouter")
_CAPTURED.clear()
p.generate(req(SIMPLE))
raw = json.dumps(_CAPTURED[-1])
print("  body contains api key token:", "SECRET-API-KEY-XYZ" in raw)
print("  body contains Authorization :", "Authorization" in raw)
print("  body keys:", sorted(_CAPTURED[-1]["body"].keys()))

print("\nDONE")
