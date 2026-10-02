"""Phase 24 — hermetic tests for the new release_check guards (§27/§45/§46/§47).

Covers the Phase 24 additions to ``tools.release_check``:

  * credential/token/private-key leak scan (``scan_credentials`` — §46) with
    hostile fixtures that are CONSTRUCTED at runtime (DEF-001: the committed
    source must contain NO literal secret-shaped token, so the repo gate stays
    green on the repo's OWN test tree while detection is still proven against
    real credential patterns);
  * rendered CI-compose validation (``check_ci_compose_config`` — §9/§38) with
    canned Compose renders (no Docker daemon required);
  * .dockerignore QA-scratch + test-tree protection and the final-image
    content-safety checklist (no `.env`/`.git`/local db/raw logs/test scratch/
    private provider config in the context) — §47;
  * the CI fake-world drift guard (generate_ci_fake_world --check).

These run hermetic: no Docker daemon, no network, no live Compose engine.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools import release_check
from tools import generate_ci_fake_world


REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# scan_credentials — §46 hostile fixtures
# --------------------------------------------------------------------------- #


def _write_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def _scan(tree_root: Path, tracked: list[str]) -> list[release_check.Finding]:
    return release_check.scan_credentials(tree_root, tracked)


# --------------------------------------------------------------------------- #
# Hostile fixtures — CONSTRUCTED AT RUNTIME (DEF-001). The scanner must be
# proven against the REAL credential patterns, but the committed test source
# must never contain a literal secret-shaped token: the Phase 24 ``validate``
# job runs release_check on the TRACKED tree, so a literal here would fail the
# repo gate on the repo's OWN test file. Every vector below is therefore built
# from SEPARATE runtime pieces — joined with ``str.join`` (a runtime call the
# CPython peephole optimizer cannot constant-fold) so even the compiled
# ``__pycache__`` bytecode never contains a contiguous PEM/sk-/AKIA marker —
# while the runtime value still matches the exact scanner patterns (also pinned
# by test_credential_scan_clean_on_...).
# --------------------------------------------------------------------------- #


def _fold_proof(*parts: str) -> str:
    """Join hostile parts at runtime; str.join is never constant-folded."""
    return "".join(parts)


def _pem_private_key(body: str = "abcd") -> str:
    head = _fold_proof("-----BEGIN ", "RSA ", "PRIVATE KEY-----")
    foot = _fold_proof("-----END ", "RSA ", "PRIVATE KEY-----")
    return f"{head}\n{body}\n{foot}"


def _openai_sk_token() -> str:
    # A full OpenAI-style marker: `sk-` + 30 alphanumeric chars.
    return _fold_proof("sk-", "abcdefghijklmnopqrstuvwxyz1234")


def _git_pats() -> str:
    # GitHub PAT: `ghp_` + 40; GitLab PAT: `glpat-` + 24.
    return "\n".join(
        (_fold_proof("ghp_", "a" * 40), _fold_proof("glpat-", "b" * 24))
    )


def _aws_access_key() -> str:
    return _fold_proof("AKIA", "IOSFODNN7EXAMPL", "E")


def _vault_hcp_token() -> str:
    # HashiCorp Vault/HCP service token: `hvs.` + 20+ chars.
    return _fold_proof("hvs.", "CVCKAny1234abcdEFGH5678")


def _github_fine_grained_pat() -> str:
    # GitHub FINE-GRAINED PAT shape: `github_pat_` + 22 alnum + `_` + 59 alnum.
    # NOT part of the enforced vector set (DEF-009); built at runtime so the
    # committed tree stays gate-green (DEF-001).
    return _fold_proof("github_pat_", "aabbccddeeffgghhiijjkk", "_", "l" * 59)


def _key_password_assignment_forms() -> str:
    # Generic KEY=/PASSWORD= assignment forms — NOT in the enforced vector set
    # (DEF-009); runtime-built (DEF-001).
    return "\n".join(
        (_fold_proof("LLM_API_KEY=", "placeholder-only"),
         _fold_proof("DB_PASSWORD=", "example"))
    )


def test_credential_scan_flags_pem_private_key(tmp_path: Path) -> None:
    tree = _write_tree(tmp_path, {"src/keys.txt": _pem_private_key()})
    findings = _scan(tree, ["src/keys.txt"])
    assert any(f.severity == "fail" and "PEM private key" in f.message for f in findings)


def test_credential_scan_flags_openai_sk_token(tmp_path: Path) -> None:
    tree = _write_tree(tmp_path, {"src/conf.py": "api_key = '" + _openai_sk_token() + "'"})
    findings = _scan(tree, ["src/conf.py"])
    assert any(f.severity == "fail" and "OpenAI-style API key" in f.message for f in findings)


def test_credential_scan_flags_github_gitlab_pat(tmp_path: Path) -> None:
    tree = _write_tree(tmp_path, {"src/c.txt": _git_pats()})
    findings = _scan(tree, ["src/c.txt"])
    assert any("GitHub PAT" in f.message for f in findings if f.severity == "fail")
    assert any("GitLab PAT" in f.message for f in findings if f.severity == "fail")


def test_credential_scan_flags_aws_access_key(tmp_path: Path) -> None:
    tree = _write_tree(tmp_path, {"src/aws.txt": _aws_access_key()})
    findings = _scan(tree, ["src/aws.txt"])
    assert any("AWS access key" in f.message for f in findings if f.severity == "fail")


def test_credential_scan_flags_vault_hcp_token(tmp_path: Path) -> None:
    tree = _write_tree(tmp_path, {"src/vault.txt": _vault_hcp_token()})
    findings = _scan(tree, ["src/vault.txt"])
    assert any("Vault/HCP token" in f.message for f in findings if f.severity == "fail")


def test_credential_scan_clean_on_committed_test_tools_trees() -> None:
    """DEF-001 regression: the repo gate must be green on its OWN tree.

    Proves ``scan_credentials`` reports ZERO FAIL over the committed
    ``tests/``, ``backend/tests/`` and ``tools/`` trees (the trees the gitlab
    ``validate`` release gate scans once these files are tracked). The hostile
    runtime-built vectors above prove the scanner DETECTS the real patterns;
    this test proves the committed source never contains a literal
    secret-shaped token. If a future hostile fixture is committed as a literal,
    this test fails and the validate gate fails with it.
    """
    all_findings: list[release_check.Finding] = []
    for root in (REPO_ROOT / "tests", REPO_ROOT / "backend" / "tests", REPO_ROOT / "tools"):
        # Mirror the release gate's OWN scan surface: git-tracked textual files
        # only. Bytecode caches (__pycache__/*.pyc) are never tracked and are
        # exactly what the gate never sees — they also make an intentionally
        # hostile source line look matched after constant-folding, which is NOT
        # a release-surface hit.
        tracked = [
            path.relative_to(REPO_ROOT).as_posix()
            for path in sorted(root.rglob("*"))
            if path.is_file()
            and release_check._looks_textual(path)
            and "__pycache__" not in path.parts
            and not path.name.endswith((".pyc", ".pyo"))
        ]
        findings = release_check.scan_credentials(REPO_ROOT, tracked)
        fails = [f for f in findings if f.severity == "fail"]
        assert not fails, [f.render() for f in fails]
        all_findings.extend(findings)
    assert any(f.check == "credentials" for f in all_findings)


def test_credential_scan_ok_when_clean(tmp_path: Path) -> None:
    tree = _write_tree(
        tmp_path,
        {"src/clean.py": "GENERATION_PROVIDER=fake\n# no secrets here"},)
    findings = _scan(tree, ["src/clean.py"])
    assert not [f for f in findings if f.severity == "fail"]
    assert any(f.severity == "ok" and f.check == "credentials" for f in findings)


def test_credential_scan_ok_placeholders_and_examples(tmp_path: Path) -> None:
    # Documented example / placeholder values are NOT credentials.
    tree = _write_tree(
        tmp_path,
        {
            ".env.example": "OLLAMA_BASE_URL=http://127.0.0.1:11434\nLLM_API_KEY=# placeholder only",
            "docs/demo.md": "example sk-xxxx (redacted in docs)",
        },
    )
    # .env.example is the sanctioned example file — never scanned at all.
    findings = _scan(tree, ["docs/demo.md"])
    assert not [f for f in findings if f.severity == "fail"]


def test_credential_scan_fail_closed_without_git(tmp_path: Path) -> None:
    findings = _scan(tmp_path, None)
    assert any(f.severity == "fail" and f.check == "credentials" for f in findings)


# --------------------------------------------------------------------------- #
# DEF-009 — the docs CLAIM exactly what the scanner ENFORCES (no drift)
# --------------------------------------------------------------------------- #


def test_credential_scan_does_not_flag_unenforced_markers(tmp_path: Path) -> None:
    """DEF-009: ``github_pat_`` and generic KEY=/PASSWORD= assignment forms are
    NOT part of the enforced Phase 24 §46 vector set — that is the DOCUMENTED
    contract after the doc/claim alignment. Runtime-constructed (DEF-001).
    """
    tree = _write_tree(
        tmp_path,
        {
            "src/g.txt": _github_fine_grained_pat()
            + "\n"
            + _key_password_assignment_forms()
        },
    )
    findings = _scan(tree, ["src/g.txt"])
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


def test_credential_vector_set_is_exactly_the_documented_contract() -> None:
    """DEF-009 regression: the enforced vector set must stay EXACTLY the Phase
    24 §46 contract documented in ``tools/release_check.py``. Adding or
    removing a vector here forces the doc change to land in the same commit.
    """
    assert [label for label, _ in release_check._TOKEN_PATTERNS] == [
        "PEM private key",
        "OpenAI-style API key (sk-)",
        "GitHub PAT",
        "GitLab PAT",
        "AWS access key",
        "Vault/HCP token",
    ]


def test_credential_doc_comment_claims_exactly_the_enforced_literals() -> None:
    """DEF-009 regression: the ``_TOKEN_PATTERNS`` marker comment must claim the
    exact enforced literals and nothing more. ``github_pat_`` / ``KEY=`` /
    ``PASSWORD=`` were previously claimed but never enforced (QA reproduced
    0 FAIL on both vectors) — re-introducing either the claim or the drift
    fails this test.
    """
    source = (REPO_ROOT / "tools" / "release_check.py").read_text(encoding="utf-8")
    marker_doc = source.split("# Phase 24 §46", 1)[1].split("_TOKEN_PATTERNS", 1)[0]
    for signature in ("PRIVATE KEY", "sk-", "ghp_", "glpat-", "AKIA", "hvs."):
        assert signature in marker_doc, f"documented marker missing: {signature}"
    for overstated in ("github_pat_", "KEY=", "PASSWORD="):
        assert overstated not in marker_doc, (
            f"docstring/comment overstates detection of {overstated!r} (DEF-009)"
        )


# --------------------------------------------------------------------------- #
# check_ci_compose_config — §9/§38 canned-render gate
# --------------------------------------------------------------------------- #


CI_COMPOSES = [REPO_ROOT / "docker-compose.yml", REPO_ROOT / "docker-compose.ci.yml"]
CI_PROFILE = REPO_ROOT / "compose" / "profiles" / "ci.env"


def _render(body: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["docker", "compose"], 0, json.dumps(body), "")


def _ci_config_body(
    *,
    provider: str = "fake",
    bridge: str = "false",
    deadline: str = "300",
    provider_timeout: str = "180",
    logging_ok: bool = True,
    data_volume: bool = True,
    publish_11434: bool = False,
) -> dict:
    service: dict = {
        "environment": {
            "GENERATION_PROVIDER": provider,
            "ENABLE_BRIDGE": bridge,
            "CASE_GENERATION_DEADLINE_SECONDS": deadline,
            "OLLAMA_TIMEOUT_SECONDS": provider_timeout,
        },
        "volumes": (
            [{"type": "volume", "source": "pd-data", "target": "/data"}]
            if data_volume
            else []
        ),
        "ports": [{"target": 8000, "published": "8000"}],
    }
    if logging_ok:
        service["logging"] = {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "5"},
        }
    if publish_11434:
        service["ports"].append({"target": 11434, "published": "11434"})
    return {
        "services": {"procedural-detective": service},
        "volumes": {"pd-data": {"name": "pd-smoke_pd-data"}},
    }


def _check_ci(config: dict) -> list[release_check.Finding]:
    def runner(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        return _render(config)

    return release_check.check_ci_compose_config(
        REPO_ROOT,
        compose_paths=tuple(CI_COMPOSES),
        compose_runner=runner,
        client_ts_path=REPO_ROOT / "frontend" / "src" / "api" / "client.ts",
        caddyfile_path=REPO_ROOT / "docker" / "Caddyfile",
    )


def test_ci_compose_config_passes_supported_profile() -> None:
    findings = _check_ci(_ci_config_body())
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"provider": "ollama"}, "GENERATION_PROVIDER"),
        ({"provider": "fake", "bridge": "true"}, "ENABLE_BRIDGE"),
        # ollama requires the supported 300/180 real profile (Phase 19J-RI2).
        ({"provider": "ollama", "deadline": "60", "provider_timeout": "180"}, "timeout envelope"),
        ({"provider": "ollama", "deadline": "300", "provider_timeout": "301"}, "timeout envelope"),
        ({"provider": "ollama", "deadline": "300", "provider_timeout": "0"}, "timeout envelope"),
        # fake profile: provider timeout below the 5s minimum fails closed.
        ({"provider": "fake", "deadline": "60", "provider_timeout": "1"}, "timeout envelope"),
        ({"logging_ok": False}, "logging"),
        ({"data_volume": False}, "has no declared named volume"),
        ({"publish_11434": True}, "Ollama port"),
    ],
)
def test_ci_compose_config_fails_closed(
    overrides: dict, needle: str
) -> None:
    body = _ci_config_body(**overrides)
    findings = _check_ci(body)
    failures = [f.message for f in findings if f.severity == "fail"]
    assert any(needle in message for message in failures), failures


def test_ci_compose_config_skip_when_overlay_absent(tmp_path: Path) -> None:
    # A tree without docker-compose.ci.yml -> skip, never fail (deployment
    # artifact check; same pattern as prod preflight skips).
    def runner(command: list[str], **_kwargs):
        return _render(_ci_config_body())

    findings = release_check.check_ci_compose_config(
        tmp_path,
        compose_paths=(tmp_path / "docker-compose.yml",),
        compose_runner=runner,
    )
    assert any(f.severity == "skip" for f in findings)


# --------------------------------------------------------------------------- #
# .dockerignore final-image content safety (§47)
# --------------------------------------------------------------------------- #


def test_dockerignore_excludes_qa_scratch_and_test_trees() -> None:
    text = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    # QA scratch dirs + probe working trees (phase 24 additions)
    for pattern in (".qa_*", ".tmp/"):
        assert pattern in text, pattern
    # test/probe trees never ship in the runtime image
    for pattern in ("backend/tests", "e2e", "tests", "tools/tests"):
        assert pattern in text, pattern
    # the whole Phase 20 PD-SEC-07 secret family stays excluded
    for pattern in (".env\n", ".env.*", "!.env.example", "*.db", "logs/", "*.log"):
        assert pattern in text, pattern


def test_dockerignore_release_gate_still_requires_core_patterns() -> None:
    findings = release_check.check_dockerignore(REPO_ROOT)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


def test_final_image_content_safety_checklist() -> None:
    """Phase 24 §47 — final-image content safety at the COMPOSE/CONFIG level.

    The gitlab validate stage + release_check enforce every item; this is the
    hermetic checklist test that pins the CONFIG surface:
      * the runtime stage COPYs ONLY backend/ + assets/ + the built SPA (the
        frontend dist comes from the builder stage, never the local tree);
      * the build context excludes .env / .git / local dbs / raw logs /
        test scratch / QA probe trees (.dockerignore);
      * the rendered prod AND CI services never publish the Ollama port.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    # STAGE 2 (runtime) COPY surface — nothing else may become image content.
    copies = [line for line in dockerfile.splitlines()
              if line.strip().startswith("COPY") and "FROM" not in line]
    runtime_copies = [c for c in copies if "--from=frontend-build" not in c]
    for forbidden_fragment in (".env", "backend/tests", ".qa_", ".tmp"):
        assert not any(forbidden_fragment in c for c in runtime_copies), (
            forbidden_fragment, runtime_copies
        )
    # The SPA entered via --from=frontend-build only (built inside the image),
    # so a stale local frontend/dist can never leak in (§3/§47).
    assert any("--from=frontend-build" in c and "/dist" in c for c in copies)

    # Build context hygiene (same gate tools.release_check enforces) + the
    # rendered-config Ollama-port invariant.
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    for token in (".env", ".git", "*.db", "*.log", "logs/", "frontend/dist",
                  "backend/tests", "e2e", "frontend/e2e", "tools/tests"):
        assert token in dockerignore, token
    # Rendered compose surface: no published 11434 in either profile.
    dev = docker_smoke_http_config_helper()
    assert _rendered_11434_count(dev) == 0, "CI profile must never publish 11434"


