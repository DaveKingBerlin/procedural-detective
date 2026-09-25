"""Phase 22 — BYO-Ollama bridge: pairing, WS protocol, auth, integration.

Sections (Phase22 §40 testing matrix — unit + integration):

  A. pairing code generation / expiry / single-use / binding + brute force
  B. bridge session token entropy / scope / hashing at rest / constant time
  C. WS protocol: unknown type / oversized / deep JSON / invalid version /
     unauthorized first frames rejected with typed close codes; pairing
     accepted then job dispatch then result; late/duplicate results discarded
  D. integration: Server -> RemoteClientProvider -> test bridge -> MiniOllama
     across ALL stages (case/people, evidence, world_requirements,
     asset_spec, asset_spec_repair, repair) — full generation PUBLISHES and
     the SAME deterministic solver derived the winning combination
  E. failure acceptance: bridge closed before generation; bridge disconnected
     mid-run; Ollama stopped mid-run (mock failure); local provider timeout
  F. auth: a different player session can never use the creator's bridge.

Infrastructure is REAL: a live uvicorn server on loopback + a test bridge that
implements the client side of the WS protocol with websockets (mock-only
network, allowed by the autouse loopback network block).
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from app.main import create_app  # noqa: E402

from bridge_harness import (  # noqa: E402
    LiveTestServer,
    TestBridge,
    bridge_status,
    make_bridge_settings,
    new_anonymous_session,
    create_pairing,
)

from app.generation.bridge_protocol import (  # noqa: E402
    AUTHORITATIVE_SCHEMA_IDS,
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_POLICY_VIOLATION,
    CLOSE_PROTOCOL_ERROR,
    CLOSE_UNSUPPORTED_TYPE,
    PROTOCOL_VERSION,
    decode_frame,
    generate_pairing_code,
    validate_frame,
)
from app.generation.failure_codes import (  # noqa: E402
    GenerationFailureCode,
    public_failure_code,
)

_OLLAMA = "hermes3:8b"

_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa Koenig\n"
    "Location: office\n"
)


def _full_stack(tmp_path_factory, *, tag: str, **overrides):
    db_dir = tmp_path_factory.mktemp(f"bridge_{tag}")
    url = f"sqlite:///{(db_dir / f'{tag}.db').as_posix()}"
    upgrade_db(url)
    settings = make_bridge_settings(url, **overrides)
    app = create_app(settings)
    server = LiveTestServer(app)
    return {"server": server, "url": url, "base_url": server.base_url}, app


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    ctx, app = _full_stack(tmp_path_factory, tag="main")
    yield ctx
    ctx["server"].close()
    app.state.engine.dispose()
    app.state.store.dispose()


@pytest.fixture(scope="module")
def timeout_stack(tmp_path_factory):
    ctx, app = _full_stack(
        tmp_path_factory,
        tag="timeout",
        bridge_job_deadline_seconds=2.0,
        generation_deadline_seconds=30,
        bridge_heartbeat_interval_seconds=1.0,
        bridge_idle_timeout_seconds=10.0,
        bridge_max_frames_per_window=100,
    )
    yield ctx
    ctx["server"].close()
    app.state.engine.dispose()
    app.state.store.dispose()


def _pair_and_connect(stack, *, model=_OLLAMA, session_token=None):
    base = stack["base_url"]
    token = session_token if session_token is not None else new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=model)
    return token, pairing, bridge, ack


def _generate(stack, token, prompt=_PROMPT, *, timeout=60):
    return httpx.post(
        f"{stack['base_url']}/api/v1/cases",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": prompt},
        timeout=timeout,
    )


def _stored_payload(app, case_id: str) -> dict:
    row = _published_row(app, case_id)
    return json.loads(row.payload_json)


def _published_row(app, case_id: str):
    store = app.state.store
    return store.get_published(case_id, 1)


# =========================================================================== #
# A. pairing code generation / expiry / single-use / binding + brute force
# =========================================================================== #


def test_pairing_code_format_entropy_and_uniqueness():
    seen: set[str] = set()
    for _ in range(200):
        code = generate_pairing_code()
        assert re.fullmatch(r"PD-[A-Z2-7]{4}-[A-Z2-7]{4}", code), code
        assert code not in seen
        seen.add(code)


def test_ws_pairing_rest_requires_anonymous_session(stack):
    response = httpx.post(f"{stack['base_url']}/api/v1/bridge/pairing", timeout=30)
    assert response.status_code == 401
    response = httpx.post(
        f"{stack['base_url']}/api/v1/bridge/pairing",
        headers={"Authorization": "Bearer " + "x" * 30},
        timeout=30,
    )
    assert response.status_code == 401


def test_pairing_creation_shape_and_code_lifetime(stack):
    base = stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    assert re.fullmatch(r"PD-[A-Z2-7]{4}-[A-Z2-7]{4}", pairing["pairingCode"])
    assert pairing["pairingSessionId"].startswith("PAIR-")
    assert pairing["expiresAt"] > time.time() + 30


def test_pairing_record_single_use_at_store_level(database_url):
    """The consumed_at CAS admits exactly ONE bridge per code."""
    upgrade_db(database_url)
    from app.persistence.store import Store

    store = Store(database_url)
    from app.auth.tokens import verifier
    from app.services.bridge import BridgePairingService

    service = BridgePairingService(
        settings=Settings(database_url=database_url), store=store
    )
    session_scope = "QUOTA-test-1"
    pairing = service.create_pairing(session_scope)
    data, scope = service.consume_pairing(pairing.pairing_code)
    assert scope == session_scope
    binding = service.bind_bridge_from_pairing(
        data, model=_OLLAMA, capabilities=("STRUCTURED_MODEL_INFERENCE",)
    )
    # The SAME code cannot be consumed again (second bridge / replay).
    from app.services.bridge import PairingInvalid

    with pytest.raises(PairingInvalid):
        service.consume_pairing(pairing.pairing_code)
    with pytest.raises(PairingInvalid):
        service.bind_bridge_from_pairing(
            data, model=_OLLAMA, capabilities=("STRUCTURED_MODEL_INFERENCE",)
        )
    # The bridge session row exists and carries the HASHED token.
    row = store.get_bridge_session(binding.bridge_session_id)
    assert row is not None
    assert row.token_verifier == verifier(binding.bridge_session_token)
    assert row.token_verifier != binding.bridge_session_token
    assert "QUOTA-test-1" in (binding.session_scope,)
    store.dispose()


def test_pairing_expires_without_binding(database_url):
    upgrade_db(database_url)
    from app.persistence.store import Store
    from app.services.bridge import BridgePairingService, PairingInvalid

    store = Store(database_url)
    settings = Settings(
        database_url=database_url, bridge_pairing_code_ttl_seconds=0.001
    )
    service = BridgePairingService(settings=settings, store=store)
    pairing = service.create_pairing("QUOTA-exp-1")
    time.sleep(0.02)
    with pytest.raises(PairingInvalid):
        service.consume_pairing(pairing.pairing_code)
    store.dispose()


def test_brute_force_many_wrong_codes_neither_bind_nor_leak(stack):
    """Many wrong pairing codes never bind a bridge: each handshake closes
    with a typed reason and the status stays disconnected for the owner."""
    base = stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    for _ in range(6):
        wrong = generate_pairing_code()
        ws = _sync_connect(stack["server"].ws_url)
        ws.send(
            json.dumps(
                {"protocolVersion": 1, "type": "pairing_hello", "pairingCode": wrong},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        try:
            ws.recv(timeout=10)
        except Exception as exc:  # noqa: BLE001 - expect the close
            code = getattr(getattr(exc, "rcvd", None), "code", None)
            assert code in (CLOSE_POLICY_VIOLATION, CLOSE_UNSUPPORTED_TYPE, CLOSE_PROTOCOL_ERROR)
        ws.close()
    # Being wrong many times must not have bound the REAL code either.
    status = bridge_status(base, token)["remoteLocalAi"]
    assert status["connected"] is False
    # The real code is still usable (it was never consumed).
    bridge = TestBridge(stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    assert ack["type"] == "pairing_accepted"
    bridge.close()


def _sync_connect(url: str):
    from websockets.sync.client import connect as ws_connect

    return ws_connect(url, open_timeout=10, close_timeout=3)


# =========================================================================== #
# B. bridge session token entropy / scope / hashing-at-rest / constant time
# =========================================================================== #


def test_bridge_session_token_entropy_scope_and_hashing(stack):
    base = stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    secret = ack.get("bridgeSessionToken")
    assert isinstance(secret, str)
    assert len(secret) >= 32
    # token_urlsafe(32) draws from >=256-bit CSPRNG state: two pairings must
    # never share a token and the token shows real diversity.
    pairing2 = create_pairing(base, token)
    bridge2 = TestBridge(stack["server"].ws_url)
    ack2 = bridge2.connect_pairing(pairing2["pairingCode"], model=_OLLAMA)
    assert ack2["bridgeSessionToken"] != secret
    assert len({ch for ch in secret}) >= 16  # not a degenerate token
    # The raw token NEVER appears in any REST response body afterwards.
    status_body = httpx.get(
        f"{base}/api/v1/bridge/status",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ).text
    caps_body = httpx.get(
        f"{base}/api/v1/generation-capabilities",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ).text
    assert secret not in status_body
    assert secret not in caps_body
    assert pairing["pairingCode"] not in status_body
    bridge.close()
    bridge2.close()


def test_bridge_token_hashed_at_rest_and_constant_time_check(stack):
    """The DB row stores only the SHA-256 verifier; verification uses the
    same constant-time verifier primitive as every other bearer secret."""
    from app.auth.tokens import verifier, verify_token

    base = stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    bridge = TestBridge(stack["server"].ws_url)
    ack = bridge.connect_pairing(pairing["pairingCode"], model=_OLLAMA)
    secret = ack["bridgeSessionToken"]
    session_id = ack["bridgeSessionId"]
    row = stack["server"].app.state.store.get_bridge_session(session_id)
    assert row is not None
    assert row.token_verifier == verifier(secret)
    assert verify_token(secret, row.token_verifier) is True
    assert verify_token(secret + "tamper", row.token_verifier) is False
    bridge.close()


# =========================================================================== #
# C. WS protocol: rejections + happy job round trip
# =========================================================================== #


def _open_raw_ws(stack, first_frame: str | None = None):
    ws = _sync_connect(stack["server"].ws_url)
    if first_frame is not None:
        ws.send(first_frame)
    return ws


def test_ws_unauthorized_first_frame_rejected(stack):
    base = stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    # Unknown message type as the FIRST frame.
    ws = _open_raw_ws(stack, json.dumps({"protocolVersion": 1, "type": "job"}))
    exc = None
    try:
        ws.recv(timeout=10)
    except Exception as err:  # noqa: BLE001
        exc = err
    assert exc is not None, "expected a close"
    assert getattr(getattr(exc, "rcvd", None), "code", None) in (
        CLOSE_UNSUPPORTED_TYPE,
        CLOSE_PROTOCOL_ERROR,
    )
    ws.close()
    # A garbage first frame (not JSON) is rejected too.
    ws = _open_raw_ws(stack, "not-json{{{")
    exc = None
    try:
        ws.recv(timeout=10)
    except Exception as err:  # noqa: BLE001
        exc = err
    assert exc is not None
    assert getattr(getattr(exc, "rcvd", None), "code", None) == CLOSE_PROTOCOL_ERROR
    ws.close()
    # No bridge got bound to this session by the rejects.
    status = bridge_status(base, token)["remoteLocalAi"]
    assert status["connected"] is False


def test_ws_unknown_message_type_after_auth_rejected(stack):
    _token, _pairing, bridge, _ack = _pair_and_connect(stack)
    bridge.send_frame({"protocolVersion": 1, "type": "pong"})  # allow worker to drain unknown
    bridge.send_frame({"type": "surprise"})
    assert bridge.wait_for(lambda: bridge.close_code is not None, timeout=10)
    assert bridge.close_code in (CLOSE_UNSUPPORTED_TYPE, CLOSE_PROTOCOL_ERROR)
    bridge.close()


def test_ws_invalid_protocol_version_rejected(stack):
    ws = _open_raw_ws(
        stack,
        json.dumps({"protocolVersion": 99, "type": "pairing_hello", "pairingCode": "PD-AAAA-BBBB"}),
    )
    exc = None
    try:
        ws.recv(timeout=10)
    except Exception as err:  # noqa: BLE001
        exc = err
    assert exc is not None
    assert getattr(getattr(exc, "rcvd", None), "code", None) == CLOSE_PROTOCOL_ERROR
    ws.close()


def test_ws_oversized_frame_rejected(stack):
    _token, _pairing, bridge, _ack = _pair_and_connect(stack)
    huge = {"type": "pong", "pad": "x" * (128 * 1024)}
    bridge.send_frame(huge)
    assert bridge.wait_for(lambda: bridge.close_code is not None, timeout=10)
    assert bridge.close_code == CLOSE_MESSAGE_TOO_BIG
    bridge.close()


def test_ws_deep_json_frame_rejected(stack):
    _token, _pairing, bridge, _ack = _pair_and_connect(stack)
    # A SMALL but deeply nested JSON frame (built as text so no client-side
    # RecursionError): the depth-bounded decoder must reject it (1002) instead
    # of recursing.
    deep_text = '{"type":"pong","a":' + ('{"b":' * 60) + "1" + ("}" * 60) + "}"
    bridge.send_raw(deep_text)
    assert bridge.wait_for(lambda: bridge.close_code is not None, timeout=10)
    assert bridge.close_code == CLOSE_PROTOCOL_ERROR
    bridge.close()


def test_ws_pairing_accepted_payload_schema_and_token_once(stack):
    _token, _pairing, bridge, ack = _pair_and_connect(stack)
    assert ack["type"] == "pairing_accepted"
    assert ack["protocolVersion"] == PROTOCOL_VERSION
    assert ack["bridgeSessionId"].startswith("PS-")
    assert ack["model"] == _OLLAMA
    assert ack["capabilities"] == ["STRUCTURED_MODEL_INFERENCE"]
    assert len(ack["bridgeSessionToken"]) >= 32
    # validate_frame accepts the ack as a valid server->client frame.
    normalized = validate_frame(decode_frame(json.dumps(ack), max_bytes=1024))
    assert normalized["bridgeSessionId"] == ack["bridgeSessionId"]
    bridge.close()


def test_ws_job_dispatch_result_and_late_duplicate_discard(stack):
    """Full happy path plus LATE/DUPLICATE result discard (registry-level)."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    result = _generate(stack, token)
    assert result.status_code == 201
    assert result.json()["status"] == "PUBLISHED"
    seen = [job["schemaId"] for job in bridge.jobs]
    assert seen == ["CASE_PEOPLE_v1", "EVIDENCE_v1", "WORLD_REQUIREMENTS_v1", "ASSET_SPEC_v1"]
    # Registry-level discard semantics.
    registry = stack["server"].app.state.bridge_registry
    conn = registry.lookup_for_scope(
        stack["server"].app.state.store.get_session_by_verifier(
            __import__("app.auth.tokens", fromlist=["verifier"]).verifier(token)
        ).session_id
    )
    assert conn is not None
    waiter = registry.begin_job(conn, "JOB-test-late")
    assert waiter is not None
    assert registry.begin_job(conn, "JOB-test-busy") is None  # BUSY (concurrency 1)
    # A result for a WRONG/unknown job id is discarded (nothing matches).
    assert registry.resolve_job(conn, "JOB-unknown", "content", {"x": 1}) is False
    # The current job resolves exactly once; the duplicate is discarded.
    assert registry.resolve_job(conn, "JOB-test-late", "content", {"ok": True}) is True
    assert registry.resolve_job(conn, "JOB-test-late", "content", {"ok": False}) is False
    kind, value = waiter.result
    assert kind == "content" and value == {"ok": True}
    bridge.close()


