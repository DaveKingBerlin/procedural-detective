"""Phase 44 (ADR-004 D2) — Gate 3: the ONE authoritative playability validator.

``validate_playability_quality`` is a PURE, deterministic, ZERO-provider/network
function of the assembled candidate (``draft``, ``public``, ``evidence``,
``universes``, ``truth``) plus the normalized ``difficulty``. It NEVER mutates
its inputs (in particular it never mutates ``CaseTruth`` — AC-04) and never
emits hidden-truth values.

Gate 3 is a SEPARATE observable result from Gate 1 (canonical) / Gate 2
(solver); it does not modify ``ValidationReport.outcome`` semantics.

Closed issue codes (a failure means the candidate is not publishable)::

    PLAYABILITY_EMPTY_WORLD
    PLAYABILITY_TOO_FEW_INSPECTABLES
    PLAYABILITY_TOO_FEW_MEANINGFUL_INSPECTABLES
    PLAYABILITY_EVIDENCE_UNREACHABLE
    PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE
    PLAYABILITY_EVIDENCE_OVERCONCENTRATED
    PLAYABILITY_RED_HERRING_REQUIREMENT_UNMET
    PLAYABILITY_WITNESS_CONTENT_UNREACHABLE
    PLAYABILITY_DIFFICULTY_CONTRACT_UNMET

Reachability model (matches RUNTIME discovery exactly — ADR-004 D2/D7): a
discoverable evidence fact is player-reachable iff EITHER

1. **placement link** — a WORLD_GRAPH placement carries its ``evidence_id`` and
   has a non-empty allowlisted interaction (the runtime ``discover_evidence``
   path: an object interaction whose placement links the record); OR
2. **witness attribution** — it is a witness-kind fact attributed (via the
   canonical ``app.domain.witness`` helpers) to a published witness person with
   a usable (non-empty ``statement``/``description``) answer.

A proposition merely referencing a public person / motive / location reveals
NOTHING at runtime (``services.investigation.discover_evidence`` /
``services.publication.project_discovery``), so THERE IS NO entity-surface
branch: an evidence record that is only canonical (no placement link and no
usable witness attribution) is UNREACHABLE. A world whose every placement has
``evidenceId=None`` therefore exposes zero evidence and is correctly rejected.

Dimension contract (§10/§11, ADV-44-07): HOMICIDE requires WHO + WHY + WEAPON +
WHEN; SUICIDE requires WHY + WEAPON + WHEN. Each REQUIRED dimension must have at
least one REACHABLE clue for the WINNING candidate — not merely a decoy fact of
the same proposition type:
  - WHO: a reachable fact referencing ``proof.who.winner`` (person proposition)
    or a usable witness statement for a witness winner;
  - WHY: a reachable ``MOTIVE_LINKED_TO_PERSON`` for ``proof.why.winner``;
  - WEAPON: a reachable positive weapon fact for ``proof.weapon.winner``
    (match=True forensic / blood / fingerprint on that object);
  - WHEN: a reachable fact in ``proof.when.critical_evidence_ids``.
A required dimension without its winning supporting clue =>
``PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE``. ``PLAYABILITY_EVIDENCE_UNREACHABLE``
fires when additionally no solver-proof-used evidence fact is reachable at all.
It is NOT required that every ``evidence_ids_used`` proof fact be individually
placed (the architecture-compatible reading of §11).

Decorative placements (empty interaction) never expose evidence.
"""

# ADV-44-03 — meaningful/source accounting is RECORD-based, never inflated by
# object multiplicity: one evidence record contributes at most ONE meaningful
# inspectable and at most ONE meaningful source, no matter how many objects link
# it; multiple records on one object still count once per distinct record.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from app.domain import witness as witness_domain
from app.domain.eligibility import CandidateUniverses, derive_universes
from app.domain.evidence import (
    ALIBI_TIME_CLAIM,
    BODY_FIRST_FOUND_AT,
    CAN_REACH_CRIME_SCENE_IN_TIME,
    CRIME_SCENE_OBSERVATION_AT,
    FORENSIC_WEAPON_MATCH,
    MOTIVE_FACT_CONTRADICTED,
    MOTIVE_LINKED_TO_PERSON,
    NOISE_HEARD_AT,
    OBJECT_CONTAINS_BLOOD,
    OBJECT_CONTAINS_FINGERPRINT,
    PERSON_OBSERVED_AT_LOCATION,
    TIME_WINDOW_EXCLUSION,
    VICTIM_LAST_SEEN_ALIVE_AT,
    WITNESS_CLAIMS,
    EvidenceFact,
)
from app.domain.proof import SolverProof
from app.domain.public import PublicCase
from app.domain.resolution import ResolutionType
from app.domain.solver import solve_case
from app.generation import case_quality, difficulty_policy, safety
from app.generation.schemas import GeneratedDraft
from app.world.roles import (
    ROLE_EVIDENCE_RELEVANT,
    ROLE_WEAPON_CANDIDATE,
    derive_object_spec_roles,
)

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids an import cycle)
    from app.generation.publish import PublishedCaseVersion

