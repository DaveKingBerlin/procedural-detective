"""WHO solver — deterministic murderer deduction (§31.9/31.10).

Inputs: PublicCase + discoverable evidence + FeasibleCrimeTimeSet (computed
first by the WHEN solver) + candidate universes (or recomputed from public).
NO ``CaseTruth`` anywhere.

Opportunity: for each suspect derive ``SuspectFeasiblePresenceSet`` from
structured observations (``PERSON_OBSERVED_AT_LOCATION`` with uncertainty) and
the public ``TRAVEL_TIME_MINIMUM`` travel rules. A suspect is EXCLUDED only
when

    SuspectFeasiblePresenceSet ∩ FeasibleCrimeTimeSet == ∅

(necessaryForCandidate=True, effect ``EXCLUDE_SUSPECT``). If any feasible
integer-second tick permits participation, the suspect remains viable
(§31.6 / 65.4).

Observation model: an observation of S at location L (≠ scene) at ``t ± u``
with travel time ``T = L→scene`` makes scene presence impossible during the
block ``[t−u, t+u+T)`` — the suspect occupies L (or is traveling) there; the
earliest possible arrival is ``t−u+T`` and the union of all departure-window
arrival blocks is exactly that half-open block. If L == scene, the observation
does not constrain presence. If no travel rule exists (travel time unknown) or
the block is degenerate/empty, the observation cannot be used to exclude and
contributes no support (DEF-025/DEF-028 — unknown never eliminates). When the
FeasibleCrimeTimeSet is empty (overconstrained), no suspect is excluded on
opportunity (DEF-027).

Alibi: a contradicted alibi claim (supporting vs contradicting evidence) yields
``ALIBI_CREDIBILITY_DECREASE`` with ``necessaryForCandidate=False`` — it NEVER
excludes. Support rules (``MOTIVE_LINKED_TO_PERSON``, presence evidence) can
``SUPPORT_CANDIDATE``/``INCREASE_SUSPICION``; their absence leaves the
proposition ``unknown`` and the candidate viable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.domain.eligibility import CandidateUniverses, derive_universes
from app.domain.evidence import (
    ALIBI_TIME_CLAIM,
    CAN_REACH_CRIME_SCENE_IN_TIME,
    EvidenceFact,
    MOTIVE_LINKED_TO_PERSON,
    PERSON_OBSERVED_AT_LOCATION,
    TypedProposition,
    discoverable_facts,
)
from app.domain.inputs import ensure_solver_input
from app.domain.proof import (
    CandidateDimensionResult,
    DIMENSION_SUSPECT,
    Exclusion,
)
from app.domain.public import PublicCase
from app.domain.rules import (
    RULE_ALIBI_CREDIBILITY,
    RULE_MOTIVE_LINK,
    RULE_OPPORTUNITY_PRESENCE,
    RULE_VERSION_FIRST,
    RuleEffect,
    RuleOutcome,
    PropositionStatus,
    sort_outcomes,
)
from app.domain.time_interval import (
    HalfOpenInterval,
    IntervalSet,
    parse_iso8601_to_epoch,
)


@dataclass(frozen=True)
class _SuspectEvaluation:
    excluded: bool
    supported: bool
    outcomes: tuple[RuleOutcome, ...]


def _opportunity_block(
    public: PublicCase, proposition: TypedProposition, scene_location_id: str | None
) -> HalfOpenInterval | None:
    """Scene-presence-impossible block for one observation of a suspect.

    Returns None when the observation cannot constrain presence:
    - the suspect is observed AT the scene,
    - no public travel rule declares the travel time,
    - the computed block is empty (e.g. ``travel_time_seconds == 0`` and
      ``uncertainty_seconds == 0`` -> ``[t, t)``, which carries no
      information) — a degenerate/empty block must never be added (DEF-025).
    """
    if proposition.location_id is None or scene_location_id is None:
        return None
    if proposition.location_id == scene_location_id:
        return None
    travel = public.travel_time(proposition.location_id, scene_location_id)
    if travel is None:
        return None
    tick = parse_iso8601_to_epoch(proposition.observed_at)
    u = proposition.uncertainty_seconds
    start = tick - u
    end = tick + u + travel
    if end <= start:
        # Degenerate/empty block: no constraint. Do not construct an invalid
        # HalfOpenInterval (which would raise) and do not claim any effect.
        return None
    return HalfOpenInterval(start, end)


def suspect_feasible_presence_set(
    public: PublicCase,
    evidence: Iterable[EvidenceFact],
    suspect_id: str,
    scene_location_id: str | None,
) -> tuple[IntervalSet, tuple[str, ...], tuple[str, ...]]:
    """Derive a suspect's feasible scene-presence set (opportunity reasoning).

    Returns ``(presence, constraining_ids, scene_ids)``:

    - ``presence`` — the IntervalSet where the suspect could feasibly be at the
      scene (full domain minus the union of all non-degenerate opportunity
      blocks);
    - ``constraining_ids`` — evidence ids whose observation produced a usable
      (non-degenerate, travel-rule-backed) presence block. These genuinely
      constrain reachability;
    - ``scene_ids`` — evidence ids that observe the suspect AT the scene
      (direct scene-presence evidence).

    Observations with no travel rule, at an unknown location, or producing a
    degenerate block contribute to NONE of the sets (DEF-028: they can neither
    exclude nor support).
    """
    evidence = tuple(discoverable_facts(evidence))
    forbidden: list[HalfOpenInterval] = []
    constraining: set[str] = set()
    scene_ids: set[str] = set()
    for fact in sorted(evidence, key=lambda f: f.id):
        for proposition in fact.propositions:
            if proposition.type != PERSON_OBSERVED_AT_LOCATION:
                continue
            if proposition.person_id != suspect_id:
                continue
            if scene_location_id is not None and proposition.location_id == scene_location_id:
                scene_ids.add(fact.id)
                continue
            block = _opportunity_block(public, proposition, scene_location_id)
            if block is not None:
                forbidden.append(block)
                constraining.add(fact.id)
    presence = IntervalSet.full_domain()
    if forbidden:
        presence = presence.difference(IntervalSet.from_intervals(forbidden))
    return presence, tuple(sorted(constraining)), tuple(sorted(scene_ids))


def _evaluate_suspect(
    public: PublicCase,
    evidence: Iterable[EvidenceFact],
    suspect_id: str,
    scene_location_id: str | None,
    feasible_time: IntervalSet,
) -> _SuspectEvaluation:
    evidence = tuple(evidence)
    outcomes: list[RuleOutcome] = []

    presence, constraining, scene_ids = suspect_feasible_presence_set(
        public, evidence, suspect_id, scene_location_id
    )
    presence_evidence = tuple(sorted(set(constraining) | set(scene_ids)))

    if feasible_time.is_empty:
        # DEF-027: an overconstrained (empty) FeasibleCrimeTimeSet cannot
        # produce an evidence-backed opportunity exclusion. presence ∩ ∅ is
        # vacuously empty but that emptiness is an artifact of the impossible
        # crime time, not of this suspect's evidence. Every suspect stays
        # viable/unknown; no exclusion.
        outcomes.append(
            RuleOutcome(
                rule_id=RULE_OPPORTUNITY_PRESENCE,
                rule_version=RULE_VERSION_FIRST,
                proposition_type=CAN_REACH_CRIME_SCENE_IN_TIME,
                status=PropositionStatus.UNKNOWN,
                effect=RuleEffect.NO_EFFECT,
                evidence_ids=(),
                target_candidate_id=suspect_id,
                necessary_for_candidate=False,
            )
        )
        intersection_empty = False
    else:
        intersection_empty = presence.intersection(feasible_time).is_empty
        if intersection_empty:
            outcomes.append(
                RuleOutcome(
                    rule_id=RULE_OPPORTUNITY_PRESENCE,
                    rule_version=RULE_VERSION_FIRST,
                    proposition_type=CAN_REACH_CRIME_SCENE_IN_TIME,
                    status=PropositionStatus.CONTRADICTED,
                    effect=RuleEffect.EXCLUDE_SUSPECT,
                    evidence_ids=constraining,
                    target_candidate_id=suspect_id,
                    necessary_for_candidate=True,
                )
            )
        elif presence_evidence:
            outcomes.append(
                RuleOutcome(
                    rule_id=RULE_OPPORTUNITY_PRESENCE,
                    rule_version=RULE_VERSION_FIRST,
                    proposition_type=CAN_REACH_CRIME_SCENE_IN_TIME,
                    status=PropositionStatus.SUPPORTED,
                    effect=RuleEffect.SUPPORT_CANDIDATE,
                    evidence_ids=presence_evidence,
                    target_candidate_id=suspect_id,
                    necessary_for_candidate=False,
                )
            )
        else:
            outcomes.append(
                RuleOutcome(
                    rule_id=RULE_OPPORTUNITY_PRESENCE,
                    rule_version=RULE_VERSION_FIRST,
                    proposition_type=CAN_REACH_CRIME_SCENE_IN_TIME,
                    status=PropositionStatus.UNKNOWN,
                    effect=RuleEffect.NO_EFFECT,
                    evidence_ids=(),
                    target_candidate_id=suspect_id,
                    necessary_for_candidate=False,
                )
            )

    alibi_outcome = _alibi_outcome(evidence, suspect_id, scene_location_id)
    if alibi_outcome is not None:
        outcomes.append(alibi_outcome)

    motive_outcome = _motive_link_outcome(evidence, suspect_id)
    if motive_outcome is not None:
        outcomes.append(motive_outcome)

    sorted_outcomes = sort_outcomes(outcomes)
    excluded = intersection_empty
    supported = any(o.is_support() for o in sorted_outcomes)
    return _SuspectEvaluation(excluded=excluded, supported=supported, outcomes=sorted_outcomes)


def _alibi_outcome(
    evidence: tuple[EvidenceFact, ...],
    suspect_id: str,
    scene_location_id: str | None,
) -> RuleOutcome | None:
    """A contradicted alibi claim yields ALIBI_CREDIBILITY_DECREASE.

    A claim is contradicted when discoverable evidence places the suspect at
    the scene after the claimed departure time. Contradiction affects
    credibility only — it is ``necessary_for_candidate=False`` and can never
    become an exclusion (Phase3.md / §48A).
    """
    claims = [
        fact
        for fact in discoverable_facts(evidence)
        if any(
            p.type == ALIBI_TIME_CLAIM and p.person_id == suspect_id for p in fact.propositions
        )
    ]
    if not claims:
        return None
    claim_ids = tuple(sorted(f.id for f in claims))

    departure_ticks: list[int] = []
    for claim in claims:
        for p in claim.propositions:
            if p.type != ALIBI_TIME_CLAIM or p.person_id != suspect_id:
                continue
            raw = p.structured.get("claimedDeparture")
            if isinstance(raw, str) and raw:
                departure_ticks.append(parse_iso8601_to_epoch(raw))

    def _after_claim(tick: int) -> bool:
        return any(tick >= d for d in departure_ticks) if departure_ticks else True

    contradicting: set[str] = set()
    supporting: set[str] = set()
    for fact in discoverable_facts(evidence):
        for p in fact.propositions:
            if p.type != PERSON_OBSERVED_AT_LOCATION or p.person_id != suspect_id:
                continue
            tick = parse_iso8601_to_epoch(p.observed_at)
            if not _after_claim(tick):
                continue
            if scene_location_id is not None and p.location_id == scene_location_id:
                contradicting.add(fact.id)
            else:
                supporting.add(fact.id)

    if contradicting:
        return RuleOutcome(
            rule_id=RULE_ALIBI_CREDIBILITY,
            rule_version=RULE_VERSION_FIRST,
            proposition_type=ALIBI_TIME_CLAIM,
            status=PropositionStatus.CONTRADICTED,
            effect=RuleEffect.ALIBI_CREDIBILITY_DECREASE,
            evidence_ids=tuple(sorted(set(claim_ids) | contradicting)),
            target_candidate_id=suspect_id,
            necessary_for_candidate=False,
        )
    if supporting:
        return RuleOutcome(
            rule_id=RULE_ALIBI_CREDIBILITY,
            rule_version=RULE_VERSION_FIRST,
            proposition_type=ALIBI_TIME_CLAIM,
            status=PropositionStatus.SUPPORTED,
            effect=RuleEffect.NO_EFFECT,
            evidence_ids=tuple(sorted(set(claim_ids) | supporting)),
            target_candidate_id=suspect_id,
            necessary_for_candidate=False,
        )
    return RuleOutcome(
        rule_id=RULE_ALIBI_CREDIBILITY,
        rule_version=RULE_VERSION_FIRST,
        proposition_type=ALIBI_TIME_CLAIM,
        status=PropositionStatus.UNKNOWN,
        effect=RuleEffect.NO_EFFECT,
        evidence_ids=claim_ids,
        target_candidate_id=suspect_id,
        necessary_for_candidate=False,
    )


def _motive_link_outcome(
    evidence: tuple[EvidenceFact, ...], suspect_id: str
) -> RuleOutcome | None:
    """MOTIVE_LINKED_TO_PERSON evidence linking this suspect -> support.

    Absence leaves the motive proposition unknown; it never excludes.
    """
    links = [
        fact
        for fact in discoverable_facts(evidence)
        if any(
            p.type == MOTIVE_LINKED_TO_PERSON and p.person_id == suspect_id
            for p in fact.propositions
        )
    ]
    if not links:
        return None
    return RuleOutcome(
        rule_id=RULE_MOTIVE_LINK,
        rule_version=RULE_VERSION_FIRST,
        proposition_type=MOTIVE_LINKED_TO_PERSON,
        status=PropositionStatus.SUPPORTED,
        effect=RuleEffect.SUPPORT_CANDIDATE,
        evidence_ids=tuple(sorted(f.id for f in links)),
        target_candidate_id=suspect_id,
        necessary_for_candidate=False,
    )


def solve_who(
    public: PublicCase,
    evidence: Iterable[EvidenceFact],
    feasible_time: IntervalSet,
    universes: CandidateUniverses | None = None,
) -> CandidateDimensionResult:
    """Deterministic murderer deduction over the suspect universe.

    ``feasible_time`` is the WHEN solver's FeasibleCrimeTimeSet. A suspect is
    eliminated ONLY through evidence-backed contradictions of necessary
    conditions (impossible opportunity). Unknown alternatives remain viable.
    """
    evidence = tuple(evidence)
    ensure_solver_input(public, evidence)
    if not isinstance(feasible_time, IntervalSet):
        raise TypeError(
            "feasible_time must be an IntervalSet (FeasibleCrimeTimeSet from the "
            f"WHEN solver); got {type(feasible_time).__name__}."
        )
    universes = universes if universes is not None else derive_universes(public)

    scene = public.scene_location_id
    evaluations: dict[str, _SuspectEvaluation] = {}
    all_outcomes: list[RuleOutcome] = []
    excluded: list[Exclusion] = []
    viable: list[str] = []
    supported: list[str] = []
    unknown: list[str] = []

    for suspect_id in universes.suspect_ids:
        evaluation = _evaluate_suspect(public, evidence, suspect_id, scene, feasible_time)
        evaluations[suspect_id] = evaluation
        all_outcomes.extend(evaluation.outcomes)
        if evaluation.excluded:
            excluded.append(
                Exclusion(
                    candidate_id=suspect_id,
                    rule_outcomes=tuple(o for o in evaluation.outcomes if o.permits_elimination()),
                )
            )
        else:
            viable.append(suspect_id)
            if evaluation.supported:
                supported.append(suspect_id)
            else:
                unknown.append(suspect_id)

    viable_sorted = tuple(sorted(viable))
    supported_sorted = tuple(sorted(supported))
    unknown_sorted = tuple(sorted(unknown))
    unique = (
        len(viable_sorted) == 1
        and len(unknown_sorted) == 0
        and len(excluded) == len(universes.suspect_ids) - 1
    )
    return CandidateDimensionResult(
        dimension=DIMENSION_SUSPECT,
        universe=universes.suspect_ids,
        viable=viable_sorted,
        supported=supported_sorted,
        excluded=tuple(excluded),
        unknown_remaining=unknown_sorted,
        unique=unique,
        winner=viable_sorted[0] if unique else None,
        rule_outcomes=sort_outcomes(all_outcomes),
    )