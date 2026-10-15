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

import json
from dataclasses import dataclass, field, replace
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
from app.generation import (
    case_quality,
    evidence_algebra,
    parser,
    prompt as prompt_mod,
    prompts,
    safety,
    trusted_context,
)
from app.generation.budgets import BudgetTracker
from app.generation.constraints import LockedConstraints
from app.generation.provider import GenerateRequest, GenerationStage
from app.generation.report import ValidationReport
from app.generation.schemas import GeneratedDraft, WorldGraphSpec
from app.generation.state_machine import GenerationState
from app.validation.solution import evaluate_solution
from app.core.observability import emit_event

# The four staged GENERATING stages in pipeline order (Phase4 E).
STAGE_ORDER: tuple[GenerationStage, ...] = (
    GenerationStage.CASE_TRUTH,
    GenerationStage.PUBLIC_WORLD,
    GenerationStage.EVIDENCE,
    GenerationStage.WORLD_GRAPH,
)

_FALLBACK_TITLE = "Untitled Case"

# Phase33 RAD-2 — the stages whose model-facing context receives the
# authoritative generation scope (asset/anchor/interaction registries and, for
# the persons-bearing stages, the locked-witness contract). The scope is
# rendered from the SAME application-owned registries the validators enforce;
# it is guidance only — every rejection semantics stays byte-identical.
_SCOPE_STAGES = frozenset(
    {
        GenerationStage.PUBLIC_WORLD,
        GenerationStage.WORLD_GRAPH,
        GenerationStage.REPAIR,
    }
)
_WITNESS_SCOPE_STAGES = frozenset(
    {GenerationStage.PUBLIC_WORLD, GenerationStage.REPAIR}
)

# Phase40 §20 — the closed candidate-integrity quality codes whose current
# public candidate membership is INVALID. When one is present in the repair
# context's report, the context never echoes the current (invalid) suspect
# candidate ids as authoritative provider guidance; it prints the REQUIRED
# CANDIDATE-INTEGRITY RULES instead (rule-only, truth-safe; never the hidden
# murderer identity).
_CANDIDATE_INTEGRITY_QUALITY_CODES: frozenset[str] = frozenset(
    {
        case_quality.VICTIM_IN_SUSPECT_CANDIDATES,
        case_quality.WITNESS_IN_SUSPECT_CANDIDATES,
        case_quality.MURDERER_NOT_SUSPECT_CANDIDATE,
        case_quality.HOMICIDE_PERPETRATOR_NOT_FINAL_TARGET,
    }
)


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
    # Phase43A (P43A-FR-08) — the SAME deferred parser issues attributed to the
    # stage that produced them. ``deferred_structural`` stays the flat union
    # (unchanged validation/classification authority); this per-stage map lets
    # the deterministic evidence completion treat an EVIDENCE-stage parse
    # failure as algebra-owned WITHOUT masking a CASE_TRUTH / PUBLIC_WORLD /
    # WORLD_GRAPH / REPAIR structural/safety/universe/locked/quality defect.
    deferred_by_stage: dict[GenerationStage, tuple[str, ...]] = field(
        default_factory=dict
    )
    # The assembled full draft (rebuilt from stage outputs, or replaced by a
    # successful REPAIR full-draft parse).
    draft: GeneratedDraft | None = None
    # Per-draft phase-3 assembly cache: (draft_identity, public, evidence, truth).
    _phase3_cache: tuple[Any, ...] | None = None
    solver_proof: SolverProof | None = None
    last_validation: ValidationReport | None = None
    reason: str | None = None
    # Canonical primary terminal code; internal detail remains in ``reason``.
    failure_code: str | None = None
    pending_id: str | None = None
    pending_stage: GenerationStage | None = None
    published: Any | None = None  # PublishedCaseVersion (frozen) once published
    published_at: float | None = None
    _admission_released: bool = False
    # Phase31A (Track B) — transient validator-code tuple that triggered the
    # most recent repair (set by the controller before a REPAIR starts,
    # consumed on the very next validation pass to emit the bounded
    # ``generation.repair.outcome`` delta). Diagnostics only — never read by
    # validators/solvers/budgets and never leaves the attempt.
    _repair_before_codes: tuple[str, ...] | None = None
    # Phase41 — hidden driver-owned witness-truthfulness stamp (statement-
    # level, trusted). Set by the driver chokepoint from ``(crime, public)``;
    # lifted into ``CaseTruth.witness_truthfulness`` by ``_draft_to_phase3``.
    # NEVER serialized into the draft, NEVER a public DTO / telemetry field.
    _witness_truthfulness: tuple[Any, ...] = ()
    # Phase43 (P43-FR-08) — REPAIR scope snapshot. ``build_request`` records
    # the pre-repair declaration ids + the owner-authorized section set so
    # ``apply_repair`` can reject unauthorized identifier churn. Both are
    # attempt-local, never serialized, never emitted; cleared on regeneration.
    _repair_pre_ids: dict[str, tuple[str, ...]] | None = None
    _repair_authorized_sections: frozenset[str] | None = None
    # Phase43A — the generic-path deterministic evidence completion runs AT MOST
    # ONCE per attempt (never a loop). Set when the EVIDENCE stage completes;
    # cleared on regeneration so a fresh attempt gets a fresh completion.
    _evidence_completed: bool = False


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
    draft: GeneratedDraft,
    *,
    case_id: str,
    title: str,
    witness_truthfulness: tuple[Any, ...] = (),
) -> tuple[PublicCase, tuple[EvidenceFact, ...], CaseTruth]:
    """Reassemble a parsed draft into the Phase 3 objects.

    Structurally equivalent to ``fixtures.golden_generation.assemble_phase3``
    (mapping a ``GeneratedDraft`` back into ``PublicCase`` + evidence facts +
    ``CaseTruth``) but generic over the draft: the case identity comes from
    the attempt (``caseId`` from IdSource, ``caseVersion = 1``) instead of the
    golden constants. This keeps the production package free of test-fixture
    imports while the golden fixtures independently verify the conversion.

    Phase41: the draft's optional trusted ``resolution_type`` and the hidden
    ``witness_truthfulness`` stamp (driver-side only; NEVER provider-parsed)
    are lifted into the ``CaseTruth`` — the trusted truth path.
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
            resolution_type=crime_spec.resolution_type,
        ),
        timeline=(),
        persons=(),
        relationships=(),
        facts=(),
        witness_truthfulness=tuple(witness_truthfulness),
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
        draft,
        case_id=attempt.case_id,
        title=_title_for_prompt(attempt.prompt),
        witness_truthfulness=tuple(getattr(attempt, "_witness_truthfulness", ()) or ()),
    )
    attempt._phase3_cache = (draft, public, evidence, truth)
    return public, evidence, truth, draft


# ---------------------------------------------------------------------------
# structural cross-reference checks
# ---------------------------------------------------------------------------


def _crime_resolution_issues(draft: GeneratedDraft) -> tuple[str, ...]:
    """Crime.* ids must resolve inside the draft's public data (structural).

    Phase41 (§15/§32): the ``murdererId -> person`` resolution check is
    HOMICIDE-only. A SUICIDE case has ``murderer_id=None`` by contract, so the
    murderer must be SKIPPED (never a dangling-reference "None" issue) while
    victim/motive/weapon/location still resolve.
    """
    issues: list[str] = []
    person_ids = {p.person_id for p in draft.persons}
    motive_ids = {m.motive_id for m in draft.motives}
    object_ids = {o.object_id for o in draft.objects}
    location_ids = {l.location_id for l in draft.locations}
    crime = draft.crime
    from app.domain.resolution import ResolutionType

    is_suicide = crime.resolution_type is ResolutionType.SUICIDE
    checks = [
        ("victimId", crime.victim_id, person_ids, "a person"),
        ("motiveId", crime.motive_id, motive_ids, "a motive"),
        ("weaponId", crime.weapon_id, object_ids, "an object"),
        ("locationId", crime.location_id, location_ids, "a location"),
    ]
    if not is_suicide:
        checks.insert(1, ("murdererId", crime.murderer_id, person_ids, "a person"))
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

    Contains ONLY sanitized generated material + locked constraints + the
    authoritative generation scope — never a CaseTruth object and never hidden
    solver internals.

    Phase33 RAD-2: the PUBLIC_WORLD / WORLD_GRAPH / REPAIR stage contexts also
    carry the authoritative scope block (``prompts.generation_scope_block`` —
    the registry-backed asset/anchor/interaction vocabularies) and the
    persons-bearing stages carry the locked-witness contract line; both solve
    the Phase33 RAD-1 "missing trusted context" / "ambiguous guidance" gaps
    without changing a single validation semantics.

Phase35 §37: the REPAIR stage additionally receives the bounded safe
    CASE-QUALITY FIX CONTEXT — allowed person IDs + public roles, witness IDs,
    suspect candidate IDs, the required witness-kind evidence vocabulary and
    the safe quality issue codes from the report. Never CaseTruth/murderer
    through public diagnostics; the canonical truth stays server-side.

    Phase43 (P43-FR-03/07/10): every controller stage additionally renders an
    AUTHORITATIVE provider-neutral block (``_authoritative_stage_block``) built
    from ``trusted_context`` + already-parsed validated stage outputs — the
    canonical locked-ID sheet, the immutable crime ids, the approved public /
    travel / deduction material, the ONE selected environment kit + its
    kit-scoped anchors/assets, and the provider-facing camelCase repair shape —
    so the generic Frontier path carries the same identity/quality/world
    contracts the Ollama driver already consumes. Still NEVER a CaseTruth
    object or hidden solver internals.
    """
    if stage is GenerationStage.REPAIR:
        material: Any = attempt.draft
        if material is None:
            material = dict(attempt.stage_outputs)
            text = safety.sanitize_for_repair(material)
        else:
            # Phase43 (P43-FR-07) — present the provider-facing camelCase
            # protocol shape so the model sees ONE canonical namespace.
            text = provider_facing_draft_json(material)
    else:
        material_context: dict[str, Any] = {"prompt": attempt.prompt}
        if attempt.prompt_note:
            material_context["promptNote"] = attempt.prompt_note
        if attempt.locked is not None:
            locked_fields = {
                key: value for key, value in attempt.locked.locked_fields() if value is not None
            }
            if locked_fields:
                material_context["lockedConstraints"] = locked_fields
        if stage is not GenerationStage.CASE_TRUTH:
            crime = attempt.stage_outputs.get(GenerationStage.CASE_TRUTH)
            if crime is not None:
                material_context["caseTruth"] = crime
        if stage in (GenerationStage.EVIDENCE, GenerationStage.WORLD_GRAPH):
            public_world = attempt.stage_outputs.get(GenerationStage.PUBLIC_WORLD)
            if public_world is not None:
                material_context["publicWorld"] = public_world
        if stage is GenerationStage.WORLD_GRAPH:
            evidence_set = attempt.stage_outputs.get(GenerationStage.EVIDENCE)
            if evidence_set is not None:
                material_context["evidence"] = evidence_set
        text = safety.sanitize_for_repair(material_context)
    # Phase43 (P43-FR-03) — the per-stage AUTHORITATIVE canonical-ID / quality
    # / world block (provider-neutral, built from trusted state only).
    authoritative = _authoritative_stage_block(attempt, stage)
    if authoritative:
        text = f"{text}\n\n{authoritative}"
    if stage in _SCOPE_STAGES:
        # Phase43 (P43-FR-10 / DEF-063) — the WORLD_GRAPH and REPAIR contexts
        # are KIT-SCOPED: only the resolved selected environment kit's anchors
        # are offered; other kits' anchors are never presented as options. The
        # PUBLIC_WORLD context keeps the original (no-arg) scope bytes.
        if stage in (GenerationStage.WORLD_GRAPH, GenerationStage.REPAIR):
            scope = prompts.generation_scope_block(selected_environment_kit(attempt))
        else:
            scope = prompts.generation_scope_block()
        if stage in _WITNESS_SCOPE_STAGES:
            scope += "\n\n" + prompts.locked_witness_contract_line()
        if stage is GenerationStage.REPAIR:
            scope += "\n\n" + case_quality_repair_context(attempt)
        text = f"{text}\n\n{scope}"
    return text


