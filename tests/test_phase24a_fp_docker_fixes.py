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
  5. F-3 LAN-overlay guard (Phase 22–24 bridge fix): ``check_lan_overlay_config``
     derives the internal-TLS mode from the RENDERED model (the ``caddy``
     service mounts ``docker/Caddyfile.internal`` at ``/etc/caddy/Caddyfile``)
     and FAILS when that LAN overlay is combined with a public-looking
     ``CADDY_DOMAIN`` (the public name would be served from Caddy's INTERNAL CA
     — browsers/bridge reject it). The guard is ADDITIVE: a single-label LAN
     host still fails ``_caddy_domain_problem`` in both modes, the canonical
     prod compose + public domain stays ready-to-host unchanged, and explicit
     local-smoke hostnames (localhost / .local / .localhost) are not blocked.

No Docker daemon, no network, no live stack: compose ``config`` renders use the
local Compose engine when present (the same CLI the release gate runs) or an
injected canned runner; the ignore-semantics check reuses git's own ignore
engine against a copy of ``.dockerignore``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

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


# --------------------------------------------------------------------------- #
# Phase 24 §9 — Caddy local-LAN TLS mechanism (Caddyfile.internal overlay)
# --------------------------------------------------------------------------- #

CADDY_CANONICAL = REPO_ROOT / "docker" / "Caddyfile"
CADDY_INTERNAL = REPO_ROOT / "docker" / "Caddyfile.internal"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
COMPOSE_LAN = REPO_ROOT / "docker-compose.lan.yml"
_COMPOSE_LAN_MARKER = "docker/Caddyfile.internal:/etc/caddy/Caddyfile:ro"
_COMPOSE_PROD_MARKER = "./docker/Caddyfile:/etc/caddy/Caddyfile:ro"


def _compose_cli_available() -> bool:
    import shutil

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


def test_caddy_canonical_has_no_tls_internal_internal_variant_has_it() -> None:
    """Phase 24 §9 — the canonical public Caddyfile must NEVER carry
    `tls internal` (public deployments keep Caddy's normal public ACME), while
    the `.internal` LAN variant DOES."""
    canonical = CADDY_CANONICAL.read_text(encoding="utf-8")
    internal = CADDY_INTERNAL.read_text(encoding="utf-8")
    assert "tls internal" not in canonical
    assert "tls internal" in internal


def test_caddy_internal_is_byte_copy_plus_tls_internal() -> None:
    """The `.internal` variant is a byte-copy of the canonical file with `tls
    internal` added exactly once inside the site block — so a drift in the
    canonical (timeouts, ports, headers) must be mirrored to the LAN variant or
    this assertion fails."""
    canonical = CADDY_CANONICAL.read_bytes()
    internal = CADDY_INTERNAL.read_bytes()
    assert internal.count(b"tls internal") == 1
    # Internal minus the tls internal line == the canonical file bytes.
    without = internal.replace(b"\ttls internal\n\n", b"", 1)
    without = without.replace(b"\ttls internal\n", b"", 1)
    assert without == canonical, "Caddyfile.internal drifted from the canonical file"


def test_caddy_prod_compose_never_references_internal_and_lan_override_does() -> None:
    """The production compose does NOT reference the internal file; the LAN
    override DOES reference it; and the canonical prod compose still mounts the
    PUBLIC Caddyfile at the container Caddy path."""
    prod = COMPOSE_PROD.read_text(encoding="utf-8")
    lan = COMPOSE_LAN.read_text(encoding="utf-8")
    assert "Caddyfile.internal" not in prod
    assert _COMPOSE_LAN_MARKER in lan
    assert _COMPOSE_PROD_MARKER in prod  # public Caddyfile stays the prod mount
    # The override only specifies the caddy mount (no other service keys).
    assert "procedural-detective" not in lan.split("services:")[1].split("caddy:")[0]


