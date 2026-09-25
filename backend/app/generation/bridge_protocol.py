"""Phase 22 — the strict, versioned BYO-Ollama bridge job protocol (v1).

One authoritative definition of every WebSocket JSON frame in BOTH directions
(server <-> bridge). The bridge CLIENT track and the frontend track consume the
EXACT schemas documented here; the server and the test bridge harness share
``validate_frame`` so a malformed frame fails identically everywhere.

Protocol (v1; JSON text frames only):

- client->server unless noted:
    {"protocolVersion":1,"type":"bridge_hello","bridgeSessionToken":"..."}   (reconnect auth)
    {"protocolVersion":1,"type":"pairing_hello","pairingCode":"PD-XXXX-XXXX",
     "model":"hermes3:8b"?, "capabilities":["STRUCTURED_MODEL_INFERENCE"]?}
    {"protocolVersion":1,"type":"job_result","jobId":"JOB-...","status":
     "SUCCESS","structuredOutput":{...}}
    {"protocolVersion":1,"type":"job_result","jobId":"JOB-...",
     "status":"FAILED","failureCode":"LOCAL_MODEL_UNAVAILABLE"}
    {"protocolVersion":1,"type":"pong"}
- server->client:
    {"protocolVersion":1,"type":"pairing_accepted","bridgeSessionId":"...",
     "bridgeSessionToken":"...","model":"...",
     "capabilities":["STRUCTURED_MODEL_INFERENCE"],"protocolVersion":1}
       (the FIRST pairing returns the raw token exactly once; a RECONNECT
        confirmation returns the same frame WITHOUT the token — the bridge
        already holds it)
    {"protocolVersion":1,"type":"job","jobId":"JOB-...",
     "jobType":"STRUCTURED_INFERENCE","schemaId":"<authoritative schema id>",
     "model":"...","prompt":"...","temperature":0.1,"timeoutMs":120000}
    {"protocolVersion":1,"type":"job_cancel","jobId":"JOB-..."}
    {"protocolVersion":1,"type":"ping"}

Strictness rules (defense in depth on BOTH sides):

- top-level ``protocolVersion`` must equal 1 for every message except the
  bare ``pong`` (and ``ping`` when the peer echoes a minimum ping); any other
  value is rejected with close 1002 PROTOCOL_ERROR;
- unknown message types are rejected (close 1003 UNSUPPORTED_TYPE);
- unknown extra keys are rejected (closed schema);
- frames are depth-bounded with the SAME ``bounded_json_loads`` preflight as
  the application parser (a JSON nesting bomb raises a clean rejection);
- frame byte size is bounded by the WS endpoint (``BRIDGE_MAX_MESSAGE_BYTES``);
- every free-text field is length-bounded; the pairing code and model label
  are restricted to safe alphabets;
- ``job_result`` frames must reference a job the server actually dispatched to
  THIS connection; late/duplicate results are DISCARDED (never a state
  mutation — the server enforces that in the registry, not here).

Close codes (WS): 1001 idle, 1002 malformed/protocol-violation, 1003
unsupported message type, 1008 unauthorized/rejected/rate-limited, 1009 frame
too large, 1011 server internal. Reasons are SHORT sanitized tokens — never
prompt/truth/token material.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from app.assets.depthguard import bounded_json_loads

PROTOCOL_VERSION = 1

# -- closed message vocabulary -------------------------------------------------
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

# -- the single narrow capability + the optional discovery capability ---------
CAPABILITY_STRUCTURED_MODEL_INFERENCE = "STRUCTURED_MODEL_INFERENCE"
CAPABILITY_LIST_LOCAL_MODELS = "LIST_LOCAL_MODELS"
ALLOWED_CAPABILITIES: frozenset[str] = frozenset(
    {CAPABILITY_STRUCTURED_MODEL_INFERENCE, CAPABILITY_LIST_LOCAL_MODELS}
)
DEFAULT_CAPABILITIES: tuple[str, ...] = (CAPABILITY_STRUCTURED_MODEL_INFERENCE,)

# -- closed job vocabulary ----------------------------------------------------
JOB_TYPE_STRUCTURED_INFERENCE = "STRUCTURED_INFERENCE"
JOB_TYPES: frozenset[str] = frozenset({JOB_TYPE_STRUCTURED_INFERENCE})
JOB_STATUS_SUCCESS = "SUCCESS"
JOB_STATUS_FAILED = "FAILED"
JOB_STATUSES: frozenset[str] = frozenset({JOB_STATUS_SUCCESS, JOB_STATUS_FAILED})

# The server may receive a FAILED job result carrying one of these typed failure
# codes from the bridge (a sanitized closed vocabulary; the server maps it onto
# its own canonical GenerationFailureCode, never echoing arbitrary text).
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

# -- close codes ---------------------------------------------------------------
CLOSE_IDLE_TIMEOUT = 1001
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_UNSUPPORTED_TYPE = 1003
CLOSE_POLICY_VIOLATION = 1008
CLOSE_MESSAGE_TOO_BIG = 1009
CLOSE_SERVER_ERROR = 1011

# -- safe alphabets / bounds -----------------------------------------------------
# Code format ``PD-XXXX-XXXX`` over the base32 alphabet (upper-case A-Z0-9).
PAIRING_CODE_RE = re.compile(r"^PD-[A-Z2-7]{4}-[A-Z2-7]{4}$")
# Model label: the same safe operator token set as OLLAMA_MODEL.
MODEL_LABEL_RE = re.compile(r"[A-Za-z0-9._:\-]+")
MAX_MODEL_LABEL_LENGTH = 80
MAX_SCHEMA_ID_LENGTH = 64
MAX_PROMPT_CHARS = 120_000  # mirrors MAX_OLLAMA_PROMPT_CHARS
MAX_JOB_ID_LENGTH = 96
MAX_BRIDGE_SESSION_ID_LENGTH = 160
MAX_FAILURE_CODE_LENGTH = 64
MAX_CAPABILITIES = 8
MAX_CAPABILITY_LENGTH = 64
JOB_ID_PREFIX = "JOB-"
MIN_BRIDGE_TOKEN_LENGTH = 20
MAX_BRIDGE_TOKEN_LENGTH = 256

# Known authoritative schema ids the server may dispatch (the closed set; a job
# frame carrying any other schemaId is rejected on BOTH sides).
AUTHORITATIVE_SCHEMA_IDS: frozenset[str] = frozenset(
    {
        "CASE_PEOPLE_v1",
        "EVIDENCE_v1",
        "WORLD_REQUIREMENTS_v1",
        "ASSET_SPEC_v1",
        "REPAIR_v1",
        "ASSET_SPEC_REPAIR_v1",
        # Phase 19J — the ACTIVITY_LOG stage travels the SAME provider
        # abstraction as every other driver stage, so it must be dispatchable
        # to a Phase 22 bridge (the bridge receives the structured-inference
        # job with this schemaId; the raw structuredOutput is validated by the
        # EXACT same Phase 19J server validators as Ollama output).
        "ACTIVITY_LOG_v1",
        "ACTIVITY_LOG_REPAIR_v1",
    }
)


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


def generate_pairing_code() -> str:
    """A fresh ``PD-XXXX-XXXX`` pairing code (base32 alphabet, 40 bits).

    High-entropy enough for a 2-5 minute single-use lifetime because the WS
    handshake is rate-limited and a failed handshake closes the connection (a
    brute forcer must reconnect per attempt); the code itself is never a
    long-lived bearer credential.
    """
    import secrets

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    chars = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"PD-{chars[:4]}-{chars[4:]}"


def generate_bridge_session_id() -> str:
    """Opaque server-owned bridge session id (PS- prefix)."""
    import secrets

    return f"PS-{secrets.token_urlsafe(12)}"


def generate_job_id() -> str:
    """Opaque one-shot job id (JOB-<random>); globally unique per generation."""
    import secrets

    return f"JOB-{secrets.token_urlsafe(12)}"


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


def decode_frame(raw_text: str, *, max_bytes: int = 256 * 1024) -> dict[str, Any]:
    """Decode + depth-bound ONE WebSocket text frame into a plain dict.

    Raises ``BridgeProtocolError`` (with a close code + sanitized reason) for
    any oversized / malformed / deep frame; the caller closes the connection.
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


