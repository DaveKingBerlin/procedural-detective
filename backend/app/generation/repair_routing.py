"""Phase38 — deterministic repair-owner routing (validator code -> owning stage).

Closed, deterministic mapping from the existing ``validation_failure_codes``
tokens onto the stage that OWNS the defect, so a controller repair re-runs
ONLY the work that can change the defect (§57). The mapping is derived from the
ACTUAL validator ownership — never from parsing issue-message strings:

- structural/parse  -> UNKNOWN      (the safe full-rerun fallback — the stage
                                     is not attributable from a closed token)
- safety/generic    -> UNKNOWN      (cross-stage bucket; safe fallback)
- universe issues   -> never reach REPAIR (they classify REGENERATE)
- world             -> WORLD_GRAPH
- quality role/candidate codes
  (PUBLIC_ROLE_TRUTH_LEAK /
   VICTIM_IN_SUSPECT_CANDIDATES /
   WITNESS_IN_SUSPECT_CANDIDATES /
   MURDERER_NOT_SUSPECT_CANDIDATE /
   HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET) -> CASE_PEOPLE  (public persons/roles)

  Phase40 refinement (§14): of those, the CANDIDATE-INTEGRITY codes
  (VICTIM_IN_SUSPECT_CANDIDATES / WITNESS_IN_SUSPECT_CANDIDATES /
  MURDERER_NOT_SUSPECT_CANDIDATE / HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET)
  elect ``candidate_integrity_only`` plans when they are the ONLY owning
  defect. The driver then corrects the parsed persons through the
  deterministic candidate-integrity projection with ZERO CASE_PEOPLE provider
  calls, reuses the world when its weapon-evidence input is unchanged
  (§18/§19), and still reruns the candidate-dependent evidence + its activity
  logs (§16/§17). PUBLIC_ROLE_TRUTH_LEAK stays EXCLUSIVELY a CASE_PEOPLE
  provider repair (the projection never rewrites public roles), so genuine
  person/content defects still reach the provider (§41).

  Phase41 refinement: the truth-shape codes (HOMICIDE_MURDERER_MISSING /
  SUICIDE_MURDERER_PRESENT / HOMICIDE_VICTIM_AS_MURDERER / SUICIDE_OPTION_
  MISSING) are owned by CASE_TRUTH — the trusted crime shape itself is
  impossible and only a fresh case can change it.
- quality witness-coverage codes
  (WITNESS_STATEMENT_MISSING/EMPTY/
   UNKNOWN_WITNESS/WITNESS_ID_MISMATCH/
   SPEAKER_MISMATCH)               -> EVIDENCE  ONLY (DEF-095)

  WHY EVIDENCE-only for witness-statement codes, per the ACTUAL validator
  ownership (``case_quality.py::_witness_complete_issues``):

  - every ``WITNESS_STATEMENT_*`` code fires on the EVIDENCE FACTS relative
    to an EXISTING public ``role == "witness"`` person: the statement is
    MISSING/EMPTY/unknown-id/mismatched. The evidence stage authors those
    facts, and the driver's deterministic projection regenerates a canonical
    witness statement for EVERY public witness (``ollama_driver``
    ``_evidence_gap_facts``) — so an EVIDENCE rerun makes every
    witness-statement defect reachable WITHOUT paying for a CASE/PEOPLE
    regeneration.
  - a WRONG witness ROLE is never reported through these codes: a
    role/owner/candidate defect (role token leak, witness in the suspect
    candidates, murderer not accuseable) surfaces under the separate
    CASE_PEOPLE-owned codes above, which DO force the case rerun. Routing
    witness coverage to CASE_PEOPLE as well would force an expensive case
    regeneration for a defect the case output had no part in (§8/§9/§33
    repair amplification).
- solver ambiguity / truth mismatch -> EVIDENCE
- activity-log family  -> ACTIVITY_LOG
- asset spec family    -> ASSET_SPEC

Dependency closure (§11) is applied by ``RepairPlan``:

    CASE_PEOPLE rerun  -> invalidate evidence + activity logs + world
    EVIDENCE rerun     -> invalidate activity logs; world reruns ONLY when the
                          weapon-evidence input actually changed (runtime
                          check in the driver — ``repair_routing`` cannot know)
    ACTIVITY_LOG rerun -> invalidate logs only (evidence + world reused)
    WORLD_GRAPH rerun  -> invalidate world + composition only
    ASSET_SPEC rerun   -> invalidate composition (asset calls) only
    UNKNOWN            -> full rerun (the pre-Phase-38 behavior — the safe
                          default; the cached CASE/PEOPLE parse is reused)

``RepairPlan`` is a frozen, bounded, closed value passed from the controller to
the driver. The plan NEVER carries free-text issue strings, provider text,
timing internals or raw generated content — only closed tokens/booleans.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from app.generation.validation_codes import (
    VALIDATOR_CODE_GENERIC,
    VALIDATOR_CODE_VOCABULARY,
    validation_failure_codes,
)


class RepairOwner(str, Enum):
    """The closed owning-stage vocabulary for a repair.

    ``UNKNOWN`` is the safe fallback for cross-stage/structural defects whose
    owner cannot be attributed from a closed token — it triggers the exact
    pre-Phase-38 full rerun behavior (with the cached CASE/PEOPLE parse
    reused, as the driver already does).
    """

    CASE_TRUTH = "CASE_TRUTH"        # the trusted truth/crime shape itself
    CASE_PEOPLE = "CASE_PEOPLE"      # public persons / roles / candidates
    EVIDENCE = "EVIDENCE"            # evidence facts + deterministic projection
    ACTIVITY_LOG = "ACTIVITY_LOG"    # per-evidence-item activity logs
    WORLD_GRAPH = "WORLD_GRAPH"      # world requirements stage
    ASSET_SPEC = "ASSET_SPEC"        # procedural ASSET_SPEC/ASSET_SPEC_REPAIR
    UNKNOWN = "UNKNOWN"              # safe full-rerun fallback


# Phase41 — the truth-shape codes: the trusted resolution/crime shape itself
# is internally impossible (HOMICIDE without a murderer, SUICIDE with one,
# victim-as-murderer, a suicide truth with no public victim). Only a fresh
# CASE_TRUTH regeneration can change the truth — owned by CASE_TRUTH.
_CASE_TRUTH_QUALITY_CODES: frozenset[str] = frozenset(
    {
        "HOMICIDE_MURDERER_MISSING",
        "SUICIDE_MURDERER_PRESENT",
        "HOMICIDE_VICTIM_AS_MURDERER",
        "SUICIDE_OPTION_MISSING",
    }
)

# The four case-quality codes that live ENTIRELY in the public persons/roles
# (public person role token, candidate role inconsistency, murderer not
# accuseable). Only the driver re-running the CASE/PEOPLE stage — or a
# deterministic canonical projection — can change this data.
_CASE_PEOPLE_QUALITY_CODES: frozenset[str] = frozenset(
    {
        "PUBLIC_ROLE_TRUTH_LEAK",
        "VICTIM_IN_SUSPECT_CANDIDATES",
        "WITNESS_IN_SUSPECT_CANDIDATES",
        "MURDERER_NOT_SUSPECT_CANDIDATE",
        # Phase41 — the murderer is not in the final homicide target universe:
        # the deterministic projection (and its CASE_PEOPLE provider fallback)
        # own the public-person membership fix.
        "HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET",
    }
)

# Phase40/41 — the closed candidate-integrity codes the trusted deterministic
# projection owns (Phase40-Fix.md §5/§14 + Phase41.md §9). A report whose ONLY
# owning defect is one of these elects the candidate-only repair path: the
# driver corrects the parsed persons with ZERO CASE_PEOPLE provider calls
# instead of a full provider regeneration. PUBLIC_ROLE_TRUTH_LEAK is
# deliberately NOT here — the projection must never rewrite a public role, so
# a truth-leak role always falls back to the canonical CASE_PEOPLE repair.
# HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET is here (the "final-target ->
# deterministic projection path" rule): the projection corrects what it can
# and the driver falls back to the canonical CASE_PEOPLE provider repair when
# the murderer's public role itself is the problem.
_CANDIDATE_INTEGRITY_CODES: frozenset[str] = frozenset(
    {
        "VICTIM_IN_SUSPECT_CANDIDATES",
        "WITNESS_IN_SUSPECT_CANDIDATES",
        "MURDERER_NOT_SUSPECT_CANDIDATE",
        "HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET",
    }
)

# The witness-coverage quality codes (DEF-095): the evidence stage authors the
# statement FACTS (including the deterministic witness-statement projection the
# driver applies for every public role=='witness' person), so a
# WITNESS_STATEMENT_* defect is EVIDENCE-owned. A wrong witness ROLE surfaces
# under the separate CASE_PEOPLE-owned codes above (PUBLIC_ROLE_TRUTH_LEAK /
# WITNESS_IN_SUSPECT_CANDIDATES / ...), which force the case rerun — see the
# module docstring for the full rationale. Phase41 adds
# DECEPTIVE_CLAIM_UNCONTRADICTED: a deceptive claim's missing contradiction
# path is an EVIDENCE-content defect (the evidence stage authors the
# mechanical contradiction record).
_WITNESS_COVERAGE_QUALITY_CODES: frozenset[str] = frozenset(
    {
        "WITNESS_STATEMENT_MISSING",
        "WITNESS_STATEMENT_EMPTY",
        "WITNESS_STATEMENT_UNKNOWN_WITNESS",
        "WITNESS_STATEMENT_WITNESS_ID_MISMATCH",
        "WITNESS_STATEMENT_SPEAKER_MISMATCH",
        "DECEPTIVE_CLAIM_UNCONTRADICTED",
    }
)

# The fixed activity-log validator family (both the closed generation codes
# and the Phase 19J typed codes that may appear in a report).
_ACTIVITY_LOG_CODES: frozenset[str] = frozenset(
    {
        "ACTIVITY_LOG_SCHEMA_INVALID",
        "ACTIVITY_LOG_ENTRY_COUNT_INVALID",
        "ACTIVITY_LOG_TIME_ORDER_INVALID",
        "ACTIVITY_LOG_CANONICAL_TIME_MISSING",
        "ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED",
        "ACTIVITY_LOG_TIME_WINDOW_INVALID",
        "ACTIVITY_LOG_DIRECT_TRUTH_LEAK",
        "ACTIVITY_LOG_ENTITY_LEAK",
    }
)


def _owner_for_code(code: str) -> RepairOwner:
    """The closed repair-owner for one validator-code token.

    Every member of ``VALIDATOR_CODE_VOCABULARY`` is covered (the mapping is
    complete); any foreign token (defensize) falls back to ``UNKNOWN``.
    """
    if code in _CASE_TRUTH_QUALITY_CODES:
        return RepairOwner.CASE_TRUTH
    if code in _CASE_PEOPLE_QUALITY_CODES:
        return RepairOwner.CASE_PEOPLE
    if code in _WITNESS_COVERAGE_QUALITY_CODES:
        # DEF-095 — the evidence stage authors witness-statement facts (and
        # the driver deterministically projects a canonical statement for
        # every public witness); a wrong witness ROLE is reported under a
        # separate CASE_PEOPLE-owned code. Witness coverage is therefore
        # EVIDENCE-only — the fix is reachable WITHOUT an expensive case
        # regeneration, §8/§9/§33.
        return RepairOwner.EVIDENCE
    if code in _ACTIVITY_LOG_CODES:
        return RepairOwner.ACTIVITY_LOG
    if code == "SOLVER_AMBIGUOUS":
        return RepairOwner.EVIDENCE
    if code == "WORLD_ASSET_UNRESOLVED":
        return RepairOwner.WORLD_GRAPH
    if code in ("ASSET_SPEC_INVALID", "GEOMETRY_VALIDATION_FAILED"):
        return RepairOwner.ASSET_SPEC
    if code == "STRUCTURED_OUTPUT_INVALID":
        return RepairOwner.UNKNOWN
    if code == VALIDATOR_CODE_GENERIC:
        return RepairOwner.UNKNOWN
    return RepairOwner.UNKNOWN


def repair_owner_for_code(code: str) -> RepairOwner:
    """Public closed owner lookup for one validator-code token (total map)."""
    return _owner_for_code(str(code))


# The fixed, total mapping from every closed validator-code token to its
# repair-owning stage (``UNKNOWN`` = safe full-rerun fallback). Constructed
# deterministically from the token table so the mapping can never drift from
# the code it documents.
REPAIR_OWNERS: Mapping[str, RepairOwner] = {
    code: repair_owner_for_code(code) for code in VALIDATOR_CODE_VOCABULARY
}


def owner_for_report(report: object) -> tuple[RepairOwner, ...]:
    """The sorted, de-duplicated owner tuple for one ``ValidationReport``.

    Derived from the report's STRUCTURED buckets (the defined dataclass fields
    the validators fill — never free-text issue-string parsing):

    - structural / safety buckets   -> UNKNOWN (cross-stage; safe full rerun)
    - world bucket                  -> WORLD_GRAPH
    - quality bucket                -> the per-code owner (CASE_PEOPLE for
                                       role/candidate codes; EVIDENCE for
                                       witness-coverage statements DEF-095)
    - solver non-unique / truth mismatch (``solver_result`` / ``validation``)
                                    -> EVIDENCE

    The closed ``validation_failure_codes`` tokens are the DEFENSIVE fallback
    for codes that surface outside a structured bucket (e.g. the activity-log
    family) — every code maps through the fixed ``_owner_for_code`` table, so
    no free-text message is ever interpreted. Reading ``report.safety_issues``
    / ``report.world_issues`` / ``report.quality_issues`` / ``report.solver_
    result`` / ``report.validation`` is reading DEFINED structured fields —
    the same fields the classification in ``report.py`` uses.
    """
    owners: set[RepairOwner] = set()

    # Cross-stage buckets: safe full-rerun fallback (never attributed to ONE
    # stage from a closed token — that would rerun the wrong stage).
    if getattr(report, "structural_issues", ()) or getattr(report, "safety_issues", ()):
        owners.add(RepairOwner.UNKNOWN)

    # The Phase14 WORld bucket (unresolved required objects / invalid
    # placements / missing evidence paths / unreachable required evidence):
    # the world_graph stage + composition own the defect.
    if getattr(report, "world_issues", ()):
        owners.add(RepairOwner.WORLD_GRAPH)

    # The Phase35 case-quality bucket (closed codes carried verbatim).
    for code in getattr(report, "quality_issues", ()):
        owners.add(_owner_for_code(str(code)))

    # Solver non-unique dimensions / ambiguous-or-overconstrained time.
    proof = getattr(report, "solver_result", None)
    if proof is not None:
        who = getattr(proof, "who", None)
        why = getattr(proof, "why", None)
        weapon = getattr(proof, "weapon", None)
        when = getattr(proof, "when", None)
        if (
            (who is None or why is None or weapon is None or when is None)
            or not (getattr(who, "unique", True) and getattr(why, "unique", True) and getattr(weapon, "unique", True))
            or bool(getattr(when, "ambiguous", False))
            or bool(getattr(when, "overconstrained", False))
        ):
            owners.add(RepairOwner.EVIDENCE)

    # Deterministic truth mismatch -> evidence owner (the evidence algebra is
    # what fixes the deduction).
    validation = getattr(report, "validation", None)
    if validation is not None and not bool(getattr(validation, "all_true", False)):
        owners.add(RepairOwner.EVIDENCE)

    if not owners:
        # Defensive fallback over the CLOSED validator-code tokens (covers
        # codes that surface outside a structured bucket, e.g. the
        # ACTIVITY_LOG family). Every token maps through the fixed table.
        for code in validation_failure_codes(report):
            owners.add(_owner_for_code(code))

    return tuple(sorted(owners, key=lambda owner: owner.value))


@dataclass(frozen=True)
class RepairPlan:
    """Frozen, bounded repair decision handed to a stage driver.

    Only closed tokens/booleans; never free-text diagnostics, never timing
    internals, never raw generated content. ``repair_owners`` is the sorted
    closed owner tuple; the boolean flags apply the §11 dependency closure:

        rerun_case            CASE_PEOPLE rerun (or UNKNOWN fallback)
        rerun_evidence        evidence rerun (forced by case or EVIDENCE)
        rerun_activity_log    logs rerun (forced by evidence/case or ACTIVITY_LOG)
        rerun_world           world rerun (forced by case/WORLD_GRAPH/UNKNOWN;
                              an EVIDENCE-only change reruns world ONLY when the
                              weapon-evidence input changed — runtime check)
        recompose             world composition (asset calls) rerun

    ``candidate_integrity_only`` (Phase40 — backward-compatible optional flag,
    default False) marks a repair whose ONLY defect is one of the three closed
    candidate-integrity codes. Such a repair must NOT regenerate CASE_PEOPLE
    from the provider (the trusted deterministic projection corrects the parsed
    persons), and it must NOT force a world/composition rerun solely because
    candidate membership changed (Phase40 §14/§18). It still reruns evidence
    and activity logs, because evidence depends on candidate eligibility.
    """

    repair_owners: tuple[RepairOwner, ...]
    validator_codes: tuple[str, ...]
    rerun_case: bool
    rerun_evidence: bool
    rerun_activity_log: bool
    rerun_world: bool
    recompose: bool
    candidate_integrity_only: bool = False

    @property
    def is_default_full_rerun(self) -> bool:
        """True when this is the pre-Phase-38 safe full-rerun behavior."""
        return RepairOwner.UNKNOWN in self.repair_owners

    @property
    def wants_case_repair(self) -> bool:
        """True when the CASE/PEOPLE owner data explicitly needs repair."""
        return RepairOwner.CASE_PEOPLE in self.repair_owners

    @property
    def wants_evidence(self) -> bool:
        return RepairOwner.EVIDENCE in self.repair_owners

    def with_case_reuse(self) -> "RepairPlan":
        """A plan identical to this one except the CASE stage is NOT rerun and
        evidence/world are not force-invalidated by a case change.

        The driver uses this for every repair owner that must NOT regenerate
        the case (the driver must never re-run CASE_TRUTH for an unrelated
        defect — that would regenerate the truth). Preserves every other flag.

        DEF-097 (ADV-38-04) — a plan whose ONLY owner is CASE_PEOPLE would
        become an EMPTY no-op here (nothing left to rerun). An empty plan would
        silently consume a repair pass with zero work, so it FAILS SAFE to the
        documented UNKNOWN full-rerun (``is_default_full_rerun`` is True; the
        CASE stage is still NOT regenerated) — never a silent no-op.

        Phase40 — a candidate-integrity-only plan becomes a no-op here too
        (the projection is the only thing that can change its owner data), so
        it FAILS SAFE exactly the same way; ``candidate_integrity_only`` is
        dropped (the reused plan performs no case-affecting change).
        """
        if not self.rerun_case:
            return self
        # Remove the CASE-affecting owners (CASE_PEOPLE + Phase41 CASE_TRUTH)
        # but keep every OTHER owner's flag — only the case-driven
        # force-invalidation is dropped.
        owners = tuple(
            o
            for o in self.repair_owners
            if o not in (RepairOwner.CASE_PEOPLE, RepairOwner.CASE_TRUTH)
        )
        evidence_forced = RepairOwner.EVIDENCE in owners
        logs_forced = RepairOwner.ACTIVITY_LOG in owners or evidence_forced
        world_forced = RepairOwner.WORLD_GRAPH in owners
        result = RepairPlan(
            repair_owners=owners,
            validator_codes=self.validator_codes,
            rerun_case=False,
            rerun_evidence=evidence_forced,
            rerun_activity_log=logs_forced,
            rerun_world=world_forced,
            recompose=world_forced or RepairOwner.ASSET_SPEC in owners,
            candidate_integrity_only=False,
        )
        if not result.repair_owners and not (
            result.rerun_evidence
            or result.rerun_activity_log
            or result.rerun_world
            or result.recompose
        ):
            return type(self).default_full_rerun(self.validator_codes)
        return result

    @classmethod
    def default_full_rerun(cls, validator_codes: tuple[str, ...]) -> "RepairPlan":
        """The exact pre-Phase-38 behavior: rerun evidence + logs + world +
        composition (the cached CASE/PEOPLE parse is reused)."""
        return cls(
            repair_owners=(RepairOwner.UNKNOWN,),
            validator_codes=tuple(sorted(validator_codes)),
            rerun_case=False,
            rerun_evidence=True,
            rerun_activity_log=True,
            rerun_world=True,
            recompose=True,
        )

    @classmethod
    def _is_candidate_integrity_only(
        cls, codes: tuple[str, ...], owners: tuple[RepairOwner, ...]
    ) -> bool:
        """True when the ONLY owning defect is the closed candidate-integrity
        set (Phase40 §14). The generic ``VALIDATION_FAILED`` token that the
        quality bucket always contributes is ignored — it never disqualifies
        the deterministic projection path."""
        if owners != (RepairOwner.CASE_PEOPLE,):
            return False
        if not codes:
            return False
        meaningful = {code for code in codes if code != VALIDATOR_CODE_GENERIC}
        return bool(meaningful) and meaningful <= _CANDIDATE_INTEGRITY_CODES

    @classmethod
    def from_report(cls, report: object) -> "RepairPlan":
        """Derive a deterministic repair plan from one ``ValidationReport``."""
        codes = tuple(sorted(set(validation_failure_codes(report))))
        owners = owner_for_report(report)
        if RepairOwner.UNKNOWN in owners:
            return cls.default_full_rerun(codes)
        candidate_only = cls._is_candidate_integrity_only(codes, owners)
        evidence = RepairOwner.EVIDENCE in owners
        case = RepairOwner.CASE_PEOPLE in owners
        # Phase41 — a CASE_TRUTH owner (impossible trusted truth shape) forces
        # the case stage to rerun exactly like a CASE_PEOPLE defect: evidence
        # and its activity logs are invalidated because the crime shape is the
        # deduction input (§11 dependency closure).
        truth_case = RepairOwner.CASE_TRUTH in owners
        act_log = RepairOwner.ACTIVITY_LOG in owners
        world = RepairOwner.WORLD_GRAPH in owners
        asset = RepairOwner.ASSET_SPEC in owners
        rerun_world = case or truth_case or world
        recompose = case or truth_case or world or asset
        if candidate_only:
            # Phase40 §14/§18/§19 — candidate-integrity-only repair: the
            # trusted deterministic projection changes ONLY candidate
            # eligibility. Evidence (candidate-dependent) and its activity logs
            # still rerun, but the CASE/PROVIDER stage is NOT regenerated and
            # the world/composition are NOT force-rerun (reused when the
            # weapon-evidence input is unchanged — runtime check in the driver).
            rerun_world = False
            recompose = False
        return cls(
            repair_owners=owners,
            validator_codes=codes,
            rerun_case=case or truth_case,
            rerun_evidence=case or truth_case or evidence,
            rerun_activity_log=case or truth_case or evidence or act_log,
            rerun_world=rerun_world,
            recompose=recompose,
            candidate_integrity_only=candidate_only,
        )


__all__ = [
    "REPAIR_OWNER_VOCABULARY",
    "REPAIR_OWNERS",
    "RepairOwner",
    "RepairPlan",
    "owner_for_report",
    "repair_owner_for_code",
]


# Closed owner vocabulary exposed for tests/observability: every code in the
# validator-code vocabulary maps to exactly one of these owners (UNKNOWN is
# the safe fallback). Kept as a tuple for deterministic iteration order.
REPAIR_OWNER_VOCABULARY: tuple[RepairOwner, ...] = (
    RepairOwner.CASE_TRUTH,
    RepairOwner.CASE_PEOPLE,
    RepairOwner.EVIDENCE,
    RepairOwner.ACTIVITY_LOG,
    RepairOwner.WORLD_GRAPH,
    RepairOwner.ASSET_SPEC,
    RepairOwner.UNKNOWN,
)