"""QA-owned tiny fake Ollama HTTP server (e2e; Phase 16_2 browser stack).

The Phase 16_2 browser seam: a loopback-only HTTP server answering the REAL
driver-stage prompts of the OllamaStageDriver (GENERATION_PROVIDER=ollama) so
the REAL endpoint/probe/generation path works end-to-end over REAL HTTP with
zero product-code changes and zero local Ollama installation (Phase16_2 §24/§26:
normal automated tests stay network-free; this is the QA-operator seam).

It answers:

- ``GET /api/tags``  -> ``{"models": [{"name": "llama3.2:3b"}]}`` so the REAL
  capability probe reports local available;
- ``POST /api/chat`` -> the stage payload for the inbound driver prompt,
  detected from the prompt text:

    - "prompt template version case_people_v1"       -> CASE/PEOPLE combined
      doc (crime + persons + motives + locations + travelRules + scene);
    - "prompt template version evidence_v1"          -> EVIDENCE doc;
    - "prompt template version world_requirements_v1"-> WORLD_REQUIREMENTS doc
      (environmentHint + objects + relations + unsafeUnsupported);
    - "prompt template version asset_spec_v1"        -> the FIRST request gets
      an INVALID AssetSpec (25 m dimensions + bronze material + forward
      parent) and every later ASSET_SPEC request gets a VALID ice-pick spec
      (so a run with repair consumes exactly invalid->repair->valid);
    - "prompt template version asset_spec_repair_v1" -> the VALID ice-pick spec.

  Two scripted chains are served, selected by the inbound sanitized prompt:

    - the GOLDEN demo chain (Sarah/Thomas/apartment; all objects known) when
      the prompt names Sarah Miller / sarah_miller — this is what the
      phase16-modes P16B "Try Demo Case" journey runs through the driver;
    - the Phase16_2 SHOWCASE chain (Anna Weiss / Paul Becker / office /
      bronze ceremonial ice pick + repair) when the prompt names Anna Weiss /
      Paul Becker — the phase162-ollama-driver showcase.

  Legacy "for the '<stage>' stage" marker prompts (phase16-era fake flows) fall
  back to the golden per-stage payloads so the phase16 server behavior is
  preserved for any old flow that still uses it.

Each response is the same deterministic JSON the shipped driver tests use
(backend/tests/test_ollama_driver.py), so the browser transcripts are EXACTLY
the canonical Phase16_2 fixture chain.

Usage (via tools/process_guard, like every QA server):
    python -m tools.process_guard launch --cmd python --args e2e/qa-phase162-fake-ollama.py --port 11498 --meta <path>
  (loopback 127.0.0.1; 11498 avoids the real 11434 and the phase16 11499.)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tests"))

MODEL = "llama3.2:3b"
_DRIVER_RE = re.compile(r"prompt template version ([a-z_]+)_v1")
_LEGACY_RE = re.compile(r"for the '([a-z_]+)' stage")

# One shared invalid-then-valid ASSET_SPEC counter (per-process; deterministic
# for the single showcase run this server is scripted for).
_state = {"asset_spec_invalid_sent": False}


def _golden_case_people() -> str:
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS
    from app.generation.provider import GenerationStage

    case = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH])
    pub = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD])
    doc = dict(case)
    for k in ("persons", "motives", "locations", "travelRules", "scene"):
        doc[k] = pub.get(k)
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=2)


def _golden_evidence() -> str:
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS
    from app.generation.provider import GenerationStage

    return GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE]


def _golden_world() -> str:
    return json.dumps({
        "environmentHint": "apartment", "locationTokens": ["apartment"],
        "objects": [{"name": "kitchen knife", "criticality": "decorative"}],
        "relations": [], "unsafeUnsupported": [],
    }, sort_keys=True, ensure_ascii=False, indent=2)


def _showcase_case_people() -> str:
    import test_ollama_driver as T  # noqa: E402
    return T._j(T._case_people())


def _showcase_evidence() -> str:
    import test_ollama_driver as T  # noqa: E402
    return T._j(T._evidence())


def _showcase_world() -> str:
    return json.dumps({
        "environmentHint": "office", "locationTokens": ["office"],
        "objects": [{"name": "bronze ceremonial ice pick", "categoryHint": "decor",
                     "criticality": "required",
                     "requiredInteraction": "inspect",
                     "evidenceId": "forensic_icepick_match_01"}],
        "relations": [{"kind": "on_desk", "target": "bronze ceremonial ice pick"}],
        "unsafeUnsupported": [],
    }, sort_keys=True, ensure_ascii=False, indent=2)


def _asset_spec_invalid() -> str:
    import test_ollama_driver as T  # noqa: E402

    bad = json.loads(T.ICEPICK_SPEC)
    bad["dimensions"] = {"x": 25, "y": 0.1, "z": 0.1}     # 25 m unit regression
    bad["parts"][0]["material"] = "bronze"                 # not allowlisted
    bad["parts"][1]["parentId"] = "part_02"                # forward parent
    return json.dumps(bad, sort_keys=True, ensure_ascii=False, indent=2)


def _asset_spec_valid() -> str:
    import test_ollama_driver as T  # noqa: E402
    return T.ICEPICK_SPEC


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep logs off the QA stack
        pass

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/tags":
            self._send_json(200, {"models": [{"name": MODEL}]})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/chat":
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": "bad request"})
            return
        prompts = [m.get("content", "") for m in payload.get("messages") or [] if isinstance(m, dict)]
        joined = "\n".join(prompts)
        is_showcase = ("Anna Weiss" in joined) or ("Paul Becker" in joined) or ("bronze ceremonial ice pick" in joined)

        driver = _DRIVER_RE.search(joined)
        if driver is not None:
            stage = driver.group(1)
            if stage == "case_people":
                content = _showcase_case_people() if is_showcase else _golden_case_people()
            elif stage == "evidence":
                content = _showcase_evidence() if is_showcase else _golden_evidence()
            elif stage == "world_requirements":
                content = _showcase_world() if is_showcase else _golden_world()
            elif stage == "asset_spec_repair":
                content = _asset_spec_valid()
            elif stage == "asset_spec":
                if not _state["asset_spec_invalid_sent"]:
                    _state["asset_spec_invalid_sent"] = True
                    content = _asset_spec_invalid()
                else:
                    content = _asset_spec_valid()
            elif stage == "repair":
                content = _golden_case_people()
            else:
                content = "<not-json>"
        else:
            legacy = _LEGACY_RE.search(joined)
            if legacy is not None:
                content = _legacy_payload(legacy.group(1))
            else:
                content = _golden_case_people()
        self._send_json(200, {
            "model": payload.get("model") or MODEL,
            "message": {"role": "assistant", "content": content},
            "done": True,
        })


def _legacy_payload(stage: str) -> str:
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS, GOLDEN_FULL_DRAFT
    from app.generation.provider import GenerationStage

    table = {
        stage.value: payload for stage, payload in GOLDEN_STAGE_PAYLOADS.items()
    }
    if stage == "repair":
        return GOLDEN_FULL_DRAFT
    return table.get(stage, GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH])


def main() -> int:
    parser = argparse.ArgumentParser(description="QA fake Ollama server (loopback only).")
    parser.add_argument("--port", type=int, default=11498)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())