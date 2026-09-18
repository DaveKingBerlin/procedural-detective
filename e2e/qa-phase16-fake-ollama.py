"""QA-owned tiny fake Ollama HTTP server (e2e; Phase 16 browser stack, part b).

The QA launch seam allowed by the Phase 16 task ("the QA may launch a tiny fake
Ollama HTTP server on an allowed private port that answers /api/tags and
/api/chat with scripted JSON — local-only, allowed — so the REAL endpoint/probe
work end-to-end"). Listens on 127.0.0.1 (loopback) on the given port. It serves:

- ``GET /api/tags``  -> ``{"models": [{"name": "<model>"}]}`` (scripted);
- ``POST /api/chat`` -> a 200 envelope whose ``message.content`` is the golden
  stage payload for the stage named in the inbound prompt (\"for the
  '<stage>' stage\"); REPAIR gets the golden full draft. This lets the REAL
  OllamaProvider + the REAL GenerationController run a complete generation to
  PUBLISHED over real HTTP with zero product-code changes and zero local
  Ollama installation.

The stage vocabulary is read from the real ``app.generation.provider`` enum and
the payloads come from the shipped golden fixtures (tests/fixtures).

Usage (via tools/process_guard, like every QA server):
    python -m tools.process_guard launch --cmd python --args e2e/qa-phase16-fake-ollama.py --port 11499 --meta <path>
  (any allowed loopback/private port works; 11499 avoids the real 11434.)
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

_STAGE_RE = re.compile(r"for the '([a-z_]+)' stage")

# Scripted model name (the default OLLAMA_MODEL; the operator config set by
# the QA launch must match).
MODEL = "llama3.2:3b"


def _stage_payloads():
    from app.generation.provider import GenerationStage

    from fixtures.golden_generation import (
        GOLDEN_FULL_DRAFT,
        GOLDEN_STAGE_PAYLOADS,
    )

    table = {
        stage.value: payload for stage, payload in GOLDEN_STAGE_PAYLOADS.items()
    }
    table["repair"] = GOLDEN_FULL_DRAFT
    return table


STAGE_PAYLOADS = _stage_payloads()


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
        prompts = [
            m.get("content", "") for m in payload.get("messages") or [] if isinstance(m, dict)
        ]
        joined = "\n".join(prompts)
        match = _STAGE_RE.search(joined)
        stage = match.group(1) if match else "case_truth"
        content = STAGE_PAYLOADS.get(stage, STAGE_PAYLOADS["case_truth"])
        self._send_json(
            200,
            {
                "model": payload.get("model") or MODEL,
                "message": {"role": "assistant", "content": content},
                "done": True,
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="QA fake Ollama server (loopback only).")
    parser.add_argument("--port", type=int, default=11499)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())