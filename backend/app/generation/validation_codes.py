"""Phase31A — pure, bounded validator-code telemetry (diagnostics only).

Maps the EXISTING safe validation issue vocabulary (the
``ValidationReport`` buckets) onto a CLOSED set of ``validatorCode`` tokens so
the generation controller can observe WHICH validators fail before/after each
repair without ever emitting free-text issue strings, raw drafts, prompts or
CaseTruth. Repairs keep receiving the unchanged free-text diagnostics
(``report.repair_diagnostics``); this module is purely additive observability.

Closed-set rule (security invariant): every code this module emits is a member
of ``VALIDATOR_CODE_VOCABULARY`` below. Unknown/mapped-free issue text collapses
to the bounded generic ``VALIDATION_FAILED`` fallback. A code is NEVER derived
from a generated value, a CaseTruth field or provider text. The bucket
classification mirrors the controller's existing ``_validation_failure_code``
mapping so the closed codes agree with the tuned failure vocabulary.

Severity ordering (documented; low -> high) used by ``repair_effectiveness``
(Phase31A §14):

    GEOMETRY_VALIDATION_FAILED       1   shape/geometry mismatch
    STRUCTURED_OUTPUT_INVALID        2   parse-level structured-output breach
    VALIDATION_FAILED                3   generic validator failure
    WORLD_ASSET_UNRESOLVED           4   required world asset unresolved
    SOLVER_AMBIGUOUS                 5   solver/regeneration-class ambiguity
    PHASE35_QUALITY                  5   case-quality content defect
        (VICTIM_IN_SUSPECT_CANDIDATES / WITNESS_IN_SUSPECT_CANDIDATES /
        MURDERER_NOT_SUSPECT_CANDIDATE / WITNESS_STATEMENT_*)
    ASSET_SPEC_INVALID               6   asset-spec semantic failure
    PUBLIC_ROLE_TRUTH_LEAK           7   pre-reveal public truth-isolation
                                        violation (same tier as activity-log
                                        truth leak)
    ACTIVITY_LOG_*                   7   activity-log validator family

Repair-effectiveness classification (Phase31A §14, deterministic):

    VALID       — no validation failures remain (``after`` empty)
    UNCHANGED   — the effective failure set is unchanged
    IMPROVED    — the worst observed class strictly improved, OR the failure
                  set STRICTLY shrank without introducing a worse class
    REGRESSED   — a new failure was introduced and/or severity increased
"""

from __future__ import annotations

import hashlib
from typing import Mapping, Sequence

# The CLOSED validator-code vocabulary (a bounded subset of the existing
# ``GenerationFailureCode`` values plus the generic fallback). Every token
# emitted by ``validation_failure_codes`` is drawn from here.
VALIDATOR_CODE_GENERIC = "VALIDATION_FAILED"

# Phase35 — the closed case-quality validator-code tokens. They are the SAME
# closed tokens ``app.generation.case_quality`` emits, registered here so the
# quality bucket contributes them to validator-code telemetry (and so
# ``_add_closed_tokens`` can pick them up from safe issue texts).
QUALITY_VALIDATOR_CODES: frozenset[str] = frozenset(
    {
        "PUBLIC_ROLE_TRUTH_LEAK",
        "VICTIM_IN_SUSPECT_CANDIDATES",
        "WITNESS_IN_SUSPECT_CANDIDATES",
        "MURDERER_NOT_SUSPECT_CANDIDATE",
        "WITNESS_STATEMENT_MISSING",
        "WITNESS_STATEMENT_EMPTY",
        "WITNESS_STATEMENT_UNKNOWN_WITNESS",
        "WITNESS_STATEMENT_WITNESS_ID_MISMATCH",
        "WITNESS_STATEMENT_SPEAKER_MISMATCH",
    }
)

VALIDATOR_CODE_VOCABULARY: frozenset[str] = frozenset(
    {
        "STRUCTURED_OUTPUT_INVALID",
        "GEOMETRY_VALIDATION_FAILED",
        "SOLVER_AMBIGUOUS",
        VALIDATOR_CODE_GENERIC,
        "WORLD_ASSET_UNRESOLVED",
        "ASSET_SPEC_INVALID",
        "ACTIVITY_LOG_SCHEMA_INVALID",
        "ACTIVITY_LOG_ENTRY_COUNT_INVALID",
        "ACTIVITY_LOG_TIME_ORDER_INVALID",
        "ACTIVITY_LOG_CANONICAL_TIME_MISSING",
        "ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED",
        "ACTIVITY_LOG_TIME_WINDOW_INVALID",
        "ACTIVITY_LOG_DIRECT_TRUTH_LEAK",
        "ACTIVITY_LOG_ENTITY_LEAK",
    }
) | QUALITY_VALIDATOR_CODES