def case_quality_repair_context(attempt: AttemptRecord) -> str:
    """Phase35 §37 — bounded safe CASE-QUALITY FIX CONTEXT for the REPAIR stage.

    Only PUBLIC/derive-facing values (allowed person IDs + closed public roles,
    witness IDs, suspect candidate IDs, the required witness-kind evidence
    vocabulary and the CLOSED safe quality issue codes). NEVER CaseTruth /
    murderer identity / statement text through public diagnostics — roles are
    projected through ``PUBLIC_ROLE_VOCABULARY`` so a truth-bearing token
    (e.g. ``"murderer"``) carried by a broken draft is masked as ``<invalid>``
    instead of re-echoed (DEF-080). Deterministic and bounded; guidance only —
    the validators stay the sole acceptance authority.
    """
    lines: list[str] = ["CASE QUALITY FIX CONTEXT (bounded, authoritative):"]
    persons: tuple[Any, ...] = ()
    public: PublicCase | None = None
    try:
        public, _evidence, _truth, _draft = assemble(attempt)
    except Exception:  # noqa: BLE001 - context degrades safely
        public = None
    report = getattr(attempt, "last_validation", None)
    quality_codes = tuple(getattr(report, "quality_issues", ()) or ())
    # Phase40 §20 — the current public candidate membership is INVALID exactly
    # when the report carries a candidate-integrity code; it must NEVER be
    # passed back as authoritative provider guidance (that contradicting
    # "accuseable set" line was the ambiguity root cause of the Easy
    # Local-Ollama regression). The fallback guidance then distinguishes:
    #   CURRENT INVALID PUBLIC CANDIDATE MEMBERSHIP (not echoed)
    #   vs REQUIRED CANDIDATE-INTEGRITY RULES (echoed, rule-only, truth-safe)
    candidate_integrity_present = any(
        code in _CANDIDATE_INTEGRITY_QUALITY_CODES for code in quality_codes
    )
    if public is not None:
        persons = public.persons
        person_ids = sorted(p.person_id for p in persons)
        lines.append("- allowed person ids and their PUBLIC roles: " + ", ".join(
            _public_role_label(p.person_id, p.role) for p in persons
        ))
        witness_ids = sorted(
            p.person_id for p in persons if p.role == "witness"
        )
        candidates: Any = None
        try:
            candidates = derive_universes(public)
        except Exception:  # noqa: BLE001
            candidates = None
        if witness_ids:
            lines.append("- witness ids (each MUST have usable witness-statement "
                         f"evidence): {', '.join(witness_ids)}")
        if candidates is not None:
            if candidate_integrity_present:
                lines.append("- REQUIRED CANDIDATE-INTEGRITY RULES (the current "
                             "public candidate memberships are INVALID — do NOT "
                             "treat them as authoritative):")
                lines.append("- a public 'victim' person must NEVER carry "
                             "SUSPECT_ELIGIBLE")
                lines.append("- a public 'witness' person must NEVER carry "
                             "SUSPECT_ELIGIBLE")
                lines.append("- the true killer MUST remain a FINAL HOMICIDE "
                             "TARGET (an ordinary public-role suspect OR "
                             "witness in the final-target universe) — a "
                             "witness killer keeps role 'witness' and MUST NOT "
                             "be given SUSPECT_ELIGIBLE")
                lines.append("- keep every already-valid suspect candidate "
                             "unchanged; remove ONLY the impossible "
                             "victim/witness SUSPECT_ELIGIBLE tokens")
            else:
                lines.append("- suspect candidate ids (the accuseable set): "
                             + ", ".join(sorted(candidates.suspect_ids)))
    lines.append(
        "- required witness-kind evidence: an evidence item with kind "
        "'witness_statement' whose statement is non-empty, whose speakerName "
        "matches the witness name and whose WITNESS_CLAIMS proposition "
        "references the witness personId (one per witness)."
    )
    if quality_codes:
        lines.append("- safe issue codes to fix: " + ", ".join(quality_codes))
    lines.append(
        "- Keep the canonical truth server-side: never stamp any public person "
        "with the truth role token and never reveal the killer identity in a "
        "public role/description."
    )
    return "\n".join(lines)


