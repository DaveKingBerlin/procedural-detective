"""Phase 44 ADV — §38 Q4 (difficulty label), Q5 (provider), Q6 (desync), Q8 (leak)."""
from _bootstrap import (  # noqa: F401
    evaluate, show, replace, playability, pipeline, difficulty_policy,
    GenerationStage, GeneratedDraft, WorldGraphSpec, PlacementSpec, ObjectSpec,
    EvidenceSpec, PropSpec, EvidenceSetSpec, PublicWorldSpec, derive_universes,
    t44, t44p,
)
import json
import logging


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


# --------------------------------------------------------------------------- #
hr("Q4 — difficulty label bypass attempts (normalize_difficulty)")
hostile = [
    None, "", "  ", "\t", "\n", "EASY", " Easy ", "hard\n", "eAsY", "HARD",
    "easy\x00", "easy\x1b", "easy\u200b", "\u0065asy", "ｅａｓｙ",  # fullwidth
    "easy ", " easy", "medium ", "bogus", "extreme", "easyhard", "e" * 100000,
    123, 1.5, True, False, b"easy", ["easy"], {"d": "easy"}, object(),
]
for raw in hostile:
    try:
        norm = difficulty_policy.normalize_difficulty(raw)
    except Exception as e:  # noqa: BLE001
        norm = f"<RAISED {type(e).__name__}: {e}>"
    label = repr(raw)[:30]
    flag = "  <-- DOWNGRADE!" if norm == "easy" and not (
        isinstance(raw, str) and raw.strip().lower() == "easy"
    ) else ""
    print(f"  {label:32} -> {norm}{flag}")


# --------------------------------------------------------------------------- #
hr("Q4b — sparse case + hostile difficulty through the REAL service guard")
# A medium-sparse draft (only 3 inspectables) must NOT publish when difficulty is
# omitted/malformed (must default to medium, not silently easy).
def sparse_record():
    # golden minus 2 objects => 3 inspectables (Easy-rich, Medium-poor)
    return t44.easy_at_min()


import test_phase44_playability_pipeline as p
for diff in [None, "", "bogus", 123, True, "ｅａｓｙ", "easy", "medium"]:
    report = evaluate(sparse_record(), diff)
    print(f"  difficulty={repr(diff):16} -> normalized={report.difficulty:7} valid={report.valid}")


# --------------------------------------------------------------------------- #
hr("Q5 — provider independence (structural): every publication funnels through the guard")
import ast, pathlib
src = pathlib.Path(t44p.__file__).read_text(encoding="utf-8")  # not used
import app.services.generation as svc
gsrc = pathlib.Path(svc.__file__).read_text(encoding="utf-8")
print("  _publish_if_ready call sites:",
      gsrc.count("self._publish_if_ready("))
print("  publish_transactionally call sites in services/generation.py:",
      gsrc.count("self._publication.publish_transactionally("))
# Both start_case_generation and start_case_version call _publish_if_ready
print("  start_case_generation path: 'or record.state.value' present:",
      "or record.state.value" in gsrc)


# --------------------------------------------------------------------------- #
hr("Q6 — stage gate vs publication gate desync (composition changes the world)")
# Feed a provider WORLD_GRAPH that PASSES the stage gate, but a non-apartment
# environment whose composition reverts to the sparse provider world.
# Simpler observable: confirm the two gates evaluate DIFFERENT candidates by
# comparing stage-gate issue codes vs publish-gate issue codes.
import app.generation.playability as pl
attempt = pipeline.AttemptRecord(attempt_id="A", case_id="C", session_id="S",
                                 difficulty="medium")
# Build a partially sparse world: 2 placements only (below Medium min).
wg = json.loads(t44p._G[GenerationStage.WORLD_GRAPH])
wg["worldGraph"]["placements"] = wg["worldGraph"]["placements"][:2]
for st, payload in t44p._G.items():
    if st in (GenerationStage.CASE_TRUTH, GenerationStage.PUBLIC_WORLD, GenerationStage.EVIDENCE):
        attempt.stage_outputs[st] = pipeline.parser.parse_stage(st, payload)
# install world graph spec directly (as apply_stage_output would)
attempt.stage_outputs[GenerationStage.WORLD_GRAPH] = pipeline.parser.parse_stage(
    GenerationStage.WORLD_GRAPH, json.dumps(wg))
attempt.draft = None
stage_issues = pl.world_graph_playability_issues(attempt)
print("  stage-gate issues:", stage_issues)
rep = pipeline.validate_draft(attempt)
print("  Gate1/2 outcome (sparse world):", rep.outcome)


# --------------------------------------------------------------------------- #
hr("Q8 — Gate 3 leak / CaseTruth mutation")
import copy
d = t44.golden_draft()
pub, ev, truth = t44.phase3(d)
before = copy.deepcopy(truth)
records = []


class _Cap(logging.Handler):
    def emit(self, rec):
        records.append(rec)


logger = logging.getLogger("procedural-detective")
h = _Cap()
logger.addHandler(h)
# Run the guard over a real frozen candidate to capture its events.
controller, handle, record = t44p._run(t44p._golden_script(), "medium")
result = controller.publish(handle.attempt_id, hold_ok=True)
record.published = result.published
stub = t44p._make_guard_stub()
t44p._publish_via_guard(stub, record, "medium")
logger.removeHandler(h)
emitted = []
for rec in records:
    name = getattr(rec, "pd_event", None)
    if name and name.startswith("playability."):
        emitted.append((name, getattr(rec, "pd_fields", {})))
for name, fields in emitted:
    print(" ", name, sorted(fields.keys()))
# Scan for hidden truth tokens in the emitted fields
truth_tokens = {"thomas_reed", "sarah_miller", "cover_up_embezzlement", "kitchen_knife"}
leak = False
for name, fields in emitted:
    text = json.dumps({k: str(v) for k, v in fields.items()})
    for tok in truth_tokens:
        if tok in text:
            print("  LEAK:", name, tok)
            leak = True
print("  hidden-truth leak in playability events:", leak)
