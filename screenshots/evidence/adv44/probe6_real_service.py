"""Phase 44 ADV — REAL GenerationService + REAL SQLite store end-to-end.

Proves ADV-INV-01 closure (empty world cannot publish) and the entity-surface
bypass (zero evidence-linked placements CAN publish) through the actual durable
publication path (no stubbed store/publication).
"""
import sys, tempfile, os, json, uuid
from pathlib import Path

REPO = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
BACKEND = REPO / "backend"
TESTS = BACKEND / "tests"
for p in (str(BACKEND), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import conftest  # noqa: E402
from app.persistence.store import Store  # noqa: E402
from app.services.generation import GenerationService  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation import pipeline  # noqa: E402
import test_phase44_playability_pipeline as t44p  # noqa: E402

PROMPT = t44p.GOLDEN_PROMPT


def run_case(script, difficulty, label):
    print("\n" + "=" * 78 + f"\n{label}\n" + "=" * 78)
    db = Path(tempfile.gettempdir()) / f"pd44adv_{uuid.uuid4().hex}.sqlite"
    url = f"sqlite:///{db.as_posix()}"
    conftest.upgrade_db(url)
    store = Store(url)
    try:
        svc = GenerationService(
            settings=conftest.make_phase5_settings(url),
            store=store,
            provider_factory=lambda: FakeProvider(script=script),
        )
        sess = svc.create_anonymous_quota_session()
        res = svc.start_case_generation(
            PROMPT,
            anonymous_quota_session_id=sess.anonymous_quota_session_id,
            difficulty=difficulty,
        )
        print("  status:", res.status, "| failure_code:", res.failure_code)
        cv = store.get_case_version(res.case_id, 1)
        print("  durable case_version state:", getattr(cv, "state", None),
              "| reason:", getattr(cv, "state_reason", None))
        pub = store.get_published(res.case_id, 1)
        print("  durable published row present:", pub is not None)
        return res, pub, store
    finally:
        store.dispose()
        try:
            os.remove(db)
        except OSError:
            pass


# 1. EMPTY world graph -> must NOT publish.
run_case(t44p._empty_world_script(), "medium", "REAL SERVICE: empty world graph (medium)")

# 2. GOLDEN world graph with EVERY evidence_id removed -> no discoverable evidence.
def stripped_script():
    wg = json.loads(t44p._G[__import__("app.generation.provider", fromlist=["GenerationStage"]).GenerationStage.WORLD_GRAPH])
    for pl in wg["worldGraph"]["placements"]:
        pl.pop("evidenceId", None)
    script = t44p._golden_script()
    from app.generation.provider import GenerationStage
    script[GenerationStage.WORLD_GRAPH] = [json.dumps(wg)]
    return script


res, pub, store = run_case(
    stripped_script(), "medium",
    "REAL SERVICE: golden graph, ZERO evidence-linked placements (medium)",
)
if pub is not None:
    payload = pub.payload if hasattr(pub, "payload") else None
    print("  *** PUBLISHED with no evidence-bearing interaction ***")
