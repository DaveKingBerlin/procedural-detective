"""The strict, versioned BYO-Ollama bridge job protocol (v1) — CLIENT copy.

This module is the bridge side of the ONE authoritative protocol definition
(``app.generation.bridge_protocol`` on the server track). Constants, closed
vocabularies, safe alphabets and per-frame validation are identical so a valid
frame is accepted identically in both directions:

- client->server: ``pairing_hello``, ``bridge_hello``, ``job_result``, ``pong``
- server->client: ``pairing_accepted``, ``job``, ``job_cancel``, ``ping``

Strictness (defense in depth on BOTH sides):

- ``protocolVersion`` must equal 1 for every message except the bare ``pong``
  (and the same rule the server applies to ``ping``);
- unknown message types are rejected (close 1003 UNSUPPORTED_TYPE);
- unknown extra keys are rejected (closed schema) — a job frame carrying
  instructions beyond ``jobId/jobType/schemaId/model/prompt/temperature/
  timeoutMs`` is rejected here, so a hostile server can never slip an
  "instruction" field through;
- frames are depth-bounded with the same ``bounded_json_loads`` preflight;
- free-text fields are length-bounded and restricted to safe alphabets.

Close codes (WS): 1001 idle, 1002 malformed/protocol-violation, 1003
unsupported message type, 1008 unauthorized/rejected, 1009 frame too large,
1011 peer internal error.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .bounded_json import bounded_json_loads

PROTOCOL_VERSION = 1

MSG_BRIDGE_HELLO = "bridge_hello"
MSG_PAIRING_HELLO = "pairing_hello"
MSG_PAIRING_ACCEPTED = "pairing_accepted"
MSG_JOB = "job"
MSG_JOB_RESULT = "job_result"
MSG_JOB_CANCEL = "job_cancel"
MSG_PING = "ping"
MSG_PONG = "pong"
MESSAGE_TYPES: frozenset[str] = frozenset(
    {
        MSG_BRIDGE_HELLO,
        MSG_PAIRING_HELLO,
        MSG_PAIRING_ACCEPTED,
        MSG_JOB,
        MSG_JOB_RESULT,
        MSG_JOB_CANCEL,
        MSG_PING,
        MSG_PONG,
    }
)

CAPABILITY_STRUCTURED_MODEL_INFERENCE = "STRUCTURED_MODEL_INFERENCE"
CAPABILITY_LIST_LOCAL_MODELS = "LIST_LOCAL_MODELS"
ALLOWED_CAPABILITIES: frozenset[str] = frozenset(
    {CAPABILITY_STRUCTURED_MODEL_INFERENCE, CAPABILITY_LIST_LOCAL_MODELS}
)
DEFAULT_CAPABILITIES: tuple[str, ...] = (CAPABILITY_STRUCTURED_MODEL_INFERENCE,)

JOB_TYPE_STRUCTURED_INFERENCE = "STRUCTURED_INFERENCE"
JOB_TYPES: frozenset[str] = frozenset({JOB_TYPE_STRUCTURED_INFERENCE})
JOB_STATUS_SUCCESS = "SUCCESS"
JOB_STATUS_FAILED = "FAILED"
JOB_STATUSES: frozenset[str] = frozenset({JOB_STATUS_SUCCESS, JOB_STATUS_FAILED})

BRIDGE_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "LOCAL_OLLAMA_UNAVAILABLE",
        "LOCAL_MODEL_UNAVAILABLE",
        "LOCAL_PROVIDER_TIMEOUT",
        "LOCAL_PROVIDER_INVALID_OUTPUT",
        "BRIDGE_BUSY",
        "BRIDGE_PROTOCOL_ERROR",
    }
)

CLOSE_IDLE_TIMEOUT = 1001
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_UNSUPPORTED_TYPE = 1003
CLOSE_POLICY_VIOLATION = 1008
CLOSE_MESSAGE_TOO_BIG = 1009
CLOSE_SERVER_ERROR = 1011

PAIRING_CODE_RE = re.compile(r"^PD-[A-Z2-7]{4}-[A-Z2-7]{4}$")
MODEL_LABEL_RE = re.compile(r"[A-Za-z0-9._:\-]+")
MAX_MODEL_LABEL_LENGTH = 80
MAX_SCHEMA_ID_LENGTH = 64
MAX_PROMPT_CHARS = 120_000
MAX_JOB_ID_LENGTH = 96
MAX_BRIDGE_SESSION_ID_LENGTH = 160
MAX_FAILURE_CODE_LENGTH = 64
MAX_CAPABILITIES = 8
MAX_CAPABILITY_LENGTH = 64
JOB_ID_PREFIX = "JOB-"
MIN_BRIDGE_TOKEN_LENGTH = 20
MAX_BRIDGE_TOKEN_LENGTH = 256

AUTHORITATIVE_SCHEMA_IDS: frozenset[str] = frozenset(
    {
        "CASE_PEOPLE_v1",
        "EVIDENCE_v1",
        "WORLD_REQUIREMENTS_v1",
        "ASSET_SPEC_v1",
        "REPAIR_v1",
        "ASSET_SPEC_REPAIR_v1",
    }
)

BRIDGE_MAX_MESSAGE_BYTES = 262_144
MAX_PROMPT_BYTES = MAX_PROMPT_CHARS
MAX_SCHEMA_BYTES = 256
MAX_RESPONSE_BYTES = BRIDGE_MAX_MESSAGE_BYTES
MAX_JSON_DEPTH = 32
MAX_COLLECTION_LENGTH = 10_000

DEFAULT_SERVER_URL = "https://detective.example.com"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "hermes3:8b"
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0

HEARTBEAT_RESPOND_TIMEOUT_SECONDS = 20.0
IDLE_TIMEOUT_SECONDS = 300.0
RECONNECT_BACKOFF_BASE_SECONDS = 1.0
RECONNECT_BACKOFF_CAP_SECONDS = 8.0


class BridgeProtocolError(Exception):
    """A rejected frame. Carries the WS close code + SHORT sanitized reason."""

    def __init__(self, close_code: int, reason: str) -> None:
        super().__init__(reason)
        self.close_code = int(close_code)
        self.reason = str(reason)


def _reject(close_code: int, reason: str) -> "None":
    raise BridgeProtocolError(close_code, reason)


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _require_str(msg: Mapping[str, Any], key: str, *, max_len: int, regex: re.Pattern | None = None) -> str:
    value = msg.get(key)
    if not isinstance(value, str) or not value:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if len(value) > max_len:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if regex is not None and not regex.fullmatch(value):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    return value


def _validate_protocol_version(msg: Mapping[str, Any]) -> None:
    version = msg.get("protocolVersion")
    if version != PROTOCOL_VERSION:
        _reject(CLOSE_PROTOCOL_ERROR, "unsupported protocol version")


def _reject_unknown_keys(msg: Mapping[str, Any], allowed: set[str], context: str) -> None:
    extra = set(msg) - set(allowed)
    if extra:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")


def _parse_capabilities(value: Any) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_CAPABILITIES
    if not isinstance(value, list) or not value:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if len(value) > MAX_CAPABILITIES:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    out: list[str] = []
    for token in value:
        if (
            not isinstance(token, str)
            or not token
            or len(token) > MAX_CAPABILITY_LENGTH
            or token not in ALLOWED_CAPABILITIES
        ):
            _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
        out.append(token)
    if CAPABILITY_STRUCTURED_MODEL_INFERENCE not in out:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    return tuple(dict.fromkeys(out))


def _validate_model_label(value: Any) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_MODEL_LABEL_LENGTH
        or not MODEL_LABEL_RE.fullmatch(value)
    ):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    return value


def _validate_job_result_schema(msg: Mapping[str, Any]) -> dict[str, Any]:
    _reject_unknown_keys(
        msg,
        {"protocolVersion", "type", "jobId", "status", "structuredOutput", "failureCode"},
        "job_result",
    )
    job_id = _require_str(msg, "jobId", max_len=MAX_JOB_ID_LENGTH)
    if not job_id.startswith(JOB_ID_PREFIX):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    status = msg.get("status")
    if status not in JOB_STATUSES:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if status == JOB_STATUS_SUCCESS:
        output = msg.get("structuredOutput")
        if not isinstance(output, Mapping) or _is_bool(output):
            _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
        if "failureCode" in msg:
            _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
        return {
            "type": MSG_JOB_RESULT,
            "jobId": job_id,
            "status": status,
            "structuredOutput": dict(output),
        }
    failure = msg.get("failureCode")
    if (
        not isinstance(failure, str)
        or not failure
        or len(failure) > MAX_FAILURE_CODE_LENGTH
        or failure not in BRIDGE_FAILURE_CODES
    ):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if "structuredOutput" in msg:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    return {"type": MSG_JOB_RESULT, "jobId": job_id, "status": status, "failureCode": failure}


def _validate_job_schema(msg: Mapping[str, Any]) -> dict[str, Any]:
    _reject_unknown_keys(
        msg,
        {
            "protocolVersion",
            "type",
            "jobId",
            "jobType",
            "schemaId",
            "model",
            "prompt",
            "temperature",
            "timeoutMs",
        },
        "job",
    )
    job_id = _require_str(msg, "jobId", max_len=MAX_JOB_ID_LENGTH)
    if not job_id.startswith(JOB_ID_PREFIX):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if msg.get("jobType") != JOB_TYPE_STRUCTURED_INFERENCE:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    schema_id = msg.get("schemaId")
    if (
        not isinstance(schema_id, str)
        or not schema_id
        or len(schema_id) > MAX_SCHEMA_ID_LENGTH
    ):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if schema_id not in AUTHORITATIVE_SCHEMA_IDS:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    prompt = msg.get("prompt")
    if not isinstance(prompt, str):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if len(prompt) > MAX_PROMPT_CHARS:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    temperature = msg.get("temperature")
    if _is_bool(temperature) or not isinstance(temperature, (int, float)):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if not (0.0 <= float(temperature) <= 2.0):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    timeout_ms = msg.get("timeoutMs")
    if _is_bool(timeout_ms) or not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    return {
        "type": MSG_JOB,
        "jobId": job_id,
        "jobType": JOB_TYPE_STRUCTURED_INFERENCE,
        "schemaId": schema_id,
        "model": _validate_model_label(msg.get("model")),
        "prompt": prompt,
        "temperature": float(temperature),
        "timeoutMs": int(timeout_ms),
    }


def _validate_handshake_schema(msg: Mapping[str, Any]) -> dict[str, Any]:
    msg_type = msg.get("type")
    if msg_type == MSG_PAIRING_HELLO:
        _reject_unknown_keys(
            msg, {"protocolVersion", "type", "pairingCode", "model", "capabilities"}, "pairing_hello"
        )
        code = _require_str(
            msg, "pairingCode", max_len=16, regex=PAIRING_CODE_RE
        )
        return {
            "type": MSG_PAIRING_HELLO,
            "pairingCode": code,
            "model": _validate_model_label(msg.get("model")),
            "capabilities": _parse_capabilities(msg.get("capabilities")),
        }
    _reject_unknown_keys(msg, {"protocolVersion", "type", "bridgeSessionToken"}, "bridge_hello")
    token = _require_str(msg, "bridgeSessionToken", max_len=MAX_BRIDGE_TOKEN_LENGTH)
    if len(token) < MIN_BRIDGE_TOKEN_LENGTH:
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    return {"type": MSG_BRIDGE_HELLO, "bridgeSessionToken": token}


def decode_frame(raw_text: str, *, max_bytes: int = BRIDGE_MAX_MESSAGE_BYTES) -> dict[str, Any]:
    """Decode + depth-bound ONE WebSocket text frame into a plain dict.

    Raises ``BridgeProtocolError`` (close code + sanitized reason) for any
    oversized / malformed / deep frame; the caller closes the connection.
    """
    if not isinstance(raw_text, str):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    if len(raw_text.encode("utf-8")) > int(max_bytes):
        _reject(CLOSE_MESSAGE_TOO_BIG, "message too large")
    try:
        obj = bounded_json_loads(raw_text)
    except (ValueError, TypeError):
        _reject(CLOSE_PROTOCOL_ERROR, "malformed json")
    if not isinstance(obj, Mapping) or _is_bool(obj):
        _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
    return {str(key): value for key, value in obj.items()}


def validate_frame(msg: Mapping[str, Any]) -> dict[str, Any]:
    """Full strict per-type validation of a decoded frame.

    Returns the normalized frame dict (always a plain dict), or raises
    ``BridgeProtocolError``. ``pong`` accepts the minimal shape
    ``{"type":"pong"}``; every other message requires ``protocolVersion == 1``.
    """
    msg_type = msg.get("type")
    if msg_type not in MESSAGE_TYPES:
        _reject(CLOSE_UNSUPPORTED_TYPE, "unknown message type")
    if msg_type == MSG_PONG:
        allowed = {"type", "protocolVersion"}
        extra = set(msg) - allowed
        if extra:
            _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
        if "protocolVersion" in msg:
            _validate_protocol_version(msg)
        return {"type": MSG_PONG}
    if msg_type in (MSG_PING, MSG_JOB_CANCEL):
        _reject_unknown_keys(msg, {"protocolVersion", "type", "jobId"}, msg_type)
        _validate_protocol_version(msg)
        if msg_type == MSG_PING:
            return {"type": MSG_PING}
        job_id = _require_str(msg, "jobId", max_len=MAX_JOB_ID_LENGTH)
        return {"type": MSG_JOB_CANCEL, "jobId": job_id}
    _validate_protocol_version(msg)
    if msg_type == MSG_PAIRING_ACCEPTED:
        _reject_unknown_keys(
            msg,
            {
                "protocolVersion",
                "type",
                "bridgeSessionId",
                "bridgeSessionToken",
                "model",
                "capabilities",
            },
            "pairing_accepted",
        )
        session_id = _require_str(msg, "bridgeSessionId", max_len=MAX_BRIDGE_SESSION_ID_LENGTH)
        token = msg.get("bridgeSessionToken")
        if token is not None and (
            not isinstance(token, str)
            or len(token) < MIN_BRIDGE_TOKEN_LENGTH
            or len(token) > MAX_BRIDGE_TOKEN_LENGTH
        ):
            _reject(CLOSE_PROTOCOL_ERROR, "invalid frame")
        return {
            "type": MSG_PAIRING_ACCEPTED,
            "bridgeSessionId": session_id,
            "bridgeSessionToken": token,
            "model": _validate_model_label(msg.get("model")),
            "capabilities": _parse_capabilities(msg.get("capabilities")),
        }
    if msg_type == MSG_PAIRING_HELLO or msg_type == MSG_BRIDGE_HELLO:
        return _validate_handshake_schema(msg)
    if msg_type == MSG_JOB:
        return _validate_job_schema(msg)
    if msg_type == MSG_JOB_RESULT:
        return _validate_job_result_schema(msg)
    _reject(CLOSE_UNSUPPORTED_TYPE, "unknown message type")
    return {}  # pragma: no cover - unreachable


def encode_frame(payload: Mapping[str, Any]) -> str:
    """Deterministic compact JSON text for an outbound frame."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def pairing_hello_frame(*, pairing_code: str, model: str | None, capabilities: tuple[str, ...]) -> str:
    return encode_frame(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "type": MSG_PAIRING_HELLO,
            "pairingCode": pairing_code,
            "model": model,
            "capabilities": list(capabilities),
        }
    )


