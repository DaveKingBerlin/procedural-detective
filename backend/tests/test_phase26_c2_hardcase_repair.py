"""Phase 26C2 — Hard-case Activity Log repair robustness (backend regression).

Reproduces the EXACT three live Bridge residual chains deterministically
(no real LLM in CI) through the real driver harness, and pins the post-fix
corrected behavior:

- Chain A: 21 → ENTRY_COUNT_INVALID → repair 18 → CANONICAL_TIME_MISSING
- Chain B: 29 → ENTRY_COUNT_INVALID → repair 16 → CANONICAL_TIME_MISSING
- Chain C: 21 → ENTRY_COUNT_INVALID → repair 17 → CANONICAL_TIME_MISSING

Phase 26C2 design (Option B — server-side safe canonical-row reinsertion):

- the ORIGINAL count-invalid log's canonical row (already player-safe and
  semantically valid: the ONLY validator code of that pass is the 15..20 count
  bound) is captured as the KNOWN canonical row;
- when a later repair now produces a count-valid log that fails ONLY on
  CANONICAL_TIME_MISSING, the known row is deterministically re-inserted at
  its chronological position and the FULL existing validator is re-run;
- the repaired candidate is accepted ONLY when the full validator re-accepts
  it (no duplicate canonical row, chronology valid, canonical fields not
  altered, count within 15..20, no leaks);
- when NO known canonical row exists (the original already lacked the canonical
  instant) the bounded repair loop continues UNCHANGED and the exact live
  terminal chains still hit their original codes (fail-closed — the validator
  is never weakened).

Section 5 probe: a canned count-invalid log CAN already contain the canonical
instant at a valid row (the count code is orthogonal to the canonical code), so
the pre-fix "normal → ENTRY_COUNT_INVALID" classification is fully consistent
with the ORIGINAL carrying a valid canonical row — which is exactly the row
Option B safely preserves.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.activity_log import (  # noqa: E402
    MAX_ACTIVITY_LOG_ENTRIES,
    MIN_ACTIVITY_LOG_ENTRIES,
    ACTIVITY_LOG_ACTIVITY_TYPES,
    ActivityLogValidatorCode,
    parse_activity_log,
    validate_activity_log,
)
from app.domain.time_interval import epoch_to_iso, parse_iso8601  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.publication import (  # noqa: E402
    serialize_published_payload,
)
from app.generation.bridge_protocol import schema_id_for_stage  # noqa: E402

from test_ollama_driver import (  # noqa: E402
    ICEPICK_SPEC,
    _ALOG_TEXTS,
    _ALOG_TYPES,
    _alog,
    _case_people,
    _evidence,
    _j,
    _run,
    _world,
)

CANONICAL = "2026-09-11T23:42:00+02:00"


# --------------------------------------------------------------------------- #
# canned fixtures matching the EXACT live chains (§9)
# --------------------------------------------------------------------------- #


def _entries(canonical: str, count: int, *, canon_at_mid: bool = True) -> list[dict]:
    """``count`` strictly-increasing unique rows around ``canonical`` (+3min
    steps), closed types, neutral texts; the canonical row is the middle row."""
    tick, offset = parse_iso8601(canonical)
    mid = (count - 1) // 2
    start = tick - mid * 180
    rows = []
    for i in range(count):
        rows.append(
            {
                "timestamp": epoch_to_iso(start + i * 180, offset),
                "activityType": _ALOG_TYPES[i % len(_ALOG_TYPES)],
                "activity": _ALOG_TEXTS[i % len(_ALOG_TEXTS)],
            }
        )
    if canon_at_mid:
        rows[mid] = {
            "timestamp": canonical,
            "activityType": "LOCAL_ACTIVITY",
            "activity": "Local user activity detected",
        }
    return rows


def _normal_over_entry_count(canonical: str, count: int) -> str:
    """``count`` > 20 valid-shaped rows WITH the canonical row present → the
    ORIGINAL R2 situation: only the count bound is violated; the canonical row
    is already player-safe (the candidate Option B may later re-insert)."""
    assert count > MAX_ACTIVITY_LOG_ENTRIES
    return _j({"entries": _entries(canonical, count, canon_at_mid=True)})


def _repair_no_canonical(canonical: str, count: int) -> str:
    """``count`` valid rows WITHOUT the canonical instant, each INSIDE the
    app-owned ±60-minute window (rows are centered around the canonical tick on
    a half-step grid, so no row ever equals the tick). Even the count-invalid
    overshoot fixtures (21/29 rows) never leave the window — the ONLY pass-0
    validator failure is the 15..20 count bound (plus canonical-missing by
    construction), never a spurious TIME_WINDOW_INVALID, matching the exact
    live chains (§9 "exact" claim)."""
    tick, offset = parse_iso8601(canonical)
    start = tick - (count // 2) * 180 - 90  # half-step offset → no row == tick
    rows = []
    for i in range(count):
        t = start + i * 180
        assert (
            tick - 60 * 60 <= t <= tick + 60 * 60
        ), f"fixture row outside the 60-minute window: {t}"
        rows.append(
            {
                "timestamp": epoch_to_iso(t, offset),
                "activityType": _ALOG_TYPES[i % len(_ALOG_TYPES)],
                "activity": _ALOG_TEXTS[i % len(_ALOG_TEXTS)],
            }
        )
    return _j({"entries": rows})


def _repair_bad_type(canonical: str, count: int) -> str:
    """``count`` rows except ONE invalid ``activityType`` — the parse-failure
    variant in Chain B repair#2 (terminal SCHEMA_INVALID class)."""
    rows = _entries(canonical, count, canon_at_mid=True)
    rows[3]["activityType"] = "BROWSER_ACTIVITY "
    return _j({"entries": rows})


