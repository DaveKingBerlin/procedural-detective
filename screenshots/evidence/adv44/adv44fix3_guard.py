"""Phase 44 FINAL re-review - capture the EXACT frozen PublishedCaseVersion the
publication guard validated (real durable service) and re-run Gate 3 over it.

Also: supplementation safety across all five environment kits.
"""
import sys, tempfile, os, json, uuid
from pathlib import Path

REPO = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
for p in (str(REPO / "backend"), str(REPO / "backend" / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import conftest  # noqa: E402
from app.persistence.store import Store  # noqa: E402
from app.services.generation import GenerationService, _ensure_dimension_clues  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation import playability, safety  # noqa: E402
from app.environments.manifests import load_environment  # noqa: E402
import test_phase44_playability_pipeline as t44p  # noqa: E402
import test_phase44_playability_gate as t44  # noqa: E402

PROMPT = t44p.GOLDEN_PROMPT
_G = t44p._G


def _script(mut=None):
    wg = json.loads(_G[GenerationStage.WORLD_GRAPH])
    if mut:
        wg = mut(wg)
    s = t44p._golden_script()
    s[GenerationStage.WORLD_GRAPH] = [json.dumps(wg)]
    return s


def relink(wg, oid, eid):
    for pl in wg["worldGraph"]["placements"]:
        if pl.get("objectId") == oid:
            pl["evidenceId"] = eid
    return wg


class _Capture:
    def __init__(self, inner):
        self.inner = inner
        self.published = None

    def publish_transactionally(self, published, **kw):
        self.published = published
        return self.inner.publish_transactionally(published, **kw)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def run(script, label, difficulty="medium"):
    print("\n" + "=" * 78 + f"\n{label}\n" + "=" * 78)
    db = Path(tempfile.gettempdir()) / f"pd44fix3g_{uuid.uuid4().hex}.sqlite"
    url = f"sqlite:///{db.as_posix()}"
    conftest.upgrade_db(url)
    store = Store(url)
    try:
        svc = GenerationService(
            settings=conftest.make_phase5_settings(url),
            store=store,
            provider_factory=lambda: FakeProvider(script=script),
        )
        cap = _Capture(svc._publication)
        svc._publication = cap
        sess = svc.create_anonymous_quota_session()
        res = svc.start_case_generation(
            PROMPT, anonymous_quota_session_id=sess.anonymous_quota_session_id,
            difficulty=difficulty)
        pub_row = store.get_published(res.case_id, 1)
        print("  status:", res.status, "| durable row:", pub_row is not None,
              "| captured candidate:", cap.published is not None)
        if cap.published is not None:
            r = playability.validate_published_candidate(cap.published, difficulty)
            print("  GATE 3 over exactly-persisted candidate: valid=", r.valid,
                  "unreach=", r.unreachable_critical_dimensions,
                  "issues=", r.issue_codes)
            draft = cap.published.draft
            reach = sorted({str(p.evidence_id) for p in draft.world_graph.placements
                            if p.interaction and p.evidence_id is not None})
            print("  reachable evidence ids:", reach)
            truth = cap.published.truth
            from app.domain.solver import solve_case
            proof = solve_case(cap.published.public, tuple(cap.published.evidence))
            print("  winner why/weapon/who:", proof.why.winner, proof.weapon.winner,
                  proof.who.winner)
            print("  winner WHY reachable:", proof.why.winner and any(
                str(getattr(pr, 'type', '')) == 'MOTIVE_LINKED_TO_PERSON'
                and str(getattr(pr, 'motive_id', '')) == proof.why.winner
                for f in cap.published.evidence if f.id in reach
                for pr in f.propositions))
        return cap
    finally:
        store.dispose()
        try:
            os.remove(db)
        except OSError:
            pass


run(_script(), "G0 CONTROL golden (medium)")
run(_script(lambda wg: relink(wg, "motive_audit_file", "motive_no_affair_01")),
    "G1 decoy-WHY exact ADV-44-07 (medium)")
run(_script(lambda wg: relink(wg, "kitchen_knife", "forensic_letter_opener_01")),
    "G2 decoy-WEAPON (medium)")
run(_script(lambda wg: relink(wg, "victim_body_placeholder", "alibi_claim_thomas_01")),
    "G3 decoy-WHEN (medium)")
run(_script(lambda wg: relink(wg, "motive_audit_file", None)),
    "G4 winner-WHY stripped (medium)")


# --------------------------------------------------------------------------- #
# Supplementation safety across ALL kits.
# --------------------------------------------------------------------------- #
print("\n" + "=" * 78 + "\nALL-KIT supplementation safety\n" + "=" * 78)
d0 = t44._drop_objects(t44.golden_draft(), {"motive_audit_file"})
for kit_name in ("apartment", "office", "warehouse", "hotel_suite", "mansion"):
    try:
        kit = load_environment(kit_name)
    except Exception as exc:  # noqa: BLE001
        print(f"  {kit_name}: load_environment raised {type(exc).__name__}: {exc}")
        continue
    out = _ensure_dimension_clues(d0, kit)
    declared = {l.location_id for l in out.world_graph.locations}
    placed = {p.location_id for p in out.world_graph.placements}
    issues = safety.validate_world_graph(
        out.world_graph, {o.object_id for o in out.objects},
        {e.id for e in out.evidence})
    dc = [(p.object_id, p.evidence_id, p.location_id) for p in out.world_graph.placements
          if str(getattr(p, "object_id", "")).startswith("dim_clue")]
    print(f"  {kit_name}: added={dc} undeclared={sorted(placed - declared)} "
          f"safety_issues={issues}")

print("\nDONE")