def test_caddy_lan_overlay_real_render_swaps_mount() -> None:
    """Compose render (no daemon, `config` only): `-f docker-compose.prod.yml
    -f docker-compose.lan.yml` renders the caddy service with Caddyfile.internal
    mounted at /etc/caddy/Caddyfile, while the prod-only render keeps the
    canonical public Caddyfile — everything else (backend ports/networks) is
    inherited."""
    if not _compose_cli_available():
        pytest.skip("Docker Compose CLI is unavailable")
    empty = Path(__file__).parent / ".tmp-empty-caddy.env"
    empty.write_text("", encoding="utf-8")
    try:
        rendered_lan = release_check._render_compose_config(
            REPO_ROOT,
            [COMPOSE_PROD.resolve(), COMPOSE_LAN.resolve()],
            env_file=empty,
        )
        rendered_prod = release_check._render_compose_config(
            REPO_ROOT, [COMPOSE_PROD.resolve()], env_file=empty
        )
    finally:
        empty.unlink(missing_ok=True)

    def _caddy_mounts(doc: dict) -> list[tuple[str, str]]:
        caddy = (doc.get("services") or {}).get("caddy") or {}
        out: list[tuple[str, str]] = []
        for mount in caddy.get("volumes") or []:
            if isinstance(mount, dict):
                out.append((str(mount.get("source") or ""), str(mount.get("target") or "")))
        return out

    lan_mounts = _caddy_mounts(rendered_lan)
    assert any("Caddyfile.internal" in src and tgt == "/etc/caddy/Caddyfile"
               for src, tgt in lan_mounts), lan_mounts
    prod_mounts = _caddy_mounts(rendered_prod)
    assert any("Caddyfile" in src and "internal" not in src and tgt == "/etc/caddy/Caddyfile"
               for src, tgt in prod_mounts), prod_mounts
    # Both renders keep the same backend service shape (inherited, not a
    # parallel deployment system).
    for doc in (rendered_lan, rendered_prod):
        backend = (doc.get("services") or {}).get("procedural-detective") or {}
        assert isinstance(backend.get("environment"), dict)
        assert not backend.get("ports"), "backend must stay private (no host ports)"


# --------------------------------------------------------------------------- #
# Phase 24 §12 — rendered compose regression checks (remote_client profile)
# --------------------------------------------------------------------------- #

_PD_EMPTY_ENV = ""


