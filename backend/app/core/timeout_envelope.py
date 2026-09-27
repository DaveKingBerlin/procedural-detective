"""Canonical timeout-envelope contract for generation requests.

Runtime admission and rendered-deployment preflight both call the same pure,
value-based validator in this module.  No helper here reads environment state.

The supported server-local real-Ollama profile is sized as::

    configured provider timeout <= 300s backend generation deadline
    backend deadline + 60s <= 360s frontend request timeout
    frontend timeout + 60s <= 420s reverse-proxy timeout

``BudgetTracker`` separately clamps each effective provider call below the
shrinking remaining backend deadline by the 0.1s classification margin.  An
operator may therefore configure provider timeout == total deadline without
violating the effective strict ordering.
"""

from __future__ import annotations

import math
from typing import Any

FRONTEND_REQUEST_TIMEOUT_SECONDS = 360
CADDY_UPSTREAM_TIMEOUT_SECONDS = 420
DEADLINE_TO_FRONTEND_MARGIN_SECONDS = 60.0
FRONTEND_TO_PROXY_MARGIN_SECONDS = 60.0
PROVIDER_CLASSIFICATION_MARGIN_SECONDS = 0.1
MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS = 300
MIN_OLLAMA_TIMEOUT_SECONDS = 5.0
MAX_OLLAMA_TIMEOUT_SECONDS = 300.0

# One supported server-local real-Ollama profile. Fake/demo deliberately keeps
# the quick 60s Settings default and is not forced into this profile.
SUPPORTED_OLLAMA_GENERATION_DEADLINE_SECONDS = 300.0

# Phase 22 BYO-Ollama bridge job ceiling.
BRIDGE_JOB_DEADLINE_HARD_CAP_SECONDS = 1800.0
BRIDGE_JOB_DEADLINE_MAX_SECONDS: float = min(
    MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS,
    BRIDGE_JOB_DEADLINE_HARD_CAP_SECONDS,
)

_GENERATION_PROVIDERS = frozenset({"fake", "live", "ollama", "remote_client"})


def provider_timeout_violation(configured_seconds: float) -> bool:
    """Whether the configured provider timeout exceeds its documented bound."""

    return float(configured_seconds) > MAX_OLLAMA_TIMEOUT_SECONDS


