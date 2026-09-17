"""Validation report + deterministic outcome classification (REQUIREMENTS 31/32.2).

``ValidationReport`` is the single machine-readable validation result of a
draft. It carries only sanitized issue strings (never tracebacks, never raw
provider error text, never hidden internals beyond the draft material that is
already public) plus the deduction proof and — as the ONLY truth-aware use in
the generation lifecycle — the ``AccusedSolutionValidation`` comparison.

Classification (deterministic, in this exact order — §32.2):

1. locked_violations non-empty             -> TERMINAL_FAILURE (§7.2: repair must
   not change locked fields; unfixable inside this attempt)
2. structural_issues or safety_issues      -> RECOVERABLE_REPAIR
3. universe_issues non-empty               -> RECOVERABLE_REGENERATE
4. world_issues non-empty (Phase 14: the WORld validation bucket —
   unresolved required object / environment mismatch / invalid placement /
   missing interaction path / unreachable required evidence) -> RECOVERABLE_REPAIR
5. solver missing/incomplete OR any of the who/why/weapon dimensions not
   unique OR when ambiguous OR when overconstrained -> RECOVERABLE_REGENERATE
6. truth mismatch (``validation.all_true`` False)   -> RECOVERABLE_REPAIR
7. else                                    -> VALID
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.proof import SolverProof
from app.generation.state_machine import ValidationOutcome
from app.validation.solution import AccusedSolutionValidation


@dataclass(frozen=True)
class ValidationReport:
    """Deterministic full validation result for one draft.

    ``solver_result`` is the truth-independent deduction proof
    (``app.domain.proof.SolverProof``); ``validation`` is the truth-aware
    comparison (``app.validation.solution.AccusedSolutionValidation``) — the
    ONLY place hidden truth touches this lifecycle.

    ``world_issues`` is the Phase 14 WORld bucket: sanitized issues produced by
    the world composer (``app.world.composer``) — unresolved required objects,
    environment mismatches, invalid placements, missing evidence interaction
    paths and unreachable required evidence. A non-empty world bucket classifies
    RECOVERABLE_REPAIR (repair reruns the COMPLETE validation pipeline).
    """

    structural_issues: tuple[str, ...] = ()
    safety_issues: tuple[str, ...] = ()
    universe_issues: tuple[str, ...] = ()
    solver_result: SolverProof | None = None
    validation: AccusedSolutionValidation | None = None
    locked_violations: tuple[str, ...] = ()
    world_issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "structural_issues", tuple(self.structural_issues))
        object.__setattr__(self, "safety_issues", tuple(self.safety_issues))
        object.__setattr__(self, "universe_issues", tuple(self.universe_issues))
        object.__setattr__(self, "locked_violations", tuple(self.locked_violations))
        object.__setattr__(self, "world_issues", tuple(self.world_issues))
        if self.validation is not None and not isinstance(
            self.validation, AccusedSolutionValidation
        ):
            raise TypeError("report.validation must be AccusedSolutionValidation or None")

    # -- derived classification ----------------------------------------------

    @property
    def valid(self) -> bool:
        """True exactly when the report classifies as VALID (§32.2)."""
        return self.outcome is ValidationOutcome.VALID

    @property
    def outcome(self) -> ValidationOutcome:
        """Deterministic §32.2 classification (order is load-bearing)."""
        if self.locked_violations:
            return ValidationOutcome.TERMINAL_FAILURE
        if self.structural_issues or self.safety_issues:
            return ValidationOutcome.RECOVERABLE_REPAIR
        if self.universe_issues:
            return ValidationOutcome.RECOVERABLE_REGENERATE
        if self.world_issues:
            # Phase 14: the composed world is unplayable -> world repair reruns
            # the COMPLETE validation pipeline again (locked fields untouched).
            return ValidationOutcome.RECOVERABLE_REPAIR
        proof = self.solver_result
        if proof is None:
            return ValidationOutcome.RECOVERABLE_REGENERATE
        if proof.who is None or proof.why is None or proof.weapon is None or proof.when is None:
            # incomplete solver result — the deduction may not be trusted
            return ValidationOutcome.RECOVERABLE_REGENERATE
        if not (proof.who.unique and proof.why.unique and proof.weapon.unique):
            return ValidationOutcome.RECOVERABLE_REGENERATE
        if proof.when.ambiguous or proof.when.overconstrained:
            return ValidationOutcome.RECOVERABLE_REGENERATE
        if self.validation is None or not self.validation.all_true:
            return ValidationOutcome.RECOVERABLE_REPAIR
        return ValidationOutcome.VALID

    @property
    def repair_diagnostics(self) -> tuple[str, ...]:
        """Sanitized, sorted, machine-readable repair diagnostics (§G).

        Never includes hidden internals, tracebacks or provider messages
        verbatim. Time/dimension ambiguity is summarized in safe form. The
        Phase 14 world bucket contributes its structured ``world.*`` issues
        (unresolved object / invalid placement / evidence path problems) —
        sanitized by construction, never the raw prompt.
        """
        diagnostics: list[str] = list(self.structural_issues)
        diagnostics += list(self.safety_issues)
        diagnostics += list(self.universe_issues)
        diagnostics += list(self.world_issues)
        proof = self.solver_result
        if proof is not None:
            if proof.who is not None and not proof.who.unique:
                diagnostics.append("suspect dimension ambiguous")
            if proof.why is not None and not proof.why.unique:
                diagnostics.append("motive dimension ambiguous")
            if proof.weapon is not None and not proof.weapon.unique:
                diagnostics.append("weapon dimension ambiguous")
            if proof.when is not None:
                if proof.when.overconstrained:
                    diagnostics.append("time solution overconstrained")
                if proof.when.ambiguous:
                    diagnostics.append("time solution ambiguous")
        validation = self.validation
        if validation is not None and not validation.all_true:
            for dimension, deduced, canonical in (
                ("murderer", validation.deducted_murderer, validation.canonical_murderer),
                ("motive", validation.deducted_motive, validation.canonical_motive),
                ("weapon", validation.deducted_weapon, validation.canonical_weapon),
            ):
                if deduced is not None and deduced != canonical:
                    diagnostics.append(f"solver mismatch for dimension: {dimension}")
        for violation in self.locked_violations:
            diagnostics.append(f"locked constraint: {violation}")
        return tuple(sorted(set(diagnostics)))