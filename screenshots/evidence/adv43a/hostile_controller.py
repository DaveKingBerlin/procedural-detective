from __future__ import annotations
import json, logging, sys
from pathlib import Path
BACKEND = Path(r"C:\Users\Fujitsu\coding\procedural-detective\backend")
sys.path.insert(0, str(BACKEND)); sys.path.insert(0, str(BACKEND / "tests"))
import test_phase43a_evidence_algebra as T
from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS as G
from app.generation.admission import AdmissionController
from app.generation.clock import ManualClock
from app.generation.controller import GenerationController
from app.generation.fake_provider import FakeProvider
from app.generation.ids import IdSource
from app.generation.provider import GenerationStage
from app.generation.state_machine import GenerationState, ValidationOutcome
from app.core import observability

PROMPT = ("Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
          "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n")

def make():
    clock = ManualClock(); ids = IdSource()
    adm = AdmissionController(clock=clock, ids=ids, max_concurrent_generations=1,
        max_concurrent_generations_global=3, max_generations_per_session_per_window=3,
        max_generations_global_per_window=20, anonymous_quota_session_ttl_seconds=86400)
    session = adm.create_anonymous_quota_session()
    return clock, ids, adm, session

def run(script, *, repairs=2, regens=1, seed=11):
    clock, ids, adm, session = make()
    fake = FakeProvider(script)
    ctl = GenerationController(provider=fake, admission=adm, clock=clock, ids=ids,
        deadline_seconds=60, max_llm_calls_per_generation=8, max_repair_passes=repairs,
        max_full_regenerations=regens, max_prompt_chars=4000, seed=seed)
    h = ctl.start_generation(PROMPT, anonymous_quota_session_id=session.session_id)
    return ctl, fake, ctl.attempt(h.attempt_id)

def empty_ev(): return json.dumps({"evidence": []}, indent=2, ensure_ascii=False, sort_keys=True)
def no_ev_refs():
    doc = json.loads(G[GenerationStage.WORLD_GRAPH])
    for pl in doc["worldGraph"]["placements"]: pl["evidenceId"] = None
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)
def degenerate_public():
    doc = json.loads(G[GenerationStage.PUBLIC_WORLD])
    for m in doc["motives"]:
        m["affordances"] = [a for a in m.get("affordances", []) if a != "MOTIVE_CANDIDATE"]
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True)

print("=== ATTACK 7: validator gate still authoritative ===")
script_ok = {GenerationStage.CASE_TRUTH:[G[GenerationStage.CASE_TRUTH]],
             GenerationStage.PUBLIC_WORLD:[G[GenerationStage.PUBLIC_WORLD]],
             GenerationStage.EVIDENCE:[empty_ev()],
             GenerationStage.WORLD_GRAPH:[no_ev_refs()]}
_, fake_ok, rec_ok = run(script_ok)
print("  control (valid world, empty evidence): state=%s calls=%d repairs=%d regens=%d" % (rec_ok.state.value, len(fake_ok.calls), rec_ok.budget.repair_passes, rec_ok.budget.regenerations))
print("  -> published:", rec_ok.state is GenerationState.PUBLISHED, "validation:", rec_ok.last_validation.outcome.value if rec_ok.last_validation else None)

deg = degenerate_public()
script_bad = {GenerationStage.CASE_TRUTH:[G[GenerationStage.CASE_TRUTH]]*2,
              GenerationStage.PUBLIC_WORLD:[deg]*2,
              GenerationStage.EVIDENCE:[G[GenerationStage.EVIDENCE]]*2,
              GenerationStage.WORLD_GRAPH:[G[GenerationStage.WORLD_GRAPH]]*2}
_, fake_bad, rec_bad = run(script_bad, repairs=2, regens=1)
print("  degenerate world (no MOTIVE_CANDIDATE): state=%s" % rec_bad.state.value)
print("  -> NOT published despite breadth rules:", rec_bad.state is not GenerationState.PUBLISHED)
print("  -> last validation outcome:", rec_bad.last_validation.outcome.value if rec_bad.last_validation else None)

# truth-leak world: set a person role to 'murderer' in the public document
leak = json.loads(G[GenerationStage.PUBLIC_WORLD])
for p in leak["persons"]:
    if p["personId"] == "sarah_miller": p["role"] = "murderer"
leak_doc = json.dumps(leak, indent=2, ensure_ascii=False, sort_keys=True)
script_leak = {GenerationStage.CASE_TRUTH:[G[GenerationStage.CASE_TRUTH]]*2,
               GenerationStage.PUBLIC_WORLD:[leak_doc]*2,
               GenerationStage.EVIDENCE:[empty_ev()]*2,
               GenerationStage.WORLD_GRAPH:[G[GenerationStage.WORLD_GRAPH]]*2}
_, fake_leak, rec_leak = run(script_leak, repairs=1, regens=0)
print("  public role truth leak: state=%s published=%s" % (rec_leak.state.value, rec_leak.state is GenerationState.PUBLISHED))

print()
print("=== ATTACK 8: budget / once-per-attempt guard ===")
_, fake8, rec8 = run(script_ok)
print("  provider calls:", len(fake8.calls), "(expect 4)")
print("  repair passes:", rec8.budget.repair_passes, "regenerations:", rec8.budget.regenerations)
calls_before = rec8.budget.calls
second = T.pipeline.complete_generic_evidence_if_underconstrained(rec8)
print("  second completion call returns:", second, "(expect False)")
print("  provider calls unchanged:", rec8.budget.calls == calls_before)
# loop probe: call 25 times, ensure no growth / no exception
for _ in range(25): T.pipeline.complete_generic_evidence_if_underconstrained(rec8)
print("  after 25 extra calls provider calls still:", rec8.budget.calls == calls_before)

print()
print("=== ATTACK 10: observability of evidence.completion.* ===")
captured = []
class H(logging.Handler):
    def emit(self, r):
        ev = getattr(r, "pd_event", None)
        if ev and str(ev).startswith("evidence.completion"):
            captured.append((ev, dict(getattr(r, "pd_fields", {}) or {})))
logger = logging.getLogger(observability.SERVICE_LOGGER_NAME)
logger.setLevel(logging.INFO); logger.addHandler(H())
att = T._attempt(T._gen_world_homicide(), T._gen_crime_homicide(), T._empty_spec())
T.pipeline.complete_generic_evidence_if_underconstrained(att)
for ev, fields in captured:
    print("  event:", ev, "fields:", fields)
    blob = json.dumps({k: str(v) for k, v in fields.items()})
    forbidden = [s for s in ("murderer_01","Victim One","Witness A","Motive A","kitchen_knife","motive_a") if s in blob]
    print("    forbidden truth substrings in emitted fields:", forbidden)
print("DONE")

