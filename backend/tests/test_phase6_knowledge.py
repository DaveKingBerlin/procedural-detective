"""Phase 6 — PlayerKnowledge (Phase6 A, REQUIREMENTS 36/41.3; Phase6 O items
1/2/3/17 + migration/readiness/integrity).

- O1  a newly created Playthrough has empty PlayerKnowledge
- O2  PlayerKnowledge survives restart (dispose store, new engines, same file)
- O3  Playthrough A cannot access B's knowledge
- O17 restart preserves discovered/read state
- migration 0002 -> head 0003 is the empty-additive path and downgrade drops
  the player_knowledge table
- readiness is green at head 0003
- store-level integrity: mismatched (case_id, case_version) rolls back with a
  clean domain error; duplicate markers are idempotent set semantics; the FK
  + ON DELETE CASCADE is real.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.persistence.store import PlayerKnowledgeError
from conftest import upgrade_db

from phase5_helpers import (
    auth,
    create_case,
    create_playthrough,
    create_session,
)

KNIFE_EVIDENCE = "forensic_knife_match_01"
KNIFE_OBJECT = "kitchen_knife"


def _client(phase5_app):
    return TestClient(phase5_app)


def _case_for(phase5_app):
    """API drive: one anonymous session + published case + creator token."""
    with _client(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        return case["caseId"], case["creatorAccessToken"]


def _playthrough(phase5_app, case_id, creator):
    with _client(phase5_app) as client:
        status, body = create_playthrough(client, creator, case_id, 1)
        assert status == 201
        return body["playthroughId"], body["playthroughAccessToken"]


def _inv_bootstrap(phase5_app, pt_id, pt_token):
    with _client(phase5_app) as client:
        res = client.get(f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token))
    return res


def _discover_knife(phase5_app, pt_id, pt_token):
    with _client(phase5_app) as client:
        res = client.post(
            f"/api/v1/playthroughs/{pt_id}/evidence/{KNIFE_EVIDENCE}/discover",
            headers=auth(pt_token),
        )
    assert res.status_code == 200, res.text
    return res.json()


def test_1_01_new_playthrough_has_empty_player_knowledge(phase5_app):
    case_id, creator = _case_for(phase5_app)
    pt_id, pt_token = _playthrough(phase5_app, case_id, creator)
    res = _inv_bootstrap(phase5_app, pt_id, pt_token)
    assert res.status_code == 200
    body = res.json()
    assert body["playerKnowledge"] == {
        "discoveredEvidenceIds": [],
        "readEvidenceIds": [],
        "visitedLocationIds": [],
    }
    # The durable row exists (first access) with EMPTY sets.
    row = phase5_app.state.store.get_or_create_player_knowledge(
        pt_id, case_id, 1, at=float(phase5_app.state.clock.now())
    )
    assert row.discovered_json == "[]"
    assert row.read_json == "[]"
    assert row.visited_json == "[]"


def test_1_02_knowledge_survives_restart(phase5_app, database_url):
    """O2: dispose the store, open engines on the SAME file -> knowledge row,
    discovered ids and visited locations survive."""
    case_id, creator = _case_for(phase5_app)
    pt_id, pt_token = _playthrough(phase5_app, case_id, creator)
    _discover_knife(phase5_app, pt_id, pt_token)

    # -- restart: dispose every engine, re-open the same file --
    phase5_app.state.engine.dispose()
    phase5_app.state.store.dispose()

    from app.core.config import Settings
    from app.main import create_app

    restarted = create_app(Settings(database_url=database_url))
    try:
        with _client(restarted) as client:
            res = client.get(
                f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
            )
        assert res.status_code == 200
        body = res.json()
        assert body["playerKnowledge"]["discoveredEvidenceIds"] == [KNIFE_EVIDENCE]
        assert body["playerKnowledge"]["visitedLocationIds"] == [
            "miller_apartment_kitchen"
        ]
        # And the raw row survived.
        row = restarted.state.store.get_or_create_player_knowledge(pt_id, case_id, 1)
        assert KNIFE_EVIDENCE in row.discovered_json
    finally:
        restarted.state.engine.dispose()
        restarted.state.store.dispose()


def test_1_03_playthrough_a_cannot_access_b_knowledge(phase5_app):
    """O3: A's token on B's investigation / discover / read -> 404 (A cannot
    touch B's knowledge, cannot see B's bootstrap)."""
    case_id, creator = _case_for(phase5_app)
    pt_a, token_a = _playthrough(phase5_app, case_id, creator)
    pt_b, token_b = _playthrough(phase5_app, case_id, creator)
    # B discovers the knife first so B HAS knowledge A must not see.
    _discover_knife(phase5_app, pt_b, token_b)

    with _client(phase5_app) as client:
        # A's token on B's investigation -> 404.
        res = client.get(
            f"/api/v1/playthroughs/{pt_b}/investigation", headers=auth(token_a)
        )
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "NOT_FOUND"
        # A's token discovering on B -> 404.
        res = client.post(
            f"/api/v1/playthroughs/{pt_b}/evidence/{KNIFE_EVIDENCE}/discover",
            headers=auth(token_a),
        )
        assert res.status_code == 404
        # A's token reading B's record -> 404.
        res = client.get(
            f"/api/v1/playthroughs/{pt_b}/records/{KNIFE_EVIDENCE}",
            headers=auth(token_a),
        )
        assert res.status_code == 404
    # B's knowledge is untouched and A never gained anything.
    snapshot_b = phase5_app.state.store.snapshot_player_knowledge(pt_b)
    snapshot_a = phase5_app.state.store.snapshot_player_knowledge(pt_a)
    assert snapshot_b.discovered == (KNIFE_EVIDENCE,)
    assert snapshot_a.discovered == ()


def test_1_17_restart_preserves_discovered_and_read_state(phase5_app, database_url):
    """O17: discover + read -> restart -> the same record reads identically
    (discovered + read state persisted)."""
    case_id, creator = _case_for(phase5_app)
    pt_id, pt_token = _playthrough(phase5_app, case_id, creator)
    _discover_knife(phase5_app, pt_id, pt_token)
    with _client(phase5_app) as client:
        res = client.get(
            f"/api/v1/playthroughs/{pt_id}/records/{KNIFE_EVIDENCE}",
            headers=auth(pt_token),
        )
        assert res.status_code == 200
        before = res.json()

    phase5_app.state.engine.dispose()
    phase5_app.state.store.dispose()

    from app.core.config import Settings
    from app.main import create_app

    restarted = create_app(Settings(database_url=database_url))
    try:
        with _client(restarted) as client:
            res = client.get(
                f"/api/v1/playthroughs/{pt_id}/records/{KNIFE_EVIDENCE}",
                headers=auth(pt_token),
            )
            assert res.status_code == 200
            after = res.json()
            # discovered AND read survive restart, DTO is byte-identical.
            assert after == before
            assert after["readByPlayer"] is True
            snap = restarted.state.store.snapshot_player_knowledge(pt_id)
            assert snap.read == (KNIFE_EVIDENCE,)
            assert snap.discovered == (KNIFE_EVIDENCE,)
    finally:
        restarted.state.engine.dispose()
        restarted.state.store.dispose()


# --------------------------------------------------------------------------- #
# migration 0002 -> 0003 + readiness
# --------------------------------------------------------------------------- #


def test_migration_0002_to_0003_empty_path(database_url):
    """The 0002 -> head (0003) path is the additive player_knowledge table."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from conftest import make_alembic_config

    command.upgrade(make_alembic_config(database_url), "0002")
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    try:
        with engine.connect() as conn:
            versions = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
        assert versions == ["0002"]
        assert "player_knowledge" not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    upgrade_db(database_url)  # -> head 0003
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    try:
        with engine.connect() as conn:
            versions = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
        assert versions == ["0003"]
        assert "player_knowledge" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    # Downgrade to 0002 specifically: player_knowledge is dropped, the schema
    # returns to the Phase 5 shape.
    command.downgrade(make_alembic_config(database_url), "0002")
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    try:
        with engine.connect() as conn:
            versions = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
        assert versions == ["0002"]
        assert "player_knowledge" not in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_readiness_green_at_head_0003(migrated_client):
    res = migrated_client.get("/api/v1/readiness")
    assert res.status_code == 200
    assert res.json() == {"status": "ready", "database": "ok", "migrations": "ok"}


