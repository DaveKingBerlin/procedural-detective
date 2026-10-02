"""Fix C — cross-copy protocol-vocabulary drift guard (generalized).

The bridge job protocol is authored ONCE (``app.generation.bridge_protocol`` on
the server track) but shipped in TWO physical copies:

  - server:  ``backend/app/generation/bridge_protocol.py``
  - client:  ``bridge/pd_ollama_bridge/protocol.py``

Phase 19J grew the SERVER copy's ``AUTHORITATIVE_SCHEMA_IDS`` with
``ACTIVITY_LOG_v1`` / ``ACTIVITY_LOG_REPAIR_v1`` but the CLIENT copy was not
updated, so a server-dispatched ``ACTIVITY_LOG_v1`` job frame was rejected by
the client as a protocol violation (close 1002) and the THIRD sequential
remote-client job failed ``BRIDGE_DISCONNECTED`` (Fix C).

F1 (accepted): that defect class is not limited to schema ids. EVERY closed
protocol vocabulary is duplicated across the two copies — message types
(MESSAGE_TYPES / MSG_*), job types and result statuses (JOB_TYPES /
JOB_STATUSES / token constants), failure codes (BRIDGE_FAILURE_CODES),
capabilities (ALLOWED_CAPABILITIES / CAPABILITY_* / DEFAULT_CAPABILITIES), the
protocol version, the WS close-code constants, and the shared prompt/field
bounds (MAX_PROMPT_CHARS / MAX_PROMPT_BYTES). They are EQUAL today, but a
future one-copy growth in any of them would reproduce the Fix C failure mode
("server dispatches, client rejects, BRIDGE_DISCONNECTED") with CI green.

This module therefore:

  1. computes the set of module-level names declared in BOTH copies and sweeps
     every shared non-callable public constant with STRUCTURAL sorted equality
     (frozenset/set/tuple/dict/regex/int/str);
  2. FAILS on any drift with a sanitized message listing only the drifted
     NAMES (the constants are public vocab tokens — no secret values);
  3. keeps the schema-id-specific assertions (client superset of server, and
     the Phase 19J ids dispatchable on both sides);
  4. proves by in-process mutation that a constant differing in only ONE copy
     is always detected (import both modules, mutate one copy's constant
     in-memory, run the compare, assert failure; no file changes persist).
"""

from __future__ import annotations

import re
import sys
import typing
from pathlib import Path

import pytest

# The bridge tests run from ``bridge/`` (pyproject pythonpath=["."]); the server
# copy lives in the sibling ``backend`` tree — add it to sys.path for the import.
_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from pd_ollama_bridge import protocol as client_protocol  # noqa: E402
from app.generation import bridge_protocol as server_protocol  # noqa: E402


# --------------------------------------------------------------------------- #
# generalized structural-equality sweep
# --------------------------------------------------------------------------- #


def _is_shared_constant(value: object) -> bool:
    """A shared protocol constant = a public value of protocol shape.

    The closed wire vocabulary is made of str/int tokens, frozenset/set/tuple
    (or list/dict) containers of them, and the two compiled regex alphabets.
    Functions, classes, modules and Python/typing plumbing (``Any``,
    ``Mapping``, the ``__future__`` ``annotations`` feature) are NOT part of
    the vocabularies both copies declare, so they are excluded from the sweep.
    """
    if isinstance(value, (set, frozenset, tuple, list)):
        return all(_is_shared_constant(item) for item in value)
    if isinstance(value, dict):
        return all(
            _is_shared_constant(k) and _is_shared_constant(v)
            for k, v in value.items()
        )
    if isinstance(value, re.Pattern):
        return True
    return isinstance(value, (str, int, bytes, type(None)))


