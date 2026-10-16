"""Phase 46 — hermetic regression tests for the submission-hardening guards.

Covers the Phase 46 additions to ``tools.release_check`` and the deployment
helper scripts:

  * MERGED PROD COMPOSE BOUNDARY (ADR-007) —
    ``check_prod_merge_effective_config`` renders the exact disallowed
    ``[docker-compose.yml, docker-compose.prod.yml]`` chain; a backend host port
    introduced by the production file (the ``8000:8000`` regression) or any
    unexpected merged host port FAILS, while the shipped chain keeps the
    documented boundaries (Caddy exactly 80/443, backend expose-only :8000, no
    published 11434) and the base dev :8000 inheritance is REPORTED.
  * APPLICATION BEARER-TOKEN HYGIENE (§2) — ``scan_app_bearer_tokens`` with
    hostile fixtures CONSTRUCTED AT RUNTIME (the committed source must never
    contain a literal token), plus clean-case and sanctioned-tree exemptions.
  * `.dockerignore` QA/evidence + coverage exclusions while the Dockerfile COPY
    inputs stay in the build context (git's own ignore engine).
  * deployment helper scripts (`build-docker` / `start-server` .sh/.bat):
    ``bash -n`` syntax, per-target compose file, target validation, and the
    non-destructive guarantees (no volume deletion / prune / upload).

No Docker daemon, no network: compose renders use the local Compose engine when
present or an injected canned runner; everything else is a plain file scan.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import release_check

REPO_ROOT = Path(__file__).resolve().parents[1]


def _render(body: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["docker", "compose"], 0, json.dumps(body), "")


def _runner(merged_body: dict, prod_body: dict):
    """Canned Compose runner: the merged chain has two ``-f`` files, the
    standalone production render has one."""

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        count = [str(a) for a in command].count("-f")
        return _render(merged_body if count >= 2 else prod_body)

    return runner


def _merged_body(
    *,
    backend_ports: list | None = None,
    caddy_ports: list | None = None,
    expose: list | None = None,
    ollama_published: bool = False,
) -> dict:
    backend: dict = {
        "expose": ["8000"] if expose is None else expose,
        "ports": (
            [{"target": 8000, "published": "8000"}]
            if backend_ports is None
            else backend_ports
        ),
    }
    caddy: dict = {
        "ports": (
            [
                {"target": 80, "published": "80"},
                {"target": 443, "published": "443"},
            ]
            if caddy_ports is None
            else caddy_ports
        )
    }
    services: dict = {"procedural-detective": backend, "caddy": caddy}
    if ollama_published:
        services["ollama"] = {"ports": [{"target": 11434, "published": "11434"}]}
    return {"services": services}


def _prod_body(*, backend_ports: list | None = None) -> dict:
    return {
        "services": {
            "procedural-detective": {
                "expose": ["8000"],
                "ports": [] if backend_ports is None else backend_ports,
            },
            "caddy": {
                "ports": [
                    {"target": 80, "published": "80"},
                    {"target": 443, "published": "443"},
                ]
            },
        }
    }


def _check(merged_body: dict, prod_body: dict) -> list[release_check.Finding]:
    return release_check.check_prod_merge_effective_config(
        REPO_ROOT, compose_runner=_runner(merged_body, prod_body)
    )


# --------------------------------------------------------------------------- #
# merged prod compose boundary (ADR-007)
# --------------------------------------------------------------------------- #


def test_merged_gate_passes_canned_shipped_shape() -> None:
    findings = _check(_merged_body(), _prod_body())
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    # The base dev :8000 inheritance is REPORTED (the merge is unsupported),
    # never silently accepted as production.
    assert any(
        f.check == "prod-merge-config" and f.severity == "report"
        for f in findings
    ), [f.render() for f in findings]


def test_merged_gate_fails_when_production_backend_publishes_port() -> None:
    """The exact regression: ``docker-compose.prod.yml`` introduces a backend
    host port (``8000:8000``) — production must stay expose-only."""
    findings = _check(
        _merged_body(),
        _prod_body(backend_ports=[{"target": 8000, "published": "8000"}]),
    )
    fails = [f for f in findings if f.severity == "fail"]
    assert any(
        "standalone production" in f.message and "unexpected host port" in f.message
        for f in fails
    ), [f.render() for f in findings]


def test_merged_gate_fails_on_unexpected_merged_backend_port() -> None:
    findings = _check(
        _merged_body(backend_ports=[{"target": 9000, "published": "9000"}]),
        _prod_body(),
    )
    fails = [f for f in findings if f.severity == "fail"]
    assert any("unexpected host port" in f.message for f in fails), [
        f.render() for f in findings
    ]


def test_merged_gate_fails_on_new_service_host_port() -> None:
    """ADV-46-03: ANY new service publishing a host port fails the merge guard."""
    merged = _merged_body()
    merged["services"]["monitoring"] = {
        "ports": [{"target": 9000, "published": "9000"}]
    }
    findings = _check(merged, _prod_body())
    fails = [f for f in findings if f.severity == "fail"]
    assert any(
        "monitoring" in f.message and "unexpected host port" in f.message
        for f in fails
    ), [f.render() for f in findings]


def test_merged_gate_fails_on_new_service_host_port_in_standalone_prod() -> None:
    prod = _prod_body()
    prod["services"]["monitoring"] = {
        "ports": [{"target": 9090, "published": "9090"}]
    }
    findings = _check(_merged_body(), prod)
    fails = [f for f in findings if f.severity == "fail"]
    assert any(
        "standalone production" in f.message
        and "monitoring" in f.message
        and "unexpected host port" in f.message
        for f in fails
    ), [f.render() for f in findings]


def test_merged_gate_fails_on_network_mode_host() -> None:
    """ADV-46-03: ``network_mode: host`` on any service fails the merge guard."""
    merged = _merged_body()
    merged["services"]["monitoring"] = {"network_mode": "host"}
    findings = _check(merged, _prod_body())
    fails = [f for f in findings if f.severity == "fail"]
    assert any(
        "network_mode" in f.message and "monitoring" in f.message for f in fails
    ), [f.render() for f in findings]


def test_merged_gate_fails_on_network_mode_host_in_standalone_prod() -> None:
    prod = _prod_body()
    prod["services"]["monitoring"] = {"network_mode": "host"}
    findings = _check(_merged_body(), prod)
    fails = [f for f in findings if f.severity == "fail"]
    assert any(
        "standalone production" in f.message and "network_mode" in f.message
        for f in fails
    ), [f.render() for f in findings]


def test_merged_gate_fails_on_wrong_caddy_ports() -> None:
    findings = _check(
        _merged_body(caddy_ports=[{"target": 443, "published": "443"}]),
        _prod_body(),
    )
    fails = [f for f in findings if f.severity == "fail"]
    assert any("exactly 80/443" in f.message for f in fails), [
        f.render() for f in findings
    ]


def test_merged_gate_fails_when_backend_does_not_expose_8000() -> None:
    findings = _check(_merged_body(expose=[]), _prod_body())
    fails = [f for f in findings if f.severity == "fail"]
    assert any("expose private port 8000" in f.message for f in fails), [
        f.render() for f in findings
    ]


def test_merged_gate_fails_when_11434_published() -> None:
    findings = _check(_merged_body(ollama_published=True), _prod_body())
    fails = [f for f in findings if f.severity == "fail"]
    assert any("11434" in f.message for f in fails), [f.render() for f in findings]


def test_merged_gate_skip_when_chain_absent(tmp_path: Path) -> None:
    findings = release_check.check_prod_merge_effective_config(
        tmp_path,
        compose_paths=(tmp_path / "docker-compose.yml", tmp_path / "docker-compose.prod.yml"),
    )
    assert any(
        f.check == "prod-merge-config" and f.severity == "skip" for f in findings
    ), [f.render() for f in findings]


def test_merged_gate_fails_closed_on_render_error() -> None:
    def error_runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 2, "", "compose exploded")

    findings = release_check.check_prod_merge_effective_config(
        REPO_ROOT, compose_runner=error_runner
    )
    fails = [f for f in findings if f.severity == "fail"]
    assert any("fail closed" in f.message for f in fails), [
        f.render() for f in findings
    ]


def test_merged_gate_passes_real_shipped_chain() -> None:
    """The REAL shipped chain (base + prod) keeps the documented boundary; the
    base dev :8000 inheritance is only REPORTED. Skip when the Compose CLI is
    unavailable (the canned tests above stay hermetic)."""
    if not _compose_cli_available():
        pytest.skip("Docker Compose CLI is unavailable")
    findings = release_check.check_prod_merge_effective_config(REPO_ROOT)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    assert any(
        f.check == "prod-merge-config" and f.severity in {"ok", "report"}
        for f in findings
    ), [f.render() for f in findings]


def test_run_all_includes_merge_gate() -> None:
    findings = release_check.check_prod_merge_effective_config(REPO_ROOT)
    assert any(f.check == "prod-merge-config" for f in findings)


def _compose_cli_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "compose", "version"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return probe.returncode == 0


# --------------------------------------------------------------------------- #
# application bearer-token scan (§2) — hostile fixtures built at RUNTIME
# --------------------------------------------------------------------------- #


def _synthetic_token() -> str:
    """A fully SYNTHETIC, deterministic token-like value (43 base64url chars).

    Never derived from any real credential — no original token is re-embedded,
    even in split form (ADV-46-01). ``"A" * 43`` is computed at runtime so the
    committed test source contains no contiguous token literal (the scan runs
    over the repo's own ``tests/`` tree).
    """
    return "A" * 43


def _hostile_shapes(token: str) -> list[str]:
    """The evasive serializations the strengthened scan must still catch."""
    return [
        '{"creatorAccessToken":"' + token + '"}',              # compact JSON
        '{"creatorAccessToken": "' + token + '"}',             # pretty JSON
        "creatorAccessToken=" + token,                         # unquoted '='
        "creatorAccessToken: " + token,                        # unquoted ':'
        '"creatorAccessToken":\n    "' + token + '",',         # value on NEXT line
        'obj["pd_playthrough_token"] = "' + token + '"',       # bracket access
        'localStorage.setItem("pd_playthrough_token", "' + token + '")',  # setItem
        '"PLAYTHROUGHACCESSTOKEN":"' + token + '"',            # case-insensitive
        '"anonymousSessionToken" : ' + token,                  # unquoted after quote
    ]


def _write_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def _app_scan(tree_root: Path, tracked: list[str]) -> list[release_check.Finding]:
    return release_check.scan_app_bearer_tokens(tree_root, tracked)


def test_app_token_scan_flags_bearer_field_value(tmp_path: Path) -> None:
    content = '{\n  "creatorAccessToken": "' + _synthetic_token() + '"\n}\n'
    tree = _write_tree(tmp_path, {"evidence/acceptance.json": content})
    findings = _app_scan(tree, ["evidence/acceptance.json"])
    fails = [f for f in findings if f.severity == "fail"]
    assert any("creatorAccessToken" in f.message for f in fails)
    # The token VALUE is never echoed.
    for finding in findings:
        assert _synthetic_token() not in finding.message


@pytest.mark.parametrize(
    "field",
    ["creatorAccessToken", "playthroughAccessToken", "anonymousSessionToken"],
)
def test_app_token_scan_flags_each_bearer_field(tmp_path: Path, field: str) -> None:
    content = f'{{"{field}": "' + _synthetic_token() + '"}'
    tree = _write_tree(tmp_path, {"evidence/ev.json": content})
    findings = _app_scan(tree, ["evidence/ev.json"])
    assert any(f.severity == "fail" for f in findings), [f.render() for f in findings]


def test_app_token_scan_flags_localstorage_key_value(tmp_path: Path) -> None:
    content = (
        'localStorage.setItem("pd_playthrough_token", "'
        + _synthetic_token()
        + '");\n'
    )
    tree = _write_tree(tmp_path, {"evidence/acceptance.cjs": content})
    findings = _app_scan(tree, ["evidence/acceptance.cjs"])
    fails = [f for f in findings if f.severity == "fail"]
    assert any("pd_playthrough_token" in f.message for f in fails), [
        f.render() for f in findings
    ]
    for finding in findings:
        assert _synthetic_token() not in finding.message


def test_app_token_scan_catches_evasive_shapes(tmp_path: Path) -> None:
    """ADV-46-02: every listed evasive serialization is detected."""
    token = _synthetic_token()
    for index, content in enumerate(_hostile_shapes(token)):
        tree = _write_tree(tmp_path / f"shape{index}", {"evidence/ev.txt": content})
        findings = _app_scan(tree, ["evidence/ev.txt"])
        assert any(f.severity == "fail" for f in findings), (
            index,
            content[:40],
            [f.render() for f in findings],
        )
        for finding in findings:
            assert token not in finding.message


def test_app_token_scan_ignores_code_reference_and_short_values(tmp_path: Path) -> None:
    """A legitimate reference that assigns a NON-literal variable, a short
    placeholder, or a value built in code is NOT a leak."""
    content = (
        'localStorage.setItem("pd_playthrough_token", token);\n'
        'const t = sess.anonymousSessionToken;\n'
        '{"creatorAccessToken": "placeholder-only"}\n'
        'const built = "A".repeat(43);\n'
        'localStorage.setItem("pd_playthrough_token", built);\n'
    )
    tree = _write_tree(tmp_path, {"evidence/acceptance.cjs": content})
    findings = _app_scan(tree, ["evidence/acceptance.cjs"])
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    assert any(f.severity == "ok" for f in findings)


def test_app_token_scan_exempts_sanctioned_test_trees(tmp_path: Path) -> None:
    """Sanctioned trees may construct FAKE tokens on purpose."""
    content = '{"creatorAccessToken": "' + _synthetic_token() + '"}'
    tracked = [
        "backend/tests/fixture.json",
        "bridge/tests/fixture.json",
        "e2e/probes/probe.py",
        "frontend/src/x.spec.ts",
        "frontend/src/y.test.tsx",
    ]
    tree = _write_tree(
        tmp_path,
        {
            "backend/tests/fixture.json": content,
            "bridge/tests/fixture.json": content,
            "e2e/probes/probe.py": content,
            "frontend/src/x.spec.ts": content,
            "frontend/src/y.test.tsx": content,
        },
    )
    findings = _app_scan(tree, tracked)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


def test_app_token_scan_fail_closed_without_git(tmp_path: Path) -> None:
    findings = _app_scan(tmp_path, None)
    assert any(
        f.severity == "fail" and f.check == "app-bearer-tokens" for f in findings
    )


def test_app_token_scan_clean_case(tmp_path: Path) -> None:
    tree = _write_tree(tmp_path, {"src/clean.py": "x = 1  # no tokens\n"})
    findings = _app_scan(tree, ["src/clean.py"])
    assert not [f for f in findings if f.severity == "fail"]
    assert any(f.severity == "ok" and f.check == "app-bearer-tokens" for f in findings)


def test_app_token_scan_clean_on_committed_tree() -> None:
    """The committed tracked tree must be gate-green: no live token values
    (QA redacted the p45fix-qa evidence) and no false positive from the scan's
    own source."""
    tracked = release_check.git_tracked_files(REPO_ROOT)
    assert tracked is not None
    findings = release_check.scan_app_bearer_tokens(REPO_ROOT, tracked)
    fails = [f for f in findings if f.severity == "fail"]
    assert fails == [], [f.render() for f in fails]


# --------------------------------------------------------------------------- #
# .dockerignore — QA/evidence + coverage excluded, COPY inputs kept (§2/§3)
# --------------------------------------------------------------------------- #


def test_dockerignore_excludes_evidence_and_coverage() -> None:
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    for pattern in ("screenshots/", "screenshots/evidence/", "htmlcov/",
                    "coverage/", ".coverage", ".coverage.*", "coverage.xml"):
        assert pattern in text, pattern


def test_required_dockerignore_text_satisfies_the_gate() -> None:
    """DEF-110: the canonical required body (used by the synthetic backend
    fixtures) must itself pass ``check_dockerignore`` — so the fixtures cannot
    drift from the enforced contract."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / ".dockerignore").write_text(
            release_check.required_dockerignore_text(), encoding="utf-8"
        )
        findings = release_check.check_dockerignore(Path(tmp))
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


