"""Per-attempt resource budgets (REQUIREMENTS 32.5/32.6/32.7, Phase4 C).

One ``BudgetTracker`` belongs to exactly one ``generationAttemptId``: the
initial generation, retries, repairs and regenerations all draw on the same
deadline, model-call count, repair-pass count and regeneration count. A new
attempt always gets a fresh tracker, so recovery counters can never leak
across attempts (REQUIREMENTS 32.5: "All recovery counters belong to one
generationAttemptId").

Phase 19 Fix C adds HIERARCHICAL provider-call accounting on top of the
single global ceiling:

- ``calls``            — the GLOBAL ceiling (``MAX_LLM_CALLS_PER_GENERATION``).
  Every real provider call counts toward it (hard, unforgiving).
- ``core_calls``       — core-bucket calls (case_truth / evidence /
  world_requirements / global repair / regeneration), capped by
  ``max_core_calls`` (``MAX_CORE_LLM_CALLS_PER_GENERATION``).
- ``asset_calls``      — procedural ASSET_SPEC/ASSET_SPEC_REPAIR calls, each
  attributed to ONE semantic object id (``asset_calls_by_object``); per-asset
  cap ``max_asset_calls`` (``MAX_LLM_CALLS_PER_PROCEDURAL_ASSET``).
- ``procedural_assets`` — distinct semantic objects that entered the
  procedural-asset path, capped by ``max_procedural_assets``
  (``MAX_PROCEDURAL_ASSETS_PER_GENERATION``).
- ``failed_assets``    — distinct semantic objects whose procedural
  generation failed, capped by ``max_failed_assets``
  (``MAX_FAILED_ASSETS_PER_GENERATION``).

Every counter is MONOTONIC: consumption reservations are one-way (a failed
provider result never returns a reservation; a new attempt gets a fresh
tracker). Deterministic LOCAL repairs (environmentHint canonicalization,
semantic-id normalization, evidence projection, catalog alias resolution,
safe deterministic fallback selection) never touch these counters — only
actual model calls do.
"""

from __future__ import annotations

from typing import Any

from app.generation.clock import Clock

PROVIDER_CALL_SAFETY_MARGIN_SECONDS = 0.1

# Canonical CORE bucket token (``None`` is accepted synonymously so legacy
# callers that never pass a bucket keep their exact behavior).
#
# Phase 19B ADV-216: the CORE bucket is a dedicated NON-STRING sentinel, never
# the literal string ``"core"``. A semantic asset id is always a string, so a
# procedural object whose requested_name is literally ``"core"`` can never
# alias the CORE bucket — it is charged to its OWN per-asset bucket exactly
# like every other object (per-asset ceiling + asset-call attribution apply).
class _CoreBucket:
    """Unique sentinel for the CORE budget bucket (never a semantic asset id)."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<CORE_BUCKET>"

    def __copy__(self) -> "_CoreBucket":  # pragma: no cover - defensive
        return self

    def __deepcopy__(self, _memo: Any) -> "_CoreBucket":  # pragma: no cover - defensive
        return self


CORE_BUCKET = _CoreBucket()


def _non_negative_int(value: Any, name: str) -> int | None:
    """Validate an optional budget ceiling int (None is allowed)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int or None")
    if value < 0:
        raise ValueError(f"{name} must be >= 0")
    return int(value)