def _public_role_label(person_id: str, role: Any) -> str:
    """Person id + the CLOSED public-role token (DEF-080).

    Roles are projected through ``PUBLIC_ROLE_VOCABULARY`` before the REPAIR
    context prints them: only a member of the closed pre-reveal vocabulary
    (``victim|suspect|witness|family|other``) is echoed; any other token
    (e.g. a truth-bearing ``"murderer"`` carried by an out-of-vocabulary
    public role in a quality-broken draft) is NEVER re-issued — the context
    prints the person id + ``<invalid>`` instead, so the fix prompt never
    re-echoes the killer's identity label.
    """
    if str(role) in case_quality.PUBLIC_ROLE_VOCABULARY:
        return f"{person_id} ({role})"
    return f"{person_id} (<invalid>)"


# --------------------------------------------------------------------------- #
# Phase43 — provider-neutral trusted ID / world contracts for the generic path
# --------------------------------------------------------------------------- #
#
# ``FrontierProvider`` runs the GENERIC controller pipeline (no stage driver),
# so ``_stage_context`` is the model-visible contract. The helpers below render
# the SAME authoritative canonical-ID / Phase41A quality / world contracts the
# Ollama driver already consumes (``trusted_context``), derived ONLY from
# already-parsed validated stage state + the locked constraints — never from raw
# provider output and never from hidden CaseTruth/solver internals.

_DECLARATION_SECTIONS: tuple[str, ...] = (
    "persons",
    "motives",
    "objects",
    "locations",
    "evidence",
)

_UNIVERSE_KEYS: tuple[str, ...] = (
    "personIds",
    "locationIds",
    "motiveIds",
    "objectIds",
    "evidenceIds",
    "assetIds",
    "anchors",
    "roles",
    "affordances",
    # DEF-062 (ADV-43-01) — role-scoped LOCKED reference universes. The
    # ``case_truth`` stage may constrain a crime reference ONLY when THAT role
    # is locked; these keys carry just the locked canonical ids per role.
    "lockedVictimIds",
    "lockedMurdererIds",
    "lockedWitnessIds",
    "lockedMotiveIds",
    "lockedWeaponIds",
    "lockedLocationIds",
)

# Provider-facing draft serialization drops these server-internal keys.
_CAMEL_DROP_KEYS: frozenset[str] = frozenset(
    {"compositionNotes", "resolutionProvenance", "generatedDefinition"}
)

_KIT_UNRESOLVED_LINE = (
    "SELECTED ENVIRONMENT KIT: (must be selected — no location/environment "
    "could be resolved; do NOT assume or mix anchors from multiple kits)"
)


def _camel_key(key: str) -> str:
    if "_" not in key:
        return key
    head, *rest = key.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _camelize(node: Any) -> Any:
    if isinstance(node, Mapping):
        out: dict[str, Any] = {}
        for key, value in node.items():
            camel = _camel_key(str(key))
            if camel in _CAMEL_DROP_KEYS:
                continue
            out[camel] = _camelize(value)
        return out
    if isinstance(node, (list, tuple)):
        return [_camelize(item) for item in node]
    return node


def provider_facing_draft(draft: Any) -> dict[str, Any]:
    """Deterministic provider-facing (camelCase) projection of a draft (FR-07).

    Converts the internal snake_case spec tree into the SAME key namespace the
    repair RESPONSE contract uses (``personId`` / ``worldGraph`` /
    ``crimeTime`` ...) so the model sees ONE canonical protocol shape. Only
    server-internal keys (``compositionNotes`` / ``resolutionProvenance`` /
    ``generatedDefinition``) are dropped.
    """
    if draft is None:
        return {}
    return _camelize(safety._spec_to_plain(draft))


def provider_facing_draft_json(draft: Any) -> str:
    """Deterministic JSON text of ``provider_facing_draft`` (stable bytes)."""
    return json.dumps(
        provider_facing_draft(draft),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )


def current_declaration_ids(draft: Any) -> dict[str, tuple[str, ...]]:
    """Declaration ids per section (FR-08).

    Returns a closed-key mapping (``persons`` / ``motives`` / ``objects`` /
    ``locations`` / ``evidence``) of the sorted ids DECLARED by the draft.
    Locked canonical ids are handled separately by ``_locked_canonical_ids``
    (they are a property of the attempt, not the draft). An incomplete/absent
    draft yields empty tuples.
    """
    if draft is None:
        return {section: () for section in _DECLARATION_SECTIONS}

    def _ids(entries: Any, attr: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                value
                for value in (
                    getattr(entry, attr, None) for entry in (entries or ())
                )
                if isinstance(value, str) and value
            )
        )

    return {
        "persons": _ids(getattr(draft, "persons", ()), "person_id"),
        "motives": _ids(getattr(draft, "motives", ()), "motive_id"),
        "objects": _ids(getattr(draft, "objects", ()), "object_id"),
        "locations": _ids(getattr(draft, "locations", ()), "location_id"),
        "evidence": _ids(getattr(draft, "evidence", ()), "id"),
    }


def _locked_canonical_ids(attempt: AttemptRecord) -> dict[str, str]:
    """Canonical locked ids keyed by locked field (victim/murderer/...)."""
    out: dict[str, str] = {}
    for identity in trusted_context.locked_identities(getattr(attempt, "locked", None)):
        out.setdefault(identity.field, identity.canonical_id)
    return out


def _crime_from_attempt(attempt: AttemptRecord) -> Any:
    crime = attempt.stage_outputs.get(GenerationStage.CASE_TRUTH)
    if crime is None and attempt.draft is not None:
        crime = attempt.draft.crime
    return crime


def _public_world_from_attempt(attempt: AttemptRecord) -> Any:
    public = attempt.stage_outputs.get(GenerationStage.PUBLIC_WORLD)
    if public is not None:
        return public
    return attempt.draft


def _evidence_from_attempt(attempt: AttemptRecord) -> Any:
    evidence = attempt.stage_outputs.get(GenerationStage.EVIDENCE)
    if evidence is not None:
        return evidence
    return attempt.draft