def test_dockerignore_release_gate_requires_new_patterns() -> None:
    findings = release_check.check_dockerignore(REPO_ROOT)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


def _gitignored_scenario_root(tmp_path: Path) -> Path:
    (tmp_path / ".gitignore").write_bytes(
        (REPO_ROOT / ".dockerignore").read_bytes()
    )
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    return tmp_path


def _is_ignored(root: Path, rel: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--no-index", "-q", rel],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def test_dockerignore_excludes_evidence_and_keeps_runtime_inputs(
    tmp_path: Path,
) -> None:
    root = _gitignored_scenario_root(tmp_path)
    # QA evidence + coverage output are excluded from the build context.
    assert _is_ignored(root, "screenshots/evidence/p45fix-qa/api-failure.txt")
    assert _is_ignored(root, "screenshots/generated/shot.png")
    assert _is_ignored(root, "htmlcov/index.html")
    assert _is_ignored(root, "coverage/index.html")
    assert _is_ignored(root, ".coverage")
    assert _is_ignored(root, ".coverage.host123")
    assert _is_ignored(root, "coverage.xml")
    # Runtime-required COPY inputs stay in the context (Dockerfile surface).
    for rel in (
        "backend/app/main.py",
        "backend/app/services/dev_mode_case.json",
        "assets/environments/foo.json",
        "docker/entrypoint.sh",
        "frontend/src/api/client.ts",
        "compose/fake-worlds/ci-activity-log-world.json",
    ):
        assert not _is_ignored(root, rel), rel


