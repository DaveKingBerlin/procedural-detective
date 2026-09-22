"""Phase 20 (PD-SEC-01) — pre-reveal evidence disclosure & discovery bypass
security regression suite (Phase20 §3/§4.4 + the added cross-scope items).

The Luna HIGH finding is that a valid player can enumerate undiscovered
evidence metadata before investigating and can discover evidence by id
without any world interaction. Phase 20 fixes, locked here:

1. fresh playthrough public-case (GET /playthroughs/{id}/public-case)
   contains NO undiscovered evidence ids;
2. fresh investigation bootstrap contains NO undiscovered evidence ids;
3. undiscovered evidence titles/descriptions are ABSENT from every pre-reveal
   DTO (bootstrap + playthrough public-case);
4. direct client-facing POST /evidence/{evidenceId}/discover is REJECTED
   (the route is REMOVED — 404, never 200);
5. a rejected direct discovery does NOT mutate PlayerKnowledge;
6. a valid object interaction (laptop/knife + driver worlds via
   MockOllamaTransport) discovers the linked evidence;
7. discovered evidence is visible afterward (bootstrap evidenceId + flag +
   PlayerKnowledge);
8. the reveal-only proof projection (explanation.dimensions etc.) remains
   reveal-gated — absent pre-reveal, present only after reveal;
9. the bootstrap candidates block is unchanged (no candidate/winner leak);
10. accusation/reveal behavior is unchanged (same truth, same scoring).

Cross-scope items (Phase 20 §4.1 + the audit context):
- the CASE-scoped dossier (GET /cases/{id}, creator credential) keeps the
  REQUIREMENTS 41.2 public-case evidence list (the case owner generated the
  case — that data is already legitimately known to their role; the Luna
  audit flagged the PLAYTHROUGH-scoped route), while the PLAYTHROUGH-scoped
  public-case evidence list stays EMPTY pre-discovery (no titles/descriptions
  before discovery);
- the bootstrap world object has NO evidenceId for undiscovered evidence
  pre-discovery, and after a driver-world interact the discovered evidenceId
  appears ONLY then.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase5_helpers import (  # noqa: E402
    assert_no_hidden_leaks,
    auth,
)
from phase6_helpers import (  # noqa: E402
    BODY_OBJECT,
    EMAIL_EVIDENCE,
    KNIFE_EVIDENCE,
    KNIFE_OBJECT,
    LAPTOP_OBJECT,
    case_for,
    client,
    interact,
    playthrough,
)
from test_ollama_driver import _run, _staged  # noqa: E402 — mocked transport
from test_phase7_helpers import (  # noqa: E402
    accuse_then_reveal,
    assert_candidates_unmarked,
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

# Driver-world canonical evidence ids (MockOllamaTransport; Phase 19C §7):
# the laptop is the activity-log device bound to d_ev_when_obs; the sharp
# weapons resolve to their canonical forensic records.
DRIVER_LAPTOP_EVIDENCE = "d_ev_when_obs"
DRIVER_KNIFE_EVIDENCE = "d_ev_weapon_false_kitchenknife"


def _playthrough_public_case(app, pt_id, pt_token):
    with client(app) as c:
        res = c.get(f"/api/v1/playthroughs/{pt_id}/public-case", headers=auth(pt_token))
        assert res.status_code == 200, res.json()
        return res.json()


def _case_public_case(app, case_id, creator):
    with client(app) as c:
        res = c.get(f"/api/v1/cases/{case_id}", headers=auth(creator))
        assert res.status_code == 200, res.json()
        return res.json()


def _bootstrap(app, pt_id, pt_token):
    from phase6_helpers import bootstrap

    res = bootstrap(app, pt_id, pt_token)
    assert res.status_code == 200, res.json()
    return res.json()


def _published_payload(app, case_id, version=1):
    row = app.state.store.get_published(case_id, version)
    assert row is not None
    return json.loads(row.payload_json)


# --------------------------------------------------------------------------- #
# 1+2+3 — fresh pre-reveal DTOs expose NO undiscovered evidence metadata
# --------------------------------------------------------------------------- #


def test_1_fresh_playthrough_public_case_has_no_undiscovered_evidence_ids(phase5_app):
    """A fresh playthrough public-case carries an EMPTY evidence list and null
    world-graph placement evidenceIds (PD-SEC-01 §4.1)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    body = _playthrough_public_case(phase5_app, pt_id, pt_token)
    assert body["evidence"] == []
    assert body["worldGraph"]["placements"], "golden world has placements"
    for placement in body["worldGraph"]["placements"]:
        assert placement["evidenceId"] is None, placement
    assert_no_hidden_leaks(body)