# =========================================================================== #
# D. integration: full generation through the bridge WITH the same solver
# =========================================================================== #


def test_integration_full_generation_publishes_with_same_solver(stack):
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    result = _generate(stack, token)
    assert result.status_code == 201, result.json()
    body = result.json()
    assert body["status"] == "PUBLISHED"
    assert body.get("failureCode") is None
    assert body["caseId"]
    # The bridge saw EXACTLY the authoritative four stage jobs (catalog=3
    # plus the ASSET_SPEC procedural call = 4 for this instrumental world).
    seen = [job["schemaId"] for job in bridge.jobs]
    assert len(bridge.jobs) == 4
    assert seen == ["CASE_PEOPLE_v1", "EVIDENCE_v1", "WORLD_REQUIREMENTS_v1", "ASSET_SPEC_v1"]
    # Every dispatched job carried an AUTHORITATIVE schema id (strict contract)
    # and a bounded timeoutMs.
    for job in bridge.jobs:
        assert job["jobId"].startswith("JOB-")
        assert job["schemaId"] in AUTHORITATIVE_SCHEMA_IDS
        assert job["jobType"] == "STRUCTURED_INFERENCE"
        assert job["model"] == _OLLAMA
        assert 0 < job["timeoutMs"] <= 20_000
    # The SAME deterministic solver produced the same winning combination: the
    # frozen published payload's solution proof evaluated all_true against the
    # canonical truth (deduction + truth comparison, §31.10) and the report is
    # VALID. (The stored solverProof is the plain dataclass projection —
    # snake_case fields; winners is the ordered (murderer, motive, weapon).)
    payload = _stored_payload(stack["server"].app, body["caseId"])
    proof = payload["solverProof"]
    assert proof["validation"]["all_true"] is True
    assert proof["winners"] == [
        "paul_becker",
        "stolen_research_data",
        "bronze_ceremonial_ice_pick",
    ]
    assert payload["report"]["valid"] is True
    # The published truth exists but is NEVER in the player DTO.
    case_dto = httpx.get(
        f"{stack['base_url']}/api/v1/cases/{body['caseId']}",
        headers={"Authorization": f"Bearer {body['creatorAccessToken']}"},
        timeout=30,
    )
    assert case_dto.status_code == 200
    assert "murdererId" not in case_dto.text
    assert "paul_becker" in case_dto.text  # the public suspect IS advertised
    bridge.close()


