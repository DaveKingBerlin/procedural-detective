import logging, json, sys
from pathlib import Path
BACKEND = Path(r"C:\Users\Fujitsu\coding\procedural-detective\backend")
sys.path.insert(0, str(BACKEND)); sys.path.insert(0, str(BACKEND / "tests"))
import test_phase43a_evidence_algebra as T
from app.generation import pipeline
from app.generation.provider import GenerationStage
from app.core import observability
captured=[]
class H(logging.Handler):
    def emit(self,r):
        ev=getattr(r,"pd_event",None)
        if ev and str(ev).startswith("evidence.completion"): captured.append((ev, dict(getattr(r,"pd_fields",{}) or {})))
lg=logging.getLogger(observability.SERVICE_LOGGER_NAME); lg.setLevel(logging.INFO); lg.addHandler(H())
# precondition-not-met skip
att=T._attempt(T._gen_world_homicide(), T._gen_crime_homicide(), T._empty_spec())
att.deferred_structural=("public_world: bad","public_world: bad")
att.deferred_by_stage={GenerationStage.PUBLIC_WORLD:("public_world: bad",)}
pipeline.complete_generic_evidence_if_underconstrained(att)
# analyzer-classification skip (not closable): overconstrained time
for ev,f in captured: print(ev, json.dumps(f, sort_keys=True))
