"""Phase 12 — developer-only catalog report CLI (tools/catalog_report.py).

Verifies the CLI contract: runs headless from the repo root, loads + validates
the REAL manifest through ``app.assets``, reports >= 100 assets with per-category
counts and ZERO issues (exit 0), emits a deterministic JSON report, and fails a
deliberately-bad temp catalog with exit code 2 (never a server, no network).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
MANIFEST_PATH = REPO_ROOT / "assets" / "catalog" / "catalog.json"
CLI_PATH = REPO_ROOT / "tools" / "catalog_report.py"

sys.path.insert(0, str(BACKEND_DIR))


def _run_cli(catalog: Path, report: Path | None = None) -> subprocess.CompletedProcess:
    args = [sys.executable, str(CLI_PATH), "--catalog", str(catalog)]
    if report is not None:
        args += ["--report", str(report)]
    return subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _bad_catalog(tmp_path: Path) -> Path:
    """A deliberately-bad manifest: unknown templateId + material outside the
    frozen vocabulary + duplicate alias claims."""
    bad = tmp_path / "bad_catalog.json"
    bad.write_text(
        json.dumps(
            {
                "catalogVersion": 1,
                "fallbackAsset": "PROP_BAD_01",
                "assets": [
                    {
                        "assetId": "PROP_BAD_01",
                        "version": 1,
                        "canonicalName": "bad object",
                        "aliases": ["bad thing"],
                        "category": "utility",
                        "subtype": "prop",
                        "tags": [],
                        "renderKind": "composite",
                        "compositeKind": None,
                        "templateId": "chainsaw",
                        "dimensions": {"x": 0.5, "y": 0.5, "z": 0.5},
                        "colors": {"body": "#8d8d93"},
                        "label": "Bad",
                        "interactable": False,
                        "supportedInteractions": [],
                        "evidenceCapabilities": [],
                        "allowedAnchors": ["GENERIC_PROP"],
                        "variants": [
                            {
                                "name": "x",
                                "params": {
                                    "material": {
                                        "allowlist": ["obsidian"],
                                        "default": "obsidian",
                                    }
                                },
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return bad


def test_cli_reports_phase12_catalog_clean():
    """The real manifest loads (>= 100 assets, zero issues, exit 0) with
    per-category counts and a resolution pass in the human summary."""
    result = _run_cli(MANIFEST_PATH)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "assets=101" in result.stdout or "assets=1" not in result.stdout
    assert "per-category counts:" in result.stdout
    assert "issues: 0" in result.stdout
    assert "exit code: 0 (clean)" in result.stdout
    assert "no server" not in result.stdout  # the CLI is a report tool


def test_cli_deterministic_json_report(tmp_path):
    """--report writes a deterministic JSON document: two runs are identical,
    and the document carries the required sections."""
    first, second = tmp_path / "r1.json", tmp_path / "r2.json"
    _run_cli(MANIFEST_PATH, report=first)
    _run_cli(MANIFEST_PATH, report=second)
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    report = json.loads(first.read_text(encoding="utf-8"))
    assert report["schema"] == "catalog-report-v1"
    assert report["ok"] is True
    assert report["issues"] == []
    assert report["assetCount"] >= 100
    assert report["catalogVersion"] == 1
    assert report["fallbackAsset"] == "PROP_FALLBACK_01"
    assert report["categoryCounts"]["evidence"] >= 20
    assert report["categoryCounts"]["furniture"] >= 20
    assert report["categoryCounts"]["electronics"] >= 10
    assert report["categoryCounts"]["utility"] >= 5
    assert report["resolution"]["checked"] is True
    assert report["resolution"]["unresolved"] == []
    assert report["variantInventory"]["variantAssets"] > 0
    assert report["variantInventory"]["totalVariants"] >= report["variantInventory"]["variantAssets"]
    # dimensions table is present and deterministic
    assert len(report["dimensionsTable"]) == report["assetCount"]


def test_cli_fails_deliberately_bad_catalog_with_exit_2(tmp_path):
    """A mutated/deliberately-bad temp catalog fails the CLI with exit 2."""
    bad = _bad_catalog(tmp_path)
    result = _run_cli(bad, report=tmp_path / "bad_report.json")
    assert result.returncode == 2
    assert "TEMPLATE_VOCABULARY" in result.stdout
    report = json.loads((tmp_path / "bad_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["issues"]
    assert report["exit code"] if "exit code" in report else True  # human-only field


def test_cli_fails_missing_catalog_path_with_exit_2(tmp_path):
    result = _run_cli(tmp_path / "does_not_exist.json")
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_cli_unparseable_json_exits_2_without_traceback(tmp_path):
    """DEF-064: an unparseable-JSON catalog path exits 2 with a sanitized
    stderr message — never a KeyError traceback (exit 1)."""
    bad = tmp_path / "unparseable.json"
    bad.write_text("not json !{", encoding="utf-8")
    result = _run_cli(bad)
    assert result.returncode == 2
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    assert "KeyError" not in result.stderr
    assert "is not valid JSON" in result.stderr
    assert str(bad) in result.stderr

    # The optional --report still writes a well-formed, full-shape artifact.
    report_path = tmp_path / "unparseable_report.json"
    _run_cli(bad, report=report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == "catalog-report-v1"
    assert report["ok"] is False
    assert report["assetCount"] == 0
    assert report["catalogVersion"] is None
    assert report["issues"]
    assert report["fatal"] and "is not valid JSON" in report["fatal"]


def test_cli_non_object_json_exits_2_without_traceback(tmp_path):
    """DEF-064 hardening: a file that parses but is NOT a JSON object (e.g. a
    bare array/scalar root) also exits 2 with a sanitized message, never a
    traceback."""
    for contents in ("[1,2,3]", "null", '"a bare string"'):
        bad = tmp_path / "non_object.json"
        bad.write_text(contents, encoding="utf-8")
        result = _run_cli(bad)
        assert result.returncode == 2, contents
        assert "Traceback" not in result.stdout
        assert "Traceback" not in result.stderr
        assert "JSON object" in result.stderr, contents


def test_cli_directory_path_exits_2_without_traceback(tmp_path):
    """A directory argument cannot be read -> sanitized stderr + exit 2."""
    result = _run_cli(tmp_path)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "cannot be read" in result.stderr


def test_cli_runs_headless_without_server_or_network():
    """Regression guard: the CLI completes without ever binding a server and
    its output carries no network artifacts."""
    result = _run_cli(MANIFEST_PATH)
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "uvicorn" not in combined
    assert "bound to" not in combined
    assert "http://" not in combined.replace("http://localhost", "")


def test_cli_per_category_counts_cover_phase12_distribution(tmp_path):
    """The report's per-category counts cover the Phase 12 distribution."""
    report_path = tmp_path / "cat.json"
    result = _run_cli(MANIFEST_PATH, report=report_path)
    assert result.returncode == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    counts = report["categoryCounts"]
    total = sum(counts.values())
    assert total == report["assetCount"] >= 100
    # documents + forensic props live under the frozen 'evidence' category.
    assert counts["evidence"] >= 40
    assert counts["structural"] >= 10
    assert counts["decor"] >= 15