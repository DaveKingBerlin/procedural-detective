"""Phase 22 — local adversarial bridge (Phase22 §32/§33).

A MALICIOUS bridge attempts every abuse in the threat model; each must FAIL
SAFELY: typed failure (or accepted-and-discarded), NO publication, NO state
corruption (later generations on fresh/new connections still publish).

Covered (§33 + §44 failure acceptance):

  - invalid structured schemas (strict parsers reject; NEVER published)
  - wrong job ids (discarded -> typed LOCAL_PROVIDER_TIMEOUT)
  - duplicate responses (first wins; the duplicate is discarded)
  - oversized response frames (close 1009; in-flight fails typed)
  - deeply nested JSON responses (close 1002; in-flight fails typed)
  - unexpected message types mid-generation (close; typed failure)
  - late response after cancellation (discarded; NO state corruption)
  - cross-stage responses (a foreign stage's output never misroutes)
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import upgrade_db  # noqa: E402
from app.main import create_app  # noqa: E402

from bridge_harness import (  # noqa: E402
    LiveTestServer,
    TestBridge,
    make_bridge_settings,
    new_anonymous_session,
    create_pairing,
)

from app.generation.bridge_protocol import (  # noqa: E402
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_PROTOCOL_ERROR,
    CLOSE_UNSUPPORTED_TYPE,
)
from app.generation.failure_codes import (  # noqa: E402
    GenerationFailureCode,
    public_failure_code,
)

_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
    "Weapon: bronze ceremonial ice pick\nTime: 23:42\nWitness: Lisa Koenig\n"
    "Location: office\n"
)


def _full_stack(tmp_path_factory, *, tag: str, **overrides):
    db_dir = tmp_path_factory.mktemp(f"evil_{tag}")
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
        bridge_reconnect_grace_seconds=30.0,
        bridge_max_frames_per_window=100,
    )
    yield ctx
    ctx["server"].close()
    app.state.engine.dispose()
    app.state.store.dispose()


def _pair_and_connect(stack, *, model="hermes3:8b", session_token=None):
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


def _published(stack, case_id):
    return stack["server"].app.state.store.get_published(case_id, 1)


def _failed_body(response):
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["status"] == "FAILED"
    return body


# The deterministic pipeline may classify adversarial bridge output through ANY
# terminal typed code (invalid output / budget exhausted from bounded retries)
# — the security property is: TYPED failure + NO publication + later healthy
# runs publish.
_TERMINAL_SAFE = {
    GenerationFailureCode.STRUCTURED_OUTPUT_INVALID.value,
    GenerationFailureCode.VALIDATION_FAILED.value,
    GenerationFailureCode.CORE_PROVIDER_CALL_BUDGET_EXHAUSTED.value,
    GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED.value,
    GenerationFailureCode.PROVIDER_CALL_BUDGET_EXHAUSTED.value,
    GenerationFailureCode.MAX_FAILED_ASSETS_EXCEEDED.value,
    GenerationFailureCode.MAX_PROCEDURAL_ASSETS_EXCEEDED.value,
}


def test_malicious_invalid_schema_never_publishes(stack):
    """A bridge returning structurally-wrong CASE data twice (bounded stage
    retry consumes both) fails the attempt typed — no publication, and the
    scoring/oracle state is untouched (the NEXT healthy run publishes)."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)

    def _garbage_case(job, b):
        bad = {"crime": {"type": "not-a-valid-crime-type"}}
        b.mini.reply(job, b, {"status": "SUCCESS", "structuredOutput": bad})

    bridge.mini.on_job = _garbage_case
    response = _generate(stack, token)
    body = _failed_body(response)
    assert public_failure_code(body["failureCode"]) in _TERMINAL_SAFE
    assert _published(stack, body["caseId"]) is None
    assert len(bridge.jobs or []) >= 1  # the bounded retry ran
    bridge.close()

    # NO state corruption: a fresh healthy bridge+session publishes.
    token2, _pairing2, bridge2, _ack2 = _pair_and_connect(stack)
    result = _generate(stack, token2)
    assert result.json()["status"] == "PUBLISHED"
    bridge2.close()