# --------------------------------------------------------------------------- #
# closed issue codes
# --------------------------------------------------------------------------- #

PLAYABILITY_EMPTY_WORLD = "PLAYABILITY_EMPTY_WORLD"
PLAYABILITY_TOO_FEW_INSPECTABLES = "PLAYABILITY_TOO_FEW_INSPECTABLES"
PLAYABILITY_TOO_FEW_MEANINGFUL_INSPECTABLES = (
    "PLAYABILITY_TOO_FEW_MEANINGFUL_INSPECTABLES"
)
PLAYABILITY_EVIDENCE_UNREACHABLE = "PLAYABILITY_EVIDENCE_UNREACHABLE"
PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE = "PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE"
PLAYABILITY_EVIDENCE_OVERCONCENTRATED = "PLAYABILITY_EVIDENCE_OVERCONCENTRATED"
PLAYABILITY_RED_HERRING_REQUIREMENT_UNMET = "PLAYABILITY_RED_HERRING_REQUIREMENT_UNMET"
PLAYABILITY_WITNESS_CONTENT_UNREACHABLE = "PLAYABILITY_WITNESS_CONTENT_UNREACHABLE"
PLAYABILITY_DIFFICULTY_CONTRACT_UNMET = "PLAYABILITY_DIFFICULTY_CONTRACT_UNMET"

PLAYABILITY_ISSUE_CODES: tuple[str, ...] = (
    PLAYABILITY_EMPTY_WORLD,
    PLAYABILITY_TOO_FEW_INSPECTABLES,
    PLAYABILITY_TOO_FEW_MEANINGFUL_INSPECTABLES,
    PLAYABILITY_EVIDENCE_UNREACHABLE,
    PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE,
    PLAYABILITY_EVIDENCE_OVERCONCENTRATED,
    PLAYABILITY_RED_HERRING_REQUIREMENT_UNMET,
    PLAYABILITY_WITNESS_CONTENT_UNREACHABLE,
    PLAYABILITY_DIFFICULTY_CONTRACT_UNMET,
)

# --------------------------------------------------------------------------- #
# dimension mapping (derived from app.domain.evidence PROPOSITION_TYPES and the
# solver premise usage)
# --------------------------------------------------------------------------- #

DIMENSION_WHO = "WHO"
DIMENSION_WHY = "WHY"
DIMENSION_WEAPON = "WEAPON"
DIMENSION_WHEN = "WHEN"

_ALL_DIMENSIONS: tuple[str, ...] = (
    DIMENSION_WHO,
    DIMENSION_WHY,
    DIMENSION_WEAPON,
    DIMENSION_WHEN,
)

_WHO_TYPES: frozenset[str] = frozenset(
    {
        PERSON_OBSERVED_AT_LOCATION,
        OBJECT_CONTAINS_FINGERPRINT,
        ALIBI_TIME_CLAIM,
        CAN_REACH_CRIME_SCENE_IN_TIME,
        WITNESS_CLAIMS,
    }
)
_WHY_TYPES: frozenset[str] = frozenset(
    {MOTIVE_LINKED_TO_PERSON, MOTIVE_FACT_CONTRADICTED}
)
_WEAPON_TYPES: frozenset[str] = frozenset(
    {FORENSIC_WEAPON_MATCH, OBJECT_CONTAINS_BLOOD, OBJECT_CONTAINS_FINGERPRINT}
)
_WHEN_TYPES: frozenset[str] = frozenset(
    {
        VICTIM_LAST_SEEN_ALIVE_AT,
        BODY_FIRST_FOUND_AT,
        NOISE_HEARD_AT,
        CRIME_SCENE_OBSERVATION_AT,
        TIME_WINDOW_EXCLUSION,
        ALIBI_TIME_CLAIM,
        CAN_REACH_CRIME_SCENE_IN_TIME,
    }
)

