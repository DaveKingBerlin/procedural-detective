"""Opt-in real-Ollama smoke CLI (Phase16 L / Phase16_2 §24 / Phase17 §13-§19).

Usage (from the repository root):

    python -m tools.ollama_smoke --enable [--out report.json]
    python -m tools.ollama_smoke --enable --roundtrip        # + CASE/PEOPLE & ASSET_SPEC round-trip
    python -m tools.ollama_smoke --enable --geometry-repair  # + Phase 17 geometry repair round-trip
    python -m tools.ollama_smoke --showcase-steps            # print the manual Local-AI showcase E2E steps

It is STRICTLY opt-in: without ``--enable`` (or the env flag
``OLLAMA_SMOKE_ENABLED=1``) it prints ``skipped (opt-in)`` and exits 0. Normal
CI never runs it (opt-in AND the backend test suite's autouse network block
makes real calls impossible anyway).

When enabled it:

1. probes the configured Ollama (``ollama_available`` — real transport);
2. when available, generates ONE small structured CASE_TRUTH stage response
   through the REAL ``OllamaProvider`` adapter;
3. runs that response through the strict parser (``parser.collect_issues``);
4. with ``--roundtrip``: issues ONE CASE/PEOPLE and ONE ASSET_SPEC prompt
   through the real adapter and reports a sanitized PASS/FAIL per stage;
5. with ``--geometry-repair`` (or as part of ``--roundtrip``): issues an
   ASSET_SPEC call and — when the returned AssetSpec fails the deterministic
   Phase 17 geometry-quality gate — up to the bounded number of
   ASSET_SPEC_REPAIR calls, all through the real adapter and the REAL driver
   (``OllamaAssetSpecProvider``), reporting ONLY sanitized Phase 17 metrics
   (issue counts, repair attempts, final declared dimensions, estimated
   bounding box, silhouette state, first-pass/repaired);
6. emits a sanitized JSON report to stdout (or ``--out``).

The report NEVER contains prompts, keys, the base URL, raw AssetSpec geometry
or any network detail — only availability, the public-safe model display name,
the (sanitized) parse issue count, a boolean PASS/FAIL and the sanitized
Phase 17 geometry metrics.

``--showcase-steps`` (no network) prints the manual browser E2E checklist for
the full Local-AI showcase; this is the QA/submission-side opt-in E2E the
mission documents (normal CI network-free).
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

_SHOWCASE_MANUAL_STEPS = (
    "Full Local-AI showcase browser E2E (manual / QA-side, requires a local "
    "Ollama running Llama 3.2, GENERATION_PROVIDER=ollama):\n"
    "1. Start the backend with GENERATION_PROVIDER=ollama + OLLAMA_BASE_URL + "
    "OLLAMA_MODEL=llama3.2:3b configured.\n"
    "2. Start the frontend and open /new; verify the selector offers "
    "'Local AI — llama3.2:3b — Ready' (and Demo alongside it).\n"
    "3. Select Local AI and enter a NON-golden six-line crime prompt, e.g.:\n"
    "   Victim: Dr. Anna Weiss\n   Murderer: Paul Becker\n"
    "   Motive: stolen research data\n   Weapon: bronze ceremonial ice pick\n"
    "   Time: 23:42\n   Witness: Lisa König\n   Location: office\n"
    "4. Generation starts; wait for the published investigation (generating -> "
    "investigating).\n"
    "5. Verify the requested OFFICE environment is visible.\n"
    "6. Verify at least one unknown object ('bronze ceremonial ice pick') "
    "became a proc.* asset (directly clickable).\n"
    "7. Click evidence objects directly in the 3D scene and read them.\n"
    "8. Accuse WHO=Paul Becker / WHY / WEAPON / WHEN and Reveal.\n"
    "9. Reload the page; the published world stays byte-identical.\n"
    "10. Confirm no hidden truth or host/URL leaked before reveal.\n"
)


def _geometry_repair_roundtrip(settings) -> dict:
    """One ASSET_SPEC + bounded Phase 17 geometry-repair round-trip through the
    REAL adapter and the REAL ``OllamaAssetSpecProvider`` driver.

    Reports ONLY sanitized Phase 17 metrics (issue count before repair, repair
    attempts, final part count / declared dimensions / estimated bounding box /
    silhouette state, first-pass vs repaired) — never the raw AssetSpec, the
    prompt text, the base URL or any network detail.
    """
    from app.assets.spec_provider import AssetSpecRequest
    from app.generation.ollama_provider import OllamaProvider
    from app.services.ollama_driver import OllamaAssetSpecProvider

    def _provider():
        return OllamaProvider(
            base_url=str(settings.ollama_base_url or "http://127.0.0.1:11434"),
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            temperature=settings.ollama_temperature,
            num_ctx=settings.ollama_num_ctx,
        )

    spec_provider = OllamaAssetSpecProvider(
        provider=_provider(),
        attempt_id="ollama-smoke-geometry",
        budget_consumer=lambda: True,
    )
    result = spec_provider.generate(
        AssetSpecRequest(
            requested_name="bronze ceremonial ice pick", category_hint="decor"
        )
    )
    out: dict = {
        "providerOk": result.error is None,
        "geometricallyValid": result.error is None,
    }
    if result.error is not None:
        out["errorSanitized"] = str(result.error)[:200]
    if spec_provider.last_geometry_metrics is not None:
        out["geometryMetrics"] = spec_provider.last_geometry_metrics
    return out


def _roundtrip_report(settings) -> dict:
    """One CASE/PEOPLE + one ASSET_SPEC round-trip through the REAL adapter.
    Reports sanitized PASS/FAIL per stage (only parse-issue counts and the
    presence of expected public data — never the prompt, base URL or raw text)."""
    from app.generation import prompts
    from app.generation.ollama_provider import OllamaProvider
    from app.generation.provider import GenerateRequest, GenerationStage
    from app.services.ollama_driver import parse_case_people
    from app.assets.specs import validate_asset_spec

    provider = OllamaProvider(
        base_url=str(settings.ollama_base_url or "http://127.0.0.1:11434"),
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        temperature=settings.ollama_temperature,
        num_ctx=settings.ollama_num_ctx,
    )

    def _stage_result(stage, prompt):
        req = GenerateRequest(
            attempt_id="ollama-smoke-roundtrip",
            stage=stage,
            prompt_context=prompt,
        )
        result = provider.generate(req)
        out = {"stage": stage.value, "providerOk": result.content is not None}
        if result.error is not None:
            out["errorSanitized"] = str(result.error)[:200]
        if result.timed_out:
            out["errorSanitized"] = "timed out"
        out["content"] = result.content if result.content is not None else None
        return out

    case = _stage_result(
        GenerationStage.CASE_TRUTH,
        prompts.build_case_people_prompt(
            "A small detective case: victim one, murderer one, motive one, "
            "weapon one, a single location and a canonical time.",
            {"victim": "Dr. Anna Weiss", "murderer": "Paul Becker"},
        )[:2000],
    )
    case_out = {k: v for k, v in case.items() if k != "content"}
    if case.get("content"):
        try:
            parse_case_people(case["content"])
            case_ok = True
        except (TypeError, ValueError):
            case_ok = False
        case_out["pass"] = case_ok
    else:
        case_out["pass"] = False

    spec = _stage_result(
        GenerationStage.ASSET_SPEC,
        prompts.build_asset_spec_prompt("bronze ceremonial ice pick", "decor")[:2000],
    )
    spec_out = {k: v for k, v in spec.items() if k != "content"}
    if spec.get("content"):
        issues = validate_asset_spec(spec["content"])
        spec_out["parseIssues"] = len(issues)
        spec_out["pass"] = not issues
    else:
        spec_out["pass"] = False

    return {
        "casePeople": case_out,
        "assetSpec": spec_out,
        "geometryRepair": _geometry_repair_roundtrip(settings),
    }


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
            "Requires --enable or OLLAMA_SMOKE_ENABLED=1; never runs in CI. "
            "Use --showcase-steps for the manual Local-AI showcase E2E checklist."
        ),
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        help="actually run the smoke test (otherwise: skipped (opt-in)).",
    )
    parser.add_argument(
        "--roundtrip",
        action="store_true",
        help="also run one CASE/PEOPLE + one ASSET_SPEC round-trip (sanitized PASS/FAIL).",
    )
    parser.add_argument(
        "--geometry-repair",
        action="store_true",
        help="also run one Phase 17 geometry-repair round-trip (sanitized metrics: issueCountBeforeRepair, repairAttempts, finalPartCount, finalBoundingBox, declaredDimensions, silhouettePassed, generatedOnFirstPass/repaired).",
    )
    parser.add_argument(
        "--showcase-steps",
        action="store_true",
        help="print the manual Local-AI showcase browser E2E steps and exit (no network).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="optional JSON output file path (report also printed to stdout).",
    )
    args = parser.parse_args(argv)
    if args.showcase_steps:
        print(_SHOWCASE_MANUAL_STEPS)
        return 0

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
        if args.roundtrip:
            report["roundtrip"] = _roundtrip_report(settings)
        if args.geometry_repair and "roundtrip" not in report:
            report["geometryRepair"] = _geometry_repair_roundtrip(settings)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
