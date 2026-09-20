"""Opt-in real-Ollama smoke CLI (Phase16 L / Phase16_2 §24 / Phase17 §13-§19 /
Phase17B).

Usage (from the repository root) — REAL hermes3:8b operator run:

PowerShell:

    $env:OLLAMA_MODEL="hermes3:8b"; $env:OLLAMA_TEMPERATURE="0"; `
    $env:OLLAMA_NUM_CTX="4096"; $env:OLLAMA_TIMEOUT_SECONDS="180"
    python -m tools.ollama_smoke --enable --debug --stage case_truth

POSIX/cmd:

    SET OLLAMA_MODEL=hermes3:8b
    SET OLLAMA_TEMPERATURE=0
    SET OLLAMA_NUM_CTX=4096
    SET OLLAMA_TIMEOUT_SECONDS=180
    python -m tools.ollama_smoke --enable --debug --stage case_truth

    python -m tools.ollama_smoke --enable [--out report.json]
    python -m tools.ollama_smoke --enable --roundtrip        # + CASE/PEOPLE & ASSET_SPEC round-trip
    python -m tools.ollama_smoke --enable --geometry-repair  # + Phase 17 geometry repair round-trip
    python -m tools.ollama_smoke --enable --debug --stage asset_spec
    python -m tools.ollama_smoke --enable --full-chain       # REAL full Prompt-to-World chain
    python -m tools.ollama_smoke --showcase-steps            # print the manual Local-AI showcase E2E steps

It is STRICTLY opt-in: without ``--enable`` (or the env flag
``OLLAMA_SMOKE_ENABLED=1``) it prints ``skipped (opt-in)`` and exits 0. Normal
CI never runs it (opt-in AND the backend test suite's autouse network block
makes real calls impossible anyway).

When enabled it:

1. probes the configured Ollama (``ollama_available`` — real transport) and
   resolves the transport structured-output capability ONCE
   (``ollama_structured_output_supported`` — documented /api/version probe);
2. when available, runs ONE stage through the REAL ``OllamaProvider`` adapter
   (default ``case_truth``; ``--stage`` selects the stage) and reports the
   strict-parse outcome;
3. with ``--roundtrip``: issues ONE CASE/PEOPLE and ONE ASSET_SPEC prompt
   through the real adapter and reports a sanitized PASS/FAIL per stage;
4. with ``--geometry-repair`` (or as part of ``--roundtrip``): issues an
   ASSET_SPEC call and — when the returned AssetSpec fails the deterministic
   Phase 17 geometry-quality gate — up to the bounded number of
   ASSET_SPEC_REPAIR calls, all through the real adapter and the REAL driver
   (``OllamaAssetSpecProvider``), reporting ONLY sanitized Phase 17 metrics;
5. emits a sanitized deterministic JSON report to stdout (or ``--out``).

Per-stage diagnostics (Phase17B §1 — sanitized):

- ``stageTemplate`` — the exact prompt-template version used (e.g.
  ``case_people_v1``);
- exact parse issue strings (the strict parser's deterministic messages) +
  ``parsedOk``;
- ``transportStructuredOutput`` — what was ACTUALLY sent in ``/api/chat``
  ``format``: ``true`` = the authoritative per-stage JSON Schema (derived from
  the SAME ``schema_contract`` mapping the prompt embeds), ``false`` = the
  documented ``"json"`` fallback;
- a sanitized, ellipsized sample of the raw model response (first/last ~200
  chars; the FULL sanitized text under ``--debug`` — credentials, URLs, hosts,
  ports and truth seeds are ALWAYS stripped);
- elapsed generation time + response byte count;
- for the AssetSpec stage: first-pass Phase 13 structural issues AND first-pass
  Phase 17 geometry issues (code/classification/message/partId), then EACH
  repair attempt's issues (order preserved, sanitized), the repair count and
  the final compiled ``proc.*`` id or a failure.

The report NEVER contains prompts, keys, the base URL, or any network detail —
only availability, the public-safe model display name, the sanitized issue
texts and the sanitized Phase 17 metrics.

``--showcase-steps`` (no network) prints the manual browser E2E checklist for
the full Local-AI showcase; this is the QA/submission-side opt-in E2E the
mission documents (normal CI network-free).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# tools/ -> repo root -> backend (so the editable install / source tree is
# importable regardless of the invocation CWD).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

_SHOWCASE_MANUAL_STEPS = (
    "Full Local-AI showcase browser E2E (manual / QA-side, requires a local "
    "Ollama running a local model, GENERATION_PROVIDER=ollama):\n"
    "1. Start the backend with GENERATION_PROVIDER=ollama + OLLAMA_BASE_URL + "
    "OLLAMA_MODEL (e.g. llama3.2:3b or hermes3:8b) configured.\n"
    "2. Start the frontend and open /new; verify the selector offers "
    "'Local AI — <model> — Ready' (and Demo alongside it).\n"
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

# The documented single-stage smoke aliases (Phase17B §3 mapping table).
_SMOKE_STAGES = (
    "case_truth",
    "evidence",
    "world_requirements",
    "asset_spec",
    "asset_spec_repair",
    "repair",
)

# Truth-seed/secret tokens stripped from RAW model-response samples ALWAYS
# (even under --debug). App-owned validator issue strings are NOT redacted —
# they are deterministic, safe diagnostics the smoke is required to show.
_TRUTH_SEED_TOKENS = (
    "murdererId",
    "crimeTime",
    "solverProof",
    "caseTruth",
    "timeline",
    "relationships",
)

_HERMES_OPERATOR_COMMAND = (
    "Real hermes3:8b re-run (operator action; Phase17B section 5):\n"
    "PowerShell:\n"
    '    $env:OLLAMA_MODEL="hermes3:8b"; $env:OLLAMA_TEMPERATURE="0"; `\n'
    '    $env:OLLAMA_NUM_CTX="4096"; $env:OLLAMA_TIMEOUT_SECONDS="180"\n'
    "    python -m tools.ollama_smoke --enable --debug --stage case_truth\n"
    "POSIX/cmd:\n"
    "    SET OLLAMA_MODEL=hermes3:8b / export OLLAMA_MODEL=hermes3:8b\n"
    "    SET OLLAMA_TEMPERATURE=0 / export OLLAMA_TEMPERATURE=0\n"
    "    SET OLLAMA_NUM_CTX=4096 / export OLLAMA_NUM_CTX=4096\n"
    "    SET OLLAMA_TIMEOUT_SECONDS=180 / export OLLAMA_TIMEOUT_SECONDS=180\n"
    "    python -m tools.ollama_smoke --enable --debug --stage case_truth\n"
)


# --------------------------------------------------------------------------- #
# sanitization / diagnostics helpers (never secrets, prompts or full raw text
# unless --debug — and --debug still strips credentials/URLs/truth seeds)
# --------------------------------------------------------------------------- #


def _sanitize_text(text: str) -> str:
    """Strip URLs, host tokens, ports and truth seeds from a RAW model sample.

    Deterministic and ALWAYS applied (with and without ``--debug``). The model
    response sample is untrusted provider text; only sanitized remnants may be
    printed.
    """
    out = str(text)
    out = re.sub(r"https?://[^\s)\]\"'}]+", "<url>", out)
    out = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<host>", out)
    out = re.sub(r"\blocalhost\b", "<host>", out, flags=re.IGNORECASE)
    out = re.sub(r"\bhost\.docker\.internal\b", "<host>", out, flags=re.IGNORECASE)
    out = re.sub(r"\b11434\b", "<port>", out)
    for token in _TRUTH_SEED_TOKENS:
        out = out.replace(token, "<redacted>")
        out = out.replace(token[0].upper() + token[1:], "<redacted>")
    return out


def _sample_sanitized(text: str, limit: int = 200) -> dict[str, object]:
    """Ellipsized sanitized raw-response sample (first/last ~``limit`` chars)."""
    clean = _sanitize_text(text)
    entry: dict[str, object] = {
        "byteLength": len(text.encode("utf-8", errors="replace")),
        "sanitizedLength": len(clean),
    }
    if len(clean) <= limit * 2:
        entry["text"] = clean
    else:
        entry["first"] = clean[:limit]
        entry["last"] = clean[-limit:]
        entry["ellipsized"] = True
    return entry


def _parse_issues_for(stage: str, content: str) -> tuple[bool, list[str]]:
    """Exact strict-parse issue strings for one smoke stage (never raises).

    Returns ``(parsedOk, issues)`` where issues is an empty list on success.
    """
    from app.generation import parser as stage_parser
    from app.generation.provider import GenerationStage
    from app.services.ollama_driver import parse_world_requirements

    if stage == "world_requirements":
        try:
            parse_world_requirements(content)
        except (TypeError, ValueError) as exc:
            return False, [str(exc)]
        return True, []
    if stage in ("asset_spec", "asset_spec_repair"):
        from app.assets.specs import validate_asset_spec

        issues = list(validate_asset_spec(content))
        return (not issues), issues
    if stage == "repair":
        issues = list(stage_parser.collect_full_draft_issues(content))
        return (not issues), issues
    if stage == "case_truth":
        issues = _case_truth_issues(content)
        return (not issues), issues
    # evidence
    issues = list(stage_parser.collect_issues(GenerationStage.EVIDENCE, content))
    return (not issues), issues


# The public sections of a case_people document (mirror of the driver's
# ``parse_case_people`` public payload).
_CASE_PEOPLE_PUBLIC_KEYS = (
    "persons",
    "motives",
    "objects",
    "locations",
    "travelRules",
    "scene",
)


def _case_truth_issues(content: str) -> list[str]:
    """Exact strict-parse issue strings of a FULL case_people document.

    The CASE_PEOPLE prompt asks for the complete document (crime + persons +
    motives + locations + travelRules + scene), and the driver splits it the
    same authoritative way (``ollama_driver.parse_case_people``): the
    ``crime`` section through the CASE_TRUTH stage parser and the remaining
    public sections through the PUBLIC_WORLD stage parser. Checking the FULL
    document against the CRIME-only parser would wrongly report the public
    sections as unknown keys (smoke-surface fix).
    """
    from app.generation import parser as stage_parser
    from app.generation.provider import GenerationStage
    from app.services.ollama_driver import _parse_doc

    try:
        data = _parse_doc(content)
    except (TypeError, ValueError) as exc:
        return [f"case_truth: {exc}"]
    if not isinstance(data, dict):
        return ["case_truth: root must be a JSON object"]
    issues: list[str] = []
    crime = data.get("crime")
    if not isinstance(crime, dict):
        issues.append("case_truth: missing required key 'crime'")
    else:
        issues += list(
            stage_parser.collect_issues(
                GenerationStage.CASE_TRUTH,
                json.dumps({"crime": crime}, sort_keys=True),
            )
        )
    public_payload = {
        key: value for key, value in data.items() if key in _CASE_PEOPLE_PUBLIC_KEYS
    }
    # The driver tolerates an absent case_people ``objects`` section by
    # defaulting it to [] (the prompt's ring-fence even omits the key); the
    # smoke mirrors that authoritative default exactly.
    if "objects" not in public_payload:
        public_payload["objects"] = []
    issues += list(
        stage_parser.collect_issues(
            GenerationStage.PUBLIC_WORLD,
            json.dumps(public_payload, sort_keys=True),
        )
    )
    return issues


def _geometry_issue_dict(issue: Any) -> dict[str, object]:
    """One sanitized geometry issue (app-owned fields only)."""
    return {
        "code": issue.code,
        "classification": issue.classification,
        "message": issue.message,
        "partId": issue.partId,
    }


def _geometry_details(provider: Any) -> dict[str, object]:
    """Sanitized per-pass Phase 17 diagnostics from the driver trace."""
    trace = getattr(provider, "last_repair_trace", None) or []
    attempts = [
        {
            "pass": index,
            "structuralIssues": list(entry.get("structuralIssues") or ()),
            "geometryIssues": list(entry.get("geometryIssues") or ()),
        }
        for index, entry in enumerate(trace)
    ]
    return {"repairTrace": attempts, "traceLength": len(attempts)}


# --------------------------------------------------------------------------- #
# Phase17 Wave-2 — REAL full Prompt-to-World chain (operator re-run command)
# --------------------------------------------------------------------------- #

_FULL_CHAIN_PROMPT = (
    "Victim: Dr. Anna Weiss\n"
    "Murderer: Paul Becker\n"
    "Motive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\n"
    "Time: 23:42\n"
    "Witness: Lisa K\u00f6nig\n"
    "Location: office\n"
)


def _solver_dim_summary(proof: Any) -> dict[str, object]:
    """Sanitized per-dimension solver summary (winner ids are PUBLIC candidate
    ids — never truth/hidden internals)."""
    if proof is None:
        return {}
    out: dict[str, object] = {}
    for name, dim in (("who", proof.who), ("why", proof.why), ("weapon", proof.weapon)):
        if dim is None:
            out[name] = None
            continue
        out[name] = {
            "unique": bool(dim.unique),
            "winner": dim.winner,
            "excludedCount": len(dim.excluded or ()),
            "unknownCount": len(dim.unknown_remaining or ()),
        }
    if proof.when is not None:
        out["when"] = {
            "ambiguous": bool(proof.when.ambiguous),
            "overconstrained": bool(proof.when.overconstrained),
        }
    return out


def _full_chain_report(settings, provider) -> dict[str, object]:
    """One REAL full Prompt-to-World run through the stage driver + controller.

    Walks the actual controller pipeline (the service path minus persistence):
    CASE/PEOPLE -> EVIDENCE (with the app-owned deterministic evidence
    projection) -> WORLD_REQUIREMENTS -> ASSET_SPEC (proc.*) -> deterministic
    solver -> publication gate. Reports ONLY sanitized facts: final state,
    validation outcome, provider-call count, elapsed, the solver winners
    (public candidate ids), the proc.* placement (evidenceId + interaction)
    and the evidence-completion audit counts. NEVER the base URL, prompts,
    raw output, credentials, hidden truth or host details.
    """
    from app.generation.admission import AdmissionController
    from app.generation.clock import RealClock
    from app.generation.controller import GenerationController
    from app.generation.ids import IdSource
    from app.services.ollama_driver import OllamaStageDriver

    clock = RealClock()
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
    )
    session = admission.create_anonymous_quota_session()
    driver = OllamaStageDriver(settings=settings, provider_factory=lambda: provider)
    controller = GenerationController(
        provider=provider,
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=settings.generation_deadline_seconds,
        max_llm_calls_per_generation=settings.max_llm_calls_per_generation,
        max_repair_passes=settings.max_repair_passes,
        max_full_regenerations=settings.max_full_regenerations,
        max_prompt_chars=settings.max_prompt_chars,
        hold_before_publish=True,
        stage_driver=driver,
        provider_timeout_seconds=settings.ollama_timeout_seconds,
        provider_name="ollama",
        provider_model=settings.ollama_model,
    )
    started = time.perf_counter()
    handle = controller.start_generation(
        _FULL_CHAIN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    elapsed = round(time.perf_counter() - started, 4)
    out: dict[str, object] = {
        "state": record.state.value,
        "elapsedSeconds": elapsed,
        "providerCalls": record.budget.calls if record.budget else 0,
        "evidenceCompletionNotes": len(getattr(driver, "last_evidence_injections", ()) or ()),
    }
    if record.last_validation is not None:
        out["validationOutcome"] = record.last_validation.outcome.value
        out["repairDiagnostics"] = list(record.last_validation.repair_diagnostics)
    out["solverWinners"] = _solver_dim_summary(record.solver_proof)
    if record.draft is not None and record.last_validation is not None and record.last_validation.valid:
        out["draftSha256"] = _draft_sha(record.draft)
        out["crime"] = {
            "victimId": record.draft.crime.victim_id,
            "murdererId": record.draft.crime.murderer_id,
            "motiveId": record.draft.crime.motive_id,
            "weaponId": record.draft.crime.weapon_id,
            "locationId": record.draft.crime.location_id,
        }
        out["sceneEnvironment"] = getattr(record.draft.scene, "environment_id", None)
        out["procPlacements"] = [
            {
                "objectId": p.object_id,
                "assetId": p.asset_id,
                "interaction": p.interaction,
                "evidenceId": p.evidence_id,
            }
            for p in record.draft.world_graph.placements
            if str(p.asset_id).startswith("proc.")
        ]
    return out


def _draft_sha(draft: Any) -> str:
    """Deterministic identity hash of the generated world (byte-determinism
    check for repeated operator runs; draft material only, never metadata)."""
    import hashlib

    try:
        payload = json.dumps(
            draft.to_dict() if hasattr(draft, "to_dict") else repr(draft),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# single-stage smoke (real provider call + strict parse diagnostics)
# --------------------------------------------------------------------------- #


def _stage_builders() -> dict[str, tuple[str, str, str]]:
    """stage alias -> (prompt builder module attr, placeholder args, version).

    The version strings are the CURRENT template versions; the smoke resolves
    them through the same prompt builders the driver uses (Phase17B §3).
    """
    return {
        "case_truth": (
            "build_case_people_prompt",
            "A small detective case: one victim, one murderer, one motive, one "
            "weapon, one location and a canonical time. Names: Dr. Anna Weiss "
            "(victim), Paul Becker (murderer).",
            "case_people_v1",
        ),
        "evidence": (
            "build_evidence_prompt",
            "Structured evidence for the case: witness observations, a CCTV "
            "record, an alibi claim and a forensic weapon match.",
            "evidence_v1",
        ),
        "world_requirements": (
            "build_world_requirements_prompt",
            "An office environment with a bronze ceremonial ice pick on the "
            "desk of the victim.",
            "world_requirements_v1",
        ),
        "asset_spec": (
            "build_asset_spec_prompt",
            "bronze ceremonial ice pick",
            "asset_spec_v1",
        ),
        "asset_spec_repair": (
            "build_asset_spec_repair_prompt",
            "bronze ceremonial ice pick",
            "asset_spec_repair_v1",
        ),
        "repair": (
            "build_repair_prompt",
            "",
            "repair_v1",
        ),
    }


def _build_stage_prompt(
    stage: str, prompts: Any, builders: dict[str, tuple[str, str, str]]
) -> str:
    """Build one stage prompt through the SAME versioned builders the driver
    uses (never a hand-maintained clone)."""
    name, arg, _version = builders[stage]
    if stage == "case_truth":
        return prompts.build_case_people_prompt(arg, None)
    if stage == "evidence":
        return prompts.build_evidence_prompt(arg, None)
    if stage == "world_requirements":
        return prompts.build_world_requirements_prompt(arg, None)
    if stage == "asset_spec":
        return prompts.build_asset_spec_prompt(arg, "decor")
    if stage == "asset_spec_repair":
        # Deterministic sanitized repair request: the observed-broken candidate
        # plus the app-owned issue text it produces.
        candidate = json.dumps(
            {
                "canonicalName": "Bronze Ceremonial Ice Pick",
                "category": "decor",
                "subtype": "ceremonial_ice_pick",
                "dimensions": {"x": 0.3, "y": 0.3, "z": 0.3},
                "parts": [
                    {
                        "id": "part_00",
                        "role": "tip",
                        "primitive": "sphere",
                        "transform": {
                            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                            "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                            "scale": {"x": 0.2, "y": 0.2, "z": 0.2},
                        },
                        "material": "metal.brass",
                    }
                ],
            },
            sort_keys=True,
        )
        return prompts.build_asset_spec_repair_prompt(
            arg,
            candidate,
            (
                "[QUALITY_ERROR SILHOUETTE_HEURISTIC]: this hand-held object "
                "has no recognizable silhouette: at least TWO distinct parts "
                "whose positions differ by more than 0.05m are required",
            ),
        )
    # repair
    draft = json.dumps(
        {
            "crime": {
                "type": "murder",
                "victimId": "anna_weiss",
                "murdererId": "paul_becker",
                "motiveId": "stolen_research_data",
                "weaponId": "bronze_ceremonial_ice_pick",
                "locationId": "office",
                "crimeTime": {
                    "canonical": "2026-09-11T23:42:00+02:00",
                    "accusationToleranceSeconds": 300,
                },
            },
            "scene": {"locationId": "office", "name": "Office"},
            "worldGraph": {"locations": [], "placements": []},
        },
        sort_keys=True,
    )
    return prompts.build_repair_prompt(draft, ("sanitized validation issue",))


def _single_stage_report(settings, provider, stage: str) -> dict[str, object]:
    """ONE real provider call for ``stage`` + strict parse diagnostics."""
    from app.generation import prompts
    from app.generation.provider import GenerateRequest, GenerationStage

    builders = _stage_builders()
    _, _, version = builders[stage]
    prompt = _build_stage_prompt(stage, prompts, builders)
    generation_stage = (
        GenerationStage.WORLD_GRAPH
        if stage == "world_requirements"
        else GenerationStage(stage)
    )
    request = GenerateRequest(
        attempt_id="ollama-smoke-stage",
        stage=generation_stage,
        prompt_context=prompt,
    )
    entry: dict[str, object] = {
        "stage": request.stage.value,
        "stageAlias": stage,
        "stageTemplate": version,
        "promptChars": len(prompt),
    }
    started = time.perf_counter()
    result = provider.generate(request)
    entry["elapsedSeconds"] = round(time.perf_counter() - started, 4)
    # Truthful transport flag: what was ACTUALLY sent in /api/chat format.
    entry["transportStructuredOutput"] = bool(provider.structured_output_sent)
    entry["providerResult"] = result.content is not None
    if result.timed_out:
        entry["errorSanitized"] = "timed out"
    elif result.error is not None:
        entry["errorSanitized"] = str(result.error)[:200]
    if result.content is not None:
        entry["responseBytes"] = len(result.content.encode("utf-8", errors="replace"))
        ok, issues = _parse_issues_for(stage, result.content)
        entry["parsedOk"] = ok
        entry["parseIssues"] = issues
        entry["parseIssueCount"] = len(issues)
        entry["rawSanitized"] = _sample_sanitized(result.content)
    if _DEBUG_FLAG:
        if result.content is not None:
            entry["rawSanitizedFull"] = _sanitize_text(result.content)
        else:
            entry["rawSanitizedFull"] = None
    return entry


# --------------------------------------------------------------------------- #
# AssetSpec geometry round-trip through the REAL driver (Phase 17 diagnostics)
# --------------------------------------------------------------------------- #


def _geometry_repair_roundtrip(settings, provider) -> dict[str, object]:
    """One ASSET_SPEC + bounded Phase 17 geometry-repair round-trip through the
    REAL adapter and the REAL ``OllamaAssetSpecProvider`` driver.

    Reports ONLY sanitized Phase 17 metrics (issue count before repair, repair
    attempts, per-pass issues, final part count / declared dimensions /
    estimated bounding box / silhouette state, first-pass vs repaired, final
    compiled ``proc.*`` id) — never the raw AssetSpec, the prompt text, the
    base URL or any network detail.
    """
    from app.assets.compiler import asset_id_for
    from app.assets.spec_provider import AssetSpecRequest
    from app.assets.specs import parse_asset_spec
    from app.services.ollama_driver import OllamaAssetSpecProvider

    spec_provider = OllamaAssetSpecProvider(
        provider=provider,
        attempt_id="ollama-smoke-geometry",
        budget_consumer=lambda: True,
    )
    result = spec_provider.generate(
        AssetSpecRequest(
            requested_name="bronze ceremonial ice pick", category_hint="decor"
        )
    )
    out: dict[str, object] = {
        "providerOk": result.error is None,
        "geometricallyValid": result.error is None,
        # True request-level count: initial ASSET_SPEC + each ASSET_SPEC_REPAIR
        # = repairAttempts + 1 (Phase17C §8 — a repair is a REAL provider call,
        # never a free local reprocessing). ``calls`` (the outer spec-provider
        # invocation) is always 1 and not a provider-consumption figure.
        "providerCalls": spec_provider.request_calls,
        "repairAttempts": spec_provider.last_geometry_metrics.get("repairAttempts", 0)
        if spec_provider.last_geometry_metrics is not None
        else 0,
    }
    if result.error is not None:
        out["errorSanitized"] = str(result.error)[:200]
    if spec_provider.last_geometry_metrics is not None:
        out["geometryMetrics"] = spec_provider.last_geometry_metrics
    out["perPassIssues"] = _geometry_details(spec_provider)
    if result.content is not None:
        try:
            spec = parse_asset_spec(result.content, non_throwing=False)
            out["finalProcId"] = asset_id_for(spec)
        except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
            out["failure"] = f"final candidate could not be compiled: {exc}"
    return out


# --------------------------------------------------------------------------- #
# report builders (never prompts / base URL / raw secrets)
# --------------------------------------------------------------------------- #


def _base_report(settings, probe_available: bool, structured_supported: bool) -> dict:
    from app.generation.ollama_provider import (
        OLLAMA_STRUCTURED_OUTPUT_MIN_VERSION,
    )

    return {
        "enabled": True,
        "provider": "ollama",
        "probeAvailable": probe_available,
        "structuredOutputProbe": {
            "supported": structured_supported,
            "minSupportedVersion": ".".join(
                str(part) for part in OLLAMA_STRUCTURED_OUTPUT_MIN_VERSION
            ),
        },
        "model": str(getattr(settings, "ollama_model", "") or ""),
        "generation": None,
    }


def _sanitized_report(
    settings,
    provider,
    probe_available: bool,
    stage: str,
    structured_supported: bool,
) -> dict:
    """One real per-stage generation through the real adapter + strict parser +
    (for asset_spec) the full Phase 17 geometry round-trip."""
    report = _base_report(settings, probe_available, structured_supported)
    report["generation"] = _single_stage_report(settings, provider, stage)
    if stage == "asset_spec":
        report["geometryRoundtrip"] = _geometry_repair_roundtrip(settings, provider)
    return report


def _roundtrip_report(settings, provider) -> dict:
    """One CASE/PEOPLE + one ASSET_SPEC round-trip through the REAL adapter.
    Reports sanitized PASS/FAIL per stage (only parse-issue counts and the
    presence of expected public data — never the prompt, base URL or raw text)."""
    case = _single_stage_report(settings, provider, "case_truth")
    case_out = dict(case)
    case_out["pass"] = bool(case.get("parsedOk"))
    case_out.pop("rawSanitized", None)
    case_out.pop("rawSanitizedFull", None)

    spec = _single_stage_report(settings, provider, "asset_spec")
    spec_out = dict(spec)
    spec_out["pass"] = bool(spec.get("parsedOk"))
    spec_out.pop("rawSanitized", None)
    spec_out.pop("rawSanitizedFull", None)

    return {
        "casePeople": case_out,
        "assetSpec": spec_out,
        "geometryRepair": _geometry_repair_roundtrip(settings, provider),
    }


_DEBUG_FLAG = False


def main(argv: list[str] | None = None) -> int:
    global _DEBUG_FLAG
    parser = argparse.ArgumentParser(
        prog="tools.ollama_smoke",
        description=(
            "Opt-in smoke test of the local Ollama generation provider. "
            "Requires --enable or OLLAMA_SMOKE_ENABLED=1; never runs in CI. "
            "Use --showcase-steps for the manual Local-AI showcase E2E checklist."
        ),
        epilog=_HERMES_OPERATOR_COMMAND,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--enable",
        action="store_true",
        help="actually run the smoke test (otherwise: skipped (opt-in)).",
    )
    parser.add_argument(
        "--stage",
        choices=list(_SMOKE_STAGES),
        default=None,
        help=(
            "run the smoke for ONE stage only (case_truth | evidence | "
            "world_requirements | asset_spec | asset_spec_repair | repair). "
            "Default: case_truth. asset_spec additionally reports the full "
            "Phase 17 geometry round-trip (first-pass issues, each repair "
            "attempt, final proc.* id)."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "expand raw model response samples to the FULL sanitized text "
            "(credentials/URLs/hosts/ports and truth seeds are ALWAYS "
            "stripped; no secrets, prompts or full unsanitized raw output)."
        ),
    )
    parser.add_argument(
        "--roundtrip",
        action="store_true",
        help="also run one CASE/PEOPLE + one ASSET_SPEC round-trip (sanitized PASS/FAIL).",
    )
    parser.add_argument(
        "--geometry-repair",
        action="store_true",
        help="also run one Phase 17 geometry-repair round-trip (sanitized metrics: issueCountBeforeRepair, repairAttempts, per-pass issues, finalPartCount, finalBoundingBox, declaredDimensions, silhouettePassed, generatedOnFirstPass/repaired, finalProcId).",
    )
    parser.add_argument(
        "--full-chain",
        action="store_true",
        help="run the REAL full Prompt-to-World chain (showcase prompt) through the stage driver + controller and report the sanitized publish outcome / solver winners / proc.* placement (operator re-run command for the Phase17 Wave-2 showcase; replaces the manual operator harness).",
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

    _DEBUG_FLAG = args.debug

    from app.core.config import Settings
    from app.generation.ollama_provider import (
        DEFAULT_OLLAMA_BASE_URL,
        OllamaProvider,
        ollama_available,
        ollama_structured_output_supported,
    )

    settings = Settings(generation_provider="ollama")
    try:
        probe_available, _detail = ollama_available(settings)
    except Exception:  # noqa: BLE001 - availability never raises; stay clean
        probe_available = False
    structured_supported = False
    if probe_available:
        try:
            structured_supported = ollama_structured_output_supported(settings)
        except Exception:  # noqa: BLE001 - capability probe never raises
            structured_supported = False

    report = _base_report(settings, probe_available, structured_supported)
    if probe_available:
        provider = OllamaProvider(
            base_url=str(settings.ollama_base_url or DEFAULT_OLLAMA_BASE_URL),
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            temperature=settings.ollama_temperature,
            num_ctx=settings.ollama_num_ctx,
            structured_output=structured_supported,
        )
        try:
            stage = args.stage or "case_truth"
            if args.full_chain:
                report["fullChain"] = _full_chain_report(settings, provider)
            else:
                report = _sanitized_report(
                    settings, provider, True, stage, structured_supported
                )
            if args.roundtrip:
                report["roundtrip"] = _roundtrip_report(settings, provider)
            if args.geometry_repair and "roundtrip" not in report:
                report["geometryRepair"] = _geometry_repair_roundtrip(
                    settings, provider
                )
        except Exception as exc:  # noqa: BLE001 - no tracebacks, sanitized JSON
            report["errorSanitized"] = (
                f"smoke run failed: {type(exc).__name__}: {str(exc)[:200]}"
            )
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
