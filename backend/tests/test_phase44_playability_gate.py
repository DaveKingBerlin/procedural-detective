"""Phase 44 unit tests — Gate 3 playability & difficulty quality gate.

Hermetic, ZERO provider/network. Builds deterministic drafts from the Phase 4
golden stage payloads, then exercises ``validate_playability_quality`` and the
difficulty policy directly.

Covers Phase44 §30 items 1-32 (the ADV-INV-01 closure, thresholds,
meaningful/decorative classification, reachability, distribution, red herrings
and the preserved invariants).
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden import (  # noqa: E402
    BAR,
    OFFICE,
    SCENE_LOCATION,
    golden_evidence,
    golden_public,
    truth_variant_a,
)
from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402

from app.domain.eligibility import derive_universes  # noqa: E402
from app.domain.evidence import (  # noqa: E402
    OTHER,
    PERSON_OBSERVED_AT_LOCATION,
    WITNESS_CLAIMS,
    EvidenceFact,
    Reliability,
    SourceRef,
    TypedProposition,
)
from app.domain.resolution import ResolutionType  # noqa: E402
from app.domain.solver import solve_case  # noqa: E402
from app.domain.truth import CaseTruth, Crime, CrimeTime  # noqa: E402
from app.generation import difficulty_policy, parser, playability, pipeline  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    EvidenceSpec,
    GeneratedDraft,
    ObjectSpec,
    PlacementSpec,
    PropSpec,
    WorldGraphSpec,
)

# --------------------------------------------------------------------------- #
# deterministic draft builders
# --------------------------------------------------------------------------- #

_G = GOLDEN_STAGE_PAYLOADS


def golden_draft() -> GeneratedDraft:
    ct = parser.parse_stage(GenerationStage.CASE_TRUTH, _G[GenerationStage.CASE_TRUTH])
    pw = parser.parse_stage(GenerationStage.PUBLIC_WORLD, _G[GenerationStage.PUBLIC_WORLD])
    ev = parser.parse_stage(GenerationStage.EVIDENCE, _G[GenerationStage.EVIDENCE])
    wg = parser.parse_stage(GenerationStage.WORLD_GRAPH, _G[GenerationStage.WORLD_GRAPH])
    return GeneratedDraft(
        crime=ct,
        persons=pw.persons,
        motives=pw.motives,
        objects=pw.objects,
        locations=pw.locations,
        travel_rules=pw.travel_rules,
        scene=pw.scene,
        evidence=ev.evidence,
        world_graph=wg,
    )


def phase3(draft: GeneratedDraft):
    return pipeline._draft_to_phase3(draft, case_id="CASE-001", title="t")


def evaluate(draft: GeneratedDraft, difficulty: str | None):
    public, evidence, truth = phase3(draft)
    return playability.validate_playability_quality(
        draft=draft,
        public=public,
        evidence=evidence,
        universes=derive_universes(public),
        truth=truth,
        difficulty=difficulty,
    )


def _drop_objects(draft: GeneratedDraft, object_ids: set[str]) -> GeneratedDraft:
    """Remove objects + their placements + any evidence referencing them."""
    objects = tuple(o for o in draft.objects if o.object_id not in object_ids)
    placements = tuple(
        p for p in draft.world_graph.placements if p.object_id not in object_ids
    )
    evidence = tuple(
        fact
        for fact in draft.evidence
        if not any(
            getattr(prop, "object_id", None) in object_ids for prop in fact.propositions
        )
    )
    return replace(
        draft,
        objects=objects,
        evidence=evidence,
        world_graph=replace(draft.world_graph, placements=placements),
    )


def _add_inspectable(
    draft: GeneratedDraft,
    object_id: str,
    *,
    evidence_id: str | None = None,
    affordances: tuple[str, ...] = ("INSPECTABLE",),
    interaction: str = "inspect",
    anchor: str = "desk_main",
    location: str = SCENE_LOCATION,
) -> GeneratedDraft:
    """Add a declared object + placement (and an optional discoverable fact)."""
    spec = ObjectSpec(
        object_id=object_id, asset_id="PROP_VASE_01", affordances=affordances
    )
    placement = PlacementSpec(
        object_id=object_id,
        asset_id="PROP_VASE_01",
        location_id=location,
        anchor=anchor,
        interaction=interaction,
        evidence_id=evidence_id,
    )
    evidence = tuple(draft.evidence)
    if evidence_id is not None:
        evidence = evidence + (
            EvidenceSpec(
                id=evidence_id,
                kind="object",
                propositions=(PropSpec(type=OTHER, object_id=object_id),),
            ),
        )
    return replace(
        draft,
        objects=tuple(draft.objects) + (spec,),
        evidence=evidence,
        world_graph=replace(
            draft.world_graph,
            placements=tuple(draft.world_graph.placements) + (placement,),
        ),
    )


def easy_at_min() -> GeneratedDraft:
    """Exactly 3 inspectables (knife WEAPON + motive record WHY + body WHEN;
    WHO is witness/motive-reachable); all required dims reachable."""
    return _drop_objects(
        golden_draft(),
        {"letter_opener", "scissors", "apartment_laptop", "cctv_hall_monitor"},
    )


def easy_below_min() -> GeneratedDraft:
    """Only 2 inspectables -> below the Easy minimum."""
    return _drop_objects(
        golden_draft(),
        {
            "letter_opener",
            "scissors",
            "apartment_laptop",
            "cctv_hall_monitor",
            "motive_audit_file",
        },
    )


def hard_at_min() -> GeneratedDraft:
    """Golden + 2 extra meaningful interactive objects -> 7 inspectables."""
    draft = golden_draft()
    draft = _add_inspectable(draft, "clue_painting_01", evidence_id="clue_painting_fact_01")
    draft = _add_inspectable(
        draft, "clue_ledger_01", evidence_id="clue_ledger_fact_01", anchor="shelf_01"
    )
    return draft


# --------------------------------------------------------------------------- #
# difficulty policy
# --------------------------------------------------------------------------- #


def test_difficulty_vocabulary_and_default():
    assert difficulty_policy.DIFFICULTY_VOCABULARY == ("easy", "medium", "hard")
    assert difficulty_policy.DEFAULT_DIFFICULTY == "medium"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "medium"),
        ("", "medium"),
        ("  ", "medium"),
        ("EASY", "easy"),
        (" hard ", "hard"),
        ("Medium", "medium"),
        ("bogus", "medium"),
        ("extreme", "medium"),
    ],
)
def test_normalize_difficulty(raw, expected):
    assert difficulty_policy.normalize_difficulty(raw) == expected


def test_policy_minimums_match_adr_004():
    easy = difficulty_policy.DIFFICULTY_POLICIES["easy"]
    medium = difficulty_policy.DIFFICULTY_POLICIES["medium"]
    hard = difficulty_policy.DIFFICULTY_POLICIES["hard"]
    assert (
        easy.min_inspectable_objects,
        easy.min_meaningful_inspectables,
        easy.min_red_herrings,
        easy.min_distinct_meaningful_sources,
    ) == (3, 2, 0, 2)
    assert (
        medium.min_inspectable_objects,
        medium.min_meaningful_inspectables,
        medium.min_red_herrings,
        medium.min_distinct_meaningful_sources,
    ) == (5, 3, 1, 3)
    assert (
        hard.min_inspectable_objects,
        hard.min_meaningful_inspectables,
        hard.min_red_herrings,
        hard.min_distinct_meaningful_sources,
    ) == (7, 5, 2, 4)
    assert medium.target_inspectable_objects == (5, 7)
    assert medium.target_meaningful_inspectables == (3, 5)
    assert medium.target_red_herrings == (1, 2)


def test_difficulty_requirements_block_is_policy_derived_no_drift():
    # AC-13/AC-14 — the prompt block must equal the policy-derived block exactly
    # for every difficulty (no hand-maintained magic numbers).
    for difficulty in difficulty_policy.DIFFICULTY_VOCABULARY:
        block = difficulty_policy.difficulty_requirements_block(difficulty)
        policy = difficulty_policy.DIFFICULTY_POLICIES[difficulty]
        assert f"{difficulty.upper()}" in block
        assert str(policy.min_inspectable_objects) in block
        assert (
            f"{policy.target_inspectable_objects[0]}-{policy.target_inspectable_objects[1]}"
            in block
        )
        assert str(policy.min_meaningful_inspectables) in block
        assert str(policy.min_red_herrings) in block
        assert str(policy.min_distinct_meaningful_sources) in block
    # Unknown difficulty falls back to the medium block (never a downgrade).
    assert (
        difficulty_policy.difficulty_requirements_block(None)
        == difficulty_policy.difficulty_requirements_block("medium")
    )


# --------------------------------------------------------------------------- #
# ADV-INV-01 closure
# --------------------------------------------------------------------------- #


def test_1_placements_empty_cannot_publish():
    draft = replace(golden_draft(), world_graph=WorldGraphSpec())
    report = evaluate(draft, "easy")
    assert not report.valid
    assert playability.PLAYABILITY_EMPTY_WORLD in report.issue_codes
    assert report.inspectable_count == 0


def test_2_one_decorative_placement_cannot_satisfy_easy():
    draft = golden_draft()
    # Reduce to a single DECORATIVE placement (empty interaction).
    draft = _drop_objects(
        draft,
        {
            "letter_opener",
            "scissors",
            "apartment_laptop",
            "victim_body_placeholder",
        },
    )
    draft = replace(
        draft,
        world_graph=replace(
            draft.world_graph,
            placements=tuple(
                replace(p, interaction="") for p in draft.world_graph.placements
            ),
        ),
    )
    report = evaluate(draft, "easy")
    assert not report.valid
    assert playability.PLAYABILITY_EMPTY_WORLD in report.issue_codes
    assert report.inspectable_count == 0


def test_3_schema_valid_sparse_world_fails_gate3():
    # A structurally valid world with a single interactive object.
    draft = easy_below_min()
    draft = _drop_objects(draft, {"victim_body_placeholder"})
    report = evaluate(draft, "medium")
    assert not report.valid
    assert playability.PLAYABILITY_TOO_FEW_INSPECTABLES in report.issue_codes


def test_4_decorative_objects_do_not_count_as_meaningful():
    base = easy_at_min()
    padded = base
    for i in range(4):
        padded = _add_inspectable(
            padded, f"decor_{i}", interaction="", anchor="shelf_01"
        )
    base_report = evaluate(base, "easy")
    padded_report = evaluate(padded, "easy")
    assert padded_report.inspectable_count == base_report.inspectable_count
    assert (
        padded_report.meaningful_inspectable_count
        == base_report.meaningful_inspectable_count
    )


# --------------------------------------------------------------------------- #
# difficulty thresholds
# --------------------------------------------------------------------------- #


def test_6_easy_below_min_fails():
    report = evaluate(easy_below_min(), "easy")
    assert not report.valid
    assert playability.PLAYABILITY_TOO_FEW_INSPECTABLES in report.issue_codes
    assert playability.PLAYABILITY_DIFFICULTY_CONTRACT_UNMET in report.issue_codes


def test_7_easy_at_min_passes():
    report = evaluate(easy_at_min(), "easy")
    assert report.valid, report.issue_codes
    assert report.inspectable_count == 3
    assert report.meaningful_inspectable_count >= 2


def test_8_medium_below_min_fails():
    report = evaluate(easy_at_min(), "medium")
    assert not report.valid
    assert playability.PLAYABILITY_TOO_FEW_INSPECTABLES in report.issue_codes


def test_9_medium_at_min_passes():
    report = evaluate(golden_draft(), "medium")
    assert report.valid, report.issue_codes
    assert report.inspectable_count >= 5
    assert report.meaningful_inspectable_count >= 3
    assert report.red_herring_count >= 1
    assert report.distinct_meaningful_source_count >= 3


def test_10_hard_below_min_fails():
    report = evaluate(easy_at_min(), "hard")
    assert not report.valid
    assert playability.PLAYABILITY_TOO_FEW_INSPECTABLES in report.issue_codes


def test_11_hard_at_min_passes():
    report = evaluate(hard_at_min(), "hard")
    assert report.valid, report.issue_codes
    assert report.inspectable_count >= 7
    assert report.meaningful_inspectable_count >= 5
    assert report.red_herring_count >= 2
    assert report.distinct_meaningful_source_count >= 4


# --------------------------------------------------------------------------- #
# meaningful classification
# --------------------------------------------------------------------------- #


def test_12_decorative_only_objects_do_not_count():
    ok = evaluate(easy_at_min(), "easy")
    padded = easy_at_min()
    for i in range(3):
        padded = _add_inspectable(padded, f"deco_{i}", interaction="")
    assert evaluate(padded, "easy").meaningful_inspectable_count == (
        ok.meaningful_inspectable_count
    )


def test_13_evidence_linked_object_counts():
    draft = easy_at_min()
    draft = _add_inspectable(draft, "linked_clue_01", evidence_id="linked_fact_01")
    before = evaluate(easy_at_min(), "easy").meaningful_inspectable_count
    after = evaluate(draft, "easy").meaningful_inspectable_count
    assert after == before + 1


def test_14_contradiction_linked_object_counts():
    # A weapon-forensic-contradicted object is MEANINGFUL (its linked record
    # counts; WEAPON_CANDIDATE role also qualifies).
    report = evaluate(golden_draft(), "medium")
    # letter_opener/scissors are contradicted weapons and count as meaningful.
    assert report.meaningful_inspectable_count >= 5


def test_15_witness_interaction_counts_correctly():
    base = golden_draft()
    base_report = evaluate(base, "medium")
    # Dropping the witness statement removes the usable witness source.
    evidence = tuple(
        f for f in base.evidence if f.id != "witness_statement_emily_01"
    )
    without = evaluate(replace(base, evidence=evidence), "medium")
    assert (
        base_report.distinct_meaningful_source_count
        == without.distinct_meaningful_source_count + 1
    )
    assert playability.PLAYABILITY_WITNESS_CONTENT_UNREACHABLE in without.issue_codes


def test_16_duplicate_evidence_on_one_object_counts_once():
    base = easy_at_min()
    extra = EvidenceSpec(
        id="extra_knife_fact_01",
        kind="forensic",
        propositions=(PropSpec(type=OTHER, object_id="kitchen_knife"),),
    )
    withdup = replace(base, evidence=tuple(base.evidence) + (extra,))
    base_report = evaluate(base, "easy")
    dup_report = evaluate(withdup, "easy")
    assert dup_report.distinct_meaningful_source_count == (
        base_report.distinct_meaningful_source_count
    )


# --------------------------------------------------------------------------- #
# reachability
# --------------------------------------------------------------------------- #


def test_17_solver_critical_evidence_without_placement_fails():
    # Drop EVERY weapon placement: the WEAPON dimension then has no reachable
    # clue and no solver-proof-used fact is reachable.
    draft = golden_draft()
    placements = tuple(
        p
        for p in draft.world_graph.placements
        if p.object_id not in {"kitchen_knife", "letter_opener", "scissors"}
    )
    draft = replace(draft, world_graph=replace(draft.world_graph, placements=placements))
    report = evaluate(draft, "easy")
    assert playability.PLAYABILITY_EVIDENCE_UNREACHABLE in report.issue_codes
    assert playability.PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE in report.issue_codes


def test_18_solver_critical_evidence_reachable_via_object_passes():
    report = evaluate(golden_draft(), "medium")
    assert playability.PLAYABILITY_EVIDENCE_UNREACHABLE not in report.issue_codes


def _link_existing_fact(draft: GeneratedDraft, object_id: str, evidence_id: str) -> GeneratedDraft:
    """Add an inspectable object whose placement links an EXISTING evidence id."""
    spec = ObjectSpec(object_id=object_id, asset_id="PROP_VASE_01", affordances=("INSPECTABLE",))
    placement = PlacementSpec(
        object_id=object_id,
        asset_id="PROP_VASE_01",
        location_id=SCENE_LOCATION,
        anchor="office_desk_01",
        interaction="read",
        evidence_id=evidence_id,
    )
    return replace(
        draft,
        objects=tuple(draft.objects) + (spec,),
        world_graph=replace(
            draft.world_graph,
            placements=tuple(draft.world_graph.placements) + (placement,),
        ),
    )


def test_adv44_07_decoy_why_does_not_satisfy_winner():
    """ADV-44-07 — a reachable DECOY motive fact does not satisfy WHY when the
    WINNING supporting motive fact is unreachable."""
    draft = _drop_objects(golden_draft(), {"motive_audit_file"})  # unlink winner motive
    # Link the EXISTING decoy contradiction fact (excluded motive).
    draft = _link_existing_fact(draft, "decoy_motive_record", "motive_no_affair_01")
    report = evaluate(draft, "medium")
    assert playability.DIMENSION_WHY in report.unreachable_critical_dimensions
    assert playability.PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE in report.issue_codes
    assert playability.PLAYABILITY_EVIDENCE_UNREACHABLE in report.issue_codes


def test_adv44_07_winner_why_reachable_passes():
    """ADV-44-07 — with the WINNING motive clue reachable, WHY is satisfied."""
    report = evaluate(golden_draft(), "medium")
    assert playability.DIMENSION_WHY in report.reachable_critical_dimensions
    assert report.valid, report.issue_codes


def test_adv44_08_supplemented_location_is_declared():
    """ADV-44-08 — supplementation binds a DECLARED world-graph location and
    leaves a structurally/safety-valid world graph."""
    from app.environments.manifests import load_environment
    from app.generation import safety
    from app.generation.pipeline import _world_graph_resolution_issues
    from app.services.generation import _ensure_dimension_clues

    draft = _drop_objects(golden_draft(), {"motive_audit_file"})
    kit = load_environment("apartment")
    supplemented = _ensure_dimension_clues(draft, kit)
    declared = {
        loc.location_id for loc in supplemented.world_graph.locations
    }
    added = [
        p
        for p in supplemented.world_graph.placements
        if str(getattr(p, "object_id", "")).startswith("dim_clue_")
    ]
    assert added, "expected at least one supplemented dimension clue"
    for placement in added:
        assert placement.location_id in declared
    object_ids = {o.object_id for o in supplemented.objects}
    evidence_ids = {e.id for e in supplemented.evidence}
    assert safety.validate_world_graph(
        supplemented.world_graph, object_ids, evidence_ids
    ) == ()
    assert _world_graph_resolution_issues(supplemented) == ()


def test_19_witness_attributed_clue_is_reachable():
    # A witness-kind fact (no proposition person id) attributed through the
    # presentation speakerName is reachable and covers WHO.
    base = golden_draft()
    witness_fact = EvidenceSpec(
        id="relayed_observation_01",
        kind="witness_statement",
        propositions=(PropSpec(type=WITNESS_CLAIMS),),
        presentation={
            "speakerName": "Emily Reed",
            "statement": "I saw the killer leave the kitchen around 22:20.",
        },
    )
    draft = replace(
        base,
        evidence=tuple(
            f for f in base.evidence if f.id != "witness_statement_emily_01"
        )
        + (witness_fact,),
    )
    public, evidence, truth = phase3(draft)
    report = playability.validate_playability_quality(
        draft=draft,
        public=public,
        evidence=evidence,
        universes=derive_universes(public),
        truth=truth,
        difficulty="easy",
    )
    assert playability.DIMENSION_WHO in report.reachable_critical_dimensions
    assert playability.PLAYABILITY_WITNESS_CONTENT_UNREACHABLE not in report.issue_codes
    assert report.valid, report.issue_codes


def test_20_all_required_dimensions_reachable_passes():
    report = evaluate(golden_draft(), "medium")
    assert report.unreachable_critical_dimensions == ()
    assert set(report.reachable_critical_dimensions) == {"WHO", "WHY", "WEAPON", "WHEN"}


# --------------------------------------------------------------------------- #
# distribution
# --------------------------------------------------------------------------- #


def test_21_all_evidence_on_one_object_fails_distribution():
    draft = golden_draft()
    # Keep exactly ONE placement-linked record (the knife forensic); pad with
    # non-meaningful interactive objects so only the distribution rule complains.
    draft = _drop_objects(
        draft,
        {
            "letter_opener",
            "scissors",
            "apartment_laptop",
            "victim_body_placeholder",
            "motive_audit_file",
            "cctv_hall_monitor",
        },
    )
    for i in range(4):
        draft = _add_inspectable(draft, f"plain_{i}", interaction="inspect")
    report = evaluate(draft, "medium")
    assert playability.PLAYABILITY_EVIDENCE_OVERCONCENTRATED in report.issue_codes


def test_22_evidence_across_required_distinct_sources_passes():
    report = evaluate(golden_draft(), "medium")
    assert report.distinct_meaningful_source_count >= 3
    assert playability.PLAYABILITY_EVIDENCE_OVERCONCENTRATED not in report.issue_codes


# --------------------------------------------------------------------------- #
# red herrings
# --------------------------------------------------------------------------- #


def test_23_medium_missing_red_herring_fails():
    draft = golden_draft()
    # Replace the two contradicted-weapon placements with non-evidence objects so
    # no meaningful object's subject is an excluded candidate.
    draft = _drop_objects(draft, {"letter_opener", "scissors"})
    draft = _add_inspectable(draft, "plain_a", interaction="inspect")
    draft = _add_inspectable(draft, "plain_b", interaction="inspect")
    report = evaluate(draft, "medium")
    assert report.red_herring_count == 0
    assert playability.PLAYABILITY_RED_HERRING_REQUIREMENT_UNMET in report.issue_codes


def test_24_red_herring_that_changes_solution_fails_solver_gate():
    # A "red herring" that actually becomes the unique survivor is NOT a red
    # herring — the existing solver gate (truth mismatch) must reject it.
    from app.generation.pipeline import AttemptRecord, validate_draft
    from app.generation.state_machine import ValidationOutcome

    base = golden_draft()
    evidence = [f for f in base.evidence if f.id != "cctv_michael_office_01"]
    evidence.append(
        EvidenceSpec(
            id="cctv_thomas_bar_01",
            kind="cctv_observation",
            propositions=(
                PropSpec(
                    type=PERSON_OBSERVED_AT_LOCATION,
                    person_id="thomas_reed",
                    location_id=BAR,
                    observed_at="2026-09-11T22:15:00+02:00",
                    uncertainty_seconds=60,
                ),
            ),
        )
    )
    evidence.append(
        EvidenceSpec(
            id="cctv_michael_scene_01",
            kind="cctv_observation",
            propositions=(
                PropSpec(
                    type=PERSON_OBSERVED_AT_LOCATION,
                    person_id="michael_carter",
                    location_id=SCENE_LOCATION,
                    observed_at="2026-09-11T22:16:40+02:00",
                    uncertainty_seconds=60,
                ),
            ),
        )
    )
    draft = replace(base, evidence=tuple(evidence))
    attempt = AttemptRecord(attempt_id="A-24", case_id="CASE-001", session_id="S")
    attempt.draft = draft
    report = validate_draft(attempt)
    # The generated truth still names thomas_reed, but the solver now uniquely
    # survives michael_carter -> the existing Gate 2 rejects (never VALID).
    assert report.outcome is not ValidationOutcome.VALID


def test_25_decorative_irrelevant_item_not_a_red_herring():
    base = evaluate(golden_draft(), "medium")
    padded = golden_draft()
    for i in range(5):
        padded = _add_inspectable(padded, f"unrelated_{i}", interaction="")
    assert evaluate(padded, "medium").red_herring_count == base.red_herring_count


# --------------------------------------------------------------------------- #
# invariants
# --------------------------------------------------------------------------- #


def test_26_homicide_semantics_unchanged():
    # HOMICIDE requires WHO; the golden homicide publishes at Medium.
    report = evaluate(golden_draft(), "medium")
    assert playability.DIMENSION_WHO in report.reachable_critical_dimensions
    assert report.valid


def _suicide_draft() -> GeneratedDraft:
    draft = golden_draft()
    crime = replace(draft.crime, resolution_type=ResolutionType.SUICIDE, murderer_id=None)
    return replace(draft, crime=crime)


def test_27_suicide_semantics_unchanged_who_not_required():
    report = evaluate(_suicide_draft(), "medium")
    assert playability.DIMENSION_WHO not in report.unreachable_critical_dimensions
    assert playability.DIMENSION_WHO not in report.reachable_critical_dimensions


def test_28_witness_murderer_still_supported():
    # A public witness (emily_reed) is the unique HOMICIDE winner and Gate 3
    # accepts the case without a witness-content rejection.
    public = golden_public()
    evidence = [f for f in golden_evidence() if f.id != "cctv_emily_bar_01"]
    evidence.append(
        EvidenceFact(
            id="cctv_thomas_bar_01",
            kind="cctv_observation",
            propositions=(
                TypedProposition(
                    type=PERSON_OBSERVED_AT_LOCATION,
                    person_id="thomas_reed",
                    location_id=BAR,
                    observed_at="2026-09-11T22:15:00+02:00",
                    uncertainty_seconds=60,
                ),
            ),
            source_ref=SourceRef(kind="record", source_id="r1"),
            reliability=Reliability.HIGH,
            presentation={"title": "t", "description": "d"},
            discoverable=True,
        )
    )
    evidence.append(
        EvidenceFact(
            id="cctv_emily_scene_01",
            kind="cctv_observation",
            propositions=(
                TypedProposition(
                    type=PERSON_OBSERVED_AT_LOCATION,
                    person_id="emily_reed",
                    location_id=SCENE_LOCATION,
                    observed_at="2026-09-11T22:16:30+02:00",
                    uncertainty_seconds=60,
                ),
            ),
            source_ref=SourceRef(kind="record", source_id="r2"),
            reliability=Reliability.HIGH,
            presentation={"title": "t", "description": "d"},
            discoverable=True,
        )
    )
    proof = solve_case(public, evidence)
    assert proof.who.winner == "emily_reed"
    assert proof.who.unique

    t = truth_variant_a()
    truth = CaseTruth(
        case_id=t.case_id,
        case_version=t.case_version,
        title=t.title,
        crime=Crime(
            type="murder",
            victim_id="sarah_miller",
            murderer_id="emily_reed",
            motive_id="cover_up_embezzlement",
            weapon_id="kitchen_knife",
            location_id=SCENE_LOCATION,
            crime_time=CrimeTime(
                canonical="2026-09-11T22:17:00+02:00",
                accusation_tolerance_seconds=300,
            ),
        ),
        timeline=(),
        persons=(),
        relationships=(),
        facts=(),
    )
    report = playability.validate_playability_quality(
        draft=golden_draft(),
        public=public,
        evidence=tuple(evidence),
        universes=derive_universes(public),
        truth=truth,
        difficulty="medium",
    )
    assert playability.PLAYABILITY_WITNESS_CONTENT_UNREACHABLE not in report.issue_codes


def test_29_witness_claims_remain_non_premise():
    public, evidence, _truth = phase3(golden_draft())
    proof = solve_case(public, evidence)
    assert "witness_statement_emily_01" not in proof.evidence_ids_used
    # WITNESS_CLAIMS never appears as a solver premise proposition type.
    premise_types = {o.proposition_type for o in proof.rule_outcomes_by_candidate}
    assert WITNESS_CLAIMS not in premise_types


def test_30_evidence_algebra_remains_idempotent():
    from test_phase43a_evidence_algebra import (  # noqa: E402
        _crime_homicide,
        _empty_spec,
        _public_homicide,
    )
    from app.generation import evidence_algebra as ea  # noqa: E402

    public = _public_homicide()
    crime = _crime_homicide()
    first = ea.complete_evidence_if_ambiguous(public, crime, _empty_spec())
    second = ea.complete_evidence_if_ambiguous(public, crime, first.evidence)
    assert not second.changed


def test_31_gate3_does_not_mutate_case_truth():
    draft = golden_draft()
    public, evidence, truth = phase3(draft)
    snapshot = copy.deepcopy(truth)
    before = truth.crime.murderer_id
    for difficulty in ("easy", "medium", "hard"):
        playability.validate_playability_quality(
            draft=draft,
            public=public,
            evidence=evidence,
            universes=derive_universes(public),
            truth=truth,
            difficulty=difficulty,
        )
    assert truth == snapshot
    assert truth.crime.murderer_id == before


def test_32_gate3_deterministic_across_repeated_runs():
    draft = golden_draft()
    public, evidence, truth = phase3(draft)
    reports = [
        playability.validate_playability_quality(
            draft=draft,
            public=public,
            evidence=evidence,
            universes=derive_universes(public),
            truth=truth,
            difficulty="medium",
        )
        for _ in range(3)
    ]
    assert reports[0] == reports[1] == reports[2]
    assert reports[0].to_dict() == reports[1].to_dict()


def test_validate_never_mutates_inputs():
    draft = golden_draft()
    public, evidence, truth = phase3(draft)
    placements_before = tuple(draft.world_graph.placements)
    objects_before = tuple(draft.objects)
    evidence_before = tuple(evidence)
    crime_before = truth.crime
    playability.validate_playability_quality(
        draft=draft,
        public=public,
        evidence=evidence,
        universes=derive_universes(public),
        truth=truth,
        difficulty="medium",
    )
    assert tuple(draft.world_graph.placements) == placements_before
    assert tuple(draft.objects) == objects_before
    assert tuple(evidence) == evidence_before
    assert truth.crime == crime_before


def test_prompt_block_injected_into_world_graph_context():
    # AC-13/AC-14: the WORLD_GRAPH stage context carries the policy-derived block
    # for the attempt's difficulty (no drift).
    from app.generation.pipeline import AttemptRecord, build_request

    for difficulty in ("easy", "medium", "hard"):
        attempt = AttemptRecord(
            attempt_id="A-1", case_id="C-1", session_id="S", difficulty=difficulty
        )
        # Minimal partial state: crime installed so context can render.
        crime = parser.parse_stage(
            GenerationStage.CASE_TRUTH, _G[GenerationStage.CASE_TRUTH]
        )
        attempt.stage_outputs[GenerationStage.CASE_TRUTH] = crime
        request = build_request(attempt, GenerationStage.WORLD_GRAPH)
        expected = difficulty_policy.difficulty_requirements_block(difficulty)
        assert expected in request.prompt_context