def test_1b_case_scoped_dossier_keeps_41_2_evidence_list(phase5_app):
    """The CASE-scoped dossier (creator credential) keeps the REQUIREMENTS
    41.2 public-case evidence list — the audit flagged the PLAYTHROUGH-scoped
    route only, and the case owner generated the case (documented decision).
    The playthrough-scoped route strips the same list (test 1)."""
    case_id, creator = case_for(phase5_app)
    body = _case_public_case(phase5_app, case_id, creator)
    assert len(body["evidence"]) == 16, "case-scoped dossier keeps the full list"
    assert len(body["worldGraph"]["placements"]) == 9
    for placement in body["worldGraph"]["placements"]:
        if placement["evidenceId"] is not None:
            assert placement["evidenceId"] in {e["id"] for e in body["evidence"]}


def test_2_fresh_bootstrap_contains_no_undiscovered_evidence_ids(phase5_app):
    """Every world object of a FRESH bootstrap carries ``evidenceId: None`` —
    no undiscovered evidence id is player-known pre-reveal."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    body = _bootstrap(phase5_app, pt_id, pt_token)
    objects = body["scene"]["worldObjects"]
    assert objects, "golden world has objects"
    for obj in objects:
        assert obj["evidenceId"] is None, obj["objectId"]
        assert obj["discovered"] is False
        assert obj["read"] is False


def test_3_undiscovered_titles_descriptions_absent(phase5_app):
    """No undiscovered evidence title/description may appear in ANY pre-reveal
    DTO (bootstrap + playthrough public-case). The golden published payload's
    evidence presentation strings are the forbidden set MINUS anything that is
    ALREADY legitimately public world material (person names, motive labels,
    location names, scene names — REQUIREMENTS 41.2 public surface) and the
    Phase 7 J/K candidate-universe ids."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    payload = _published_payload(phase5_app, case_id, 1)

    # Every string already public outside of evidence: an evidence title that
    # merely echoes a public person/location/motive name is not a leak (the
    # public world independently carries it); an evidence-specific
    # description/body is.
    public_safe: set[str] = set()
    for person in payload["draft"].get("persons") or ():
        name = person.get("name")
        if isinstance(name, str) and name:
            public_safe.add(name)
    for motive in payload["draft"].get("motives") or ():
        label = motive.get("label")
        if isinstance(label, str) and label:
            public_safe.add(label)
    for loc in payload["draft"].get("locations") or ():
        name = loc.get("name")
        if isinstance(name, str) and name:
            public_safe.add(name)
    scene = payload["draft"].get("scene") or {}
    if isinstance(scene.get("name"), str):
        public_safe.add(scene["name"])
    universes = payload.get("universes") or {}
    public_ids = {
        str(i)
        for list_key in ("suspect_ids", "motive_ids", "weapon_ids")
        for i in (universes.get(list_key) or ())
    }
    public_safe |= public_ids

    forbidden_elements: list[str] = []
    for fact in payload["draft"]["evidence"]:
        presentation = fact.get("presentation") or {}
        for value in presentation.values():
            if not isinstance(value, str) or not value:
                continue
            if value in public_safe:
                continue  # echoes public world material / candidate id
            forbidden_elements.append(value)
    assert forbidden_elements, "golden evidence must carry non-public content to scan"

    bundles = {
        "bootstrap": json.dumps(
            _bootstrap(phase5_app, pt_id, pt_token), sort_keys=True
        ),
        "playthrough public-case": json.dumps(
            _playthrough_public_case(phase5_app, pt_id, pt_token), sort_keys=True
        ),
    }
    for scope_name, text in bundles.items():
        for needle in forbidden_elements:
            assert needle not in text, (
                f"undiscovered evidence content leaked in {scope_name}: {needle!r}"
            )
        for marker in ("propositions", "sourceRef", "observedAt"):
            assert marker not in text, (
                f"structural evidence marker leaked in {scope_name}: {marker!r}"
            )