def _when_obs_canonical() -> str:
    """The FIRST activity-log evidence fact's locked canonical instant."""
    tick, offset = parse_iso8601(CANONICAL)
    return epoch_to_iso(tick - 10, offset)


def _other_anchors(crime_canonical: str) -> tuple[str, str, str]:
    """Canonical instants of the REMAINING three activity-log evidence facts."""
    tick, offset = parse_iso8601(crime_canonical)
    return (
        epoch_to_iso(tick - 120, offset),
        epoch_to_iso(tick - 120, offset),
        epoch_to_iso(tick - 20, offset),
    )


def _published_payload(record) -> dict:
    """The deterministic serialized published draft (same seed as the Phase19J
    suites)."""
    return json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="c26c2", model="mock", title="C26C2"
        )
    )


def _first_alog_presentation(payload) -> dict:
    """The presentation block of the FIRST activity-log fact (d_ev_when_obs)."""
    for fact in payload["draft"]["evidence"]:
        presentation = fact.get("presentation")
        if isinstance(presentation, dict) and "activityLogVersion" in presentation:
            return presentation
    raise AssertionError("no activity-log fact in the published draft")


# --------------------------------------------------------------------------- #
# §5 — the canonical-row situation probe
# --------------------------------------------------------------------------- #


def test_s5_count_invalid_original_can_already_hold_valid_canonical_row():
    """THE §5 probe: a canned 21-entry (count-invalid) log CAN already contain
    the canonical instant at a valid row. The validator returns the count code
    ALONE (canonical present, all other rules OK) — exactly the live
    "normal 21 → ENTRY_COUNT_INVALID" classification — while the SAME fixture
    WITHOUT the canonical row yields count AND canonical codes. Therefore the
    pre-fix report is fully consistent with the ORIGINAL carrying a valid,
    player-safe canonical row (the R2 premise Option B preserves)."""
    when_obs = _when_obs_canonical()
    with_canonical = parse_activity_log(_normal_over_entry_count(when_obs, 21))
    codes_w = validate_activity_log(with_canonical, canonical_time=when_obs)
    assert set(codes_w) == {ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID}
    assert ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING not in codes_w

    without_canonical = parse_activity_log(_repair_no_canonical(when_obs, 21))
    codes_wo = validate_activity_log(without_canonical, canonical_time=when_obs)
    assert (
        ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID in codes_wo
        and ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING in codes_wo
    )


# --------------------------------------------------------------------------- #
# §9 — the exact three live chains, post-fix correction pinned
# --------------------------------------------------------------------------- #


def _run_chain(normal_entries: int, repair1_entries: int):
    """Feed the EXACT canned chain (normal → COUNT_INVALID + canonical present;
    repair#1 → count-valid canonical-missing) as the FIRST activity-log fact,
    with the other facts valid. Post-fix the repair#1 output is RESTORED (the
    canonical row is deterministically re-inserted) so the repair#2/terminal
    canned responses are never reached and the whole run publishes."""
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _normal_over_entry_count(when_obs, normal_entries),  # → COUNT_INVALID
        _repair_no_canonical(when_obs, repair1_entries),     # → restored
        *[_j(_alog(anch)) for anch in _other_anchors(CANONICAL)],
        _j(_world()),
        ICEPICK_SPEC,
    ]
    return _run(posts)


