"""Canonical, player-safe generation failure codes (Phase 17E Wave 2)."""

from __future__ import annotations

from enum import Enum


class GenerationFailureCode(str, Enum):
    GENERATION_DEADLINE_EXCEEDED = "GENERATION_DEADLINE_EXCEEDED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    PROVIDER_CALL_BUDGET_EXHAUSTED = "PROVIDER_CALL_BUDGET_EXHAUSTED"
    REPAIR_BUDGET_EXHAUSTED = "REPAIR_BUDGET_EXHAUSTED"
    REGENERATION_BUDGET_EXHAUSTED = "REGENERATION_BUDGET_EXHAUSTED"
    STRUCTURED_OUTPUT_INVALID = "STRUCTURED_OUTPUT_INVALID"
    ASSET_SPEC_INVALID = "ASSET_SPEC_INVALID"
    GEOMETRY_VALIDATION_FAILED = "GEOMETRY_VALIDATION_FAILED"
    SOLVER_AMBIGUOUS = "SOLVER_AMBIGUOUS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PUBLICATION_FAILED = "PUBLICATION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # Phase 19 Fix C — the narrower hierarchical budget codes. The system
    # emits these instead of the generic PROVIDER_CALL_BUDGET_EXHAUSTED when
    # the narrower cause is known (the global code stays for a GLOBAL ceiling
    # hit with no narrower attribution).
    CORE_PROVIDER_CALL_BUDGET_EXHAUSTED = "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED"
    ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED = "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED"
    MAX_PROCEDURAL_ASSETS_EXCEEDED = "MAX_PROCEDURAL_ASSETS_EXCEEDED"
    MAX_FAILED_ASSETS_EXCEEDED = "MAX_FAILED_ASSETS_EXCEEDED"


PUBLIC_FAILURE_CODES = frozenset(code.value for code in GenerationFailureCode)


def public_failure_code(value: object) -> str | None:
    """Return a known public code, never arbitrary internal text."""

    if isinstance(value, GenerationFailureCode):
        return value.value
    if isinstance(value, str) and value in PUBLIC_FAILURE_CODES:
        return value
    return None


def infer_failure_code(reason: str | None) -> GenerationFailureCode:
    """Map legacy/internal reason text to one canonical primary code."""

    text = (reason or "").lower()
    if "deadline" in text:
        return GenerationFailureCode.GENERATION_DEADLINE_EXCEEDED
    if "timed out" in text or "timeout" in text:
        return GenerationFailureCode.PROVIDER_TIMEOUT
    # Phase 19 Fix C: the narrower hierarchical budget causes are detected
    # BEFORE the generic "call budget" fallback so a narrowly-attributed
    # exhaustion never collapses back into PROVIDER_CALL_BUDGET_EXHAUSTED.
    if "core model call budget" in text or "core call budget" in text:
        return GenerationFailureCode.CORE_PROVIDER_CALL_BUDGET_EXHAUSTED
    if "asset model call budget" in text or "asset call budget" in text:
        return GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED
    if "procedural asset" in text and ("ceiling" in text or "exceeded" in text or "max" in text):
        return GenerationFailureCode.MAX_PROCEDURAL_ASSETS_EXCEEDED
    if "failed asset" in text and ("threshold" in text or "ceiling" in text or "exceeded" in text):
        return GenerationFailureCode.MAX_FAILED_ASSETS_EXCEEDED
    if "call budget" in text or "model call budget" in text:
        return GenerationFailureCode.PROVIDER_CALL_BUDGET_EXHAUSTED
    if "repair budget" in text:
        return GenerationFailureCode.REPAIR_BUDGET_EXHAUSTED
    if "regeneration budget" in text:
        return GenerationFailureCode.REGENERATION_BUDGET_EXHAUSTED
    if "asset spec" in text and ("invalid" in text or "geometr" in text):
        return GenerationFailureCode.ASSET_SPEC_INVALID
    if "geometr" in text:
        return GenerationFailureCode.GEOMETRY_VALIDATION_FAILED
    if "ambiguous" in text:
        return GenerationFailureCode.SOLVER_AMBIGUOUS
    if "provider" in text and ("unavailable" in text or "failure" in text):
        return GenerationFailureCode.PROVIDER_UNAVAILABLE
    if "provider" in text and ("response" in text or "content" in text):
        return GenerationFailureCode.PROVIDER_INVALID_RESPONSE
    if "publication" in text:
        return GenerationFailureCode.PUBLICATION_FAILED
    if "structured" in text or "parse" in text:
        return GenerationFailureCode.STRUCTURED_OUTPUT_INVALID
    if "validation" in text or "solver" in text:
        return GenerationFailureCode.VALIDATION_FAILED
    return GenerationFailureCode.INTERNAL_ERROR


def failure_code_for_budget_reason(reason: str | None) -> GenerationFailureCode:
    """Map a ``BudgetTracker.exhausted_reason`` text to the narrowest code.

    The BudgetTracker's reason strings already carry the narrowed phrases
    (``"core model call budget exhausted"`` / ``"asset model call budget
    exhausted for <id>"``); the generic global reason maps back to the plain
    PROVIDER_CALL_BUDGET_EXHAUSTED exactly as before.
    """
    return infer_failure_code(reason)


__all__ = [
    "GenerationFailureCode",
    "PUBLIC_FAILURE_CODES",
    "failure_code_for_budget_reason",
    "infer_failure_code",
    "public_failure_code",
]