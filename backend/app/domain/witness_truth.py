"""Trusted statement-level witness truthfulness (Phase41.md §11-§14).

A witness statement is a CLAIM, not automatically a hard world fact. This
module owns the closed, statement-level truthfulness value object that lives
ONLY in the trusted truth path (``CaseTruth.witness_truthfulness``) and is
never serialized into a public DTO or pre-reveal telemetry.

Why a dedicated module (not ``app.domain.truth``): the deterministic evidence
help layer (``app.generation.evidence_facts``) needs to author/label deceptive
statements WITHOUT importing the canonical ``CaseTruth`` module — the
import-graph boundary tests forbid an ``app.generation`` module (outside the
trusted lifecycle modules) from importing ``app.domain.truth``. Keeping the
value object here lets the trusted truth path (``truth.py``) AND the evidence
support layer share it while preserving that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

# The CLOSED deception reasons (Phase41.md §13): a witness may lie for
# self-protection, to protect someone else, or to hide an unrelated secret.
SELF_PROTECTION = "SELF_PROTECTION"
PROTECT_OTHER = "PROTECT_OTHER"
HIDE_UNRELATED_SECRET = "HIDE_UNRELATED_SECRET"

_DECEPTION_REASONS = frozenset(
    {SELF_PROTECTION, PROTECT_OTHER, HIDE_UNRELATED_SECRET}
)


@dataclass(frozen=True)
class WitnessStatementTruth:
    """Hidden statement-level witness truthfulness (Phase41.md §11-§14).

    Server-only, trusted truth metadata: ``(witness_id, question_type)`` ->
    ``TRUTHFUL | DECEPTIVE`` (+ an optional bounded ``deception_reason`` such
    as ``SELF_PROTECTION`` / ``PROTECT_OTHER`` / ``HIDE_UNRELATED_SECRET``).
    Statement-level on purpose — a witness may lie about one topic and tell
    the truth about another.

    SECURITY: this value lives ONLY in the trusted truth path
    (``CaseTruth.witness_truthfulness``). It must NEVER be serialized into a
    public DTO or telemetry before reveal.
    """

    witness_id: str
    question_type: str
    truthfulness: str
    deception_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("witness_id", "question_type", "truthfulness"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"WitnessStatementTruth.{name} must be a non-empty string"
                )
        if self.truthfulness not in ("TRUTHFUL", "DECEPTIVE"):
            raise ValueError(
                "WitnessStatementTruth.truthfulness must be TRUTHFUL or DECEPTIVE"
            )
        if self.deception_reason is not None and (
            not isinstance(self.deception_reason, str) or not self.deception_reason
        ):
            raise ValueError(
                "WitnessStatementTruth.deception_reason must be None or a "
                "non-empty string"
            )


__all__ = [
    "HIDE_UNRELATED_SECRET",
    "PROTECT_OTHER",
    "SELF_PROTECTION",
    "WitnessStatementTruth",
]