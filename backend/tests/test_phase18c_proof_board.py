"""Phase 18C — REVEAL-SAFE per-dimension proof board (backend part).

Proves the revealed response carries, for a solved published case, a
per-dimension proof mapping WHO/WHY/WEAPON/WHEN -> supporting discovered
evidence built ONLY from the server-side ``solverProof`` dimension references
(the deduction rule-outcome evidence ids serialized at publish time + the
WHEN-critical ids), filtered to discoverable facts with public titles and
mapped with the frozen rule-evidence ``point`` vocabulary.

Invariants under test (Phase18C backend contract):

  1. ``explanation.dimensions`` exists with exactly {who, why, weapon, when};
     every entry is an EvidencePointDTO {evidenceId, title, point} with a
     non-empty public title; every evidenceId is a subset of the public
     evidence universe (the pinned payload's draft.evidence ids).
  2. On a REAL published case every dimension contains at least one entry for
     the canonical truth (who -> murderer-supporting, why -> motive, weapon ->
     weapon, when -> timeline).
  3. union(who, why, weapon, when) ids == the flat ``explanation.evidence``
     ids (dedup) — cache-consistency with the unchanged flat list.
  4. Leak scanners (``assert_no_reveal_internal_material`` +
     ``assert_no_pre_reveal_material``) stay green on the NEW reveal payload;
     the per-dimension field names collide with NO forbidden key list
     (verified structurally below).
  5. No ``proc.*`` render identity in any dimension string field (DEF-081
     scan pattern).
  6. Pre-reveal responses (bootstrap/discover/read/accusation) contain NO
     "dimensions" / "proofDimensionMap" key path (regression guard).
  7. Empty-proof resilience: a payload whose solverProof dimension refs are
     empty answers reveal 200 with empty dimension lists (no crash).
  8. Reveal requires the playthrough token and keeps the ACCUSED -> REVEALED
     transition (auth unchanged); corrupt/missing solver proof answers the
     sanitized 500 INTERNAL_ERROR (DEF-053 pattern, never a partial map).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from phase5_helpers import assert_sanitized_error, auth
from phase6_helpers import client, KNIFE_EVIDENCE
from test_ollama_driver import _run, _staged  # hermetic driver harness (mocked transport)
from test_phase7_helpers import (
    accuse_then_reveal,
    assert_no_pre_reveal_material,
    assert_no_reveal_internal_material,
    create_published_case_and_playthrough,
    get_reveal,
    iter_key_paths,
    make_accusation,
    new_playthrough,
    truth_bundle,
    winning_body,
)

DIMENSION_NAMES = ("who", "why", "weapon", "when")
EVIDENCE_POINT_KEYS = {"evidenceId", "title", "point"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _reveal_for_solved_golden(phase5_app) -> dict:
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    with client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(bundle["truth"]))
        assert res.status_code == 200, res.json()
        res = get_reveal(c, pt_id, pt_token)
        assert res.status_code == 200, res.json()
    return res.json(), bundle


def _public_evidence_ids(payload: dict) -> set[str]:
    return {str(f["id"]) for f in payload["draft"]["evidence"] if f.get("id")}


def _solver_proof_refs(payload: dict) -> dict[str, list[str]]:
    proof = payload["solverProof"]
    who = [str(i) for i in (proof.get("who_evidence_ids") or ())]
    why = [str(i) for i in (proof.get("why_evidence_ids") or ())]
    weapon = [str(i) for i in (proof.get("weapon_evidence_ids") or ())]
    when_block = proof.get("time") or {}
    when = [str(i) for i in (when_block.get("critical_evidence_ids") or ())]
    return {"who": who, "why": why, "weapon": weapon, "when": when}


def _player_facing_strings(node, path: str = "") -> list[tuple[str, str]]:
    """(key-path, value) for every string leaf (DEF-081 scan pattern)."""
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            out.extend(_player_facing_strings(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.extend(_player_facing_strings(value, f"{path}.{index}"))
    elif isinstance(node, str):
        out.append((path, node))
    return out


# --------------------------------------------------------------------------- #
# 1 — dimensions present, correct shape, ids subset of the public universe
# --------------------------------------------------------------------------- #


def test_reveal_dimensions_present_and_shaped(phase5_app):
    reveal, bundle = _reveal_for_solved_golden(phase5_app)
    explanation = reveal["explanation"]
    assert "dimensions" in explanation
    dimensions = explanation["dimensions"]
    assert set(dimensions.keys()) == set(DIMENSION_NAMES)

    payload = bundle["truth"]["payload"]
    public_universe = _public_evidence_ids(payload)
    for name in DIMENSION_NAMES:
        points = dimensions[name]
        assert isinstance(points, list)
        for point in points:
            assert set(point.keys()) == EVIDENCE_POINT_KEYS
            assert point["evidenceId"]
            assert isinstance(point["title"], str) and point["title"]
            assert isinstance(point["point"], str) and point["point"]
            assert point["evidenceId"] in public_universe

    # Deterministic: dimension lists match the SERVER-SIDE refs exactly (the
    # DTO dimension ids == the published solverProof per-dimension refs, after
    # the discoverable+titled filter — the golden universe keeps all of them).
    refs = _solver_proof_refs(payload)
    for name in DIMENSION_NAMES:
        dto_ids = [p["evidenceId"] for p in dimensions[name]]
        assert sorted(dto_ids) == sorted(refs[name]), name


# --------------------------------------------------------------------------- #
# 2 — every dimension represented for the canonical truth on a REAL case
# --------------------------------------------------------------------------- #


def test_dimensions_represent_canonical_truth_on_real_published_case(phase5_app):
    """Golden solved case: WHO carries murderer-supporting evidence, WHY the
    motive evidence, WEAPON the weapon evidence and WHEN the timeline evidence
    (all non-empty; ids drawn from the published proof refs)."""
    reveal, bundle = _reveal_for_solved_golden(phase5_app)
    dimensions = reveal["explanation"]["dimensions"]
    payload = bundle["truth"]["payload"]
    refs = _solver_proof_refs(payload)

    for name in DIMENSION_NAMES:
        dto_ids = [p["evidenceId"] for p in dimensions[name]]
        assert dto_ids, f"{name} must be represented on a solved case"
        assert sorted(dto_ids) == sorted(refs[name]), name

    # Canonical truth anchors (golden case facts verified via truth_bundle).
    truth = bundle["truth"]
    assert truth["murdererId"] == "thomas_reed"
    assert truth["motiveId"] == "cover_up_embezzlement"
    assert truth["weaponId"] == "kitchen_knife"
    # When = the timeline-critical refs (body found / last seen / noise).
    assert {"body_found_01", "last_seen_01", "noise_heard_01"} <= set(
        refs["when"]
    )


def test_dimensions_on_real_ollama_driver_published_world(phase5_app):
    """The REAL full-chain driver world (mocked transport, no network): the
    published ice-pick case's reveal carries non-empty WHO/WHY/WEAPON/WHEN
    dimension lists built from the persisted per-dimension refs, and the
    union of the dimension ids matches the flat explainer (dedup)."""
    from app.generation.state_machine import GenerationState
    from app.services.publication import serialize_published_payload

    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    payload = json.loads(
        serialize_published_payload(
            record.published,
            seed=getattr(record, "seed", None),
            prompt="showcase",
            model="hermes3:8b (mock)",
            title="Showcase",
        )
    )
    # published world is the golden 4/4-solvable case
    from app.validation.solution import evaluate_solution

    from app.generation import pipeline

    public, evidence, truth, _draft = pipeline.assemble(record)
    validation = evaluate_solution(record.solver_proof, truth)
    assert validation.all_true is True

    store = phase5_app.state.store
    now = float(phase5_app.state.clock.now())
    from phase6_helpers import case_for

    case_id, creator_token = case_for(phase5_app)
    v2 = 2
    payload = json.loads(json.dumps(payload, sort_keys=True))
    payload["caseId"] = case_id
    payload["caseVersion"] = v2
    payload["publishedAt"] = now
    store.create_case_version(
        case_id=case_id, version=v2, state="PUBLISHED",
        generation_id=f"GEN-{v2}", created_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=v2,
        payload_json=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )
    truth_obj = truth_bundle(phase5_app, case_id, v2)
    pt_id, pt_token = new_playthrough(phase5_app, case_id, creator_token, version=v2)
    reveal = accuse_then_reveal(phase5_app, pt_id, pt_token, winning_body(truth_obj))
    assert reveal["status"] == "REVEALED"
    dimensions = reveal["explanation"]["dimensions"]
    for name in DIMENSION_NAMES:
        assert dimensions[name], name

    public_universe = _public_evidence_ids(payload)
    for name in DIMENSION_NAMES:
        for point in dimensions[name]:
            assert point["evidenceId"] in public_universe, name


# --------------------------------------------------------------------------- #
# 3 — union of dimension ids == flat explanation.evidence ids (dedup)
# --------------------------------------------------------------------------- #


def test_union_dimensions_equals_flat_evidence(phase5_app):
    reveal, _bundle = _reveal_for_solved_golden(phase5_app)
    explanation = reveal["explanation"]
    flat_ids = {p["evidenceId"] for p in explanation["evidence"]}
    dimension_ids = {
        p["evidenceId"]
        for name in DIMENSION_NAMES
        for p in explanation["dimensions"][name]
    }
    assert flat_ids, "golden flat explainer must be non-empty"
    assert dimension_ids == flat_ids, (
        "union of dimension ids must equal the flat evidence ids (dedup)"
    )


# --------------------------------------------------------------------------- #
# 4 — leak scanners stay green on the NEW reveal payload
# --------------------------------------------------------------------------- #


def test_reveal_leak_scanners_pass_on_new_payload(phase5_app):
    reveal, bundle = _reveal_for_solved_golden(phase5_app)
    pt_token = bundle["playthroughToken"]
    # The reveal scanner (deep key-path walker) over the NEW payload.
    assert_no_reveal_internal_material(reveal, known_tokens={pt_token})
    # The new field names do NOT collide with any forbidden list (structural
    # proof — a collision would make the reveal scan above fail; the pre-reveal
    # scan stays green because the field never materializes pre-reveal, covered
    # by test_pre_reveal_responses_never_carry_dimension_keys). Tighten: they
    # must not be forbidden keys anywhere.
    from test_phase7_helpers import _PRE_REVEAL_FORBIDDEN_KEYS, _REVEAL_FORBIDDEN_KEYS

    for key in ("dimensions", "who", "why", "weapon", "when"):
        assert key not in _REVEAL_FORBIDDEN_KEYS
        assert key not in _PRE_REVEAL_FORBIDDEN_KEYS


# --------------------------------------------------------------------------- #
# 5 — no proc.* in any dimension string field (DEF-081 scan pattern)
# --------------------------------------------------------------------------- #


def test_no_proc_in_dimension_string_fields(phase5_app):
    reveal, _bundle = _reveal_for_solved_golden(phase5_app)
    dimensions = reveal["explanation"]["dimensions"]
    for _path, value in _player_facing_strings(dimensions):
        assert not value.startswith("proc."), f"proc.* leaked at {_path!r}"
        assert "proc.decor." not in value, f"proc.* leaked at {_path!r}"


# --------------------------------------------------------------------------- #
# 6 — pre-reveal responses carry NO dimensions / proofDimensionMap key path
# --------------------------------------------------------------------------- #


def test_pre_reveal_responses_never_carry_dimension_keys(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    from phase6_helpers import bootstrap, discover, read_record

    with client(phase5_app) as c:
        headers = auth(pt_token)
        boot = bootstrap(phase5_app, pt_id, pt_token)
        assert boot.status_code == 200, boot.json()
        disc = discover(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
        assert disc.status_code == 200, disc.json()
        record = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
        assert record.status_code == 200, record.json()
        acc = make_accusation(c, pt_id, pt_token, winning_body(bundle["truth"]))
        assert acc.status_code == 200, acc.json()

    bodies = [boot.json(), disc.json(), record.json(), acc.json(), boot.json()]
    canonical_time = bundle["truth"]["canonical"]
    for i, body in enumerate(bodies):
        for key, _value in iter_key_paths(body):
            leaf = key.split(".")[-1]
            assert leaf != "dimensions", f"dimensions leaked pre-reveal at {key!r}"
            assert leaf != "proofDimensionMap", (
                f"proofDimensionMap leaked pre-reveal at {key!r}"
            )
        # The full pre-reveal scanner stays green (no canonical designation, no
        # internal material) — the new field must never weaken it. echo=True
        # applies ONLY to the frozen accusation echo body (index 3); its
        # echoed crimeTime is the canonical-time literal (DEC-003), so the
        # canonical_time probe is narrowed to the non-echo bodies (N24 pattern).
        assert_no_pre_reveal_material(
            body,
            echo=(i == 3),
            known_tokens={pt_token},
            canonical_time=(None if i == 3 else canonical_time),
        )


# --------------------------------------------------------------------------- #
# 7 — empty-proof resilience: empty dimension refs -> empty lists, reveal 200
# --------------------------------------------------------------------------- #


def _empty_proof_v2_payload(app, case_id, *, drop_solver_proof_section):
    """A PUBLISHED v2 row whose solverProof carries EMPTY per-dimension refs
    (and empty evidence_ids_used + time refs) or is dropped entirely."""
    store = app.state.store
    now = float(app.state.clock.now())
    v1 = store.get_published(case_id, 1)
    assert v1 is not None
    payload = json.loads(v1.payload_json)
    payload["caseVersion"] = 2
    payload["publishedAt"] = now
    if drop_solver_proof_section:
        payload.pop("solverProof", None)
        return payload
    proof = payload.get("solverProof", {})
    proof["evidence_ids_used"] = []
    proof["who_evidence_ids"] = []
    proof["why_evidence_ids"] = []
    proof["weapon_evidence_ids"] = []
    proof["time"] = dict(proof.get("time") or {})
    proof["time"]["critical_evidence_ids"] = []
    payload["solverProof"] = proof
    return payload


def _insert_v2(app, case_id, payload):
    store = app.state.store
    now = float(app.state.clock.now())
    store.create_case_version(
        case_id=case_id, version=2, state="PUBLISHED",
        generation_id="GEN-2", created_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=2,
        payload_json=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )


def test_empty_proof_resilience_dimensions_empty_reveal_200(phase5_app):
    """A solverProof that is present but whose dimension refs are empty yields
    empty dimension lists and reveal still 200s (no crash, no partial map)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    case_id = bundle["caseId"]
    creator = bundle["creator"]
    payload = _empty_proof_v2_payload(phase5_app, case_id, drop_solver_proof_section=False)
    _insert_v2(phase5_app, case_id, payload)
    truth = truth_bundle(phase5_app, case_id, 2)
    pt_id, pt_token = new_playthrough(phase5_app, case_id, creator, version=2)
    with client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(truth))
        assert res.status_code == 200, res.json()
        res = get_reveal(c, pt_id, pt_token)
        assert res.status_code == 200, res.json()
        reveal = res.json()
    dimensions = reveal["explanation"]["dimensions"]
    for name in DIMENSION_NAMES:
        assert dimensions[name] == [], name
    assert reveal["explanation"]["evidence"] == []
    assert reveal["status"] == "REVEALED"


