"""Phase 21B — deployment/docs closure (POST-audit, deployment track).

Covers, hermetic and read-only (no network, no database):

  Finding 2 (MEDIUM) — DEV vs PRODUCTION env profile separation:
    - `.env.example` is the DEV profile (`ENVIRONMENT=development`,
      `TRUST_PROXY=false`) and `.env.production.example` is the PRODUCTION
      profile (`ENVIRONMENT=production`, `PD_DEV_TRACE=false`,
      `TRUST_PROXY=true` for the shipped Caddy edge) with no dev-only value;
    - a DEV `.env` copied over the prod compose FAILS the effective-config
      preflight (the core Finding-2 defect), while `.env.production.example`
      passes;
    - `check_prod_env_profile` fails when the split is regressed.

  Finding 6 (LOW) — privacy docs: PRIVACY.md documents the canonical audited
    `python -m tools.delete_case` host-side path and NO LONGER claims
    `alembic upgrade head` recreates dropped triggers; a NEW
    docs/OPERATIONS.md runbook exists (backup-before-delete, single-writer,
    host-side tool availability); the runtime Dockerfile does NOT copy the
    tools/ tree (host-side invocation documented).

  Finding 7 — fail-closed production preflight (`check_prod_effective_config`
    + `python -m tools.prod_preflight`): effective values (ENVIRONMENT /
    TRUST_PROXY / PD_DEV_TRACE), P-02 timeout envelope, json-file log bounds
    on both public services, private backend port, no published Ollama port,
    placeholder CADDY_DOMAIN fails the ready-to-host verdict, and the prod
    example itself never carries dev values.

Read-only: no backend/frontend SOURCE is changed; only the deployment/docs
artifacts owned by this track are asserted. The compose/preflight helpers are
unit-tested against tmp_path trees seeded from the real files.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent
for _p in (str(_BACKEND_DIR), str(_REPO_ROOT), str(_REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import release_check  # noqa: E402

_COMPOSE_PROD = _REPO_ROOT / "docker-compose.prod.yml"
_CLIENT_TS = _REPO_ROOT / "frontend" / "src" / "api" / "client.ts"
_CADDYFILE = _REPO_ROOT / "docker" / "Caddyfile"
_ENV_DEV = _REPO_ROOT / ".env.example"
_ENV_PROD = _REPO_ROOT / ".env.production.example"


def _real_compose_text() -> str:
    return _COMPOSE_PROD.read_text(encoding="utf-8")


def _real_client_ts() -> Path:
    assert _CLIENT_TS.is_file()
    return _CLIENT_TS


def _real_caddyfile() -> Path:
    assert _CADDYFILE.is_file()
    return _CADDYFILE


def _write_prod_example(
    tmp_path: Path, *, domain: str = "detective.procedural-game.dev"
) -> None:
    (tmp_path / ".env.production.example").write_text(
        "ENVIRONMENT=production\n"
        "PD_DEV_TRACE=false\n"
        "TRUST_PROXY=true\n"
        f"CADDY_DOMAIN={domain}\n"
        "CASE_GENERATION_DEADLINE_SECONDS=300\n",
        encoding="utf-8",
    )


def _seed(tmp_path: Path, *, compose: str | None = None) -> Path:
    """A tmp "deployment root": real prod compose (+ real client/Caddy files
    referenced by absolute path via the helper overrides), plus the PROD env
    example copied to the documented startup ``.env``. Returns tmp_path."""
    (tmp_path / "docker-compose.prod.yml").write_text(
        compose if compose is not None else _real_compose_text(), encoding="utf-8"
    )
    _write_prod_example(tmp_path)
    # Match the documented startup profile: production example copied to the
    # automatic Compose .env before `docker compose ... up` / preflight.
    (tmp_path / ".env").write_text(
        (tmp_path / ".env.production.example").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# Finding 2 — DEV/PROD env profile split
# --------------------------------------------------------------------------- #


def test_examples_split_dev_and_prod_on_real_tree():
    assert _ENV_DEV.is_file(), ".env.example must exist"
    assert _ENV_PROD.is_file(), ".env.production.example must exist"

    dev = release_check._read_env_file(_REPO_ROOT, ".env.example")
    prod = release_check._read_env_file(_REPO_ROOT, ".env.production.example")

    # DEV profile.
    assert dev["ENVIRONMENT"] == "development"
    assert dev["TRUST_PROXY"] == "false"

    # PRODUCTION profile.
    assert prod["ENVIRONMENT"] == "production"
    assert prod["PD_DEV_TRACE"] == "false"
    assert prod["TRUST_PROXY"] == "true"

    # No dev-only value anywhere in the production example.
    assert prod.get("ENVIRONMENT") != "development"
    assert prod.get("TRUST_PROXY") != "false"
    assert prod.get("PD_DEV_TRACE") != "true"


def test_check_prod_env_profile_passes_on_real_tree():
    findings = release_check.check_prod_env_profile(_REPO_ROOT)
    assert [f.severity for f in findings] == ["ok"], [f.render() for f in findings]


def test_check_prod_env_profile_fails_when_prod_example_carries_dev_value(tmp_path):
    (tmp_path / ".env.example").write_text(
        "ENVIRONMENT=development\nTRUST_PROXY=false\n", encoding="utf-8"
    )
    (tmp_path / ".env.production.example").write_text(
        "ENVIRONMENT=production\nPD_DEV_TRACE=false\nTRUST_PROXY=false\n",
        encoding="utf-8",
    )
    findings = release_check.check_prod_env_profile(tmp_path)
    assert any(f.severity == "fail" for f in findings), [f.render() for f in findings]
    assert any("TRUST_PROXY" in f.message for f in findings)


def test_check_prod_env_profile_skips_when_example_missing(tmp_path):
    """A document tree WITHOUT the production deployment templates is SKIPPED
    (not failed): the DEV/PROD split check applies only when the deployment
    examples exist. A tree that DOES carry the examples still fails closed on
    every asserted violation (see the dev-value regression test above)."""
    findings = release_check.check_prod_env_profile(tmp_path)
    assert [f.severity for f in findings] == ["skip"], [f.render() for f in findings]
    assert any(".env.production.example" in f.message for f in findings)


def test_tracked_secrets_still_clean_with_prod_example():
    """git ls-files must not flag .env / logs / dbs — and the PRODUCTION example
    stays SANCTIONED (a tracked template, never a secret) even after it is
    staged for release."""
    tracked = release_check.git_tracked_files(_REPO_ROOT)
    assert tracked is not None
    assert release_check.tracked_secret_entries(tracked) == []
    simulated = list(tracked) + [".env.production.example"]
    assert release_check.tracked_secret_entries(simulated) == [], (
        ".env.production.example must be a sanctioned example, never a tracked secret"
    )
    assert ".env.production.example" in release_check._SANCTIONED_ENV_EXAMPLES
    assert _ENV_PROD.is_file()


# --------------------------------------------------------------------------- #
# Finding 7 — fail-closed effective-config preflight
# --------------------------------------------------------------------------- #


def test_effective_prod_profile_passes_with_prod_example(tmp_path):
    _seed(tmp_path)
    findings = release_check.check_prod_effective_config(
        tmp_path,
        allow_local=False,
        client_ts_path=_real_client_ts(),
        caddyfile_path=_real_caddyfile(),
    )
    assert not any(f.severity == "fail" for f in findings), [f.render() for f in findings]
    assert any(f.severity == "ok" for f in findings)


def test_dev_env_copied_over_prod_compose_preflight_fails(tmp_path):
    """Finding 2 core defect: an operator copies the DEV example to .env and
    runs the prod compose — the effective config MUST fail the preflight."""
    _seed(tmp_path)
    (tmp_path / ".env").write_text(
        "ENVIRONMENT=development\nTRUST_PROXY=false\nPD_DEV_TRACE=true\n",
        encoding="utf-8",
    )
    findings = release_check.check_prod_effective_config(
        tmp_path,
        allow_local=True,  # CADDY_DOMAIN forgiveness must NOT hide this
        client_ts_path=_real_client_ts(),
        caddyfile_path=_real_caddyfile(),
    )
    fail_messages = [f.message for f in findings if f.severity == "fail"]
    assert fail_messages, [f.render() for f in findings]
    assert any("ENVIRONMENT" in m for m in fail_messages), fail_messages
    assert any("TRUST_PROXY" in m for m in fail_messages), fail_messages
    assert any("PD_DEV_TRACE" in m for m in fail_messages), fail_messages


def test_dev_env_trust_proxy_false_only_fails_too(tmp_path):
    """Even a single dev override (TRUST_PROXY=false) is enough to fail."""
    _seed(tmp_path)
    (tmp_path / ".env").write_text("TRUST_PROXY=false\n", encoding="utf-8")
    findings = release_check.check_prod_effective_config(
        tmp_path,
        allow_local=True,
        client_ts_path=_real_client_ts(),
        caddyfile_path=_real_caddyfile(),
    )
    fail_messages = [f.message for f in findings if f.severity == "fail"]
    assert any("TRUST_PROXY" in m for m in fail_messages), fail_messages


def test_placeholder_caddy_domain_fails_ready_to_host(tmp_path):
    """CADDY_DOMAIN=localhost/empty FAILS the strict ready-to-host verdict and
    is a REPORT under --allow-local (local-smoke default)."""
    _seed(tmp_path)
    (tmp_path / ".env").write_text(
        "ENVIRONMENT=production\nPD_DEV_TRACE=false\nTRUST_PROXY=true\n"
        "CADDY_DOMAIN=localhost\n",
        encoding="utf-8",
    )
    strict = release_check.check_prod_effective_config(
        tmp_path, allow_local=False,
        client_ts_path=_real_client_ts(), caddyfile_path=_real_caddyfile(),
    )
    assert any(
        f.severity == "fail" and "CADDY_DOMAIN" in f.message for f in strict
    ), [f.render() for f in strict]

    local = release_check.check_prod_effective_config(
        tmp_path, allow_local=True,
        client_ts_path=_real_client_ts(), caddyfile_path=_real_caddyfile(),
    )
    assert not any(f.severity == "fail" for f in local), [f.render() for f in local]
    assert any(
        f.severity == "report" and "CADDY_DOMAIN" in f.message for f in local
    ), [f.render() for f in local]


def test_empty_caddy_domain_fails_ready_to_host(tmp_path):
    _seed(tmp_path)
    (tmp_path / ".env").write_text(
        "ENVIRONMENT=production\nPD_DEV_TRACE=false\nTRUST_PROXY=true\n"
        "CADDY_DOMAIN=\n",
        encoding="utf-8",
    )
    strict = release_check.check_prod_effective_config(
        tmp_path, allow_local=False,
        client_ts_path=_real_client_ts(), caddyfile_path=_real_caddyfile(),
    )
    assert any(
        f.severity == "fail" and "CADDY_DOMAIN" in f.message for f in strict
    ), [f.render() for f in strict]


def test_missing_log_bounds_fails(tmp_path):
    """Finding 7 — removing the json-file 10m x 5 blocks fails the preflight."""
    text = _real_compose_text()
    # Drop both logging blocks (and their option bodies) to simulate a regressed
    # compose, then re-seed WITHOUT the blocks.
    import re as _re

    stripped = _re.sub(
        r"\n    logging:\n      driver: json-file\n      options:\n"
        r"        max-size: \"10m\"\n        max-file: \"5\"",
        "",
        text,
    )
    assert "logging:" not in stripped
    (tmp_path / "docker-compose.prod.yml").write_text(stripped, encoding="utf-8")
    _write_prod_example(tmp_path)
    findings = release_check.check_prod_effective_config(
        tmp_path, allow_local=True,
        client_ts_path=_real_client_ts(), caddyfile_path=_real_caddyfile(),
    )
    fail_messages = [f.message for f in findings if f.severity == "fail"]
    assert any("logging" in m.lower() or "json-file" in m for m in fail_messages), fail_messages


def test_deadline_above_frontend_timeout_fails(tmp_path):
    """P-02 envelope: effective deadline >= frontend 360s fails the preflight."""
    _seed(tmp_path)
    # Phase21C validates Compose's rendered container environment.  The
    # service env_file is the real startup input for Settings-only keys such as
    # CASE_GENERATION_DEADLINE_SECONDS, so exercise a real .env here.
    (tmp_path / ".env").write_text(
        "ENVIRONMENT=production\nPD_DEV_TRACE=false\nTRUST_PROXY=true\n"
        "CADDY_DOMAIN=detective.procedural-game.dev\n"
        "CASE_GENERATION_DEADLINE_SECONDS=400\n",
        encoding="utf-8",
    )
    findings = release_check.check_prod_effective_config(
        tmp_path, allow_local=True,
        client_ts_path=_real_client_ts(), caddyfile_path=_real_caddyfile(),
    )
    fail_messages = [f.message for f in findings if f.severity == "fail"]
    assert any("deadline" in m for m in fail_messages), fail_messages


def test_backend_port_publication_fails(tmp_path):
    """Finding 7 — a `ports:` block on the backend (or any published 11434)
    fails the preflight."""
    text = _real_compose_text()
    text = text.replace(
        "    expose:\n      - \"8000\"",
        "    ports:\n      - \"8000:8000\"\n    expose:\n      - \"8000\"",
    )
    _seed(tmp_path, compose=text)
    findings = release_check.check_prod_effective_config(
        tmp_path, allow_local=True,
        client_ts_path=_real_client_ts(), caddyfile_path=_real_caddyfile(),
    )
    fail_messages = [f.message for f in findings if f.severity == "fail"]
    assert any("ports:" in m for m in fail_messages), fail_messages


def test_published_ollama_port_fails(tmp_path):
    text = _real_compose_text().replace(
        "\nvolumes:\n",
        "\n  ollama-leak:\n"
        "    image: ollama/ollama:latest\n"
        "    ports:\n"
        '      - "11434:11434"\n'
        "    networks:\n"
        "      - pd-internal\n"
        "\nvolumes:\n",
        1,
    )
    assert "11434:11434" in text
    _seed(tmp_path, compose=text)
    findings = release_check.check_prod_effective_config(
        tmp_path, allow_local=True,
        client_ts_path=_real_client_ts(), caddyfile_path=_real_caddyfile(),
    )
    fail_messages = [f.message for f in findings if f.severity == "fail"]
    assert any("11434" in m for m in fail_messages), fail_messages


def test_run_all_wires_new_checks_and_stays_green_on_real_tree():
    """run_all includes prod-env-profile + prod-effective-config; on the real
    tree BOTH pass (the local operator .env carries no prod markers, so the
    compose defaults apply)."""
    findings = release_check.run_all(_REPO_ROOT, allow_hosted=True)
    check_names = {f.check for f in findings}
    assert "prod-env-profile" in check_names
    assert "prod-effective-config" in check_names
    new_fail = [
        f for f in findings
        if f.check in ("prod-env-profile", "prod-effective-config")
        and f.severity == "fail"
    ]
    assert new_fail == [], [f.render() for f in new_fail]


# --------------------------------------------------------------------------- #
# Finding 6 — privacy docs + host-side tool availability
# --------------------------------------------------------------------------- #


def test_privacy_doc_canonical_command_and_no_obsolete_sql():
    privacy = _REPO_ROOT / "docs" / "PRIVACY.md"
    text = privacy.read_text(encoding="utf-8")
    blob = " ".join(text.split())  # wrapping-safe

    # Canonical audited path documented.
    assert "python -m tools.delete_case <case_id> --yes" in blob
    assert "alembic upgrade head" in text
    # The obsolete raw-SQL trigger-drop procedure is GONE.
    assert "DROP TRIGGER IF EXISTS" not in text
    assert "sqlite3.connect(DB)" not in text
    # And the false claim is gone too.
    assert "recreate missing triggers" not in blob
    assert "idempotent and recreate" not in blob


def test_privacy_doc_documents_compose_native_invocation_and_backup_first():
    text = _REPO_ROOT.joinpath("docs", "PRIVACY.md").read_text(encoding="utf-8")
    blob = " ".join(text.split())
    lowered = blob.lower()
    assert "docker compose run" in lowered
    assert "does **not** ship" in blob
    assert "back up" in lowered and "first" in lowered
    assert "tools.backup_production backup" in blob
    assert "docker compose -f docker-compose.prod.yml stop" in blob
    assert "-v pd-data:" not in blob
    assert "/var/lib/docker/volumes/pd-data" not in blob


def test_dockerfile_does_not_copy_tools_tree_into_runtime():
    """The runtime image copies backend/ only — so docs correctly document
    HOST-side invocation of tools/delete_case.py (tool availability finding)."""
    dockerfile = _REPO_ROOT / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    assert "COPY backend/ ./backend/" in text
    assert "COPY tools/" not in text


def test_operations_runbook_exists_and_covers_backup_before_delete():
    runbook = _REPO_ROOT / "docs" / "OPERATIONS.md"
    assert runbook.is_file(), "docs/OPERATIONS.md (new operator runbook) must exist"
    text = runbook.read_text(encoding="utf-8")
    blob = " ".join(text.split())
    assert "python -m tools.delete_case" in blob
    assert "tools.backup_production backup" in blob
    assert "tools.backup_production restore" in blob
    assert "single-writer" in text.lower()
    assert "docker compose" in text.lower()
    assert "-v pd-data:" not in blob
    assert "prod_preflight" in blob


def test_deployment_docs_operations_section_points_to_deletion_path():
    deployment = _REPO_ROOT.joinpath("docs", "DEPLOYMENT.md").read_text(encoding="utf-8")
    blob = " ".join(deployment.split())
    lowered = blob.lower()
    assert "python -m tools.delete_case <case_id> --yes" in blob
    assert "docs/OPERATIONS.md" in blob
    assert "back up the volume/database first" in lowered


def test_deployment_docs_env_split_no_dev_copy_for_production():
    deployment = _REPO_ROOT.joinpath("docs", "DEPLOYMENT.md").read_text(encoding="utf-8")
    blob = " ".join(deployment.split())
    assert ".env.production.example" in blob
    assert "Never copy the DEV example into a production deployment" in blob
    assert "ONE shared judge-wide bucket" in blob or "one shared Caddy-peer" in blob


# --------------------------------------------------------------------------- #
# CLI — python -m tools.prod_preflight
# --------------------------------------------------------------------------- #


def test_prod_preflight_cli_local_smoke_exit_zero():
    result = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight", "--allow-local"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "production preflight: ALL" in result.stdout


def test_prod_preflight_cli_strict_fails_on_localhost_domain():
    result = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    # No .env set here sets a real CADDY_DOMAIN -> localhost default FAILS the
    # ready-to-host verdict. This is the strict preflight contract.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "CADDY_DOMAIN" in result.stdout
    assert "1 FAILING" in result.stdout or "FAILING" in result.stdout