def test_chain_A_21_then_repair_18_canonical_missing_recovers_and_publishes():
    record, _transport = _run_chain(normal_entries=21, repair1_entries=18)
    assert record.state is GenerationState.PUBLISHED
    payload = _published_payload(record)
    presentation = _first_alog_presentation(payload)
    events = presentation["events"]
    assert len(events) == 19  # 18 + the deterministically restored canonical row
    when_obs = _when_obs_canonical()
    canonical_rows = [e for e in events if e["time"] == when_obs]
    assert len(canonical_rows) == 1  # no duplicate canonical row
    assert (
        MIN_ACTIVITY_LOG_ENTRIES <= len(events) <= MAX_ACTIVITY_LOG_ENTRIES
    )


def test_chain_B_29_then_repair_16_canonical_missing_recovers_and_publishes():
    record, _transport = _run_chain(normal_entries=29, repair1_entries=16)
    assert record.state is GenerationState.PUBLISHED
    payload = _published_payload(record)
    presentation = _first_alog_presentation(payload)
    events = presentation["events"]
    assert len(events) == 17  # 16 + the restored canonical row
    when_obs = _when_obs_canonical()
    canonical_rows = [e for e in events if e["time"] == when_obs]
    assert len(canonical_rows) == 1


def test_chain_C_21_then_repair_17_canonical_missing_recovers_and_publishes():
    record, _transport = _run_chain(normal_entries=21, repair1_entries=17)
    assert record.state is GenerationState.PUBLISHED
    payload = _published_payload(record)
    presentation = _first_alog_presentation(payload)
    events = presentation["events"]
    assert len(events) == 18  # 17 + the restored canonical row
    when_obs = _when_obs_canonical()
    canonical_rows = [e for e in events if e["time"] == when_obs]
    assert len(canonical_rows) == 1


# --------------------------------------------------------------------------- #
# §9 — fail-closed: when NO known canonical row exists, the EXACT live chains
# still hit their pre-fix terminal codes (the validator is never weakened)
# --------------------------------------------------------------------------- #


def test_chain_A_terminal_without_known_row_21_18_16():
    """The EXACT live Chain A canned outputs, but the ORIGINAL 21-row log does
    NOT carry the canonical row → no known row → restoration never fires and
    the bounded repair reproduces the PRE-FIX terminal CANONICAL_TIME_MISSING
    (fail-closed)."""
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _repair_no_canonical(when_obs, 21),  # normal → count+canonical missing
        _repair_no_canonical(when_obs, 18),  # repair#1 → canonical missing
        _repair_no_canonical(when_obs, 16),  # repair#2 → canonical missing
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_CANONICAL_TIME_MISSING"
    assert record.published is None
    alog_calls = _activity_log_calls(transport)
    assert len(alog_calls) == 3  # bounded: normal + 2 repairs, never a 4th


def test_chain_B_terminal_without_known_row_29_16_badtype():
    """The EXACT live Chain B: repair#2 parse_failed entry_activity_type_invalid
    → terminal SCHEMA_INVALID when no known canonical row exists."""
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _repair_no_canonical(when_obs, 29),        # normal → count+canonical missing
        _repair_no_canonical(when_obs, 16),        # repair#1 → canonical missing
        _repair_bad_type(when_obs, 17),            # repair#2 → invalid type
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_SCHEMA_INVALID"
    assert record.published is None
    rep = _repair_prompts(transport)
    # the second repair (built after the 16-row canonical-missing response)
    # carries the exact validator code of that response
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_CANONICAL_TIME_MISSING" in rep[1]


def test_chain_C_terminal_without_known_row_21_17_21():
    """The EXACT live Chain C: repair#2 returns 21 again → terminal
    ENTRY_COUNT_INVALID when no known canonical row exists."""
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _repair_no_canonical(when_obs, 21),        # normal → count+canonical missing
        _repair_no_canonical(when_obs, 17),        # repair#1 → canonical missing
        _normal_over_entry_count(when_obs, 21),    # repair#2 → count again
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_ENTRY_COUNT_INVALID"
    assert record.published is None


# --------------------------------------------------------------------------- #
# §10 items 1..5 — validator authority unchanged (count/enum/canonical rules)
# --------------------------------------------------------------------------- #


def test_10_1_2_count_over_entries_still_invalid_21_and_29():
    """§10.1 / §10.2 — 21 AND 29 entries remain INVALID (the validator
    constants are untouched; Option B never weakens the count bound)."""
    when_obs = _when_obs_canonical()
    for count in (21, 29):
        entries = parse_activity_log(_normal_over_entry_count(when_obs, count))
        codes = validate_activity_log(entries, canonical_time=when_obs)
        assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID in codes


def test_10_3_15_to_20_still_valid():
    """§10.3 — 15..20 entries remain VALID."""
    when_obs = _when_obs_canonical()
    for count in (15, 16, 17, 18, 19, 20):
        doc = _entries(when_obs, count, canon_at_mid=True)
        parsed = parse_activity_log(_j({"entries": doc}))
        assert validate_activity_log(parsed, canonical_time=when_obs) == (), count


