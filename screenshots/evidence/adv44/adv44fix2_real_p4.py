"""Phase 44 fix-round — REAL durable GenerationService end-to-end for the NEW
Gate 3 semantic gap: a DECOY clue covers a required dimension while the WINNING
proof fact for that dimension is NOT player-reachable.

Variant A: golden script but the `motive_audit_file` placement's evidenceId is
repointed from the WINNING motive (`motive_audit_01`) to a DECOY excluded motive
(`motive_no_affair_01`). The WHY dimension stays "reachable" (via the decoy), but
the player can never discover the winning motive link. If the service PUBLISHES,
Gate 3 is bypassed.
"""
import sys, tempfile, os, json, uuid
from pathlib import Path

REPO = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
for p in (str(REPO / "backend"), str(REPO / "backend" / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import conftest  # noqa: E402
from app.persistence.store import Store  # noqa: E402
from app.services.generation import GenerationService  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation import pipeline, playability  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
import test_phase44_playability_pipeline as t44p  # noqa: E402

PROMPT = t44p.GOLDEN_PROMPT


def decoy_script():
    wg = json.loads(t44p._G[GenerationStage.WORLD_GRAPH])
    for pl in wg["worldGraph"]["placements"]:
        if pl.get("objectId") == "motive_audit_file":
            pl["evidenceId"] = "motive_no_affair_01"   # decoy, NOT the winner
    script = t44p._golden_script()
    script[GenerationStage.WORLD_GRAPH] = [json.dumps(wg)]
    return script


def run_case(script, difficulty, label):
    print("\n" + "=" * 78 + f"\n{label}\n" + "=" * 78)
    db = Path(tempfile.gettempdir()) / f"pd44fix2_{uuid.uuid4().hex}.sqlite"
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
        if pub is not None:
            payload = json.loads(pub.payload_json)
            wg = payload["draft"]["world_graph"]
            ev = sorted({p.get("evidence_id") for p in wg["placements"] if p.get("evidence_id")})
            print("  reachable evidence ids:", ev)
            print("  winning WHY 'motive_audit_01' reachable:", "motive_audit_01" in ev)
            print("  decoy 'motive_no_affair_01' reachable:", "motive_no_affair_01" in ev)
            if "motive_audit_01" not in ev:
                print("  *** PUBLISHED while the winning WHY fact is unreachable ***")
        return res, pub, store
    finally:
        store.dispose()
        try:
            os.remove(db)
        except OSError:
            pass


run_case(decoy_script(), "medium",
         "REAL SERVICE: decoy motive covers WHY, winning motive unreachable (medium)")
