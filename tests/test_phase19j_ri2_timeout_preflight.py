"""Phase 19J-RI2 production timeout-envelope regressions.

These tests exercise the release/preflight consumer of the canonical runtime
validator. They intentionally validate Compose's rendered model rather than
reimplementing Compose interpolation in Python.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import release_check


REPO_ROOT = Path(__file__).resolve().parents[1]


class _TextPath:
    """Minimal read-only path seam for timeout-constant parsing."""

    def __init__(self, text: str) -> None:
        self._text = text

    def is_file(self) -> bool:
        return True

    def read_text(self, **_kwargs: object) -> str:
        return self._text


def _rendered_config(
    *,
    provider: object = "ollama",
    deadline: object = "300",
    provider_timeout: object = "180",
) -> dict[str, object]:
    return {
        "services": {
            "procedural-detective": {
                "environment": {
                    "ENVIRONMENT": "production",
                    "PD_DEV_TRACE": "false",
                    "TRUST_PROXY": "true",
                    "GENERATION_PROVIDER": provider,
                    "CASE_GENERATION_DEADLINE_SECONDS": deadline,
                    "OLLAMA_TIMEOUT_SECONDS": provider_timeout,
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
                "environment": {"CADDY_DOMAIN": "localhost"},
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
        "volumes": {"pd-data": {"name": "test_pd-data"}},
    }


def _check(
    rendered: dict[str, object],
    *,
    client_ts_path: Path | None = None,
    caddyfile_path: Path | None = None,
) -> list[release_check.Finding]:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(rendered), "")

    return release_check.check_prod_effective_config(
        REPO_ROOT,
        allow_local=True,
        compose_runner=runner,
        client_ts_path=client_ts_path,
        caddyfile_path=caddyfile_path,
        frontend_dir=REPO_ROOT / "__phase19j_ri2_missing_dist__",
    )


@pytest.mark.parametrize(
    ("provider", "deadline", "provider_timeout"),
    [
        ("ollama", "300", "180"),
        ("fake", "60", "60"),
    ],
)
def test_supported_rendered_timeout_profiles_pass(
    provider: str, deadline: str, provider_timeout: str
) -> None:
    findings = _check(
        _rendered_config(
            provider=provider,
            deadline=deadline,
            provider_timeout=provider_timeout,
        )
    )
    assert not [item for item in findings if item.severity == "fail"], [
        item.render() for item in findings
    ]


@pytest.mark.parametrize(
    ("provider", "deadline", "provider_timeout"),
    [
        ("ollama", "60", "60"),
        ("ollama", "300", "301"),
        ("ollama", "invalid", "180"),
        ("ollama", "0", "180"),
        ("ollama", "300", "invalid"),
        ("ollama", "300", "0"),
        ("ollama", "300", "1"),
        ("fake", "60.5", "60"),
        ("OLLAMA", "300", "180"),
        (" ollama ", "300", "180"),
        ("unknown", "300", "180"),
    ],
)
def test_unsupported_rendered_timeout_profiles_fail_closed(
    provider: object, deadline: object, provider_timeout: object
) -> None:
    findings = _check(
        _rendered_config(
            provider=provider,
            deadline=deadline,
            provider_timeout=provider_timeout,
        )
    )
    failures = [item.message for item in findings if item.severity == "fail"]
    assert any("timeout envelope" in message for message in failures), failures


def test_frontend_timeout_not_above_deadline_fails_closed() -> None:
    findings = _check(
        _rendered_config(),
        client_ts_path=_TextPath("const REQUEST_TIMEOUT_MS = 300000;\n"),  # type: ignore[arg-type]
        caddyfile_path=_TextPath("response_header_timeout 420s\n"),  # type: ignore[arg-type]
    )
    assert any(
        item.severity == "fail" and "timeout envelope" in item.message
        for item in findings
    )


def test_proxy_timeout_not_above_frontend_fails_closed() -> None:
    findings = _check(
        _rendered_config(),
        client_ts_path=_TextPath("const REQUEST_TIMEOUT_MS = 360000;\n"),  # type: ignore[arg-type]
        caddyfile_path=_TextPath("response_header_timeout 360s\n"),  # type: ignore[arg-type]
    )
    assert any(
        item.severity == "fail" and "timeout envelope" in item.message
        for item in findings
    )


def _require_compose() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI unavailable")
    probe = subprocess.run(
        ["docker", "compose", "version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("Docker Compose plugin unavailable")


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("CASE_GENERATION_DEADLINE_SECONDS", "60", "60"),
        ("OLLAMA_TIMEOUT_SECONDS", "45", "45"),
    ],
)
def test_shell_timeout_override_changes_actual_compose_render(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str, expected: str
) -> None:
    _require_compose()
    monkeypatch.setenv("GENERATION_PROVIDER", "ollama")
    monkeypatch.setenv("CASE_GENERATION_DEADLINE_SECONDS", "300")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv(key, value)

    rendered = release_check._render_prod_compose_config(
        REPO_ROOT,
        (REPO_ROOT / "docker-compose.prod.yml").resolve(),
    )
    backend = release_check._rendered_service(rendered, "procedural-detective")
    assert backend is not None
    assert release_check._rendered_environment(backend)[key] == expected


@pytest.mark.parametrize(
    ("deadline", "provider_timeout"),
    [
        ("60", "60"),
        ("300", "0"),
        ("300", "1"),
        ("300", "not-a-number"),
    ],
)
def test_invalid_shell_timeout_override_fails_prod_preflight_cli(
    monkeypatch: pytest.MonkeyPatch,
    deadline: str,
    provider_timeout: str,
) -> None:
    _require_compose()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setenv("CADDY_DOMAIN", "localhost")
    monkeypatch.setenv("GENERATION_PROVIDER", "ollama")
    monkeypatch.setenv("CASE_GENERATION_DEADLINE_SECONDS", deadline)
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", provider_timeout)

    completed = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight", "--allow-local"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "timeout envelope" in completed.stdout


def test_fractional_deadline_shell_override_fails_prod_preflight_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_compose()
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setenv("CADDY_DOMAIN", "localhost")
    monkeypatch.setenv("GENERATION_PROVIDER", "fake")
    monkeypatch.setenv("CASE_GENERATION_DEADLINE_SECONDS", "60.5")
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS", "180")

    completed = subprocess.run(
        [sys.executable, "-m", "tools.prod_preflight", "--allow-local"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "timeout envelope" in completed.stdout


def test_examples_and_compose_declare_supported_real_profile() -> None:
    development = release_check._read_env_file(REPO_ROOT, ".env.example")
    production = release_check._read_env_file(REPO_ROOT, ".env.production.example")
    compose_text = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

    for profile in (development, production):
        assert profile["CASE_GENERATION_DEADLINE_SECONDS"] == "300"
        assert profile["OLLAMA_TIMEOUT_SECONDS"] == "180"
    assert "${CASE_GENERATION_DEADLINE_SECONDS:-300}" in compose_text
    assert "${OLLAMA_TIMEOUT_SECONDS:-180}" in compose_text


@pytest.mark.parametrize(
    "missing_key",
    ["CASE_GENERATION_DEADLINE_SECONDS", "OLLAMA_TIMEOUT_SECONDS"],
)
def test_env_profile_gate_rejects_missing_timeout_key(
    monkeypatch: pytest.MonkeyPatch, missing_key: str
) -> None:
    original = release_check._read_env_file

    def without_required_key(repo_root: Path, name: str) -> dict[str, str]:
        profile = original(repo_root, name)
        if name == ".env.production.example":
            profile.pop(missing_key, None)
        return profile

    monkeypatch.setattr(release_check, "_read_env_file", without_required_key)
    findings = release_check.check_prod_env_profile(REPO_ROOT)
    assert any(
        item.severity == "fail" and missing_key in item.message
        for item in findings
    )