_TYPE_DIMENSIONS: Mapping[str, tuple[str, ...]] = {}


def _register(type_codes: Iterable[str], dimension: str) -> None:
    for code in type_codes:
        _TYPE_DIMENSIONS.setdefault(code, ())
        _TYPE_DIMENSIONS[code] = _TYPE_DIMENSIONS[code] + (dimension,)


_register(_WHO_TYPES, DIMENSION_WHO)
_register(_WHY_TYPES, DIMENSION_WHY)
_register(_WEAPON_TYPES, DIMENSION_WEAPON)
_register(_WHEN_TYPES, DIMENSION_WHEN)


def _dimensions_for_type(proposition_type: str) -> tuple[str, ...]:
    return _TYPE_DIMENSIONS.get(str(proposition_type), ())


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlayabilityReport:
    """Deterministic, non-secret Gate 3 result (metrics + closed issue codes)."""

    difficulty: str
    inspectable_count: int
    meaningful_inspectable_count: int
    distinct_meaningful_source_count: int
    red_herring_count: int
    reachable_critical_dimensions: tuple[str, ...] = ()
    unreachable_critical_dimensions: tuple[str, ...] = ()
    issue_codes: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issue_codes

    def to_dict(self) -> dict[str, Any]:
        return {
            "difficulty": self.difficulty,
            "inspectableCount": self.inspectable_count,
            "meaningfulInspectableCount": self.meaningful_inspectable_count,
            "distinctMeaningfulSourceCount": self.distinct_meaningful_source_count,
            "redHerringCount": self.red_herring_count,
            "reachableCriticalDimensions": list(self.reachable_critical_dimensions),
            "unreachableCriticalDimensions": list(self.unreachable_critical_dimensions),
            "issueCodes": list(self.issue_codes),
            "valid": self.valid,
        }


# --------------------------------------------------------------------------- #
# deterministic classification helpers
# --------------------------------------------------------------------------- #


def _interactive_placements(draft: GeneratedDraft) -> tuple[Any, ...]:
    """Placements with a non-empty allowlisted interaction (== inspectable)."""
    world_graph = getattr(draft, "world_graph", None)
    if world_graph is None:
        return ()
    allow = safety.INTERACTION_ALLOWLIST
    return tuple(
        placement
        for placement in (getattr(world_graph, "placements", ()) or ())
        if getattr(placement, "interaction", "") and placement.interaction in allow
    )


def _inspected_object_ids(draft: GeneratedDraft) -> tuple[str, ...]:
    """Distinct object ids of interactive placements (in placement order)."""
    seen: dict[str, None] = {}
    for placement in _interactive_placements(draft):
        object_id = getattr(placement, "object_id", None)
        if isinstance(object_id, str) and object_id:
            seen.setdefault(object_id, None)
    return tuple(seen)


def _object_specs_by_id(draft: GeneratedDraft) -> dict[str, Any]:
    return {
        getattr(spec, "object_id"): spec
        for spec in (getattr(draft, "objects", ()) or ())
        if getattr(spec, "object_id", None)
    }


def _discoverable_facts(evidence: Iterable[EvidenceFact]) -> tuple[EvidenceFact, ...]:
    return tuple(f for f in evidence if getattr(f, "discoverable", True) is not False)


def _evidence_by_id(evidence: Iterable[EvidenceFact]) -> dict[str, EvidenceFact]:
    return {fact.id: fact for fact in evidence}


