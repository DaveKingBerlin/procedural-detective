"""Phase 21C deployment-integrity regressions for AUD-21B-02."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent
for _path in (str(_BACKEND_DIR), str(_REPO_ROOT), str(_REPO_ROOT / "tools")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tools import release_check  # noqa: E402


def _rendered_config(
    *,
    environment: str = "production",
    dev_trace: str = "false",
    trust_proxy: str = "true",
    domain: str = "detective.procedural-game.dev",
) -> dict[str, object]:
    return {
        "services": {
            "procedural-detective": {
                "environment": {
                    "ENVIRONMENT": environment,
                    "PD_DEV_TRACE": dev_trace,
                    "TRUST_PROXY": trust_proxy,
                    "CASE_GENERATION_DEADLINE_SECONDS": "300",
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
            },
        },
        "volumes": {"pd-data": {"name": "project_pd-data"}},
    }


class _ShellAwareComposeRunner:
    """A deterministic stand-in for Compose's already-rendered output."""

    def __init__(self, *, result: dict[str, object] | None = None) -> None:
        self.result = result
        self.command: list[str] | None = None
        self.kwargs: dict[str, object] = {}

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.command = command
        self.kwargs = kwargs
        rendered = self.result or _rendered_config(
            environment=os.environ.get("ENVIRONMENT", "production"),
            dev_trace=os.environ.get("PD_DEV_TRACE", "false"),
            trust_proxy=os.environ.get("TRUST_PROXY", "true"),
        )
        return subprocess.CompletedProcess(command, 0, json.dumps(rendered), "")


def _check(runner: _ShellAwareComposeRunner, **kwargs: object) -> list[release_check.Finding]:
    return release_check.check_prod_effective_config(
        _REPO_ROOT,
        compose_runner=runner,
        client_ts_path=_REPO_ROOT / "frontend" / "src" / "api" / "client.ts",
        caddyfile_path=_REPO_ROOT / "docker" / "Caddyfile",
        frontend_dir=_REPO_ROOT / "__phase21c_missing_dist__",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ENVIRONMENT", "development"),
        ("PD_DEV_TRACE", "true"),
        ("TRUST_PROXY", "false"),
    ],
)
def test_shell_override_in_render_fails_closed(monkeypatch, key: str, value: str):
    monkeypatch.setenv(key, value)
    findings = _check(_ShellAwareComposeRunner())
    failures = [finding.message for finding in findings if finding.severity == "fail"]
    assert any(key in message for message in failures), failures


