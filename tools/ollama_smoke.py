"""Opt-in real-Ollama smoke CLI (Phase16 L).

Usage (from the repository root):

    python -m tools.ollama_smoke --enable [--out report.json]

It is STRICTLY opt-in: without ``--enable`` (or the env flag
``OLLAMA_SMOKE_ENABLED=1``) it prints ``skipped (opt-in)`` and exits 0. Normal
CI never runs it (opt-in AND the backend test suite's autouse network block
makes real calls impossible anyway).

When enabled it:

1. probes the configured Ollama (``ollama_available`` — real transport);
2. when available, generates ONE small structured CASE_TRUTH stage response
   through the REAL ``OllamaProvider`` adapter;
3. runs that response through the strict parser (``parser.collect_issues``);
4. emits a sanitized JSON report to stdout (or ``--out``).

The report NEVER contains prompts, keys, the base URL or any network detail —
only availability, the public-safe model display name and the (sanitized)
parse issue count.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# tools/ -> repo root -> backend (so the editable install / source tree is
# importable regardless of the invocation CWD).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _sanitized_report(provider, settings, probe_available: bool) -> dict:
    """One small real CALE_TRUTH generation through the real adapter + strict
    parser. Never includes the prompt itself, the base URL or the raw output."""
    from app.generation.parser import collect_issues
    from app.generation.provider import GenerateRequest, GenerationStage

    request = GenerateRequest(
        attempt_id="ollama-smoke",
        stage=GenerationStage.CASE_TRUTH,
        prompt_context=(
            "Generate a fresh, self-consistent minimal detective case: one "
            "victim, one murderer, one real motive, one weapon and one crime "
            "location with a canonical timestamp. Return ONLY the documented "
            "JSON object."
        ),
    )
    result = provider.generate(request)
    entry: dict = {
        "stage": GenerationStage.CASE_TRUTH.value,
        "providerResult": result.content is not None,
    }
    if result.error is not None:
        entry["errorSanitized"] = str(result.error)[:200]
    if result.timed_out:
        entry["errorSanitized"] = "timed out"
    if result.content is not None:
        issues = collect_issues(GenerationStage.CASE_TRUTH, result.content)
        entry["parseIssues"] = len(issues)
        entry["parsedOk"] = not issues
    return {
        "enabled": True,
        "provider": "ollama",
        "probeAvailable": probe_available,
        "model": str(getattr(settings, "ollama_model", "") or ""),
        "generation": entry,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools.ollama_smoke",
        description=(
            "Opt-in smoke test of the local Ollama generation provider. "
            "Requires --enable or OLLAMA_SMOKE_ENABLED=1; never runs in CI."
        ),
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        help="actually run the smoke test (otherwise: skipped (opt-in)).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="optional JSON output file path (report also printed to stdout).",
    )
    args = parser.parse_args(argv)
    enabled = args.enable or os.environ.get("OLLAMA_SMOKE_ENABLED") == "1"
    if not enabled:
        print("skipped (opt-in)")
        return 0

    from app.core.config import Settings
    from app.generation.ollama_provider import OllamaProvider, ollama_available

    settings = Settings(generation_provider="ollama")
    probe_available, _detail = ollama_available(settings)
    report = {
        "enabled": True,
        "provider": "ollama",
        "probeAvailable": probe_available,
        "model": str(settings.ollama_model),
        "generation": None,
    }
    if probe_available:
        provider = OllamaProvider(
            base_url=str(settings.ollama_base_url or "http://127.0.0.1:11434"),
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            temperature=settings.ollama_temperature,
            num_ctx=settings.ollama_num_ctx,
        )
        report = _sanitized_report(provider, settings, probe_available=True)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())