# --------------------------------------------------------------------------- #
# 4+5 — direct discovery route is gone; rejection never mutates knowledge
# --------------------------------------------------------------------------- #


def test_4_direct_discovery_without_interaction_rejected(phase5_app):
    """POST /playthroughs/{id}/evidence/{evidenceId}/discover is REMOVED: it
    answers 404 (route gone) for ANY evidence id — reachable or fabricated —
    and never a 200."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    for evidence_id in (
        KNIFE_EVIDENCE,  # placement-reachable in the golden world
        EMAIL_EVIDENCE,
        "ghost_evidence_99",
        "truth",
        "",
        ".." * 20,
    ):
        with client(phase5_app) as c:
            res = c.post(
                f"/api/v1/playthroughs/{pt_id}/evidence/{evidence_id}/discover",
                headers=auth(pt_token),
            )
        assert res.status_code == 404, (evidence_id, res.status_code)
        assert res.json()["error"]["code"] == "NOT_FOUND"


def test_5_rejected_direct_discovery_does_not_mutate_knowledge(phase5_app):
    """A rejected direct discovery leaves PlayerKnowledge untouched (no
    discovered id, no visited location, no read id)."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    with client(phase5_app) as c:
        for evidence_id in (KNIFE_EVIDENCE, EMAIL_EVIDENCE, "fake"):
            res = c.post(
                f"/api/v1/playthroughs/{pt_id}/evidence/{evidence_id}/discover",
                headers=auth(pt_token),
            )
            assert res.status_code == 404
    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == ()
    assert snap.read == ()
    assert snap.visited == ()


# --------------------------------------------------------------------------- #
# 6+7 — valid object interaction discovers linked evidence, then it shows
# --------------------------------------------------------------------------- #