def test_valid_shell_values_equal_to_defaults_pass(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")
    runner = _ShellAwareComposeRunner()

    findings = _check(runner)

    assert not [finding for finding in findings if finding.severity == "fail"]
    assert runner.command is not None
    assert runner.command[:4] == [
        "docker", "compose", "--project-directory", str(_REPO_ROOT.resolve())
    ]
    assert runner.command[-5:] == [
        "-f",
        str((_REPO_ROOT / "docker-compose.prod.yml").resolve()),
        "config",
        "--format",
        "json",
    ]
    assert "--env-file" not in runner.command
    assert "env" not in runner.kwargs, "current process environment must be inherited"


def test_explicit_startup_env_file_is_forwarded_to_compose():
    runner = _ShellAwareComposeRunner()
    env_file = _REPO_ROOT / ".env.production.example"

    findings = _check(runner, compose_env_file=env_file)

    assert not [finding for finding in findings if finding.severity == "fail"]
    assert runner.command is not None
    position = runner.command.index("--env-file")
    assert runner.command[position + 1] == str(env_file.resolve())


@pytest.mark.parametrize(
    "completed",
    [
        subprocess.CompletedProcess(["docker"], 2, "", "secret-looking stderr"),
        subprocess.CompletedProcess(["docker"], 0, "not-json", ""),
    ],
)
def test_compose_command_and_parse_errors_are_sanitized_and_fail_closed(completed):
    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return completed

    findings = release_check.check_prod_effective_config(
        _REPO_ROOT,
        compose_runner=runner,
    )
    assert [finding.severity for finding in findings] == ["fail"]
    message = findings[0].message
    assert "fail closed" in message
    assert "secret-looking" not in message
    assert "not-json" not in message


def test_alternate_ingress_requires_proxy_trust_off_but_cannot_certify():
    runner = _ShellAwareComposeRunner(result=_rendered_config(trust_proxy="false"))
    findings = _check(runner, ingress_profile="alternate")

    assert any(
        finding.severity == "fail" and "separate verification" in finding.message
        for finding in findings
    )


@pytest.mark.parametrize(
    "domain",
    [
        "detective.example.com",
        "example.invalid",
        "detective.example",
        "detective.test",
        "127.0.0.1",
        "localhost.",
        "lab-machine.local",
        "not-a-domain",
        "bad_host.public.dev",
    ],
)
def test_strict_domain_gate_rejects_reserved_ip_local_and_malformed(domain: str):
    runner = _ShellAwareComposeRunner(result=_rendered_config(domain=domain))

    findings = _check(runner, allow_local=False)

    failures = [finding.message for finding in findings if finding.severity == "fail"]
    assert any("CADDY_DOMAIN" in message for message in failures), failures
    assert all(domain not in message for message in failures), "domain value must stay sanitized"


def test_plausible_non_reserved_domain_passes_syntax_gate_without_dns_claim():
    runner = _ShellAwareComposeRunner(
        result=_rendered_config(domain="detective.procedural-game.dev")
    )

    findings = _check(runner, allow_local=False)

    assert not [finding for finding in findings if finding.severity == "fail"]
    assert all("DNS" not in finding.message or "not" in finding.message for finding in findings)


def test_allow_local_only_downgrades_explicit_local_hostname():
    local = _check(
        _ShellAwareComposeRunner(result=_rendered_config(domain="localhost.")),
        allow_local=True,
    )
    reserved = _check(
        _ShellAwareComposeRunner(result=_rendered_config(domain="detective.example.com")),
        allow_local=True,
    )

    assert not [finding for finding in local if finding.severity == "fail"]
    assert any(finding.severity == "report" for finding in local)
    assert any(finding.severity == "fail" for finding in reserved)


def _require_compose_cli() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is unavailable")
    probe = subprocess.run(
        ["docker", "compose", "version"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("Docker Compose plugin is unavailable")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ENVIRONMENT", "development"),
        ("PD_DEV_TRACE", "true"),
        ("TRUST_PROXY", "false"),
    ],
)
def test_exact_compose_preflight_rejects_shell_override(
    monkeypatch, key: str, value: str
):
    """The operator CLI and its child Compose process share the hostile shell."""
    _require_compose_cli()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setenv(key, value)

    completed = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight", "--allow-local"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert key in completed.stdout


def test_compose_cli_render_observes_valid_shell_value(monkeypatch):
    """A valid shell value is rendered and accepted, not rejected by presence."""
    _require_compose_cli()

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")
    rendered = release_check._render_prod_compose_config(
        _REPO_ROOT,
        (_REPO_ROOT / "docker-compose.prod.yml").resolve(),
    )
    backend = release_check._rendered_service(rendered, "procedural-detective")
    assert backend is not None
    assert release_check._rendered_environment(backend)["ENVIRONMENT"] == "production"

    completed = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight", "--allow-local"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_exact_strict_cli_rejects_example_domain_shell_override(monkeypatch):
    _require_compose_cli()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setenv("CADDY_DOMAIN", "detective.example.com")

    completed = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "CADDY_DOMAIN" in completed.stdout
    assert "detective.example.com" not in completed.stdout + completed.stderr


def test_exact_strict_cli_accepts_plausible_domain_syntax(monkeypatch):
    _require_compose_cli()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setenv("CADDY_DOMAIN", "detective.procedural-game.dev")

    completed = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_exact_alternate_profile_cannot_certify_shipped_compose(monkeypatch):
    _require_compose_cli()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "false")
    monkeypatch.setenv("CADDY_DOMAIN", "detective.procedural-game.dev")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.prod_preflight",
            "--ingress-profile",
            "alternate",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "alternate ingress" in completed.stdout.lower()