def shared_constant_names() -> tuple[str, ...]:
    """The union of names declared as public protocol constants in BOTH copies.

    Computed dynamically (intersection of module namespaces) so a new shared
    protocol constant is swept automatically instead of requiring the allowlist
    to be hand-extended. The per-copy helper/implementation names fall out
    naturally because they are callables/classes (excluded above).
    """
    names = set(dir(client_protocol)) & set(dir(server_protocol))
    names = {
        name
        for name in names
        if not name.startswith("_")
        and _is_shared_constant(getattr(client_protocol, name))
    }
    return tuple(sorted(names))


def _normalize(value: object) -> object:
    """Structural sorted-equality normal form for one protocol constant."""
    if isinstance(value, re.Pattern):
        return ("REGEX", value.pattern, int(value.flags))
    if isinstance(value, (set, frozenset, list)):
        return tuple(sorted(_normalize(item) for item in value))
    if isinstance(value, tuple):
        return tuple(_normalize(item) for item in value)
    if isinstance(value, typing.Mapping):
        return tuple(
            sorted((_normalize(k), _normalize(v)) for k, v in value.items())
        )
    return value  # int / str / None — compared directly


def find_drifted_names() -> list[str]:
    """Names whose VALUE differs between the two copies (empty == identical)."""
    drifted: list[str] = []
    for name in shared_constant_names():
        client_value = getattr(client_protocol, name)
        server_value = getattr(server_protocol, name)
        if _normalize(client_value) != _normalize(server_value):
            drifted.append(name)
    return drifted


def assert_no_drift() -> None:
    """Assert the two copies declare identical values for every shared
    protocol constant. Failure lists ONLY the drifted NAMES (public vocab
    tokens; no secret/value leakage)."""
    drifted = find_drifted_names()
    assert not drifted, (
        "cross-copy protocol-vocabulary drift: the server copy "
        "(app.generation.bridge_protocol) and the bridge client copy "
        "(pd_ollama_bridge.protocol) differ for: "
        + ", ".join(sorted(drifted))
    )


def test_all_shared_protocol_constants_structurally_identical():
    """F1 — the generalized drift guard: EVERY shared protocol constant between
    the two physical copies is structurally identical (schema ids, message/job/
    result types, failure codes, capabilities, protocol version, close codes and
    the shared prompt/field bounds). FAILS on drift with the drifted NAMES."""
    names = shared_constant_names()
    # The sweep is actually covering the whole shared vocabulary surface.
    assert {"AUTHORITATIVE_SCHEMA_IDS", "MESSAGE_TYPES", "JOB_TYPES",
            "JOB_STATUSES", "BRIDGE_FAILURE_CODES", "ALLOWED_CAPABILITIES",
            "DEFAULT_CAPABILITIES", "PROTOCOL_VERSION",
            "CLOSE_PROTOCOL_ERROR", "MAX_PROMPT_BYTES",
            "MAX_PROMPT_CHARS"}.issubset(set(names))
    assert_no_drift()


# --------------------------------------------------------------------------- #
# one-copy mutation simulations (in-process; nothing is persisted)
# --------------------------------------------------------------------------- #

_MUTATIONS: list[tuple[str, object]] = [
    ("AUTHORITATIVE_SCHEMA_IDS", "DRIFT_SCHEMA_v1"),
    ("MESSAGE_TYPES", "drift_message_type"),
    ("MSG_JOB", "drift_job_echo"),
    ("JOB_TYPES", "DRIFT_JOB_TYPE"),
    ("JOB_STATUSES", "DRIFT_STATUS"),
    ("BRIDGE_FAILURE_CODES", "DRIFT_FAILURE"),
    ("ALLOWED_CAPABILITIES", "DRIFT_CAPABILITY"),
    ("DEFAULT_CAPABILITIES", "DRIFT_CAPABILITY"),
    ("PROTOCOL_VERSION", 2),
    ("CLOSE_IDLE_TIMEOUT", 1001 + 1),
    ("CLOSE_PROTOCOL_ERROR", 1002 + 1),
    ("MAX_PROMPT_BYTES", 120_000 - 1),
    ("MAX_PROMPT_CHARS", 120_000 + 1),
]


