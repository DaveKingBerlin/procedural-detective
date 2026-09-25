"""P-02 (Phase 21) — strict timeout envelope for a generation request.

Deployment contract (docs/DEPLOYMENT.md § timeout envelope; the ENVELOPE the
frontend, proxy and backend deadlines must satisfy for every code path that
serves a generation):

    provider timeout  <  remaining backend generation deadline
                       <  frontend request timeout
                       <  reverse-proxy upstream timeout

with EXPLICIT margins at every hop. This module is the single documented
source of the coordinated frontend/proxy constants and of the margin policy,
so a Python config-wiring test can pin the relationship of every layer.

Envelope (choose from clamping math, not from the phase example):

- ``CASE_GENERATION_DEADLINE_SECONDS``  = 300 max (showcase; default 60).
  The **provider call timeout is clamped to ``remaining_deadline - 0.1s``**
  (``BudgetTracker.effective_provider_timeout``, margin
  ``PROVIDER_CALL_SAFETY_MARGIN_SECONDS``) so the strictly-smaller operator
  ``provider < deadline`` ALWAYS holds mechanically — even when the configured
  ``OLLAMA_TIMEOUT_SECONDS`` (bounded 5..300 by Settings) equals the deadline
  (the showcase 300 == 300), the effective call timeout is ``299.9``.
- FRONTEND request timeout = **360s** (``frontend/src/api/client.ts
  REQUEST_TIMEOUT_MS``): 60s over the max 300s deadline — the browser never
  aborts a request the backend still legitimately allows, and a full max-length
  provider call starting at t=0 still lands before the browser boundary.
- CADDY upstream timeout = **420s** (``docker/Caddyfile response_header_timeout``):
  60s over the frontend — the TLS edge never aborts before the browser does,
  and it covers the frontend's 360s plus the browser's own scheduling slack.

Margins (documented policy):

- ``PROVIDER_CLASSIFICATION_MARGIN_SECONDS`` (0.1s) — reserved between the
  effective provider timeout and the remaining deadline so the controller has
  time to classify a timeout and persist the terminal state.
- ``DEADLINE_TO_FRONTEND_MARGIN_SECONDS`` (60s) — worst-case backend deadline
  (300s) + 60s <= frontend 360s.
- ``FRONTEND_TO_PROXY_MARGIN_SECONDS`` (60s) — frontend 360s + 60s <= proxy 420s.

The DEFAULT (60s deadline) and the recommended showcase (300s deadline) both
satisfy the envelope. ``envelope_violations`` flags a configuration that does
NOT (e.g. a deadline above the documented max, or an operator provider
timeout configured ABOVE the deadline so the clamp silently shortens every
call).
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Coordinated constants — single documented source. These MUST match:
#   frontend/src/api/client.ts  -> REQUEST_TIMEOUT_MS
#   docker/Caddyfile             -> response_header_timeout
# (the config-wiring test test_phase21_timeout_envelope.py parses BOTH files
#  and fails on drift).
# --------------------------------------------------------------------------- #

# Frontend fetch abort after this many SECONDS (client.ts, ms value / 1000).
FRONTEND_REQUEST_TIMEOUT_SECONDS = 360

# Reverse-proxy (Caddy) upstream response-header timeout in SECONDS.
CADDY_UPSTREAM_TIMEOUT_SECONDS = 420

# Margin: maximum backend deadline + margin must be <= FRONTEND (300 + 60).
DEADLINE_TO_FRONTEND_MARGIN_SECONDS = 60.0

# Margin: frontend + margin must be <= CADDY (360 + 60).
FRONTEND_TO_PROXY_MARGIN_SECONDS = 60.0

# Margin reserved between the effective provider timeout and the remaining
# deadline (must stay == PROVIDER_CALL_SAFETY_MARGIN_SECONDS in budgets.py).
PROVIDER_CLASSIFICATION_MARGIN_SECONDS = 0.1

# The maximum backend generation deadline the envelope supports (showcase).
# Operator deadlines ABOVE this must raise the frontend/proxy constants with
# the same margins — the browser would otherwise abort a request the backend
# still legitimately allows.
MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS = 300

# The operator-configurable provider timeout bound
# (Settings ``OLLAMA_TIMEOUT_SECONDS`` doc bound: 5..300). The strict
# ``provider < deadline`` relation is guaranteed by the CLAMP, not by this
# bound (a 300 == 300 showcase config is fine; effective timeout is 299.9).
MAX_OLLAMA_TIMEOUT_SECONDS = 300.0

# --------------------------------------------------------------------------- #
# ADV-250 — the BRIDGE_JOB_DEADLINE_SECONDS ceiling (Phase 22 BYO-Ollama).
# The per-job deadline the server puts on a bridge job frame (``timeoutMs``)
# is clamped to min(remaining generation deadline, BRIDGE_JOB_DEADLINE_SECONDS
# configured by the operator). The CONFIGURED value is additionally bounded by
# BOTH the maximum generation deadline the app allows (the envelope showcase
# bound) AND a hard 1800s cap, so a hostile/oversized configured value can
# never reach a job frame even if the transport-level clamp were bypassed.
BRIDGE_JOB_DEADLINE_HARD_CAP_SECONDS = 1800.0
BRIDGE_JOB_DEADLINE_MAX_SECONDS: float = min(
    MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS,
    BRIDGE_JOB_DEADLINE_HARD_CAP_SECONDS,
)


def provider_timeout_violation(configured_seconds: float) -> bool:
    """True when an operator configured the provider timeout ABOVE the deadline
    bound (the clamp would silently shorten every call)."""
    return float(configured_seconds) > MAX_OLLAMA_TIMEOUT_SECONDS


def envelope_violations(settings: Any) -> list[str]:
    """Sanitized list of P-02 envelope problems for one Settings object.

    Returns [] for the DEFAULT (60s) and the recommended showcase (300s)
    configurations. Never raises and never returns internal details — the
    strings are operator-facing and intentionally free of secrets/paths.
    """
    violations: list[str] = []
    deadline = int(settings.generation_deadline_seconds)
    provider = float(settings.ollama_timeout_seconds)

    if provider_timeout_violation(provider):
        violations.append(
            "OLLAMA_TIMEOUT_SECONDS above the documented bound (300s) — the "
            "per-call provider timeout is clamped to the remaining deadline "
            "anyway; lower it to the documented 5..300 range."
        )
    if provider > deadline:
        violations.append(
            "OLLAMA_TIMEOUT_SECONDS above CASE_GENERATION_DEADLINE_SECONDS — "
            "every provider call is clamped to the remaining deadline minus the "
            "0.1s classification margin, so the configured timeout is never used "
            "in full; set OLLAMA_TIMEOUT_SECONDS <= the deadline."
        )
    if deadline > MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS:
        violations.append(
            "CASE_GENERATION_DEADLINE_SECONDS above the envelope maximum (300s): "
            "the frontend request timeout (360s) must remain strictly larger "
            "with margin; reduce the deadline or raise the frontend/proxy "
            "constants together."
        )
    if deadline + DEADLINE_TO_FRONTEND_MARGIN_SECONDS > FRONTEND_REQUEST_TIMEOUT_SECONDS:
        violations.append(
            "timeout envelope broken: CASE_GENERATION_DEADLINE_SECONDS plus the "
            "60s margin exceeds the frontend request timeout (360s); the browser "
            "can abort a request the backend still allows."
        )
    if (
        FRONTEND_REQUEST_TIMEOUT_SECONDS + FRONTEND_TO_PROXY_MARGIN_SECONDS
        > CADDY_UPSTREAM_TIMEOUT_SECONDS
    ):
        violations.append(
            "timeout envelope broken: the frontend request timeout (360s) plus "
            "the 60s margin exceeds the reverse-proxy timeout (420s); the proxy "
            "can abort before the browser."
        )
    if MAX_OLLAMA_TIMEOUT_SECONDS >= FRONTEND_REQUEST_TIMEOUT_SECONDS:
        violations.append(
            "timeout envelope broken: a max-length provider call (300s) must stay "
            "strictly under the frontend request timeout (360s)."
        )
    return violations


__all__ = [
    "FRONTEND_REQUEST_TIMEOUT_SECONDS",
    "CADDY_UPSTREAM_TIMEOUT_SECONDS",
    "DEADLINE_TO_FRONTEND_MARGIN_SECONDS",
    "FRONTEND_TO_PROXY_MARGIN_SECONDS",
    "PROVIDER_CLASSIFICATION_MARGIN_SECONDS",
    "MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS",
    "MAX_OLLAMA_TIMEOUT_SECONDS",
    "BRIDGE_JOB_DEADLINE_HARD_CAP_SECONDS",
    "BRIDGE_JOB_DEADLINE_MAX_SECONDS",
    "envelope_violations",
    "provider_timeout_violation",
]