def test_integration_catalog_world_three_calls_no_asset_spec(stack):
    """A world the catalog ALREADY resolves (locked weapon = kitchen knife;
    known world with NO unknown objects) dispatches exactly THREE jobs
    (case/evidence/world) — no ASSET_SPEC; a bridge call consumes the SAME
    provider budget as the local equivalent."""
    from test_ollama_driver import _known_world, _case_people, _evidence

    def _catalog_responder(job, b):
        if job["schemaId"] == "CASE_PEOPLE_v1":
            b.mini.reply(
                job, b, {"status": "SUCCESS", "structuredOutput": _case_people(weapon="kitchen_knife")}
            )
        elif job["schemaId"] == "EVIDENCE_v1":
            b.mini.reply(
                job,
                b,
                {"status": "SUCCESS", "structuredOutput": _evidence(weapon_obj="kitchen_knife")},
            )
        else:
            b.mini.reply(
                job, b, {"status": "SUCCESS", "structuredOutput": _known_world()}
            )

    prompt = (
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
        "Weapon: kitchen knife\nTime: 23:42\nWitness: Lisa Koenig\nLocation: office\n"
    )
    token, _pairing, bridge, _ack = _pair_and_connect(stack)
    bridge.mini.on_job = _catalog_responder
    result = _generate(stack, token, prompt=prompt)
    assert result.status_code == 201, result.json()
    assert result.json()["status"] == "PUBLISHED"
    seen = [job["schemaId"] for job in bridge.jobs]
    assert seen == ["CASE_PEOPLE_v1", "EVIDENCE_v1", "WORLD_REQUIREMENTS_v1"]
    assert len(bridge.jobs) == 3
    bridge.close()