def pairing_accepted_frame(
    *,
    bridge_session_id: str,
    bridge_session_token: str | None,
    model: str | None,
    capabilities: tuple[str, ...],
) -> str:
    """The handshake-ack frame. ``bridge_session_token`` is included EXACTLY
    once (fresh pairing); a reconnect confirmation omits it (the bridge holds
    the token)."""
    payload: dict[str, Any] = {
        "protocolVersion": PROTOCOL_VERSION,
        "type": MSG_PAIRING_ACCEPTED,
        "bridgeSessionId": bridge_session_id,
        "model": model,
        "capabilities": list(capabilities),
    }
    if bridge_session_token is not None:
        payload["bridgeSessionToken"] = bridge_session_token
    return encode_frame(payload)


def job_frame(
    *,
    job_id: str,
    schema_id: str,
    model: str | None,
    prompt: str,
    temperature: float,
    timeout_ms: int,
) -> str:
    """One outbound STRUCTURED_INFERENCE job frame (server -> bridge)."""
    return encode_frame(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "type": MSG_JOB,
            "jobId": job_id,
            "jobType": JOB_TYPE_STRUCTURED_INFERENCE,
            "schemaId": schema_id,
            "model": model,
            "prompt": prompt,
            "temperature": temperature,
            "timeoutMs": timeout_ms,
        }
    )


