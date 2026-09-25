import json

import pytest

from pd_ollama_bridge import protocol
from pd_ollama_bridge.protocol import (
    BridgeProtocolError,
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_PROTOCOL_ERROR,
    CLOSE_UNSUPPORTED_TYPE,
    encode_frame,
    job_result_failed_frame,
    job_result_success_frame,
    pairing_hello_frame,
    pong_frame,
    validate_frame,
    decode_frame,
)


def _decode_ok(raw):
    return validate_frame(decode_frame(raw))


def test_pairing_hello_roundtrip():
    frame = _decode_ok(
        pairing_hello_frame(
            pairing_code="PD-AB2D-CF34",
            model="hermes3:8b",
            capabilities=("STRUCTURED_MODEL_INFERENCE",),
        )
    )
    assert frame["type"] == "pairing_hello"
    assert frame["pairingCode"] == "PD-AB2D-CF34"
    assert frame["model"] == "hermes3:8b"
    assert frame["capabilities"] == ("STRUCTURED_MODEL_INFERENCE",)


def test_job_frame_accepted():
    msg = {
        "protocolVersion": 1,
        "type": "job",
        "jobId": "JOB-abc",
        "jobType": "STRUCTURED_INFERENCE",
        "schemaId": "ASSET_SPEC_v1",
        "model": "hermes3:8b",
        "prompt": "solve it",
        "temperature": 0.1,
        "timeoutMs": 120000,
    }
    out = _decode_ok(encode_frame(msg))
    assert out["jobId"] == "JOB-abc"
    assert out["timeoutMs"] == 120000


def test_job_unknown_job_type_rejected():
    msg = {
        "protocolVersion": 1,
        "type": "job",
        "jobId": "JOB-abc",
        "jobType": "EXECUTE_COMMAND",
        "schemaId": "ASSET_SPEC_v1",
        "model": "hermes3:8b",
        "prompt": "x",
        "temperature": 0.1,
        "timeoutMs": 120000,
    }
    with pytest.raises(BridgeProtocolError) as exc:
        _decode_ok(encode_frame(msg))
    assert exc.value.close_code == CLOSE_PROTOCOL_ERROR


def test_job_unknown_schema_rejected():
    msg = {
        "protocolVersion": 1,
        "type": "job",
        "jobId": "JOB-abc",
        "jobType": "STRUCTURED_INFERENCE",
        "schemaId": "FREEFORM_ANYTHING",
        "model": "hermes3:8b",
        "prompt": "x",
        "temperature": 0.1,
        "timeoutMs": 120000,
    }
    with pytest.raises(BridgeProtocolError):
        _decode_ok(encode_frame(msg))


def test_job_extraneous_instruction_field_rejected():
    msg = {
        "protocolVersion": 1,
        "type": "job",
        "jobId": "JOB-abc",
        "jobType": "STRUCTURED_INFERENCE",
        "schemaId": "ASSET_SPEC_v1",
        "model": "hermes3:8b",
        "prompt": "x",
        "temperature": 0.1,
        "timeoutMs": 120000,
        "fetch": "http://attacker.example/read-file",
    }
    with pytest.raises(BridgeProtocolError) as exc:
        _decode_ok(encode_frame(msg))
    assert exc.value.close_code == CLOSE_PROTOCOL_ERROR


def test_unknown_message_type_rejected():
    with pytest.raises(BridgeProtocolError) as exc:
        _decode_ok('{"protocolVersion": 1, "type": "delete_all_files"}')
    assert exc.value.close_code == CLOSE_UNSUPPORTED_TYPE


def test_wrong_protocol_version_rejected():
    with pytest.raises(BridgeProtocolError) as exc:
        _decode_ok('{"protocolVersion": 2, "type": "ping"}')
    assert exc.value.close_code == CLOSE_PROTOCOL_ERROR


def test_ping_without_version_rejected():
    with pytest.raises(BridgeProtocolError):
        _decode_ok('{"type": "ping"}')


def test_bare_pong_accepted():
    assert _decode_ok('{"type": "pong"}') == {"type": "pong"}
    assert _decode_ok(pong_frame()) == {"type": "pong"}


def test_pong_with_extra_field_rejected():
    with pytest.raises(BridgeProtocolError):
        _decode_ok('{"type": "pong", "protocolVersion": 1, "extra": 1}')


def test_oversized_frame_rejected():
    pad = "a" * (protocol.BRIDGE_MAX_MESSAGE_BYTES + 100)
    raw = json.dumps(
        {"protocolVersion": 1, "type": "ping", "pad": pad},
        separators=(",", ":"),
    )
    with pytest.raises(BridgeProtocolError) as exc:
        decode_frame(raw)
    assert exc.value.close_code == CLOSE_MESSAGE_TOO_BIG


def test_deep_frame_rejected():
    raw = '{"a":' + "[" * 100 + "1" + "]" * 100 + "}"
    with pytest.raises(BridgeProtocolError) as exc:
        decode_frame(raw)
    assert exc.value.close_code == CLOSE_PROTOCOL_ERROR


def test_malformed_frame_rejected():
    with pytest.raises(BridgeProtocolError) as exc:
        decode_frame("not json at all")
    assert exc.value.close_code == CLOSE_PROTOCOL_ERROR


def test_job_result_frames_validate():
    ok = validate_frame(
        decode_frame(job_result_success_frame(job_id="JOB-01", structured_output={"x": 1}))
    )
    assert ok["status"] == "SUCCESS"
    bad = validate_frame(
        decode_frame(job_result_failed_frame(job_id="JOB-01", failure_code="BRIDGE_BUSY"))
    )
    assert bad["failureCode"] == "BRIDGE_BUSY"


def test_failed_result_with_unknown_code_rejected():
    raw = json.dumps(
        {
            "protocolVersion": 1,
            "type": "job_result",
            "jobId": "JOB-01",
            "status": "FAILED",
            "failureCode": "RUN_SHELL",
        }
    )
    with pytest.raises(BridgeProtocolError):
        _decode_ok(raw)