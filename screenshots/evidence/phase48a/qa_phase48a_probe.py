"""Phase 48.1 — INDEPENDENT QA probe (QA-owned; never a product-source edit).

This probe re-derives the Phase 48.1 behavior from the PRODUCT code and the
shared hermetic fixtures. It is NOT the developer's test file; it asserts its
own scenarios and prints machine-readable PASS/FAIL lines.

Run from the repository root (or anywhere):

    python screenshots/evidence/phase48a/qa_phase48a_probe.py

Scope: Dev-Box hermetic only. No Ollama, no network, no Gaming-PC test.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

from app.generation import prompts  # noqa: E402
from app.generation.repair_routing import (  # noqa: E402
    RepairOwner,
    RepairPlan,
    owner_for_report,
    owner_for_stage_prefixed_issue,
    stage_labels_for_report,
)
from app.generation.report import ValidationReport  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services import ollama_driver as od  # noqa: E402
from app.services.ollama_driver import parse_world_requirements  # noqa: E402
from app.world.requirements import (  # noqa: E402
    CRITICALITY_REQUIRED,
    ObjectRequest,
    PlacementRelation,
    RELATION_KINDS,
    RELATION_TO_ANCHOR_TYPES,
    WorldRequirements,
    normalize_relation_kind,
)

from test_ollama_driver import (  # noqa: E402
    ICEPICK_SPEC,
    PROMPT,
    _alog_posts,
    _case_people,
    _evidence,
    _j,
    _run,
    _world,
)

RESULTS: list[tuple[str, bool, str]] = []
_CRIME = "2026-09-11T23:42:00+02:00"


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.events: list[tuple[str, dict]] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        ev = getattr(record, "pd_event", None)
        if ev is not None:
            self.events.append((ev, getattr(record, "pd_fields", {}) or {}))

    def named(self, event: str) -> list[dict]:
        return [f for e, f in self.events if e == event]


def _world_doc(kind, *, name="bronze ceremonial ice pick", env="office"):
    return json.dumps(
        {
            "environmentHint": env,
            "locationTokens": [env],
            "objects": [{"name": name, "criticality": "required"}],
            "relations": [{"kind": kind, "target": name}],
            "unsafeUnsupported": [],
        }
    )


# --------------------------------------------------------------------------- #
# §1 BEFORE-equivalent: reproduce the PRE-FIX strict rejection without touching
# source by swapping the module-global normalizer back to the original check.
# --------------------------------------------------------------------------- #
def before_equivalent() -> None:
    real = od.normalize_relation_kind
    try:
        # Pre-fix code was exactly: `if kind not in RELATION_KINDS: raise`.
        od.normalize_relation_kind = (
            lambda raw: raw if raw in RELATION_KINDS else None
        )
        raised = None
        try:
            parse_world_requirements(_world_doc("on_kitchen_counter"))
        except ValueError as exc:
            raised = str(exc)
        check(
            "BEFORE-equivalent: strict parser rejects on_kitchen_counter",
            raised is not None and "on_kitchen_counter" in raised and "not in" in raised,
            raised or "NO RAISE",
        )
    finally:
        od.normalize_relation_kind = real

    # AFTER: the real boundary normalizes the same payload to a canonical kind.
    req = parse_world_requirements(_world_doc("on_kitchen_counter"))
    kinds = tuple(r.kind for r in req.relations)
    check(
        "AFTER: parse boundary canonicalizes on_kitchen_counter -> on_table",
        kinds == ("on_table",),
        f"kinds={kinds}",
    )


# --------------------------------------------------------------------------- #
# §2 Normalization contract
# --------------------------------------------------------------------------- #
ALIASES = {
    "on_kitchen_counter": "on_table",
    "on_counter": "on_table",
    "on_worktop": "on_table",
    "On Kitchen Counter": "on_table",
    "on-kitchen-counter": "on_table",
    "desk": "on_desk",
    "on_desk": "on_desk",
    "shelf": "inside_cabinet",
    "on_shelf": "inside_cabinet",
    "cabinet": "inside_cabinet",
    "floor": "floor_area",
    "on_floor": "floor_area",
    "wall": "on_wall",
    "on_wall": "on_wall",
    "beside": "near_victim",
    "beside_the_victim": "near_victim",
    "near_victim": "near_victim",
    "on_table": "on_table",
    "inside_cabinet": "inside_cabinet",
    "floor_area": "floor_area",
}

UNSUPPORTED = {
    "teleported_inside": "observed-failure-family arbitrary value",
    "behind_the_sofa": "arbitrary location phrase",
    "on_bed": "invented enum not in the table",
    "on_chair": "invented enum not in the table",
    "on_floor_near_window": "invented enum not in the table",
    "inside_black_hole": "hostile value",
    "": "empty",
    "on_": "prefix only",
    None: "none",
    123: "non-string int",
    "on_table" * 40: "oversized (>120)",
    "on_table_extra_words": "arbitrary suffix",
    "x" * 200: "oversized garbage",
}


def normalization_contract() -> None:
    bad = []
    for raw, expected in ALIASES.items():
        got = normalize_relation_kind(raw)
        if got != expected:
            bad.append(f"{raw!r}->{got!r} (want {expected!r})")
    check("contract: every mapped alias -> correct canonical", not bad, "; ".join(bad))

    check(
        "contract: on_desk vs on_table distinction preserved",
        normalize_relation_kind("on_desk") == "on_desk"
        and normalize_relation_kind("on_table") == "on_table"
        and normalize_relation_kind("on_desk") != normalize_relation_kind("on_table"),
    )

    coerced = [
        f"{raw!r} ({why}) -> {normalize_relation_kind(raw)!r}"
        for raw, why in UNSUPPORTED.items()
        if normalize_relation_kind(raw) is not None
    ]
    check(
        "contract: UNSUPPORTED/arbitrary/oversized/non-string NOT normalized",
        not coerced,
        "; ".join(coerced) if coerced else f"{len(UNSUPPORTED)} rejected",
    )

    strict_ok = False
    try:
        PlacementRelation(kind="on_kitchen_counter", target="x")
    except ValueError:
        strict_ok = True
    check(
        "contract: PlacementRelation stays strict (raw alias raises)",
        strict_ok
        and PlacementRelation(kind="on_table").kind == "on_table"
        and PlacementRelation(kind="on_desk").kind == "on_desk",
    )

    check(
        "contract: RELATION_KINDS is EXACTLY the canonical six",
        tuple(RELATION_KINDS)
        == (
            "on_desk",
            "on_table",
            "near_victim",
            "inside_cabinet",
            "floor_area",
            "on_wall",
        ),
        repr(RELATION_KINDS),
    )
    check(
        "contract: RELATION_TO_ANCHOR_TYPES keys == RELATION_KINDS (6, not expanded)",
        set(RELATION_TO_ANCHOR_TYPES) == set(RELATION_KINDS)
        and len(RELATION_TO_ANCHOR_TYPES) == 6,
        repr(tuple(RELATION_TO_ANCHOR_TYPES)),
    )
    check(
        "contract: no alias token leaked into the canonical vocabulary",
        not any(
            tok in ("on_kitchen_counter", "desk", "shelf", "cabinet", "floor", "wall")
            for tok in RELATION_KINDS
        ),
    )


# --------------------------------------------------------------------------- #
# §3 Composer: the normalized target actually places the weapon in a kit
# --------------------------------------------------------------------------- #
def composer_places() -> None:
    from app.environments.resolver import resolve_environment
    from app.world.composer import KnownObjectSpecProvider, compose_world
    from test_world_composer import CATALOG, GOLDEN_PLACEMENTS, KITS

    for kit_id in ("apartment", "office"):
        requirements = WorldRequirements(
            environment_hint=kit_id,
            objects=(
                ObjectRequest(
                    requested_name="kitchen knife",
                    evidence_id="forensic_knife_match_01",
                    required_interaction="inspect",
                    criticality=CRITICALITY_REQUIRED,
                ),
            ),
            relations=(
                PlacementRelation(
                    kind=normalize_relation_kind("on_kitchen_counter"),
                    target="kitchen knife",
                ),
            ),
        )
        comp = compose_world(
            requirements,
            env_resolver=resolve_environment,
            spec_provider=KnownObjectSpecProvider(),
            evidence_placements=GOLDEN_PLACEMENTS,
            catalog=CATALOG,
            kit=KITS[kit_id],
        )
        placed = any(p.object_id == "kitchen_knife" for p in comp.placements)
        check(
            f"composer: normalized on_kitchen_counter places weapon in '{kit_id}'",
            comp.issues == () and placed,
            f"issues={comp.issues} placed={placed}",
        )


# --------------------------------------------------------------------------- #
# §4 End-to-end: alias on FIRST attempt publishes with no unrelated replay
# --------------------------------------------------------------------------- #
def e2e_alias_publishes() -> None:
    cap = _Capture()
    logging.getLogger("procedural-detective").addHandler(cap)
    logging.getLogger("procedural-detective").setLevel(logging.INFO)
    try:
        alias_world = _world()
        alias_world["environmentHint"] = "office"
        alias_world["locationTokens"] = ["office"]
        alias_world["relations"] = [
            {"kind": "on_kitchen_counter", "target": "bronze ceremonial ice pick"}
        ]
        posts = [
            _j(_case_people()),
            _j(_evidence()),
            *_alog_posts(_CRIME),
            _j(alias_world),
            ICEPICK_SPEC,
        ]
        record, transport = _run(posts)
        seen = transport.post_calls
        n_case = sum("case_people_v1" in p for p in seen)
        n_ev = sum("evidence_v1" in p for p in seen)
        n_log = sum("Canonical evidence time:" in p for p in seen)
        n_world = sum("world_requirements_v1" in p for p in seen)
        n_asset = sum("asset_spec_v1" in p for p in seen)
        check(
            "e2e: alias on first attempt PUBLISHES",
            record.state is GenerationState.PUBLISHED
            and record.published is not None
            and record.last_validation is not None
            and record.last_validation.valid is True
            and record.deferred_structural == (),
            f"state={record.state} deferred={record.deferred_structural}",
        )
        check(
            "e2e: solver uniqueness intact (who/why/weapon)",
            record.solver_proof is not None
            and record.solver_proof.who.unique
            and record.solver_proof.why.unique
            and record.solver_proof.weapon.unique,
        )
        check(
            "e2e: unrelated stage output stable (case=1 ev=1 logs=5 world=1 asset=1)",
            (n_case, n_ev, n_log, n_world, n_asset) == (1, 1, 5, 1, 1),
            f"case={n_case} ev={n_ev} logs={n_log} world={n_world} asset={n_asset} "
            f"calls={transport.call_count}",
        )
        check(
            "e2e: canonicalization event emitted, no repair started",
            len(cap.named("world.relation_kind.canonicalized")) == 1
            and not cap.named("generation.repair.started"),
            f"events={[e for e, _ in cap.events if e in ('world.relation_kind.canonicalized','generation.repair.started')]}",
        )
    finally:
        logging.getLogger("procedural-detective").removeHandler(cap)


# --------------------------------------------------------------------------- #
# §5 End-to-end: repeated UNSUPPORTED alias fails closed early (no deadline burn)
# --------------------------------------------------------------------------- #
def e2e_repeated_unsupported_fails_closed() -> None:
    prompt = (
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
        "Motive: stolen research data\nWeapon: kitchen knife\nTime: 23:42\n"
        "Witness: Lisa König\nLocation: office\n"
    )
    invalid = _world(unknown_name="kitchen knife")
    invalid["relations"] = [{"kind": "teleported_inside", "target": "kitchen knife"}]
    invalid_json = _j(invalid)
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife")),
        *_alog_posts(_CRIME),
        invalid_json,
        invalid_json,
        invalid_json,
        invalid_json,
    ]
    record, transport = _run(posts, prompt=prompt, max_repair_passes=2)
    reason = record.reason or ""
    check(
        "e2e: repeated unsupported alias FAILS CLOSED with STRUCTURED_OUTPUT_INVALID",
        record.state is GenerationState.FAILED
        and record.published is None
        and record.failure_code == "STRUCTURED_OUTPUT_INVALID",
        f"state={record.state} code={record.failure_code}",
    )
    check(
        "e2e: precise reason names WORLD_GRAPH + failingStage, no deadline mention",
        "repair made no progress" in reason
        and "WORLD_GRAPH" in reason
        and "failingStage=world_requirements" in reason
        and "deadline" not in reason.casefold(),
        reason,
    )
    check(
        "e2e: deadline NOT passed and repair_passes bounded (==1)",
        record.budget is not None
        and record.budget.deadline_passed() is False
        and record.budget.repair_passes == 1,
        f"deadline_passed={record.budget.deadline_passed() if record.budget else None} "
        f"repair_passes={record.budget.repair_passes if record.budget else None}",
    )
    n_ev = sum("evidence_v1" in p for p in transport.post_calls)
    n_log = sum("Canonical evidence time:" in p for p in transport.post_calls)
    n_world = sum("world_requirements_v1" in p for p in transport.post_calls)
    n_asset = sum("asset_spec_v1" in p for p in transport.post_calls)
    check(
        "e2e: evidence/logs NOT replayed; world only (ev=1 logs=5 world=4 asset=0)",
        (n_ev, n_log, n_world, n_asset) == (1, 5, 4, 0),
        f"ev={n_ev} logs={n_log} world={n_world} asset={n_asset} calls={transport.call_count}",
    )


# --------------------------------------------------------------------------- #
# §6 Repair scope: world-only defect does NOT re-call evidence / activity log
# --------------------------------------------------------------------------- #
def repair_scope_world_only() -> None:
    valid_world = _world()  # canonical on_desk, office, unknown ice pick
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        *_alog_posts(_CRIME),
        "<not-json>",       # pass-1 world: strict parse failure -> repair
        "<not-json>",       # pass-1 world bounded retry (still fails)
        ICEPICK_SPEC,       # pass-1 composition still reaches ASSET_SPEC
        _j(valid_world),    # repair pass: WORLD only (ev/logs/case reused)
        ICEPICK_SPEC,       # repair-pass recompose (asset), not a stage replay
    ]
    record, transport = _run(posts, max_repair_passes=2)
    n_case = sum("case_people_v1" in p for p in transport.post_calls)
    n_ev = sum("evidence_v1" in p for p in transport.post_calls)
    n_log = sum("Canonical evidence time:" in p for p in transport.post_calls)
    n_world = sum("world_requirements_v1" in p for p in transport.post_calls)
    check(
        "scope: world-only repair PUBLISHES",
        record.state is GenerationState.PUBLISHED,
        f"state={record.state} reason={record.reason}",
    )
    check(
        "scope: evidence + activity_log NOT re-called (stage-level)",
        n_ev == 1 and n_log == 5,
        f"ev={n_ev} logs={n_log}",
    )
    check(
        "scope: case cached; world re-run only (case=1 world=3)",
        n_case == 1 and n_world == 3,
        f"case={n_case} world={n_world} calls={transport.call_count}",
    )


# --------------------------------------------------------------------------- #
# §7 Owner routing (closed, bounded, no free-text parsing)
# --------------------------------------------------------------------------- #
OBSERVED = (
    "world_requirements stage parse failed: "
    "world_requirements.relations[0]: kind 'on_kitchen_counter' not in "
    "['on_desk', 'on_table', 'near_victim', 'inside_cabinet', "
    "'floor_area', 'on_wall']"
)


def owner_routing() -> None:
    rep = ValidationReport(structural_issues=(OBSERVED,))
    check(
        "routing: exact observed message -> WORLD_GRAPH (not UNKNOWN)",
        owner_for_report(rep) == (RepairOwner.WORLD_GRAPH,),
        f"owners={owner_for_report(rep)}",
    )
    check(
        "routing: stage label reported as world_requirements",
        stage_labels_for_report(rep) == ("world_requirements",),
    )
    plan = RepairPlan.from_report(rep)
    check(
        "routing: plan reruns world+recompose only",
        plan.rerun_world is True
        and plan.recompose is True
        and plan.rerun_case is False
        and plan.rerun_evidence is False
        and plan.rerun_activity_log is False,
        f"case={plan.rerun_case} ev={plan.rerun_evidence} log={plan.rerun_activity_log} "
        f"world={plan.rerun_world} recompose={plan.recompose}",
    )

    unprefixed = ValidationReport(
        structural_issues=("draft assembly failed: generated draft cannot become a public case",)
    )
    check(
        "routing: unprefixed structural -> UNKNOWN (legacy fallback)",
        owner_for_report(unprefixed) == (RepairOwner.UNKNOWN,)
        and RepairPlan.from_report(unprefixed).is_default_full_rerun,
        f"owners={owner_for_report(unprefixed)}",
    )

    hostile = [
        "world_requirements_bogus: kind 'x' not in []",
        "prefix world_requirements: bogus",
        "notworld_graph: junk",
        "world requirements stage parse failed: junk",
        "world_requirementsX",
    ]
    bad = [
        f"{text!r} -> {owner_for_stage_prefixed_issue(text)}"
        for text in hostile
        if owner_for_stage_prefixed_issue(text) is not None
    ]
    check(
        "routing: bounded/closed — hostile free text never attributes an owner",
        not bad,
        "; ".join(bad) if bad else "all None",
    )
    for label, owner in (
        ("world_graph: unknown key 'bad'", RepairOwner.WORLD_GRAPH),
        ("evidence: malformed evidence", RepairOwner.EVIDENCE),
        ("case_truth: bad crime", RepairOwner.CASE_TRUTH),
        ("asset_spec: bad spec", RepairOwner.ASSET_SPEC),
        ("activity_log: bad row", RepairOwner.ACTIVITY_LOG),
    ):
        got = owner_for_stage_prefixed_issue(label)
        check(f"routing: closed label {label.split(':')[0]!r}", got is owner, f"got={got}")
    check(
        "routing: public_world deliberately UNKNOWN",
        owner_for_report(
            ValidationReport(structural_issues=("public_world: unknown key 'bad'",))
        )
        == (RepairOwner.UNKNOWN,),
    )


# --------------------------------------------------------------------------- #
# §8 Prompt/schema exposes the canonical six kinds (single source)
# --------------------------------------------------------------------------- #
def prompt_schema() -> None:
    blob = prompts.build_world_requirements_prompt(PROMPT, None)
    check(
        "prompt: world prompt exposes all six canonical kinds",
        all(k in blob for k in RELATION_KINDS),
    )
    schema = prompts.schema_contract_as_json_schema("world_requirements")
    kind_schema = schema["properties"]["relations"]["items"]["properties"]["kind"]
    check(
        "schema: transport enum == sorted(RELATION_KINDS)",
        kind_schema == {"type": "string", "enum": sorted(RELATION_KINDS)},
        repr(kind_schema),
    )
    check(
        "schema: world_graph mapping derives the same canonical schema",
        prompts.json_schema_for_generation_stage("world_graph")
        == prompts.schema_contract_as_json_schema("world_requirements"),
    )


# --------------------------------------------------------------------------- #
# §9 Control-char raw probe (raw, explicit reporting — see QA report)
# --------------------------------------------------------------------------- #
def control_char_probe() -> None:
    print("\n--- control-char raw probe (informational) ---")
    for raw in ("\x00", "\x01on_table", "on_table\x01", "on\x00table", "\ton_table"):
        print(f"  normalize_relation_kind({raw!r}) = {normalize_relation_kind(raw)!r}")


def main() -> int:
    before_equivalent()
    normalization_contract()
    composer_places()
    e2e_alias_publishes()
    e2e_repeated_unsupported_fails_closed()
    repair_scope_world_only()
    owner_routing()
    prompt_schema()
    control_char_probe()

    failed = [r for r in RESULTS if not r[1]]
    print("\n================ QA PROBE SUMMARY ================")
    print(f"total={len(RESULTS)} pass={len(RESULTS) - len(failed)} fail={len(failed)}")
    for name, _ok, detail in failed:
        print(f"  FAILED: {name} :: {detail}")
    out = {
        "total": len(RESULTS),
        "pass": len(RESULTS) - len(failed),
        "fail": len(failed),
        "results": [{"name": n, "passed": ok, "detail": d} for n, ok, d in RESULTS],
    }
    (Path(__file__).resolve().parent / "qa_phase48a_probe_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