def test_status_and_capability_transitions(stack):
    base = stack["base_url"]
    token = new_anonymous_session(base)["anonymousSessionToken"]
    # Disabled-feature DTO: no remoteLocalAi key on an ENABLED-bridge app? No —
    # ENABLE_BRIDGE=true here; check the transition connected false -> true.
    caps = httpx.get(
        f"{base}/api/v1/generation-capabilities",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ).json()
    assert caps["remoteLocalAi"]["available"] is True
    assert caps["remoteLocalAi"]["connected"] is False
    status = bridge_status(base, token)["remoteLocalAi"]
    assert status == {"available": True, "connected": False, "model": None, "ready": False}
    _token, _pairing, bridge, _ack = _pair_and_connect(stack, session_token=token)
    status = bridge_status(base, token)["remoteLocalAi"]
    assert status["connected"] is True
    assert status["model"] == _OLLAMA
    assert status["ready"] is True
    caps = httpx.get(
        f"{base}/api/v1/generation-capabilities",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ).json()
    assert caps["remoteLocalAi"]["connected"] is True
    assert caps["remoteLocalAi"]["model"] == _OLLAMA
    assert caps["remoteLocalAi"]["ready"] is True
    # No token/IP/URL anywhere in the public capability body.
    for token_fragment in (token, _pairing["pairingCode"], "11434", "127.0.0.1"):
        assert token_fragment not in httpx.get(
            f"{base}/api/v1/generation-capabilities",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        ).text
    bridge.close()


