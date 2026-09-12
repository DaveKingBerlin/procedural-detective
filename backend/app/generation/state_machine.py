"""CaseVersion generation state machine (REQUIREMENTS 7.3, 32.2/32.3).

The exact ``§7.3`` state set with the exact allowed transitions. ``FAILED`` is
terminal (no transition out of FAILED — a retry creates a NEW attempt) and
``PUBLISHED`` is terminal for this phase (the optional ``RETIRED`` state is
NOT implemented).

Validation outcomes classify a ``ValidationReport`` into the recovery paths of
``§32.2``: VALID -> PUBLISHED, RECOVERABLE_REPAIR -> REPAIRING,
RECOVERABLE_REGENERATE -> GENERATING, TERMINAL_FAILURE -> FAILED.
"""

from __future__ import annotations

from enum import Enum

DRAFT = "DRAFT"
GENERATING = "GENERATING"
VALIDATING = "VALIDATING"
REPAIRING = "REPAIRING"
PUBLISHED = "PUBLISHED"
FAILED = "FAILED"

VALID = "VALID"
RECOVERABLE_REPAIR = "RECOVERABLE_REPAIR"
RECOVERABLE_REGENERATE = "RECOVERABLE_REGENERATE"
TERMINAL_FAILURE = "TERMINAL_FAILURE"


class GenerationState(Enum):
    """Exact §7.3 CaseVersion generation state set."""

    DRAFT = DRAFT
    GENERATING = GENERATING
    VALIDATING = VALIDATING
    REPAIRING = REPAIRING
    PUBLISHED = PUBLISHED
    FAILED = FAILED


class ValidationOutcome(Enum):
    """Exact §32.2 validation-outcome classification."""

    VALID = VALID
    RECOVERABLE_REPAIR = RECOVERABLE_REPAIR
    RECOVERABLE_REGENERATE = RECOVERABLE_REGENERATE
    TERMINAL_FAILURE = TERMINAL_FAILURE


ALLOWED_TRANSITIONS: dict[GenerationState, frozenset[GenerationState]] = {
    GenerationState.DRAFT: frozenset({GenerationState.GENERATING}),
    GenerationState.GENERATING: frozenset(
        {GenerationState.VALIDATING, GenerationState.FAILED}
    ),
    GenerationState.VALIDATING: frozenset(
        {
            GenerationState.PUBLISHED,
            GenerationState.REPAIRING,
            GenerationState.GENERATING,
            GenerationState.FAILED,
        }
    ),
    GenerationState.REPAIRING: frozenset(
        {GenerationState.VALIDATING, GenerationState.FAILED}
    ),
    GenerationState.PUBLISHED: frozenset(),  # terminal (§7.3; RETIRED not implemented)
    GenerationState.FAILED: frozenset(),  # terminal (§32.3)
}


class IllegalTransitionError(Exception):
    """Raised by ``assert_transition`` on a forbidden state transition."""

    def __init__(self, state: GenerationState, target: GenerationState) -> None:
        self.state = state
        self.target = target
        super().__init__(
            f"illegal generation state transition: "
            f"{state.value} -> {target.value}"
        )


def assert_transition(state: GenerationState, target: GenerationState) -> None:
    """Raise ``IllegalTransitionError`` when ``state -> target`` is forbidden."""
    if not isinstance(state, GenerationState):
        raise TypeError(f"state must be a GenerationState; got {type(state).__name__}")
    if not isinstance(target, GenerationState):
        raise TypeError(
            f"target must be a GenerationState; got {type(target).__name__}"
        )
    if target not in ALLOWED_TRANSITIONS[state]:
        raise IllegalTransitionError(state, target)