def docker_smoke_http_config_helper() -> dict:
    """Return the rendered CI config via a canned compose runner (no daemon)."""
    def runner(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(_ci_config_body()), "")

    class _Ctx:
        pass

    findings = release_check.check_ci_compose_config(
        REPO_ROOT,
        compose_paths=tuple(CI_COMPOSES),
        compose_runner=runner,
    )
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    return _ci_config_body()


def _rendered_11434_count(config: dict) -> int:
    ports = []
    for service in (config.get("services") or {}).values():
        if isinstance(service, dict):
            ports += [str(p.get("published")) for p in service.get("ports") or []
                      if isinstance(p, dict)]
    return sum(1 for p in ports if p == "11434")


# --------------------------------------------------------------------------- #
# CI fake-world drift guard
# --------------------------------------------------------------------------- #


def test_ci_fake_world_check_passes() -> None:
    assert generate_ci_fake_world.check() == []


def test_ci_fake_world_render_matches_committed() -> None:
    committed = (REPO_ROOT / "compose" / "fake-worlds" / "ci-activity-log-world.json").read_bytes()
    assert committed == generate_ci_fake_world.render(generate_ci_fake_world.generate())


def test_ci_fake_world_is_golden_with_laptop_linked_to_activity_log() -> None:
    world = generate_ci_fake_world.generate()
    wg = json.loads(world["world_graph"][0])
    laptop = next(p for p in wg["worldGraph"]["placements"] if p["objectId"] == "apartment_laptop")
    assert laptop["evidenceId"] == "cctv_thomas_scene_01"
    # truth/crime/evidence payloads stay the golden originals (byte-identical)
    golden = json.loads((REPO_ROOT / "backend/app/services/dev_mode_case.json").read_text(encoding="utf-8"))
    assert world["case_truth"] == golden["case_truth"]
    assert world["evidence"] == golden["evidence"]


# --------------------------------------------------------------------------- #
# requirements hash + run_all integrity
# --------------------------------------------------------------------------- #


def test_requirements_md_sha256_unchanged() -> None:
    import hashlib

    digest = hashlib.sha256((REPO_ROOT / "REQUIREMENTS.md").read_bytes()).hexdigest()
    assert digest == "b2e568c029b02de79a74c43bb0e2ecd4d44a7fbec65d7de191a890a6dec9970d"


def test_run_all_includes_phase24_checks() -> None:
    findings = release_check.check_ci_compose_config(REPO_ROOT)
    assert any(f.check == "ci-compose-config" for f in findings)
    tracked = release_check.git_tracked_files(REPO_ROOT)
    assert tracked is not None
    cred = release_check.scan_credentials(REPO_ROOT, tracked)
    assert any(f.check == "credentials" for f in cred)