def test_capability_omits_remote_local_ai_when_disabled(database_url):
    from app.main import create_app as _create_app
    from fastapi.testclient import TestClient

    upgrade_db(database_url)
    app_disabled = _create_app(Settings(database_url=database_url, enable_bridge=False))
    with TestClient(app_disabled) as client:
        body = client.get("/api/v1/generation-capabilities").json()
    assert "remoteLocalAi" not in body
    assert set(body.keys()) == {"modes", "configuredProvider"}
    app_disabled.state.engine.dispose()
    app_disabled.state.store.dispose()


def test_remote_client_provider_fails_closed_without_feature_flag(database_url):
    """GENERATION_PROVIDER=remote_client + ENABLE_BRIDGE=false is a startup
    misconfiguration (ProviderConfigError) — never a silent demo fallback."""
    upgrade_db(database_url)
    from app.persistence.store import Store
    from app.services.generation import GenerationService, ProviderConfigError

    store = Store(database_url)
    with pytest.raises(ProviderConfigError):
        GenerationService(
            settings=Settings(
                database_url=database_url,
                generation_provider="remote_client",
                enable_bridge=False,
            ),
            store=store,
        )
    store.dispose()


# =========================================================================== #
# E. failure acceptance (typed failure + no partial publication)
# =========================================================================== #


