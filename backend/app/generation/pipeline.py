"""Deterministic generation pipeline (Phase4 E) and full validation (Phase4 31/47A).

This module owns the *working record* of one generation attempt
(``AttemptRecord``), prompt normalization, stage output application, the
phase-3 reassembly of a parsed ``GeneratedDraft`` (the functional equivalent
of ``fixtures.golden_generation.assemble_phase3`` — implemented here so the
production package never imports test fixtures), and the complete deterministic
``validate_draft`` pipeline:

  a. cross-reference STRUCTURAL checks (evidence proposition ids, world-graph
     placements, crime.* ids) — the checks the strict parser defers;
  b. generated-content SAFETY checks (content scan over the whole
     player-visible draft, asset-reference registry per assetId, world-graph
     allowlists + referential integrity);
  c. candidate universes non-empty (REQUIREMENTS 31.1);
  d. truth-independent deduction (``solve_case``) + the ONLY truth-aware step
     (``evaluate_solution``) + the §31.7 accepted-scoring time proof checks;
  e. locked user constraints (``LockedConstraints.violations_against``).

Validation makes ZERO provider calls; it is fully deterministic and safe to
rerun after every repair (REQUIREMENTS 32.13 — a repaired draft must pass the
COMPLETE validation suite again, never incremental trust).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.domain.eligibility import derive_universes
from app.domain.evidence import EvidenceFact, validate_evidence
from app.domain.proof import SolverProof
from app.domain.public import (
    PublicCase,
    PublicLocation,
    PublicMotive,
    PublicObject,
    PublicPerson,
    PublicScene,
    PublicTravelRule,
)
from app.domain.solver import solve_case
from app.domain.truth import CaseTruth, Crime, CrimeTime
from app.generation import parser, prompt as prompt_mod, safety
from app.generation.budgets import BudgetTracker
from app.generation.constraints import LockedConstraints
from app.generation.provider import GenerateRequest, GenerationStage
from app.generation.report import ValidationReport
from app.generation.schemas import GeneratedDraft, WorldGraphSpec
from app.generation.state_machine import GenerationState
from app.validation.solution import evaluate_solution

# The four staged GENERATING stages in pipeline order (Phase4 E).
STAGE_ORDER: tuple[GenerationStage, ...] = (
    GenerationStage.CASE_TRUTH,
    GenerationStage.PUBLIC_WORLD,
    GenerationStage.EVIDENCE,
    GenerationStage.WORLD_GRAPH,
)

_FALLBACK_TITLE = "Untitled Case"


@dataclass
class AttemptRecord:
    """Mutable per-attempt working record (NOT part of the published payload).

    Lives here (not in the controller) so the pipeline can operate on it
    without a circular import; the controller owns the lifecycle around it.
    """

    attempt_id: str
    case_id: str
    session_id: str
    state: GenerationState = GenerationState.DRAFT
    seed: int | None = None
    prompt: str = ""
    prompt_note: str = ""
    locked: LockedConstraints = field(default_factory=LockedConstraints)
    budget: BudgetTracker | None = None
    # Accumulated per-stage outputs (only the four GENERATING stages).
    stage_outputs: dict[GenerationStage, Any] = field(default_factory=dict)
    stage_done: set[GenerationStage] = field(default_factory=set)
    # Structural issues deferred by the strict parser (stage parse failures).
    deferred_structural: tuple[str, ...] = ()
    # The assembled full draft (rebuilt from stage outputs, or replaced by a
    # successful REPAIR full-draft parse).
    draft: GeneratedDraft | None = None
    # Per-draft phase-3 assembly cache: (draft_identity, public, evidence, truth).
    _phase3_cache: tuple[Any, ...] | None = None
    solver_proof: SolverProof | None = None
    last_validation: ValidationReport | None = None
    reason: str | None = None
    pending_id: str | None = None
    pending_stage: GenerationStage | None = None
    published: Any | None = None  # PublishedCaseVersion (frozen) once published
    published_at: float | None = None
    _admission_released: bool = False


# ---------------------------------------------------------------------------
# prompt normalization (REQUIREMENTS 33)
# ---------------------------------------------------------------------------


def normalize_prompt(
    prompt: str, *, max_chars: int
) -> tuple[LockedConstraints, str]:
    """Normalize a structured prompt into locked constraints + a note.

    Delegates to the primitives' ``prompt.parse_prompt`` which enforces
    ``max_chars`` (raises ``PromptError`` when exceeded) and duplicate keys.
    """
    return prompt_mod.parse_prompt(prompt, max_chars=max_chars)


# ---------------------------------------------------------------------------
# draft assembly from accumulated stage outputs
# ---------------------------------------------------------------------------


def current_draft(attempt: AttemptRecord) -> GeneratedDraft | None:
    """Return the attempt's current ``GeneratedDraft`` (staging identity).

    When a full-draft REPAIR replaced ``attempt.draft`` it is used directly;
    otherwise the four stage outputs are joined. Returns None only when the
    CASE_TRUTH stage never produced a valid crime (draft impossible).
    """
    if attempt.draft is not None:
        return attempt.draft
    crime = attempt.stage_outputs.get(GenerationStage.CASE_TRUTH)
    if crime is None:
        return None
    public_world = attempt.stage_outputs.get(GenerationStage.PUBLIC_WORLD)
    evidence_set = attempt.stage_outputs.get(GenerationStage.EVIDENCE)
    world_graph: WorldGraphSpec = attempt.stage_outputs.get(
        GenerationStage.WORLD_GRAPH, WorldGraphSpec()
    )
    draft = GeneratedDraft(
        crime=crime,
        persons=public_world.persons if public_world is not None else (),
        motives=public_world.motives if public_world is not None else (),
        objects=public_world.objects if public_world is not None else (),
        locations=public_world.locations if public_world is not None else (),
        travel_rules=public_world.travel_rules if public_world is not None else (),
        scene=public_world.scene if public_world is not None else None,
        evidence=evidence_set.evidence if evidence_set is not None else (),
        world_graph=world_graph,
    )
    attempt.draft = draft
    return draft


def _title_for_prompt(prompt: str) -> str:
    first = ""
    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        if line:
            first = line
            break
    if not first:
        return _FALLBACK_TITLE
    return first[:120]


# ---------------------------------------------------------------------------
# phase-3 reassembly (functional equivalent of golden assemble_phase3)
# ---------------------------------------------------------------------------


def _draft_to_phase3(
    draft: GeneratedDraft, *, case_id: str, title: str
) -> tuple[PublicCase, tuple[EvidenceFact, ...], CaseTruth]:
    """Reassemble a parsed draft into the Phase 3 objects.

    Structurally equivalent to ``fixtures.golden_generation.assemble_phase3``
    (mapping a ``GeneratedDraft`` back into ``PublicCase`` + evidence facts +
    ``CaseTruth``) but generic over the draft: the case identity comes from
    the attempt (``caseId`` from IdSource, ``caseVersion = 1``) instead of the
    golden constants. This keeps the production package free of test-fixture
    imports while the golden fixtures independently verify the conversion.
    """
    persons = tuple(
        PublicPerson(
            person_id=spec.person_id,
            name=spec.name,
            role=spec.role,
            public_affordances=frozenset(spec.affordances),
            presented_data=dict(spec.presented_data) if spec.presented_data else {},
        )
        for spec in draft.persons
    )
    motives = tuple(
        PublicMotive(
            motive_id=spec.motive_id,
            label=spec.label,
            public_affordances=frozenset(spec.affordances),
        )
        for spec in draft.motives
    )
    objects = tuple(
        PublicObject(
            object_id=spec.object_id,
            asset_id=spec.asset_id,
            public_affordances=frozenset(spec.affordances),
            subtype=spec.subtype,
        )
        for spec in draft.objects
    )
    locations = tuple(
        PublicLocation(location_id=spec.location_id, name=spec.name)
        for spec in draft.locations
    )
    travel_rules = tuple(
        PublicTravelRule(
            from_location_id=spec.from_location_id,
            to_location_id=spec.to_location_id,
            travel_time_seconds=spec.travel_time_seconds,
        )
        for spec in draft.travel_rules
    )
    scene = (
        None
        if draft.scene is None
        else PublicScene(location_id=draft.scene.location_id, name=draft.scene.name)
    )
    public = PublicCase(
        case_id=case_id,
        case_version=1,
        persons=persons,
        motives=motives,
        objects=objects,
        locations=locations,
        travel_rules=travel_rules,
        scene=scene,
    )
    evidence = tuple(
        EvidenceFact(
            id=spec.id,
            kind=spec.kind,
            propositions=tuple(p._as_typed_proposition() for p in spec.propositions),
            source_ref=(
                None
                if spec.source_ref is None
                else _source_ref(spec.source_ref)
            ),
            reliability=spec.reliability,
            presentation=dict(spec.presentation) if spec.presentation else {},
            discoverable=spec.discoverable,
        )
        for spec in draft.evidence
    )
    crime_spec = draft.crime
    truth = CaseTruth(
        case_id=case_id,
        case_version=1,
        title=title,
        crime=Crime(
            type=crime_spec.type,
            victim_id=crime_spec.victim_id,
            murderer_id=crime_spec.murderer_id,
            motive_id=crime_spec.motive_id,
            weapon_id=crime_spec.weapon_id,
            location_id=crime_spec.location_id,
            crime_time=CrimeTime(
                canonical=crime_spec.crime_time.canonical,
                accusation_tolerance_seconds=crime_spec.crime_time.accusation_tolerance_seconds,
            ),
        ),
        timeline=(),
        persons=(),
        relationships=(),
        facts=(),
    )
    return public, evidence, truth


def _source_ref(mapping: Mapping[str, Any]) -> Any:
    from app.domain.evidence import SourceRef  # local import avoids top-weight

    return SourceRef(
        kind=mapping.get("kind", ""),
        source_id=mapping.get("sourceId", ""),
    )


def assemble(
    attempt: AttemptRecord,
) -> tuple[PublicCase, tuple[EvidenceFact, ...], CaseTruth, GeneratedDraft]:
    """Build the Phase 3 objects from the attempt's current draft (cached).

    Returns ``(PublicCase, tuple[EvidenceFact, ...], CaseTruth, GeneratedDraft)``.
    Raises ``ValueError`` when the draft is incomplete (no crime yet).
    """
    draft = current_draft(attempt)
    if draft is None:
        raise ValueError("attempt has no assembled draft (case truth missing)")
    cache = attempt._phase3_cache
    if cache is not None and cache[0] is draft:
        return cache[1], cache[2], cache[3], draft
    public, evidence, truth = _draft_to_phase3(
        draft, case_id=attempt.case_id, title=_title_for_prompt(attempt.prompt)
    )
    attempt._phase3_cache = (draft, public, evidence, truth)
    return public, evidence, truth, draft


# ---------------------------------------------------------------------------
# structural cross-reference checks
# ---------------------------------------------------------------------------


def _crime_resolution_issues(draft: GeneratedDraft) -> tuple[str, ...]:
    """Crime.* ids must resolve inside the draft's public data (structural)."""
    issues: list[str] = []
    person_ids = {p.person_id for p in draft.persons}
    motive_ids = {m.motive_id for m in draft.motives}
    object_ids = {o.object_id for o in draft.objects}
    location_ids = {l.location_id for l in draft.locations}
    crime = draft.crime
    checks = (
        ("victimId", crime.victim_id, person_ids, "a person"),
        ("murdererId", crime.murderer_id, person_ids, "a person"),
        ("motiveId", crime.motive_id, motive_ids, "a motive"),
        ("weaponId", crime.weapon_id, object_ids, "an object"),
        ("locationId", crime.location_id, location_ids, "a location"),
    )
    for field_name, value, known, expected in checks:
        if value not in known:
            issues.append(
                f"crime.{field_name} {value!r} does not resolve to {expected} "
                "in the generated draft"
            )
    return tuple(sorted(set(issues)))


