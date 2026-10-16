"""Phase 44 QA3 — independent final retest (hermetic, zero network).

Covers:
  A. DEF-107 / ADV-44-08  pure `_ensure_dimension_clues` declared-location +
     winning-fact binding + safety/resolution cleanliness.
  B. DEF-107 / ADV-44-08  REAL durable GenerationService: no undeclared location
     publishes; the winning WHY fact is bound and reachable.
  C. ADV-44-07            pure Gate 3: decoy does NOT satisfy the winning dim.
  D. ADV-44-07            REAL durable service: golden decoy variant AND the
     stronger "winner absent + decoy present" variant.
  E. ADV-44-01            zero-link never publishes; golden publishes.
  F. ADV-44-03            record-based accounting (no inflation).

Writes `qa3_retest_report.json` next to this file.
"""
import json
import os
import sys
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

REPO = Path(r"C:\Users\Fujitsu\coding\procedural-detective")
for p in (str(REPO / "backend"), str(REPO / "backend" / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import conftest  # noqa: E402
import test_phase44_playability_gate as t44  # noqa: E402
import test_phase44_playability_pipeline as t44p  # noqa: E402

from app.domain.eligibility import derive_universes  # noqa: E402
from app.environments.manifests import load_environment  # noqa: E402
from app.generation import pipeline, playability, safety  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import EvidenceSpec, PropSpec  # noqa: E402
from app.persistence.store import Store  # noqa: E402
from app.services.generation import GenerationService, _ensure_dimension_clues  # noqa: E402

PROMPT = t44p.GOLDEN_PROMPT
REPORT = {}

# --------------------------------------------------------------------------- #
# real durable service runner
# --------------------------------------------------------------------------- #


def run_real(script, difficulty, label):
    db = Path(tempfile.gettempdir()) / f"pd44qa3_{uuid.uuid4().hex}.sqlite"
    url = f"sqlite:///{db.as_posix()}"
    conftest.upgrade_db(url)
    store = Store(url)
    out = {"label": label, "difficulty": difficulty}
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
        out["status"] = res.status
        out["failure_code"] = res.failure_code
        pub = store.get_published(res.case_id, 1)
        out["published_row_present"] = pub is not None
        if pub is not None:
            payload = json.loads(pub.payload_json)
            wg = payload["draft"]["world_graph"]
            declared = sorted({l["location_id"] for l in wg["locations"]})
            placed = sorted({p["location_id"] for p in wg["placements"]})
            out["declared_locations"] = declared
            out["undeclared_placement_locations"] = sorted(set(placed) - set(declared))
            ev = sorted(
                {p.get("evidence_id") for p in wg["placements"] if p.get("evidence_id")}
            )
            out["reachable_evidence_ids"] = ev
            out["winning_why_reachable"] = "motive_audit_01" in ev
            out["decoy_why_reachable"] = "motive_no_affair_01" in ev
            out["dim_clue_placements"] = [
                (p["object_id"], p["location_id"], p.get("evidence_id"))
                for p in wg["placements"]
                if str(p.get("object_id", "")).startswith("dim_clue")
            ]
    finally:
        store.dispose()
        try:
            os.remove(db)
        except OSError:
            pass
    REPORT.setdefault("real_service", []).append(out)
    return out


# --------------------------------------------------------------------------- #
# script builders (independent of the implementation)
# --------------------------------------------------------------------------- #


def _golden_script():
    return t44p._golden_script()


def _strip_winning_why_script():
    wg = json.loads(t44p._G[GenerationStage.WORLD_GRAPH])
    for pl in wg["worldGraph"]["placements"]:
        if pl.get("objectId") == "motive_audit_file":
            pl["evidenceId"] = None
    script = t44p._golden_script()
    script[GenerationStage.WORLD_GRAPH] = [json.dumps(wg)]
    return script


def _decoy_why_script():
    wg = json.loads(t44p._G[GenerationStage.WORLD_GRAPH])
    for pl in wg["worldGraph"]["placements"]:
        if pl.get("objectId") == "motive_audit_file":
            pl["evidenceId"] = "motive_no_affair_01"
    script = t44p._golden_script()
    script[GenerationStage.WORLD_GRAPH] = [json.dumps(wg)]
    return script


def _decoy_why_winner_indiscoverable_script():
    """Decoy linked AND the winning motive fact marked non-discoverable (still
    canonical, so Gate 1/2 can stay VALID; supplementation cannot bind it)."""
    wg = json.loads(t44p._G[GenerationStage.WORLD_GRAPH])
    for pl in wg["worldGraph"]["placements"]:
        if pl.get("objectId") == "motive_audit_file":
            pl["evidenceId"] = "motive_no_affair_01"
    ev = json.loads(t44p._G[GenerationStage.EVIDENCE])
    for e in ev["evidence"]:
        if e.get("id") == "motive_audit_01":
            e["discoverable"] = False
    script = t44p._golden_script()
    script[GenerationStage.WORLD_GRAPH] = [json.dumps(wg)]
    script[GenerationStage.EVIDENCE] = [json.dumps(ev)]
    return script


def _zero_link_script():
    return t44p._zero_link_script()


# --------------------------------------------------------------------------- #
# A. DEF-107 pure supplementation
# --------------------------------------------------------------------------- #


def part_a():
    draft = t44._drop_objects(t44.golden_draft(), {"motive_audit_file"})
    kit = load_environment("apartment")
    supp = _ensure_dimension_clues(draft, kit)
    declared = {loc.location_id for loc in supp.world_graph.locations}
    added = [
        p
        for p in supp.world_graph.placements
        if str(getattr(p, "object_id", "")).startswith("dim_clue_")
    ]
    object_ids = {o.object_id for o in supp.objects}
    evidence_ids = {e.id for e in supp.evidence}
    safety_issues = safety.validate_world_graph(supp.world_graph, object_ids, evidence_ids)
    resolution_issues = pipeline._world_graph_resolution_issues(supp)
    why_clue = [
        p for p in added if str(p.object_id) == "dim_clue_why"
    ]
    REPORT["part_a_def107_pure"] = {
        "declared_locations": sorted(declared),
        "added_dim_clue_placements": [
            (p.object_id, p.location_id, p.evidence_id) for p in added
        ],
        "all_added_locations_declared": all(p.location_id in declared for p in added),
        "safety_issues": list(safety_issues),
        "resolution_issues": list(resolution_issues),
        "winning_why_bound": bool(why_clue)
        and str(why_clue[0].evidence_id) == "motive_audit_01",
    }


# --------------------------------------------------------------------------- #
# C. ADV-44-07 pure Gate 3
# --------------------------------------------------------------------------- #


def part_c():
    # decoy variant (winner object removed, decoy linked) -- test builder
    decoy = t44._drop_objects(t44.golden_draft(), {"motive_audit_file"})
    decoy = t44._link_existing_fact(decoy, "decoy_motive_record", "motive_no_affair_01")
    rep_decoy = t44.evaluate(decoy, "medium")
    rep_golden = t44.evaluate(t44.golden_draft(), "medium")
    REPORT["part_c_adv4407_pure"] = {
        "decoy_valid": rep_decoy.valid,
        "decoy_unreachable": list(rep_decoy.unreachable_critical_dimensions),
        "decoy_issues": list(rep_decoy.issue_codes),
        "golden_valid": rep_golden.valid,
        "golden_reachable": list(rep_golden.reachable_critical_dimensions),
        "golden_issues": list(rep_golden.issue_codes),
    }


def part_d_guard():
    """Independent reproduction of the REAL service publication guard on the
    frozen decoy candidate (no supplementation on this path)."""
    controller, handle, record = t44p._run(t44p._decoy_why_script(), "medium")
    frozen = None
    stage_trusted = GenerationStage.WORLD_GRAPH in record.stage_done
    if record.state.name != "PUBLISHED":
        result = controller.publish(handle.attempt_id, hold_ok=True)
        frozen = result.published
    else:
        frozen = record.published
    record.published = frozen
    stub = t44p._make_guard_stub()
    status = t44p._publish_via_guard(stub, record, "medium")
    gate3 = playability.validate_published_candidate(frozen, "medium")
    REPORT["part_d_adv4407_guard"] = {
        "world_graph_stage_trusted": stage_trusted,
        "frozen_gate3_valid": gate3.valid,
        "frozen_gate3_unreachable": list(gate3.unreachable_critical_dimensions),
        "guard_status": status,
        "publication_calls": stub._publication.calls,
        "record_state": record.state.value,
    }


# --------------------------------------------------------------------------- #
# F. ADV-44-03 accounting
# --------------------------------------------------------------------------- #


def _link_existing(draft, object_id, evidence_id):
    from app.generation.schemas import ObjectSpec, PlacementSpec

    return replace(
        draft,
        objects=tuple(draft.objects)
        + (ObjectSpec(object_id=object_id, asset_id="PROP_VASE_01", affordances=("INSPECTABLE",)),),
        world_graph=replace(
            draft.world_graph,
            placements=tuple(draft.world_graph.placements)
            + (
                PlacementSpec(
                    object_id=object_id,
                    asset_id="PROP_VASE_01",
                    location_id=t44.SCENE_LOCATION,
                    anchor="shelf_01",
                    interaction="read",
                    evidence_id=evidence_id,
                ),
            ),
        ),
    )


def part_f():
    # ADV-44-03 — ONE record shared by MANY objects must not inflate
    # meaningful/source counts. Link an EXISTING discoverable fact with a first
    # object, then a second object to the SAME record.
    base = t44.easy_at_min()
    existing = str(base.evidence[0].id)
    b = t44.evaluate(base, "easy")
    one = t44.evaluate(_link_existing(base, "share_a", existing), "easy")
    two = t44.evaluate(
        _link_existing(_link_existing(base, "share_a", existing), "share_b", existing),
        "easy",
    )
    REPORT["part_f_adv4403"] = {
        "record_shared": existing,
        "base_mean": b.meaningful_inspectable_count,
        "base_src": b.distinct_meaningful_source_count,
        "one_obj_mean": one.meaningful_inspectable_count,
        "one_obj_src": one.distinct_meaningful_source_count,
        "two_obj_mean": two.meaningful_inspectable_count,
        "two_obj_src": two.distinct_meaningful_source_count,
        "second_object_mean_inflation": (
            two.meaningful_inspectable_count - one.meaningful_inspectable_count
        ),
        "second_object_src_inflation": (
            two.distinct_meaningful_source_count - one.distinct_meaningful_source_count
        ),
    }


def main():
    part_a()
    part_c()
    part_d_guard()
    part_f()

    # B: DEF-107 real service (winning-WHY link removed -> supplementation)
    run_real(
        _strip_winning_why_script(),
        "medium",
        "DEF-107 strip winning-WHY link (legacy supplementation)",
    )
    # E: ADV-44-01
    run_real(_zero_link_script(), "medium", "ADV-44-01 zero evidence links")
    run_real(_golden_script(), "medium", "ADV-44-01 golden control")
    # D: ADV-44-07 real service decoy (winner present) + winner absent
    run_real(_decoy_why_script(), "medium", "ADV-44-07 decoy WHY, winner present")
    run_real(
        _decoy_why_winner_indiscoverable_script(),
        "medium",
        "ADV-44-07 decoy WHY, winner non-discoverable (unsupplementable)",
    )

    out = Path(__file__).with_name("qa3_retest_report.json")
    out.write_text(json.dumps(REPORT, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(REPORT, indent=2, sort_keys=True))
    print("WROTE", out)


if __name__ == "__main__":
    main()
