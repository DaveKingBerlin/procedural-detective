"""Tests for tools/generate_adapters.py (REQUIREMENTS.md 65A.5 / 65A.6 / 65A.7 / 65B).

All tests are self-contained: they build a fixture workspace under tmp_path and
drive the generator via subprocess (or import it directly for the fingerprint
unit test). The real .rad/, .codex/, .opencode/, .claude/, .cursor/ and
CLAUDE.md are never touched.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "generate_adapters.py"

sys.path.insert(0, str(REPO_ROOT))
import tools.generate_adapters as ga  # noqa: E402


def _run(ws, *flags):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ws), *[str(f) for f in flags]],
        capture_output=True,
        text=True,
        cwd=str(ws),
    )


def _check_report(ws):
    result = _run(ws, "--check", "--all", "--json")
    return json.loads(result.stdout), result


def _snapshot(ws):
    entries = set()
    for dirpath, dirnames, filenames in os.walk(str(ws)):
        root = Path(dirpath)
        for items, is_dir in ((filenames, False), (dirnames, True)):
            for name in items:
                path = root / name
                stat = path.stat()
                rel = path.relative_to(ws).as_posix()
                entries.add((rel + ("/" if is_dir else ""), stat.st_mtime_ns, stat.st_size))
    return entries


def _stub(rel):
    return ("# stub %s\n" % rel).encode("utf-8")


@pytest.fixture
def make_workspace(tmp_path):
    """Build a self-contained fixture workspace and generate adapters once."""
    ws = tmp_path / "workspace"
    (ws / ".rad").mkdir(parents=True)
    manifest = json.loads((REPO_ROOT / ".rad" / "manifest.json").read_bytes())
    (ws / ".rad" / "manifest.json").write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
    canonical = [manifest["core_protocol"], manifest["orchestrator_role"]]
    canonical += [role["path"] for role in manifest["roles"].values()]
    canonical += list(manifest["workflows"].values())
    canonical += list(manifest["policies"])
    for rel in canonical:
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_stub(rel))
    (ws / "AGENTS.md").write_bytes(b"# stub AGENTS.md\n")
    result = _run(ws, "--all")
    assert result.returncode == 0, result.stdout + result.stderr
    return ws


def test_check_all_clean_and_read_only_after_generation(make_workspace):
    ws = make_workspace
    before = _snapshot(ws)
    report, result = _check_report(ws)
    assert result.returncode == 0
    assert report["ok"] is True
    assert report["missing"] == []
    assert report["drifted"] == []
    assert report["stale"] == []
    assert report["generated"] == 0
    assert report["expected_files"] == 28
    assert report["adapter"] == "all"
    assert len(report["fingerprint"]) == 16
    # --check must be strictly read-only: no file or directory metadata may change.
    assert _snapshot(ws) == before


def test_generated_file_counts_per_adapter(make_workspace):
    ws = make_workspace
    counts = {}
    for adapter_dir in (".codex", ".claude", ".cursor", ".opencode"):
        base = ws / adapter_dir
        counts[adapter_dir] = sum(1 for p in base.rglob("*") if p.is_file())
    assert counts[".codex"] == 6
    assert counts[".claude"] == 5
    assert counts[".cursor"] == 10
    assert counts[".opencode"] == 6
    assert (ws / "CLAUDE.md").is_file()


def test_cursor_command_uses_manifest_workflow_path(make_workspace):
    ws = make_workspace
    manifest = json.loads((ws / ".rad" / "manifest.json").read_bytes())
    content = (ws / ".cursor" / "commands" / "rad-preflight.md").read_text(encoding="utf-8")
    assert manifest["workflows"]["preflight"] in content
    assert "Read `%s` and execute that canonical RAD workflow." % manifest["workflows"]["preflight"] in content


def test_missing_generated_file_is_reported(make_workspace):
    ws = make_workspace
    (ws / ".codex" / "agents" / "qa.toml").unlink()
    report, result = _check_report(ws)
    assert result.returncode == 1
    assert report["ok"] is False
    assert report["missing"] == [".codex/agents/qa.toml"]
    assert report["drifted"] == []
    assert report["stale"] == []
    assert report["expected_files"] == 28


def test_stale_adapter_files_are_detected_but_allowlisted_files_are_not(make_workspace):
    ws = make_workspace
    (ws / ".cursor" / "commands" / "rad-obsolete.md").write_bytes(b"stale\n")
    (ws / ".opencode" / "obsolete.md").write_bytes(b"stale\n")
    node = ws / ".opencode" / "node_modules" / "some" / "pkg" / "index.js"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"stale\n")
    (ws / ".opencode" / "package.json").write_bytes(b"{}\n")
    report, result = _check_report(ws)
    assert result.returncode == 1
    assert report["ok"] is False
    stale = set(report["stale"])
    assert ".cursor/commands/rad-obsolete.md" in stale
    assert ".opencode/obsolete.md" in stale
    assert ".opencode/node_modules/some/pkg/index.js" not in stale
    assert ".opencode/package.json" not in stale
    assert ".cursor/commands/rad-obsolete.md" not in report["missing"]
    assert ".cursor/commands/rad-obsolete.md" not in report["drifted"]


def test_canonical_change_drifts_all_adapters_then_regeneration_cleans(make_workspace):
    ws = make_workspace
    protocol = ws / ".rad" / "core" / "protocol.md"
    protocol.write_bytes(protocol.read_bytes() + b"\n")
    report, result = _check_report(ws)
    assert result.returncode == 1
    drifted = set(report["drifted"])
    for rel in ("CLAUDE.md", ".codex/config.toml", ".claude/agents/qa.md",
                ".cursor/commands/rad-preflight.md", ".opencode/agents/orchestrator.md"):
        assert rel in drifted
    result = _run(ws, "--all")
    assert result.returncode == 0
    report, result = _check_report(ws)
    assert result.returncode == 0
    assert report["ok"] is True


def test_manifest_workflow_path_change_updates_cursor_command(make_workspace):
    ws = make_workspace
    manifest_path = ws / ".rad" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["workflows"]["preflight"] = ".rad/workflows/preflight-v2.md"
    manifest["workflows"]["phase_gate"] = ".rad/workflows/phase-gate-v2.md"
    manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
    for rel in (".rad/workflows/preflight-v2.md", ".rad/workflows/phase-gate-v2.md"):
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_stub(rel))
    result = _run(ws, "--all")
    assert result.returncode == 0
    content = (ws / ".cursor" / "commands" / "rad-preflight.md").read_text(encoding="utf-8")
    assert "preflight-v2.md" in content
    assert "workflows/preflight.md" not in content
    # phase_gate has no command mapping: no spurious file may appear.
    assert list((ws / ".cursor" / "commands").glob("rad-phase-gate*")) == []
    report, result = _check_report(ws)
    assert result.returncode == 0
    assert report["ok"] is True


def test_core_fingerprint_deterministic_and_sensitive(make_workspace):
    ws = make_workspace
    manifest = ga.load_manifest(ws / ".rad" / "manifest.json")
    fp1 = ga.core_fingerprint(ws, ".rad/manifest.json", manifest)
    fp2 = ga.core_fingerprint(ws, ".rad/manifest.json", manifest)
    assert fp1 == fp2
    assert len(fp1) == 16
    protocol = ws / ".rad" / "core" / "protocol.md"
    protocol.write_bytes(protocol.read_bytes() + b"\n")
    fp3 = ga.core_fingerprint(ws, ".rad/manifest.json", ga.load_manifest(ws / ".rad" / "manifest.json"))
    assert fp3 != fp1


def test_missing_canonical_file_is_manifest_error(make_workspace):
    ws = make_workspace
    (ws / ".rad" / "core" / "protocol.md").unlink()
    result = _run(ws, "--check", "--all", "--json")
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "error" in report


def test_manifest_missing_required_key_is_manifest_error(make_workspace):
    ws = make_workspace
    manifest_path = ws / ".rad" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    del manifest["generated_adapters"]
    manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
    result = _run(ws, "--check", "--all", "--json")
    assert result.returncode == 2


def test_invalid_manifest_json_is_manifest_error(make_workspace):
    ws = make_workspace
    (ws / ".rad" / "manifest.json").write_bytes(b"{not json")
    result = _run(ws, "--check", "--all", "--json")
    assert result.returncode == 2


def test_unknown_adapter_names_are_skipped(make_workspace):
    ws = make_workspace
    manifest_path = ws / ".rad" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["generated_adapters"].append("frobnicator")
    manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
    result = _run(ws, "--all")
    assert result.returncode == 0
    report, result = _check_report(ws)
    assert result.returncode == 0
    assert report["ok"] is True
    assert report["expected_files"] == 28


def test_default_invocation_is_read_only_check(make_workspace):
    ws = make_workspace
    before = _snapshot(ws)
    result = _run(ws)
    assert result.returncode == 0
    assert _snapshot(ws) == before


def test_deactivated_adapter_generated_files_are_stale(make_workspace):
    """ADV-001 / DEF-001: the complete generated-file set is compared, not just
    the active adapters — a deactivated adapter's generated tree must be reported
    as stale (65A.5 / 65B.4)."""
    ws = make_workspace
    manifest_path = ws / ".rad" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["generated_adapters"] = [a for a in manifest["generated_adapters"] if a != "cursor"]
    manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
    cursor_files = sorted(
        p.relative_to(ws).as_posix() for p in (ws / ".cursor").rglob("*") if p.is_file()
    )
    assert len(cursor_files) == 10

    report, result = _check_report(ws)
    assert result.returncode == 1
    assert report["ok"] is False
    assert set(report["stale"]) == set(cursor_files)
    assert report["expected_files"] == 28 - len(cursor_files)

    # 65A.5 only requires DETECTION: `--all` alone must not delete the files.
    result = _run(ws, "--all")
    assert result.returncode == 0
    assert all((ws / rel).is_file() for rel in cursor_files)

    # Removing the obsolete tree heals the check.
    shutil.rmtree(ws / ".cursor")
    report, result = _check_report(ws)
    assert result.returncode == 0
    assert report["ok"] is True
    assert report["stale"] == []


def test_role_path_from_manifest_is_used_in_generated_agents(make_workspace):
    """ADV-002: agent blurb role paths must follow manifest roles[role]["path"]
    (65A.6 / 65B.5), not the hardcoded `.rad/roles/<key>.md`."""
    ws = make_workspace
    manifest_path = ws / ".rad" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["roles"]["backend-dev"]["path"] = ".rad/roles/backend-v2.md"
    manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))
    (ws / ".rad" / "roles" / "backend-v2.md").write_bytes(_stub(".rad/roles/backend-v2.md"))

    result = _run(ws, "--all")
    assert result.returncode == 0
    for rel in (".codex/agents/backend-dev.toml", ".claude/agents/backend-dev.md",
                ".cursor/agents/backend-dev.md", ".opencode/agents/backend-dev.md"):
        content = (ws / rel).read_text(encoding="utf-8")
        assert ".rad/roles/backend-v2.md" in content
        assert ".rad/roles/backend-dev.md" not in content

    report, result = _check_report(ws)
    assert result.returncode == 0
    assert report["ok"] is True


def test_stale_scan_does_not_follow_symlink_out_of_workspace(make_workspace, tmp_path):
    """ADV-005: external files reachable through a symlink/junction inside an
    adapter dir must never be reported as stale."""
    ws = make_workspace
    outside = tmp_path / "outside_secret"
    (outside / "nested").mkdir(parents=True)
    (outside / "secret_0.bin").write_bytes(b"secret\n")
    (outside / "nested" / "deep.txt").write_bytes(b"secret\n")
    link = ws / ".opencode" / "linkout"
    try:
        os.symlink(str(outside), str(link), target_is_directory=True)
    except OSError as exc:
        pytest.skip("symlink creation not permitted on this platform: %s" % exc)

    report, result = _check_report(ws)
    assert result.returncode == 0
    assert report["ok"] is True
    assert report["stale"] == []


def test_stale_scan_skips_symlinked_adapter_dir_with_note(make_workspace, tmp_path):
    """ADV-005: an adapter BASE whose realpath escapes the workspace is skipped
    entirely (note in non-json output), never crawled as a stale source."""
    ws = make_workspace
    outside = tmp_path / "outside_adapter"
    shutil.copytree(ws / ".cursor", outside)
    (outside / "obsolete.md").write_bytes(b"stale\n")
    shutil.rmtree(ws / ".cursor")
    try:
        os.symlink(str(outside), str(ws / ".cursor"), target_is_directory=True)
    except OSError as exc:
        pytest.skip("symlink creation not permitted on this platform: %s" % exc)

    report, result = _check_report(ws)
    assert result.returncode == 0
    assert report["ok"] is True
    assert report["stale"] == []

    plain = _run(ws)
    assert plain.returncode == 0
    assert "note: skipped .cursor:" in plain.stdout


def test_all_is_idempotent_no_mtime_churn(make_workspace):
    """ADV-007: `--all` on an up-to-date tree rewrites nothing and changes no
    mtime — writes are skipped when bytes are equal, and atomic otherwise."""
    ws = make_workspace
    before = _snapshot(ws)
    result = _run(ws, "--all", "--json")
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["generated"] == 0
    assert _snapshot(ws) == before


def test_manifest_role_value_not_a_dict_is_element_error(make_workspace):
    """ADV-008: a roles member that is not a dict must be a clean GeneratorError
    (exit 2) with a parseable --json report, never a traceback."""
    ws = make_workspace
    manifest_path = ws / ".rad" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["roles"]["adversary"] = "not-a-dict"
    manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))

    result = _run(ws, "--check", "--all", "--json")
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "error" in report
    assert "Traceback" not in result.stderr


def test_manifest_workflow_value_not_a_string_is_element_error(make_workspace):
    """ADV-008: a workflows value that is not a string must be a clean
    GeneratorError (exit 2), never a traceback."""
    ws = make_workspace
    manifest_path = ws / ".rad" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["workflows"]["preflight"] = ["x"]
    manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))

    result = _run(ws, "--check", "--all", "--json")
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "error" in report
    assert "Traceback" not in result.stderr


def test_manifest_absolute_workflow_path_is_rejected(make_workspace):
    """ADV-009: absolute manifest path values must be rejected on load (exit 2)."""
    ws = make_workspace
    manifest_path = ws / ".rad" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["workflows"]["preflight"] = "/etc/evil"
    manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))

    result = _run(ws, "--check", "--all", "--json")
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "error" in report
    assert "Traceback" not in result.stderr


def test_manifest_traversal_workflow_path_is_rejected(make_workspace):
    """ADV-009: `..` path values that escape the workspace must be rejected (exit 2)."""
    ws = make_workspace
    manifest_path = ws / ".rad" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["workflows"]["preflight"] = "../escape.md"
    manifest_path.write_bytes(json.dumps(manifest, indent=2).encode("utf-8"))

    result = _run(ws, "--check", "--all", "--json")
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "error" in report
    assert "Traceback" not in result.stderr