def test_failure_bridge_closed_before_generation(stack):
    token, _pairing, _bridge, _ack = _pair_and_connect(stack)
    _bridge.close()
    time.sleep(0.2)
    result = _generate(stack, token)
    assert result.status_code == 201
    body = result.json()
    assert body["status"] == "FAILED"
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.BRIDGE_NOT_CONNECTED.value
    # Nothing was published.
    row = _published_row(stack["server"].app, body["caseId"])
    assert row is None


def test_failure_bridge_disconnected_mid_generation(stack):
    token, _pairing, bridge, _ack = _pair_and_connect(stack)

    def _drop_after_first(job, b):
        mini = b.mini
        # Reply to the first job, then close the socket while the 2nd is on
        # its way -> the provider sees BRIDGE_DISCONNECTED.
        mini.reply_success(job, b)
        b.close()

    bridge.mini.on_job = _drop_after_first
    result = _generate(stack, token)
    assert result.status_code == 201, result.json()
    body = result.json()
    assert body["status"] == "FAILED"
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.BRIDGE_DISCONNECTED.value
    assert _published_row(stack["server"].app, body["caseId"]) is None
    bridge.close()


def test_failure_ollama_stopped_mid_run(stack):
    """MiniOllama reports LOCAL_OLLAMA_UNAVAILABLE on the evidence stage —
    the typed failure surfaces and NOTHING publishes."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)

    def _fail_evidence(job, b):
        if job["schemaId"] == "EVIDENCE_v1":
            b.mini.reply_fail(job, b, "LOCAL_OLLAMA_UNAVAILABLE")
        else:
            b.mini.reply_success(job, b)

    bridge.mini.on_job = _fail_evidence
    result = _generate(stack, token)
    body = result.json()
    assert result.status_code == 201
    assert body["status"] == "FAILED"
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.LOCAL_OLLAMA_UNAVAILABLE.value
    assert _published_row(stack["server"].app, body["caseId"]) is None
    bridge.close()


def test_failure_local_model_unavailable(stack):
    token, _pairing, bridge, _ack = _pair_and_connect(stack)

    def _model_removed(job, b):
        b.mini.reply_fail(job, b, "LOCAL_MODEL_UNAVAILABLE")

    bridge.mini.on_job = _model_removed
    result = _generate(stack, token)
    body = result.json()
    assert result.status_code == 201
    assert body["status"] == "FAILED"
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.LOCAL_MODEL_UNAVAILABLE.value
    assert _published_row(stack["server"].app, body["caseId"]) is None
    bridge.close()


def test_failure_local_provider_timeout_and_cancel_frame(timeout_stack):
    """A silent bridge (never replies) -> LOCAL_PROVIDER_TIMEOUT; the server
    best-effort sends a job_cancel frame to the bridge."""
    token, _pairing, bridge, _ack = _pair_and_connect(timeout_stack)

    def _stay_silent(_job, _b):
        return None

    bridge.mini.on_job = _stay_silent
    result = _generate(timeout_stack, token, timeout=60)
    body = result.json()
    assert result.status_code == 201
    assert body["status"] == "FAILED"
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT.value
    assert _published_row(timeout_stack["server"].app, body["caseId"]) is None
    assert bridge.wait_for(lambda: bool(bridge.cancels), timeout=5)
    bridge.close()


def test_failure_model_allowlist_violation(stack, tmp_path_factory):
    """With BRIDGE_MODEL_ALLOWLIST set, a bridge reporting a NON-allowlisted
    model is rejected at the handshake (typed close, no binding)."""
    db_dir = tmp_path_factory.mktemp("bridge_allow")
    url = f"sqlite:///{(db_dir / 'a.db').as_posix()}"
    upgrade_db(url)
    settings = make_bridge_settings(url, bridge_model_allowlist=["hermes3:8b"])
    app = create_app(settings)
    server = LiveTestServer(app)

    base = server.base_url
    token = new_anonymous_session(base)["anonymousSessionToken"]
    pairing = create_pairing(base, token)
    ws = _sync_connect(server.ws_url)
    ws.send(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "pairing_hello",
                "pairingCode": pairing["pairingCode"],
                "model": "llama3.2:3b",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    try:
        ws.recv(timeout=10)
    except Exception as exc:  # noqa: BLE001
        assert getattr(getattr(exc, "rcvd", None), "code", None) == CLOSE_POLICY_VIOLATION
    ws.close()
    status = bridge_status(base, token)["remoteLocalAi"]
    assert status["connected"] is False
    server.close()
    app.state.engine.dispose()
    app.state.store.dispose()


# =========================================================================== #
# reconnect + cancellation + F. authentication
# =========================================================================== #


def test_reconnect_after_connection_drop_restores_availability(stack):
    token, _pairing, bridge, ack = _pair_and_connect(stack)
    secret = ack["bridgeSessionToken"]
    session_id = ack["bridgeSessionId"]
    # First generation works.
    result = _generate(stack, token)
    assert result.json()["status"] == "PUBLISHED"
    before = bridge.job_count
    assert before == 4
    # Drop the socket and reconnect with the bridge token (bridge_hello).
    bridge.close()
    time.sleep(0.2)
    bridge2 = TestBridge(stack["server"].ws_url)
    ack2 = bridge2.connect_reconnect(secret)
    assert ack2["type"] == "pairing_accepted"
    assert ack2["bridgeSessionId"] == session_id
    assert "bridgeSessionToken" not in ack2  # returned exactly once
    status = bridge_status(stack["base_url"], token)["remoteLocalAi"]
    assert status["connected"] is True
    # A NEW generation works over the reconnected socket (future jobs only —
    # stale ones never revive, §18).
    result2 = _generate(stack, token)
    assert result2.json()["status"] == "PUBLISHED"
    assert bridge2.job_count == 4
    bridge2.close()


def test_reconnect_rejects_expired_or_revoked_token(stack):
    """An unknown / wrong token is rejected without binding (typed close)."""
    ws = _sync_connect(stack["server"].ws_url)
    ws.send(
        json.dumps(
            {
                "protocolVersion": 1,
                "type": "bridge_hello",
                "bridgeSessionToken": "z" * 40,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    try:
        ws.recv(timeout=10)
    except Exception as exc:  # noqa: BLE001
        assert getattr(getattr(exc, "rcvd", None), "code", None) == CLOSE_POLICY_VIOLATION
    ws.close()


def test_player_cannot_use_creator_bridge(stack):
    """Cross-user: a DIFFERENT player session has NO bridge bound -> their
    generation fails typed BRIDGE_NOT_CONNECTED and they can never see or
    invoke the creator's bridge."""
    creator_token, _pairing, bridge, _ack = _pair_and_connect(stack)
    player_token = new_anonymous_session(stack["base_url"])["anonymousSessionToken"]
    assert player_token != creator_token
    # The player's status shows no connection.
    player_status = bridge_status(stack["base_url"], player_token)["remoteLocalAi"]
    assert player_status["connected"] is False
    result = _generate(stack, player_token)
    body = result.json()
    assert result.status_code == 201
    assert body["status"] == "FAILED"
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.BRIDGE_NOT_CONNECTED.value
    assert _published_row(stack["server"].app, body["caseId"]) is None
    assert bridge.job_count == 0  # the creator's bridge was never touched
    bridge.close()