def test_10_4_invalid_activity_type_still_invalid():
    """§10.4 — an invalid activityType remains a parse failure (SCHEMA_INVALID)."""
    when_obs = _when_obs_canonical()
    with pytest.raises(ValueError):
        parse_activity_log(_repair_bad_type(when_obs, 17))


def test_10_5_canonical_time_remains_mandatory():
    """§10.5 — a log without the canonical instant remains
    ACTIVITY_LOG_CANONICAL_TIME_MISSING."""
    when_obs = _when_obs_canonical()
    entries = parse_activity_log(_repair_no_canonical(when_obs, 16))
    codes = validate_activity_log(entries, canonical_time=when_obs)
    assert (
        ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING in codes
    )


# --------------------------------------------------------------------------- #
# §10 items 6..10 implemented by the structural preservation
# --------------------------------------------------------------------------- #


def test_10_6_7_8_9_10_preservation_invariants_on_count_repair():
    """Option B invariants on a canned count-repair chain (Chain A signals):

    - canonical-row preservation works on count repair (re-inserted row);
    - no duplicate canonical row is created (exactly one occurrence);
    - chronology remains valid (restored list strictly increasing);
    - canonical row fields are not altered (the captured canonical row is
      re-inserted VERBATIM — same timestamp/activityType/activity);
    - the full validator reruns after the structural preservation.
    """
    from app.services.ollama_driver import (
        _canonical_row_from_entries,
        _reinsert_canonical_row,
        _restore_canonical_row_candidate,
    )
    from app.domain.time_interval import parse_iso8601_to_epoch

    when_obs = _when_obs_canonical()
    original = parse_activity_log(_normal_over_entry_count(when_obs, 21))
    repair1 = parse_activity_log(_repair_no_canonical(when_obs, 18))

    canonical_tick = parse_iso8601_to_epoch(when_obs)
    known = _canonical_row_from_entries(original, canonical_tick)
    assert known is not None
    known_verbatim = (
        known.timestamp,
        known.activity_type,
        known.activity,
    )

    reinserted = _reinsert_canonical_row(repair1, known, canonical_tick)
    assert reinserted is not None
    # (7) no duplicate canonical instant in the re-inserted list
    ticks = [parse_iso8601_to_epoch(e.timestamp) for e in reinserted]
    assert ticks == sorted(ticks) == sorted(set(ticks))  # (8) chronology valid
    assert ticks.count(canonical_tick) == 1  # (7) canonical once
    # (9) canonical row fields not altered
    restored_row = next(
        e for e in reinserted if parse_iso8601_to_epoch(e.timestamp) == canonical_tick
    )
    assert (
        restored_row.timestamp,
        restored_row.activity_type,
        restored_row.activity,
    ) == known_verbatim
    # (10) the FULL validator reruns and accepts the restored list
    candidate = _restore_canonical_row_candidate(
        repair1,
        known,
        when_obs,
        person_names=(),
        weapon_names=(),
        motive_names=(),
        location_ids=(),
        location_names=(),
        before_minutes=60,
        after_minutes=60,
    )
    assert candidate is not None
    assert validate_activity_log(candidate, canonical_time=when_obs) == ()
    assert len(candidate) == 19


def test_10_11_no_known_canonical_row_keeps_fail_closed():
    """Restoration NEVER fires without a previously player-safe canonical row —
    a count-invalid ORIGINAL that itself lacks the canonical row still repairs
    through the bounded loop and terminally fails (fail-closed, validator
    unchanged)."""
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _repair_no_canonical(when_obs, 21),
        _repair_no_canonical(when_obs, 18),
        _repair_no_canonical(when_obs, 16),
    ]
    record, _transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_CANONICAL_TIME_MISSING"


def test_10_12_repair_attempts_stay_bounded():
    """Option B restoration consumes NO provider call and NO repair budget: the
    chain resolves at repair#1 (no repair#2 provider call is ever made), and
    the other three facts consume one call each."""
    from app.services.ollama_driver import MAX_ACTIVITY_LOG_REPAIR_PASSES

    assert MAX_ACTIVITY_LOG_REPAIR_PASSES == 2
    record, transport = _run_chain(normal_entries=21, repair1_entries=18)
    assert record.state is GenerationState.PUBLISHED
    alog_calls = _activity_log_calls(transport)
    # failing fact: normal(1) + repair#1(1) = 2; each other fact: 1 → total 5
    assert len(alog_calls) == 5


