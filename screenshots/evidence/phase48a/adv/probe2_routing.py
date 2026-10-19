r"""ADV-48.1 probe 2 - stage-prefix owner-routing spoofing battery.

Run from backend/:
    python ..\screenshots\evidence\phase48a\adv\probe2_routing.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="backslashreplace")
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

from app.generation.repair_routing import (  # noqa: E402
    RepairOwner,
    RepairPlan,
    owner_for_report,
    owner_for_stage_prefixed_issue,
    stage_labels_for_report,
)
from app.generation.report import ValidationReport  # noqa: E402

OBSERVED = (
    "world_requirements stage parse failed: "
    "world_requirements.relations[0]: kind 'on_kitchen_counter' not in "
    "['on_desk', 'on_table', 'near_victim', 'inside_cabinet', "
    "'floor_area', 'on_wall']"
)

ISSUES = [
    OBSERVED,
    "world_requirements.relations[0]: kind 'x' not in []",
    "world_requirements stage parse failed",
    "world_requirements stage parse failedX",
    "world_requirements stage parse succeeded",
    "world_requirements: bad",
    "WORLD_REQUIREMENTS: bad",
    "  world_requirements: bad",
    "world_requirements[0]: bad",
    "world_requirements/relations[0]: bad",
    "world_requirements-relations[0]: bad",
    "world_requirements_relations[0]: bad",
    "x world_requirements: bad",
    "prefix world_requirements: bad",
    "not world_requirements: bad",
    "world_requirementsX: bad",
    "world_requirements_bogus: bad",
    "world_requirements.relations[0]",
    "world_graph: bad",
    "world_graph.relations[0]: bad",
    "evidence: bad",
    "evidence.facts[0]: bad",
    "case_truth: bad",
    "case_people: bad",
    "asset_spec: bad",
    "asset_spec_repair: bad",
    "activity_log: bad",
    "activity_log_repair: bad",
    "public_world: bad",
    "repair: bad",
    "draft assembly failed: cannot publish",
    "world composition failed for the requested world",
    "world_requirements stage: unsupported environment",
    "world composition produced no usable result",
    "world_graph produced no usable result",
    "world_graph stage parse failed: JSON error",
    "notworld_graph: bad",
    "evidenced: bad",
    "evidenceX: bad",
    "world_requirements stage parse failed and world_graph: bad",
]

print("=== single-issue routing ===")
for issue in ISSUES:
    owner = owner_for_stage_prefixed_issue(issue)
    print(f"{issue[:72]!r:76} -> {owner.value if owner else None}")

print("\n=== report-level routing / plan ===")


def plan_of(structural=(), safety=(), world=(), quality=()):
    rep = ValidationReport(
        structural_issues=tuple(structural),
        safety_issues=tuple(safety),
        world_issues=tuple(world),
        quality_issues=tuple(quality),
    )
    owners = owner_for_report(rep)
    plan = RepairPlan.from_report(rep)
    return owners, plan


CASES = {
    "observed only": dict(structural=[OBSERVED]),
    "prefixed + unprefixed": dict(structural=[OBSERVED, "draft assembly failed: x"]),
    "prefixed + safety": dict(structural=[OBSERVED], safety=["generated content rejected"]),
    "prefixed + world_issues": dict(structural=[OBSERVED], world=["world.unresolved-object: foo"]),
    "prefixed + quality": dict(structural=[OBSERVED], quality=["VICTIM_IN_SUSPECT_CANDIDATES", "VALIDATION_FAILED"]),
    "safety only": dict(safety=["generated content rejected"]),
    "world only": dict(world=["world.unresolved-object: foo"]),
    "empty": dict(),
    "unprefixed only": dict(structural=["draft assembly failed: x"]),
    "two prefixed": dict(structural=[OBSERVED, "evidence: bad"]),
}
for name, kwargs in CASES.items():
    owners, plan = plan_of(**kwargs)
    print(
        f"{name:26} owners={tuple(o.value for o in owners)!s:42} "
        f"full_rerun={plan.is_default_full_rerun} "
        f"case={plan.rerun_case} ev={plan.rerun_evidence} "
        f"log={plan.rerun_activity_log} world={plan.rerun_world} "
        f"recompose={plan.recompose}"
    )

print("\n=== non-string / malformed structural issues ===")
for bad in (None, 123, {"x": 1}, ["world_requirements: x"], ""):
    rep = ValidationReport(structural_issues=(bad,))
    print(f"{bad!r:30} -> owners={tuple(o.value for o in owner_for_report(rep))} "
          f"stages={stage_labels_for_report(rep)}")

print("\n=== stage_labels_for_report ===")
for issue in ISSUES[:12]:
    print(f"{issue[:60]!r:64} -> {stage_labels_for_report(ValidationReport(structural_issues=(issue,)))}")
