"""Phase 48.1 — bounded relation-kind canonicalization + repair ownership.

Hermetic regressions for the observed Local Ollama failure:

    world_requirements.relations[0]: kind 'on_kitchen_counter' not in [...]

Covered:
- §12 bounded alias normalization (canonical target ``on_table``) and STRICT
  rejection of unsupported values (``teleported_inside`` etc.);
- §13 stage-prefixed structural failures route to the owning stage (never
  UNKNOWN) while unprefixed structural issues keep the safe UNKNOWN fallback;
- §14 a provider fixture emitting the alias on the first attempt publishes with
  the same stage-call shape (no evidence/activity-log replay);
- §15 a provider that repeats an UNSUPPORTED alias fails closed early with a
  precise structured error (never GENERATION_DEADLINE_EXCEEDED);
- §16 canonical values behave exactly as before;
- §17/§18 the published case still passes the canonical validation/playability
  gate and the Phase 47/48 fixtures are unaffected.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation import pipeline  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.repair_routing import (  # noqa: E402
    RepairOwner,
    RepairPlan,
    owner_for_report,
    owner_for_stage_prefixed_issue,
    stage_labels_for_report,
)
from app.generation.report import ValidationReport  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.ollama_driver import parse_world_requirements  # noqa: E402
from app.world.requirements import (  # noqa: E402
    CRITICALITY_REQUIRED,
    ObjectRequest,
    PlacementRelation,
    RELATION_KINDS,
    WorldRequirements,
    _RELATION_KIND_ALIASES,
    normalize_relation_kind,
)

from test_ollama_driver import (  # noqa: E402
    ICEPICK_SPEC,
    PROMPT,
    MockOllamaTransport,
    _admission,
    _alog_posts,
    _case_people,
    _controller,
    _driver_posts,
    _evidence,
    _j,
    _make_driver,
    _run,
    _staged,
    _world,
)
from test_world_composer import CATALOG, GOLDEN_PLACEMENTS, KITS  # noqa: E402

_CRIME_CANONICAL = "2026-09-11T23:42:00+02:00"
_OBSERVED_MESSAGE = (
    "world_requirements stage parse failed: "
    "world_requirements.relations[0]: kind 'on_kitchen_counter' not in "
    "['on_desk', 'on_table', 'near_victim', 'inside_cabinet', "
    "'floor_area', 'on_wall']"
)


# --------------------------------------------------------------------------- #
# §12 — bounded relation-kind normalization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        # the observed failure + the bounded counter/worktop family
        ("on_kitchen_counter", "on_table"),
        ("On Kitchen Counter", "on_table"),
        ("on-kitchen-counter", "on_table"),
        ("on_counter", "on_table"),
        ("on_worktop", "on_table"),
        ("on_bench", "on_table"),
        ("on_island", "on_table"),
        ("on_tabletop", "on_table"),
        ("on_surface", "on_table"),
        ("on_table", "on_table"),
        # desk semantics stay distinct
        ("on_desk", "on_desk"),
        ("on_bureau", "on_desk"),
        ("on_console", "on_desk"),
        # storage / floor / wall / proximity families
        ("on_shelf", "inside_cabinet"),
        ("in_the_cabinet", "inside_cabinet"),
        ("on_cabinet", "inside_cabinet"),
        ("in_drawer", "inside_cabinet"),
        ("in_cupboard", "inside_cabinet"),
        ("on_floor", "floor_area"),
        ("on the floor", "floor_area"),
        ("on_ground", "floor_area"),
        ("on_carpet", "floor_area"),
        ("on_rug", "floor_area"),
        ("on_wall", "on_wall"),
        ("on_hook", "on_wall"),
        ("wall-mounted", "on_wall"),
        ("beside_the_victim", "near_victim"),
        ("next_to_the_body", "near_victim"),
        ("beside", "near_victim"),
        ("next-to", "near_victim"),
    ],
)
def test_safe_aliases_normalize_to_the_canonical_six(raw, canonical):
    assert normalize_relation_kind(raw) is canonical
    assert canonical in RELATION_KINDS


@pytest.mark.parametrize(
    "raw",
    [
        "teleported_inside",
        "behind_the_sofa",
        "on_television",
        "inside_black_hole",
        "on_",
        "",
        None,
        123,
        ["on_table"],
        "on_table" * 40,  # beyond the bounded input length
        # ADV-48.1-02/-04 — a leading preposition never invents a mapping; the
        # old generic preposition strip inverted surface semantics.
        "in_desk",
        "inside_desk",
        "in_wall",
        "at_table",
        "at_cabinet",
        "on_on_table",
        "inside_on_table",
        "in_in_cabinet",
        "on_table_on_table",
        "not_on_table",
        "upon_counter",
        "rests_on_counter",
        "on_countertop",
        "atop_table",
    ],
)
def test_unsupported_values_are_never_normalized(raw):
    assert normalize_relation_kind(raw) is None


def test_relation_kind_vocabulary_is_still_exactly_six():
    assert RELATION_KINDS == (
        "on_desk",
        "on_table",
        "near_victim",
        "inside_cabinet",
        "floor_area",
        "on_wall",
    )


def test_alias_table_is_strict_and_never_inverts_semantics():
    """ADV-48.1-02: the closed table's image is exactly the canonical six.

    Every key maps onto its declared canonical kind (so no alias can leak a
    seventh value) and there is NO generic preposition strip: each accepted
    spelling is an explicit key.
    """
    assert set(_RELATION_KIND_ALIASES.values()) == set(RELATION_KINDS)
    for alias, canonical in _RELATION_KIND_ALIASES.items():
        assert normalize_relation_kind(alias) == canonical, alias
    # explicit preposition forms are keys; contradictory prepositions are not.
    for required in (
        "on_kitchen_counter",
        "on_counter",
        "on_worktop",
        "on_bench",
        "on_island",
        "on_tabletop",
        "on_surface",
        "desk",
        "shelf",
        "cabinet",
        "drawer",
        "cupboard",
        "floor",
        "wall",
        "beside",
    ):
        assert required in _RELATION_KIND_ALIASES, required
    for forbidden in ("in_desk", "inside_desk", "in_wall", "at_table", "at_cabinet"):
        assert forbidden not in _RELATION_KIND_ALIASES, forbidden


@pytest.mark.parametrize(
    "raw",
    [
        "on\x00table",
        "on\x01table",
        "\x01on_table",
        "on_table\x01",
        "on\ttable",
        "on\ntable",
        "on\u200b_table",
        "on\ufeff_table",
        "o\u200bn_table",
        "on\u2028table",
        "on\u2029table",
        "on\u2060table",
        "\u202e on_table \u202c",
    ],
)
def test_control_and_format_glyphs_are_rejected_before_normalization(raw):
    """ADV-48.1-05: invisible glyphs must fail strict validation, not be folded."""
    assert normalize_relation_kind(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "On-Kitchen-Counter",
        "on.kitchen.counter",
        "on the kitchen counter",
        "ON_TABLE",
        "  on_table  ",
        "on the table",
        "next-to",
        "beside the victim",
    ],
)
def test_case_and_separator_tolerance_for_listed_aliases(raw):
    assert normalize_relation_kind(raw) in RELATION_KINDS


def test_placement_relation_stays_strict():
    with pytest.raises(ValueError):
        PlacementRelation(kind="on_kitchen_counter", target="kitchen knife")
    # canonical values remain accepted.
    assert PlacementRelation(kind="on_table").kind == "on_table"
    assert PlacementRelation(kind="on_desk").kind == "on_desk"


def _world_doc(kind: str, *, name: str = "bronze ceremonial ice pick", env: str = "office"):
    return json.dumps(
        {
            "environmentHint": env,
            "locationTokens": [env],
            "objects": [{"name": name, "criticality": "required"}],
            "relations": [{"kind": kind, "target": name}],
            "unsafeUnsupported": [],
        }
    )


def test_parse_boundary_canonicalizes_the_observed_alias(caplog):
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        requirements = parse_world_requirements(
            _world_doc("on_kitchen_counter"),
            case_id="CASE-p481",
            attempt_id="GA-p481",
        )
    assert isinstance(requirements, WorldRequirements)
    assert tuple(relation.kind for relation in requirements.relations) == ("on_table",)
    events = {
        getattr(record, "pd_event", None): getattr(record, "pd_fields", {})
        for record in caplog.records
        if getattr(record, "pd_event", None)
    }
    fields = events["world.relation_kind.canonicalized"]
    assert fields["rawRelationKind"] == "on_kitchen_counter"
    assert fields["canonicalRelationKind"] == "on_table"
    assert fields["relationIndex"] == 0


def test_parse_boundary_still_rejects_unsupported_alias():
    with pytest.raises(ValueError, match="teleported_inside"):
        parse_world_requirements(_world_doc("teleported_inside"))


def test_on_table_target_composes_in_real_kits():
    """ADV-48.1-07: the alias->``on_table`` hint actually drives placement.

    ``kitchen knife`` is pre-materialized by ``GOLDEN_PLACEMENTS`` (the
    composer's "semantic identity already materialized" rule), so the composer
    ignores the relation hint for it — the previous version of this test was
    vacuous. A NON-golden decorative catalog object (``brass candlestick`` ->
    ``PROP_CANDLESTICK_01``, allowed on GENERIC_PROP + TABLE_PROP) is placed by
    the hint: the normalized ``on_kitchen_counter`` alias (-> ``on_table``)
    selects a ``TABLE_PROP`` anchor, while the un-hinted natural allocation
    lands on a different anchor type. The difference proves the hint — not a
    coincidence — drives placement.
    """
    from app.environments.resolver import resolve_environment
    from app.world.composer import KnownObjectSpecProvider, compose_world

    def anchor_type_for(kind):
        relations = (
            (PlacementRelation(kind=kind, target="brass candlestick"),)
            if kind is not None
            else ()
        )
        requirements = WorldRequirements(
            environment_hint=kit_id,
            objects=(ObjectRequest(requested_name="brass candlestick"),),
            relations=relations,
        )
        composition = compose_world(
            requirements,
            env_resolver=resolve_environment,
            spec_provider=KnownObjectSpecProvider(),
            evidence_placements=GOLDEN_PLACEMENTS,
            catalog=CATALOG,
            kit=KITS[kit_id],
        )
        assert composition.issues == (), kit_id
        placed = [
            placement
            for placement in composition.placements
            if placement.object_id == "brass_candlestick"
        ]
        assert placed, kit_id
        return KITS[kit_id].by_id[placed[0].anchor].type

    for kit_id in ("apartment", "office"):
        hinted = anchor_type_for(normalize_relation_kind("on_kitchen_counter"))
        assert hinted == "TABLE_PROP", kit_id
        natural = anchor_type_for(None)
        assert natural != "TABLE_PROP", kit_id


# --------------------------------------------------------------------------- #
# §13 — stage-prefixed structural failures route to the owning stage
# --------------------------------------------------------------------------- #


def test_observed_world_requirements_message_owns_world_graph():
    report = ValidationReport(structural_issues=(_OBSERVED_MESSAGE,))
    assert owner_for_report(report) == (RepairOwner.WORLD_GRAPH,)
    assert stage_labels_for_report(report) == ("world_requirements",)
    plan = RepairPlan.from_report(report)
    assert plan.repair_owners == (RepairOwner.WORLD_GRAPH,)
    assert plan.rerun_case is False
    assert plan.rerun_evidence is False
    assert plan.rerun_activity_log is False
    assert plan.rerun_world is True
    assert plan.recompose is True


def test_prefixed_path_without_stage_parse_phrase_still_attributes():
    issue = (
        "world_requirements.relations[0]: kind 'on_kitchen_counter' "
        "not in ['on_desk', 'on_table']"
    )
    assert owner_for_stage_prefixed_issue(issue) is RepairOwner.WORLD_GRAPH
    assert owner_for_report(ValidationReport(structural_issues=(issue,))) == (
        RepairOwner.WORLD_GRAPH,
    )


def test_unprefixed_structural_issue_stays_unknown():
    report = ValidationReport(
        structural_issues=("draft assembly failed: generated draft cannot become a public case",)
    )
    assert owner_for_report(report) == (RepairOwner.UNKNOWN,)
    assert RepairPlan.from_report(report).is_default_full_rerun


def test_safety_bucket_stays_unknown():
    report = ValidationReport(safety_issues=("generated content rejected",))
    assert owner_for_report(report) == (RepairOwner.UNKNOWN,)


def test_mixed_prefixed_and_unprefixed_keeps_safe_unknown_fallback():
    report = ValidationReport(
        structural_issues=(
            _OBSERVED_MESSAGE,
            "draft assembly failed: generated draft cannot become a public case",
        )
    )
    assert owner_for_report(report) == (
        RepairOwner.UNKNOWN,
        RepairOwner.WORLD_GRAPH,
    )
    assert RepairPlan.from_report(report).is_default_full_rerun


def test_public_world_prefix_is_deliberately_not_attributed():
    report = ValidationReport(structural_issues=("public_world: unknown key 'bad'",))
    assert owner_for_report(report) == (RepairOwner.UNKNOWN,)


def test_stage_parse_failed_requires_an_exact_token_boundary():
    """ADV-48.1-09: only the exact closed deferral phrase attributes."""
    # a character glued to the phrase, or a foreign token, never attributes.
    for issue in (
        "world_requirements stage parse failedX",
        "world_requirementsX",
        "world_requirements stage parse failed and world_graph: bad",
        "world_requirements stage parse succeeded",
    ):
        assert owner_for_stage_prefixed_issue(issue) is None, issue
    # the exact documented deferral phrase still attributes.
    assert (
        owner_for_stage_prefixed_issue("world_requirements stage parse failed")
        is RepairOwner.WORLD_GRAPH
    )
    assert (
        owner_for_stage_prefixed_issue("world_requirements stage parse failed: x")
        is RepairOwner.WORLD_GRAPH
    )


def test_world_stage_deferral_attributes_but_composition_stays_unknown():
    """ADV-48.1-06: bounded world-requirements stage deferrals attribute."""
    deferral = "world_requirements stage: unsupported environment"
    assert owner_for_stage_prefixed_issue(deferral) is RepairOwner.WORLD_GRAPH
    report = ValidationReport(structural_issues=(deferral,))
    assert owner_for_report(report) == (RepairOwner.WORLD_GRAPH,)
    assert RepairPlan.from_report(report).is_default_full_rerun is False
    # genuinely cross-stage / composition-only messages keep the safe fallback.
    for issue in (
        "world composition failed for the requested world",
        "world composition produced no usable result",
        "world_graph produced no usable result",
        "public_world: unknown key 'bad'",
        "repair: bad",
    ):
        assert owner_for_stage_prefixed_issue(issue) is None, issue


def test_case_people_structural_failure_is_owned_by_case_people():
    """ADV-48.1-08: ``case_people`` -> CASE_PEOPLE, not CASE_TRUTH."""
    assert (
        owner_for_stage_prefixed_issue("case_people: bad")
        is RepairOwner.CASE_PEOPLE
    )
    report = ValidationReport(
        structural_issues=("case_people stage parse failed: bad",)
    )
    assert owner_for_report(report) == (RepairOwner.CASE_PEOPLE,)
    assert RepairPlan.from_report(report).rerun_case is True
    # the trusted-truth-shape owner is unchanged for the truth stage.
    assert (
        owner_for_stage_prefixed_issue("case_truth: bad")
        is RepairOwner.CASE_TRUTH
    )


# --------------------------------------------------------------------------- #
# §14 — targeted hermetic repair for the exact observed alias
# --------------------------------------------------------------------------- #


def test_alias_world_publishes_without_repair_or_unrelated_replay(caplog):
    alias_world = _world()
    alias_world["environmentHint"] = "office"
    alias_world["locationTokens"] = ["office"]
    alias_world["relations"] = [
        {"kind": "on_kitchen_counter", "target": "bronze ceremonial ice pick"}
    ]
    posts = _driver_posts(
        _j(_case_people()), _j(_evidence()), _j(alias_world), _CRIME_CANONICAL
    ) + [ICEPICK_SPEC]
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, transport = _run(posts)

    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    assert record.deferred_structural == ()
    assert record.last_validation is not None and record.last_validation.valid is True
    assert record.solver_proof is not None
    assert record.solver_proof.who.unique
    assert record.solver_proof.why.unique
    assert record.solver_proof.weapon.unique

    # The alias is canonicalized BEFORE the parser, so there is no parse retry
    # and no targeted repair pass: the clean Core call shape is unchanged.
    assert transport.call_count == 9
    prompt_calls = transport.post_calls
    assert sum("case_people_v1" in p for p in prompt_calls) == 1
    assert sum("evidence_v1" in p for p in prompt_calls) == 1
    assert sum("Canonical evidence time:" in p for p in prompt_calls) == 5
    assert sum("world_requirements_v1" in p for p in prompt_calls) == 1
    assert sum("asset_spec_v1" in p for p in prompt_calls) == 1

    events = [
        getattr(event, "pd_event", None) for event in caplog.records
    ]
    assert "world.relation_kind.canonicalized" in events
    assert "generation.repair.started" not in events


# --------------------------------------------------------------------------- #
# §15 — repeated unsupported alias fails closed early, never by deadline
# --------------------------------------------------------------------------- #


def test_repeated_unsupported_alias_fails_early_with_precise_error():
    # A KNOWN catalog weapon keeps the world/asset path out of the picture, so
    # the call shape isolates the world-requirements stage.
    prompt = (
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
        "Motive: stolen research data\nWeapon: kitchen knife\nTime: 23:42\n"
        "Witness: Lisa König\nLocation: office\n"
    )
    invalid = _world(unknown_name="kitchen knife")
    invalid["relations"] = [
        {"kind": "teleported_inside", "target": "kitchen knife"}
    ]
    invalid_json = _j(invalid)
    # Initial world (call + bounded parse retry) then one targeted world repair
    # (call + bounded parse retry). No evidence/activity-log replay.
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife")),
        *_alog_posts(_CRIME_CANONICAL),
        invalid_json,
        invalid_json,
        invalid_json,
        invalid_json,
    ]
    record, transport = _run(
        posts, prompt=prompt, max_repair_passes=2
    )

    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.failure_code == "STRUCTURED_OUTPUT_INVALID"
    assert "repair made no progress" in (record.reason or "")
    assert "WORLD_GRAPH" in (record.reason or "")
    assert "failingStage=world_requirements" in (record.reason or "")
    assert "deadline" not in (record.reason or "").casefold()
    assert record.budget is not None and record.budget.deadline_passed() is False
    # Fail closed after the FIRST unchanged TARGETED repair (one repair pass),
    # not after burning the full repair budget.
    assert record.budget.repair_passes == 1

    prompt_calls = transport.post_calls
    assert sum("evidence_v1" in p for p in prompt_calls) == 1
    assert sum("Canonical evidence time:" in p for p in prompt_calls) == 5
    # 2 calls (initial + bounded retry) + 2 calls (targeted repair + retry).
    assert sum("world_requirements_v1" in p for p in prompt_calls) == 4
    assert not any("asset_spec_v1" in p for p in prompt_calls)


# --------------------------------------------------------------------------- #
# §8/§D — the fail-closed branch keys off the plan ACTUALLY executed
# --------------------------------------------------------------------------- #


_UNPREFIXED_STRUCTURAL = (
    "draft assembly failed: generated draft cannot become a public case"
)
_PREFIXED_WORLD_STRUCTURAL = (
    "world_requirements.relations[0]: kind 'on_kitchen_counter' not in []"
)


def _drive_reports(monkeypatch, reports, *, max_repair_passes=3):
    """Drive a REAL controller with a controlled report sequence + no-op driver.

    ZERO provider calls. Returns the terminal attempt and the closed repair
    owner tuples the driver actually executed (``None`` for the initial pass).
    """
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=[])
    driver = _make_driver(transport)
    executed: list[tuple[str, ...] | None] = []

    def fake_run_into(attempt, diagnostics=(), repair_plan=None):
        executed.append(
            tuple(owner.value for owner in repair_plan.repair_owners)
            if repair_plan is not None
            else None
        )
        return None

    monkeypatch.setattr(driver, "run_into", fake_run_into)
    monkeypatch.setattr(driver, "reset_attempt_cache", lambda _aid: None)

    queue = list(reports)

    def fake_validate(attempt, *, event_prefix="generation.validation"):
        report = queue.pop(0) if queue else reports[-1]
        attempt.last_validation = report
        return report

    monkeypatch.setattr(pipeline, "validate_draft", fake_validate)

    controller = _controller(
        driver, transport, admission, clock, ids,
        max_repair_passes=max_repair_passes,
    )
    handle = controller.start_generation(
        PROMPT, anonymous_quota_session_id=session.session_id
    )
    return controller.attempt(handle.attempt_id), executed


def test_fail_closed_uses_the_plan_actually_executed(monkeypatch):
    """ADV-48.1-01: an UNKNOWN full-rerun unchanged repair must not fail closed.

    The repair that ACTUALLY ran is the decision source (not the current
    re-validation report's plan). An UNKNOWN/default-full-rerun repair that
    reproduces the same fingerprint keeps the legacy behavior and proceeds to a
    second pass; only the targeted (WORLD_GRAPH) repair fails closed.
    """
    unprefixed = ValidationReport(
        structural_issues=(_UNPREFIXED_STRUCTURAL,)
    )
    prefixed = ValidationReport(
        structural_issues=(_PREFIXED_WORLD_STRUCTURAL,)
    )

    # Scenario A: the first repair EXECUTED as UNKNOWN, but the re-validation
    # report is targeted WORLD_GRAPH with the SAME validator-code set. The old
    # code (keyed off the current report) failed closed after ONE pass; the fix
    # must NOT: it consumes a SECOND repair pass (the legacy behavior).
    attempt_a, executed_a = _drive_reports(
        monkeypatch, [unprefixed, prefixed, prefixed, prefixed]
    )
    assert executed_a[0] is None
    assert executed_a[1] == ("UNKNOWN",)
    assert executed_a[2] == ("WORLD_GRAPH",)  # a SECOND repair ran
    assert attempt_a.budget.repair_passes == 2
    # the second (targeted) unchanged repair DOES fail closed.
    assert attempt_a.state is GenerationState.FAILED
    assert attempt_a.failure_code == "STRUCTURED_OUTPUT_INVALID"
    assert "repair made no progress" in (attempt_a.reason or "")

    # Scenario B: every repair EXECUTED as UNKNOWN -> legacy budget exhaustion,
    # never the early fail-closed branch.
    attempt_b, executed_b = _drive_reports(
        monkeypatch, [unprefixed, unprefixed, unprefixed, unprefixed]
    )
    assert set(executed_b[1:]) == {("UNKNOWN",)}
    assert attempt_b.failure_code == "REPAIR_BUDGET_EXHAUSTED"
    assert attempt_b.budget.repair_passes == 3

    # Scenario C: a targeted repair with the same fingerprint fails closed
    # after exactly ONE pass.
    attempt_c, executed_c = _drive_reports(
        monkeypatch, [prefixed, prefixed, prefixed, prefixed]
    )
    assert executed_c[1] == ("WORLD_GRAPH",)
    assert attempt_c.failure_code == "STRUCTURED_OUTPUT_INVALID"
    assert "repair made no progress" in (attempt_c.reason or "")
    assert attempt_c.budget.repair_passes == 1


# --------------------------------------------------------------------------- #
# §16 — canonical provider output is unchanged
# --------------------------------------------------------------------------- #


def test_canonical_values_still_validate_and_behave_identically():
    for kind in ("on_desk", "on_table"):
        requirements = parse_world_requirements(_world_doc(kind))
        assert tuple(relation.kind for relation in requirements.relations) == (kind,)
    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 9


# --------------------------------------------------------------------------- #
# §E — the single canonical schema path teaches the exact six kinds
# --------------------------------------------------------------------------- #


def test_world_prompt_and_transport_schema_expose_the_six_kinds():
    blob = prompts.build_world_requirements_prompt(PROMPT, None)
    for kind in RELATION_KINDS:
        assert kind in blob
    schema = prompts.schema_contract_as_json_schema("world_requirements")
    kind_schema = schema["properties"]["relations"]["items"]["properties"]["kind"]
    assert kind_schema == {"type": "string", "enum": sorted(RELATION_KINDS)}
    # The Direct-Ollama transport mapping (``world_graph``) derives the same
    # canonical schema; the bridge receives this prompt text and no competing
    # world schema (documented free-form JSON fallback for this schema id).
    assert (
        prompts.json_schema_for_generation_stage("world_graph")
        == prompts.schema_contract_as_json_schema("world_requirements")
    )