def _apply_drift(original: object, sentinel: object) -> object:
    """Return a drifted value of the SAME type as the shared constant."""
    if isinstance(original, set):
        return set(original) | {sentinel}
    if isinstance(original, frozenset):
        return frozenset(original | {sentinel})
    if isinstance(original, tuple):
        return (*original, sentinel)
    if isinstance(original, dict):
        return {**original, f"_drift_{sentinel}": sentinel}
    if isinstance(original, bool):
        return not original
    if isinstance(original, int):
        return int(original) + 1
    if isinstance(original, str):
        return f"{original}_drift"
    return sentinel


@pytest.mark.parametrize("name,sentinel", _MUTATIONS, ids=[n for n, _ in _MUTATIONS])
def test_drift_detected_when_only_client_copy_mutated(monkeypatch, name, sentinel):
    """Mutating ONLY the client copy of ANY shared constant must make the
    generalized guard FAIL and name that constant."""
    original = getattr(client_protocol, name)
    monkeypatch.setattr(client_protocol, name, _apply_drift(original, sentinel))
    drifted = find_drifted_names()
    assert name in drifted, f"client-only drift of {name} was not detected"
    with pytest.raises(AssertionError):
        assert_no_drift()


@pytest.mark.parametrize("name,sentinel", _MUTATIONS, ids=[n for n, _ in _MUTATIONS])
def test_drift_detected_when_only_server_copy_mutated(monkeypatch, name, sentinel):
    """Mutating ONLY the server copy of ANY shared constant must make the
    generalized guard FAIL and name that constant (the Fix C direction:
    server grew, client did not)."""
    original = getattr(server_protocol, name)
    monkeypatch.setattr(server_protocol, name, _apply_drift(original, sentinel))
    drifted = find_drifted_names()
    assert name in drifted, f"server-only drift of {name} was not detected"
    with pytest.raises(AssertionError):
        assert_no_drift()


def test_server_only_schema_growth_reproduces_fixc_defect_class(monkeypatch):
    """The EXACT pre-Fix-C scenario as a drift: the SERVER copy carries an
    extra schema id the client copy never learned about. The generalized guard
    fails naming AUTHORITATIVE_SCHEMA_IDS — the 'server dispatches, client
    rejects, BRIDGE_DISCONNECTED' defect class can never go CI-green again."""
    grown = frozenset(server_protocol.AUTHORITATIVE_SCHEMA_IDS) | {"DRIFT_SCHEMA_v1"}
    monkeypatch.setattr(server_protocol, "AUTHORITATIVE_SCHEMA_IDS", grown)
    drifted = find_drifted_names()
    assert "AUTHORITATIVE_SCHEMA_IDS" in drifted


# --------------------------------------------------------------------------- #
# schema-id-specific assertions (kept from the original Fix C guard)
# --------------------------------------------------------------------------- #


def test_client_schema_ids_superset_of_server():
    """The client's closed job-schema vocabulary must be a SUPERSET of (or
    equal to) the server's — no server-dispatchable schema may be unknown to
    the client (that asymmetry produced the Fix C 1002)."""
    server_ids = set(server_protocol.AUTHORITATIVE_SCHEMA_IDS)
    client_ids = set(client_protocol.AUTHORITATIVE_SCHEMA_IDS)
    missing = server_ids - client_ids
    assert not missing, (
        "client AUTHORITATIVE_SCHEMA_IDS drifted from the server copy: "
        f"server ids missing on the client: {sorted(missing)}"
    )
    assert client_ids >= server_ids
    # The two Phase 19J ids that triggered Fix C are present on BOTH sides.
    assert "ACTIVITY_LOG_v1" in client_ids
    assert "ACTIVITY_LOG_REPAIR_v1" in client_ids