def _world_graph_resolution_issues(draft: GeneratedDraft) -> tuple[str, ...]:
    """World-graph placements must reference known object/location/evidence ids."""
    issues: list[str] = []
    object_ids = {o.object_id for o in draft.objects}
    evidence_ids = {e.id for e in draft.evidence}
    wg_locations = {loc.location_id for loc in draft.world_graph.locations}
    for index, placement in enumerate(draft.world_graph.placements):
        where = f"world_graph.placements[{index}]"
        if placement.object_id not in object_ids:
            issues.append(
                f"{where}: unknown objectId {placement.object_id!r}"
            )
        if placement.location_id not in wg_locations:
            issues.append(
                f"{where}: unknown locationId {placement.location_id!r}"
            )
        if placement.evidence_id is not None and placement.evidence_id not in evidence_ids:
            issues.append(
                f"{where}: evidenceId {placement.evidence_id!r} is not a known "
                "evidence id"
            )
    return tuple(sorted(set(issues)))


# ---------------------------------------------------------------------------
# stage drivers
# ---------------------------------------------------------------------------


def _stage_context(attempt: AttemptRecord, stage: GenerationStage) -> str:
    """Safe, deterministic textual projection of accumulated material.

    Contains ONLY sanitized generated material + locked constraints — never a
    CaseTruth object and never hidden solver internals.
    """
    if stage is GenerationStage.REPAIR:
        material: Any = attempt.draft
        if material is None:
            material = dict(attempt.stage_outputs)
        return safety.sanitize_for_repair(material)
    material: dict[str, Any] = {"prompt": attempt.prompt}
    if attempt.prompt_note:
        material["promptNote"] = attempt.prompt_note
    if attempt.locked is not None:
        locked_fields = {
            key: value for key, value in attempt.locked.locked_fields() if value is not None
        }
        if locked_fields:
            material["lockedConstraints"] = locked_fields
    if stage is not GenerationStage.CASE_TRUTH:
        crime = attempt.stage_outputs.get(GenerationStage.CASE_TRUTH)
        if crime is not None:
            material["caseTruth"] = crime
    if stage in (GenerationStage.EVIDENCE, GenerationStage.WORLD_GRAPH):
        public_world = attempt.stage_outputs.get(GenerationStage.PUBLIC_WORLD)
        if public_world is not None:
            material["publicWorld"] = public_world
    if stage is GenerationStage.WORLD_GRAPH:
        evidence_set = attempt.stage_outputs.get(GenerationStage.EVIDENCE)
        if evidence_set is not None:
            material["evidence"] = evidence_set
    return safety.sanitize_for_repair(material)


