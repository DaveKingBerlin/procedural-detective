"""Phase 21 P-02 — strict timeout envelope (config wiring + clamping).

Envelope (docs/DEPLOYMENT.md §16, source of truth
``app/core/timeout_envelope.py``):

    provider timeout  <  remaining backend generation deadline
                       <  frontend request timeout
                       <  reverse-proxy upstream timeout

with explicit margins: 0.1s provider classification margin, 60s
deadline-to-frontend, 60s frontend-to-proxy.

This suite pins:

  1. the ordered constants (max provider 300s < frontend 360s < Caddy 420s);
  2. COORDINATION: the constants match the ACTUAL source files
     (frontend/src/api/client.ts REQUEST_TIMEOUT_MS, docker/Caddyfile
     response_header_timeout) — drift between the doc and the code fails here;
  3. the clamp: ``effective_provider_timeout`` always stays BELOW the remaining
     deadline (even when the configured provider timeout EQUALS the deadline,
     e.g. the showcase 300 == 300 -> effective 299.9), so backend terminal
     codes are complete strictly before the frontend timeout;
  4. representative configs (default 60s and showcase 300s) satisfy the
     envelope with zero violations, and a broken deadline is flagged;
  5. the DEFAULT and SHOWCASE configs never let a full provider call (300s)
     outlive the frontend 360s or proxy 420s boundaries.

Deterministic (ManualClock; no sockets — the autouse network block is active).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.core.timeout_envelope import (  # noqa: E402
    CADDY_UPSTREAM_TIMEOUT_SECONDS,
    DEADLINE_TO_FRONTEND_MARGIN_SECONDS,
    FRONTEND_REQUEST_TIMEOUT_SECONDS,
    FRONTEND_TO_PROXY_MARGIN_SECONDS,
    MAX_OLLAMA_TIMEOUT_SECONDS,
    MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS,
    PROVIDER_CLASSIFICATION_MARGIN_SECONDS,
    envelope_violations,
)
from app.generation.budgets import (  # noqa: E402
    PROVIDER_CALL_SAFETY_MARGIN_SECONDS,
    BudgetTracker,
)
from app.generation.clock import ManualClock  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_TS = REPO_ROOT / "frontend" / "src" / "api" / "client.ts"
CADDYFILE = REPO_ROOT / "docker" / "Caddyfile"


def _tracker(clock, *, deadline: int) -> BudgetTracker:
    return BudgetTracker(
        clock,
        deadline_seconds=deadline,
        max_calls=128,
        max_repairs=2,
        max_regenerations=1,
    )


# --------------------------------------------------------------------------- #
# 1. ordered constants with explicit margins
# --------------------------------------------------------------------------- #


def test_envelope_constants_are_strictly_ordered():
    assert MAX_OLLAMA_TIMEOUT_SECONDS < FRONTEND_REQUEST_TIMEOUT_SECONDS < CADDY_UPSTREAM_TIMEOUT_SECONDS
    # The documented margins are EXACT (300 + 60 = 360; 360 + 60 = 420).
    assert (
        MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS + DEADLINE_TO_FRONTEND_MARGIN_SECONDS
        == FRONTEND_REQUEST_TIMEOUT_SECONDS
    )
    assert (
        FRONTEND_REQUEST_TIMEOUT_SECONDS + FRONTEND_TO_PROXY_MARGIN_SECONDS
        == CADDY_UPSTREAM_TIMEOUT_SECONDS
    )
    # The provider classification margin is the SAME constant the budget clamp uses.
    assert PROVIDER_CLASSIFICATION_MARGIN_SECONDS == PROVIDER_CALL_SAFETY_MARGIN_SECONDS


def test_max_provider_timeout_below_deadline_bound_documented_clamp():
    """300 == 300 numerically, so the strict provider < deadline relation is
    GUARANTEED BY THE CLAMP (documented): effective <= remaining - 0.1 always."""
    clock = ManualClock(start_time=0.0)
    tracker = _tracker(clock, deadline=MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS)
    effective = tracker.effective_provider_timeout(MAX_OLLAMA_TIMEOUT_SECONDS)
    assert 0.0 < effective < MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS
    assert (
        effective
        <= MAX_RECOMMENDED_GENERATION_DEADLINE_SECONDS - PROVIDER_CLASSIFICATION_MARGIN_SECONDS
    )


# --------------------------------------------------------------------------- #
# 2. coordination with the ACTUAL frontend/proxy source files
# --------------------------------------------------------------------------- #


def test_frontend_source_coordinates_with_envelope_constant():
    text = CLIENT_TS.read_text(encoding="utf-8")
    match = re.search(r"REQUEST_TIMEOUT_MS\s*=\s*(\d+)", text)
    assert match, "frontend/src/api/client.ts must define REQUEST_TIMEOUT_MS"
    ms = int(match.group(1))
    assert ms // 1000 == FRONTEND_REQUEST_TIMEOUT_SECONDS, (
        f"client.ts REQUEST_TIMEOUT_MS={ms} ms drifts from the documented "
        f"envelope constant {FRONTEND_REQUEST_TIMEOUT_SECONDS}s"
    )


def test_caddy_source_coordinates_with_envelope_constant():
    text = CADDYFILE.read_text(encoding="utf-8")
    match = re.search(r"response_header_timeout\s+(\d+)\s*s", text)
    assert match, "docker/Caddyfile must define response_header_timeout in seconds"
    seconds = int(match.group(1))
    assert seconds == CADDY_UPSTREAM_TIMEOUT_SECONDS, (
        f"Caddyfile response_header_timeout={seconds}s drifts from the "
        f"documented envelope constant {CADDY_UPSTREAM_TIMEOUT_SECONDS}s"
    )


# --------------------------------------------------------------------------- #
# 3. the clamp: provider stays below the SHRINKING remaining deadline
# --------------------------------------------------------------------------- #


def test_provider_clamped_below_remaining_deadline_throughout_the_attempt():
    """At every point before the deadline expires, the effective provider
    timeout is strictly below the remaining deadline (never the aborting party
    ahead of the backend's own deadline classification)."""
    for elapsed, expected_ceiling in ((0, 300), (60, 240), (270, 30), (299, 1)):
        clock = ManualClock(start_time=0.0)
        tracker = _tracker(clock, deadline=300)
        clock.advance(elapsed)  # deterministic time jump
        remaining = tracker.remaining_seconds()
        effective = tracker.effective_provider_timeout(300.0)
        assert 0.0 < effective < remaining + 1e-9
        assert effective <= expected_ceiling - PROVIDER_CLASSIFICATION_MARGIN_SECONDS
    # Once the deadline has passed no meaningful provider call may start (0.0),
    # even though the configured 300s timeout would still be "available".
    clock = ManualClock(start_time=0.0)
    tracker = _tracker(clock, deadline=300)
    clock.advance(300.5)
    assert tracker.effective_provider_timeout(300.0) == 0.0


def test_effective_provider_timeout_never_exceeds_frontend_bound():
    """The worst possible backend behavior (a max-length 299.9s provider call
    at t=0) still finishes strictly before the frontend 360s abort and the
    Caddy 420s edge — backend terminal codes are complete before any outer
    layer aborts when deadline < frontend timeout."""
    assert 300.0 - PROVIDER_CLASSIFICATION_MARGIN_SECONDS < FRONTEND_REQUEST_TIMEOUT_SECONDS


# --------------------------------------------------------------------------- #
# 4. representative configs satisfy the envelope; broken ones are flagged
# --------------------------------------------------------------------------- #


def test_default_settings_satisfy_envelope():
    settings = Settings()  # canonical defaults: deadline 60, provider 60
    assert settings.generation_deadline_seconds == 60
    assert envelope_violations(settings) == []


def test_showcase_settings_satisfy_envelope():
    settings = Settings(
        CASE_GENERATION_DEADLINE_SECONDS=300,
        OLLAMA_TIMEOUT_SECONDS=300,
    )
    assert envelope_violations(settings) == []  # 300 == 300 is the clamp case


def test_deadline_above_envelope_maximum_is_flagged():
    viol = envelope_violations(
        Settings(CASE_GENERATION_DEADLINE_SECONDS=400, OLLAMA_TIMEOUT_SECONDS=180)
    )
    assert any("above the envelope maximum" in v for v in viol)


def test_provider_timeout_above_deadline_is_flagged():
    viol = envelope_violations(
        Settings(CASE_GENERATION_DEADLINE_SECONDS=60, OLLAMA_TIMEOUT_SECONDS=120)
    )
    assert any("above CASE_GENERATION_DEADLINE_SECONDS" in v for v in viol)
    assert any("clamped" in v for v in viol)


def test_fixed_boundaries_cannot_be_torn_down_by_operator_config():
    """Even a maximally generous operator config can never push the effective
    provider timeout past the frontend/proxy bounds (they are code constants)."""
    assert MAX_OLLAMA_TIMEOUT_SECONDS < FRONTEND_REQUEST_TIMEOUT_SECONDS
    assert FRONTEND_REQUEST_TIMEOUT_SECONDS < CADDY_UPSTREAM_TIMEOUT_SECONDS
    # bounds are ALSO the maximum the Settings validator accepts
    assert Settings(ollama_timeout_seconds=300).ollama_timeout_seconds == 300.0
    with pytest.raises(Exception):
        Settings(ollama_timeout_seconds=301)  # le=300 config validation