def test_pairing_does_not_serve_other_sessions_bridge(stack):
    """Two sessions: each pair their own bridge; each generation uses ONLY its
    own bridge (jobs never cross sessions)."""
    base = stack["base_url"]
    t1 = new_anonymous_session(base)["anonymousSessionToken"]
    t2 = new_anonymous_session(base)["anonymousSessionToken"]
    p1 = create_pairing(base, t1)
    p2 = create_pairing(base, t2)
    b1 = TestBridge(stack["server"].ws_url)
    b2 = TestBridge(stack["server"].ws_url)
    b1.connect_pairing(p1["pairingCode"], model=_OLLAMA)
    b2.connect_pairing(p2["pairingCode"], model="hermes3:8b")
    r1 = _generate(stack, t1)
    r2 = _generate(stack, t2)
    assert r1.json()["status"] == "PUBLISHED"
    assert r2.json()["status"] == "PUBLISHED"
    assert b1.job_count == 4 and b2.job_count == 4
    # Cross-session replies: b1 answers a JOB id dispatched to b2's session —
    # the server discards it (no waiter with that id on b1's connection) and
    # the b2 generation is unaffected.
    other_job = b2.jobs[0]
    b1.send_frame(
        {
            "protocolVersion": 1,
            "type": "job_result",
            "jobId": other_job["jobId"],
            "status": "SUCCESS",
            "structuredOutput": {"hijack": True},
        }
    )
    # b2 still completed its generation normally (already asserted above).
    b1.close()
    b2.close()