def build_request(
    attempt: AttemptRecord,
    stage: GenerationStage,
    *,
    diagnostics: tuple[str, ...] = (),
) -> GenerateRequest:
    """Build the next provider invocation for ``stage`` (sanitized material)."""
    return GenerateRequest(
        attempt_id=attempt.attempt_id,
        stage=stage,
        prompt_context=_stage_context(attempt, stage),
        locked=attempt.locked,
        diagnostics=tuple(diagnostics),
        seed=attempt.seed,
    )


def apply_stage_output(attempt: AttemptRecord, stage: GenerationStage, content: str) -> None:
    """Apply one GENERATING stage's provider output (parse -> accumulate).

    Parse issues are collected (never coerced) into the attempt's deferred
    structural issues and feed the full validation later.
    """
    if stage is GenerationStage.REPAIR:
        apply_repair(attempt, content)
        return
    spec = parser.parse_stage(stage, content, non_throwing=True)
    if spec is None:
        attempt.deferred_structural = tuple(
            sorted(set(attempt.deferred_structural) | set(parser.collect_issues(stage, content)))
        )
    else:
        attempt.stage_outputs[stage] = spec
        attempt.draft = None
        attempt._phase3_cache = None
    attempt.stage_done.add(stage)


def apply_repair(attempt: AttemptRecord, content: str) -> tuple[str, ...]:
    """Apply a REPAIR (full-draft) provider output.

    A failed full-draft parse counts as a failed repair (structural issues are
    recorded, the previous draft is kept). A successful parse REPLACES the
    whole draft and clears stale deferred issues; the caller MUST rerun the
    complete validation suite afterwards (REQUIREMENTS 32.13).
    """
    issues = parser.collect_full_draft_issues(content)
    if issues:
        attempt.deferred_structural = tuple(
            sorted(set(attempt.deferred_structural) | set(issues))
        )
        return tuple(sorted(set(issues)))
    draft = parser.parse_full_draft(content, non_throwing=True)
    attempt.draft = draft
    attempt.stage_outputs = {}
    attempt.stage_done = set()
    attempt.deferred_structural = ()
    attempt._phase3_cache = None
    return ()