def test_malicious_wrong_job_id_results_discarded_then_timeout(timeout_stack):
    """A bridge answering with the WRONG jobId never matches the in-flight
    job: every reply is discarded, the provider times out typed, and nothing
    publishes (Phase22 §33: wrong job IDs / §17 late/duplicate discard)."""
    token, _pairing, bridge, _ack = _pair_and_connect(timeout_stack)

    def _wrong_job_id(job, b):
        frame = {
            "protocolVersion": 1,
            "type": "job_result",
            "jobId": "JOB-EVIL-WRONG-ID",
            "status": "SUCCESS",
            "structuredOutput": {"evil": True},
        }
        b.send_frame(frame)

    bridge.mini.on_job = _wrong_job_id
    response = _generate(timeout_stack, token, timeout=60)
    body = _failed_body(response)
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT.value
    assert _published(timeout_stack, body["caseId"]) is None
    bridge.close()


def test_malicious_duplicate_response_discarded_and_generation_succeeds(stack):
    """Duplicating a SUCCESS reply for the same job: the first resolves the
    job, the duplicate is DISCARDED (no double-apply, no corruption), and the
    generation still PUBLISHES with exactly four jobs."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)

    def _duplicate_after_success(job, b):
        b.mini.reply_success(job, b)
        b.mini.reply_success(job, b)  # duplicate for the same jobId

    bridge.mini.on_job = _duplicate_after_success
    response = _generate(stack, token)
    assert response.status_code == 201, response.json()
    assert response.json()["status"] == "PUBLISHED"
    assert len(bridge.jobs) == 8  # 4 core + 4 Phase 19J activity logs
    bridge.close()


def test_malicious_oversized_response_closes_and_fails_typed(stack):
    token, _pairing, bridge, _ack = _pair_and_connect(stack)

    def _oversized(job, b):
        b.send_frame(
            {
                "protocolVersion": 1,
                "type": "job_result",
                "jobId": job["jobId"],
                "status": "SUCCESS",
                "structuredOutput": {"pad": "x" * 150 * 1024},
            }
        )

    bridge.mini.on_job = _oversized
    response = _generate(stack, token)
    body = _failed_body(response)
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.BRIDGE_DISCONNECTED.value
    assert _published(stack, body["caseId"]) is None
    assert bridge.wait_for(lambda: bridge.close_code is not None, timeout=10)
    assert bridge.close_code == CLOSE_MESSAGE_TOO_BIG
    bridge.close()


def test_malicious_deep_json_response_closes_and_fails_typed(stack):
    token, _pairing, bridge, _ack = _pair_and_connect(stack)

    def _deep(job, b):
        deep_text = '{"type":"job_result","jobId":"' + job["jobId"] + '","status":"SUCCESS","structuredOutput":' + (
            '{"k":' * 60
        ) + '1' + ("}" * 60) + "}"
        b.send_raw(deep_text)

    bridge.mini.on_job = _deep
    response = _generate(stack, token)
    body = _failed_body(response)
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.BRIDGE_DISCONNECTED.value
    assert _published(stack, body["caseId"]) is None
    assert bridge.wait_for(lambda: bridge.close_code is not None, timeout=10)
    assert bridge.close_code == CLOSE_PROTOCOL_ERROR
    bridge.close()


def test_malicious_unexpected_message_mid_generation_closes(stack):
    """An unexpected message type while a job is in flight closes the
    connection (1003) and the in-flight job fails typed BRIDGE_DISCONNECTED."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)

    def _spam(job, b):
        b.send_frame({"type": "delete-all-files-please"})

    bridge.mini.on_job = _spam
    response = _generate(stack, token)
    body = _failed_body(response)
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.BRIDGE_DISCONNECTED.value
    assert _published(stack, body["caseId"]) is None
    assert bridge.wait_for(lambda: bridge.close_code is not None, timeout=10)
    assert bridge.close_code in (CLOSE_UNSUPPORTED_TYPE, CLOSE_PROTOCOL_ERROR)
    bridge.close()