# --------------------------------------------------------------------------- #
# deployment helper scripts (§7-§9)
# --------------------------------------------------------------------------- #

_SHELL_SCRIPTS = ("build-docker.sh", "start-server.sh")
_BAT_SCRIPTS = ("build-docker.bat", "start-server.bat")


def _find_bash() -> str | None:
    """A WORKING bash: on Windows ``shutil.which('bash')`` may resolve to the
    non-functional WSL stub, so probe candidates (Git Bash included)."""
    candidates: list[str] = []
    which = shutil.which("bash")
    if which:
        candidates.append(which)
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
                r"C:\Program Files (x86)\Git\bin\bash.exe",
            ]
        )
    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        try:
            probe = subprocess.run(
                [candidate, "-c", "exit 0"],
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def test_shell_scripts_pass_bash_n() -> None:
    bash = _find_bash()
    if bash is None:
        pytest.skip("bash is unavailable")
    for name in _SHELL_SCRIPTS:
        path = REPO_ROOT / name
        assert path.is_file(), f"{name} is missing at the repo root"
        result = subprocess.run(
            [bash, "-n", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, (name, result.stderr)


def test_shell_scripts_use_strict_mode_and_compose_v2() -> None:
    for name in _SHELL_SCRIPTS:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "set -euo pipefail" in text, name
        assert "docker compose" in text or "$DOCKER compose" in text, name
        # Generic `docker-compose` (v1) is not used.
        assert "docker-compose -f" not in text, name


@pytest.mark.parametrize("name", _SHELL_SCRIPTS + _BAT_SCRIPTS)
def test_scripts_select_correct_compose_file_per_target(name: str) -> None:
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    # dev -> base; netcup/prod -> standalone production file.
    assert "docker-compose.yml" in text, name
    assert "docker-compose.prod.yml" in text, name
    assert "netcup" in text and "prod" in text and "dev" in text, name


@pytest.mark.parametrize("name", _SHELL_SCRIPTS + _BAT_SCRIPTS)
def test_scripts_are_non_destructive(name: str) -> None:
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    lowered = text.lower()
    for banned in ("down -v", "down --volumes", "prune", " push", "git push",
                   "docker push"):
        assert banned not in lowered, (name, banned)


def test_start_server_prints_compose_ps() -> None:
    for name in ("start-server.sh", "start-server.bat"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert " ps" in text, name
        assert " up -d" in text, name


def test_build_docker_builds_only() -> None:
    for name in ("build-docker.sh", "build-docker.bat"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert " build" in text, name


@pytest.mark.parametrize("name", _SHELL_SCRIPTS + _BAT_SCRIPTS)
def test_scripts_validate_target_and_show_usage(name: str) -> None:
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    assert "invalid target" in text.lower(), name
    assert "usage" in text.lower(), name


def test_shell_start_server_uses_sudo_only_as_fallback() -> None:
    text = (REPO_ROOT / "start-server.sh").read_text(encoding="utf-8")
    assert "sudo docker" in text
    assert "docker info" in text  # socket probe before the sudo fallback
