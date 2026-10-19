r"""Phase 48.1 ADVERSARIAL FIX BATCH — INDEPENDENT QA PROBE (QA-owned).

Re-validates the backend-dev's targeted fixes for the Phase 48.1 adversarial
findings ADV-48.1-01/02/04/05/06/07/08/09/10 *independently of the product
tests*. Hermetic: ZERO network, ZERO Ollama, ZERO paid calls. Nothing in
product source is modified.

Run from ``backend/``::

    python ..\screenshots\evidence\phase48a\qa_advfix\qa_advfix_probe.py

Writes ``qa_advfix_probe_output.txt`` (via the caller) and
``qa_advfix_probe_results.json`` next to this file.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve()
REPO = _HERE.parents[4]          # <repo>/screenshots/evidence/phase48a/qa_advfix
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

RESULTS: list[dict] = []


def check(section: str, name: str, condition: bool, detail: str = "") -> bool:
    ok = bool(condition)
    RESULTS.append(
        {"section": section, "name": name, "ok": ok, "detail": str(detail)}
    )
    print(f"[{'PASS' if ok else 'FAIL'}] {section} :: {name} {detail}")
    return ok


def section(title: str) -> None:
    print(f"\n===== {title} =====")


# --------------------------------------------------------------------------- #
# Section 1 — ADV-48.1-02 (semantic inversion) + ADV-48.1-04 (multi-token)
# --------------------------------------------------------------------------- #
def section_semantic() -> None:
    section("1. ADV-48.1-02/-04 semantic inversion + multi-token rejection")
    from app.world.requirements import (
        RELATION_KINDS,
        RELATION_TO_ANCHOR_TYPES,
        PlacementRelation,
        _RELATION_KIND_ALIASES,
        normalize_relation_kind,
    )

    rejected = [
        "in_desk",
        "inside_desk",
        "in_wall",
        "at_table",
        "at_cabinet",
        "on_on_table",
        "inside_on_table",
    ]
    for raw in rejected:
        got = normalize_relation_kind(raw)
        check("02_semantic", f"normalize_relation_kind({raw!r}) is None", got is None, f"got={got!r}")

    documented = {
        "on_kitchen_counter": "on_table",
        "On Kitchen Counter": "on_table",
        "on-kitchen-counter": "on_table",
        "on_counter": "on_table",
        "on_worktop": "on_table",
        "on_bench": "on_table",
        "on_island": "on_table",
        "on_tabletop": "on_table",
        "on_surface": "on_table",
        "on_table": "on_table",
        "on_desk": "on_desk",
        "on_bureau": "on_desk",
        "on_console": "on_desk",
        "on_shelf": "inside_cabinet",
        "on_cabinet": "inside_cabinet",
        "in_drawer": "inside_cabinet",
        "in_cupboard": "inside_cabinet",
        "on_floor": "floor_area",
        "on the floor": "floor_area",
        "on_ground": "floor_area",
        "on_carpet": "floor_area",
        "on_wall": "on_wall",
        "on_hook": "on_wall",
        "wall-mounted": "on_wall",
        "beside_the_victim": "near_victim",
        "next_to_the_body": "near_victim",
        "beside": "near_victim",
        "next-to": "near_victim",
    }
    bad = {r: normalize_relation_kind(r) for r, c in documented.items() if normalize_relation_kind(r) != c}
    check("02_semantic", "documented aliases normalize to declared canonicals", not bad, f"mismatches={bad}")

    # on_desk / on_table stay DISTINCT (the fix must not collapse them).
    d = normalize_relation_kind("on_desk")
    t = normalize_relation_kind("on_table")
    check("02_semantic", "on_desk stays on_desk", d == "on_desk", f"got={d!r}")
    check("02_semantic", "on_table stays on_table", t == "on_table", f"got={t!r}")
    check("02_semantic", "on_desk != on_table", d != t, f"{d!r} vs {t!r}")

    # alias-table IMAGE is EXACTLY the six canonical kinds.
    image = set(_RELATION_KIND_ALIASES.values())
    canonical = set(RELATION_KINDS)
    check("02_semantic", "alias table image == canonical six", image == canonical, f"image={sorted(image)}")
    check("02_semantic", "canonical vocabulary has exactly six", len(canonical) == 6, f"{sorted(canonical)}")
    check(
        "02_semantic",
        "RELATION_KINDS is exactly the documented six (order too)",
        RELATION_KINDS
        == ("on_desk", "on_table", "near_victim", "inside_cabinet", "floor_area", "on_wall"),
        f"{RELATION_KINDS!r}",
    )
    check(
        "02_semantic",
        "RELATION_TO_ANCHOR_TYPES keys == exactly the six",
        set(RELATION_TO_ANCHOR_TYPES) == canonical and len(RELATION_TO_ANCHOR_TYPES) == 6,
        f"{sorted(RELATION_TO_ANCHOR_TYPES)}",
    )

    # the strict dataclass never accepts a rejected/inverted label.
    try:
        PlacementRelation(kind="in_desk")
        check("02_semantic", "PlacementRelation(kind='in_desk') raises", False, "no error")
    except ValueError:
        check("02_semantic", "PlacementRelation(kind='in_desk') raises", True)
    check("02_semantic", "PlacementRelation(kind='on_table') ok", PlacementRelation(kind="on_table").kind == "on_table")


# --------------------------------------------------------------------------- #
# Section 2 — ADV-48.1-05 (control/format glyphs) + ordinary separators
# --------------------------------------------------------------------------- #
def section_glyphs() -> None:
    section("2. ADV-48.1-05 glyph rejection + ordinary separators still work")
    from app.world.requirements import normalize_relation_kind
    from app.services.ollama_driver import parse_world_requirements

    glyphs = [
        "on\x00table",
        "on\u200b_table",
        "\u202e on_table \u202c",
        "on\ufeff_table",
    ]
    for raw in glyphs:
        got = normalize_relation_kind(raw)
        check("05_glyphs", f"reject glyph payload {raw!r}", got is None, f"got={got!r}")

    for raw, expected in {
        "on table": "on_table",
        "on-table": "on_table",
        "on_table": "on_table",
        "on.table": "on_table",
    }.items():
        got = normalize_relation_kind(raw)
        check("05_glyphs", f"ordinary separator {raw!r} -> {expected}", got == expected, f"got={got!r}")

    # end-to-end: the parse boundary must REJECT, not fold, a glyph-bearing kind.
    doc = json.dumps(
        {
            "environmentHint": "office",
            "locationTokens": ["office"],
            "objects": [{"name": "bronze ceremonial ice pick", "criticality": "required"}],
            "relations": [{"kind": "on\x00table", "target": "bronze ceremonial ice pick"}],
            "unsafeUnsupported": [],
        }
    )
    try:
        parse_world_requirements(doc, case_id="CASE-qa", attempt_id="GA-qa")
        check("05_glyphs", "parse_world_requirements rejects NUL kind", False, "no error")
    except ValueError:
        check("05_glyphs", "parse_world_requirements rejects NUL kind", True)


# --------------------------------------------------------------------------- #
# Section 3 — ADV-48.1-06/08/09 owner routing
# --------------------------------------------------------------------------- #
def section_routing() -> None:
    section("3. ADV-48.1-06/-08/-09 closed stage-prefix routing")
    from app.generation.repair_routing import (
        RepairOwner,
        RepairPlan,
        owner_for_report,
        owner_for_stage_prefixed_issue,
        stage_labels_for_report,
    )
    from app.generation.report import ValidationReport

    OBSERVED = (
        "world_requirements stage parse failed: "
        "world_requirements.relations[0]: kind 'on_kitchen_counter' not in "
        "['on_desk', 'on_table', 'near_victim', 'inside_cabinet', "
        "'floor_area', 'on_wall']"
    )

    # exact rejected forms must be None -> UNKNOWN
    for issue in (
        "world_requirementsX",
        "world_requirements stage parse failedX",
        "world_requirements stage parse failed and world_graph: bad",
    ):
        got = owner_for_stage_prefixed_issue(issue)
        check("06_09_routing", f"{issue!r} -> None", got is None, f"got={got!r}")
        rep = ValidationReport(structural_issues=(issue,))
        check(
            "06_09_routing",
            f"{issue!r} -> owner_for_report UNKNOWN",
            owner_for_report(rep) == (RepairOwner.UNKNOWN,),
            f"{owner_for_report(rep)}",
        )

    # exact attributed forms
    for issue, expected in {
        OBSERVED: RepairOwner.WORLD_GRAPH,
        "world_requirements stage parse failed": RepairOwner.WORLD_GRAPH,
        "world_requirements stage parse failed: detail": RepairOwner.WORLD_GRAPH,
        "world_requirements.relations[0]: kind 'x' not in []": RepairOwner.WORLD_GRAPH,
        "world_requirements: bad": RepairOwner.WORLD_GRAPH,
        "world_requirements stage: unsupported environment": RepairOwner.WORLD_GRAPH,
        "world_requirements stage produced no usable content": RepairOwner.WORLD_GRAPH,
    }.items():
        got = owner_for_stage_prefixed_issue(issue)
        check("06_09_routing", f"attribute {issue[:48]!r} -> {expected.value}", got is expected, f"got={got!r}")

    rep = ValidationReport(structural_issues=(OBSERVED,))
    check("06_09_routing", "observed message owner == (WORLD_GRAPH,)", owner_for_report(rep) == (RepairOwner.WORLD_GRAPH,), f"{owner_for_report(rep)}")
    check("06_09_routing", "observed message label == ('world_requirements',)", stage_labels_for_report(rep) == ("world_requirements",), f"{stage_labels_for_report(rep)}")
    plan = RepairPlan.from_report(rep)
    check("06_09_routing", "observed plan targeted (not default full rerun)", not plan.is_default_full_rerun, f"owners={plan.repair_owners}")
    check(
        "06_09_routing",
        "observed plan reruns world+recompose only",
        plan.rerun_world and plan.recompose and not plan.rerun_case and not plan.rerun_evidence and not plan.rerun_activity_log,
        f"case={plan.rerun_case} ev={plan.rerun_evidence} log={plan.rerun_activity_log} world={plan.rerun_world} recomp={plan.recompose}",
    )

    # cross-stage / public_world / repair / composition stay UNKNOWN
    for issue in (
        "public_world: unknown key 'bad'",
        "repair: bad",
        "world composition failed for the requested world",
        "world composition produced no usable result",
        "world_graph produced no usable result",
    ):
        got = owner_for_stage_prefixed_issue(issue)
        check("06_08_routing", f"stays UNKNOWN {issue!r}", got is None, f"got={got!r}")

    # ADV-48.1-08 — case_people -> CASE_PEOPLE (not CASE_TRUTH)
    for issue, expected in {
        "case_people: bad": RepairOwner.CASE_PEOPLE,
        "case_people stage parse failed: bad": RepairOwner.CASE_PEOPLE,
        "case_truth: bad": RepairOwner.CASE_TRUTH,
    }.items():
        got = owner_for_stage_prefixed_issue(issue)
        check("08_routing", f"{issue!r} -> {expected.value}", got is expected, f"got={got!r}")
    cp_plan = RepairPlan.from_report(ValidationReport(structural_issues=("case_people stage parse failed: bad",)))
    check("08_routing", "case_people plan reruns case", cp_plan.rerun_case, f"owners={cp_plan.repair_owners}")

    # mixed prefixed + unprefixed keeps the safe UNKNOWN fallback
    mixed = ValidationReport(structural_issues=(OBSERVED, "draft assembly failed: x"))
    check(
        "06_routing",
        "mixed prefixed+unprefixed -> {UNKNOWN, WORLD_GRAPH}",
        owner_for_report(mixed) == (RepairOwner.UNKNOWN, RepairOwner.WORLD_GRAPH),
        f"{owner_for_report(mixed)}",
    )
    check("06_routing", "mixed plan is_default_full_rerun", RepairPlan.from_report(mixed).is_default_full_rerun)

    # safety bucket always UNKNOWN
    safety = ValidationReport(safety_issues=("generated content rejected",))
    check("06_routing", "safety bucket -> UNKNOWN", owner_for_report(safety) == (RepairOwner.UNKNOWN,), f"{owner_for_report(safety)}")


# --------------------------------------------------------------------------- #
# Section 4 — ADV-48.1-01 fail-closed keyed to the plan ACTUALLY executed
# --------------------------------------------------------------------------- #
def section_failclosed() -> None:
    section("4. ADV-48.1-01 fail-closed uses the plan ACTUALLY executed")
    from app.generation import pipeline
    from app.generation.repair_routing import RepairPlan
    from app.generation.report import ValidationReport
    from app.generation.state_machine import GenerationState

    import test_ollama_driver as tod

    _UNPREFIXED = "draft assembly failed: generated draft cannot become a public case"
    _PREFIXED = "world_requirements.relations[0]: kind 'on_kitchen_counter' not in []"

    def drive(reports, *, max_repair_passes=3, emulate_old_current_report=False):
        clock, ids = tod.ManualClock(), tod.IdSource()
        admission = tod._admission(clock, ids)
        session = admission.create_anonymous_quota_session()
        transport = tod.MockOllamaTransport(posts=[])
        driver = tod._make_driver(transport)
        executed: list = []

        def fake_run_into(attempt, diagnostics=(), repair_plan=None):
            executed.append(
                tuple(o.value for o in repair_plan.repair_owners)
                if repair_plan is not None
                else None
            )
            return None

        driver.run_into = fake_run_into
        driver.reset_attempt_cache = lambda _aid: None
        queue = list(reports)

        def fake_validate(attempt, *, event_prefix="generation.validation"):
            rep = queue.pop(0) if queue else reports[-1]
            attempt.last_validation = rep
            return rep

        controller = tod._controller(
            driver, transport, admission, clock, ids, max_repair_passes=max_repair_passes
        )
        if emulate_old_current_report:
            # Emulate the OLD (buggy) decision source: overwrite the stored
            # "plan actually executed" with the CURRENT re-validation report's
            # freshly derived plan right before the fail-closed branch reads it.
            orig = controller._emit_repair_outcome

            def wrapped(attempt, *, before_codes, after_codes):
                eff = orig(attempt, before_codes=before_codes, after_codes=after_codes)
                if attempt.last_validation is not None:
                    attempt._last_repair_plan = RepairPlan.from_report(attempt.last_validation)
                return eff

            controller._emit_repair_outcome = wrapped  # type: ignore[assignment]

        with mock.patch.object(pipeline, "validate_draft", fake_validate):
            handle = controller.start_generation(
                tod.PROMPT, anonymous_quota_session_id=session.session_id
            )
            return controller.attempt(handle.attempt_id), executed

    unprefixed = ValidationReport(structural_issues=(_UNPREFIXED,))
    prefixed = ValidationReport(structural_issues=(_PREFIXED,))

    # Scenario A — first repair EXECUTED as UNKNOWN, re-validation report is
    # targeted WORLD_GRAPH with the SAME code set. The fix must NOT fail closed
    # (it consumes a 2nd pass); only the 2nd targeted unchanged repair does.
    attempt_a, executed_a = drive([unprefixed, prefixed, prefixed, prefixed])
    check("01_failclosed", "A: executed plans [None, UNKNOWN, WORLD_GRAPH]", executed_a[:3] == [None, ("UNKNOWN",), ("WORLD_GRAPH",)], f"executed={executed_a}")
    check("01_failclosed", "A: consumed 2 repair passes", attempt_a.budget.repair_passes == 2, f"passes={attempt_a.budget.repair_passes}")
    check("01_failclosed", "A: ended FAILED/STRUCTURED_OUTPUT_INVALID", attempt_a.state is GenerationState.FAILED and attempt_a.failure_code == "STRUCTURED_OUTPUT_INVALID", f"state={attempt_a.state} code={attempt_a.failure_code}")
    check("01_failclosed", "A: precise no-progress reason", "repair made no progress" in (attempt_a.reason or ""), f"reason={attempt_a.reason!r}")

    # Scenario B — every repair EXECUTED as UNKNOWN -> legacy budget exhaustion.
    attempt_b, executed_b = drive([unprefixed, unprefixed, unprefixed, unprefixed])
    check("01_failclosed", "B: all repairs UNKNOWN", set(executed_b[1:]) == {("UNKNOWN",)}, f"executed={executed_b}")
    check("01_failclosed", "B: legacy REPAIR_BUDGET_EXHAUSTED", attempt_b.failure_code == "REPAIR_BUDGET_EXHAUSTED", f"code={attempt_b.failure_code}")
    check("01_failclosed", "B: consumed all 3 passes", attempt_b.budget.repair_passes == 3, f"passes={attempt_b.budget.repair_passes}")

    # Scenario C — targeted repair from the start fails closed after ONE pass.
    attempt_c, executed_c = drive([prefixed, prefixed, prefixed, prefixed])
    check("01_failclosed", "C: first repair WORLD_GRAPH", executed_c[1] == ("WORLD_GRAPH",), f"executed={executed_c}")
    check("01_failclosed", "C: fail closed after 1 pass", attempt_c.failure_code == "STRUCTURED_OUTPUT_INVALID" and attempt_c.budget.repair_passes == 1, f"code={attempt_c.failure_code} passes={attempt_c.budget.repair_passes}")

    # Differential D — emulating the OLD current-report decision source on the
    # Scenario-A sequence makes it fail closed after ONE pass (proof the fix's
    # plan source is what changes the behaviour).
    attempt_d, executed_d = drive(
        [unprefixed, prefixed, prefixed, prefixed], emulate_old_current_report=True
    )
    check(
        "01_failclosed",
        "D(old-behaviour): fails closed after 1 pass",
        attempt_d.failure_code == "STRUCTURED_OUTPUT_INVALID"
        and attempt_d.budget.repair_passes == 1,
        f"code={attempt_d.failure_code} passes={attempt_d.budget.repair_passes} executed={executed_d}",
    )


# --------------------------------------------------------------------------- #
# Section 5 — ADV-48.1-07 non-vacuous composition test
# --------------------------------------------------------------------------- #
def section_composition() -> None:
    section("5. ADV-48.1-07 composition test is non-vacuous (non-golden object)")
    from app.environments.resolver import resolve_environment
    from app.world.composer import KnownObjectSpecProvider, compose_world
    from app.world.requirements import (
        ObjectRequest,
        PlacementRelation,
        WorldRequirements,
        normalize_relation_kind,
    )

    import test_world_composer as twc

    golden_ids = {getattr(p, "object_id", None) for p in twc.GOLDEN_PLACEMENTS}
    check(
        "07_compose",
        "'brass_candlestick' is NOT a golden placement",
        "brass_candlestick" not in golden_ids,
        f"golden_has={sorted(x for x in golden_ids if x)}",
    )

    def _anchor_type_for(kit_id: str, kind):
        relations = (
            (PlacementRelation(kind=kind, target="brass candlestick"),) if kind else ()
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
            evidence_placements=twc.GOLDEN_PLACEMENTS,
            catalog=twc.CATALOG,
            kit=twc.KITS[kit_id],
        )
        placed = [p for p in composition.placements if p.object_id == "brass_candlestick"]
        if composition.issues or not placed:
            return None, composition.issues
        return twc.KITS[kit_id].by_id[placed[0].anchor].type, composition.issues

    def _reproduce_test_assertions(kit_id: str, normalize_fn):
        """The EXACT production of the rewritten product test, parameterised by
        the alias mapper so a neutralized hint can be injected."""
        hinted, _ = _anchor_type_for(kit_id, normalize_fn("on_kitchen_counter"))
        assert hinted == "TABLE_PROP", (kit_id, "hinted", hinted)
        natural, _ = _anchor_type_for(kit_id, None)
        assert natural != "TABLE_PROP", (kit_id, "natural", natural)
        return hinted, natural

    for kit_id in ("apartment", "office"):
        hinted, natural = _reproduce_test_assertions(kit_id, normalize_relation_kind)
        check("07_compose", f"{kit_id}: alias on_kitchen_counter -> TABLE_PROP", hinted == "TABLE_PROP", f"hinted={hinted!r}")
        check("07_compose", f"{kit_id}: hint IGNORED (no relation) -> {natural!r} != TABLE_PROP", natural != "TABLE_PROP", f"natural={natural!r}")

    # Throwaway neutralization: if the alias->on_table hint did not exist (the
    # mapper returned None = hint ignored), the product test's assertion
    # ``hinted == "TABLE_PROP"`` MUST fail. This proves the test is non-vacuous.
    for kit_id in ("apartment", "office"):
        try:
            _reproduce_test_assertions(kit_id, lambda _raw: None)
            check("07_compose", f"{kit_id}: neutralized hint makes the test FAIL", False, "test still passed with hint ignored (VACUOUS)")
        except AssertionError as exc:
            check("07_compose", f"{kit_id}: neutralized hint makes the test FAIL", True, f"AssertionError={exc}")


# --------------------------------------------------------------------------- #
# Section 6 — ADV-48.1-10 CORE exhaustion during a repair loop
# --------------------------------------------------------------------------- #
def section_coverage() -> None:
    section("6. ADV-48.1-10 CORE exhaustion genuinely during a repair loop")
    import logging as _logging

    from app.core.config import Settings
    from app.generation.ollama_provider import OllamaProvider
    from app.generation.state_machine import GenerationState
    from app.services.ollama_driver import OllamaStageDriver

    import test_ollama_driver as tod

    posts = [
        tod._j(tod._case_people(weapon="kitchen_knife")),
        tod._j(tod._evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *tod._alog_posts("2026-09-11T23:42:00+02:00"),
        "<not-json>",  # pass-1 world (strict parse fail -> WORLD_GRAPH repair)
        "<not-json>",  # pass-1 world bounded retry
        "<not-json>",  # repair world re-run (CORE call 10)
        "<not-json>",  # repair bounded retry -> CORE cap (call 11)
    ]

    class RecordingTransport:
        def __init__(self, posts):
            self.posts = list(posts)
            self.post_calls = []

        def post_json(self, url, payload, timeout):
            self.post_calls.append(payload.get("messages", [{}])[0].get("content", ""))
            content = self.posts.pop(0) if self.posts else "<not-json>"
            return 200, json.dumps(
                {"model": tod.OLLAMA_MODEL, "message": {"role": "assistant", "content": content}}
            ).encode()

        def get(self, url, timeout):
            return 200, json.dumps({"models": [{"name": tod.OLLAMA_MODEL}]}).encode()

    transport = RecordingTransport(posts)

    def factory():
        return OllamaProvider(
            base_url=tod.OLLAMA_BASE,
            model=tod.OLLAMA_MODEL,
            timeout_seconds=5,
            transport=transport,
        )

    driver = OllamaStageDriver(settings=Settings(), provider_factory=factory)
    clock, ids = tod.ManualClock(), tod.IdSource()
    admission = tod._admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    controller = tod._controller(
        driver,
        transport,
        admission,
        clock,
        ids,
        max_llm_calls_per_generation=60,
        max_core_llm_calls=10,
        max_repair_passes=10,
        max_full_regenerations=1,
    )

    events: list[str] = []

    class _Capture(_logging.Handler):
        def emit(self, record):
            ev = getattr(record, "pd_event", None)
            if ev:
                events.append(ev)

    cap = _Capture()
    logger = _logging.getLogger("procedural-detective")
    previous_level = logger.level
    logger.setLevel(_logging.INFO)
    logger.addHandler(cap)
    try:
        handle = controller.start_generation(
            tod.PROMPT, anonymous_quota_session_id=session.session_id
        )
        attempt = controller.attempt(handle.attempt_id)
    finally:
        logger.removeHandler(cap)
        logger.setLevel(previous_level)

    world_calls = sum("world_requirements_v1" in p for p in transport.post_calls)
    check("10_coverage", "state FAILED", attempt.state is GenerationState.FAILED, f"state={attempt.state}")
    check("10_coverage", "narrow CORE code wins", attempt.failure_code == "CORE_PROVIDER_CALL_BUDGET_EXHAUSTED", f"code={attempt.failure_code}")
    check("10_coverage", "core_calls == 10 (past the 9-call clean pass)", attempt.budget.core_calls == 10, f"core={attempt.budget.core_calls}")
    check("10_coverage", "repair_passes == 1 (a repair WAS in flight)", attempt.budget.repair_passes == 1, f"passes={attempt.budget.repair_passes}")
    check("10_coverage", "world stage re-ran during the repair (>=3 calls)", world_calls >= 3, f"world_calls={world_calls}")
    check("10_coverage", "generation.repair.started emitted", "generation.repair.started" in events, f"events={sorted(set(events))}")


def section_parse_boundary() -> None:
    section("2b. parse-boundary canonicalization (observed alias) + strict reject")
    import logging as _logging

    from app.services.ollama_driver import parse_world_requirements

    def _doc(kind: str) -> str:
        return json.dumps(
            {
                "environmentHint": "office",
                "locationTokens": ["office"],
                "objects": [
                    {"name": "bronze ceremonial ice pick", "criticality": "required"}
                ],
                "relations": [{"kind": kind, "target": "bronze ceremonial ice pick"}],
                "unsafeUnsupported": [],
            }
        )

    logger = _logging.getLogger("procedural-detective")
    prev = logger.level
    logger.setLevel(_logging.INFO)
    captured: list = []

    class _Cap(_logging.Handler):
        def emit(self, record):
            captured.append(
                (getattr(record, "pd_event", None), getattr(record, "pd_fields", {}))
            )

    cap = _Cap()
    logger.addHandler(cap)
    try:
        reqs = parse_world_requirements(_doc("on_kitchen_counter"), case_id="C-QA", attempt_id="G-QA")
    finally:
        logger.removeHandler(cap)
        logger.setLevel(prev)

    kinds = tuple(r.kind for r in reqs.relations)
    check("02b_parse", "observed alias canonicalizes to on_table", kinds == ("on_table",), f"kinds={kinds}")
    ev = {e: f for e, f in captured if e == "world.relation_kind.canonicalized"}
    check("02b_parse", "canonicalization event emitted once", len(ev) == 1, f"count={len(ev)}")
    if ev:
        fields = next(iter(ev.values()))
        check(
            "02b_parse",
            "event fields bounded + correct",
            fields.get("rawRelationKind") == "on_kitchen_counter"
            and fields.get("canonicalRelationKind") == "on_table"
            and fields.get("relationIndex") == 0,
            f"{fields}",
        )

    try:
        parse_world_requirements(_doc("teleported_inside"), case_id="C-QA", attempt_id="G-QA")
        check("02b_parse", "unsupported alias still rejected", False, "no error")
    except ValueError as exc:
        check("02b_parse", "unsupported alias still rejected", "teleported_inside" in str(exc), f"{exc}")


def main() -> int:
    for fn in (
        section_semantic,
        section_glyphs,
        section_parse_boundary,
        section_routing,
        section_failclosed,
        section_composition,
        section_coverage,
    ):
        try:
            fn()
        except Exception:  # noqa: BLE001
            check(fn.__name__, "section raised", False, traceback.format_exc())

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = total - passed
    print(f"\n===== SUMMARY: {passed}/{total} PASS, {failed} FAIL =====")
    out = _HERE.parent / "qa_advfix_probe_results.json"
    out.write_text(json.dumps({"passed": passed, "total": total, "results": RESULTS}, indent=2), encoding="utf-8")
    print(f"results: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