def selected_environment_kit(attempt: AttemptRecord) -> str | None:
    """Deterministic resolution of the ONE authoritative environment kit (FR-10).

    Priority order (all TRUSTED inputs): locked ``location`` -> the parsed
    scene's ``environment_id`` -> the parsed scene/location id or name -> the
    parsed crime location -> an explicit ``Location:`` prompt line. Each
    candidate is canonicalized through
    ``app.world.environment.canonicalize_environment_hint``; the FIRST value
    that canonicalizes onto a supported kit wins. Returns ``None`` when no kit
    can be resolved (the caller must then state that a kit must be selected —
    never dump all kits' anchors as equal options).
    """
    from app.world.environment import canonicalize_environment_hint

    candidates: list[Any] = []
    locked = getattr(attempt, "locked", None)
    if locked is not None:
        candidates.append(getattr(locked, "location", None))
    public = _public_world_from_attempt(attempt)
    scene = getattr(public, "scene", None) if public is not None else None
    if scene is not None:
        candidates.append(getattr(scene, "environment_id", None))
        candidates.append(getattr(scene, "name", None))
        candidates.append(getattr(scene, "location_id", None))
    crime = _crime_from_attempt(attempt)
    if crime is not None:
        candidates.append(getattr(crime, "location_id", None))
    for line in (attempt.prompt or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("location:"):
            candidates.append(stripped.split(":", 1)[1].strip())
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        kit, _issues = canonicalize_environment_hint(candidate)
        if kit is not None:
            return kit
    return None


def _kit_anchors(kit_id: str | None) -> tuple[str, ...]:
    if not kit_id:
        return ()
    try:
        table = dict(prompts._kit_anchor_table())
    except Exception:  # noqa: BLE001 - manifest unavailability degrades safely
        return ()
    return table.get(kit_id, ())


def trusted_reference_universe(
    attempt: AttemptRecord, stage: GenerationStage
) -> dict[str, tuple[str, ...]] | None:
    """Closed reference universe for ``stage`` from TRUSTED canonical state.

    Built ONLY from already-validated parsed stage outputs / the assembled
    draft / the locked constraints — NEVER from raw provider output. Returns
    ``None`` (fail-open to the un-narrowed schema) when no trustworthy universe
    exists for that stage.
    """
    buckets: dict[str, set[str]] = {key: set() for key in _UNIVERSE_KEYS}

    def add(key: str, value: Any) -> None:
        if isinstance(value, str) and value:
            buckets[key].add(value)

    for identity in trusted_context.locked_identities(getattr(attempt, "locked", None)):
        if identity.field in ("victim", "murderer", "witness"):
            add("personIds", identity.canonical_id)
        elif identity.field == "motive":
            add("motiveIds", identity.canonical_id)
        elif identity.field == "weapon":
            add("objectIds", identity.canonical_id)
        elif identity.field == "location":
            add("locationIds", identity.canonical_id)
        # DEF-062 — the role-scoped locked bucket, populated ONLY for the roles
        # that actually carry a locked value (so case_truth narrows per role).
        locked_role_key = {
            "victim": "lockedVictimIds",
            "murderer": "lockedMurdererIds",
            "witness": "lockedWitnessIds",
            "motive": "lockedMotiveIds",
            "weapon": "lockedWeaponIds",
            "location": "lockedLocationIds",
        }.get(identity.field)
        if locked_role_key is not None:
            add(locked_role_key, identity.canonical_id)

    crime = attempt.stage_outputs.get(GenerationStage.CASE_TRUTH)
    if crime is None and attempt.draft is not None:
        crime = attempt.draft.crime
    if crime is not None:
        add("personIds", getattr(crime, "victim_id", None))
        add("personIds", getattr(crime, "murderer_id", None))
        add("motiveIds", getattr(crime, "motive_id", None))
        add("objectIds", getattr(crime, "weapon_id", None))
        add("locationIds", getattr(crime, "location_id", None))

    public = attempt.stage_outputs.get(GenerationStage.PUBLIC_WORLD)
    if public is None and attempt.draft is not None:
        public = attempt.draft
    if public is not None:
        for person in getattr(public, "persons", ()) or ():
            add("personIds", getattr(person, "person_id", None))
        for motive in getattr(public, "motives", ()) or ():
            add("motiveIds", getattr(motive, "motive_id", None))
        for obj in getattr(public, "objects", ()) or ():
            add("objectIds", getattr(obj, "object_id", None))
        for location in getattr(public, "locations", ()) or ():
            add("locationIds", getattr(location, "location_id", None))
        scene = getattr(public, "scene", None)
        if scene is not None:
            add("locationIds", getattr(scene, "location_id", None))

    evidence = attempt.stage_outputs.get(GenerationStage.EVIDENCE)
    if evidence is None and attempt.draft is not None:
        evidence = attempt.draft
    if evidence is not None:
        for item in getattr(evidence, "evidence", ()) or ():
            add("evidenceIds", getattr(item, "id", None))

    if attempt.draft is not None:
        for location in getattr(attempt.draft.world_graph, "locations", ()) or ():
            add("locationIds", getattr(location, "location_id", None))

    # The world stages additionally narrow against the application-owned
    # composition vocabulary + the ONE selected kit's anchors.
    if stage in (GenerationStage.WORLD_GRAPH, GenerationStage.REPAIR):
        try:
            for asset_id in prompts._composition_asset_ids():
                add("assetIds", asset_id)
        except Exception:  # noqa: BLE001 - catalog unavailability degrades safely
            pass
        for anchor in _kit_anchors(selected_environment_kit(attempt)):
            add("anchors", anchor)

    universe = {key: tuple(sorted(values)) for key, values in buckets.items()}
    if not any(universe.values()):
        return None
    return universe


def _authorized_repair_sections(attempt: AttemptRecord) -> frozenset[str] | None:
    """Owner-authorized declaration sections for a REPAIR (FR-08 / DEF-065).

    Declaration ids are IMMUTABLE BY DEFAULT: a structural / cross-stage
    (``UNKNOWN``) owner authorizes NO declaration replacement, so this returns
    an EMPTY frozenset — never ``None`` ("all sections authorized"), which was
    the ADV-43-03 fail-open. Only a report whose owner explicitly names a
    declaration-owning stage authorizes that stage's sections. Locked ids are
    ALWAYS immutable regardless of this scope (enforced in
    ``_repair_id_churn_issues``).
    """
    report = getattr(attempt, "last_validation", None)
    if report is None:
        return frozenset()
    try:
        from app.generation.repair_routing import RepairOwner, RepairPlan

        plan = RepairPlan.from_report(report)
    except Exception:  # noqa: BLE001 - scope derivation is immutable-by-default
        return frozenset()
    if plan.is_default_full_rerun:
        # Structural / safety / UNKNOWN owner: no declaration may be replaced.
        return frozenset()
    owners = set(plan.repair_owners)
    sections: set[str] = set()
    if RepairOwner.CASE_TRUTH in owners or RepairOwner.CASE_PEOPLE in owners:
        sections.update({"persons", "motives"})
    if RepairOwner.EVIDENCE in owners or RepairOwner.ACTIVITY_LOG in owners:
        sections.add("evidence")
    if RepairOwner.WORLD_GRAPH in owners:
        sections.update({"objects", "world_graph", "locations"})
    if RepairOwner.ASSET_SPEC in owners:
        sections.add("objects")
    return frozenset(sections)


def _authoritative_case_truth_block(attempt: AttemptRecord) -> str:
    parts: list[str] = []
    sheet = trusted_context.render_locked_identity_sheet(
        getattr(attempt, "locked", None)
    )
    if sheet:
        parts.append(sheet)
    parts.append(trusted_context.PHASE41A_QUALITY_RULES)
    parts.append(
        "RESOLUTION SEMANTICS (authoritative):\n"
        "- HOMICIDE names a NON-VICTIM murderer: murdererId is the canonical "
        "non-victim person id and resolutionType=HOMICIDE.\n"
        "- SUICIDE sets resolutionType=SUICIDE and murdererId=null (there is "
        "no murderer)."
    )
    return "\n\n".join(parts)


def _authoritative_public_world_block(attempt: AttemptRecord) -> str:
    crime = _crime_from_attempt(attempt)
    lines = [
        "IMMUTABLE CRIME IDENTIFIERS (reuse THESE EXACT ids — never rename, "
        "replace or substitute display names):"
    ]
    if crime is not None:
        from app.domain.resolution import ResolutionType

        is_suicide = getattr(crime, "resolution_type", None) is ResolutionType.SUICIDE
        lines.append(f"- victimId: {getattr(crime, 'victim_id', '')}")
        if is_suicide:
            lines.append("- murdererId: null (SUICIDE)")
        elif getattr(crime, "murderer_id", None):
            lines.append(f"- murdererId: {crime.murderer_id}")
        lines.append(f"- motiveId: {getattr(crime, 'motive_id', '')}")
        lines.append(f"- weaponId: {getattr(crime, 'weapon_id', '')}")
        lines.append(f"- locationId: {getattr(crime, 'location_id', '')}")
    else:
        lines.append("- (case truth not yet available)")
    locked_ids = _locked_canonical_ids(attempt)
    if "witness" in locked_ids:
        lines.append(f"- locked witnessId: {locked_ids['witness']}")
    lines.append(
        "The locked location/motive/weapon identity is immutable; the public "
        "world MAY create new red-herring people/motives/locations, but it "
        "MUST NOT rename locked canonical ids or invent ids for locked "
        "entities."
    )
    # Phase43 (P43-FR-06) — the exact affordability contract the derivation
    # consumes: without it the generic Frontier path emitted motives/objects
    # with only VISIBLE_CHARACTER (motive/weapon universe empty on the live
    # Easy case). Guidance only; the validators remain authoritative.
    lines.append(
        "AFFORDANCE CONTRACT (authoritative — the candidate universes are "
        "derived from these exact tokens):\n"
        "- every victim/witness person carries ONLY VISIBLE_CHARACTER;\n"
        "- every suspect person (including the murderer) carries "
        "SUSPECT_ELIGIBLE;\n"
        "- every motive's affordances MUST contain MOTIVE_CANDIDATE;\n"
        "- every weapon object's affordances MUST contain POTENTIAL_WEAPON "
        "(and POTENTIAL_SHARP_WEAPON for a knife/sharp weapon);\n"
        "- affordance tokens are EXACTLY: SUSPECT_ELIGIBLE, MOTIVE_CANDIDATE, "
        "POTENTIAL_WEAPON, POTENTIAL_SHARP_WEAPON, POTENTIAL_BLUNT_WEAPON, "
        "POTENTIAL_POISON, INSPECTABLE, VISIBLE_CHARACTER."
    )
    return (
        "\n".join(lines)
        + "\n\n"
        + trusted_context.PHASE41A_QUALITY_RULES
        # Phase43 (P43-FR-06) — the generic Frontier path now also carries the
        # canonical shared case-quality rules the Ollama path already supplies
        # (single source, no drift): exact roles/affordances, the mandatory
        # witness statement and the resolve-every-id rule. Guidance only; the
        # validators remain authoritative.
        + "\n\n"
        + prompts.CASE_QUALITY_RULES
        # Phase43A — the explicit world-breadth/cardinality contract the Ollama
        # ``_CASE_FIELD_RULES`` template already teaches (victim + murderer +
        # witness + >= 2 red-herring suspects; locked motive + >= 2 red-herring
        # MOTIVE_CANDIDATE motives; locked scene + >= 2 other locations;
        # mandatory travel rules from every non-scene location to the scene,
        # >= 2 of them >= 1200s). Without it the live Easy PUBLIC_WORLD stage
        # under-provisioned the world and produced an empty FINAL HOMICIDE
        # TARGET universe (``FINAL_TARGET_UNIVERSE_EMPTY``). Guidance only; the
        # validators remain authoritative.
        + "\n\n"
        + prompts.PUBLIC_WORLD_BREADTH_RULES
    )


def _authoritative_evidence_block(attempt: AttemptRecord) -> str:
    public = _public_world_from_attempt(attempt)
    crime = _crime_from_attempt(attempt)
    parts: list[str] = []
    if public is not None:
        approved = trusted_context.approved_public_material(public)
        if approved:
            parts.append(approved)
        travel = trusted_context.approved_travel_sheet(public)
        if travel:
            parts.append(travel)
    if crime is not None and public is not None:
        seed = trusted_context.deduction_seed(attempt, crime, public)
        if seed:
            parts.append(seed)
    enum_lines = [
        "AUTHORITATIVE ID ENUMERATIONS (referenced ids MUST come from these "
        "closed lists):"
    ]

    def _line(label: str, entries: Any, attr: str) -> None:
        ids = sorted(
            value
            for value in (getattr(entry, attr, None) for entry in (entries or ()))
            if isinstance(value, str) and value
        )
        enum_lines.append(f"- {label}: " + (", ".join(ids) if ids else "(none)"))

    if public is not None:
        _line("personIds", getattr(public, "persons", ()), "person_id")
        _line("locationIds", getattr(public, "locations", ()), "location_id")
        _line("motiveIds", getattr(public, "motives", ()), "motive_id")
        _line("objectIds", getattr(public, "objects", ()), "object_id")
    parts.append("\n".join(enum_lines))
    parts.append(trusted_context.PHASE41A_QUALITY_RULES)
    # Phase43 (P43-FR-06) — the EVIDENCE stage must be told that every public
    # witness needs a usable witness_statement evidence item. This canonical
    # shared rule is what the Ollama evidence prompt already supplies; without
    # it the generic Frontier path produced WITNESS_STATEMENT_MISSING on the
    # live Easy case (the sole initial-pass validator defect). Guidance only;
    # the case-quality validator stays authoritative.
    parts.append(prompts.CASE_QUALITY_RULES)
    parts.append(
        "Use exact IDs from the authoritative lists. Never invent, rename, "
        "translate, slug, expand, or substitute display names for IDs."
    )
    return "\n\n".join(part for part in parts if part)


def _authoritative_world_graph_block(attempt: AttemptRecord) -> str:
    kit = selected_environment_kit(attempt)
    lines: list[str] = []
    if kit:
        lines.append(f"SELECTED ENVIRONMENT KIT (authoritative — exactly ONE kit): {kit}")
        anchors = _kit_anchors(kit)
        lines.append(f"- selectedEnvironmentKit: {kit}")
        lines.append(
            "- allowedAnchors (this kit ONLY): "
            + (", ".join(anchors) if anchors else "(none derived)")
        )
    else:
        lines.append(_KIT_UNRESOLVED_LINE)
    try:
        assets = prompts._composition_asset_ids()
    except Exception:  # noqa: BLE001
        assets = ()
    lines.append(
        "- allowedAssetIds (composition-usable): "
        + (", ".join(assets) if assets else "(none)")
    )
    public = _public_world_from_attempt(attempt)
    evidence = _evidence_from_attempt(attempt)
    object_ids = sorted(
        value
        for value in (
            getattr(obj, "object_id", None)
            for obj in (getattr(public, "objects", ()) or ())
        )
        if isinstance(value, str) and value
    )
    location_ids = sorted(
        value
        for value in (
            getattr(loc, "location_id", None)
            for loc in (getattr(public, "locations", ()) or ())
        )
        if isinstance(value, str) and value
    )
    if public is not None and getattr(public, "scene", None) is not None:
        scene_id = getattr(public.scene, "location_id", None)
        if isinstance(scene_id, str) and scene_id and scene_id not in location_ids:
            location_ids = sorted({*location_ids, scene_id})
    evidence_ids = sorted(
        value
        for value in (
            getattr(item, "id", None)
            for item in (getattr(evidence, "evidence", ()) or ())
        )
        if isinstance(value, str) and value
    )
    lines.append("- currentObjectIds: " + (", ".join(object_ids) or "(none)"))
    lines.append("- currentLocationIds: " + (", ".join(location_ids) or "(none)"))
    lines.append("- currentEvidenceIds: " + (", ".join(evidence_ids) or "(none)"))
    # Phase43A — explicit placement discipline. The live Easy WORLD_GRAPH
    # emitted many placements with duplicate objectIds (a safety defect that
    # blocks the world). Guidance only; the safety/structural validators remain
    # authoritative.
    lines.append(
        "PLACEMENT DISCIPLINE (authoritative — placements are validated after "
        "the pass):\n"
        "- place ONLY the objects the case declares (the currentObjectIds "
        "above) — never invent a new object or asset;\n"
        "- each objectId appears AT MOST ONCE across ALL placements — never "
        "emit two placements for the same objectId;\n"
        "- keep placements MINIMAL: the single crime weapon plus ONLY the "
        "objects an evidence item links to;\n"
        "- NEVER duplicate an objectId — a duplicate placement is a safety "
        "defect and blocks the world."
    )
    return "\n".join(lines)


def _authoritative_repair_block(attempt: AttemptRecord) -> str:
    parts: list[str] = []
    draft = attempt.draft
    if draft is not None:
        parts.append(
            "PROVIDER-FACING DRAFT (canonical camelCase protocol shape — "
            "respond with THESE field names):\n"
            + provider_facing_draft_json(draft)
        )
    ids = current_declaration_ids(draft)
    id_lines = [
        "CURRENT DECLARATION IDS (immutable unless the repair owner authorizes "
        "that section; references reuse them, they do not declare them):"
    ]
    for section in _DECLARATION_SECTIONS:
        values = ids.get(section, ())
        id_lines.append(f"- {section}: " + (", ".join(values) if values else "(none)"))
    parts.append("\n".join(id_lines))

    locked_ids = _locked_canonical_ids(attempt)
    locked_lines = [
        "AUTHORITATIVE LOCKED IDS (IMMUTABLE — never rename, replace or "
        "substitute; a changed/removed locked id is an unauthorized repair):"
    ]
    for field, label in (
        ("victim", "victimId"),
        ("murderer", "murdererId"),
        ("motive", "motiveId"),
        ("weapon", "weaponId"),
        ("location", "locationId"),
    ):
        if field in locked_ids:
            locked_lines.append(f"- {label}: {locked_ids[field]}")
    if "witness" in locked_ids:
        locked_lines.append(
            "- a locked Witness is TERMINAL (a missing/renamed locked witness "
            "cannot be repaired) — never rename the locked witness"
        )
    parts.append("\n".join(locked_lines))

    scope = getattr(attempt, "_repair_authorized_sections", None)
    if scope is None:
        parts.append(
            "REPAIR SCOPE: the reported defects below are cross-stage / "
            "structural; the whole draft may be corrected, but declaration ids "
            "in sections unrelated to a reported defect MUST be preserved "
            "unchanged."
        )
    else:
        parts.append(
            "REPAIR SCOPE (owner-authorized declaration sections ONLY): "
            + ", ".join(sorted(scope))
            + ". Declaration ids in every OTHER section are IMMUTABLE."
        )

    universe = trusted_reference_universe(attempt, GenerationStage.REPAIR) or {}
    crime = _crime_from_attempt(attempt)
    # The CURRENT declaration allowlists are derived from the DRAFT's declared
    # ids + the crime ids (both already generated), never from locked display
    # values: a locked witness that is missing from the draft is TERMINAL and
    # unreachable by repair, so its raw value is never echoed here.
    allow: dict[str, set[str]] = {
        "personIds": set(ids.get("persons", ())),
        "locationIds": set(ids.get("locations", ())),
        "motiveIds": set(ids.get("motives", ())),
        "objectIds": set(ids.get("objects", ())),
        "evidenceIds": set(ids.get("evidence", ())),
        "assetIds": set(universe.get("assetIds", ())),
        "anchors": set(universe.get("anchors", ())),
    }
    if crime is not None:
        for identifier in (getattr(crime, "victim_id", None), getattr(crime, "murderer_id", None)):
            if isinstance(identifier, str) and identifier:
                allow["personIds"].add(identifier)
        for key, value in (
            ("locationIds", getattr(crime, "location_id", None)),
            ("motiveIds", getattr(crime, "motive_id", None)),
            ("objectIds", getattr(crime, "weapon_id", None)),
        ):
            if isinstance(value, str) and value:
                allow[key].add(value)
    allow_lines = [
        "REQUEST ID ALLOWLISTS (references MUST use these exact current ids):"
    ]
    for key in (
        "personIds",
        "locationIds",
        "motiveIds",
        "objectIds",
        "evidenceIds",
        "assetIds",
        "anchors",
    ):
        values = tuple(sorted(allow[key]))
        allow_lines.append(f"- {key}: " + (", ".join(values) if values else "(none)"))
    parts.append("\n".join(allow_lines))

    kit = selected_environment_kit(attempt)
    if kit:
        parts.append(
            f"SELECTED ENVIRONMENT KIT: {kit}; allowedAnchors: "
            + ", ".join(_kit_anchors(kit))
        )
    else:
        parts.append(_KIT_UNRESOLVED_LINE)
    # DEF-061 (P43-FR-06 / DoD#6) — the generic Frontier REPAIR context carries
    # the SAME shared Phase 41A quality rules the initial stages receive.
    parts.append(trusted_context.PHASE41A_QUALITY_RULES)
    # Phase43 (P43-FR-06) — and the canonical shared case-quality rules (exact
    # roles/affordances, the mandatory per-witness witness_statement and the
    # resolve-every-id rule), so a full-draft repair can restore the exact
    # shapes the validators require.
    parts.append(prompts.CASE_QUALITY_RULES)
    # Phase43A — the world-breadth/cardinality contract (victim + murderer +
    # witness + >= 2 red-herring suspects; locked motive + >= 2 red-herring
    # MOTIVE_CANDIDATE motives; locked scene + >= 2 other locations; mandatory
    # travel rules, >= 2 of them >= 1200s), so a full-draft repair can restore
    # a valid FINAL HOMICIDE TARGET universe after an under-provisioned
    # PUBLIC_WORLD pass. Guidance only; the validators remain authoritative.
    parts.append(prompts.PUBLIC_WORLD_BREADTH_RULES)
    return "\n\n".join(parts)


def _authoritative_stage_block(attempt: AttemptRecord, stage: GenerationStage) -> str:
    if stage is GenerationStage.CASE_TRUTH:
        return _authoritative_case_truth_block(attempt)
    if stage is GenerationStage.PUBLIC_WORLD:
        return _authoritative_public_world_block(attempt)
    if stage is GenerationStage.EVIDENCE:
        return _authoritative_evidence_block(attempt)
    if stage is GenerationStage.WORLD_GRAPH:
        return _authoritative_world_graph_block(attempt)
    if stage is GenerationStage.REPAIR:
        return _authoritative_repair_block(attempt)
    return ""


def build_request(
    attempt: AttemptRecord,
    stage: GenerationStage,
    *,
    diagnostics: tuple[str, ...] = (),
    timeout_seconds: float | None = None,
) -> GenerateRequest:
    """Build the next provider invocation for ``stage`` (sanitized material).

    Phase30-fix DEF-B — the canonical server-owned stage JSON Schema (and its
    stable schema id) is attached here so every non-driver provider receives
    the trusted transport schema: ``FrontierProvider`` sends it as native
    structured output when its registry entry declared the capability; other
    providers (Fake/Live) simply ignore it. Never browser-supplied.
    DEF-019 — the attached schema is the PARSER-SHAPED stage-output schema
    (``json_schema_for_stage_output``: case_truth -> ``{crime}``, world_graph
    -> ``{worldGraph}``, the other stages unchanged), so a strict native
    structured-output provider can never be forced to emit a document the
    controller's strict parsers reject. ``json_schema_for_generation_stage``
    (the Ollama/transport contract) and every other consumer are unchanged.
    """
    # Phase43 (P43-FR-08) — snapshot the pre-repair declaration ids + the
    # owner-authorized section scope so a successful full-draft REPAIR parse
    # can reject unauthorized identifier churn. Deterministic; attempt-local.
    if stage is GenerationStage.REPAIR:
        pre_draft = current_draft(attempt)
        attempt._repair_pre_ids = (
            current_declaration_ids(pre_draft) if pre_draft is not None else None
        )
        attempt._repair_authorized_sections = _authorized_repair_sections(attempt)
    return GenerateRequest(
        attempt_id=attempt.attempt_id,
        stage=stage,
        prompt_context=_stage_context(attempt, stage),
        locked=attempt.locked,
        diagnostics=tuple(diagnostics),
        seed=attempt.seed,
        timeout_seconds=timeout_seconds,
        json_schema=prompts.json_schema_for_stage_output(
            stage.value,
            reference_universe=trusted_reference_universe(attempt, stage),
        ),
        schema_id=prompts.schema_id_for_generation_stage(stage.value),
        selected_environment_kit=selected_environment_kit(attempt),
    )


def _record_deferred_issues(
    attempt: AttemptRecord, stage: GenerationStage, issues: tuple[str, ...]
) -> None:
    """Append parser issues to the flat union AND the per-stage map (P43A)."""
    if not issues:
        return
    attempt.deferred_structural = tuple(
        sorted(set(attempt.deferred_structural) | set(issues))
    )
    prior = set(attempt.deferred_by_stage.get(stage, ()))
    attempt.deferred_by_stage[stage] = tuple(sorted(prior | set(issues)))


def _clear_deferred_stage(attempt: AttemptRecord, stage: GenerationStage) -> None:
    """Clear ONE stage's deferred issues (from the per-stage map AND the union).

    Used by the deterministic evidence completion once the EVIDENCE algebra has
    successfully supplied the evidence: the (now-cleared) parse failure is
    algebra-owned, so it must stop feeding the structural bucket. No OTHER
    stage's issues are touched (P43A-FR-08).
    """
    owned = set(attempt.deferred_by_stage.pop(stage, ()))
    if not owned:
        return
    remaining = set(attempt.deferred_structural) - owned
    attempt.deferred_structural = tuple(sorted(remaining))


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
        _record_deferred_issues(
            attempt, stage, tuple(parser.collect_issues(stage, content))
        )
    else:
        # A successful parse clears any stale issue for THIS stage only (a
        # re-parsed stage owns its own defects).
        _clear_deferred_stage(attempt, stage)
        attempt.stage_outputs[stage] = spec
        attempt.draft = None
        attempt._phase3_cache = None
    attempt.stage_done.add(stage)


def _repair_id_churn_issues(attempt: AttemptRecord, new_draft: GeneratedDraft) -> tuple[str, ...]:
    """FR-08 — deterministic unauthorized-identifier-churn detection.

    Compares the new draft's DECLARATION ids per section against the snapshot
    taken by ``build_request``. A removed/changed LOCKED canonical id is always
    unauthorized; a removed/changed NON-locked declaration id is unauthorized
    unless its section was owner-authorized. Returns sorted deterministic issue
    tokens, or ``()`` when the snapshot is absent (fail-open — no snapshot means
    the repair was not scope-tracked).

    ADDITIONS (Phase43 live-acceptance fix): an ADDED declaration id does NOT
    rename/remove any existing id, so it is NOT churn. Detecting churn purely
    from removals/renames is sufficient — a rename that "replaces" an id still
    REMOVES the old id and is therefore caught by ``pre_ids - new_ids`` above.
    Rejecting additions outright discarded the WHOLE repair draft on every pass
    (a rejected repair never applies), so a draft missing required entities
    (e.g. the live Easy ``FINAL_TARGET_UNIVERSE_EMPTY`` case) could never be
    corrected: every repair pass stayed UNCHANGED and the attempt ended
    ``REPAIR_BUDGET_EXHAUSTED``.

    A TARGETED owner repair (non-empty authorized scope) still forbids additions
    OUTSIDE the authorized sections — arbitrary fabricated declarations are
    never accepted as a side effect of a scoped repair. A FULL-RERUN repair
    (empty authorized scope: structural/safety/UNKNOWN owner) rebuilds the whole
    draft and MAY add the missing declarations; the complete validation suite
    (and the solver) remains the sole acceptance authority afterwards, and
    locked/removed existing ids are still protected above.
    """
    pre = getattr(attempt, "_repair_pre_ids", None)
    if pre is None:
        return ()
    locked_ids = set(_locked_canonical_ids(attempt).values())
    authorized = getattr(attempt, "_repair_authorized_sections", None)
    # A non-empty authorized scope is a TARGETED owner repair: additions outside
    # the authorized sections are unauthorized. ``None`` (untracked) is
    # fail-open; the empty frozenset (full-rerun/UNKNOWN owner) rebuilds the
    # whole draft and permits additions.
    targeted_scope = authorized is not None and len(authorized) > 0
    new = current_declaration_ids(new_draft)
    issues: list[str] = []
    for section in _DECLARATION_SECTIONS:
        pre_ids = set(pre.get(section, ()))
        new_ids = set(new.get(section, ()))
        section_authorized = authorized is None or section in authorized
        for identifier in sorted(pre_ids - new_ids):
            if identifier in locked_ids:
                issues.append(f"repair locked id changed: {section} {identifier}")
            elif not section_authorized:
                issues.append(f"repair id churn: {section} {identifier}")
        if targeted_scope and not section_authorized:
            for identifier in sorted(new_ids - pre_ids):
                if identifier in locked_ids:
                    continue
                issues.append(f"repair id churn: {section} {identifier} (added)")
    return tuple(sorted(set(issues)))


def apply_repair(attempt: AttemptRecord, content: str) -> tuple[str, ...]:
    """Apply a REPAIR (full-draft) provider output.

    A failed full-draft parse counts as a failed repair (structural issues are
    recorded, the previous draft is kept). A successful parse REPLACES the
    whole draft and clears stale deferred issues; the caller MUST rerun the
    complete validation suite afterwards (REQUIREMENTS 32.13).

    Phase43 (P43-FR-08): a successful parse that changes/removes a LOCKED id —
    or an unrelated declaration id outside the owner-authorized repair scope —
    is REJECTED as unauthorized churn: the pre-existing draft is kept and the
    deterministic issue tokens are recorded so validation classifies the
    attempt exactly as before (never a silent namespace rewrite).
    """
    issues = parser.collect_full_draft_issues(content)
    if issues:
        _record_deferred_issues(
            attempt, GenerationStage.REPAIR, tuple(sorted(set(issues)))
        )
        return tuple(sorted(set(issues)))
    draft = parser.parse_full_draft(content, non_throwing=True)
    churn = _repair_id_churn_issues(attempt, draft)
    if churn:
        _record_deferred_issues(
            attempt, GenerationStage.REPAIR, tuple(sorted(set(churn)))
        )
        return tuple(sorted(set(churn)))
    attempt.draft = draft
    attempt.stage_outputs = {}
    attempt.stage_done = set()
    attempt.deferred_structural = ()
    attempt.deferred_by_stage = {}
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
    emit_event(
        "generation.validation.started",
        caseId=attempt.case_id,
        generationAttemptId=attempt.attempt_id,
        deadlineRemainingMs=(
            int(attempt.budget.remaining_seconds() * 1000)
            if attempt.budget is not None else None
        ),
    )
    if draft is None:
        report = _incomplete_draft_report(attempt.deferred_structural)
        attempt.solver_proof = None
        attempt.last_validation = report
        emit_event(
            "generation.validation.complete",
            caseId=attempt.case_id,
            generationAttemptId=attempt.attempt_id,
            validationOutcome=report.outcome.value,
            issueCodes=report.repair_diagnostics,
            deadlineRemainingMs=(
                int(attempt.budget.remaining_seconds() * 1000)
                if attempt.budget is not None else None
            ),
        )
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
    universes: Any = None
    if public is None:
        universe_issues.append("candidate universes unavailable: public case incomplete")
    else:
        universes = derive_universes(public)
        # Phase41 (§15/§32) + Phase41A (§3/§5): an empty INVESTIGATIVE suspect
        # universe must NOT force a REGENERATE for a SUICIDE case — an empty
        # suspect universe is legitimately possible when the end-game is the
        # victim-suicide option (the final target universe + the suicide
        # option provide the resolution). HOMICIDE still requires a non-empty
        # FINAL HOMICIDE TARGET universe (REQUIREMENTS 31.1.1A / 31.10): the
        # WHO solver needs at least one suspect-or-witness target to solve —
        # a zero-suspect-but-witnesses HOMICIDE is LEGAL (final targets are
        # the deduction universe and a witness can be the murderer).
        from app.domain.resolution import ResolutionType

        if truth is not None:
            is_suicide = truth.crime.resolution_type is ResolutionType.SUICIDE
        else:
            is_suicide = False
        if not universes.homicide_target_ids and not is_suicide:
            universe_issues.append("final homicide target universe is empty")
        if not universes.motive_ids:
            universe_issues.append("motive universe is empty")
        if not universes.weapon_ids:
            universe_issues.append("weapon universe is empty")
    universe_tuple = tuple(sorted(set(universe_issues)))

    # (c-2) Phase35 case-quality invariants (public-role / candidate-role /
    # murderer accusal / witness completeness). Runs AFTER the universe bucket
    # and BEFORE the solver gate: quality failures classify REPAIR (report.py
    # outcome branch) and the solver is skipped on a quality-broken draft so a
    # MURDERER_NOT_SUSPECT_CANDIDATE code is not double-reported as a solver
    # truth mismatch. ZERO provider calls; deterministic.
    quality_issues: list[str] = []
    if public is not None and truth is not None and universes is not None:
        quality_issues.extend(
            case_quality.validate_case_quality(public, truth, evidence, universes, draft)
        )
    quality_tuple = tuple(sorted(set(quality_issues)))

    # (d) solver + truth-aware comparison ------------------------------------
    solver_result: SolverProof | None = None
    validation = None
    if public is not None and truth is not None and not (
        structural_tuple or safety_tuple or universe_tuple or quality_tuple
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
        quality_issues=quality_tuple,
    )
    attempt.solver_proof = solver_result
    attempt.last_validation = report
    emit_event(
        "generation.validation.complete",
        caseId=attempt.case_id,
        generationAttemptId=attempt.attempt_id,
        validationOutcome=report.outcome.value,
        issueCodes=report.repair_diagnostics,
        deadlineRemainingMs=(
            int(attempt.budget.remaining_seconds() * 1000)
            if attempt.budget is not None else None
        ),
    )
    return report


# ---------------------------------------------------------------------------
# Phase 43A — generic-path deterministic evidence completion (P43A-FR-07/09/10)
# ---------------------------------------------------------------------------
#
# The Frontier/generic controller pipeline (no stage driver) must close an
# evidence-only solver ambiguity deterministically BEFORE the WORLD_GRAPH stage
# (so the world graph is generated against the completed evidence), instead of
# burning the bounded repair/regeneration budget on an evidence-underconstrained
# case (DEF-071). The completion is provider-neutral (``evidence_algebra``) and
# can NEVER mask a model-owned structural/safety/quality defect — see
# ``_evidence_completion_preconditions_met``.

# The case-quality codes the deterministic algebra itself OWNS (a complete
# canonical evidence set supplies a usable witness statement for every public
# witness). Every OTHER quality code is model-owned and keeps the existing
# repair/regeneration owner, so completion never masks a non-evidence defect.
_EVIDENCE_OWNED_QUALITY_CODES: frozenset[str] = frozenset(
    {
        case_quality.WITNESS_STATEMENT_MISSING,
        case_quality.WITNESS_STATEMENT_EMPTY,
    }
)


def _evidence_completion_preconditions_met(
    attempt: AttemptRecord, report: ValidationReport
) -> bool:
    """Whether a completion is SAFE: only an evidence-only defect remains.

    Unknown-id / broken-reference (structural), safety, locked-constraint,
    world-graph and universe defects — and every non-evidence quality defect
    (role truth leak / invalid candidate membership / deceptive-claim) — block
    completion so they keep routing through their existing owner.

    Phase43A (P43A-FR-08): a deferred parser failure attributed to the EVIDENCE
    stage is OWNED by the deterministic algebra (a complete canonical evidence
    set is what supplies the missing evidence), so it does NOT block. Every
    OTHER stage's deferred structural issue — and every non-deferred structural
    issue (unknown references, assembly failure, world resolution) — still
    blocks, so a CASE_TRUTH / PUBLIC_WORLD / WORLD_GRAPH / REPAIR defect is
    never masked.
    """
    evidence_owned = set(
        attempt.deferred_by_stage.get(GenerationStage.EVIDENCE, ())
    )
    non_evidence_structural = set(report.structural_issues) - evidence_owned
    if (
        report.locked_violations
        or non_evidence_structural
        or report.safety_issues
        or report.world_issues
        or report.universe_issues
    ):
        return False
    for code in report.quality_issues:
        if code not in _EVIDENCE_OWNED_QUALITY_CODES:
            return False
    return True


def _apply_completed_evidence(attempt: AttemptRecord, completion: Any) -> None:
    """Store a completed EvidenceSetSpec (+ any extra travel rules) on the attempt."""
    attempt.stage_outputs[GenerationStage.EVIDENCE] = completion.evidence
    extra_rules = tuple(completion.extra_travel_rules or ())
    if extra_rules:
        public_world = attempt.stage_outputs.get(GenerationStage.PUBLIC_WORLD)
        if public_world is not None:
            existing = tuple(getattr(public_world, "travel_rules", ()) or ())
            # Replace same-endpoint rules (a deterministic override) and append
            # the genuinely new ones; the endpoint set of the result is what
            # changes, so compare the canonical pair sets.
            override_endpoints = {
                (rule.from_location_id, rule.to_location_id) for rule in extra_rules
            }
            merged = [
                rule
                for rule in existing
                if (rule.from_location_id, rule.to_location_id) not in override_endpoints
            ]
            merged.extend(extra_rules)
            before_pairs = {
                (
                    getattr(rule, "from_location_id", None),
                    getattr(rule, "to_location_id", None),
                    getattr(rule, "travel_time_seconds", None),
                )
                for rule in existing
            }
            after_pairs = {
                (
                    getattr(rule, "from_location_id", None),
                    getattr(rule, "to_location_id", None),
                    getattr(rule, "travel_time_seconds", None),
                )
                for rule in merged
            }
            if before_pairs != after_pairs:
                attempt.stage_outputs[GenerationStage.PUBLIC_WORLD] = replace(
                    public_world, travel_rules=tuple(merged)
                )
    # Force a rebuild from the (now completed) stage outputs.
    attempt.draft = None
    attempt._phase3_cache = None


def complete_generic_evidence_if_underconstrained(attempt: AttemptRecord) -> bool:
    """Phase 43A — deterministic evidence completion for the generic path.

    Runs AT MOST ONCE per attempt (``attempt._evidence_completed``) on the
    trusted canonical state after the EVIDENCE stage parsed and BEFORE the
    WORLD_GRAPH stage. Returns True iff the evidence was completed.

    Completion is attempted only when the FULL canonical validation of the
    current partial draft reports NO locked/structural/safety/world/universe
    issue and NO non-evidence quality issue, AND
    :func:`evidence_algebra.analyze_evidence_gaps` independently classifies the
    state as specifically ``EVIDENCE_UNDERCONSTRAINED``. Canonical validation +
    solver are rerun afterwards and remain the SOLE acceptance authority — a
    deterministic fact receives no privileged status. Non-evidence defects keep
    routing through the existing repair/regeneration path.
    """
    if getattr(attempt, "_evidence_completed", False):
        return False
    attempt._evidence_completed = True
    if attempt.budget is not None and attempt.budget.deadline_passed():
        return False

    emit_event(
        "evidence.completion.started",
        caseId=attempt.case_id,
        generationAttemptId=attempt.attempt_id,
        providerCallCount=(
            attempt.budget.calls if attempt.budget is not None else None
        ),
        repairCount=(
            attempt.budget.repair_passes if attempt.budget is not None else None
        ),
        regenerationCount=(
            attempt.budget.regenerations if attempt.budget is not None else None
        ),
    )

    def _skip(reason: str, *, analysis: Any = None) -> bool:
        fields: dict[str, Any] = {
            "caseId": attempt.case_id,
            "generationAttemptId": attempt.attempt_id,
            "reasonCode": reason,
        }
        if analysis is not None:
            fields.update(
                {
                    "factsAddedCount": 0,
                    "dimensionsBefore": tuple(analysis.ambiguous_dimensions),
                    "dimensionsAfter": tuple(analysis.ambiguous_dimensions),
                    "solverOutcomeBefore": analysis.solver_outcome,
                    "solverOutcomeAfter": analysis.solver_outcome,
                    "completionIdempotent": True,
                    "completionOutcome": analysis.classification,
                }
            )
        emit_event("evidence.completion.skipped", **fields)
        return False

    try:
        public, evidence, truth, _draft = assemble(attempt)
    except (TypeError, ValueError):
        return _skip("STRUCTURED_OUTPUT_INVALID")

    report = validate_draft(attempt)
    if not _evidence_completion_preconditions_met(attempt, report):
        return _skip("PRECONDITIONS_NOT_MET")

    analysis = evidence_algebra.analyze_evidence_gaps(public, truth, evidence)
    if not analysis.closable:
        return _skip(analysis.classification, analysis=analysis)

    current_spec = attempt.stage_outputs.get(GenerationStage.EVIDENCE)
    completion = evidence_algebra.complete_evidence_if_ambiguous(
        public,
        truth.crime,
        current_spec,
        witness_truthfulness=tuple(
            getattr(attempt, "_witness_truthfulness", ()) or ()
        ),
    )
    if not completion.changed:
        return _skip("NO_CHANGE", analysis=analysis)

    _apply_completed_evidence(attempt, completion)
    # P43A-FR-08 — the EVIDENCE-stage parse failure (if any) is now algebra-owned:
    # the deterministic algebra supplied the evidence, so its deferred issues are
    # cleared. No other stage's issues are touched. Canonical validation + solver
    # rerun below remain the SOLE acceptance authority (P43A-FR-10).
    _clear_deferred_stage(attempt, GenerationStage.EVIDENCE)
    after_report = validate_draft(attempt)

    emit_event(
        "evidence.completion.completed",
        caseId=attempt.case_id,
        generationAttemptId=attempt.attempt_id,
        factsAddedCount=int(completion.facts_added),
        dimensionsBefore=tuple(completion.dimensions_before),
        dimensionsAfter=tuple(completion.dimensions_after),
        solverOutcomeBefore=completion.solver_outcome_before,
        solverOutcomeAfter=completion.solver_outcome_after,
        completionIdempotent=bool(completion.idempotent),
        completionOutcome=analysis.classification,
        validationOutcome=after_report.outcome.value,
        providerCallCount=(
            attempt.budget.calls if attempt.budget is not None else None
        ),
        repairCount=(
            attempt.budget.repair_passes if attempt.budget is not None else None
        ),
        regenerationCount=(
            attempt.budget.regenerations if attempt.budget is not None else None
        ),
    )
    return True


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
    "complete_generic_evidence_if_underconstrained",
    "current_declaration_ids",
    "current_draft",
    "normalize_prompt",
    "provider_facing_draft",
    "selected_environment_kit",
    "time_proof_checks_passed",
    "trusted_reference_universe",
    "validate_draft",
    "_draft_to_phase3",
]
