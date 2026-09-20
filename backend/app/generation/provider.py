"""Narrow provider abstraction (Phase 4 D; contract fixed for lifecycle).

The pipeline code only ever sees ``GenerateRequest`` / ``ProviderResult`` and
the ``GenerationStage`` enum. No provider-specific types (OpenAI/Anthropic/
OpenRouter/httpx...) leak through this boundary.

``completion sinks`` are the ONLY way a deferred ("pending") provider result
becomes a completed one: the caller (or a test) eventually calls
``resolve(pending_id, result)`` on the provider, which routes the result into
the attached ``CompletionSink``. There is no threading and no sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class GenerationStage(Enum):
    """The explicit generation stages (Phase4 E).

    ``ASSET_SPEC`` / ``ASSET_SPEC_REPAIR`` are INTERNAL stage-like request
    names used ONLY by the Ollama stage driver (Phase 16_2) to key an
    AssetSpec round-trip through the SAME ``GenerateRequest`` / ``ProviderResult``
    boundary. They never appear in the controller's ``STAGE_ORDER`` and the
    deterministic parser never consumes them — the driver parses their output
    with the Phase 13 AssetSpec parser. Documented here so ``GenerateRequest``
    accepts them; the pipeline/controller treat them only inside the ollama
    driver path (frozen contract).
    """

    CASE_TRUTH = "case_truth"
    PUBLIC_WORLD = "public_world"
    EVIDENCE = "evidence"
    WORLD_GRAPH = "world_graph"
    # REPAIR returns a COMPLETE replacement draft (all four sections).
    REPAIR = "repair"
    # Phase 16_2 Ollama-stage-driver internal AssetSpec stages.
    ASSET_SPEC = "asset_spec"
    ASSET_SPEC_REPAIR = "asset_spec_repair"


@dataclass(frozen=True)
class GenerateRequest:
    """One provider invocation.

    ``prompt_context`` must contain sanitized material ONLY — never a
    ``CaseTruth`` object and never hidden solver internals. ``locked`` carries
    the sanitized ``LockedConstraints`` projection (when the caller chooses to
    include it). ``diagnostics`` carries sanitized structured repair
    diagnostics only.
    """

    attempt_id: str
    stage: GenerationStage
    prompt_context: str  # sanitized material ONLY
    locked: "LockedConstraints | None" = None
    diagnostics: tuple[str, ...] = ()  # sanitized structured repair diagnostics
    seed: int | None = None
    # Runtime-only effective timeout for this call.  It is populated by the
    # lifecycle controller/driver and is intentionally optional so FakeProvider
    # and existing callers remain unchanged.
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or not self.attempt_id:
            raise ValueError("GenerateRequest.attempt_id must be a non-empty string")
        if not isinstance(self.stage, GenerationStage):
            raise ValueError(
                f"GenerateRequest.stage must be a GenerationStage; got "
                f"{type(self.stage).__name__}"
            )
        if not isinstance(self.prompt_context, str):
            raise ValueError("GenerateRequest.prompt_context must be a str")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        for item in self.diagnostics:
            if not isinstance(item, str):
                raise ValueError(
                    "GenerateRequest.diagnostics must contain strings only"
                )
        if self.seed is not None and (
            not isinstance(self.seed, int) or isinstance(self.seed, bool)
        ):
            raise ValueError("GenerateRequest.seed must be an int or None")
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("GenerateRequest.timeout_seconds must be positive when set")


@dataclass(frozen=True)
class ProviderResult:
    """The single provider outcome (raw text to be parsed + validated).

    Invariant: at most ONE of ``content`` / ``pending`` / ``timed_out`` /
    ``error`` may be set. A pending result MUST carry a ``pending_id`` (and
    vice-versa). This keeps the contract deterministic for the pipeline.
    """

    content: str | None = None  # raw provider text (to be parsed/validated)
    pending: bool = False  # True => deferred completion; caller must resolve()
    pending_id: str | None = None
    timed_out: bool = False
    error: str | None = None  # sanitized provider error message

    def __post_init__(self) -> None:
        labels = int(
            (self.content is not None)
            + int(self.pending)
            + int(self.timed_out)
            + int(self.error is not None)
        )
        if labels > 1:
            raise ValueError(
                "ProviderResult must declare at most one outcome "
                "(content | pending | timed_out | error)"
            )
        if self.pending and not self.pending_id:
            raise ValueError("ProviderResult: pending requires a pending_id")
        if self.pending_id is not None and not self.pending:
            raise ValueError("ProviderResult: pending_id requires pending=True")


class Provider(Protocol):
    """Structural protocol every provider implements."""

    def generate(self, request: GenerateRequest) -> ProviderResult: ...


class CompletionSink(Protocol):
    """Receives the completed result of a previously-deferred call."""

    def on_completion(self, pending_id: str, result: ProviderResult) -> None: ...


class ProviderError(Exception):
    """Base provider failure (script exhaustion, hard errors, ...)."""


class ProviderTimeout(ProviderError):
    """A provider call exceeded its time budget."""
