"""Phase 22 — BYO-Ollama provider budgets: a bridge call == a regular call.

(Phase22 §16/§43 — provider budgets unchanged; no hidden bridge probe may
consume generation budget.)

Proves, with the REAL driver over a scripted bridge connection:

  1. the bridge path consumes the SAME BudgetTracker hierarchy as the local
     Ollama equivalent: the standard ice-pick world runs 4 provider calls
     (globalCallCount=4, core=3, asset=1, proceduralAssets=1, failed=0) and a
     catalog-only world runs exactly 3 (core=3, asset=0);
  2. the bridge's job counter equals the provider-call counter (a bridge job
     IS one provider call);
  3. capability / health / status probes are served by separate bounded
     infrastructure and NEVER consume a generation provider call;
  4. the full API generation through the bridge reaches the SAME
     providerCallCount the local ollama run reaches.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import upgrade_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.persistence.store import Store  # noqa: E402
from app.services.generation import GenerationService  # noqa: E402

from bridge_harness import (  # noqa: E402
    LiveTestServer,
    TestBridge,
    make_bridge_settings,
    make_scripted_bridge,
    new_anonymous_session,
    create_pairing,
)

_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa Koenig\n"
    "Location: office\n"
)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("bridge_budget")
    url = f"sqlite:///{(db_dir / 'b.db').as_posix()}"
    upgrade_db(url)
    settings = make_bridge_settings(url)
    app = create_app(settings)
    server = LiveTestServer(app)
    yield {"server": server, "url": url, "base_url": server.base_url}
    server.close()
    app.state.engine.dispose()
    app.state.store.dispose()


def _scripts(unknown=True):
    """The SAME canned stage data as the ollama driver suite (procedural world
    card => outputs consumed per dispatched job)."""
    from test_ollama_driver import ICEPICK_SPEC, _case_people, _evidence, _world
    from test_ollama_driver import _known_world

    return {
        "procedural": [
            _case_people(),
            _evidence(),
            _world(),
            __import__("json").loads(ICEPICK_SPEC),
        ],
        "catalog": [
            _case_people(weapon="kitchen_knife"),
            _evidence(weapon_obj="kitchen_knife"),
            _known_world(),
        ],
    }


def _service_run(database_url, *, cassette, prompt=_PROMPT):
    """One GenerationService run over a scripted bridge; returns the attempt
    record (budget) + the scripted socket (job count)."""
    from app.services.bridge import BridgeRegistry

    store = Store(database_url)
    settings = make_bridge_settings(database_url)
    registry = BridgeRegistry(settings=settings, store=store)
    service = GenerationService(settings=settings, store=store, bridge_registry=registry)
    session = service.create_anonymous_quota_session()
    conn, sock, loop_thread = make_scripted_bridge(
        registry,
        session_scope=session.anonymous_quota_session_id,
        model="hermes3:8b",
        outputs=cassette,
        settings=settings,
    )
    try:
        handle, record, _now = service._run_generation(
            prompt,
            anonymous_quota_session_id=session.anonymous_quota_session_id,
            creator_token=None,
        )
        return record, sock, service
    finally:
        loop_thread.close()
        store.dispose()


def test_bridge_procedural_world_budget_equals_local_equivalent(database_url):
    """Ice-pick procedural world via the bridge: 4 provider calls consumed by
    the SAME hierarchical BudgetTracker buckets as the local Ollama run."""
    upgrade_db(database_url)
    record, sock, _service = _service_run(
        database_url, cassette=_scripts(unknown=True)["procedural"]
    )
    from app.generation.state_machine import GenerationState

    assert record.state is GenerationState.PUBLISHED
    budget = record.budget
    assert budget.calls == 4
    assert budget.core_calls == 3
    assert budget.asset_calls == 1
    assert budget.procedural_asset_count == 1
    assert budget.failed_asset_count == 0
    # ONE bridge job == ONE provider call (no extra bridge-side probing).
    job_frames = [
        __import__("json").loads(frame)
        for frame in sock.sent_frames
        if __import__("json").loads(frame).get("type") == "job"
    ]
    assert len(job_frames) == 4
    assert budget.calls == len(job_frames)


def test_bridge_catalog_world_budget_three_calls(database_url):
    """A catalog-resolved world runs exactly THREE provider calls (no hidden
    asset call; capability/health probes are separate infrastructure)."""
    upgrade_db(database_url)
    catalog_prompt = (
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
        "Weapon: kitchen knife\nTime: 23:42\nWitness: Lisa Koenig\nLocation: office\n"
    )
    record, sock, _service = _service_run(
        database_url,
        cassette=_scripts(unknown=True)["catalog"],
        prompt=catalog_prompt,
    )
    assert record.state.value == "PUBLISHED"
    assert record.budget.calls == 3
    assert record.budget.core_calls == 3
    assert record.budget.asset_calls == 0
    assert record.budget.procedural_asset_count == 0

def test_budget_snapshot_bridge_matches_ollama_driver(database_url):
    """Literal snapshot parity with the local equivalent: the ollama-driver
    harness run (same canned data) and the bridge run produce IDENTICAL budget
    snapshots (global/core/asset/procedural/failed counters)."""
    upgrade_db(database_url)
    record, _sock, _service = _service_run(
        database_url, cassette=_scripts(unknown=True)["procedural"]
    )
    bridge_snapshot = record.budget.snapshot()

    from test_ollama_driver import _run, _staged

    ollama_record, _transport = _run(
        _staged(),
        max_llm_calls_per_generation=16,
        max_core_llm_calls=12,
        max_llm_calls_per_procedural_asset=5,
        max_procedural_assets_per_generation=20,
        max_failed_assets_per_generation=3,
        deadline_seconds=60,
    )
    ollama_snapshot = ollama_record.budget.snapshot()

    assert bridge_snapshot == ollama_snapshot
    assert bridge_snapshot["globalCallCount"] == 4
    assert bridge_snapshot["coreCallCount"] == 3
    assert bridge_snapshot["assetCallCount"] == 1


def test_probes_never_consume_generation_budget(stack):
    """Capability/health/status probes use separate bounded infrastructure:
    with a bridge CONNECTED they dispatch ZERO jobs and consume ZERO provider
    budget (the only job producer is an actual /cases generation)."""
    base = stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(stack["server"].ws_url)
    bridge.connect_pairing(pairing["pairingCode"], model="hermes3:8b")
    # Probe the public + private surfaces repeatedly.
    for _ in range(4):
        caps = httpx.get(
            f"{base}/api/v1/generation-capabilities",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        health = httpx.get(f"{base}/api/v1/health", timeout=30)
        status = httpx.get(
            f"{base}/api/v1/bridge/status",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        assert caps.status_code == 200
        assert health.status_code == 200
        assert status.status_code == 200
    assert bridge.job_count == 0
    assert bridge.cancels == []
    bridge.close()


def test_api_generation_provider_call_count_matches_local(stack):
    """The full API integration (session -> pairing -> bridge -> MiniOllama ->
    POST /cases) reaches the SAME provider-call count as the local ollama
    equivalent (4), and probes afterwards consume nothing."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    response = httpx.post(
        f"{stack['base_url']}/api/v1/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": _PROMPT},
        timeout=120,
    )
    body = response.json()
    assert response.status_code == 201, body
    assert body["status"] == "PUBLISHED"
    assert len(bridge.jobs) == 4

    # Capability / health probes right after the generation consume NOTHING.
    probe = httpx.get(
        f"{stack['base_url']}/api/v1/generation-capabilities",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert probe.status_code == 200
    assert bridge.job_count == 4  # probes never dispatched a job
    bridge.close()


def _pair_and_connect(stack, *, model="hermes3:8b", session_token=None):
    base = stack["base_url"]
    token = session_token if session_token is not None else new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=model)
    return token, pairing, bridge, ack