def bridge_hello_frame(*, bridge_session_token: str) -> str:
    return encode_frame(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "type": MSG_BRIDGE_HELLO,
            "bridgeSessionToken": bridge_session_token,
        }
    )


def job_result_success_frame(*, job_id: str, structured_output: Mapping[str, Any]) -> str:
    return encode_frame(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "type": MSG_JOB_RESULT,
            "jobId": job_id,
            "status": JOB_STATUS_SUCCESS,
            "structuredOutput": dict(structured_output),
        }
    )


def job_result_failed_frame(*, job_id: str, failure_code: str) -> str:
    return encode_frame(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "type": MSG_JOB_RESULT,
            "jobId": job_id,
            "status": JOB_STATUS_FAILED,
            "failureCode": failure_code,
        }
    )


def pong_frame() -> str:
    return encode_frame({"type": MSG_PONG})


__all__ = [
    "ALLOWED_CAPABILITIES",
    "AUTHORITATIVE_SCHEMA_IDS",
    "BRIDGE_FAILURE_CODES",
    "BRIDGE_MAX_MESSAGE_BYTES",
    "CAPABILITY_LIST_LOCAL_MODELS",
    "CAPABILITY_STRUCTURED_MODEL_INFERENCE",
    "CLOSE_IDLE_TIMEOUT",
    "CLOSE_MESSAGE_TOO_BIG",
    "CLOSE_POLICY_VIOLATION",
    "CLOSE_PROTOCOL_ERROR",
    "CLOSE_SERVER_ERROR",
    "CLOSE_UNSUPPORTED_TYPE",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_CAPABILITIES",
    "DEFAULT_MODEL",
    "DEFAULT_OLLAMA_URL",
    "DEFAULT_SERVER_URL",
    "HEARTBEAT_RESPOND_TIMEOUT_SECONDS",
    "IDLE_TIMEOUT_SECONDS",
    "JOB_ID_PREFIX",
    "JOB_STATUS_FAILED",
    "JOB_STATUS_SUCCESS",
    "JOB_STATUSES",
    "JOB_TYPE_STRUCTURED_INFERENCE",
    "JOB_TYPES",
    "MAX_BRIDGE_SESSION_ID_LENGTH",
    "MAX_BRIDGE_TOKEN_LENGTH",
    "MAX_CAPABILITIES",
    "MAX_CAPABILITY_LENGTH",
    "MAX_COLLECTION_LENGTH",
    "MAX_FAILURE_CODE_LENGTH",
    "MAX_JOB_ID_LENGTH",
    "MAX_JSON_DEPTH",
    "MAX_MODEL_LABEL_LENGTH",
    "MAX_PROMPT_BYTES",
    "MAX_PROMPT_CHARS",
    "MAX_RESPONSE_BYTES",
    "MAX_SCHEMA_BYTES",
    "MAX_SCHEMA_ID_LENGTH",
    "MESSAGE_TYPES",
    "MIN_BRIDGE_TOKEN_LENGTH",
    "MSG_BRIDGE_HELLO",
    "MSG_JOB",
    "MSG_JOB_CANCEL",
    "MSG_JOB_RESULT",
    "MSG_PAIRING_ACCEPTED",
    "MSG_PAIRING_HELLO",
    "MSG_PING",
    "MSG_PONG",
    "MODEL_LABEL_RE",
    "PAIRING_CODE_RE",
    "PROTOCOL_VERSION",
    "RECONNECT_BACKOFF_BASE_SECONDS",
    "RECONNECT_BACKOFF_CAP_SECONDS",
    "BridgeProtocolError",
    "bridge_hello_frame",
    "decode_frame",
    "encode_frame",
    "job_result_failed_frame",
    "job_result_success_frame",
    "pairing_hello_frame",
    "pong_frame",
    "validate_frame",
]