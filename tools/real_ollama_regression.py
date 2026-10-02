"""Phase 24 — real-Ollama regression runner (gitlab `real-ollama-regression` job).

Operator-triggered REAL remote-Ollama acceptance against the real provider.
This tool is the job runner for the ``real-ollama`` pipeline stage:

    python -m tools.real_ollama_regression --matrix            # §35 nightly
    python -m tools.real_ollama_regression --release-gate      # §36 release gate

STRICT RULES (Phase 24 §33/§34, Level-0):

  * Runs ONLY when the operator really configures real Ollama:
        - ``GENERATION_PROVIDER`` == ``ollama`` AND
        - ``OLLAMA_BASE_URL`` and ``OLLAMA_MODEL`` are SET (GitLab CI/CD
          variables or the operator environment — NEVER committed);
  * otherwise it FAILS TRUTHFULLY and typed — it NEVER falls back to
    cassette/mock/FakeProvider (no silent mode switch, §8 last bullet);
  * reuses Phase 19J-RI2 LIVE tooling: ``tools.ollama_smoke`` — the real
    provider probe, the ACTIVITY_LOG round-trip (``--activity-log-roundtrip``)
    and the full controller/driver chain (``_full_chain_report``);
  * diagnostics are SAFE structural/timing facts: PUBLISHED/FAILED, failed
    stage, typed code, provider calls, repairs, duration — NEVER prompts,
    raw provider output, CaseTruth, credentials or the base URL;
  * FAILS TRUTHFULLY when Ollama is unreachable (never a fake pass).

Modes:

  --matrix        Lightweight §35 acceptance:
                  Easy x1, Medium x1, procedural arbitrary-object x1,
                  Activity-Log case x1 — per-item PUBLISHED/FAILED, failed
                  stage, typed code, provider calls, repairs, duration.
                  The PROCESS EXIT CODE IS THE MATRIX RESULT (DEF-006): exit 1
                  when ANY item is not PUBLISHED, so the gitlab
                  `real-ollama-regression` job FAILS TRUTHFULLY on a red
                  matrix (never a green CI job over per-item failures).
  --release-gate  Full §36 gate: max-generations (default 10, min 10) REAL
                  generations, >= 90% published, 0 incorrect / 0 partial
                  publications, Activity Log live validation, plus the
                  operator-provided witness + browser journey reports
                  (--witness-report / --browser-report). Default: the gate
                  FAILS unless BOTH journey reports are supplied, parse as
                  JSON, and indicate a passing journey (ok: true). An operator
                  who knowingly accepts missing journey evidence must pass
                  --allow-skip-journeys; the skipped journeys are still
                  reported in the JSON (DEF-007 — never fabricated).

The tool NEVER prints prompts, tokens, truth or host details. Real-Ollama
calls are NEVER part of pytest: unit tests mock the transport/provider.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# tools/ -> repo root -> backend (source-tree import, like ollama_smoke).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# --------------------------------------------------------------------------- #
# matrix item prompts (safe, intentionally boring — never truth/credentials)
# --------------------------------------------------------------------------- #

_EASY_PROMPT = (
    "Victim: Dr. Anna Weiss\n"
    "Murderer: Paul Becker\n"
    "Motive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\n"
    "Time: 23:42\n"
    "Witness: Lisa K\u00f6nig\n"
    "Location: office\n"
)

_MEDIUM_PROMPT = (
    "Victim: Marcus Holt\n"
    "Murderer: Diane Voss\n"
    "Motive: a contested inheritance\n"
    "Weapon: heavy glass paperweight\n"
    "Time: 21:05\n"
    "Witness: Ren\u00e9 Weber\n"
    "Location: hotel suite\n"
)

_PROCEDURAL_ARBITRARY_PROMPT = (
    "Victim: Kaia S\u00f8rensen\n"
    "Murderer: Tom Altmann\n"
    "Motive: a forged audit report\n"
    "Weapon: an antique brass hourglass\n"
    "Time: 00:15\n"
    "Witness: Petra Lenz\n"
    "Location: mansion study\n"
)

_ACTIVITY_LOG_PROMPT = (
    "Victim: Nora Fischer\n"
    "Murderer: Elias Kr\u00fcger\n"
    "Motive: embezzled pension fund\n"
    "Weapon: kitchen knife\n"
    "Time: 23:42\n"
    "Witness: Miriam Schulz\n"
    "Location: office\n"
)

_MATRIX_ITEMS = (
    ("easy", _EASY_PROMPT),
    ("medium", _MEDIUM_PROMPT),
    ("procedural_arbitrary_object", _PROCEDURAL_ARBITRARY_PROMPT),
    ("activity_log", _ACTIVITY_LOG_PROMPT),
)


class RealOllamaConfigError(RuntimeError):
    """Typed config guard: real-Ollama env missing (never a silent fallback)."""


def _require_real_ollama_env() -> None:
    """FAIL TRUTHFULLY and typed unless real Ollama is actually configured.

    This is the Level-0 no-fake-fallback guard. ``GENERATION_PROVIDER`` must
    be exactly ``ollama`` and BOTH operator secrets must be present in the
    environment (GitLab CI/CD variables / operator env — never committed).
    """
    provider = os.environ.get("GENERATION_PROVIDER", "").strip().lower()
    base_url = os.environ.get("OLLAMA_BASE_URL", "").strip()
    model = os.environ.get("OLLAMA_MODEL", "").strip()
    if provider != "ollama":
        raise RealOllamaConfigError(
            "real-ollama regression requires GENERATION_PROVIDER=ollama "
            f"(got {provider!r}); refusing to run (no fake/cassette/mock "
            "fallback is ever allowed — Phase 24 §8/§34)"
        )
    if not base_url:
        raise RealOllamaConfigError(
            "real-ollama regression requires OLLAMA_BASE_URL in the "
            "environment (GitLab CI/CD variable / operator environment); "
            "refusing to run without a real Ollama endpoint"
        )
    if not model:
        raise RealOllamaConfigError(
            "real-ollama regression requires OLLAMA_MODEL in the environment "
            "(GitLab CI/CD variable / operator environment); refusing to run "
            "without a real model"
        )


# --------------------------------------------------------------------------- #
# provider construction (injectable seam for hermetic tests)
# --------------------------------------------------------------------------- #


def _build_settings() -> object:
    from app.core.config import Settings

    return Settings(generation_provider="ollama")


def _probe_and_provider_factory(settings):
    """Return ``(provider_factory, probe_report)`` — REAL Ollama only.

    ``ollama_available`` is a real transport probe; when the probe fails the
    tool must FAIL TRUTHFULLY (never fake). The factory builds the real
    ``OllamaProvider`` through the SAME probe result the driver uses.
    """
    from app.generation.ollama_provider import (
        DEFAULT_OLLAMA_BASE_URL,
        OllamaProvider,
        ollama_available,
        ollama_structured_output_supported,
    )

    probe_available, _detail = ollama_available(settings)
    structured_supported = False
    if probe_available:
        try:
            structured_supported = ollama_structured_output_supported(settings)
        except Exception:  # noqa: BLE001 - capability probe never blocks
            structured_supported = False

    base_url = str(
        getattr(settings, "ollama_base_url", None) or DEFAULT_OLLAMA_BASE_URL
    )

    def _factory():
        return OllamaProvider(
            base_url=base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            temperature=settings.ollama_temperature,
            num_ctx=settings.ollama_num_ctx,
            structured_output=structured_supported,
        )

    probe_report = {
        "probeAvailable": bool(probe_available),
        "structuredOutputProbe": {
            "supported": bool(structured_supported),
            "probeUrl": None,  # NEVER the base URL — only the boolean
            "model": str(getattr(settings, "ollama_model", "") or ""),
        },
    }
    return _factory, probe_report, probe_available


# --------------------------------------------------------------------------- #
# diagnostics (sanitized)
# --------------------------------------------------------------------------- #


def _failed_stage_label(failure_code: object, validation_outcome: object = None) -> str:
    """Sanitized failed-stage label derived from the typed failure code.

    The controller record's stage is internal; the PUBLIC typed failure code
    (§17E vocabulary) maps deterministically to the generation stage that
    failed so the matrix can report ``failed stage`` without any internal
    detail. Unknown codes degrade to ``validation``/``provider``/``unknown`` —
    never a traceback, never internal fields.
    """
    code = str(failure_code) if failure_code else ""
    mapping = {
        "GENERATION_DEADLINE_EXCEEDED": "generation",
        "PROVIDER_TIMEOUT": "provider",
        "PROVIDER_UNAVAILABLE": "provider",
        "PROVIDER_INVALID_RESPONSE": "provider",
        "PROVIDER_CALL_BUDGET_EXHAUSTED": "provider",
        "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED": "provider",
        "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED": "asset_spec",
        "MAX_PROCEDURAL_ASSETS_EXCEEDED": "asset_spec",
        "MAX_FAILED_ASSETS_EXCEEDED": "asset_spec",
        "REPAIR_BUDGET_EXHAUSTED": "repair",
        "REGENERATION_BUDGET_EXHAUSTED": "regeneration",
        "STRUCTURED_OUTPUT_INVALID": "case_truth",
        "ASSET_SPEC_INVALID": "asset_spec",
        "GEOMETRY_VALIDATION_FAILED": "asset_spec",
        "SOLVER_AMBIGUOUS": "validation",
        "VALIDATION_FAILED": "validation",
        "PUBLICATION_FAILED": "publication",
        "ACTIVITY_LOG_SCHEMA_INVALID": "activity_log",
        "ACTIVITY_LOG_ENTRY_COUNT_INVALID": "activity_log",
        "ACTIVITY_LOG_TIME_ORDER_INVALID": "activity_log",
        "ACTIVITY_LOG_CANONICAL_TIME_MISSING": "activity_log",
        "ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED": "activity_log",
        "ACTIVITY_LOG_TIME_WINDOW_INVALID": "activity_log",
        "ACTIVITY_LOG_DIRECT_TRUTH_LEAK": "activity_log",
        "ACTIVITY_LOG_ENTITY_LEAK": "activity_log",
        "ACTIVITY_LOG_PROVIDER_FAILED": "activity_log",
        "LOCAL_OLLAMA_UNAVAILABLE": "provider",
        "LOCAL_MODEL_UNAVAILABLE": "provider",
        "LOCAL_PROVIDER_TIMEOUT": "provider",
        "LOCAL_PROVIDER_INVALID_OUTPUT": "provider",
    }
    if code in mapping:
        return mapping[code]
    if validation_outcome not in (None, "", "valid"):
        return "validation"
    return "unknown"


def _matrix_item(
    settings, provider_factory, name: str, prompt: str
) -> dict[str, object]:
    """One real generation through the full driver/controller chain."""
    from tools import ollama_smoke

    provider = provider_factory()
    started = time.perf_counter()
    report = ollama_smoke._full_chain_report(settings, provider, prompt=prompt)
    duration = round(time.perf_counter() - started, 4)
    raw_state = str(report.get("state") or "")
    outcome = str(report.get("validationOutcome") or "").lower()
    # ``_full_chain_report`` runs the controller with hold_before_publish=True:
    # a VALIDING+VALID draft is the publication-gate terminal (the CAS gate
    # would transition it PUBLISHED without holding). Report the gate verdict
    # as PUBLISHED/FAILED per §35 while keeping the raw controller state.
    if raw_state == "PUBLISHED" or (raw_state == "VALIDATING" and outcome == "valid"):
        gate_state = "PUBLISHED"
    elif raw_state in ("FAILED", "ERROR"):
        gate_state = "FAILED"
    elif raw_state == "VALIDATING" and outcome:
        gate_state = "FAILED"  # terminal validation failure (typed code present)
    else:
        gate_state = raw_state or "unknown"
    return {
        "item": name,
        "state": gate_state,
        "rawState": raw_state,
        "failureCode": report.get("failureCode"),
        "failedStage": _failed_stage_label(
            report.get("failureCode"), report.get("validationOutcome")
        ),
        "validationOutcome": report.get("validationOutcome"),
        "providerCalls": report.get("providerCalls"),
        "elapsedSeconds": duration,
        "reportElapsedSeconds": report.get("elapsedSeconds"),
        "repairCount": len(report.get("repairDiagnostics") or ()),
        "procPlacementCount": len(report.get("procPlacements") or ()),
    }


def _activity_log_live_validation(settings, provider_factory) -> dict[str, object]:
    """Phase 19J-RI2 LIVE activity-log round-trip (real provider calls)."""
    from tools import ollama_smoke

    provider = provider_factory()
    started = time.perf_counter()
    roundtrip = ollama_smoke._activity_log_repair_roundtrip(settings, provider)
    duration = round(time.perf_counter() - started, 4)
    passes = roundtrip.get("passes") or []
    final_ok = bool(passes) and all(
        bool(p.get("pass")) for p in passes
    )
    return {
        "pass": final_ok,
        "maxRepairPasses": roundtrip.get("maxRepairPasses"),
        "passCount": len(passes),
        "elapsedSeconds": duration,
        "entryCounts": [int(p.get("entryCount") or 0) for p in passes],
        "validatorCodes": [
            list(p.get("validatorCodes") or ()) for p in passes
        ],
    }


# --------------------------------------------------------------------------- #
# modes
# --------------------------------------------------------------------------- #


def run_matrix(
    settings, provider_factory, probe_report: dict[str, object]
) -> dict[str, object]:
    items: list[dict[str, object]] = []
    for name, prompt in _MATRIX_ITEMS:
        items.append(
            _matrix_item(settings, provider_factory, name, prompt)
        )
    activity = _activity_log_live_validation(settings, provider_factory)
    return {
        "mode": "matrix",
        "probe": probe_report,
        "items": items,
        "activityLogLive": activity,
        "summary": {
            "items": len(items),
            "published": sum(1 for i in items if i["state"] == "PUBLISHED"),
            "failed": sum(1 for i in items if i["state"] == "FAILED"),
        },
    }


def run_release_gate(
    settings,
    provider_factory,
    probe_report: dict[str, object],
    *,
    max_generations: int,
    witness_report: str | None = None,
    browser_report: str | None = None,
    allow_skip_journeys: bool = False,
) -> dict[str, object]:
    """Full §36 release gate: >=10 real generations, >=90% published, 0
    incorrect/0 partial, Activity Log live validation, AND the operator
    witness/browser journey evidence.

    §36 requires the witness live journey and the full browser journey as
    part of the release gate (DEF-007). By DEFAULT (allow_skip_journeys=False)
    ``gatePassed`` is False unless BOTH ``--witness-report`` and
    ``--browser-report`` are supplied, parse as JSON objects, and indicate a
    passing journey (``ok: true``). A missing/failed-to-load/failed journey
    can only be accepted through the EXPLICIT operator flag
    ``--allow-skip-journeys``; the skipped journeys are still reported under
    ``operatorJourneys`` and never fabricated.
    """
    prompts: list[str] = []
    count = max(max_generations, 10)
    for index in range(count):
        prompts.append(_MATRIX_ITEMS[index % len(_MATRIX_ITEMS)][1])

    items: list[dict[str, object]] = []
    for index, prompt in enumerate(prompts, start=1):
        label = f"r{index}:{_MATRIX_ITEMS[(index - 1) % len(_MATRIX_ITEMS)][0]}"
        items.append(_matrix_item(settings, provider_factory, label, prompt))

    activity = _activity_log_live_validation(settings, provider_factory)

    published = [i for i in items if i["state"] == "PUBLISHED"]
    incorrect = [i for i in items if i.get("validationOutcome") not in (None, "valid")]
    partial = [
        i for i in items if i["state"] == "PUBLISHED" and i.get("validationOutcome") != "valid"
    ]

    witness = _load_operator_report(witness_report, "witness") if witness_report else None
    browser = _load_operator_report(browser_report, "browser") if browser_report else None
    witness_passed = _journey_passed(witness)
    browser_passed = _journey_passed(browser)
    journeys_ok = allow_skip_journeys or (witness_passed and browser_passed)

    numeric_ok = (
        len(items) >= 10
        and len(published) * 10 >= max(len(items), 10) * 9
        and not incorrect
        and not partial
        and activity.get("pass") is True
    )
    gate_ok = numeric_ok and journeys_ok
    return {
        "mode": "release-gate",
        "generations": len(items),
        "threshold": {
            "minGenerations": 10,
            "minPublished": ">= 90%",
            "requiredZeroCorrectFailures": True,
            "requiredZeroPartialPublications": True,
            "requiredOperatorJourneys": not allow_skip_journeys,
        },
        "probe": probe_report,
        "items": items,
        "activityLogLive": activity,
        "operatorJourneys": {
            "witness": witness or {"source": None, "loaded": False, "ok": False, "skipped": True},
            "browser": browser or {"source": None, "loaded": False, "ok": False, "skipped": True},
        },
        "allowSkipJourneys": bool(allow_skip_journeys),
        "gatePassed": bool(gate_ok),
        "summary": {
            "generations": len(items),
            "published": len(published),
            "incorrect": len(incorrect),
            "partial": len(partial),
            "activityLogPass": activity.get("pass"),
            "journeyEvidence": "skipped (operator opted in)" if allow_skip_journeys
            else ("present" if (witness_passed and browser_passed) else "MISSING/FAILED"),
        },
    }


def _journey_passed(report: dict[str, object] | None) -> bool:
    """True ONLY for a supplied journey report that loaded and PASSES.

    The operator journey evidence (§36) counts only when the report file was
    supplied, parsed as a JSON object AND explicitly reports a passing journey
    (``ok: true`` — the same contract ``_load_operator_report`` surfaces).
    Missing reports, unreadable/parsing-failing files and journeys that loaded
    but report failure all count as NOT passed. Operator evidence is never
    fabricated or inferred.
    """
    if report is None:
        return False
    if report.get("loaded") is not True:
        return False
    return bool(report.get("ok") is True)


def _load_operator_report(path: str | None, kind: str) -> dict[str, object] | None:
    if not path:
        return None
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        return {
            "source": path,
            "loaded": False,
            "error": f"{kind} report could not be read: {type(exc).__name__}",
        }
    if not isinstance(data, dict):
        return {"source": path, "loaded": False, "error": "not a JSON object"}
    return {
        "source": path,
        "loaded": True,
        "ok": bool(data.get("ok", False)),
        "checks": data.get("checks") or data.get("summary"),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.real_ollama_regression",
        description=(
            "Phase 24 real-Ollama regression runner (operator-triggered; "
            "gitlab real-ollama job). Requires real Ollama env — fails "
            "truthfully otherwise (never a fake fallback)."
        ),
    )
    parser.add_argument(
        "--matrix", action="store_true",
        help="Run the lightweight §35 matrix (Easy/Medium/procedural/activity-log). "
             "Exit code IS the matrix result: 1 when any item is not PUBLISHED.",
    )
    parser.add_argument(
        "--release-gate", action="store_true",
        help="Run the full §36 release gate (>=10 generations, >=90% published, "
             "witness + browser journey evidence required unless "
             "--allow-skip-journeys).",
    )
    parser.add_argument(
        "--max-generations", type=int, default=10,
        help="Release-gate generation count (min 10 enforced).",
    )
    parser.add_argument(
        "--witness-report", default=None,
        help="Operator-produced witness journey JSON report (release evidence; "
             "must parse and report ok:true for the gate to pass by default).",
    )
    parser.add_argument(
        "--browser-report", default=None,
        help="Operator-produced full browser journey JSON report (release "
             "evidence; must parse and report ok:true for the gate to pass by "
             "default).",
    )
    parser.add_argument(
        "--allow-skip-journeys", action="store_true",
        help="Operator opt-in (DEF-007): permit the §36 witness/browser journey "
             "evidence to be SKIPPED in a release gate. The skipped journeys "
             "are still reported under operatorJourneys in the JSON. WITHOUT "
             "this flag the gate FAILS unless BOTH --witness-report and "
             "--browser-report are supplied, parse, and indicate a passing "
             "journey.",
    )
    parser.add_argument(
        "--out", default=None,
        help="Optional sanitized JSON report output path.",
    )
    args = parser.parse_args(argv)

    if not args.matrix and not args.release_gate:
        parser.error("pass --matrix or --release-gate")

    try:
        _require_real_ollama_env()
    except RealOllamaConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    settings = _build_settings()
    try:
        provider_factory, probe_report, probe_available = _probe_and_provider_factory(
            settings
        )
    except Exception as exc:  # noqa: BLE001 - typed sanitized failure
        print(
            f"real-ollama regression: provider construction failed: "
            f"{type(exc).__name__}: {str(exc)[:200]}",
            file=sys.stderr,
        )
        return 2

    if not probe_available:
        print(
            "real-ollama regression: Ollama is NOT reachable/configured — "
            "FAILING TRUTHFULLY (never a fake fallback; Phase 24 §8/§34)",
            file=sys.stderr,
        )
        return 2

    try:
        if args.release_gate:
            report = run_release_gate(
                settings,
                provider_factory,
                probe_report,
                max_generations=args.max_generations,
                witness_report=args.witness_report,
                browser_report=args.browser_report,
                allow_skip_journeys=args.allow_skip_journeys,
            )
        else:
            report = run_matrix(settings, provider_factory, probe_report)
    except Exception as exc:  # noqa: BLE001 - sanitized, never a traceback
        print(
            f"real-ollama regression: run failed: {type(exc).__name__}: "
            f"{str(exc)[:200]}",
            file=sys.stderr,
        )
        return 2

    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    if args.release_gate:
        if not report.get("gatePassed"):
            print("release gate FAILED (see summary)", file=sys.stderr)
            return 1
        return 0
    # Matrix mode (DEF-006): the process exit code IS the matrix result — the
    # gitlab `real-ollama-regression` job must fail truthfully on a red matrix.
    # The report still carries every per-item status + typed failureCode.
    items = report.get("items") or []
    failed_items = [i for i in items if i.get("state") != "PUBLISHED"]
    if failed_items:
        print(
            f"matrix FAILED: {len(failed_items)}/{len(items)} items not "
            "PUBLISHED (per-item status + typed failureCode in the report)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())