# Ordered severity (low -> high). The documented ordering in the module
# docstring; every closed code has a value so ``repair_effectiveness`` is a
# total function over the vocabulary (unknown/foreign codes fail safe to the
# LOWEST severity — a repair that touches them can never be over-credited).
_VALIDATOR_CODE_SEVERITY: Mapping[str, int] = {
    "GEOMETRY_VALIDATION_FAILED": 1,
    "STRUCTURED_OUTPUT_INVALID": 2,
    "VALIDATION_FAILED": 3,
    "WORLD_ASSET_UNRESOLVED": 4,
    "SOLVER_AMBIGUOUS": 5,
    "ASSET_SPEC_INVALID": 6,
    "ACTIVITY_LOG_SCHEMA_INVALID": 7,
    "ACTIVITY_LOG_ENTRY_COUNT_INVALID": 7,
    "ACTIVITY_LOG_TIME_ORDER_INVALID": 7,
    "ACTIVITY_LOG_CANONICAL_TIME_MISSING": 7,
    "ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED": 7,
    "ACTIVITY_LOG_TIME_WINDOW_INVALID": 7,
    "ACTIVITY_LOG_DIRECT_TRUTH_LEAK": 7,
    "ACTIVITY_LOG_ENTITY_LEAK": 7,
    # Phase35 case-quality repairs: provider-content defects that a full-draft
    # targeted repair can fix (role rewrite / candidate affordance / witness
    # statement). PUBLIC_ROLE_TRUTH_LEAK is a truth-isolation violation on the
    # same severity tier as the activity-log truth leak.
    "VICTIM_IN_SUSPECT_CANDIDATES": 5,
    "WITNESS_IN_SUSPECT_CANDIDATES": 5,
    "MURDERER_NOT_SUSPECT_CANDIDATE": 5,
    "WITNESS_STATEMENT_MISSING": 5,
    "WITNESS_STATEMENT_EMPTY": 5,
    "WITNESS_STATEMENT_UNKNOWN_WITNESS": 5,
    "WITNESS_STATEMENT_WITNESS_ID_MISMATCH": 5,
    "WITNESS_STATEMENT_SPEAKER_MISMATCH": 5,
    "PUBLIC_ROLE_TRUTH_LEAK": 7,
}

_DEFAULT_SEVERITY = 0  # unknown/foreign codes: lowest possible severity


def _severity(code: str) -> int:
    return _VALIDATOR_CODE_SEVERITY.get(code, _DEFAULT_SEVERITY)


def _add_closed_tokens(codes: set[str], texts: Sequence[str]) -> None:
    """Deterministic closed-vocabulary scan over safe issue texts.

    An issue text that contains a closed validator code token (case-insensitive
    substring) contributes exactly that token. Never raw generated values: the
    scan only ever matches members of the closed vocabulary.
    """
    for item in texts:
        lowered = str(item).casefold()
        for token in VALIDATOR_CODE_VOCABULARY:
            if token.casefold() in lowered:
                codes.add(token)


def validation_failure_codes(report: object) -> tuple[str, ...]:
    """The bounded, sorted validator-code tuple for a ``ValidationReport``.

    Deterministic classification of the report buckets onto the CLOSED code
    set (see ``VALIDATOR_CODE_VOCABULARY``). Returns an EMPTY tuple when the
    report is fully valid (no failures). Never includes free-text issue
    strings, raw generated values, prompts or CaseTruth.

    Bucket mapping (mirrors ``controller._validation_failure_code``):
    - structural issues             -> STRUCTURED_OUTPUT_INVALID
    - safety / universe / locked    -> VALIDATION_FAILED
    - quality_issues                -> their exact closed Phase35 tokens (the
      quality codes ARE the closed vocabulary members) + VALIDATION_FAILED
    - ``world.unresolved-object``   -> WORLD_ASSET_UNRESOLVED
    - non-unique / ambiguous solver -> SOLVER_AMBIGUOUS
    - incomplete solver / truth mismatch -> VALIDATION_FAILED
    - any closed token appearing in a safe issue text contributes itself
      (e.g. an ``ACTIVITY_LOG_*`` fragment in a diagnostic).
    """
    structural = tuple(getattr(report, "structural_issues", ()) or ())
    safety = tuple(getattr(report, "safety_issues", ()) or ())
    universe = tuple(getattr(report, "universe_issues", ()) or ())
    world = tuple(getattr(report, "world_issues", ()) or ())
    locked = tuple(getattr(report, "locked_violations", ()) or ())
    quality = tuple(getattr(report, "quality_issues", ()) or ())
    diagnostics = tuple(getattr(report, "repair_diagnostics", ()) or ())

    codes: set[str] = set()
    _add_closed_tokens(codes, structural)
    _add_closed_tokens(codes, safety)
    _add_closed_tokens(codes, universe)
    _add_closed_tokens(codes, world)
    _add_closed_tokens(codes, locked)
    _add_closed_tokens(codes, quality)
    _add_closed_tokens(codes, diagnostics)

    if structural:
        codes.add("STRUCTURED_OUTPUT_INVALID")
    if safety or universe:
        codes.add(VALIDATOR_CODE_GENERIC)
    if any("world.unresolved-object" in item for item in world):
        codes.add("WORLD_ASSET_UNRESOLVED")
    if world:
        codes.add(VALIDATOR_CODE_GENERIC)
    if locked:
        codes.add(VALIDATOR_CODE_GENERIC)
    if quality:
        codes.update(QUALITY_VALIDATOR_CODES & set(quality))
        codes.add(VALIDATOR_CODE_GENERIC)

    proof = getattr(report, "solver_result", None)
    if proof is not None:
        who = getattr(proof, "who", None)
        why = getattr(proof, "why", None)
        weapon = getattr(proof, "weapon", None)
        when = getattr(proof, "when", None)
        if who is None or why is None or weapon is None or when is None:
            codes.add(VALIDATOR_CODE_GENERIC)
        else:
            if not (who.unique and why.unique and weapon.unique):
                codes.add("SOLVER_AMBIGUOUS")
            if bool(getattr(when, "ambiguous", False)) or bool(
                getattr(when, "overconstrained", False)
            ):
                codes.add("SOLVER_AMBIGUOUS")

    validation = getattr(report, "validation", None)
    if validation is not None and not bool(getattr(validation, "all_true", False)):
        codes.add(VALIDATOR_CODE_GENERIC)

    return tuple(sorted(codes))