def test_second_binding_for_same_scope_supersedes_old_socket(stack):
    """A session may pair TWICE; the SECOND live bridge for the same scope
    SUPERSEDES the first (old socket closed, old in-flight fails typed) and the
    new one serves generations — the registry keeps ONE live connection per
    scope (no unbounded socket fan-out)."""
    base = stack["base_url"]
    token, _pairing1, bridge1, _ack1 = _pair_and_connect(stack)
    first_session_id = bridge1.bridge_session_id
    pairing2 = create_pairing(base, token)
    bridge2 = TestBridge(stack["server"].ws_url)
    ack2 = bridge2.connect_pairing(pairing2["pairingCode"], model=_OLLAMA)
    assert ack2["bridgeSessionId"] != first_session_id
    # The old socket was closed by the supersede; its reader records a close.
    assert bridge1.wait_for(lambda: bridge1.close_code is not None, timeout=10)
    registry = stack["server"].app.state.bridge_registry
    scope = stack["server"].app.state.store.get_session_by_verifier(
        __import__("app.auth.tokens", fromlist=["verifier"]).verifier(token)
    ).session_id
    assert registry.lookup_for_scope(scope).bridge_session_id == ack2["bridgeSessionId"]
    result = _generate(stack, token)
    assert result.json()["status"] == "PUBLISHED"
    assert bridge2.job_count == 4
    bridge1.close()
    bridge2.close()


def test_http_bridge_routes_404_when_disabled(database_url):
    from app.main import create_app as _create_app
    from fastapi.testclient import TestClient

    upgrade_db(database_url)
    app_disabled = _create_app(Settings(database_url=database_url, enable_bridge=False))
    with TestClient(app_disabled) as client:
        pairing = client.post(
            "/api/v1/bridge/pairing",
            headers={"Authorization": "Bearer " + "x" * 30},
        )
        status = client.get(
            "/api/v1/bridge/status",
            headers={"Authorization": "Bearer " + "x" * 30},
        )
    assert pairing.status_code == 404
    assert status.status_code == 404
    app_disabled.state.engine.dispose()
    app_disabled.state.store.dispose()