# ---------------------------------------------------------------------------
# full deterministic validation (zero provider calls)
# ---------------------------------------------------------------------------


def _whole_draft_safety_issues(draft: GeneratedDraft) -> tuple[str, ...]:
    """Content-safety scan over the whole player-visible draft.

    The primitives' ``sanitize_for_repair`` produces the deterministic plain
    JSON projection of the draft tree; scanning that text covers every
    generated string AND mapping key with the same forbidden-token vocabulary.
    """
    projection = safety.sanitize_for_repair(draft)
    return safety.validate_content_safety(projection)


def _asset_reference_issues(draft: GeneratedDraft) -> tuple[str, ...]:
    issues: list[str] = []
    for spec in draft.objects:
        if safety.is_procedural_asset_id(spec.asset_id):
            # Phase 13: procedural ids are NOT registry assets; their validity
            # is bound to the world-graph placement's embedded definition
            # (enforced by ``safety.validate_procedural_placement``).
            continue
        issues.extend(safety.validate_asset_reference(spec.asset_id))
    for placement in draft.world_graph.placements:
        if safety.is_procedural_asset_id(placement.asset_id):
            continue
        issues.extend(safety.validate_asset_reference(placement.asset_id))
    return tuple(sorted(set(issues)))


def _incomplete_draft_report(deferred: tuple[str, ...] = ()) -> ValidationReport:
    structural = tuple(
        sorted(
            set(deferred)
            | {"draft is incomplete: the case truth stage never produced a valid crime"}
        )
    )
    return ValidationReport(structural_issues=structural)