def job_cancel_frame(job_id: str) -> str:
    return encode_frame(
        {"protocolVersion": PROTOCOL_VERSION, "type": MSG_JOB_CANCEL, "jobId": job_id}
    )


def ping_frame() -> str:
    return encode_frame({"protocolVersion": PROTOCOL_VERSION, "type": MSG_PING})


def pong_frame() -> str:
    return encode_frame({"type": MSG_PONG})


# Canonical GenerationStage.value -> authoritative schema id (Phase22 §9).
# The SAME ids are documented in the closed AUTHORITATIVE_SCHEMA_IDS set.
STAGE_TO_SCHEMA_ID: Mapping[str, str] = {
    "case_truth": "CASE_PEOPLE_v1",
    "evidence": "EVIDENCE_v1",
    "world_graph": "WORLD_REQUIREMENTS_v1",
    "asset_spec": "ASSET_SPEC_v1",
    "asset_spec_repair": "ASSET_SPEC_REPAIR_v1",
    "activity_log": "ACTIVITY_LOG_v1",
    "activity_log_repair": "ACTIVITY_LOG_REPAIR_v1",
    "repair": "REPAIR_v1",
}


def schema_id_for_stage(stage_value: str) -> str | None:
    """The authoritative schemaId for a ``GenerationStage.value`` (None when the
    stage is not bridged — the server then fails closed)."""
    return STAGE_TO_SCHEMA_ID.get(stage_value)


__all__ = [
    "ALLOWED_CAPABILITIES",
    "AUTHORITATIVE_SCHEMA_IDS",
    "BRIDGE_FAILURE_CODES",
    "CAPABILITY_LIST_LOCAL_MODELS",
    "CAPABILITY_STRUCTURED_MODEL_INFERENCE",
    "CLOSE_IDLE_TIMEOUT",
    "CLOSE_MESSAGE_TOO_BIG",
    "CLOSE_POLICY_VIOLATION",
    "CLOSE_PROTOCOL_ERROR",
    "CLOSE_SERVER_ERROR",
    "CLOSE_UNSUPPORTED_TYPE",
    "DEFAULT_CAPABILITIES",
    "JOB_STATUS_FAILED",
    "JOB_STATUS_SUCCESS",
    "JOB_STATUSES",
    "JOB_TYPE_STRUCTURED_INFERENCE",
    "JOB_TYPES",
    "MESSAGE_TYPES",
    "MSG_BRIDGE_HELLO",
    "MSG_JOB",
    "MSG_JOB_CANCEL",
    "MSG_JOB_RESULT",
    "MSG_PAIRING_ACCEPTED",
    "MSG_PAIRING_HELLO",
    "MSG_PING",
    "MSG_PONG",
    "PAIRING_CODE_RE",
    "PROTOCOL_VERSION",
    "STAGE_TO_SCHEMA_ID",
    "BridgeProtocolError",
    "decode_frame",
    "encode_frame",
    "generate_bridge_session_id",
    "generate_job_id",
    "generate_pairing_code",
    "job_cancel_frame",
    "job_frame",
    "pairing_accepted_frame",
    "ping_frame",
    "pong_frame",
    "schema_id_for_stage",
    "validate_frame",
]