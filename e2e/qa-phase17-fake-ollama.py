"""QA-owned tiny fake Ollama HTTP server (e2e; Phase 17 browser stack).

The Phase 17 browser seam: a loopback-only HTTP server answering the REAL
stage prompts of the OllamaStageDriver (GENERATION_PROVIDER=ollama) so the REAL
endpoint/probe/generation path works end-to-end over REAL HTTP with zero
product-code changes and zero local Ollama installation (Phase17 §15; the
standing QA stack precedent from Phase16/16_2).

The AssetSpec chain is the Phase 17 repaired-object chain:

- the FIRST ``prompt template version asset_spec_v1`` request returns a
  SCHEMA-VALID but GEOMETRICALLY-INVALID spec (2.5 m hand-held ice pick —
  inside the Phase 13 absolute bound 0.05..4, so Phase 13 schema/security
  validation PASSES, but the Phase 17 deterministic Geometry Quality Validator
  rejects it via DECLARED_DIMENSIONS_IMPLAUSIBLE) so the ASSET_SPEC_REPAIR is
  triggered by the Phase 17 gate specifically (schema-valid !=
  geometrically-valid);
- the ASSET_SPEC_REPAIR request returns the VALID ice-pick spec -> the driver
  re-runs BOTH validations, accepts, compiles and PUBLISHES the repaired
  proc.* object.

All other stage payloads are the exact canonical non-golden Phase16_2
showcase chain (Anna Weiss / Paul Becker / office / bronze ceremonial ice
pick) that the shipped driver tests use, so the browser transcript matches
the canonical fixture chain.

Usage (via tools/process_guard, like every QA server):
    python -m tools.process_guard launch --cmd python --args e2e/qa-phase17-fake-ollama.py --port 11497 --meta <path>
  (loopback 127.0.0.1; 11497 avoids the real 11434 and the Phase16 11499 /
   Phase16_2 11498 QA ports.)
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

# One shared broken-then-repaired ASSET_SPEC counter (per-process; deterministic
# for the single showcase run this server is scripted for).
_state = {"asset_spec_broken_sent": False}


def _showcase_case_people() -> str:
    import test_ollama_driver as T  # noqa: E402
    return T._j(T._case_people())


def _showcase_evidence() -> str:
    import test_ollama_driver as T  # noqa: E402
    return T._j(T._evidence())


def _showcase_world() -> str:
    import test_ollama_driver as T  # noqa: E402
    return json.dumps({
        "environmentHint": "office", "locationTokens": ["office"],
        "objects": [{"name": "bronze ceremonial ice pick", "categoryHint": "decor",
                     "criticality": "required",
                     "requiredInteraction": "inspect",
                     "evidenceId": "forensic_icepick_match_01"}],
        "relations": [{"kind": "on_desk", "target": "bronze ceremonial ice pick"}],
        "unsafeUnsupported": [],
    }, sort_keys=True, ensure_ascii=False, indent=2)


def _asset_spec_geometry_broken() -> str:
    """Phase 17 Case-A-flavoured chain opener: SCHEMA-VALID but GEOMETRICALLY
    INVALID (2.5 m hand-held ice pick inside the 0.05..4 absolute bound). The
    Phase 13 schema/security gate passes; the Phase 17 geometry gate rejects
    DECLARED_DIMENSIONS_IMPLAUSIBLE -> ASSET_SPEC_REPAIR."""
    import test_ollama_driver as T  # noqa: E402
    bad = json.loads(T.ICEPICK_SPEC)
    bad["dimensions"] = {"x": 2.5, "y": 0.5, "z": 0.1}   # 2.5 m "ice pick"
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
                if not _state["asset_spec_broken_sent"]:
                    _state["asset_spec_broken_sent"] = True
                    content = _asset_spec_geometry_broken()
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


def _legacy_payload(stage: str) -> str:
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS, GOLDEN_FULL_DRAFT
    from app.generation.provider import GenerationStage

    table = {stage.value: payload for stage, payload in GOLDEN_STAGE_PAYLOADS.items()}
    if stage == "repair":
        return GOLDEN_FULL_DRAFT
    return table.get(stage, GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH])


def main() -> int:
    parser = argparse.ArgumentParser(description="QA fake Ollama server for Phase 17 (loopback only).")
    parser.add_argument("--port", type=int, default=11497)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())