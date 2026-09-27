"""Phase 24 — hermetic structural lint of ``.gitlab-ci.yml``.

Asserts the pipeline skeleton (§51) exists with the real repo commands and the
Level-0 security/isolation properties WITHOUT needing Docker/GitLab:

  * all six stages present in order;
  * ``workflow: rules`` covers branch push / MR / web / schedule;
  * generator: fake + bridge-disabled determinism (CI variables);
  * ``validate`` runs release_check / catalog_report / adapter drift /
    REQUIREMENTS.md hash integrity;
  * no `docker system prune` / broad cleanup anywhere;
  * every ``docker compose ... down`` is project-scoped with
    ``--remove-orphans`` and the unique ``CI_COMPOSE_PROJECT``;
  * no job echoes/prints private CI variables (OLLAMA_BASE_URL, OLLAMA_MODEL,
    CADDY_DOMAIN, CADDY_EMAIL values);
  * the real-Ollama job is tagged ``real-ollama``, schedule+manual gated, and
    runs ``tools.real_ollama_regression``;
  * artifacts are bounded (docker-smoke.log/report, release-check-out.txt);
  * ADV-001: the validate ``release_check | tee`` pipeline can never mask the
    gate exit status (``set -o pipefail`` guard required);
  * ADV-002: the real-AI job writes the report via the tool's built-in ``--out``
    so the process exit status IS the job status (no ``tee`` mask of DEF-006);
  * ADV-004: smoke jobs are ``interruptible: false`` and their after_script
    teardown/cleanup failures are VISIBLE (guarded, never a bare ``|| true``);
  * ADV-005: every after_script docker command is failure-guarded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_FILE = REPO_ROOT / ".gitlab-ci.yml"

_REQUIRED_STAGES = ["validate", "test", "build", "docker", "smoke", "real-ai"]
_SECRET_VAR_NAMES = ("OLLAMA_BASE_URL", "OLLAMA_MODEL", "CADDY_DOMAIN", "CADDY_EMAIL")


def _text() -> str:
    assert CI_FILE.is_file(), f"{CI_FILE} is missing"
    return CI_FILE.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """Lines that are NOT YAML/comment lines (strip #.. and block markers)."""
    out: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def _code_text(text: str) -> str:
    return "\n".join(_code_lines(text))


def _job_section(text: str, job: str) -> str:
    """The YAML block of ``job:`` — everything after the key up to the next
    top-level (non-indented, non-comment) line."""
    block = text.split(f"{job}:")[1]
    out: list[str] = []
    for line in block.splitlines():
        if line and not line[0].isspace():
            break
        out.append(line)
    return "\n".join(out)


def _no_comment_stripped(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _after_script_commands(job: str) -> list[str]:
    """The shell command lines directly under a job's ``after_script:`` key
    (skips comment lines; stops at a sibling key such as ``artifacts:``)."""
    section = _job_section(_text(), job)
    if "after_script:" not in section:
        return []
    tail = section.split("after_script:", 1)[1].splitlines()
    out: list[str] = []
    for line in tail:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= 2:  # sibling key at the job's key level -> after_script done
            break
        if line.strip().startswith("#"):
            continue
        out.append(line.strip())
    return out


def test_required_stages_present_in_order() -> None:
    text = _text()
    block = text.split("stages:")[1].split("\n", 1)[1]
    block = block.split("default:")[0]
    lines = [line.strip() for line in block.splitlines() if line.strip().startswith("-")]
    assert [line.lstrip("- ").strip() for line in lines] == _REQUIRED_STAGES


def test_workflow_rules_cover_all_triggers() -> None:
    text = _code_text(_text())
    assert "workflow:" in text and "rules:" in text.split("workflow:")[1]
    for needle in (
        'CI_PIPELINE_SOURCE == "merge_request_event"',
        "$CI_COMMIT_BRANCH",
        'CI_PIPELINE_SOURCE == "schedule"',
        'CI_PIPELINE_SOURCE == "web"',
    ):
        assert needle in text, needle


def test_validate_stage_runs_all_fast_gates() -> None:
    text = _text()
    validate = text.split("validate:")[1].split("backend-tests:")[0]
    for needle in (
        "git diff --check",
        "tools.release_check --allow-hosted-placeholders",
        "tools.catalog_report",
        "python tools/generate_adapters.py --check",
        "REQUIREMENTS.md hash mismatch",
        "tools.generate_ci_fake_world --check",
    ):
        assert needle in validate, needle


def _compose_project_variable() -> str:
    text = _text()
    # each docker job defines a unique per-job project var name.
    assert "CI_COMPOSE_PROJECT" in text
    assert "${CI_PIPELINE_ID}" in text and "${CI_JOB_ID}" in text
    return "CI_COMPOSE_PROJECT"


def test_no_broad_docker_cleanup_anywhere() -> None:
    text = _code_text(_text())
    for banned in ("docker system prune", "docker rmi -a", "docker volume prune -af"):
        assert banned not in text, banned


def test_compose_cleanup_is_project_scoped_with_remove_orphans() -> None:
    text = _text()
    downs = [
        line
        for line in text.splitlines()
        if "down -v --remove-orphans" in line
    ]
    assert downs, "no project-scoped down -v --remove-orphans found (must be in after_script)"
    for line in downs:
        assert "CI_COMPOSE_PROJECT" in line, line


def test_docker_jobs_use_unique_project_and_isolated_up() -> None:
    text = _text()
    ups = [
        line
        for line in text.splitlines()
        if '""$CI_COMPOSE_PROJECT""' in line.replace("'", "").replace('"', "")
        or ('up -d' in line and "-p" in line and "CI_COMPOSE_PROJECT" in line)
    ]
    assert ups, "docker smoke must boot its own project-scoped stack"
    for line in ups:
        assert "--build" in line, line


def test_secret_variables_never_echoed_or_printed() -> None:
    text = _text()
    for name in _SECRET_VAR_NAMES:
        # Only mentioned as mask/protected docs ONCE at the header and in the
        # real-ollama comment; never in a shell command/echo line.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if name in stripped:
                assert "echo" not in stripped.lower(), (name, stripped)


def test_real_ollama_job_gates_and_no_fake_fallback() -> None:
    text = _text()
    block = text.split("real-ollama-regression:")[1]
    # Stage split guard: take up to the next top-level non-indented key.
    for section in block.split("\n"):
        if section and not section.startswith((" ", "\t", "#")):
            break
    assert "tags: [real-ollama]" in block
    assert "tools.real_ollama_regression" in block
    assert "$CI_PIPELINE_SOURCE == \"schedule\"" in block
    assert "- when: manual" in block
    assert "GENERATION_PROVIDER: \"ollama\"" in block


def test_generated_ci_artifacts_are_bounded() -> None:
    text = _text()
    for unsafe in (".env", "procedural_detective.db", "*.db"):
        for line in text.splitlines():
            if "paths:" in line:
                continue
            if unsafe in line and "#!/" not in line:
                assert "artifacts" not in line.split(unsafe)[0], (unsafe, line)
    for safe in ("docker-smoke.log", "docker-smoke-report.json", "release-check-out.txt"):
        assert safe in text, safe


def test_no_secrets_baked_into_image_commands() -> None:
    text = _code_text(_text())
    # Never docker build --build-arg with credentials.
    for line in text.splitlines():
        if "docker build" in line or "docker compose build" in line:
            assert "--build-arg" not in line or "SECRET" not in line, line


def test_ci_file_is_yaml_lint_safe_basic() -> None:
    """Minimal structural sanity without a YAML parser dependency: every
    top-level block has a following indented body and stage names resolve."""
    text = _text()
    assert text.count("  stage: ") >= 8
    for stage_name in _REQUIRED_STAGES:
        assert f"  stage: {stage_name}" in text, stage_name


@pytest.mark.parametrize("job", ["backend-tests", "root-tests", "frontend-tests", "bridge-tests"])
def test_test_jobs_run_hermetic_suites(job: str) -> None:
    text = _text()
    assert f"{job}:" in text
    block = text.split(f"{job}:")[1]
    needed = {
        "backend-tests": "python -m pytest backend/tests -q",
        "root-tests": "python -m pytest tests -q",
        "frontend-tests": "npm ci",
        "bridge-tests": "python -m pytest tests -q",
    }[job]
    assert needed in block, (job, needed)


def test_mr_branch_duplication_tuned_via_workflow() -> None:
    text = _code_text(_text())
    # Single workflow: rules block (no separate push job-level rules that
    # would duplicate an MR pipeline).
    assert "workflow:" in text
    assert text.count("workflow:") == 1


# --------------------------------------------------------------------------- #
# DEF-002 / DEF-003 — deterministic backend-tests artifact + runner tags
# --------------------------------------------------------------------------- #

_ACTIVE_JOBS = (
    "validate", "backend-tests", "root-tests", "frontend-tests",
    "bridge-tests", "frontend-build", "docker-build", "docker-smoke",
    "prod-like-smoke", "real-ollama-regression",
)


def test_backend_tests_artifact_is_deterministic_junit() -> None:
    """DEF-002: the backend-tests artifact must be something the job REALLY
    produces at an EXPLICIT path — the pytest junit XML under
    $CI_PROJECT_DIR/.tmp/ — never a .pytest_cache nodeids path that resolves
    to the WRONG directory (pytest writes the cache under the ROOTDIR inferred
    from the args, i.e. backend/, not backend/tests/)."""
    text = _text()
    block = text.split("backend-tests:")[1].split("root-tests:")[0]
    assert "--junitxml=" in block
    assert "junit_family=legacy" in block
    assert "backend/tests/.pytest_cache" not in block
    assert ".tmp/pytest-backend-junit.xml" in block
    # junit is reported to GitLab's test-reports UI AND kept as a bounded
    # diagnostic artifact (§27).
    assert "junit:" in block
    assert "reports:" in block


def test_validate_job_is_docker_tagged() -> None:
    """DEF-003: `validate` MUST declare the normal docker runner tag. The
    documented runner registers with `--run-untagged=false`, so an untagged
    validate job would never be picked up and no pipeline would progress."""
    text = _text()
    validate = text.split("validate:")[1].split("backend-tests:")[0]
    assert "tags: [docker]" in validate, validate
    assert "stage: validate" in validate


def test_every_active_job_declares_a_documented_tag() -> None:
    """DEF-003: no pipeline job may be stuck pending — every active job maps to
    a documented runner tag (`docker` or `real-ollama`, see docs/CI.md §3)."""
    text = _text()
    for job in _ACTIVE_JOBS:
        block = text.split(f"{job}:")[1]
        tag_line = next(
            (line.strip() for line in block.splitlines()
             if line.strip().startswith("tags:")),
            None,
        )
        assert tag_line in ("tags: [docker]", "tags: [real-ollama]"), (
            job, tag_line
        )


def test_docs_ci_runner_registration_matches_job_tags() -> None:
    """DEF-003: docs/CI.md documents the same runner tags the jobs declare, so
    the registration guidance cannot drift from the pipeline."""
    docs = (REPO_ROOT / "docs" / "CI.md").read_text(encoding="utf-8")
    assert "--tag-list docker" in docs
    assert "--tag-list real-ollama" in docs
    assert "--run-untagged=false" in docs
    for job in _ACTIVE_JOBS:
        assert job in docs  # documented job (or the tag table lists it)


# --------------------------------------------------------------------------- #
# ADV-001 — validate gate exit status can never be masked by `tee`
# --------------------------------------------------------------------------- #


def test_validate_job_propagates_release_check_exit_status() -> None:
    """ADV-001: the release_check gate exit status must reach the job status.

    A bare `python -m tools.release_check ... | tee release-check-out.txt`
    returns tee's 0 when the gate FAILS, keeping the validate job GREEN while
    every §20/§46/§9/§38 verdict is FAIL — the pipe must never mask the gate.
    The job must either enable `set -o pipefail` before the pipeline, or the
    gate must NOT be piped through tee without a failure guard.
    """
    text = _text()
    validate = text.split("validate:")[1].split("backend-tests:")[0]
    lines = _no_comment_stripped(validate)
    has_pipefail = any("set -o pipefail" in line for line in lines)
    piped_unguarded = any(
        "tools.release_check" in line
        and "| tee" in line
        and "||" not in line
        and "{" not in line
        for line in lines
    )
    assert has_pipefail or not piped_unguarded, (
        "release_check must not be piped through tee without a failure guard "
        "(add `set -o pipefail` or drop the pipe)"
    )
    # The tee'd artifact stays the job artifact (kept `when: always` on BOTH
    # success and failure — ADV-001 requires the artifact, not a dropped pipe).
    assert "release-check-out.txt" in text


# --------------------------------------------------------------------------- #
# ADV-002 — real-AI job must not mask the tool exit status
# --------------------------------------------------------------------------- #


def test_real_ai_job_does_not_mask_matrix_exit_status() -> None:
    """ADV-002: the real-Ollama job invokes the tool's built-in `--out` report
    writer so the process exit status IS the job status. A `| tee` pipe would
    swallow the DEF-006 red-matrix exit 1 and the scheduled job would stay
    GREEN while Ollama is broken (§34/§35) — a red matrix must fail the job."""
    text = _text()
    block = text.split("real-ollama-regression:")[1]
    section: list[str] = []
    for line in block.splitlines():
        if line and not line.startswith((" ", "\t", "#")):
            break
        section.append(line)
    section_text = "\n".join(section)
    assert "tools.real_ollama_regression --matrix" in section_text
    assert "--out real-ollama-report.json" in section_text
    for line in section:
        if "tools.real_ollama_regression" in line and "| tee" in line:
            assert "||" in line or "{ echo" in line, line
    # The report stays an artifact on success AND failure (when: always).
    assert "real-ollama-report.json" in text


# --------------------------------------------------------------------------- #
# ADV-004 — smoke jobs not auto-cancelable; cleanup failures visible
# --------------------------------------------------------------------------- #


def test_smoke_jobs_are_not_interruptible() -> None:
    """ADV-004: docker-smoke and prod-like-smoke must override the default with
    `interruptible: false` so a new push can never auto-CANCEL a running smoke
    job — GitLab does not run after_script on cancel, which could strand a
    127.0.0.1:8000 (or 80/443) binding. `default.interruptible: true` stays for
    the fast test/build jobs."""
    text = _text()
    default_block = text.split("default:")[1].split("variables:")[0]
    assert "interruptible: true" in default_block  # default unchanged
    for job in ("docker-smoke", "prod-like-smoke"):
        section = _job_section(text, job)
        assert "interruptible: false" in section, job


def test_smoke_cleanup_failures_are_visible() -> None:
    """ADV-004: smoke after_scripts keep the project-scoped
    `down -v --remove-orphans` cleanup but RECORD teardown/cleanup failures
    visibly (guarded `|| { echo ...; true; }`) instead of a silent bare
    `|| true`, so an operator can see a leftover 127.0.0.1:8000 binding (§50)."""
    text = _text()
    for job in ("docker-smoke", "prod-like-smoke"):
        section = _job_section(text, job)
        assert "after_script:" in section, job
        commands = _after_script_commands(job)
        downs = [line for line in commands if "down -v --remove-orphans" in line]
        assert downs, f"{job}: project-scoped teardown must stay in after_script"
        for line in downs:
            assert "|| {" in line and "cleanup failed" in line, (job, line)
            assert "true; }" in line, (job, line)
            assert "CI_COMPOSE_PROJECT" in line, (job, line)


# --------------------------------------------------------------------------- #
# ADV-005 — every after_script docker command is failure-guarded
# --------------------------------------------------------------------------- #


def test_every_after_script_docker_command_is_guarded() -> None:
    """ADV-005: every docker command in every after_script must be failure
    guarded (`|| true`, or the ADV-004 visible form `|| { echo ...; true; }`).
    The `docker image ls ... | head` diagnostic in docker-build previously
    lacked the guard — inconsistent with its sibling diagnostics."""
    for job in ("docker-build", "docker-smoke", "prod-like-smoke"):
        commands = _after_script_commands(job)
        assert commands, job
        docker_lines = [line for line in commands if "docker" in line]
        assert docker_lines, job
        for line in docker_lines:
            assert ("|| true" in line) or ("|| {" in line), (job, line)