def validate_draft(attempt: AttemptRecord) -> ValidationReport:
    """Run the COMPLETE deterministic validation suite over the current draft.

    Never calls the provider. May safely be rerun after every repair or
    regeneration. EVERY exit path stores the report on ``attempt`` so the
    controller's observability (``attempt.last_validation``) and the FAILED
    reason string are always consistent with what validation decided.
    """
    draft = current_draft(attempt)
    if draft is None:
        report = _incomplete_draft_report(attempt.deferred_structural)
        attempt.solver_proof = None
        attempt.last_validation = report
        return report

    # (a) structural cross-references ---------------------------------------
    structural: list[str] = list(attempt.deferred_structural)
    public: PublicCase | None = None
    evidence: tuple[EvidenceFact, ...] = ()
    truth: CaseTruth | None = None
    try:
        public, evidence, truth, _ = assemble(attempt)
    except (TypeError, ValueError):
        structural.append("draft assembly failed: generated draft cannot become a public case")
    if public is not None:
        structural.extend(validate_evidence(public, evidence))
    structural.extend(_crime_resolution_issues(draft))
    structural.extend(_world_graph_resolution_issues(draft))
    structural_tuple = tuple(sorted(set(structural)))

    # (b) generated-content safety ------------------------------------------
    safety_issues = list(_whole_draft_safety_issues(draft))
    safety_issues.extend(_asset_reference_issues(draft))
    safety_issues.extend(
        safety.validate_world_graph(
            draft.world_graph,
            {o.object_id for o in draft.objects},
            {e.id for e in draft.evidence},
        )
    )
    safety_tuple = tuple(sorted(set(safety_issues)))

    # (c) candidate universes -----------------------------------------------
    universe_issues: list[str] = []
    if public is None:
        universe_issues.append("candidate universes unavailable: public case incomplete")
    else:
        universes = derive_universes(public)
        if not universes.suspect_ids:
            universe_issues.append("suspect universe is empty")
        if not universes.motive_ids:
            universe_issues.append("motive universe is empty")
        if not universes.weapon_ids:
            universe_issues.append("weapon universe is empty")
    universe_tuple = tuple(sorted(set(universe_issues)))

    # (d) solver + truth-aware comparison ------------------------------------
    solver_result: SolverProof | None = None
    validation = None
    if public is not None and truth is not None and not (
        structural_tuple or safety_tuple or universe_tuple
    ):
        try:
            proof = solve_case(public, evidence)
            if (
                proof.who is None
                or proof.why is None
                or proof.weapon is None
                or proof.when is None
            ):
                structural.append("solver failure: deduction returned an incomplete proof")
            else:
                solver_result = proof
                validation = evaluate_solution(proof, truth)
        except (TypeError, ValueError):
            structural.append(
                "solver failure: deduction did not complete on the generated evidence"
            )
        structural_tuple = tuple(sorted(set(structural)))

    # (e) locked user constraints --------------------------------------------
    # A locked comparison is only meaningful against a structurally coherent
    # draft: when structural issues exist (e.g. the public-world stage failed
    # and no persons exist), "witness not found" is an artifact of the broken
    # draft, not a locked-constraint violation — the draft must be repairable.
    # A structurally-complete draft that truly violates a lock is terminal.
    locked_violations = ()
    if attempt.locked is not None and not structural_tuple:
        locked_violations = attempt.locked.violations_against(draft)

    report = ValidationReport(
        structural_issues=structural_tuple,
        safety_issues=safety_tuple,
        universe_issues=universe_tuple,
        solver_result=solver_result,
        validation=validation,
        locked_violations=locked_violations,
    )
    attempt.solver_proof = solver_result
    attempt.last_validation = report
    return report


def time_proof_checks_passed(validation: Any) -> bool:
    """§31.8/31.7 time-proof summary: single connected, in scoring, canonical inside.

    Present for caller convenience/tests; the same conditions are already
    encoded in ``AccusedSolutionValidation.time_accepted``.
    """
    if validation is None:
        return False
    return bool(
        validation.time_unique_single_interval
        and validation.time_within_scoring
        and validation.canonical_in_feasible
        and not validation.time_overconstrained
    )


__all__ = [
    "STAGE_ORDER",
    "AttemptRecord",
    "assemble",
    "apply_repair",
    "apply_stage_output",
    "build_request",
    "current_draft",
    "normalize_prompt",
    "time_proof_checks_passed",
    "validate_draft",
    "_draft_to_phase3",
]