def test_malicious_late_response_after_cancel_discarded_no_corruption(timeout_stack):
    """A response arriving AFTER the provider cancelled (deadline timeout)
    finds no current job and is DISCARDED server-side; a RECONNECT with the
    bridge token restores availability and the next generation PUBLISHES —
    no state corruption (Phase22 §17/§18: stale jobs never revive)."""
    token, _pairing, bridge, ack = _pair_and_connect(timeout_stack)
    secret = ack["bridgeSessionToken"]

    def _silent_then_late(job, b):
        # Reply ONLY after the provider timed out (2s job deadline) and sent
        # job_cancel: the late result finds no current job and is discarded.
        time.sleep(3.5)
        b.mini.reply_success(job, b)

    bridge.mini.on_job = _silent_then_late
    response = _generate(timeout_stack, token, timeout=60)
    body = _failed_body(response)
    assert public_failure_code(body["failureCode"]) == GenerationFailureCode.LOCAL_PROVIDER_TIMEOUT.value
    assert _published(timeout_stack, body["caseId"]) is None
    # Let the late reply actually arrive and be DISCARDED; the connection stays
    # alive and the job slot is free again (registry has no in-flight job).
    time.sleep(4.5)
    assert bridge.close_code is None
    registry = timeout_stack["server"].app.state.bridge_registry
    from app.auth.tokens import verifier as _verifier

    scope = timeout_stack["server"].app.state.store.get_session_by_verifier(
        _verifier(token)
    ).session_id
    conn = registry.lookup_for_scope(scope)
    assert conn is not None
    with conn._job_lock:
        assert conn.current_job_id is None  # cancelled job slot is free

    # A reconnect with the bridge token restores availability (the DB session
    # was never revoked by the cancellation).
    bridge.close()
    time.sleep(0.2)
    bridge2 = TestBridge(timeout_stack["server"].ws_url)
    bridge2.connect_reconnect(secret)
    result = _generate(timeout_stack, token, timeout=60)
    assert result.json()["status"] == "PUBLISHED"
    assert len(bridge2.jobs) == 8  # 4 core + 4 Phase 19J activity logs
    bridge2.close()


def test_malicious_cross_stage_output_rejected(stack):
    """A bridge answering the CASE job with ASSET_SPEC-shaped content fails
    the strict stage parse -> typed failure -> NO publication (a foreign
    stage's output can never be misrouted into another stage's draft)."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)

    def _cross_stage(job, b):
        # A perfectly valid AssetSpec-shaped response served for CASE/PEOPLE.
        b.mini.reply(
            job,
            b,
            {
                "status": "SUCCESS",
                "structuredOutput": {
                    "canonicalName": "Ice Pick",
                    "category": "decor",
                    "dimensions": {"x": 0.1, "y": 0.4, "z": 0.1},
                    "parts": [],
                },
            },
        )

    bridge.mini.on_job = _cross_stage
    response = _generate(stack, token)
    body = _failed_body(response)
    assert public_failure_code(body["failureCode"]) in _TERMINAL_SAFE
    assert _published(stack, body["caseId"]) is None
    bridge.close()


def test_malicious_bridge_failure_code_projected_never_raw(stack):
    """A bridge-reported untyped FAILED result (arbitrary failureCode text)
    is projected onto the closed vocabulary — arbitrary bridge text NEVER
    reaches the public failureCode."""
    token, _pairing, bridge, _ack = _pair_and_connect(stack)

    def _evil_failure_code(job, b):
        b.send_frame(
            {
                "protocolVersion": 1,
                "type": "job_result",
                "jobId": job["jobId"],
                "status": "FAILED",
                "failureCode": "DROP TABLE cases",  # not in the closed set
            }
        )

    bridge.mini.on_job = _evil_failure_code
    response = _generate(stack, token)
    body = _failed_body(response)
    code = public_failure_code(body["failureCode"])
    assert code in {
        GenerationFailureCode.BRIDGE_PROTOCOL_ERROR.value,
        GenerationFailureCode.BRIDGE_DISCONNECTED.value,
    }
    assert "DROP TABLE" not in json.dumps(body)
    assert _published(stack, body["caseId"]) is None
    bridge.close()