def _positive_finite_number(value: object) -> float | None:
    """Return a positive finite float or ``None`` without ever raising."""

    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _positive_integer(value: object) -> int | None:
    """Match Settings' positive integer semantics without coercing fractions."""

    parsed = _positive_finite_number(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


def timeout_envelope_violations(
    *,
    generation_provider: str,
    generation_deadline_seconds: object,
    provider_timeout_seconds: object,
    frontend_timeout_seconds: object = FRONTEND_REQUEST_TIMEOUT_SECONDS,
    proxy_timeout_seconds: object = CADDY_UPSTREAM_TIMEOUT_SECONDS,
) -> list[str]:
    """Return sanitized violations for explicit rendered/runtime values.

    The helper never raises and fails closed for unknown providers, malformed,
    non-finite, boolean and non-positive values.  A provider timeout may equal
    the total deadline because the runtime clamp reserves the 0.1s
    classification margin from the effective per-call timeout.
    """

    violations: list[str] = []
    # Settings uses a Literal: case and surrounding whitespace are invalid,
    # so rendered/preflight values must use the exact same token semantics.
    provider_name = generation_provider if isinstance(generation_provider, str) else ""
    deadline = _positive_integer(generation_deadline_seconds)
    provider_timeout = _positive_finite_number(provider_timeout_seconds)
    frontend_timeout = _positive_finite_number(frontend_timeout_seconds)
    proxy_timeout = _positive_finite_number(proxy_timeout_seconds)

    if provider_name not in _GENERATION_PROVIDERS:
        violations.append(
            "GENERATION_PROVIDER must be one of fake, live, ollama, remote_client."
        )
    if deadline is None:
        violations.append(
            "CASE_GENERATION_DEADLINE_SECONDS must be a positive finite number."
        )
    if provider_timeout is None:
        violations.append("OLLAMA_TIMEOUT_SECONDS must be a positive finite number.")
    if frontend_timeout is None:
        violations.append("frontend generation timeout must be a positive finite number.")
    if proxy_timeout is None:
        violations.append("reverse-proxy timeout must be a positive finite number.")
    if violations:
        return violations

    assert deadline is not None
    assert provider_timeout is not None
    assert frontend_timeout is not None
    assert proxy_timeout is not None

    if provider_timeout < MIN_OLLAMA_TIMEOUT_SECONDS:
        violations.append(
            "OLLAMA_TIMEOUT_SECONDS must be at least 5 seconds."
        )
    if provider_timeout > MAX_OLLAMA_TIMEOUT_SECONDS:
        violations.append(
            "OLLAMA_TIMEOUT_SECONDS above the documented bound (300s)."
        )
    if provider_name == "ollama" and provider_timeout > deadline:
        violations.append(
            "OLLAMA_TIMEOUT_SECONDS must be <= "
            "CASE_GENERATION_DEADLINE_SECONDS; otherwise every call is "
            "silently shortened by the remaining-deadline clamp."
        )
    if (
        provider_name == "ollama"
        and deadline != SUPPORTED_OLLAMA_GENERATION_DEADLINE_SECONDS
    ):
        violations.append(
            "GENERATION_PROVIDER=ollama requires the supported real-generation "
            "profile CASE_GENERATION_DEADLINE_SECONDS=300."
        )
    if deadline > MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS:
        violations.append(
            "CASE_GENERATION_DEADLINE_SECONDS above the envelope maximum (300s)."
        )
    if deadline + DEADLINE_TO_FRONTEND_MARGIN_SECONDS > frontend_timeout:
        violations.append(
            "timeout envelope broken: CASE_GENERATION_DEADLINE_SECONDS plus "
            "the 60s margin exceeds the frontend generation timeout."
        )
    if frontend_timeout + FRONTEND_TO_PROXY_MARGIN_SECONDS > proxy_timeout:
        violations.append(
            "timeout envelope broken: frontend generation timeout plus the "
            "60s margin exceeds the reverse-proxy timeout."
        )
    return violations


def envelope_violations(settings: Any) -> list[str]:
    """Compatibility wrapper for one Settings-like object."""

    return timeout_envelope_violations(
        generation_provider=getattr(settings, "generation_provider", ""),
        generation_deadline_seconds=getattr(
            settings, "generation_deadline_seconds", None
        ),
        provider_timeout_seconds=getattr(settings, "ollama_timeout_seconds", None),
    )


def enforce_runtime_timeout_envelope(settings: Any) -> None:
    """Reject an unsupported profile before the application starts serving."""

    violations = envelope_violations(settings)
    if violations:
        raise RuntimeError(
            "unsupported generation timeout configuration: " + " ".join(violations)
        )


__all__ = [
    "FRONTEND_REQUEST_TIMEOUT_SECONDS",
    "CADDY_UPSTREAM_TIMEOUT_SECONDS",
    "DEADLINE_TO_FRONTEND_MARGIN_SECONDS",
    "FRONTEND_TO_PROXY_MARGIN_SECONDS",
    "PROVIDER_CLASSIFICATION_MARGIN_SECONDS",
    "MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS",
    "MIN_OLLAMA_TIMEOUT_SECONDS",
    "MAX_OLLAMA_TIMEOUT_SECONDS",
    "SUPPORTED_OLLAMA_GENERATION_DEADLINE_SECONDS",
    "BRIDGE_JOB_DEADLINE_HARD_CAP_SECONDS",
    "BRIDGE_JOB_DEADLINE_MAX_SECONDS",
    "timeout_envelope_violations",
    "envelope_violations",
    "enforce_runtime_timeout_envelope",
    "provider_timeout_violation",
]