class BudgetTracker:
    """Deadline + hierarchical model-call + repair/regeneration budgets."""

    def __init__(
        self,
        clock: Clock,
        *,
        deadline_seconds: int,
        max_calls: int,
        max_repairs: int,
        max_regenerations: int,
        max_core_calls: int | None = None,
        max_asset_calls: int | None = None,
        max_procedural_assets: int | None = None,
        max_failed_assets: int | None = None,
        started_at: float | None = None,
    ) -> None:
        if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, int):
            raise TypeError("deadline_seconds must be an int")
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be > 0")
        if isinstance(max_calls, bool) or not isinstance(max_calls, int):
            raise TypeError("max_calls must be an int")
        if max_calls <= 0:
            raise ValueError("max_calls must be > 0")
        if isinstance(max_repairs, bool) or not isinstance(max_repairs, int):
            raise TypeError("max_repairs must be an int")
        if max_repairs < 0:
            raise ValueError("max_repairs must be >= 0")
        if isinstance(max_regenerations, bool) or not isinstance(max_regenerations, int):
            raise TypeError("max_regenerations must be an int")
        if max_regenerations < 0:
            raise ValueError("max_regenerations must be >= 0")
        self._clock = clock
        self._deadline_seconds = int(deadline_seconds)
        self._max_calls = int(max_calls)
        self._max_core_calls = _non_negative_int(max_core_calls, "max_core_calls")
        self._max_asset_calls = _non_negative_int(max_asset_calls, "max_asset_calls")
        self._max_procedural_assets = _non_negative_int(
            max_procedural_assets, "max_procedural_assets"
        )
        self._max_failed_assets = _non_negative_int(
            max_failed_assets, "max_failed_assets"
        )
        self._max_repairs = int(max_repairs)
        self._max_regenerations = int(max_regenerations)
        self._started_at = (
            float(started_at) if started_at is not None else float(clock.now())
        )
        # Public counters (readable for tests/inspection).
        self.calls = 0
        self.core_calls = 0
        self.asset_calls = 0
        self.asset_calls_by_object: dict[str, int] = {}
        self.procedural_assets: set[str] = set()
        self.failed_assets: set[str] = set()
        self.repair_passes = 0
        self.regenerations = 0

    # -- accessors -----------------------------------------------------------

    @property
    def started_at(self) -> float:
        return self._started_at

    @property
    def deadline_seconds(self) -> int:
        return self._deadline_seconds

    def remaining_global_calls(self) -> int:
        """Calls still available under the GLOBAL ceiling."""
        return max(0, self._max_calls - self.calls)

    def remaining_core_calls(self) -> int | None:
        """Core calls still available (None = no core ceiling configured)."""
        if self._max_core_calls is None:
            return None
        return max(0, self._max_core_calls - self.core_calls)

    def remaining_asset_calls(self, object_id: str) -> int | None:
        """Asset calls still available for ONE semantic object (None = none)."""
        if self._max_asset_calls is None:
            return None
        return max(0, self._max_asset_calls - self.asset_call_count(object_id))

    @property
    def procedural_asset_count(self) -> int:
        return len(self.procedural_assets)

    @property
    def failed_asset_count(self) -> int:
        return len(self.failed_assets)

    @property
    def max_procedural_assets(self) -> int | None:
        return self._max_procedural_assets

    @property
    def max_failed_assets(self) -> int | None:
        return self._max_failed_assets

    @property
    def max_asset_calls(self) -> int | None:
        return self._max_asset_calls

    @property
    def max_core_calls(self) -> int | None:
        return self._max_core_calls

    def asset_call_count(self, object_id: str) -> int:
        """Asset-bucket calls attributed to ONE semantic object id."""
        return self.asset_calls_by_object.get(object_id, 0)

    # -- budget checks -------------------------------------------------------

    def deadline_passed(self) -> bool:
        """True when ``clock.now() >= started_at + deadline_seconds``."""
        return self._clock.now() >= self._started_at + self._deadline_seconds

    def remaining_seconds(self) -> float:
        """Seconds of overall deadline still available (0.0 when expired).

        DEV/observability helper (Phase 17E diagnosis); never modifies state.
        """
        return max(0.0, self._started_at + self._deadline_seconds - float(self._clock.now()))

    def effective_provider_timeout(
        self,
        configured_seconds: float,
        *,
        safety_margin_seconds: float = PROVIDER_CALL_SAFETY_MARGIN_SECONDS,
    ) -> float:
        """Return a call timeout bounded by the remaining attempt deadline.

        A small reserved margin leaves the controller enough time to classify a
        timeout, release admission and persist the terminal state.  ``0.0`` is
        the explicit signal that no meaningful provider call may start.
        """

        configured = max(0.0, float(configured_seconds))
        remaining = self.remaining_seconds()
        margin = max(0.0, float(safety_margin_seconds))
        if remaining <= margin:
            return 0.0
        return min(configured, remaining - margin)

    def consume_call(self, bucket: str | None = None) -> bool:
        """Reserve one REAL model call in a budget bucket.

        ``bucket`` is ``None`` or ``CORE_BUCKET`` (the CORE bucket: case/
        evidence/world stage calls plus global repair/regeneration calls) or a
        non-empty semantic object id (the ASSET:<objectId> bucket: procedural
        ASSET_SPEC / ASSET_SPEC_REPAIR / geometry calls for that object).
        ``CORE_BUCKET`` is a NON-STRING sentinel (ADV-216): ANY string — even
        the literal ``"core"`` — is always interpreted as a semantic asset id,
        so an asset id can never alias the CORE bucket.

        Enforces (simultaneously): the GLOBAL ceiling (hard), the CORE ceiling
        for core-bucket calls (hard when configured) and the per-asset ceiling
        for asset-bucket calls (hard when configured). Returns False (with NO
        reservation) when ANY applicable ceiling is exhausted OR the deadline
        has passed — the caller must treat False as terminal for the attempt
        (or a failed asset when only its sub-budget is spent). Deterministic
        local repairs NEVER call this method.
        """
        if self.deadline_passed():
            return False
        if self.calls >= self._max_calls:
            return False
        if bucket is None or bucket == CORE_BUCKET:
            if self._max_core_calls is not None and self.core_calls >= self._max_core_calls:
                return False
            self.core_calls += 1
        else:
            if not isinstance(bucket, str) or not bucket:
                raise ValueError(
                    "consume_call bucket must be None, CORE_BUCKET or a non-empty "
                    "semantic object id"
                )
            if self._max_asset_calls is not None and self.asset_call_count(bucket) >= self._max_asset_calls:
                return False
            self.asset_calls += 1
            self.asset_calls_by_object[bucket] = self.asset_call_count(bucket) + 1
        self.calls += 1
        return True

    def consume_core_call(self) -> bool:
        """New-style alias for ``consume_call(bucket=CORE_BUCKET)``."""
        return self.consume_call(bucket=CORE_BUCKET)

    def consume_asset_call(self, object_id: str) -> bool:
        """New-style alias for ``consume_call(bucket=<object_id>)``."""
        return self.consume_call(bucket=object_id)

    def exhausted_reason(self, bucket: str | None = None) -> str:
        """The deterministic narrowest exhaustion reason, or ``""``.

        Never raises and never mutates. The reason text carries the
        ``"model call budget"`` prefix so legacy ``infer_failure_code`` still
        classifies it, while the more specific phrases (``"core model call
        budget"`` / ``"asset model call budget"``) let the lifecycle emit the
        narrower Phase 19 failure codes.
        """
        if self.calls >= self._max_calls:
            return "model call budget exhausted"
        if bucket is None or bucket == CORE_BUCKET:
            if self._max_core_calls is not None and self.core_calls >= self._max_core_calls:
                return "core model call budget exhausted"
            return ""
        if (
            self._max_asset_calls is not None
            and isinstance(bucket, str)
            and self.asset_call_count(bucket) >= self._max_asset_calls
        ):
            return f"asset model call budget exhausted for {bucket}"
        return ""

    def consume_repair_pass(self) -> bool:
        """Reserve one repair pass; False when exhausted (with no reservation)."""
        if self.repair_passes >= self._max_repairs:
            return False
        self.repair_passes += 1
        return True

    def consume_regeneration(self) -> bool:
        """Reserve one full regeneration; False when exhausted (no reservation)."""
        if self.regenerations >= self._max_regenerations:
            return False
        self.regenerations += 1
        return True

    def consume_procedural_asset(self, object_id: str) -> bool:
        """Reserve procedural-asset entry for ONE semantic object.

        Each DISTINCT semantic object that enters the procedural ASSET_SPEC
        path counts once. Returns False (with no reservation) when the
        procedural-asset ceiling (``MAX_PROCEDURAL_ASSETS_PER_GENERATION``) is
        already reached. Never consumes a model call — this is a per-object
        count guard, not a provider call.

        ADV-239 (documented decision): the ceiling is PER GENERATION ATTEMPT —
        repair/regeneration passes of ONE attempt share the SAME tracker, so a
        decoration-rich world whose unknown objects repeat across passes can
        reach the 20-distinct ceiling. REQUIREMENTS defines the ceiling per
        generation attempt and ADR-001 documents it as the "Richness bound:
        distinct procedural assets per generation" — this is CORRECT budget
        semantics, NOT a decorative-drop defect. Within ONE composition the
        per-composition provider budget caps decorations (the composer drops
        over-bound decoration with player-safe notes and provides the
        required iterate deterministically), so a decorative-only over-ceiling
        never invalidates REQUIRED objects and never fails a case that would
        otherwise publish in a single pass.
        """
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("procedural asset id must be a non-empty string")
        if object_id in self.procedural_assets:
            return True
        if self._max_procedural_assets is not None and len(self.procedural_assets) >= self._max_procedural_assets:
            return False
        self.procedural_assets.add(object_id)
        return True

    def mark_failed_asset(self, object_id: str) -> bool:
        """Record ONE procedural asset that could not be safely produced.

        Returns False when the failed-asset ceiling
        (``MAX_FAILED_ASSETS_PER_GENERATION``) would be exceeded by recording
        this NEW failure — the caller then knows the attempt must fail closed
        (``MAX_FAILED_ASSETS_EXCEEDED``). Re-recording the same object id is a
        no-op returning True. Never consumes a model call.
        """
        if not isinstance(object_id, str) or not object_id:
            raise ValueError("failed asset id must be a non-empty string")
        if object_id in self.failed_assets:
            return True
        if (
            self._max_failed_assets is not None
            and len(self.failed_assets) >= self._max_failed_assets
        ):
            return False
        self.failed_assets.add(object_id)
        return True

    def snapshot(self) -> dict[str, Any]:
        """Sanitized monotonic accounting snapshot (never raw content)."""
        return {
            "globalCallCount": self.calls,
            "coreCallCount": self.core_calls,
            "assetCallCount": self.asset_calls,
            "remainingGlobalCalls": self.remaining_global_calls(),
            "remainingCoreCalls": self.remaining_core_calls(),
            "proceduralAssetCount": self.procedural_asset_count,
            "failedAssetCount": self.failed_asset_count,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"BudgetTracker(calls={self.calls}/{self._max_calls}, "
            f"core={self.core_calls}/{self._max_core_calls}, "
            f"assets={self.asset_calls}, "
            f"repairs={self.repair_passes}/{self._max_repairs}, "
            f"regenerations={self.regenerations}/{self._max_regenerations})"
        )


def is_budget_tracker(value: Any) -> bool:  # pragma: no cover - isinstance helper
    return isinstance(value, BudgetTracker)