def test_6_object_interaction_discovers_linked_evidence_golden(phase5_app):
    """The golden (dev-mode published) world: laptop -> email, knife ->
    forensic; discovery happens through the validated interaction."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)

    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200, res.json()
    assert res.json()["evidenceId"] == EMAIL_EVIDENCE
    assert res.json()["discovery"]["state"] == "discovered"

    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200, res.json()
    assert res.json()["evidenceId"] == KNIFE_EVIDENCE

    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert set(snap.discovered) == {EMAIL_EVIDENCE, KNIFE_EVIDENCE}


def test_6b_object_interaction_discovers_driver_world_evidence(phase5_app):
    """A REAL driver-world published world (MockOllamaTransport, no network):
    the laptop discovers the canonical activity-log record d_ev_when_obs and
    the kitchen knife discovers the canonical forensic record. The driver
    payload is published as version 2 of a golden case (same pattern as
    test_phase18c_proof_board's driver-world test)."""
    from app.generation.state_machine import GenerationState
    from app.services.publication import serialize_published_payload

    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    driver_payload = json.loads(
        serialize_published_payload(
            record.published,
            seed=getattr(record, "seed", None),
            prompt="p20 driver",
            model="hermes3:8b (mock)",
            title="Driver",
        )
    )

    case_id, creator = case_for(phase5_app)
    v2 = 2
    driver_payload["caseId"] = case_id
    driver_payload["caseVersion"] = v2
    driver_payload["publishedAt"] = float(phase5_app.state.clock.now())
    store = phase5_app.state.store
    now = float(phase5_app.state.clock.now())
    store.create_case_version(
        case_id=case_id, version=v2, state="PUBLISHED",
        generation_id=f"GEN-{v2}", created_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=v2,
        payload_json=json.dumps(
            driver_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )

    pt_id, pt_token = new_playthrough(phase5_app, case_id, creator, version=v2)

    # Pre-discovery: NOTHING undiscovered is exposed on the driver world.
    boot_text = json.dumps(_bootstrap(phase5_app, pt_id, pt_token), sort_keys=True)
    assert DRIVER_LAPTOP_EVIDENCE not in boot_text
    assert DRIVER_KNIFE_EVIDENCE not in boot_text

    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200, res.json()
    assert res.json()["evidenceId"] == DRIVER_LAPTOP_EVIDENCE
    assert res.json()["discovery"]["state"] == "discovered"

    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200, res.json()
    assert res.json()["evidenceId"] == DRIVER_KNIFE_EVIDENCE

    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert set(snap.discovered) == {DRIVER_LAPTOP_EVIDENCE, DRIVER_KNIFE_EVIDENCE}


def test_6c_driver_world_undiscovered_object_has_no_evidence_id_pre_reveal(phase5_app):
    """Driver-world pre-reveal addendum: every world object of a FRESH driver
    bootstrap withholds its linked evidenceId; the discovered evidenceId appears
    ONLY after the player has interacted with the object."""
    from app.generation.state_machine import GenerationState
    from app.services.publication import serialize_published_payload

    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(record.published, title="Driver2")
    )
    case_id, creator = case_for(phase5_app)
    store = phase5_app.state.store
    now = float(phase5_app.state.clock.now())
    payload["caseId"] = case_id
    payload["caseVersion"] = 2
    payload["publishedAt"] = now
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
    pt_id, pt_token = new_playthrough(phase5_app, case_id, creator, version=2)

    boot = _bootstrap(phase5_app, pt_id, pt_token)
    laptop = next(
        o for o in boot["scene"]["worldObjects"] if o["objectId"] == LAPTOP_OBJECT
    )
    knife = next(
        o for o in boot["scene"]["worldObjects"] if o["objectId"] == KNIFE_OBJECT
    )
    # PD-SEC-01: undiscovered -> NO evidenceId, BUT the interaction affordance
    # that drives the interact endpoint stays present.
    assert laptop["evidenceId"] is None
    assert laptop["interaction"] == "read"
    assert knife["evidenceId"] is None
    assert knife["interaction"] == "inspect"

    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200, res.json()

    # The evidenceId appears ONLY now (it is player-known).
    boot = _bootstrap(phase5_app, pt_id, pt_token)
    laptop = next(
        o for o in boot["scene"]["worldObjects"] if o["objectId"] == LAPTOP_OBJECT
    )
    assert laptop["evidenceId"] == DRIVER_LAPTOP_EVIDENCE
    assert laptop["discovered"] is True


def test_7_discovered_evidence_visible_afterward(phase5_app):
    """After discovery: PlayerKnowledge lists the id, the bootstrap object
    shows the evidenceId + discovered flag, and the playthrough public-case
    finally carries the player-known evidence entry."""
    case_id, creator = case_for(phase5_app)
    pt_id, pt_token = playthrough(phase5_app, case_id, creator)
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200

    snap = phase5_app.state.store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == (KNIFE_EVIDENCE,)

    boot = _bootstrap(phase5_app, pt_id, pt_token)
    knife = next(o for o in boot["scene"]["worldObjects"] if o["objectId"] == KNIFE_OBJECT)
    assert knife["evidenceId"] == KNIFE_EVIDENCE
    assert knife["discovered"] is True

    pub = _playthrough_public_case(phase5_app, pt_id, pt_token)
    assert pub["evidence"] == [e for e in pub["evidence"] if e["id"] == KNIFE_EVIDENCE]
    knife_placement = next(
        p for p in pub["worldGraph"]["placements"] if p["objectId"] == KNIFE_OBJECT
    )
    assert knife_placement["evidenceId"] == KNIFE_EVIDENCE
    # The playthrough public-case still reveals no OTHER undiscovered ids.
    for placement in pub["worldGraph"]["placements"]:
        if placement["evidenceId"] is not None:
            assert placement["evidenceId"] == KNIFE_EVIDENCE


# --------------------------------------------------------------------------- #
# 8+9+10 — reveal gating / candidates / accusation-reveal unchanged
# --------------------------------------------------------------------------- #


def test_8_reveal_only_proof_projection_remains_reveal_gated(phase5_app):
    """Pre-reveal responses NEVER carry the reveal-only proof projection
    ('explanation', 'dimensions', 'proofDimensionMap'); after the reveal the
    per-dimension board IS present (the gate exists in BOTH directions)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    pt_id = bundle["playthroughId"]
    pt_token = bundle["playthroughToken"]

    from phase6_helpers import read_record

    pre_bodies = [
        _bootstrap(phase5_app, pt_id, pt_token),
    ]
    res = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
    assert res.status_code == 200, res.json()
    pre_bodies.append(res.json())
    record = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
    assert record.status_code == 200, record.json()
    pre_bodies.append(record.json())
    with client(phase5_app) as c:
        acc = make_accusation(c, pt_id, pt_token, winning_body(bundle["truth"]))
        assert acc.status_code == 200, acc.json()
        pre_bodies.append(acc.json())
        reveal = get_reveal(c, pt_id, pt_token)
        assert reveal.status_code == 200, reveal.json()
        reveal_body = reveal.json()

    for body in pre_bodies:
        for key, _value in iter_key_paths(body):
            leaf = key.split(".")[-1]
            assert leaf != "dimensions", f"dimensions leaked pre-reveal at {key!r}"
            assert leaf != "proofDimensionMap", (
                f"proofDimensionMap leaked pre-reveal at {key!r}"
            )
            assert leaf != "explanation", f"explanation leaked pre-reveal at {key!r}"

    # The reveal DTO is the ONLY player surface with the per-dimension board.
    assert set(reveal_body["explanation"].keys()) == {"evidence", "dimensions"}
    assert set(reveal_body["explanation"]["dimensions"].keys()) == {
        "who", "why", "weapon", "when"
    }


def test_9_no_candidate_winner_leak_introduced(phase5_app):
    """The Phase 7 J/K candidates block is unchanged: alphabetical, exactly the
    frozen allowlisted keys, winning ids present UNMARKED (PD-SEC-01 must not
    introduce a candidate/winner leak)."""
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = bundle["truth"]
    boot = _bootstrap(phase5_app, bundle["playthroughId"], bundle["playthroughToken"])
    assert_candidates_unmarked(boot["candidates"], truth)
    assert_no_pre_reveal_material(
        boot, known_tokens={bundle["playthroughToken"]}, canonical_time=truth["canonical"]
    )


def test_10_accusation_reveal_unchanged(phase5_app):
    """The accusation/reveal flow is byte-for-byte unchanged: accuse+reveal on
    the golden case yields the SAME truth + 4 correct dimensions, and a fresh
    playthrough on the same case yields the SAME reveal — deterministic."""
    case_id, creator = case_for(phase5_app)
    truth = truth_bundle(phase5_app, case_id, 1)
    pt1_id, pt1_token = playthrough(phase5_app, case_id, creator)
    reveal1 = accuse_then_reveal(phase5_app, pt1_id, pt1_token, winning_body(truth))
    assert reveal1["status"] == "REVEALED"
    assert reveal1["score"]["correctDimensions"] == 4
    assert reveal1["result"]["murdererCorrect"] is True
    assert reveal1["result"]["motiveCorrect"] is True
    assert reveal1["result"]["weaponCorrect"] is True
    assert reveal1["result"]["timeCorrect"] is True
    assert_no_reveal_internal_material(reveal1, known_tokens={pt1_token})

    pt2_id, pt2_token = new_playthrough(phase5_app, case_id, creator)
    reveal2 = accuse_then_reveal(phase5_app, pt2_id, pt2_token, winning_body(truth))
    assert reveal2["truth"]["murdererId"] == reveal1["truth"]["murdererId"]
    assert reveal2["truth"]["weaponId"] == reveal1["truth"]["weaponId"]
    assert reveal2["explanation"]["dimensions"] == reveal1["explanation"]["dimensions"]


__all__ = []  # pytest module: no accidental public names