"""ADV-43B probe 6 — pending/deferred completion cannot bypass the stage stop.

Uses the deterministic FakeProvider (no network). Run from repo root:
  python screenshots/evidence/adv43b/probe6_pending.py
"""
from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from app.core.observability import SERVICE_LOGGER_NAME, JsonEventFormatter  # noqa: E402
from app.generation.admission import AdmissionController  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.provider import GenerationStage, ProviderResult  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS as G  # noqa: E402

PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)
CONTEXT_ECHO = json.dumps({"caseTruth": {}, "lockedConstraints": {}, "prompt": "...", "promptNote": "..."})


def sect(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def _controller(provider):
    clock = ManualClock(); ids = IdSource()
    admission = AdmissionController(clock=clock, ids=ids, max_concurrent_generations=1,
        max_concurrent_generations_global=3, max_generations_per_session_per_window=3,
        max_generations_global_per_window=20, anonymous_quota_session_ttl_seconds=86400)
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(provider=provider, admission=admission, clock=clock,
        ids=ids, deadline_seconds=60, max_llm_calls_per_generation=8, max_repair_passes=2,
        max_full_regenerations=1, max_prompt_chars=4000, seed=11, provider_name="fake",
        provider_model="fake-model", provider_sub_id=None)
    return controller, session


def _capture():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(JsonEventFormatter())
    logging.getLogger().addHandler(handler)
    logging.getLogger(SERVICE_LOGGER_NAME).setLevel(logging.DEBUG)
    return buf, handler


def _events(text):
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _stages(events):
    return [e["stage"] for e in events
            if e.get("event") == "provider.call.start" and "stage" in e]


sect("ATTACK 4d-1 — pending CASE_TRUTH resolved MALFORMED must stop downstream")
buf, handler = _capture()
try:
    fake = FakeProvider({GenerationStage.CASE_TRUTH: ["pending"],
                         GenerationStage.PUBLIC_WORLD: ["pending"],
                         GenerationStage.EVIDENCE: ["pending"],
                         GenerationStage.WORLD_GRAPH: ["pending"]})
    controller, session = _controller(fake)
    fake._sink = controller
    h = controller.start_generation(PROMPT, anonymous_quota_session_id=session.session_id)
    pid = fake.pending_ids()[0]
    fake.resolve(pid, ProviderResult(content="<not-json>"))
    rec = controller.attempt(h.attempt_id)
    events = _events(buf.getvalue())
    print("  stages after pending malformed case_truth:", _stages(events))
    print("  state:", rec.state, "pending:", rec.pending_id)
    print("  public_world/evidence/world_graph never started:",
          not any(s in _stages(events) for s in ("public_world", "evidence", "world_graph")))
finally:
    logging.getLogger().removeHandler(handler)

sect("ATTACK 4d-2 — pending EVIDENCE resolved CONTEXT-ECHO (valid case/public) -> completion, not accepted echo")
buf, handler = _capture()
try:
    fake = FakeProvider({
        GenerationStage.CASE_TRUTH: ["pending", G[GenerationStage.CASE_TRUTH]],
        GenerationStage.PUBLIC_WORLD: ["pending", G[GenerationStage.PUBLIC_WORLD]],
        GenerationStage.EVIDENCE: ["pending", "malformed", "malformed"],
        GenerationStage.WORLD_GRAPH: ["malformed", "malformed"],
    })
    controller, session = _controller(fake)
    fake._sink = controller
    h = controller.start_generation(PROMPT, anonymous_quota_session_id=session.session_id)
    for _ in range(4):
        pids = fake.pending_ids()
        if not pids:
            break
        # resolve each pending with the proper stage content
        pid = pids[0]
        rec = controller.attempt(h.attempt_id)
        stage = rec.pending_stage
        if stage is GenerationStage.CASE_TRUTH:
            fake.resolve(pid, ProviderResult(content=G[GenerationStage.CASE_TRUTH]))
        elif stage is GenerationStage.PUBLIC_WORLD:
            fake.resolve(pid, ProviderResult(content=G[GenerationStage.PUBLIC_WORLD]))
        elif stage is GenerationStage.EVIDENCE:
            fake.resolve(pid, ProviderResult(content=CONTEXT_ECHO))
        elif stage is GenerationStage.WORLD_GRAPH:
            fake.resolve(pid, ProviderResult(content="<not-json>"))
    rec = controller.attempt(h.attempt_id)
    events = _events(buf.getvalue())
    print("  stage sequence:", _stages(events))
    print("  state:", rec.state, "EVIDENCE trusted:", GenerationStage.EVIDENCE in rec.stage_done)
    comp = [e for e in events if e.get("event") == "evidence.completion.completed"]
    print("  completion events:", len(comp))
    validated = [e for e in events if e.get("event") == "provider.structured_output.validated"
                 and e.get("stage") == "evidence"]
    print("  evidence structured_output.validated:", len(validated))
finally:
    logging.getLogger().removeHandler(handler)

print("\nDONE")
