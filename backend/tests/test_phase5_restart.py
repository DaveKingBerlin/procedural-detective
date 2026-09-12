"""Phase 5 — restart durability (Phase5 H, M11/M19, INVARIANT 7).

The authoritative state lives in SQLite, never in process memory. These tests
dispose every engine, open the SAME db file with a fresh application/service,
and prove the exact data and version identities survive.

Run as: python -m pytest -q backend/tests/test_phase5_restart.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from phase5_helpers import auth, create_case, create_playthrough, create_session
from app.auth.tokens import verifier as token_verifier


def _reopen_app(database_url):
    from app.core.config import Settings
    from app.main import create_app

    return create_app(
        Settings(
            database_url=database_url,
            cors_allowed_origins=["http://localhost:5173"],
            max_generations_per_session_per_window=8,
            max_concurrent_generations=2,
        )
    )


def test_full_restart_cycle_keeps_authorized_data_and_pins(
    phase5_app, database_url
):
    """Steps 1-7 of Phase5 H on one real file."""
    # 1. create/generate/publish a case through the API.
    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        case_id, creator = case["caseId"], case["creatorAccessToken"]
        case_before = client.get(
            f"/api/v1/cases/{case_id}", headers=auth(creator)
        ).json()
        _, pt = create_playthrough(client, creator, case_id, 1)
        pt_id, pt_token = pt["playthroughId"], pt["playthroughAccessToken"]
        pt_before = client.get(f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token)).json()

    # 2. shut the application/database session down.
    phase5_app.state.engine.dispose()
    phase5_app.state.store.dispose()

    # 3. create a fresh application over the SAME file.
    restarted = _reopen_app(database_url)
    try:
        # 4. retrieve the authorized case/version — identical public payload.
        with TestClient(restarted) as client:
            case_after = client.get(
                f"/api/v1/cases/{case_id}", headers=auth(creator)
            )
            assert case_after.status_code == 200
            assert case_after.json() == case_before

            # 5/6. retrieve the pinned playthrough — identical pin.
            pt_after = client.get(f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token))
            assert pt_after.status_code == 200
            assert pt_after.json() == pt_before
            assert pt_after.json()["caseVersion"] == 1

            # Create a NEW playthrough after restart on the same version.
            _, pt2 = create_playthrough(client, creator, case_id, 1)
            assert pt2["playthroughId"] != pt_id
            assert pt2["caseVersion"] == 1

            # 7. exact data/version identity survives.
            assert restarted.state.store.get_published(case_id, 1) is not None
            sa = restarted.state.store.get_session_by_verifier(token_verifier(session_token))
            assert sa is not None
            progress = restarted.state.generation_service.get_generation_progress(
                case["generationId"], case_id=case_id
            )
            assert progress["status"] == "PUBLISHED"
            assert progress["progress"] == 100
    finally:
        restarted.state.engine.dispose()
        restarted.state.store.dispose()


def test_restart_preserves_frozen_payload_bytes(phase5_app, database_url):
    """The stored frozen payload is byte-identical across a restart."""
    from app.persistence.store import Store

    with TestClient(phase5_app) as client:
        session_token, _ = create_session(client)
        case = create_case(client, session_token)
        creator = case["creatorAccessToken"]
        res = client.get(
            f"/api/v1/cases/{case['caseId']}", headers=auth(creator)
        )
        body_before = res.json()

    phase5_app.state.engine.dispose()
    phase5_app.state.store.dispose()

    restarted = _reopen_app(database_url)
    try:
        row = restarted.state.store.get_published(case["caseId"], 1)
        assert row is not None
        payload = json.loads(row.payload_json)
        assert payload["schemaVersion"] == 1
        assert payload["caseId"] == case["caseId"]
        assert payload["caseVersion"] == 1
        # The exact public projection is reconstructable after restart.
        with TestClient(restarted) as client:
            res = client.get(
                f"/api/v1/cases/{case['caseId']}", headers=auth(creator)
            )
            assert res.json() == body_before
    finally:
        restarted.state.engine.dispose()
        restarted.state.store.dispose()


def test_restart_progress_terminal_states(phase5_app, database_url):
    """PUBLISHED and FAILED progress both survive a database restart."""
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    phase5_app.state.engine.dispose()
    phase5_app.state.store.dispose()

    from app.core.config import Settings

    settings = Settings(
        database_url=database_url,
        max_generations_per_session_per_window=8,
        max_concurrent_generations=2,
    )
    from app.generation.fake_provider import FakeProvider
    from app.generation.provider import GenerationStage

    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    session = service.create_anonymous_quota_session()
    ok = service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert ok.status == "PUBLISHED"
    # A FAILED attempt on the SAME file (fresh session, failing provider).
    service2 = GenerationService(
        settings=settings,
        store=Store(database_url),
        provider_factory=lambda: FakeProvider(
            script={
                stage: ["malformed"]
                for stage in (
                    GenerationStage.CASE_TRUTH,
                    GenerationStage.PUBLIC_WORLD,
                    GenerationStage.EVIDENCE,
                    GenerationStage.WORLD_GRAPH,
                    GenerationStage.REPAIR,
                )
            }
        ),
    )
    session2 = service2.create_anonymous_quota_session()
    failed = service2.start_case_generation(
        "A broken one",
        anonymous_quota_session_id=session2.anonymous_quota_session_id,
    )
    assert failed.status == "FAILED"
    store.dispose()

    reopened = Store(database_url)
    try:
        reopened_progress = GenerationService(
            settings=settings, store=reopened
        )
        assert reopened_progress.get_generation_progress(
            ok.generation_id, case_id=ok.case_id
        )["status"] == "PUBLISHED"
        assert reopened_progress.get_generation_progress(
            failed.generation_id, case_id=failed.case_id
        )["status"] == "FAILED"
        # The same session tokens still authorize after restart (DB-only).
        assert reopened.get_session(session.anonymous_quota_session_id) is not None
        assert reopened.get_session(session2.anonymous_quota_session_id) is not None
    finally:
        reopened.dispose()