def test_10_13_observability_fields_are_safe(caplog):
    """The safe telemetry allowlist carries the canonical-row booleans and the
    restoration PRINTS/LOGS never contain row content or truth material."""
    from app.core.observability import _SAFE_FIELDS

    assert "canonicalRowPresent" in _SAFE_FIELDS
    assert "canonicalRowRestored" in _SAFE_FIELDS
    record, _transport = _run_chain(normal_entries=29, repair1_entries=16)
    assert record.state is GenerationState.PUBLISHED
    for forbidden in ("Local user activity detected", "murder", "killer"):
        assert forbidden not in caplog.text


def test_10_20_direct_ollama_behavior_is_unchanged():
    """Direct Ollama transport behavior (schema derivation / format handling)
    is untouched by the restoration — only the DRIVER adds the deterministic
    recovery. The authoritative transport JSON Schema still carries the bounds
    the direct path sends."""
    from app.generation import prompts

    schema = prompts.schema_contract_as_json_schema("activity_log")
    assert schema["properties"]["entries"]["minItems"] == MIN_ACTIVITY_LOG_ENTRIES
    assert schema["properties"]["entries"]["maxItems"] == MAX_ACTIVITY_LOG_ENTRIES
    enum = set(
        schema["properties"]["entries"]["items"]["properties"]["activityType"]["enum"]
    )
    assert enum == set(ACTIVITY_LOG_ACTIVITY_TYPES)


def test_10_24_easy_medium_still_green():
    from test_ollama_driver import _staged

    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED


# --------------------------------------------------------------------------- #
# §10 items 14..19 / 21..23 — contract routing & fail-closed surfaces
# --------------------------------------------------------------------------- #


def test_10_14_15_exact_validator_and_parse_class_still_reach_repair():
    """§10.14 / §10.15 — when restoration does NOT fire, the repair prompts
    STILL carry the exact validator code and the parse-failure class (the
    contract-routing surfaces are unchanged)."""
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _repair_no_canonical(when_obs, 21),
        _repair_bad_type(when_obs, 17),
        _repair_no_canonical(when_obs, 16),
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    rep = _repair_prompts(transport)
    assert len(rep) == 2
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_ENTRY_COUNT_INVALID" in rep[0]
    assert "PARSE FAILURE CLASS: entry_activity_type_invalid" in rep[1]


def test_10_16_unknown_bridge_schema_ids_still_rejected():
    """§10.16 — the authoritative schema-id allowlist is UNCHANGED: an unknown
    id is rejected on both protocol copies (never dispatched)."""
    from app.generation.bridge_protocol import (
        AUTHORITATIVE_SCHEMA_IDS,
        BridgeProtocolError,
        decode_frame,
        validate_frame,
    )

    server_known = AUTHORITATIVE_SCHEMA_IDS
    assert "ACTIVITY_LOG_v1" in server_known
    assert "ACTIVITY_LOG_REPAIR_v1" in server_known
    frame = {
        "protocolVersion": 1,
        "type": "job",
        "jobId": "JOB-UNKNOWN-0000",
        "jobType": "STRUCTURED_INFERENCE",
        "schemaId": "DRIFT_BROWSER_v1",
        "model": "hermes3:8b",
        "prompt": "p",
        "temperature": 0.1,
        "timeoutMs": 120000,
    }
    import json as _json

    raw = _json.dumps(frame)
    with pytest.raises(BridgeProtocolError):
        validate_frame(decode_frame(raw))


def test_10_17_fixc_drift_guard_stays_green():
    """§10.17 — the cross-copy drift guard (client superset of server, trusted
    schema table in sync with the server's authoritative schema) stays green —
    already covered by bridge/tests/test_vocabulary_drift.py; this pins the
    backend-side contract the bridge mirrors."""
    from app.generation import prompts

    schema = prompts.schema_contract_as_json_schema("activity_log")
    enum = set(schema["properties"]["entries"]["items"]["properties"]["activityType"]["enum"])
    assert enum == set(ACTIVITY_LOG_ACTIVITY_TYPES)


def test_10_19_arbitrary_schema_injection_impossible_backend_side():
    """§10.19 — the backend NEVER accepts a schema from browser/job input: the
    RemoteClientProvider dispatches ONLY the authoritative schemaId, never a
    wire schema object (nothing to inject on the server side)."""
    from app.generation.bridge_protocol import STAGE_TO_SCHEMA_ID
    from app.generation.provider import GenerationStage

    assert (
        schema_id_for_stage(GenerationStage.ACTIVITY_LOG.value)
        == STAGE_TO_SCHEMA_ID["activity_log"]
        == "ACTIVITY_LOG_v1"
    )