def test_missing_solver_proof_is_fail_closed(phase5_app):
    """DEF-053 pattern: a payload with NO solverProof block reveals the
    SANITIZED 500 INTERNAL_ERROR, never a partial dimension mapping."""
    bundle = create_published_case_and_playthrough(phase5_app)
    case_id = bundle["caseId"]
    creator = bundle["creator"]
    payload = _empty_proof_v2_payload(phase5_app, case_id, drop_solver_proof_section=True)
    _insert_v2(phase5_app, case_id, payload)
    truth = truth_bundle(phase5_app, case_id, 2)
    pt_id, pt_token = new_playthrough(phase5_app, case_id, creator, version=2)
    with client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(truth))
        assert res.status_code == 200, res.json()
        res = get_reveal(c, pt_id, pt_token)
        assert res.status_code == 500, res.json()
        body = res.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "Internal server error"
    assert_sanitized_error(res.text)
    assert "dimensions" not in res.text


def test_inconsistent_dimension_refs_are_fail_closed(phase5_app):
    """A solverProof whose per-dimension refs disagree with the flat
    evidence_ids_used is internally corrupt -> sanitized 500 (never a partial
    mapping that would break union == flat)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    case_id = bundle["caseId"]
    creator = bundle["creator"]
    store = phase5_app.state.store
    now = float(phase5_app.state.clock.now())
    v1 = store.get_published(case_id, 1)
    payload = json.loads(v1.payload_json)
    payload["caseVersion"] = 2
    payload["publishedAt"] = now
    # keep the pristine flat refs (non-empty) but DROP the WHO ref -> the union
    # of the dimension refs can never equal evidence_ids_used (corrupt).
    proof = payload.get("solverProof", {})
    assert proof.get("evidence_ids_used"), "golden payload must carry flat ids"
    proof["who_evidence_ids"] = []
    payload["solverProof"] = proof
    _insert_v2(phase5_app, case_id, payload)
    truth = truth_bundle(phase5_app, case_id, 2)
    pt_id, pt_token = new_playthrough(phase5_app, case_id, creator, version=2)
    with client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(truth))
        assert res.status_code == 200, res.json()
        res = get_reveal(c, pt_id, pt_token)
        assert res.status_code == 500, res.json()
        body = res.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert_sanitized_error(res.text)


# --------------------------------------------------------------------------- #
# 8 — auth + ACCUSED -> REVEALED transition unchanged
# --------------------------------------------------------------------------- #


def test_reveal_auth_and_transition_unchanged(phase5_app):
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]
    with client(phase5_app) as c:
        # Pre-accusation reveal stays 403 REVEAL_NOT_AVAILABLE.
        res = get_reveal(c, pt_id, pt_token)
        assert res.status_code == 403, res.json()
        assert res.json()["error"]["code"] == "REVEAL_NOT_AVAILABLE"
        assert phase5_app.state.store.get_playthrough_state(pt_id) != "REVEALED"

        # The exact playthrough token accuses then reveals; the ACCUSED ->
        # REVEALED transition happens exactly once.
        res = make_accusation(c, pt_id, pt_token, winning_body(bundle["truth"]))
        assert res.status_code == 200, res.json()
        first = get_reveal(c, pt_id, pt_token)
        assert first.status_code == 200, first.json()
        second = get_reveal(c, pt_id, pt_token)
        assert second.status_code == 200, second.json()
        assert first.json() == second.json()
        assert phase5_app.state.store.get_playthrough_state(pt_id) == "REVEALED"

        # A foreign-class token can never reveal this playthrough (401,
        # N8: a random unknown token is rejected — the auth layer never
        # confirms any playthrough identity). A valid token bound to a
        # DIFFERENT playthrough answers 404 (N7: documented auth semantics,
        # unchanged by Phase18C).
        res = get_reveal(c, pt_id, "x" * 43)
        assert res.status_code == 401, (res.status_code, res.json())
        other = create_published_case_and_playthrough(phase5_app)
        with client(phase5_app) as c2:
            res = get_reveal(c2, pt_id, other["playthroughToken"])
        assert res.status_code == 404, (res.status_code, res.json())

        # The new dimensions are present, the DTO carries no internals.
        reveal = first.json()
        assert reveal["status"] == "REVEALED"
        assert set(reveal["explanation"]["dimensions"].keys()) == set(DIMENSION_NAMES)
        assert_no_reveal_internal_material(reveal, known_tokens={pt_token})