def _write_env(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "profile.env"
    path.write_text(content, encoding="utf-8")
    return path


def test_section12_base_and_ci_empty_env_render_no_ollama_vars() -> None:
    """`docker compose -f docker-compose.yml --env-file <empty> config` and the
    CI overlay chain render deterministic clean defaults (fake / bridge off) and
    NEVER render OLLAMA_BASE_URL / OLLAMA_MODEL / a published 11434."""
    if not _compose_cli_available():
        pytest.skip("Docker Compose CLI is unavailable")
    empty = Path(__file__).parent / ".tmp-empty-s12.env"
    empty.write_text(_PD_EMPTY_ENV, encoding="utf-8")
    try:
        base = release_check._render_compose_config(
            REPO_ROOT, [REPO_ROOT / "docker-compose.yml"], env_file=empty
        )
        ci = release_check._render_compose_config(
            REPO_ROOT,
            [REPO_ROOT / "docker-compose.yml", REPO_ROOT / "docker-compose.ci.yml"],
            env_file=empty,
        )
    finally:
        empty.unlink(missing_ok=True)

    for label, doc in (("base", base), ("ci", ci)):
        backend = release_check._rendered_service(doc, "procedural-detective")
        assert backend is not None
        env = release_check._rendered_environment(backend)
        assert env.get("GENERATION_PROVIDER") == "fake", (label, env)
        assert env.get("ENABLE_BRIDGE") == "false", (label, env)
        raw = json.dumps(doc)
        assert "OLLAMA_BASE_URL" not in raw, label
        assert "OLLAMA_MODEL" not in raw, label
        assert "11434" not in raw, label


def test_section12_prod_remote_client_profile_never_given_ollama_vars(
    tmp_path: Path,
) -> None:
    """§12 INPUT profile that intentionally sets remote_client+true: the
    rendered PRODUCTION compose resolves GENERATION_PROVIDER=remote_client while
    OLLAMA_BASE_URL / OLLAMA_MODEL never appear, Caddy publishes 80/443, the
    backend has no host :8000, and no service publishes :11434."""
    if not _compose_cli_available():
        pytest.skip("Docker Compose CLI is unavailable")
    profile = _write_env(
        tmp_path,
        "GENERATION_PROVIDER=remote_client\nENABLE_BRIDGE=true\n",
    )
    rendered = release_check._render_prod_compose_config(
        REPO_ROOT, (REPO_ROOT / "docker-compose.prod.yml").resolve(),
        env_file=profile,
    )
    backend = release_check._rendered_service(rendered, "procedural-detective")
    caddy = release_check._rendered_service(rendered, "caddy")
    assert backend is not None and caddy is not None
    env = release_check._rendered_environment(backend)
    assert env.get("GENERATION_PROVIDER") == "remote_client", env
    # The RUNNING backend for a remote_client deployment is never given Ollama
    # provider vars (Level-0 constraint: no OLLAMA_BASE_URL/OLLAMA_MODEL).
    raw = json.dumps(rendered)
    assert "OLLAMA_BASE_URL" not in raw
    assert "OLLAMA_MODEL" not in raw
    # Caddy publishes 80/443; backend never publishes host :8000.
    caddy_ports = release_check._rendered_port_targets(caddy)
    assert "80" in caddy_ports and "443" in caddy_ports, caddy_ports
    assert release_check._rendered_port_targets(backend) == [], (
        release_check._rendered_port_targets(backend)
    )
    exposes = backend.get("expose")
    assert isinstance(exposes, list) and "8000" in {str(item) for item in exposes}
    # No service publishes :11434.
    services = rendered.get("services")
    assert isinstance(services, dict)
    for service in services.values():
        if isinstance(service, dict):
            assert "11434" not in release_check._rendered_port_targets(service)


def test_section12_prod_source_never_references_ollama_vars() -> None:
    """Source-level pin (no daemon): `docker-compose.prod.yml` and the LAN
    overlay never interpolate OLLAMA_BASE_URL / OLLAMA_MODEL, so a remote_client
    (BYO-Ollama bridge) deployment can never carry Ollama env vars."""
    prod = COMPOSE_PROD.read_text(encoding="utf-8")
    lan = COMPOSE_LAN.read_text(encoding="utf-8")
    for text in (prod, lan):
        for token in ("${OLLAMA_BASE_URL", "${OLLAMA_MODEL", "OLLAMA_BASE_URL:", "OLLAMA_MODEL:"):
            assert token not in text, token


# --------------------------------------------------------------------------- #
# Phase 24 F-3 — LAN overlay (`tls internal`) must never serve a PUBLIC name
# --------------------------------------------------------------------------- #

# The certified chain an operator who deployed the LAN overlay passes to the
# preflight (`--compose-overlay docker-compose.lan.yml`, mirroring --env-file).
_LAN_CHAIN = (REPO_ROOT / "docker-compose.prod.yml", COMPOSE_LAN)


def _lan_prod_body(domain: str, *, lan: bool = True) -> dict:
    """A canned RENDERED prod-config model; ``lan`` controls whether the caddy
    service mounts the INTERNAL Caddyfile (LAN overlay applied -> ``tls
    internal``) or the canonical PUBLIC Caddyfile (default production edge)."""
    return {
        "services": {
            "procedural-detective": {
                "environment": {
                    "ENVIRONMENT": "production",
                    "PD_DEV_TRACE": "false",
                    "TRUST_PROXY": "true",
                    "GENERATION_PROVIDER": "ollama",
                    "CASE_GENERATION_DEADLINE_SECONDS": "300",
                    "OLLAMA_TIMEOUT_SECONDS": "180",
                },
                "expose": ["8000"],
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "10m", "max-file": "5"},
                },
                "volumes": [
                    {"type": "volume", "source": "pd-data", "target": "/data"}
                ],
            },
            "caddy": {
                "environment": {
                    "CADDY_DOMAIN": domain,
                    "CADDY_EMAIL": "operator@example.com",
                },
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "10m", "max-file": "5"},
                },
                "ports": [
                    {"target": 80, "published": "80"},
                    {"target": 443, "published": "443"},
                ],
                "volumes": [
                    {
                        "type": "bind",
                        "source": (
                            "./docker/Caddyfile.internal"
                            if lan
                            else "./docker/Caddyfile"
                        ),
                        "target": "/etc/caddy/Caddyfile",
                        "read_only": True,
                    }
                ],
            },
        },
        "volumes": {"pd-data": {"name": "project_pd-data"}},
    }


def _lan_body_runner(body: dict):
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _render(body)

    return runner


def _check_prod(body: dict, *, allow_local: bool = False) -> list[release_check.Finding]:
    return release_check.check_prod_effective_config(
        REPO_ROOT,
        allow_local=allow_local,
        compose_runner=_lan_body_runner(body),
        frontend_dir=REPO_ROOT / "__phase24_f3_missing_dist__",
    )


def test_lan_overlay_detection_from_rendered_model() -> None:
    """The guard derives the internal-TLS mode from the RENDERED model (the
    caddy volume mount), NOT from a source-file string match."""
    assert release_check._lan_overlay_rendered(_lan_prod_body("localhost", lan=True))
    assert not release_check._lan_overlay_rendered(_lan_prod_body("localhost", lan=False))
    # No caddy service in the model -> not the LAN mode (fail-closed skip later).
    assert not release_check._lan_overlay_rendered({"services": {}})


