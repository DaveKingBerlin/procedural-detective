"""Instrument the controller for the decoy-WHY world graph to see whether the
stage-local Gate 3 rejects the WORLD_GRAPH stage, and what the controller does."""
import sys, json
from pathlib import Path

REPO = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
for p in (str(REPO / "backend"), str(REPO / "backend" / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.generation import pipeline  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.state_machine import ValidationOutcome  # noqa: E402
import test_phase44_playability_pipeline as t44p  # noqa: E402

_G = t44p._G


def decoy_why_script():
    wg = json.loads(_G[GenerationStage.WORLD_GRAPH])
    for pl in wg["worldGraph"]["placements"]:
        if pl.get("objectId") == "motive_audit_file":
            pl["evidenceId"] = "motive_no_affair_01"
    s = t44p._golden_script()
    s[GenerationStage.WORLD_GRAPH] = [json.dumps(wg)]
    return s


for label, script in (
    ("golden", t44p._golden_script()),
    ("decoy-WHY", decoy_why_script()),
):
    controller, handle, record = t44p._run(script, "medium")
    print(f"\n### {label}: state={record.state} stage_done={sorted(s.value for s in record.stage_done)}")
    rep = pipeline.validate_draft(record)
    print("    validate_draft outcome:", rep.outcome.value)
    print("    structural:", rep.structural_issues)
    print("    safety:", rep.safety_issues)
    try:
        pre = record.draft
        from app.generation import playability as _p
        from app.domain.eligibility import derive_universes as _du
        pub, ev, truth = pipeline.assemble(record)[:3]
        r = _p.validate_playability_quality(
            draft=pre, public=pub, evidence=ev, universes=_du(pub),
            truth=truth, difficulty="medium")
        print("    PRE-composition Gate 3 valid:", r.valid,
              "unreach:", r.unreachable_critical_dimensions, "issues:", r.issue_codes)
    except Exception as exc:  # noqa: BLE001
        print("    pre-gate3 raised:", type(exc).__name__, exc)
print("\nDONE")