def failure_set_fingerprint(codes: Sequence[str]) -> str:
    """Stable SHA-256 hex of the SORTED closed code set.

    Deterministic fingerprint of a validator-code failure set (used as the
    bounded ``failureCodeSetFingerprint`` observability field). An empty set
    hashes the empty byte string, so a fully-valid pass has a stable
    all-clear fingerprint too.
    """
    unique = sorted({str(code) for code in codes if str(code)})
    if not unique:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256("\x1f".join(unique).encode("utf-8")).hexdigest()


def failure_set_delta(
    before: Sequence[str],
    after: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    """Deterministic set algebra over the closed validator-code tokens.

    Returns ``{"codes_fixed": ..., "codes_unchanged": ..., "codes_introduced":
    ...}`` — each a sorted tuple of closed tokens. Pure set operations; never
    free-text, never generated content.
    """
    before_set = {str(code) for code in before if str(code)}
    after_set = {str(code) for code in after if str(code)}
    return {
        "codes_fixed": tuple(sorted(before_set - after_set)),
        "codes_unchanged": tuple(sorted(before_set & after_set)),
        "codes_introduced": tuple(sorted(after_set - before_set)),
    }


def repair_effectiveness(before: Sequence[str], after: Sequence[str]) -> str:
    """Phase31A §14 — deterministic repair-effectiveness classification.

    Returns one of the four closed tokens (``VALID`` / ``UNCHANGED`` /
    ``IMPROVED`` / ``REGRESSED``):

    - ``VALID``      — ``after`` is empty (no validation failures remain);
    - ``UNCHANGED``  — the effective failure set (sorted unique codes) is equal;
    - ``IMPROVED``   — the worst observed severity strictly DECREASED, or the
                       failure set strictly SHRANK without introducing any new
                       failure (a strict subset can only equal-or-lower the
                       worst class);
    - ``REGRESSED``  — every other change (a new failure appeared and/or the
                       worst severity did not improve).

    Closed ordering: see ``_VALIDATOR_CODE_SEVERITY``. Foreign/unknown codes
    map to the lowest severity (fail-safe: never credited as an improvement).
    Purely diagnostic — this classification NEVER changes repair limits,
    budgets or deadlines.
    """
    before_set = {str(code) for code in before if str(code)}
    after_set = {str(code) for code in after if str(code)}
    if not after_set:
        return "VALID"
    if before_set == after_set:
        return "UNCHANGED"
    before_max = max((_severity(code) for code in before_set), default=0)
    after_max = max((_severity(code) for code in after_set), default=0)
    if after_max < before_max:
        return "IMPROVED"
    if after_set < before_set:
        # Strictly smaller set: nothing new was introduced, so the worst class
        # is equal-or-lower (already handled above) and failures decreased.
        return "IMPROVED"
    return "REGRESSED"


__all__ = [
    "QUALITY_VALIDATOR_CODES",
    "VALIDATOR_CODE_GENERIC",
    "VALIDATOR_CODE_VOCABULARY",
    "failure_set_delta",
    "failure_set_fingerprint",
    "repair_effectiveness",
    "validation_failure_codes",
]