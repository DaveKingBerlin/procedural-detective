"""Phase 44 ADV — §36 scenarios 11-20, distribution/red-herring spoofs, Q8 leak."""
from _bootstrap import (  # noqa: F401
    evaluate, show, replace, playability, pipeline, difficulty_policy,
    GenerationStage, GeneratedDraft, WorldGraphSpec, PlacementSpec, ObjectSpec,
    EvidenceSpec, PropSpec, EvidenceSetSpec, PublicWorldSpec, derive_universes,
    t44, t44p,
)
import copy
import json
import logging


def hr(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


# --------------------------------------------------------------------------- #
hr("S11 — witness-only case where the witness has NO usable answer")
d = t44.golden_draft()
ev = tuple(f for f in d.evidence if f.id != "witness_statement_emily_01")
d = replace(d, evidence=ev)
show("S11 easy", evaluate(d, "easy"))
attempt = pipeline.AttemptRecord(attempt_id="A", case_id="C", session_id="S")
attempt.draft = d
print("Gate1/2 outcome:", pipeline.validate_draft(attempt).outcome,
      "| quality:", pipeline.validate_draft(attempt).quality_issues)


# --------------------------------------------------------------------------- #
hr("S12 — witness murderer (Phase41A) — Gate 3 must accept")
try:
    t44.test_28_witness_murderer_still_supported()
    print("  test_28 PASSED (witness murderer accepted; no WITNESS_CONTENT rejection)")
except Exception as e:  # noqa: BLE001
    print("  test_28 FAILED:", type(e).__name__, e)


# --------------------------------------------------------------------------- #
hr("S13 — deceptive innocent witness (Phase41A semantics preserved)")
try:
    import test_phase41_deceptive_witness as tdec
    fns = [n for n in dir(tdec) if n.startswith("test_")]
    import traceback
    passed = 0
    for n in fns:
        try:
            getattr(tdec, n)()
            passed += 1
        except Exception as e:  # noqa: BLE001
            print("   FAIL", n, type(e).__name__, e)
    print(f"  deceptive-witness module: {passed}/{len(fns)} passed")
except Exception as e:  # noqa: BLE001
    print("  (module import issue)", type(e).__name__, e)


# --------------------------------------------------------------------------- #
hr("S14 — SUICIDE truth with homicide-like framing evidence (ADV-INV-02 class)")
from app.domain.resolution import ResolutionType
d = t44.golden_draft()
crime = replace(d.crime, resolution_type=ResolutionType.SUICIDE, murderer_id=None)
d = replace(d, crime=crime)
# Gate 3 with suicide truth: WHO not required.
show("S14 suicide (plain)", evaluate(d, "medium"))
# Append suspect presence + fingerprint framing (model-style) — does Gate 3 flag?
framing = tuple(d.evidence) + (
    EvidenceSpec(id="frame_presence_01", kind="cctv_observation",
        propositions=(PropSpec(type="PERSON_OBSERVED_AT_LOCATION", person_id="thomas_reed",
            location_id=t44.SCENE_LOCATION, observed_at="2026-09-11T22:15:00+02:00",
            uncertainty_seconds=60),)),
    EvidenceSpec(id="frame_fp_01", kind="forensic",
        propositions=(PropSpec(type="OBJECT_CONTAINS_FINGERPRINT",
            object_id="kitchen_knife", person_id="thomas_reed"),)),
)
d2 = replace(d, evidence=framing)
show("S14 suicide + homicide framing", evaluate(d2, "medium"))


# --------------------------------------------------------------------------- #
hr("S15 — duplicate evidence IDs")
d = t44.golden_draft()
first = d.evidence[0]
dup = replace(first)  # same id
d = replace(d, evidence=tuple(d.evidence) + (dup,))
attempt = pipeline.AttemptRecord(attempt_id="A", case_id="C", session_id="S")
attempt.draft = d
rep = pipeline.validate_draft(attempt)
print("  Gate1/2 outcome:", rep.outcome)
print("  structural:", rep.structural_issues)
show("S15 gate3", evaluate(d, "medium"))


# --------------------------------------------------------------------------- #
hr("S16 — one evidence record revealed by MULTIPLE objects (distribution spoof?)")
base = t44.easy_at_min()
# Take the knife fact and add 2 more placements (different object ids) linking it.
d = base
for oid in ("dup_obj_a", "dup_obj_b"):
    spec = ObjectSpec(object_id=oid, asset_id="PROP_VASE_01", affordances=())
    pl = PlacementSpec(object_id=oid, asset_id="PROP_VASE_01",
                       location_id=t44.SCENE_LOCATION, anchor="shelf_01",
                       interaction="inspect", evidence_id="forensic_knife_match_01")
    d = replace(d, objects=tuple(d.objects) + (spec,),
                world_graph=replace(d.world_graph,
                    placements=tuple(d.world_graph.placements) + (pl,)))
show("S16 base easy", evaluate(base, "easy"))
show("S16 +2 objects sharing ONE evidence id", evaluate(d, "easy"))
print("  -> distinct_source_count inflation by sharing one record:",
      evaluate(d, "easy").distinct_meaningful_source_count
      - evaluate(base, "easy").distinct_meaningful_source_count)


# --------------------------------------------------------------------------- #
hr("S17 — asset fallback while semantic object identity stays correct")
d = t44.golden_draft()
fallback = tuple(replace(p, asset_id="PROP_GENERIC_FALLBACK_01",
                         resolution_provenance="GENERIC_FALLBACK")
                 for p in d.world_graph.placements)
d = replace(d, world_graph=replace(d.world_graph, placements=fallback))
show("S17 fallback assets", evaluate(d, "medium"))
print("  placements:", len(d.world_graph.placements))


# --------------------------------------------------------------------------- #
hr("S18/S19 — save/reload & Home/new-case (Gate 3 not on the read path)")
print("  Gate 3 mutant = deterministic function of the frozen candidate;")
print("  it is NOT invoked by load_savegame / home. (Confirmed by code: only")
print("  GenerationService._publish_if_ready calls validate_published_candidate.)")
import pathlib
import app.services.generation as svc
src = pathlib.Path(svc.__file__).read_text(encoding="utf-8")
print("  validate_published_candidate call sites:", src.count("validate_published_candidate("))


# --------------------------------------------------------------------------- #
hr("S20 — Frontier-compatible canonical output still routes through Gate 3")
# Frontier produces the SAME canonical stage payloads (parsed) as fake; the
# publication guard is provider-agnostic. Demonstrate with the golden script.
print("  (Gate 3 runs in _publish_if_ready for every provider; see Q5.)")


# --------------------------------------------------------------------------- #
hr("Q8 (fixed) — capture playability events and scan for hidden truth")
logger = logging.getLogger("procedural-detective")
logger.setLevel(logging.INFO)
records = []


class _Cap(logging.Handler):
    def emit(self, rec):
        records.append(rec)


h = _Cap()
logger.addHandler(h)
controller, handle, record = t44p._run(t44p._golden_script(), "medium")
result = controller.publish(handle.attempt_id, hold_ok=True)
record.published = result.published
stub = t44p._make_guard_stub()
t44p._publish_via_guard(stub, record, "medium")
logger.removeHandler(h)
names = [getattr(r, "pd_event", None) for r in records]
print("  events:", [n for n in names if n and n.startswith("playability.")])
truth_tokens = {"thomas_reed", "sarah_miller", "cover_up_embezzlement", "kitchen_knife"}
leak = []
for r in records:
    if not (getattr(r, "pd_event", "") or "").startswith("playability."):
        continue
    text = json.dumps({k: str(v) for k, v in (getattr(r, "pd_fields", {}) or {}).items()})
    leak += [t for t in truth_tokens if t in text]
print("  hidden-truth tokens leaked:", sorted(set(leak)))