def test_phase19j_activity_log_ids_are_dispatchable():
    """The exact ids the server dispatches for the THIRD sequential job are in
    both vocabularies (server dispatch surface + client validation surface)."""
    assert "ACTIVITY_LOG_v1" in server_protocol.AUTHORITATIVE_SCHEMA_IDS
    assert "ACTIVITY_LOG_v1" in client_protocol.AUTHORITATIVE_SCHEMA_IDS
    assert server_protocol.STAGE_TO_SCHEMA_ID["activity_log"] == "ACTIVITY_LOG_v1"  # noqa: E501
    assert server_protocol.STAGE_TO_SCHEMA_ID["activity_log_repair"] == (
        "ACTIVITY_LOG_REPAIR_v1"
    )


# --------------------------------------------------------------------------- #
# F2 — the symmetric prompt BYTE bound is part of the shared vocabulary and is
# enforced by BOTH copies with the same close code.
# --------------------------------------------------------------------------- #


def test_max_prompt_bytes_is_a_symmetric_shared_bound():
    """F2: ``MAX_PROMPT_BYTES`` is declared in BOTH copies with the same value
    (a symmetric protocol bound). A job frame whose prompt exceeds it must be
    rejected by BOTH copies with the SAME close code 1002."""
    assert client_protocol.MAX_PROMPT_BYTES == server_protocol.MAX_PROMPT_BYTES
    assert client_protocol.MAX_PROMPT_BYTES == client_protocol.MAX_PROMPT_CHARS
    assert server_protocol.MAX_PROMPT_BYTES == server_protocol.MAX_PROMPT_CHARS

    # A prompt of multi-byte characters whose UTF-8 length EXCEEDS the cap while
    # the character count stays under MAX_PROMPT_CHARS (so only the byte cap
    # can bind). The whole frame stays well under BRIDGE_MAX_MESSAGE_BYTES, so
    # the frame-size bound (1009) does not fire first — the byte cap does.
    per_char = len("界".encode("utf-8"))  # 3 bytes
    over = "界" * (client_protocol.MAX_PROMPT_BYTES // per_char + 1)
    assert len(over) <= client_protocol.MAX_PROMPT_CHARS  # char bound passes
    assert len(over.encode("utf-8")) > client_protocol.MAX_PROMPT_BYTES
    frame = {
        "protocolVersion": 1,
        "type": "job",
        "jobId": "JOB-F2",
        "jobType": "STRUCTURED_INFERENCE",
        "schemaId": "ASSET_SPEC_v1",
        "model": "hermes3:8b",
        "prompt": over,
        "temperature": 0.1,
        "timeoutMs": 120000,
    }
    raw = client_protocol.encode_frame(frame)
    for module in (client_protocol, server_protocol):
        with pytest.raises(module.BridgeProtocolError) as excinfo:
            module.validate_frame(module.decode_frame(raw, max_bytes=256 * 1024))
        assert excinfo.value.close_code == module.CLOSE_PROTOCOL_ERROR

    # A prompt at/under the byte cap is accepted by BOTH copies.
    ok = "a" * (client_protocol.MAX_PROMPT_BYTES - 1)
    frame_ok = {**frame, "prompt": ok}
    raw_ok = client_protocol.encode_frame(frame_ok)
    for module in (client_protocol, server_protocol):
        decoded = module.validate_frame(
            module.decode_frame(raw_ok, max_bytes=256 * 1024)
        )
        assert decoded["prompt"] == ok


def test_frame_size_bound_still_dominates_both_copies():
    """F2: adding the symmetric prompt byte cap did NOT weaken the frame-size
    bound — a whole frame exceeding the transport bound is still rejected with
    close 1009 (never reaching per-field prompt checks) in BOTH copies."""
    oversized = client_protocol.encode_frame(
        {
            "protocolVersion": 1,
            "type": "ping",
            "pad": "a" * 300 * 1024,
        }
    )
    for module in (client_protocol, server_protocol):
        with pytest.raises(module.BridgeProtocolError) as excinfo:
            module.decode_frame(oversized, max_bytes=256 * 1024)
        assert excinfo.value.close_code == module.CLOSE_MESSAGE_TOO_BIG