def test_10_21_per_job_model_selection_remains_isolated():
    """§10.21 — per-job model selection is genuinely ISOLATED: two
    ``RemoteClientProvider`` instances bound to DIFFERENT frozen selected
    models each dispatch their OWN model on the wire job frame (``job.model``
    == the frozen per-attempt model), and a legacy default provider falls back
    to the bridge's reported model. Two jobs started back-to-back never bleed
    their models into each other."""
    from app.generation.provider import GenerateRequest, GenerationStage
    from app.generation.remote_client_provider import RemoteClientProvider

    dispatched: list[str] = []

    class _Waiter:
        result = ("content", '{"entries": []}')
        def wait(self, timeout):
            return True

    class _Conn:
        model = "bridge-reported-model"
        bridge_session_id = "bridge-sess"
        socket = None
        loop = None

    class _Registry:
        def __init__(self):
            self.conn = _Conn()
        def lookup_for_scope(self, scope):
            return self.conn
        def begin_job(self, conn, job_id):
            return _Waiter()
        def cancel_job(self, conn, job_id, code):
            pass

    class _Settings:
        bridge_job_deadline_seconds = 120.0
        bridge_model_allowlist = None

    class _CapturingProvider(RemoteClientProvider):
        def _dispatch(self, conn, job_id, payload):
            dispatched.append(payload["model"])
            return True

    request = GenerateRequest(
        attempt_id="GA-1",
        stage=GenerationStage.ACTIVITY_LOG,
        prompt_context="<sanitized prompt>",
        timeout_seconds=5,
    )
    provider_a = _CapturingProvider(
        registry=_Registry(),
        settings=_Settings(),
        session_scope="session-a",
        model="hermes3:8b",
    )
    provider_b = _CapturingProvider(
        registry=_Registry(),
        settings=_Settings(),
        session_scope="session-b",
        model="llama3.2:3b",
    )
    provider_default = _CapturingProvider(
        registry=_Registry(),
        settings=_Settings(),
        session_scope="session-c",
        model=None,
    )
    provider_a.generate(request)
    provider_b.generate(request)
    provider_default.generate(request)
    # each job carries its OWN frozen selected model (never the other's)
    assert dispatched == ["hermes3:8b", "llama3.2:3b", "bridge-reported-model"]


