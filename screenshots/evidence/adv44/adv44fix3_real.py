"""Phase 44 FINAL re-review (fix round 3) - REAL durable GenerationService.

Hermetic (FakeProvider, migrated SQLite). Replicates ADV-44-07 end-to-end and
attempts decoy WHO / WEAPON / WHEN / mixed + supplementation on the legacy path.
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
from app.generation.provider import GenerationStage  # noqa: E402
import test_phase44_playability_pipeline as t44p  # noqa: E402

PROMPT = t44p.GOLDEN_PROMPT
_G = t44p._G


def _script(wg_mut=None):
    wg = json.loads(_G[GenerationStage.WORLD_GRAPH])
    if wg_mut is not None:
        wg = wg_mut(wg)
    script = t44p._golden_script()
    script[GenerationStage.WORLD_GRAPH] = [json.dumps(wg)]
    return script


def relink(wg, object_id, evidence_id):
    for pl in wg["worldGraph"]["placements"]:
        if pl.get("objectId") == object_id:
            pl["evidenceId"] = evidence_id
    return wg


def run_case(script, difficulty, label):
    print("\n" + "=" * 78 + f"\n{label}\n" + "=" * 78)
    db = Path(tempfile.gettempdir()) / f"pd44fix3_{uuid.uuid4().hex}.sqlite"
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
        cv = store.get_case_version(res.case_id, 1)
        pub = store.get_published(res.case_id, 1)
        print("  status:", res.status, "| failure_code:", res.failure_code)
        print("  durable case_version state:", getattr(cv, "state", None))
        print("  durable published row present:", pub is not None)
        if pub is not None:
            payload = json.loads(pub.payload_json)
            wg = payload["draft"]["world_graph"]
            declared = sorted({l["location_id"] for l in wg["locations"]})
            placed = sorted({p["location_id"] for p in wg["placements"]})
            undeclared = sorted(set(placed) - set(declared))
            ev = sorted({p.get("evidence_id") for p in wg["placements"]
                         if p.get("evidence_id")})
            print("  declared locations:", declared)
            print("  undeclared placement locations:", undeclared)
            print("  reachable evidence ids:", ev)
            for winner in ("motive_audit_01", "forensic_knife_match_01",
                           "body_found_01", "cctv_thomas_scene_01"):
                print(f"  winning fact {winner} reachable:", winner in ev)
            dc = [p for p in wg["placements"]
                  if str(p.get("object_id", "")).startswith("dim_clue")]
            print("  dim_clue placements:",
                  [(p["object_id"], p["evidence_id"], p["location_id"]) for p in dc])
        return res, pub, store
    finally:
        store.dispose()
        try:
            os.remove(db)
        except OSError:
            pass


# R0 control: golden medium publishes.
run_case(_script(), "medium", "R0 CONTROL golden (medium) -> expect PUBLISHED")

# R1 exact ADV-44-07: winner WHY unlinked, decoy WHY linked.
run_case(_script(lambda wg: relink(wg, "motive_audit_file", "motive_no_affair_01")),
         "medium", "R1 decoy-WHY (ADV-44-07 exact) -> expect FAILED, no row")

# R1b winner WHY link removed entirely (supplementation must bind winner at a
# DECLARED location and publish).
run_case(_script(lambda wg: relink(wg, "motive_audit_file", None)),
         "medium", "R1b winner-WHY stripped -> expect PUBLISHED @declared location")

# R1c winner WHY AND winner WHO-referencing fact removed (motive unlinked; the
# only other reachable winner reference is the motive link itself) -> both WHO
# and WHY must be supplemented at a declared location.
run_case(_script(lambda wg: relink(wg, "motive_audit_file", "cctv_michael_office_01")),
         "medium", "R1c decoy-WHO (winner ref unreachable) -> expect FAILED")

# R2 decoy WEAPON: winner positive replaced by a negative non-winner match.
run_case(_script(lambda wg: relink(wg, "kitchen_knife", "forensic_letter_opener_01")),
         "medium", "R2 decoy-WEAPON -> expect FAILED, no row")

# R3 decoy WHEN: critical time fact replaced by a non-critical WHO/WHEN fact.
run_case(_script(lambda wg: relink(wg, "victim_body_placeholder", "alibi_claim_thomas_01")),
         "medium", "R3 decoy-WHEN -> expect FAILED, no row")


def mixed(wg):
    relink(wg, "motive_audit_file", "motive_no_affair_01")
    relink(wg, "kitchen_knife", "forensic_letter_opener_01")
    relink(wg, "victim_body_placeholder", "alibi_claim_thomas_01")
    return wg


run_case(_script(mixed), "medium", "R4 mixed decoys -> expect FAILED, no row")

# R5 strip winner WHY + winner WHO reference (both) -> supplementation publishes.
run_case(_script(lambda wg: relink(wg, "motive_audit_file", None)),
         "hard", "R5 winner-WHY stripped (hard) -> expect FAILED (below hard min)")

print("\nDONE")