def _meaningful_accounting(
    draft: GeneratedDraft, discoverable_evidence_ids: frozenset[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(linked_record_ids, no_link_meaningful_object_ids)``.

    ADV-44-01 + ADV-44-03 — RECORD-based meaningful accounting:

    - a *linked record* is a distinct discoverable evidence id that an
      interactive placement's ``evidence_id`` links (counts ONCE regardless of
      how many objects link it);
    - a *no-link meaningful object* is an interactive object with NO resolving
      discoverable evidence link whose derived semantic roles include
      EVIDENCE_RELEVANT / WEAPON_CANDIDATE (e.g. the murder weapon).

    An object is NOT meaningful merely because a proposition references its
    object_id (a canonical reference reveals nothing at runtime). Pure
    decoration (empty interaction) never counts.
    """
    linked: dict[str, None] = {}
    no_link_objects: dict[str, None] = {}
    specs = _object_specs_by_id(draft)
    seen_objects: set[str] = set()
    for placement in _interactive_placements(draft):
        object_id = getattr(placement, "object_id", None)
        if not isinstance(object_id, str) or not object_id or object_id in seen_objects:
            continue
        seen_objects.add(object_id)
        evidence_id = getattr(placement, "evidence_id", None)
        if evidence_id is not None and str(evidence_id) in discoverable_evidence_ids:
            linked.setdefault(str(evidence_id), None)
            continue
        spec = specs.get(object_id)
        roles = derive_object_spec_roles(spec) if spec is not None else ()
        if ROLE_EVIDENCE_RELEVANT in roles or ROLE_WEAPON_CANDIDATE in roles:
            no_link_objects.setdefault(object_id, None)
    return tuple(linked), tuple(no_link_objects)


def _witness_reachability(
    public: PublicCase, evidence: tuple[EvidenceFact, ...]
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """``(usable_witness_ids, usable_fact_ids, all_witness_ids)``.

    A witness is *usable* when it has at least one attributed witness-kind fact
    with usable statement text (reuses the canonical case-quality helpers).
    """
    view = case_quality._payload_view(public, evidence)
    witness_ids = case_quality._witness_ids(public)
    usable_witnesses: set[str] = set()
    usable_fact_ids: set[str] = set()
    for fact_view in view.get("draft", {}).get("evidence", ()) or ():
        kind = str(fact_view.get("kind") or "")
        if kind not in witness_domain.WITNESS_KINDS:
            continue
        witness_id = case_quality._attributed_witness_id(view, fact_view)
        if witness_id is None or witness_id not in witness_ids:
            continue
        if case_quality._usable_statement_text(fact_view.get("presentation") or {}):
            usable_witnesses.add(witness_id)
            fact_id = str(fact_view.get("id") or "")
            if fact_id:
                usable_fact_ids.add(fact_id)
    return frozenset(usable_witnesses), frozenset(usable_fact_ids), witness_ids


def _fact_reachable(
    fact: EvidenceFact,
    *,
    placement_linked_fact_ids: frozenset[str],
    usable_fact_ids: frozenset[str],
) -> bool:
    """ADV-44-01 — a discoverable fact is player-reachable IFF it is
    placement-linked (an interactive placement's ``evidence_id`` == fact.id) OR
    witnessed (a witness-kind fact attributed to a published witness with usable
    content). The person/motive/location "entity surface" branch is REMOVED: a
    proposition merely referencing a public entity reveals nothing at runtime
    (``services.investigation.discover_evidence`` /
    ``services.publication.project_discovery``)."""
    return fact.id in placement_linked_fact_ids or fact.id in usable_fact_ids


def _excluded_candidate_ids(proof: SolverProof | None) -> frozenset[str]:
    if proof is None:
        return frozenset()
    excluded: set[str] = set()
    for dimension in (getattr(proof, "who", None), getattr(proof, "why", None), getattr(proof, "weapon", None)):
        if dimension is None:
            continue
        for exclusion in (getattr(dimension, "excluded", ()) or ()):
            candidate_id = str(getattr(exclusion, "candidate_id", "") or "")
            if candidate_id:
                excluded.add(candidate_id)
    return frozenset(excluded)


def _winner_ids(proof: SolverProof | None) -> frozenset[str]:
    if proof is None:
        return frozenset()
    winners: set[str] = set()
    for dimension in (getattr(proof, "who", None), getattr(proof, "why", None), getattr(proof, "weapon", None)):
        if dimension is None:
            continue
        winner = getattr(dimension, "winner", None)
        if isinstance(winner, str) and winner:
            winners.add(winner)
    return frozenset(winners)


def _subjects_of_facts(evidence: Mapping[str, EvidenceFact], fact_ids: Iterable[str]) -> frozenset[str]:
    subjects: set[str] = set()
    for fact_id in fact_ids:
        fact = evidence.get(fact_id)
        if fact is None:
            continue
        for proposition in (getattr(fact, "propositions", ()) or ()):
            for attribute in ("object_id", "person_id", "motive_id"):
                value = getattr(proposition, attribute, None)
                if isinstance(value, str) and value:
                    subjects.add(value)
    return frozenset(subjects)


def _count_red_herrings(
    reachable_linked_fact_ids: frozenset[str],
    evidence_by_id: Mapping[str, EvidenceFact],
    proof: SolverProof | None,
) -> int:
    """Red-herring count (ADR-004 D7).

    A red herring is a PLACEMENT-LINKED reachable discoverable fact whose
    subject (object/person/motive referenced by that fact) is an evidence-backed
    EXCLUDED candidate of the solver proof and is NOT a winning candidate.
    ADV-44-03 — counted per distinct LINKED RECORD (never per object) and only
    for placement-linked reachable facts (a canonical-only fact reveals nothing).
    """
    if proof is None:
        return 0
    excluded = _excluded_candidate_ids(proof)
    winners = _winner_ids(proof)
    if not excluded:
        return 0
    count = 0
    for fact_id in sorted(reachable_linked_fact_ids):
        subjects = _subjects_of_facts(evidence_by_id, (fact_id,))
        if any(subject in excluded and subject not in winners for subject in subjects):
            count += 1
    return count


def _required_dimensions(truth: Any) -> tuple[str, ...]:
    resolution = getattr(getattr(truth, "crime", None), "resolution_type", None)
    if resolution is ResolutionType.SUICIDE:
        return (DIMENSION_WHY, DIMENSION_WEAPON, DIMENSION_WHEN)
    return _ALL_DIMENSIONS


def _reachable_dimensions(
    reachable_facts: Iterable[EvidenceFact],
) -> frozenset[str]:
    reachable: set[str] = set()
    for fact in reachable_facts:
        for proposition in (getattr(fact, "propositions", ()) or ()):
            for dimension in _dimensions_for_type(getattr(proposition, "type", "")):
                reachable.add(dimension)
    return frozenset(reachable)


# --------------------------------------------------------------------------- #
# ADV-44-07 — WINNER/SUPPORTING-clue reachability per dimension
# --------------------------------------------------------------------------- #


def _winner_dimension_supported(
    dimension: str,
    reachable_facts: Iterable[EvidenceFact],
    proof: SolverProof | None,
    usable_witness_ids: frozenset[str],
) -> bool:
    """Does the reachable set contain the WINNING/SUPPORTING clue for a dimension?

    A decoy fact of the same type (e.g. a MOTIVE_FACT_CONTRADICTED for an
    EXCLUDED motive) must NOT satisfy the dimension while the winning supporting
    fact is unreachable. When no proof exists, fall back to type-based coverage
    (Gate 1/2 owns solvability).
    """
    if proof is None:
        return dimension in _reachable_dimensions(reachable_facts)
    facts = tuple(reachable_facts)
    if dimension == DIMENSION_WHO:
        winner = getattr(getattr(proof, "who", None), "winner", None)
        if not isinstance(winner, str) or not winner:
            return False
        if winner in usable_witness_ids:
            return True
        return any(
            str(getattr(prop, "person_id", "")) == winner
            for fact in facts
            for prop in (getattr(fact, "propositions", ()) or ())
        )
    if dimension == DIMENSION_WHY:
        winner = getattr(getattr(proof, "why", None), "winner", None)
        if not isinstance(winner, str) or not winner:
            return False
        return any(
            str(getattr(prop, "type", "")) == MOTIVE_LINKED_TO_PERSON
            and str(getattr(prop, "motive_id", "")) == winner
            for fact in facts
            for prop in (getattr(fact, "propositions", ()) or ())
        )
    if dimension == DIMENSION_WEAPON:
        winner = getattr(getattr(proof, "weapon", None), "winner", None)
        if not isinstance(winner, str) or not winner:
            return False
        for fact in facts:
            for prop in (getattr(fact, "propositions", ()) or ()):
                if str(getattr(prop, "object_id", "")) != winner:
                    continue
                ptype = str(getattr(prop, "type", ""))
                if ptype == FORENSIC_WEAPON_MATCH:
                    structured = getattr(prop, "structured", None) or {}
                    if isinstance(structured, Mapping) and structured.get("match") is True:
                        return True
                elif ptype in (OBJECT_CONTAINS_BLOOD, OBJECT_CONTAINS_FINGERPRINT):
                    return True
        return False
    if dimension == DIMENSION_WHEN:
        crit = set(getattr(getattr(proof, "when", None), "critical_evidence_ids", ()) or ())
        if crit:
            return any(fact.id in crit for fact in facts)
        return dimension in _reachable_dimensions(facts)
    return False


# --------------------------------------------------------------------------- #
# the single deterministic entry point
# --------------------------------------------------------------------------- #


def validate_playability_quality(
    *,
    draft: GeneratedDraft,
    public: PublicCase,
    evidence: Iterable[EvidenceFact],
    universes: CandidateUniverses | None,
    truth: Any,
    difficulty: str | None,
) -> PlayabilityReport:
    """Run Gate 3 over one assembled candidate (pure, ZERO provider/network).

    Never mutates ``draft``/``public``/``evidence``/``universes``/``truth``.
    """
    del universes  # reserved for future derived-universe checks; solver re-derives
    difficulty_name = difficulty_policy.normalize_difficulty(difficulty)
    policy = difficulty_policy.DIFFICULTY_POLICIES[difficulty_name]

    evidence_tuple = tuple(evidence)
    evidence_by_id = _evidence_by_id(evidence_tuple)
    discoverable = _discoverable_facts(evidence_tuple)
    discoverable_ids = frozenset(fact.id for fact in discoverable)

    placements = tuple(getattr(draft.world_graph, "placements", ()) or ())
    interactive = _interactive_placements(draft)
    placement_linked_fact_ids = frozenset(
        str(getattr(p, "evidence_id"))
        for p in interactive
        if getattr(p, "evidence_id", None) is not None
    )

    usable_witness_ids, usable_fact_ids, all_witness_ids = _witness_reachability(
        public, evidence_tuple
    )

    reachable_facts = tuple(
        fact
        for fact in discoverable
        if _fact_reachable(
            fact,
            placement_linked_fact_ids=placement_linked_fact_ids,
            usable_fact_ids=usable_fact_ids,
        )
    )
    reachable_fact_ids = frozenset(fact.id for fact in reachable_facts)

    inspectable_count = len(_inspected_object_ids(draft))
    # ADV-44-03 — RECORD-based meaningful/source accounting (a single record
    # contributes at most one meaningful inspectable / source no matter how many
    # objects link it; multiple records on one object count once per record).
    linked_record_ids, no_link_meaningful_object_ids = _meaningful_accounting(
        draft, discoverable_ids
    )
    meaningful_count = len(linked_record_ids) + len(no_link_meaningful_object_ids)
    distinct_source_count = meaningful_count + len(usable_witness_ids)

    # Solver proof — recomputed deterministically (pure). A candidate that
    # cannot solve is classified by Gate 1/2; here we degrade to proof-less
    # count-only classification rather than raising.
    proof: SolverProof | None = None
    try:
        proof = solve_case(public, evidence_tuple)
    except (TypeError, ValueError):
        proof = None

    red_herring_count = _count_red_herrings(
        placement_linked_fact_ids, evidence_by_id, proof
    )

    required_dimensions = _required_dimensions(truth)
    # ADV-44-07 — each required dimension must be supported by the
    # WINNING/supporting clue (not merely a decoy of the same proposition type).
    if proof is not None:
        reachable_required = tuple(
            dim
            for dim in required_dimensions
            if _winner_dimension_supported(
                dim, reachable_facts, proof, usable_witness_ids
            )
        )
        unreachable_required = tuple(
            dim
            for dim in required_dimensions
            if dim not in reachable_required
        )
    else:
        reachable_dimensions = _reachable_dimensions(reachable_facts)
        reachable_required = tuple(
            dim for dim in required_dimensions if dim in reachable_dimensions
        )
        unreachable_required = tuple(
            dim for dim in required_dimensions if dim not in reachable_dimensions
        )

    issues: set[str] = set()

    if not placements or inspectable_count == 0:
        issues.add(PLAYABILITY_EMPTY_WORLD)

    if inspectable_count < policy.min_inspectable_objects:
        issues.add(PLAYABILITY_TOO_FEW_INSPECTABLES)
    if meaningful_count < policy.min_meaningful_inspectables:
        issues.add(PLAYABILITY_TOO_FEW_MEANINGFUL_INSPECTABLES)
    if red_herring_count < policy.min_red_herrings:
        issues.add(PLAYABILITY_RED_HERRING_REQUIREMENT_UNMET)
    if distinct_source_count < policy.min_distinct_meaningful_sources:
        issues.add(PLAYABILITY_EVIDENCE_OVERCONCENTRATED)

    if unreachable_required:
        issues.add(PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE)

    # ADV-44-01 definitive semantics (§10/§11, architecture-compatible): it is
    # NOT required that every ``evidence_ids_used`` fact be individually placed.
    # What MUST hold is that (i) at least one solver-proof-used evidence fact is
    # reachable at all, AND (ii) every required dimension has at least one
    # reachable clue. A required dimension with no reachable clue also raises
    # ``PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE`` (the per-dimension code);
    # ``PLAYABILITY_EVIDENCE_UNREACHABLE`` additionally fires for the
    # zero-reachable-proof-evidence and missing-dimension-coverage cases.
    if proof is not None:
        proof_ids = frozenset(getattr(proof, "evidence_ids_used", ()) or ())
        if not (proof_ids & reachable_fact_ids):
            issues.add(PLAYABILITY_EVIDENCE_UNREACHABLE)
        if unreachable_required:
            issues.add(PLAYABILITY_EVIDENCE_UNREACHABLE)

    # A published witness with no usable answer (relied on for playability).
    if all_witness_ids and not usable_witness_ids:
        issues.add(PLAYABILITY_WITNESS_CONTENT_UNREACHABLE)

    if issues:
        issues.add(PLAYABILITY_DIFFICULTY_CONTRACT_UNMET)

    return PlayabilityReport(
        difficulty=difficulty_name,
        inspectable_count=inspectable_count,
        meaningful_inspectable_count=meaningful_count,
        distinct_meaningful_source_count=distinct_source_count,
        red_herring_count=red_herring_count,
        reachable_critical_dimensions=reachable_required,
        unreachable_critical_dimensions=unreachable_required,
        issue_codes=tuple(sorted(issues)),
    )


def validate_published_candidate(
    published: "PublishedCaseVersion", difficulty: str | None
) -> PlayabilityReport:
    """Gate 3 over the FROZEN published candidate (the exact persisted payload)."""
    return validate_playability_quality(
        draft=published.draft,
        public=published.public,
        evidence=tuple(published.evidence),
        universes=published.universes,
        truth=published.truth,
        difficulty=difficulty,
    )


def world_graph_playability_issues(attempt: Any) -> tuple[str, ...]:
    """Stage-local Gate 3 for the WORLD_GRAPH stage gate.

    Assembles the current draft/public/evidence/truth from the attempt and runs
    Gate 3. Returns ``()`` (fail-open) ONLY when the draft cannot be ASSEMBLED
    (a shape error owned by the strict parser / structural bucket). Any
    UNEXPECTED validator exception is FAIL-CLOSED (ADV-44-06): the stage is
    reported NOT trusted instead of being silently trusted.
    """
    from app.generation import pipeline  # local import: pipeline imports us

    try:
        public, evidence, truth, draft = pipeline.assemble(attempt)
    except (TypeError, ValueError):
        return ()  # assembly shape error: parser/structural owns it
    try:
        report = validate_playability_quality(
            draft=draft,
            public=public,
            evidence=evidence,
            universes=derive_universes(public),
            truth=truth,
            difficulty=getattr(attempt, "difficulty", None),
        )
    except Exception:  # noqa: BLE001 - ADV-44-06 fail-closed stage gate
        return (PLAYABILITY_DIFFICULTY_CONTRACT_UNMET,)
    return report.issue_codes


__all__ = [
    "DIMENSION_WEAPON",
    "DIMENSION_WHEN",
    "DIMENSION_WHO",
    "DIMENSION_WHY",
    "PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE",
    "PLAYABILITY_DIFFICULTY_CONTRACT_UNMET",
    "PLAYABILITY_EMPTY_WORLD",
    "PLAYABILITY_EVIDENCE_OVERCONCENTRATED",
    "PLAYABILITY_EVIDENCE_UNREACHABLE",
    "PLAYABILITY_ISSUE_CODES",
    "PLAYABILITY_RED_HERRING_REQUIREMENT_UNMET",
    "PLAYABILITY_TOO_FEW_INSPECTABLES",
    "PLAYABILITY_TOO_FEW_MEANINGFUL_INSPECTABLES",
    "PLAYABILITY_WITNESS_CONTENT_UNREACHABLE",
    "PlayabilityReport",
    "validate_playability_quality",
    "validate_published_candidate",
    "world_graph_playability_issues",
]
