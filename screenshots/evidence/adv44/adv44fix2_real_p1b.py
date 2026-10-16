"""Phase 44 fix-round — REAL durable GenerationService end-to-end for the
_ensure_dimension_clues LEGACY-PATH location corruption.

Variant B: golden script with ONLY the winning-WHY placement link removed
(`motive_audit_file` evidenceId -> None). The apartment legacy path then runs
`_ensure_dimension_clues`, which adds a `dim_clue_why` placement using a KIT
zone_id (`apartment_kitchen`) while the provider world declares
`miller_apartment_kitchen`. If it PUBLISHES, the frozen candidate has a placement
at an UNDECLARED location (structurally invalid world graph).
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


def strip_winning_why_script():
    wg = json.loads(t44p._G[GenerationStage.WORLD_GRAPH])
    for pl in wg["worldGraph"]["placements"]:
        if pl.get("objectId") == "motive_audit_file":
            pl["evidenceId"] = None   # remove the ONLY winning-WHY link
    script = t44p._golden_script()
    script[GenerationStage.WORLD_GRAPH] = [json.dumps(wg)]
    return script


def run_case(script, difficulty, label):
    print("\n" + "=" * 78 + f"\n{label}\n" + "=" * 78)
    db = Path(tempfile.gettempdir()) / f"pd44fix2b_{uuid.uuid4().hex}.sqlite"
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
            draft = payload["draft"]
            wg = draft["world_graph"]
            declared = sorted({l["location_id"] for l in wg["locations"]})
            placed = sorted({p["location_id"] for p in wg["placements"]})
            undeclared = sorted(set(placed) - set(declared))
            print("  declared locations:", declared)
            print("  placement locationIds:", placed)
            print("  UNDECLARED placement locations:", undeclared)
            ev = sorted({p.get("evidence_id") for p in wg["placements"] if p.get("evidence_id")})
            print("  reachable evidence ids:", ev)
            print("  winning WHY 'motive_audit_01' reachable:", "motive_audit_01" in ev)
            dim_clues = [p for p in wg["placements"] if str(p.get("object_id", "")).startswith("dim_clue")]
            print("  dim_clue placements:", [(p["object_id"], p["location_id"], p.get("evidence_id")) for p in dim_clues])
            if undeclared:
                print("  *** PUBLISHED with placement(s) at UNDECLARED location(s) ***")
        return res, pub, store
    finally:
        store.dispose()
        try:
            os.remove(db)
        except OSError:
            pass


run_case(strip_winning_why_script(), "medium",
         "REAL SERVICE: winning-WHY link removed; legacy-path supplementation (medium)")
