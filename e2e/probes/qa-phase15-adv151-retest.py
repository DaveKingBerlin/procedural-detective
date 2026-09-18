"""QA-owned ADV-151 / DEF-073 FIX-READY retest probe (independent; e2e/probes).

Reproduces the ORIGINAL phase-15 final-sweep finding (ADV-151: hostile
invocations of `tools/showcase_matrix.py` produced uncaught tracebacks with
exit 1 instead of the documented clean exit-2 path) and verifies the fix:

  1. REAL CLI subprocess: `--tmp` pointing at an EXISTING FILE -> exit 2,
     exactly ONE sanitized stderr line (no traceback), empty stdout.
  2. Broken fixtures import (`fixtures.world_matrix` fails to import) driven
     through the REAL CLI entry (`harness.main`) -> exit 2, one-line sanitized
     stderr, no traceback  (the CLI cannot be pointed at a broken fixture
     without modifying test sources, so the import failure is injected the
     same way the shipped regression tests do — via sys.modules).
  3. Tampered matrix row id (`MATRIX_ORDER` containing an unknown row) ->
     exit 2, one-line sanitized stderr, no traceback.
  4. NORMAL run via the REAL CLI -> exit 0, the 10-row report intact:
     generationSuccessRate 1.0, 10 rows, 10 distinct world graphs, 10/10
     solver-unique-resolved, quality gate pass; report written when --out.

Exit codes: 0 = all PASS, 1 = any FAIL (QA convention, e2e/probes series).
Run: python e2e/probes/qa-phase15-adv151-retest.py [out.json]
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "tests"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:520]}")


def _run_cli(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run the REAL tool CLI as a subprocess; return (rc, stdout, stderr) raw."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "showcase_matrix.py"), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_main(args: list[str]) -> tuple[int, str, str]:
    """Drive the REAL CLI entry (harness.main) in-process; return (rc, out, err)."""
    import tools.showcase_matrix as harness

    out_buf, err_buf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        rc = harness.main(args)
    assert harness.main is not None  # static import sanity; main is the CLI entry
    return int(rc), out_buf.getvalue(), err_buf.getvalue()


def _snapshot_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="qa_adv151_retest_"))

    # --- 1. --tmp pointing at an existing FILE (REAL CLI subprocess) ----------
    blocker = scratch / "occupied.txt"
    blocker.write_text("not a directory", encoding="utf-8")
    out1 = scratch / "m1.json"
    rc, out, err = _run_cli(["--tmp", str(blocker), "--out", str(out1)], REPO_ROOT)
    detail = {
        "rc": rc,
        "stderr_lines": len(err.splitlines()),
        "stdout_bytes": len(out),
        "stderr": err.strip()[:200],
        "traceback_in_err": "Traceback" in err,
    }
    record(
        "cli --tmp existing FILE exits 2, one-line sanitized stderr, no traceback",
        rc == 2
        and len(err.splitlines()) == 1
        and "existing FILE" in err
        and "Traceback" not in err
        and len(out) == 0,
        detail,
    )

    # --- 2. broken fixtures import (injected via sys.modules, CLI entry) ------
    import tools.showcase_matrix as harness

    real_mod = sys.modules.get("fixtures.world_matrix")
    sys.modules["fixtures.world_matrix"] = None  # import now raises ImportError
    scratch2 = scratch / "scratch2"
    rc2, out2, err2 = _run_main(["--tmp", str(scratch2)])
    if real_mod is not None:
        sys.modules["fixtures.world_matrix"] = real_mod
    else:
        sys.modules.pop("fixtures.world_matrix", None)
    detail2 = {
        "rc": rc2,
        "final_stderr_line": err2.splitlines()[-1] if err2.splitlines() else "",
        "traceback_in_err": "Traceback" in err2,
        "traceback_in_out": "Traceback" in out2,
    }
    # NOTE: the harness's own alembic migration INFO log lines (identical on the
    # happy path) may precede the fatal line on stderr; the FATAL sanitized
    # one-liner is the LAST line and no traceback appears anywhere.
    record(
        "broken fixtures import exits 2 with a sanitized fatal one-liner, no traceback",
        rc2 == 2
        and err2.splitlines()
        and err2.splitlines()[-1].startswith("showcase matrix: ")
        and "fixtures" in err2
        and "Traceback" not in err2 + out2,
        detail2,
    )

    # --- 3. tampered matrix row id (MATRIX_ORDER -> unknown row, CLI entry) ---
    from fixtures import world_matrix as world_matrix_mod

    orig_order = world_matrix_mod.MATRIX_ORDER
    world_matrix_mod.MATRIX_ORDER = ("apartment_a", "does_not_exist_row")
    scratch3 = scratch / "scratch3"
    rc3, out3, err3 = _run_main(["--tmp", str(scratch3)])
    world_matrix_mod.MATRIX_ORDER = orig_order
    detail3 = {
        "rc": rc3,
        "final_stderr_line": err3.splitlines()[-1] if err3.splitlines() else "",
        "traceback_in_err": "Traceback" in err3,
        "traceback_in_out": "Traceback" in out3,
    }
    record(
        "tampered unknown matrix row id exits 2 with a sanitized fatal one-liner, no traceback",
        rc3 == 2
        and err3.splitlines()
        and err3.splitlines()[-1].startswith("showcase matrix: ")
        and "unknown matrix row" in err3
        and "Traceback" not in err3 + out3,
        detail3,
    )

    # --- 4. NORMAL run (REAL CLI subprocess, --out nested path) ---------------
    nested = scratch / "out" / "nested"
    out4 = nested / "matrix.json"
    rc4, out4_txt, err4 = _run_cli(["--out", str(out4), "--tmp", str(scratch / "scratch4")], REPO_ROOT)
    report = _snapshot_report(out4) if out4.exists() else None
    summary = (report or {}).get("summary") or {}
    gate = (report or {}).get("qualityGate") or {}
    detail4 = {
        "rc": rc4,
        "report_written": report is not None,
        "generationSuccessRate": summary.get("generationSuccessRate"),
        "rowCount": summary.get("rowCount"),
        "distinctWorldGraphs": summary.get("distinctWorldGraphs"),
        "distinctObjectSets": summary.get("distinctObjectSets"),
        "solverUniqueResolvedCount": summary.get("solverUniqueResolvedCount"),
        "qualityGatePass": gate.get("pass"),
        "demoMode": (report or {}).get("demoMode"),
        "traceback_in_out": "Traceback" in out4_txt + err4,
    }
    record(
        "normal run exits 0 with the 10-row report intact (success 1.0, 10 distinct worlds, gate pass)",
        rc4 == 0
        and report is not None
        and summary.get("generationSuccessRate") == 1.0
        and summary.get("rowCount") == 10
        and summary.get("distinctWorldGraphs") == 10
        and summary.get("distinctObjectSets") == 10
        and summary.get("solverUniqueResolvedCount") == 10
        and gate.get("pass") is True
        and report.get("demoMode") == "deterministic_showcase"
        and "Traceback" not in out4_txt + err4,
        detail4,
    )

    # --- summary ---------------------------------------------------------------
    passed = sum(1 for r in results if r["ok"])
    print(f"\n=== ADV-151/DEF-073 RETEST: {passed}/{len(results)} PASS ===")
    out_path = sys.argv[1] if len(sys.argv) > 1 else str(
        REPO_ROOT / "e2e" / "artifacts" / "qa-phase15-adv151-retest.json"
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps({"probe": "qa-phase15-adv151-retest", "results": results},
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"evidence: {out_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())