# --------------------------------------------------------------------------- #
# store-level integrity / idempotency
# --------------------------------------------------------------------------- #


def test_knowledge_row_requires_matching_pinned_tuple(phase5_app):
    """A knowledge row can only be created for the playthrough's OWN
    (case_id, case_version) — any mismatch rolls back with a clean error and
    creates nothing."""
    case_id, creator = _case_for(phase5_app)
    pt_id, _ = _playthrough(phase5_app, case_id, creator)
    store = phase5_app.state.store
    with pytest.raises(PlayerKnowledgeError):
        store.get_or_create_player_knowledge(
            pt_id, "SOME-OTHER-CASE", 99, at=1000.0
        )
    # No stray row was created.
    assert store.snapshot_player_knowledge(pt_id).discovered == ()
    with store._read_session() as session:
        from app.models.knowledge import PlayerKnowledge

        found = session.get(PlayerKnowledge, pt_id)
        assert found is None or found.case_id == case_id


def test_knowledge_row_unknown_playthrough_clean_error(phase5_app):
    store = phase5_app.state.store
    with pytest.raises(PlayerKnowledgeError):
        store.get_or_create_player_knowledge(
            "PT-NOT-EXISTING", "CASE-ANY", 1, at=1000.0
        )


def test_duplicate_markers_are_idempotent_set_semantics(phase5_app):
    case_id, creator = _case_for(phase5_app)
    pt_id, _ = _playthrough(phase5_app, case_id, creator)
    store = phase5_app.state.store
    store.get_or_create_player_knowledge(pt_id, case_id, 1, at=1000.0)
    store.mark_discovered(pt_id, KNIFE_EVIDENCE, "miller_apartment_kitchen", at=1001.0)
    store.mark_discovered(pt_id, KNIFE_EVIDENCE, "miller_apartment_kitchen", at=1002.0)
    snap = store.snapshot_player_knowledge(pt_id)
    assert snap.discovered == (KNIFE_EVIDENCE,)
    assert snap.visited == ("miller_apartment_kitchen",)
    store.mark_read(pt_id, KNIFE_EVIDENCE, at=1003.0, opened_at=1003.0)
    store.mark_read(pt_id, KNIFE_EVIDENCE, at=1004.0, opened_at=1004.0)
    snap = store.snapshot_player_knowledge(pt_id)
    assert snap.read == (KNIFE_EVIDENCE,)
    # openedAt of the FIRST read is stable.
    assert snap.opened_at.get(KNIFE_EVIDENCE) == 1003.0


def test_player_knowledge_fk_cascade_on_playthrough_delete(phase5_app):
    """The FK ON DELETE CASCADE is real: deleting the playthrough removes its
    knowledge row in one database-level operation."""
    case_id, creator = _case_for(phase5_app)
    pt_id, _ = _playthrough(phase5_app, case_id, creator)
    store = phase5_app.state.store
    store.get_or_create_player_knowledge(pt_id, case_id, 1, at=1000.0)
    store.mark_discovered(pt_id, KNIFE_EVIDENCE, None, at=1001.0)
    assert store.snapshot_player_knowledge(pt_id).discovered == (KNIFE_EVIDENCE,)

    with store.transaction() as session:
        session.execute(
            text("DELETE FROM playthroughs WHERE playthrough_id = :pid"),
            {"pid": pt_id},
        )
    assert store.snapshot_player_knowledge(pt_id).discovered == ()
    with store._read_session() as session:
        from app.models.knowledge import PlayerKnowledge

        assert session.get(PlayerKnowledge, pt_id) is None