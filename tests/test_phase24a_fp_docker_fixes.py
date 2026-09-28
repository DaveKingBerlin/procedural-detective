"""Phase 24A-FP — hermetic regression tests for the Docker acceptance-fix pass.

Locks the four Phase 24A-FP outcomes WITHOUT a Docker daemon:

  1. BASE dev-compose clean-checkout contract (R1/R2): ``docker-compose.yml``
     rendered alone with an EMPTY env file must SUCCEED (no required/hardcoded
     ``OLLAMA_BASE_URL`` / ``OLLAMA_MODEL`` in the base env block) and must
     never inject ``LLM_API_KEY`` / ``LLM_MODEL`` / ``LIVE_PROVIDER_URL`` as
     empty strings (the original acceptance defect 2). The gate lives in
     ``tools.release_check::check_base_compose_config``; the tests cover the
     real Compose render (when the engine is available) AND a canned runner
     (always hermetic).
  2. The canonical 300/180 timeout envelope defaults are declared identically
     in the base / CI / production compose files (R2 — no 300/300 drift).
  3. ``docker/entrypoint.sh`` (and any other tracked ``*.sh`` with an
     entrypoint role) contains NO CRLF bytes.
  4. Docker build-context hygiene: ``.dockerignore`` carries the pytest/bytecode
     exclusion block exactly once and the pattern set really excludes
     ``backend/.pytest_cache``, nested ``__pycache__`` dirs and ``*.pyc`` files
     (the original acceptance defect 1 — "Zugriff verweigert") while keeping
     real source files (``backend/app/main.py``).

No Docker daemon, no network, no live stack: compose ``config`` renders use the
local Compose engine when present (the same CLI the release gate runs) or an
injected canned runner; the ignore-semantics check reuses git's own ignore
engine against a copy of ``.dockerignore``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools import release_check

REPO_ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- #
# base compose clean-checkout contract (R1 — no required/hardcoded provider var)
# --------------------------------------------------------------------------- #


def _clean_base_config_body() -> dict:
    """A clean rendered BASE dev service: fake default, canonical envelope,
    and NO provider-specific variables at all (clean-checkout contract)."""
    return {
        "services": {
            "procedural-detective": {
                "environment": {
                    "DATABASE_URL": "sqlite:////data/procedural_detective.db",
                    "ENVIRONMENT": "development",
                    "GENERATION_PROVIDER": "fake",
                    "ENABLE_BRIDGE": "false",
                    "CASE_GENERATION_DEADLINE_SECONDS": "300",
                    "OLLAMA_TIMEOUT_SECONDS": "180",
                    "TRUST_PROXY": "false",
                    "CORS_ALLOWED_ORIGINS": "http://localhost:5173",
                    "STATIC_DIR": "/app/static",
                },
                "volumes": [{"type": "volume", "source": "pd-data", "target": "/data"}],
                "ports": [{"target": 8000, "published": "8000"}],
            }
        },
        "volumes": {"pd-data": {"name": "pd-base_pd-data"}},
    }


def _render(body: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["docker", "compose"], 0, json.dumps(body), "")


def _base_runner(body: dict):
    def runner(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        return _render(body)

    return runner


def test_base_compose_gate_passes_clean_canned_render() -> None:
    """Hermetic (no Compose engine needed): a clean base render passes."""
    findings = release_check.check_base_compose_config(
        REPO_ROOT, compose_runner=_base_runner(_clean_base_config_body())
    )
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    assert any(f.check == "base-compose-config" and f.severity == "ok"
               for f in findings)


def test_base_compose_gate_fails_on_empty_provider_strings() -> None:
    """Original acceptance defect 2 regression: the gate rejects the three
    provider settings injected as EMPTY strings (the exact ``LLM_API_KEY: ""``
    shape that broke Settings validation in the container)."""
    body = _clean_base_config_body()
    env = body["services"]["procedural-detective"]["environment"]
    env["LLM_API_KEY"] = ""
    env["LLM_MODEL"] = ""
    env["LIVE_PROVIDER_URL"] = ""
    findings = release_check.check_base_compose_config(
        REPO_ROOT, compose_runner=_base_runner(body)
    )
    failures = [f for f in findings if f.severity == "fail"]
    assert any("EMPTY strings" in f.message for f in failures), [
        f.render() for f in findings
    ]


def test_base_compose_gate_skips_without_base_file(tmp_path: Path) -> None:
    """Deployment-artifact behavior: no base compose -> skip, never fail."""
    findings = release_check.check_base_compose_config(
        tmp_path, compose_runner=_base_runner(_clean_base_config_body())
    )
    assert any(f.severity == "skip" and f.check == "base-compose-config"
               for f in findings)


def test_base_compose_real_render_succeeds_without_ollama_base_url() -> None:
    """R1 regression via the REAL Compose engine (empty env file): the base
    profile renders 'docker compose up --build' with no operator .env and no
    OLLAMA_BASE_URL anywhere. Before the fix this died at interpolation with
    'required variable OLLAMA_BASE_URL is missing a value'."""
    findings = release_check.check_base_compose_config(REPO_ROOT)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


def test_base_compose_source_never_references_provider_vars() -> None:
    """R1 source-level pin: base ``docker-compose.yml`` contains NO
    interpolation of the provider-specific env names (they arrive only via
    ``env_file: .env`` / operator overlays / platform variables)."""
    text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for token in (
        "${LLM_API_KEY",
        "${LLM_MODEL",
        "${LIVE_PROVIDER_URL",
        "${OLLAMA_BASE_URL",
        "${OLLAMA_MODEL",
    ):
        assert token not in text, f"base compose still references {token!r}"


# --------------------------------------------------------------------------- #
# R2 — canonical 300/180 timeout-envelope defaults everywhere
# --------------------------------------------------------------------------- #


def test_canonical_timeout_envelope_defaults_are_300_180_everywhere() -> None:
    """R2 regression: the owner's commit drifted the base dev profile to a
    300/300 envelope while production and CI stayed 300/180. Every compose
    profile must declare the SAME canonical 300/180 defaults
    (docs/DEPLOYMENT.md §16, Phase19J-RI2)."""
    for name in ("docker-compose.yml", "docker-compose.ci.yml", "docker-compose.prod.yml"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert (
            "CASE_GENERATION_DEADLINE_SECONDS: "
            "${CASE_GENERATION_DEADLINE_SECONDS:-300}" in text
        ), f"{name}: deadline must default to 300"
        assert (
            "OLLAMA_TIMEOUT_SECONDS: "
            "${OLLAMA_TIMEOUT_SECONDS:-180}" in text
        ), f"{name}: provider timeout must default to 180"


def test_base_compose_declared_defaults_form_supported_envelope() -> None:
    """The declared 300/180 defaults pass the canonical backend validator."""
    base = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "OLLAMA_TIMEOUT_SECONDS: ${OLLAMA_TIMEOUT_SECONDS:-180}" in base
    violations = release_check.timeout_envelope_violations(
        generation_provider="ollama",
        generation_deadline_seconds="300",
        provider_timeout_seconds="180",
    )
    assert violations == []


# --------------------------------------------------------------------------- #
# shell-script LF enforcement (original acceptance defect 3)
# --------------------------------------------------------------------------- #


def test_linux_shell_entrypoints_are_lf_only() -> None:
    """docker/entrypoint.sh (and any other tracked *.sh that could play a
    Linux entrypoint role) must contain ZERO CRLF bytes — the Windows
    'exec ... no such file or directory' acceptance failure."""
    targets: list[Path] = []
    for script_dir in ("docker", "scripts"):
        directory = REPO_ROOT / script_dir
        if directory.is_dir():
            targets.extend(sorted(directory.glob("*.sh")))
    tracked = set(release_check.git_tracked_files(REPO_ROOT) or [])
    targets = [p for p in targets
               if p.relative_to(REPO_ROOT).as_posix() in tracked]
    assert targets, "no tracked shell scripts found to enforce LF"
    for path in targets:
        data = path.read_bytes()
        assert b"\r" not in data, (
            f"{path.relative_to(REPO_ROOT).as_posix()} contains CRLF bytes — "
            "Linux containers fail on it (Phase24A-FP defect 3)"
        )


# --------------------------------------------------------------------------- #
# .dockerignore build-context hygiene (original acceptance defect 1)
# --------------------------------------------------------------------------- #

_DOCKERIGNORE_MANDATORY_PATTERNS = (
    ".pytest_cache/",
    "**/.pytest_cache/",
    "**/__pycache__/",
    "**/*.pyc",
)


def test_dockerignore_pytest_cache_block_exactly_once() -> None:
    """R3 regression: each of the four Phase 24 cache/bytecode patterns must
    appear EXACTLY ONCE (the owner's commit appended a duplicated block)."""
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    for pattern in _DOCKERIGNORE_MANDATORY_PATTERNS:
        count = lines.count(pattern)
        assert count == 1, (
            f"{pattern!r} appears {count} times in .dockerignore (must be "
            "exactly once)"
        )
    assert text.endswith("\n"), ".dockerignore must end with a trailing newline"


def test_dockerignore_keeps_all_other_exclusions() -> None:
    """The dedup must not remove any OTHER existing exclusion (Phase20
    PD-SEC-07 family + Phase 24 test/probe trees stay)."""
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    for token in (
        ".env\n", ".env.*", "!.env.example",
        "logs/", "*.log", "*.db",
        "backend/tests", "e2e", "tests", "tools/tests",
        ".qa_*", ".tmp/", ".ollama",
    ):
        assert token in text, f".dockerignore lost required exclusion {token!r}"


def _gitignored_scenario_root(tmp_path: Path) -> Path:
    """A throwaway dir whose ``.gitignore`` is a byte copy of the repo's
    ``.dockerignore`` (Docker ignore syntax is gitignore-based), so git's own
    ignore engine evaluates the exact shipped pattern set hermetically."""
    source = (REPO_ROOT / ".dockerignore").read_bytes()
    (tmp_path / ".gitignore").write_bytes(source)
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    return tmp_path


def _is_ignored(root: Path, rel: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--no-index", "-q", rel],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def test_dockerignore_build_context_excludes_pytest_caches(
    tmp_path: Path,
) -> None:
    """Original acceptance defect 1: the build context would exclude
    ``backend/.pytest_cache``, nested ``__pycache__`` trees and arbitrary
    ``*.pyc`` files, while real backend source stays in the context."""
    root = _gitignored_scenario_root(tmp_path)
    assert _is_ignored(root, "backend/.pytest_cache/CACHEDIR.TAG")
    assert _is_ignored(root, "backend/.pytest_cache/README.md")
    assert _is_ignored(root, "backend/a/b/__pycache__/mod.cpython-312.pyc")
    assert _is_ignored(root, "backend/tests/__pycache__/test_x.pyc")
    assert _is_ignored(root, "backend/foo.pyc")
    assert _is_ignored(root, "backend/scripts/util.pyc")
    # Required source/build inputs must NOT be dropped (item B).
    assert not _is_ignored(root, "backend/app/main.py")
    assert not _is_ignored(root, "backend/app/services/dev_mode_case.json")
    assert not _is_ignored(root, "frontend/src/api/client.ts")
    assert not _is_ignored(root, "docker/entrypoint.sh")


def test_ci_compose_gate_passes_with_real_render() -> None:
    """Phase24A-FP item 4: the existing CI rendered-compose gate keeps passing
    now that the base profile no longer requires OLLAMA_BASE_URL at
    interpolation time (the whole chain rendered with the CI profile)."""
    findings = release_check.check_ci_compose_config(REPO_ROOT)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


def test_run_all_includes_base_compose_gate() -> None:
    # Mirrors the CLI gate (`--allow-hosted-placeholders`): hosting/video
    # placeholders are REPORT-only, so the run must have zero FAILs.
    findings = release_check.run_all(
        REPO_ROOT, allow_hosted=True, frontend_dir=None,
    )
    assert any(f.check == "base-compose-config" for f in findings)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]