def test_lan_overlay_public_fqdn_fails_guard() -> None:
    """F-3 requirement (1) — the new-gate regression: a public FQDN passes the
    existing ``_caddy_domain_problem`` syntax gate (no CADDY_DOMAIN finding), so
    BEFORE this fix NOTHING caught that the LAN overlay would serve it from the
    INTERNAL CA. The new guard FAILS the combination when the certified chain is
    prod + the LAN overlay."""
    domain = "detective.procedural-game.dev"
    assert release_check._caddy_domain_problem(domain) == (None, False)
    findings = release_check.check_lan_overlay_config(
        REPO_ROOT,
        compose_paths=_LAN_CHAIN,
        compose_runner=_lan_body_runner(_lan_prod_body(domain, lan=True)),
    )
    fails = [f for f in findings if f.severity == "fail"]
    assert any(
        f.check == "lan-overlay-config" and "INTERNAL CA" in f.message
        for f in fails
    ), [f.render() for f in findings]
    # The public FQDN in the CANONICAL (non-LAN) render still has no finding.
    canonical = _check_prod(_lan_prod_body(domain, lan=False))
    assert not [f for f in canonical if f.severity == "fail"], [
        f.render() for f in canonical
    ]


def test_lan_overlay_public_example_domain_still_reports_fail() -> None:
    """F-3 requirement (1) with the finding's example name: LAN overlay +
    ``detective.example.com`` reports a FAIL — the existing ready-to-host gate
    rejects the RESERVED example domain (and the LAN guard adds no second
    failure for a name that can never be certified)."""
    body = _lan_prod_body("detective.example.com", lan=True)
    lan_findings = release_check.check_lan_overlay_config(
        REPO_ROOT,
        compose_paths=_LAN_CHAIN,
        compose_runner=_lan_body_runner(body),
    )
    assert not [f for f in lan_findings if f.severity == "fail"], [
        f.render() for f in lan_findings
    ]
    prod_findings = _check_prod(body)
    fails = [f for f in prod_findings if f.severity == "fail"]
    assert any("CADDY_DOMAIN" in f.message for f in fails), [
        f.render() for f in prod_findings
    ]
    # The domain VALUE stays sanitized everywhere.
    for f in lan_findings + prod_findings:
        assert "detective.example.com" not in f.message


def test_lan_overlay_single_label_still_fails_in_plain_and_allow_local() -> None:
    """F-3 requirement (2) — the guard is ADDITIVE: ``Enshrouded-Server``
    (single label, no dot) still FAILS the existing ``_caddy_domain_problem``
    gate in BOTH plain and ``--allow-local`` modes even when the LAN overlay
    render is in scope; the new guard itself reports ok (it only owns the
    public-domain combination)."""
    problem, explicit_local = release_check._caddy_domain_problem("Enshrouded-Server")
    assert problem is not None
    assert explicit_local is False  # single-label is NOT an explicit local smoke
    body = _lan_prod_body("Enshrouded-Server", lan=True)
    for allow_local in (False, True):
        findings = _check_prod(body, allow_local=allow_local)
        fails = [f for f in findings if f.severity == "fail"]
        assert any("CADDY_DOMAIN" in f.message for f in fails), (
            allow_local, [f.render() for f in findings]
        )
        assert all("Enshrouded-Server" not in f.message for f in findings), (
            "domain value must stay sanitized"
        )
    lan_findings = release_check.check_lan_overlay_config(
        REPO_ROOT,
        compose_paths=_LAN_CHAIN,
        compose_runner=_lan_body_runner(body),
    )
    assert not [f for f in lan_findings if f.severity == "fail"], [
        f.render() for f in lan_findings
    ]


def test_canonical_prod_compose_public_domain_no_new_finding() -> None:
    """F-3 requirement (3) — canonical prod compose (NO LAN overlay) + public
    domain stays ready-to-host unchanged: the effective-config gate passes and
    the LAN guard reports ok (overlay not in effect)."""
    body = _lan_prod_body("detective.procedural-game.dev", lan=False)
    prod_findings = _check_prod(body)
    assert not [f for f in prod_findings if f.severity == "fail"], [
        f.render() for f in prod_findings
    ]
    lan_findings = release_check.check_lan_overlay_config(
        REPO_ROOT, compose_runner=_lan_body_runner(body)
    )
    assert not [f for f in lan_findings if f.severity == "fail"], [
        f.render() for f in lan_findings
    ]
    assert any(
        f.check == "lan-overlay-config" and f.severity == "ok" for f in lan_findings
    )


