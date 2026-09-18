"""QA Phase 15 — independent Showcase-Matrix verification probe (durable).

Independently re-runs the Phase 15 matrix harness TWICE and asserts the gate
contract WITHOUT trusting the developer's baseline file:

  - both runs exit 0 and produce a report file;
  - the two reports are BYTE-IDENTICAL (determinism);
  - summary: rowCount 10, generationSuccessRate 1.0, environmentsAtLeastFive,
    worldGraphsAreTenDistinct, noWorldGraphCollision, zeroUnresolvedOrFallback,
    zeroRequiredObjectFallbacks, allRowsSolverUniqueResolved, allRowsEvidenceReachable,
    objectSetsAreTenDistinct, worldGraphRecompositionIdentical;
  - evidence reachability totals: evidenceLinkedPlacementsTotal == 32 across the
    matrix (every critical-evidence placement non-empty interaction + payload
    evidence id + evidence-capable anchor);
  - provenanceDistribution carries >= 5 PROCEDURAL_GENERATED entries;
  - quality gate pass == True with all four assertions green;
  - demo-honesty markers: demoMode == "deterministic_showcase",
    providerHonesty == "distinct_worlds_per_prompt".

Usage:  python e2e/probes/qa-phase15-matrix-verify.py
Exit 0 = all green (MATRIX_VERIFY_OK). Exit 1 = any assertion failed.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HARNESS = _REPO / "tools" / "showcase_matrix.py"

REQUIRED_EVIDENCE_LINKED_TOTAL = 32
REQUIRED_MIN_PROCEDURAL_GENERATED_PROVENANCE = 5

asserted: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    asserted.append(label)
    if not ok:
        raise AssertionError(f"{label}: FAILED {detail}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="qa_p15_matrix_"))
    out_a = tmp / "run_a.json"
    out_b = tmp / "run_b.json"

    env = dict(os.environ)
    for label, out in (("run1", out_a), ("run2", out_b)):
        proc = subprocess.run(
            [sys.executable, str(_HARNESS), "--out", str(out), "--tmp", str(tmp / label)],
            cwd=str(_REPO),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        check(
            f"{label} exit 0",
            proc.returncode == 0,
            f"rc={proc.returncode} stderr={proc.stderr[-2000:]}",
        )
        check(f"{label} report written", out.exists())

    sha_a, sha_b = sha256(out_a), sha256(out_b)
    check("byte-deterministic identical SHA", sha_a == sha_b, f"{sha_a} vs {sha_b}")

    report = json.loads(out_a.read_text(encoding="utf-8"))
    s = report["summary"]
    gate = report["qualityGate"]

    check("demoMode deterministic_showcase", report["demoMode"] == "deterministic_showcase", report["demoMode"])
    check("providerHonesty distinct_worlds_per_prompt", report["providerHonesty"] == "distinct_worlds_per_prompt", report["providerHonesty"])

    check("rowCount == 10", s["rowCount"] == 10, s["rowCount"])
    check("generationSuccessRate == 1.0", s["generationSuccessRate"] == 1.0, s["generationSuccessRate"])
    check("assertions.environmentsAtLeastFive", s["assertions"]["environmentsAtLeastFive"] is True)
    check("assertions.worldGraphsAreTenDistinct", s["assertions"]["worldGraphsAreTenDistinct"] is True)
    check("assertions.noWorldGraphCollision", s["assertions"]["noWorldGraphCollision"] is True and s["collisions"] == [])
    check("assertions.zeroUnresolvedOrFallback", s["assertions"]["zeroUnresolvedOrFallback"] is True)
    check("assertions.zeroRequiredObjectFallbacks", s["assertions"]["zeroRequiredObjectFallbacks"] is True)
    check("assertions.allRowsSolverUniqueResolved", s["assertions"]["allRowsSolverUniqueResolved"] is True)
    check("assertions.allRowsEvidenceReachable", s["assertions"]["allRowsEvidenceReachable"] is True)
    check("assertions.objectSetsAreTenDistinct", s["assertions"]["objectSetsAreTenDistinct"] is True)
    check("assertions.worldGraphRecompositionIdentical", s["assertions"]["worldGraphRecompositionIdentical"] is True)
    check("assertions.allRowsEnvironmentCorrect", s["assertions"]["allRowsEnvironmentCorrect"] is True)
    check("summary assertions all green", all(s["assertions"].values()))

    check(
        "evidenceLinkedPlacementsTotal == 32",
        s["evidenceLinkedPlacementsTotal"] == REQUIRED_EVIDENCE_LINKED_TOTAL,
        s["evidenceLinkedPlacementsTotal"],
    )
    row_evidence = [r["evidenceLinkedPlacements"] for r in report["rows"]]
    check("per-row evidence reachable all True", all(r["evidenceReachableAll"] for r in report["rows"]))
    for r in report["rows"]:
        check(f"row {r['rowId']} generationSuccess", r["generationSuccess"] is True)
        check(f"row {r['rowId']} environmentCorrect", r["environmentCorrect"] is True)
        check(f"row {r['rowId']} solverUniqueResolved", r["solverUniqueResolved"] is True)
        check(f"row {r['rowId']} evidenceReachableAll", r["evidenceReachableAll"] is True)
        for e in r["evidenceReachable"]:
            check(f"row {r['rowId']} evidence {e['objectId']} reachable", e["reachable"] is True, json.dumps(e))

    prov = s["provenanceDistribution"]
    proc_generated = prov.get("PROCEDURAL_GENERATED", 0)
    check(
        "provenance has >= 5 PROCEDURAL_GENERATED",
        proc_generated >= REQUIRED_MIN_PROCEDURAL_GENERATED_PROVENANCE,
        json.dumps(prov),
    )
    unknown_prov = set(prov) - {
        "CATALOG_EXACT", "CATALOG_ALIAS", "SEMANTIC_MATCH", "PARAMETRIC_VARIANT",
        "PROCEDURAL_GENERATED", "FALLBACK",
    }
    check("provenance keys all documented", not unknown_prov, str(unknown_prov))

    check("qualityGate pass", gate["pass"] is True, json.dumps(gate["assertions"]))
    check("qualityGate assertions all green", all(gate["assertions"].values()))
    check("no cross-class winner", gate["crossClassSemantic"]["pass"] is True)
    check("fallback rarity within ceiling", gate["fallbackRarity"]["pass"] is True)
    check("procedural objects recognizable", gate["proceduralRecognition"]["pass"] is True)
    check("geometry distinct", gate["geometryDistinctness"]["pass"] is True)

    print("MATRIX_VERIFY_OK")
    print(f"checks={len(asserted)} sha={sha_a}")
    print(json.dumps(
        {
            "rowCount": s["rowCount"],
            "generationSuccessRate": s["generationSuccessRate"],
            "distinctEnvironments": s["distinctEnvironments"],
            "distinctObjectSets": s["distinctObjectSets"],
            "distinctWorldGraphs": s["distinctWorldGraphs"],
            "collisions": s["collisions"],
            "unresolvedOrFallbackTotal": s["unresolvedOrFallbackAssetCountTotal"],
            "requiredObjectFallbackTotal": s["requiredObjectFallbackCountTotal"],
            "solverUniqueResolvedCount": s["solverUniqueResolvedCount"],
            "evidenceLinkedPlacementsTotal": s["evidenceLinkedPlacementsTotal"],
            "perRowEvidenceLinked": row_evidence,
            "provenanceDistribution": prov,
            "proceduralAssetCount": s["proceduralAssetCount"],
            "validationRepairCountTotal": s["validationRepairCountTotal"],
            "sceneReadyMsg": f"avg={s['averageSceneReadyEstimateMs']} min={s['minSceneReadyEstimateMs']} max={s['maxSceneReadyEstimateMs']}",
            "qualityGatePass": gate["pass"],
            "summaryAssertionsPass": all(s["assertions"].values()),
        },
        indent=1,
    ))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"MATRIX_VERIFY_FAIL: {exc}", file=sys.stderr)
        sys.exit(1)