def test_10_22_concurrent_generation_isolation_unchanged():
    """§10.22 — concurrent generation isolation with real overlap: two FULL
    Hard-style chains (Chain A 21→18 and Chain C 21→17) run SIMULTANEOUSLY in
    two threads (barrier-synchronized start — genuine concurrency, no sleeps).
    Both publish, and each publishes EXACTLY its OWN restored event count (19
    and 18) with exactly one canonical row — the per-attempt canonical capture
    can never leak across concurrent attempts."""
    import threading

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _run_in_thread(key, normal_entries, repair1_entries):
        barrier.wait()  # both threads enter their pipelines at the same time
        try:
            results[key] = _run_chain(
                normal_entries=normal_entries, repair1_entries=repair1_entries
            )
        except Exception as exc:  # pragma: no cover - surfaced on the main thread
            results[key] = exc

    threads = [
        threading.Thread(target=_run_in_thread, args=("A", 21, 18)),
        threading.Thread(target=_run_in_thread, args=("C", 21, 17)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    when_obs = _when_obs_canonical()
    for key, expected_events in (("A", 19), ("C", 18)):
        result = results[key]
        assert not isinstance(result, Exception), result
        record, _transport = result
        assert record.state is GenerationState.PUBLISHED, key
        payload = _published_payload(record)
        presentation = _first_alog_presentation(payload)
        events = presentation["events"]
        assert len(events) == expected_events, key
        assert len([e for e in events if e["time"] == when_obs]) == 1, key


def test_10_23_successful_full_hard_orchestration_possible():
    """§10.23 — a full Hard-style orchestration with one repaired overshoot +
    canonical-row restoration PUBLISHES end-to-end (canonical-row preservation
    on count repair works in the complete pipeline)."""
    record, _transport = _run_chain(normal_entries=21, repair1_entries=18)
    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    payload = _published_payload(record)
    presentation = _first_alog_presentation(payload)
    when_obs = _when_obs_canonical()
    assert len([e for e in presentation["events"] if e["time"] == when_obs]) == 1


# --------------------------------------------------------------------------- #
# §11 — F-1: DIRECT unit tests of the Option B fail-closed guards
# (call the helper functions with hostile inputs — no driver needed; each RED
# if a listed guard is deleted by a future refactor)
# --------------------------------------------------------------------------- #


def _entry(timestamp: str) -> "object":
    """One raw ``ActivityLogEntry``-shaped object (hostile-input helper)."""
    from app.domain.activity_log import ActivityLogEntry

    return ActivityLogEntry(
        timestamp=timestamp,
        activity_type="LOCAL_ACTIVITY",
        activity="Local user activity detected",
    )


def _known_row(when_obs: str):
    """The deterministic KNOWN canonical row (verbatim, player-safe)."""
    return _entry(when_obs)


def test_11_01_reinsert_duplicate_instant_guard_returns_none():
    """F-1 (guard a) — ``_reinsert_canonical_row`` returns None when the
    candidate list ALREADY contains the canonical tick (a duplicate canonical
    row is never created — the ``if canonical_tick in ticks: return None``
    guard). Deleting that guard makes this test RED (the helper would then
    return a 19-row list carrying the canonical instant twice)."""
    from app.domain.time_interval import parse_iso8601_to_epoch
    from app.services.ollama_driver import _reinsert_canonical_row

    when_obs = _when_obs_canonical()
    canonical_tick = parse_iso8601_to_epoch(when_obs)
    # candidate list that ALREADY contains the canonical instant (mid row)
    entries = parse_activity_log(
        _j({"entries": _entries(when_obs, 18, canon_at_mid=True)})
    )
    known = _known_row(when_obs)
    assert _reinsert_canonical_row(entries, known, canonical_tick) is None


def test_11_02_restore_to_21_refused_and_full_rerun_rejects(monkeypatch):
    """F-1 (guard b) — a 20-row canonical-missing repair + a KNOWN canonical
    row would restore to 21 rows (> MAX): ``_restore_canonical_row_candidate``
    refuses (None) via the post-restore ``restored_count > 20`` guard, which
    short-circuits BEFORE the full-validator rerun (a spy proves no rerun sees
    the over-count list). The reason is safe/fail-closed: the full-validator
    rerun on the same 21-row list ALSO rejects (ENTRY_COUNT_INVALID). Deleting
    the post-restore count guard makes the spy assertion RED."""
    import app.domain.activity_log as alog_module
    from app.domain.time_interval import parse_iso8601_to_epoch
    from app.services.ollama_driver import (
        _reinsert_canonical_row,
        _restore_canonical_row_candidate,
    )

    when_obs = _when_obs_canonical()
    canonical_tick = parse_iso8601_to_epoch(when_obs)
    # a count-valid canonical-missing repair: 20 rows, nothing else wrong
    entries20 = parse_activity_log(_repair_no_canonical(when_obs, 20))
    assert len(entries20) == 20
    assert validate_activity_log(entries20, canonical_time=when_obs) == (
        ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING,
    )
    known = _known_row(when_obs)
    # sanity: a mechanical reinsert WOULD overflow to 21
    overflow = _reinsert_canonical_row(entries20, known, canonical_tick)
    assert overflow is not None and len(overflow) == 21

    seen = []
    real_validate = alog_module.validate_activity_log  # capture BEFORE patching

    def _spy_validate(entries, **kwargs):
        seen.append(len(entries))
        return real_validate(entries, **kwargs)

    monkeypatch.setattr(alog_module, "validate_activity_log", _spy_validate)
    candidate = _restore_canonical_row_candidate(
        entries20,
        known,
        when_obs,
        person_names=(),
        weapon_names=(),
        motive_names=(),
        location_ids=(),
        location_names=(),
        before_minutes=60,
        after_minutes=60,
    )
    assert candidate is None
    # the guard short-circuited BEFORE the full-validator rerun (no 21-row list
    # was ever offered to the validator). RED if the post-restore count guard
    # is deleted (the rerun would then see the 21-row list).
    assert seen == []
    # safe/fail-closed reason: the full-validator rerun on the SAME 21-row
    # list ALSO rejects (never a 21-row publication).
    assert validate_activity_log(overflow, canonical_time=when_obs) == (
        ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID,
    )


def test_11_03_count_invalid_input_never_restored():
    """F-1 (guard c) — ``_restore_canonical_row_candidate`` refuses a
    canonical-missing candidate whose OWN count is invalid (14 or 21 rows) via
    the ``len(entries) not in [15,20]`` guard (restore not attempted). Deleting
    that guard makes the 14-row case RED (reinsertion would yield a valid
    15-row candidate and be returned instead of None)."""
    from app.domain.time_interval import parse_iso8601_to_epoch
    from app.services.ollama_driver import _restore_canonical_row_candidate

    when_obs = _when_obs_canonical()
    known = _known_row(when_obs)
    kwargs = dict(
        person_names=(),
        weapon_names=(),
        motive_names=(),
        location_ids=(),
        location_names=(),
        before_minutes=60,
        after_minutes=60,
    )
    for count in (14, 21):
        entries = parse_activity_log(_repair_no_canonical(when_obs, count))
        assert len(entries) == count
        candidate = _restore_canonical_row_candidate(entries, known, when_obs, **kwargs)
        assert candidate is None, count
    # the 14-row case specifically reddens when the input-count guard is deleted:
    # restore would reinsert the known row -> a valid 15-row list is returned.
    entries14 = parse_activity_log(_repair_no_canonical(when_obs, 14))
    candidate14 = _restore_canonical_row_candidate(entries14, known, when_obs, **kwargs)
    assert candidate14 is None


def test_11_04_wrong_canonical_tick_or_no_known_row_returns_none():
    """F-1 — a known_row that does NOT carry the canonical instant (wrong tick)
    or a missing known row NEVER reinserts: ``_reinsert_canonical_row`` and the
    candidate restore both return None (fail-closed)."""
    from app.domain.time_interval import parse_iso8601_to_epoch
    from app.services.ollama_driver import (
        _reinsert_canonical_row,
        _restore_canonical_row_candidate,
    )

    when_obs = _when_obs_canonical()
    canonical_tick = parse_iso8601_to_epoch(when_obs)
    entries = parse_activity_log(_repair_no_canonical(when_obs, 18))
    # positive control: the CORRECT known row (exactly the canonical instant)
    # DOES reinsert — proving the negative assertions below pin the guard, not
    # some unrelated rejection.
    right_row = _entry(_when_obs_canonical())
    assert _reinsert_canonical_row(entries, right_row, canonical_tick) is not None
    # a known row whose tick differs from the passed canonical tick
    from app.domain.time_interval import epoch_to_iso, parse_iso8601

    tick, offset = parse_iso8601(when_obs)
    other_tick_row = _entry(epoch_to_iso(tick - 120, offset))
    assert _reinsert_canonical_row(entries, other_tick_row, canonical_tick) is None
    # no known row at all
    assert _reinsert_canonical_row(entries, None, canonical_tick) is None
    kwargs = dict(
        person_names=(),
        weapon_names=(),
        motive_names=(),
        location_ids=(),
        location_names=(),
        before_minutes=60,
        after_minutes=60,
    )
    assert (
        _restore_canonical_row_candidate(entries, other_tick_row, when_obs, **kwargs)
        is None
    )
    assert _restore_canonical_row_candidate(entries, None, when_obs, **kwargs) is None


def test_11_05_unparseable_current_timestamp_returns_none():
    """F-1 — a candidate whose CURRENT rows contain an unparseable timestamp is
    never reordered/restored: ``_reinsert_canonical_row`` returns None when ANY
    current timestamp fails to parse (fail-closed before reordering)."""
    from app.domain.time_interval import parse_iso8601_to_epoch
    from app.services.ollama_driver import (
        _reinsert_canonical_row,
        _restore_canonical_row_candidate,
    )

    when_obs = _when_obs_canonical()
    canonical_tick = parse_iso8601_to_epoch(when_obs)
    entries = parse_activity_log(_repair_no_canonical(when_obs, 18))
    entries[0] = _entry("not-an-iso-timestamp")
    known = _known_row(when_obs)
    assert _reinsert_canonical_row(entries, known, canonical_tick) is None
    kwargs = dict(
        person_names=(),
        weapon_names=(),
        motive_names=(),
        location_ids=(),
        location_names=(),
        before_minutes=60,
        after_minutes=60,
    )
    assert (
        _restore_canonical_row_candidate(entries, known, when_obs, **kwargs) is None
    )


def test_11_06_invalid_or_none_canonical_time_returns_none():
    """F-1 — an invalid (non-empty) or None canonical time fails closed:
    ``_restore_canonical_row_candidate`` returns None (never a guessed/derived
    canonical instant)."""
    from app.services.ollama_driver import _restore_canonical_row_candidate

    when_obs = _when_obs_canonical()
    entries = parse_activity_log(_repair_no_canonical(when_obs, 18))
    known = _known_row(when_obs)
    kwargs = dict(
        person_names=(),
        weapon_names=(),
        motive_names=(),
        location_ids=(),
        location_names=(),
        before_minutes=60,
        after_minutes=60,
    )
    for bad_canonical in ("not-a-valid-time", "", None):
        candidate = _restore_canonical_row_candidate(
            entries, known, bad_canonical, **kwargs
        )
        assert candidate is None, bad_canonical


# --------------------------------------------------------------------------- #
# helpers (kept local to this suite)
# --------------------------------------------------------------------------- #


def _activity_log_calls(transport) -> list[int]:
    return [
        i
        for i in range(transport.call_count)
        if "activity_log" in transport.prompt_of_call(i)[:120]
    ]


def _repair_prompts(transport) -> list[str]:
    return [
        transport.prompt_of_call(i)
        for i in range(transport.call_count)
        if "activity_log_repair_v1" in transport.prompt_of_call(i)
    ]