@pytest.mark.parametrize(
    "domain",
    ["localhost", "host.localhost", "lab.local", "gaming-pc.local"],
)
def test_lan_overlay_local_hostname_not_blocked(domain: str) -> None:
    """F-3 requirement (4) — LAN overlay + explicit local-smoke hostname is the
    documented local-TLS use (internal CA serving a LAN/local name); the new
    guard reports ok and never blocks it."""
    findings = release_check.check_lan_overlay_config(
        REPO_ROOT,
        compose_paths=_LAN_CHAIN,
        compose_runner=_lan_body_runner(_lan_prod_body(domain, lan=True)),
    )
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    assert any(
        f.check == "lan-overlay-config" and f.severity == "ok" for f in findings
    )


def test_lan_overlay_guard_skip_when_overlay_absent(tmp_path: Path) -> None:
    """Deployment-artifact behavior (same pattern as the prod/CI gates): no
    docker-compose.lan.yml in the tree -> skip, never fail."""
    findings = release_check.check_lan_overlay_config(
        tmp_path, compose_runner=_lan_body_runner(_lan_prod_body("localhost"))
    )
    assert any(
        f.check == "lan-overlay-config" and f.severity == "skip" for f in findings
    )


def test_lan_overlay_guard_fails_closed_on_render_error() -> None:
    """A broken overlay chain (render error) is fail-closed, exactly like the
    other compose gates."""

    def error_runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 2, "", "compose exploded")

    findings = release_check.check_lan_overlay_config(
        REPO_ROOT, compose_runner=error_runner
    )
    fails = [f for f in findings if f.severity == "fail"]
    assert any(
        f.check == "lan-overlay-config" and "fail closed" in f.message
        for f in fails
    ), [f.render() for f in findings]


def test_run_all_includes_lan_overlay_gate() -> None:
    """run_all (the release_check CLI gate) wires the F-3 guard in and stays
    green on the shipped tree (default CADDY_DOMAIN=localhost with an empty/env
    -free shell -> LAN render reports ok)."""
    findings = release_check.run_all(
        REPO_ROOT, allow_hosted=True, frontend_dir=None,
    )
    assert any(f.check == "lan-overlay-config" for f in findings)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


def test_lan_overlay_real_render_guard_ok_on_default_localhost() -> None:
    """Real Compose render (client-side ``config`` only, no daemon): the repo's
    own LAN chain with an EMPTY env file defaults CADDY_DOMAIN=localhost, so
    the new guard reports ok and the shipped tree stays gate-green even when an
    operator certifies the LAN overlay chain."""
    if not _compose_cli_available():
        pytest.skip("Docker Compose CLI is unavailable")
    empty = Path(__file__).parent / ".tmp-empty-f3.env"
    empty.write_text("", encoding="utf-8")
    try:
        findings = release_check.check_lan_overlay_config(
            REPO_ROOT, compose_paths=_LAN_CHAIN, compose_env_file=empty
        )
    finally:
        empty.unlink(missing_ok=True)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    assert any(
        f.check == "lan-overlay-config" and f.severity == "ok" for f in findings
    )


def _require_compose() -> None:
    if not _compose_cli_available():
        pytest.skip("Docker Compose CLI is unavailable")


def test_prod_preflight_cli_lan_overlay_public_domain_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-3 at the CLI: an operator who APPLIES the LAN overlay (`--compose-overlay
    docker-compose.lan.yml`, the same -f they pass to `docker compose up`) and
    sets a public CADDY_DOMAIN gets a FAILING preflight — the internal CA would
    serve the public name and browsers/bridge reject it."""
    _require_compose()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setenv("CADDY_DOMAIN", "detective.procedural-game.dev")

    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight", "--compose-overlay", "docker-compose.lan.yml"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "lan-overlay-config" in completed.stdout
    assert "INTERNAL CA" in completed.stdout
    # The domain VALUE stays sanitized in the output.
    assert "detective.procedural-game.dev" not in completed.stdout + completed.stderr


def test_prod_preflight_cli_lan_overlay_local_smoke_pass_and_plain_still_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LAN overlay guard never blocks the documented local-TLS use: with
    CADDY_DOMAIN=localhost, `--allow-local --compose-overlay docker-compose.lan.yml`
    passes (all findings ok); the STRICT plain run still FAILS on the explicit
    local-smoke name exactly as before (the existing gate stays intact)."""
    _require_compose()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setenv("CADDY_DOMAIN", "localhost")

    import sys

    allow_local = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight", "--allow-local",
         "--compose-overlay", "docker-compose.lan.yml"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert allow_local.returncode == 0, allow_local.stdout + allow_local.stderr
    assert "lan-overlay-config" in allow_local.stdout

    strict = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight",
         "--compose-overlay", "docker-compose.lan.yml"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert strict.returncode == 1, strict.stdout + strict.stderr
    assert "CADDY_DOMAIN" in strict.stdout  # the pre-existing local-smoke gate