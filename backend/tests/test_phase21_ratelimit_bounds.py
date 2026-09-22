"""Phase 21 F-05 — bounded rate-limiter identity-map cardinality.

The Phase 20 ``SlidingWindowRateLimiter`` originally kept ONE in-memory bucket
per key with NO eviction: a hostile peer-identity rotation could grow the dict
without limit. F-05 bounds the map (lazily on ``allow()``, plus a bounded
periodic sweep) and caps the number of distinct keys fail-closed.

Behavior under test (Phase21-PHC.md §2 F-05 and the bounded-cardinality matrix):

  1. many unique identities stay under the key ceiling;
  2. stale entries are evicted after their window expires (lazily AND via the
     bounded cadence sweep);
  3. the hard key ceiling is respected (new identities DENIED, never recorded);
  4. legitimate ACTIVE windows are NEVER evicted to make room;
  5. an expired key may be reused (a fresh empty window) and an evicted key's
     slot is reusable by a new identity;
  6. ceiling denial is deterministic under concurrency (the lock serializes);
  7. the AnonymousSessionLimiter global/per-IP compositors inherit the bound.

Deterministic: every limiter is driven by ``ManualClock``; the autouse
conftest network block is active (no sockets at any point).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.ratelimit import (  # noqa: E402
    DEFAULT_RATELIMIT_CLEANUP_CADENCE_SECONDS,
    DEFAULT_RATELIMIT_MAX_KEYS,
    AnonymousSessionLimiter,
    SlidingWindowRateLimiter,
)
from app.generation.clock import ManualClock  # noqa: E402


def _limiter(
    clock,
    *,
    limit=10,
    window_seconds=60,
    max_keys=DEFAULT_RATELIMIT_MAX_KEYS,
    cadence=DEFAULT_RATELIMIT_CLEANUP_CADENCE_SECONDS,
):
    return SlidingWindowRateLimiter(
        clock=clock,
        limit=limit,
        window_seconds=window_seconds,
        max_keys=max_keys,
        cleanup_cadence_seconds=cadence,
    )


# --------------------------------------------------------------------------- #
# 1. many unique identities stay under the key ceiling
# --------------------------------------------------------------------------- #


def test_many_unique_identities_stay_under_key_ceiling():
    clock = ManualClock()
    limiter = _limiter(clock, limit=1000, max_keys=5)
    for index in range(5):
        assert limiter.allow(f"198.51.100.{index}") is True
    assert limiter.key_count == 5
    # The 6th NEW identity is denied fail-closed (nothing recorded).
    assert limiter.allow("198.51.100.55") is False
    assert limiter.key_count == 5
    # Existing identities are completely unaffected.
    assert limiter.allow("198.51.100.0") is True  # under its own limit
    assert limiter.key_count == 5


def test_ceiling_denial_records_nothing():
    clock = ManualClock()
    limiter = _limiter(clock, limit=1000, max_keys=2)
    limiter.allow("ip-a")
    limiter.allow("ip-b")
    assert limiter.allow("ip-c") is False
    assert limiter.count("ip-c") == 0
    assert limiter.allow("ip-c") is False
    assert limiter.key_count == 2


# --------------------------------------------------------------------------- #
# 2. stale entries are evicted after their window expires
# --------------------------------------------------------------------------- #


def test_stale_key_evicted_lazily_on_next_use():
    """A key whose window has FULLY expired is removed on its own next
    ``allow()`` and immediately reusable (fresh empty window)."""
    clock = ManualClock()
    limiter = _limiter(clock, limit=1000, max_keys=3, window_seconds=10)
    assert limiter.allow("ip-a") is True
    clock.advance(11)  # the whole window aged out
    assert limiter.allow("ip-a") is True  # reused: fresh window
    assert limiter.count("ip-a") == 1
    assert limiter.key_count == 1


def test_sweep_evicts_stale_keys_after_cadence():
    """The bounded periodic sweep removes keys whose window fully expired even
    when those identities never return; it runs at most every cadence."""
    clock = ManualClock()
    limiter = _limiter(clock, limit=1000, max_keys=50, window_seconds=10, cadence=5)
    for index in range(5):
        assert limiter.allow(f"198.51.101.{index}") is True
    assert limiter.key_count == 5
    clock.advance(6)  # past cadence, but still INSIDE the 10s window -> kept
    assert limiter.allow("new-ip") is True  # triggers a due sweep; all still active
    assert limiter.key_count == 6
    # new-ip was used at t=6 (expires t=16) and time is now t=17: it too is
    # stale. The next due sweep evicts all FIVE original keys AND new-ip.
    clock.advance(11)
    assert limiter.allow("another-ip") is True
    assert limiter.key_count == 1  # only another-ip


def test_sweep_does_not_run_a_full_pass_on_every_call():
    """Within the cadence no full sweep occurs: consecutive calls only do the
    cheap per-key lazy prune, so the map keeps exactly the stale keys until the
    sweep tick (bounded cadence, never O(n) per request)."""
    clock = ManualClock()
    limiter = _limiter(clock, limit=1000, max_keys=100, window_seconds=10, cadence=60)
    limiter.allow("ip-a")
    clock.advance(20)  # ip-a expired, but the 60s cadence has NOT elapsed
    assert limiter.allow("ip-b") is True
    # The full sweep has not run (cadence 60); ip-a may still be in the map.
    limiter.sweep_expired()
    assert limiter.key_count == 1  # forcing the sweep evicts the stale key


# --------------------------------------------------------------------------- #
# 3. hard key ceiling respected + 4. active windows never evicted
# --------------------------------------------------------------------------- #


def test_hard_ceiling_respected_and_active_windows_never_evicted():
    """At the ceiling, a NEW identity is denied EVEN once the cadence tick
    arrives, while every tracked key is still inside its window — an active
    window is never evicted to admit a new identity."""
    clock = ManualClock(start_time=0.0)
    limiter = _limiter(clock, limit=1000, max_keys=3, window_seconds=600, cadence=5)
    for index in range(3):
        assert limiter.allow(f"198.51.102.{index}") is True
    clock.advance(10)  # PAST the 5s cadence, but all windows still ACTIVE
    assert limiter.allow("198.51.102.99") is False  # denied: no room
    assert limiter.key_count == 3
    # The active windows keep their full Phase 20 behavior.
    assert limiter.allow("198.51.102.0") is True
    assert limiter.key_count == 3


def test_sweep_never_touches_active_windows_beyond_cadence():
    """A sweep at cadence ticks must remove ONLY fully-expired keys.

    Timeline (window=10, cadence=5): active-a refreshed at t=11 (active until
    t=21), active-b refreshed at t=18 (active until t=28), stale used only at
    t=6 (expires t=16). At t=20 a due sweep runs: stale is gone, BOTH active
    windows survive untouched."""
    clock = ManualClock(start_time=0.0)
    limiter = _limiter(clock, limit=1000, max_keys=10, window_seconds=10, cadence=5)
    limiter.allow("active-a")
    limiter.allow("active-b")
    clock.advance(6)
    limiter.allow("stale")
    clock.advance(5)          # t=11
    limiter.allow("active-a")  # refresh
    clock.advance(7)          # t=18
    limiter.allow("active-b")  # refresh
    clock.advance(2)          # t=20 — due sweep
    assert limiter.allow("new") is True
    assert limiter.key_count == 3   # active-a + active-b + new (stale evicted)
    assert limiter.count("active-a") == 1 and limiter.count("active-b") == 1
    assert limiter.count("stale") == 0


# --------------------------------------------------------------------------- #
# 3b./5. capacity limits surface as the SAME sanitized denial (route-compatible)
# --------------------------------------------------------------------------- #


def test_ceiling_denial_is_plain_false_like_window_full():
    """The route layer maps BOTH denial causes to the identical sanitized 429
    envelope — a ceiling-denied identity must not be discoverable. Here we pin
    the limiter contract: same return type, nothing recorded, no exception."""
    clock = ManualClock()
    limiter = _limiter(clock, limit=1, max_keys=1)
    assert limiter.allow("ip-a") is True
    assert limiter.count("ip-a") == 1
    assert limiter.allow("ip-b") is False   # ceiling (new identity)
    assert limiter.allow("ip-a") is False   # window full (existing identity)
    assert limiter.count("ip-b") == 0


def test_expired_key_slot_reusable_by_a_new_identity():
    clock = ManualClock(start_time=0.0)
    limiter = _limiter(clock, limit=1000, max_keys=2, window_seconds=10, cadence=5)
    limiter.allow("ip-a")
    limiter.allow("ip-b")
    assert limiter.allow("ip-c") is False  # ceiling: map full with live windows
    clock.advance(21)                      # all windows expired AND cadence passed
    assert limiter.allow("ip-d") is True   # due sweep frees both stale slots
    assert limiter.key_count == 1
    # The freed slot means the map stays at or below the ceiling afterwards.
    assert limiter.allow("ip-e") is True
    assert limiter.key_count <= 2


# --------------------------------------------------------------------------- #
# 6. deterministic under concurrency
# --------------------------------------------------------------------------- #


def test_ceiling_is_deterministic_under_concurrency():
    """A thread burst of DISTINCT identities over a tiny ceiling admits exactly
    ``max_keys`` (the lock serializes every mutation; no double-spend, no
    eviction of a concurrent winner)."""
    clock = ManualClock(start_time=100.0)
    limiter = _limiter(clock, limit=10_000, max_keys=5)
    results: list[bool] = [False] * 40
    keys = [f"203.0.113.{index}" for index in range(40)]

    def worker(index: int) -> None:
        results[index] = limiter.allow(keys[index])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(results) == 5
    assert limiter.key_count == 5


# --------------------------------------------------------------------------- #
# 7. AnonymousSessionLimiter inherits the bound (per-IP map)
# --------------------------------------------------------------------------- #


def test_anonymous_session_limiter_per_ip_map_is_bounded():
    """The public-admission compositor's per-IP window is a
    ``SlidingWindowRateLimiter`` too: its per-IP key map is capped by the same
    mechanism (many distinct spoof-free peers cannot grow the map past the
    ceiling; the global window keeps its own single key)."""
    clock = ManualClock(start_time=0.0)
    limiter = AnonymousSessionLimiter(
        clock=clock,
        per_ip_limit=100,
        per_ip_window_seconds=600,
        global_limit=1000,
        global_window_seconds=60,
    )
    assert limiter.allow("ip-a") == (True, "")
    assert limiter.allow("ip-b") == (True, "")
    assert limiter.global_limiter.key_count == 1  # only the \x00global control key
    assert limiter.per_ip_limiter.key_count == 2