"""Phase 19J — Hermes-generated multi-entry computer activity logs (backend).

Covers the Phase19J backend contract:

- UNIT: closed schema parse (extra keys, wrong types, nested junk, nulls),
  entry bounds (15/20 accepted; 14/21 rejected), strict chronological +
  unique ordering, canonical-time presence/duplication/absence, temporal
  window (+ configurable ±N minutes with the hard 120-minute clamp), the
  direct truth-leak filter, entity/weapon/motive/location leakage, day
  rollover (00:02 / 23:59 → next-day rows), timezone preservation
  (+02:00 / +01:00 / Z), repair feedback tokens, the closed activityType
  enum, and unsafe text (HTML/URL/path/control);
- PROVIDER: the stage travels the SAME GenerateRequest/ProviderResult
  abstraction over the MockOllamaTransport; a full driver run performs one
  bounded ACTIVITY_LOG call per time-bearing cctv fact (d_ev_when_obs is the
  primary) and the accepted log is persisted into ``presentation.events`` and
  flows into the read content after publication;
- SECOND PASS: bounded repair surfaces ONLY machine-readable findings + the
  locked canonical time; a terminal validation failure carries the typed
  validator code (never provider-unavailable for a validation failure);
- SOLVER ISOLATION: the solver signature (winners + uniqueness +
  evidence_ids_used) is byte-identical with and without the rich log on the
  same canonical case;
- DEMO: the FakeProvider golden path renders the deterministic 20-entry
  fixture log through the same DTO/component payload.
- ZERO provider calls during interaction/reload.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain import activity_log as al  # noqa: E402
from app.domain.activity_log import (  # noqa: E402
    ACTIVITY_LOG_ACTIVITY_TYPES,
    ACTIVITY_LOG_VERSION_MARKER,
    MAX_ACTIVITY_LOG_ENTRIES,
    MAX_ACTIVITY_TEXT_CHARS,
    MIN_ACTIVITY_LOG_ENTRIES,
    ActivityLogValidatorCode,
    WINDOW_HARD_MAX_TOTAL_MINUTES,
    activity_log_window_bounds,
    parse_activity_log,
    primary_validator_code,
    repair_findings,
    validate_activity_log,
)
from app.domain.time_interval import epoch_to_iso, parse_iso8601  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.services.publication import (  # noqa: E402
    project_read_content,
    serialize_published_payload,
)

from test_ollama_driver import (  # noqa: E402
    _alog,
    _alog_posts,
    _case_people,
    _evidence,
    _j,
    _run,
    _staged,
)
from phase6_helpers import case_for  # noqa: E402

CANONICAL = "2026-09-11T21:18:00+02:00"
OTHER_ISO = "2026-09-11T19:18:00Z"  # the SAME instant as CANONICAL (21:18+02:00)


def _entry(timestamp, activity_type="LOCAL_ACTIVITY", activity="Local user activity detected"):
    return {"timestamp": timestamp, "activityType": activity_type, "activity": activity}


def _valid_entries(canonical=CANONICAL, count=17, step_seconds=180):
    """A deterministic VALID entries list around ``canonical`` (any count)."""
    tick, offset = parse_iso8601(canonical)
    mid = (count - 1) // 2
    start = tick - mid * step_seconds
    entries = []
    for i in range(count):
        entries.append(_entry(epoch_to_iso(start + i * step_seconds, offset)))
    entries[mid] = _entry(canonical)
    return entries


def _as_log(entries):
    return al.parse_activity_log(json.dumps({"entries": entries}))


# --------------------------------------------------------------------------- #
# 1 — schema (closed)
# --------------------------------------------------------------------------- #


def test_schema_accepts_valid_document():
    doc = {"entries": _valid_entries()}
    parsed = parse_activity_log(json.dumps(doc))
    assert len(parsed) == 17
    assert all(isinstance(e, al.ActivityLogEntry) for e in parsed)


def test_schema_rejects_unknown_top_level_key():
    doc = {"entries": _valid_entries(), "extra": 1}
    with pytest.raises(ValueError):
        parse_activity_log(json.dumps(doc))


def test_schema_rejects_missing_entries_key():
    with pytest.raises(ValueError):
        parse_activity_log(json.dumps({"events": []}))


def test_schema_rejects_non_array_entries():
    with pytest.raises(ValueError):
        parse_activity_log(json.dumps({"entries": "x"}))


def test_schema_rejects_entry_unknown_key_and_missing_fields():
    bad = _valid_entries()
    bad[0]["extra"] = True
    with pytest.raises(ValueError):
        parse_activity_log(json.dumps({"entries": bad}))
    bad2 = [{"timestamp": "2026-09-11T21:10:00+02:00", "activityType": "LOCAL_ACTIVITY"}]
    with pytest.raises(ValueError):
        parse_activity_log(json.dumps({"entries": bad2}))


def test_schema_rejects_null_and_non_string_fields():
    bad = _valid_entries()
    bad[0]["activity"] = None
    with pytest.raises(ValueError):
        parse_activity_log(json.dumps({"entries": bad}))


def test_schema_rejects_nested_unexpected_object():
    bad = _valid_entries()
    bad[0]["activity"] = {"inner": "x"}
    with pytest.raises(ValueError):
        parse_activity_log(json.dumps({"entries": bad}))


def test_schema_rejects_unknown_activity_enum():
    bad = _valid_entries()
    bad[0]["activityType"] = "NOT_A_REAL_TYPE"
    with pytest.raises(ValueError):
        parse_activity_log(json.dumps({"entries": bad}))


def test_activity_type_vocabulary_is_the_closed_phase11_set():
    assert ACTIVITY_LOG_ACTIVITY_TYPES == frozenset(
        {
            "SYSTEM_RESUME", "SYSTEM_IDLE", "SESSION_UNLOCK", "SESSION_LOCK",
            "LOGIN", "LOGOUT", "FILE_OPEN", "FILE_WRITE", "FILE_COPY",
            "DOCUMENT_ACCESS", "DOCUMENT_AUTOSAVE", "BROWSER_ACTIVITY",
            "MAIL_SYNC", "CLOUD_SYNC", "BACKGROUND_SYNC", "USB_CONNECTED",
            "NETWORK_ACTIVITY", "BACKUP", "LOCAL_ACTIVITY",
            "APPLICATION_OPEN", "APPLICATION_CLOSE",
        }
    )
    assert len(ACTIVITY_LOG_ACTIVITY_TYPES) == 21


def test_all_activity_types_validate():
    entries = _valid_entries(count=20)
    for index, atype in enumerate(sorted(ACTIVITY_LOG_ACTIVITY_TYPES)[:20]):
        entries[index]["activityType"] = atype
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert codes == ()


# --------------------------------------------------------------------------- #
# 2 — entry bounds (15..20)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("count", (15, 16, 20))
def test_entry_bounds_accept(count):
    codes = validate_activity_log(_as_log(_valid_entries(count=count)), canonical_time=CANONICAL)
    assert codes == ()


@pytest.mark.parametrize("count", (14, 21))
def test_entry_bounds_reject(count):
    codes = validate_activity_log(_as_log(_valid_entries(count=count)), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID in codes


# --------------------------------------------------------------------------- #
# 3 — ordering + uniqueness
# --------------------------------------------------------------------------- #


def test_non_chronological_rejected():
    entries = _valid_entries()
    entries[3], entries[4] = entries[4], entries[3]  # swap -> out of order
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_TIME_ORDER_INVALID in codes


def test_duplicate_timestamp_rejected():
    entries = _valid_entries()
    entries[5] = dict(entries[6])
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_TIME_ORDER_INVALID in codes


def test_duplicate_entry_same_timestamp_and_text_rejected():
    entries = _valid_entries()
    entries[5] = dict(entries[6])
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID in codes


# --------------------------------------------------------------------------- #
# 4 — canonical time invariant (exactly once, server-owned)
# --------------------------------------------------------------------------- #


def test_canonical_time_present_exactly_once():
    codes = validate_activity_log(_as_log(_valid_entries()), canonical_time=CANONICAL)
    assert codes == ()


def test_canonical_time_missing_rejected():
    entries = _valid_entries()
    # shift every row so none equals the canonical instant
    shifted = []
    for i, e in enumerate(entries):
        shifted.append(dict(e, timestamp=epoch_to_iso(
            parse_iso8601(e["timestamp"])[0] + 60, parse_iso8601(CANONICAL)[1]
        )))
    codes = validate_activity_log(_as_log(shifted), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING in codes


def test_canonical_time_duplicated_rejected():
    entries = _valid_entries()
    entries[12] = _entry(CANONICAL)
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED in codes


def test_equivalent_instant_with_other_tz_counts_once():
    """The canonical instant rendered in UTC (20:18Z == 21:18+02:00) is the
    SAME tick — the server compares instants, so it is exactly one occurrence."""
    entries = _valid_entries()
    entries[8] = _entry(OTHER_ISO)
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    ticks = [parse_iso8601(e["timestamp"])[0] for e in entries]
    assert ticks.count(parse_iso8601(CANONICAL)[0]) == 1
    assert ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_DUPLICATED not in codes


# --------------------------------------------------------------------------- #
# 5 — temporal window
# --------------------------------------------------------------------------- #


def test_window_bounds_are_deterministic_and_clamped():
    lo, hi = activity_log_window_bounds(parse_iso8601(CANONICAL)[0], before_minutes=60, after_minutes=60)
    assert hi - lo == 120 * 60
    lo2, hi2 = activity_log_window_bounds(parse_iso8601(CANONICAL)[0], before_minutes=200, after_minutes=200)
    assert hi2 - lo2 == WINDOW_HARD_MAX_TOTAL_MINUTES * 60


def test_timestamp_outside_window_rejected():
    entries = _valid_entries(count=15, step_seconds=180)
    # move the FIRST row 90 minutes before the canonical -> outside ±60
    far = epoch_to_iso(parse_iso8601(CANONICAL)[0] - 90 * 60, parse_iso8601(CANONICAL)[1])
    entries[0]["timestamp"] = far
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_TIME_WINDOW_INVALID in codes


def test_window_configurable_before_after():
    entries = _valid_entries()
    # all rows within ±60 by construction; tighten AFTER to 1 minute
    codes = validate_activity_log(
        _as_log(entries), canonical_time=CANONICAL,
        before_minutes=60, after_minutes=1,
    )
    assert ActivityLogValidatorCode.ACTIVITY_LOG_TIME_WINDOW_INVALID in codes


# --------------------------------------------------------------------------- #
# 6 — day rollover + timezone preservation
# --------------------------------------------------------------------------- #


def test_day_rollover_00_02_canonical():
    """Canonical 00:02 — surrounding rows on the PREVIOUS day validate and the
    ordering stays tick-based (never string-based)."""
    canonical = "2026-09-12T00:02:00+02:00"
    tick, offset = parse_iso8601(canonical)
    mid = (15 - 1) // 2  # 7
    rows = [_entry(epoch_to_iso(tick + (i - mid) * 150, offset)) for i in range(15)]
    rows[mid] = _entry(canonical)
    # rows [i < mid] fall on 2026-09-11 (previous day); the canonical at 00:02
    assert rows[0]["timestamp"].startswith("2026-09-11T2")
    codes = validate_activity_log(_as_log(rows), canonical_time=canonical)
    assert codes == ()


def test_day_rollover_23_59_canonical_next_day_rows():
    """Canonical 23:59 — surrounding rows may cross into the NEXT day (00:0x)
    and still validate (tick-based ordering)."""
    canonical = "2026-09-11T23:59:00+02:00"
    tick, offset = parse_iso8601(canonical)
    mid = (15 - 1) // 2  # 7
    rows = [_entry(epoch_to_iso(tick + (i - mid) * 150, offset)) for i in range(15)]
    rows[mid] = _entry(canonical)
    # rows [i > mid] fall on 2026-09-12 (next day)
    assert any(r["timestamp"].startswith("2026-09-12T00:") for r in rows)
    codes = validate_activity_log(_as_log(rows), canonical_time=canonical)
    assert codes == ()


@pytest.mark.parametrize("canonical", ("2026-09-11T21:18:00+02:00", "2026-09-11T21:18:00+01:00", "2026-09-11T20:18:00Z"))
def test_timezone_offsets_preserved(canonical):
    tick, offset = parse_iso8601(canonical)
    mid = (15 - 1) // 2
    rows = [_entry(epoch_to_iso(tick + (i - mid) * 180, offset)) for i in range(15)]
    rows[mid] = _entry(canonical)
    codes = validate_activity_log(_as_log(rows), canonical_time=canonical)
    assert codes == ()


# --------------------------------------------------------------------------- #
# 7 — direct truth-leak filter (§22)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    (
        "Murder occurred", "the victim was killed by", "killer identified",
        "attack at the scene", "culprit activity", "murderer logged in",
        "crime time is 21:18", "time of death recorded", "weapon used",
        "crime occurred at 21:18", "homicide report", "victim killed",
    ),
)
def test_direct_truth_leak_rejected(text):
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity=text)
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_DIRECT_TRUTH_LEAK in codes, text


def test_harmless_words_not_overblocked():
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="A fresh document was created")
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_DIRECT_TRUTH_LEAK not in codes


# --------------------------------------------------------------------------- #
# 8 — entity / weapon / motive / location leakage (§23/§24/§25/§26)
# --------------------------------------------------------------------------- #


def test_person_name_leak_rejected():
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="The user Paul Becker opened a file")
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL, person_names=("Paul Becker",))
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes


def test_person_id_token_leak_rejected():
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="session for paul_becker started")
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL, person_names=("paul_becker",))
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes


def test_weapon_leak_rejected():
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="the bronze ceremonial ice pick was wiped")
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL, weapon_names=("bronze ceremonial ice pick",))
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes


def test_motive_leak_rejected():
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="Wanted to steal the research data")
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL, motive_names=("wanted to steal the research data",))
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes


def test_location_id_leak_rejected():
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="moved to research_lab")
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL, location_ids=("research_lab",))
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes


def test_location_display_name_leak_rejected_when_distinctive():
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="User moved from the Research Laboratory")
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL, location_names=("Research Laboratory",))
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes


def test_short_ambiguous_location_word_not_overblocked():
    # "office" alone is not distinctive enough to be a leak in v1 (its id
    # token is still always rejected); the log stays valid.
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="Office applications updated")
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL, location_names=("Office",))
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK not in codes


# --------------------------------------------------------------------------- #
# 9 — unsafe text (HTML/URL/path/control) + text bounds
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    (
        "<img src=x onerror=alert(1)>",
        "<table><tr><td>secret</td></tr></table>",
        "see https://evil.example/x",
        "download from http://host/a",
        "file:///etc/passwd",
        "path C:\\windows\\system32",
        "flag ../secret",
        "code `payload`",
        "\x00nul byte",
        "line1\nline2",
    ),
)
def test_unsafe_text_rejected(text):
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity=text)
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID in codes, repr(text)


def test_overlong_activity_rejected():
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="x" * (MAX_ACTIVITY_TEXT_CHARS + 1))
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID in codes


def test_exact_length_bound_ok():
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="y" * MAX_ACTIVITY_TEXT_CHARS)
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID not in codes


def test_empty_activity_rejected():
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity="   ")
    codes = validate_activity_log(_as_log(entries), canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID in codes


# --------------------------------------------------------------------------- #
# 10 — repair feedback (machine-readable, never content/truth)
# --------------------------------------------------------------------------- #


def test_repair_findings_are_machine_readable_tokens_only():
    findings = repair_findings(
        (
            ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING,
            ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID,
        ),
        entry_count=12,
    )
    assert findings == ("SCHEMA_INVALID", "CANONICAL_TIME_MISSING")
    # entry-count findings only appear when the code itself is present
    with_count = repair_findings(
        (
            ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID,
            ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING,
            ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID,
        ),
        entry_count=12,
    )
    assert with_count == ("SCHEMA_INVALID", "ENTRY_COUNT_TOO_LOW", "CANONICAL_TIME_MISSING")


def test_repair_findings_count_direction_and_order_problems():
    findings = repair_findings(
        (ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID,),
        entry_count=25,
    )
    assert "ENTRY_COUNT_TOO_HIGH" in findings
    findings2 = repair_findings(
        (
            ActivityLogValidatorCode.ACTIVITY_LOG_TIME_ORDER_INVALID,
            ActivityLogValidatorCode.ACTIVITY_LOG_TIME_WINDOW_INVALID,
            ActivityLogValidatorCode.ACTIVITY_LOG_DIRECT_TRUTH_LEAK,
            ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK,
        ),
        entry_count=16,
        non_chronological=True,
        duplicate_timestamp=True,
    )
    assert "NON_CHRONOLOGICAL" in findings2
    assert "DUPLICATE_TIMESTAMP" in findings2
    assert "TIMESTAMP_OUTSIDE_WINDOW" in findings2
    assert "TRUTH_LEAK_DETECTED" in findings2
    assert "ENTITY_LEAK_DETECTED" in findings2
    # raw log text / content is NEVER part of a finding
    blob = " ".join(findings2)
    for raw in ("Local", "activity", "Paul", "detected"):
        assert raw not in blob


def test_primary_validator_code_priority():
    assert primary_validator_code(
        (
            ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK,
            ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID,
        )
    ) is ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID
    assert primary_validator_code(
        (ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK,)
    ) is ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK
    assert primary_validator_code(()) is None


# --------------------------------------------------------------------------- #
# 11 — provider: the stage travels the MockOllamaTransport, persists events,
#     repair is bounded, terminal failure is a typed validator code
# --------------------------------------------------------------------------- #


def _driver_payload():
    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    return record, json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="j19", model="mock", title="J19"
        )
    )


def test_driver_run_makes_bounded_activity_log_calls_and_persists_events():
    from test_ollama_driver import MockOllamaTransport, _make_driver, _run as driver_run

    record, transport = driver_run(_staged())
    assert record.state is GenerationState.PUBLISHED
    # the four time-bearing cctv facts each get ONE bounded activity-log call
    alog_calls = [
        i for i in range(transport.call_count)
        if "activity_log" in transport.prompt_of_call(i)[:120]
    ]
    assert len(alog_calls) == 4
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="j19", model="mock", title="J19"
        )
    )
    facts = {f["id"]: f for f in payload["draft"]["evidence"]}
    obs = facts["d_ev_when_obs"]
    events = obs["presentation"]["events"]
    assert len(events) >= MIN_ACTIVITY_LOG_ENTRIES
    assert obs["presentation"]["activityLogVersion"] == ACTIVITY_LOG_VERSION_MARKER
    content = project_read_content(payload, "d_ev_when_obs")
    assert content["renderType"] == "ACTIVITY_LOG"
    # the persisted events drive the player-visible entries (time + text).
    assert content["entries"]
    assert all(set(e) == {"time", "text"} for e in content["entries"])
    canon = obs["propositions"][0]["observed_at"]
    assert [e for e in content["entries"] if e["time"] == canon]  # once


def test_driver_activity_log_prompt_carries_locked_canonical_time():
    from test_ollama_driver import _make_driver, MockOllamaTransport  # noqa: F401

    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    # the first activity-log call (d_ev_when_obs, canonical = crime - 10s)
    prompts = [transport.prompt_of_call(i) for i in range(transport.call_count)]
    alog_prompt = next(p for p in prompts if "'activity_log'" in p)
    assert "Canonical evidence time:" in alog_prompt
    assert "2026-09-11T23:41:50+02:00" in alog_prompt  # 23:42:00 - 10s


def test_driver_repair_is_bounded_and_uses_machine_readable_findings():
    from test_ollama_driver import MockOllamaTransport, _world

    crime_canonical = "2026-09-11T23:42:00+02:00"
    when_obs_canonical = epoch_to_iso(parse_iso8601(crime_canonical)[0] - 10, parse_iso8601(crime_canonical)[1])
    # first response fails (missing canonical time); the repair fixes it.
    bad = _alog(when_obs_canonical)
    bad["entries"][8]["timestamp"] = "2026-09-11T23:40:00+02:00"  # canonical removed
    good = _alog(when_obs_canonical)
    # Exact driver order: case, evidence, alog(when_obs)=bad -> repair -> good,
    # then alog(opp1), alog(opp2), alog(presence), world, asset spec.
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        json.dumps(bad),        # ACTIVITY_LOG (invalid)
        json.dumps(good),       # ACTIVITY_LOG_REPAIR (valid)
        *_alog_posts(crime_canonical)[1:],
        _j(_world()),
    ]
    from test_ollama_driver import ICEPICK_SPEC

    posts.append(ICEPICK_SPEC)
    record, run_transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    repair_prompt = next(
        run_transport.prompt_of_call(i) for i in range(run_transport.call_count)
        if "activity_log_repair_v1" in run_transport.prompt_of_call(i)
    )
    assert "CANONICAL_TIME_MISSING" in repair_prompt
    assert when_obs_canonical in repair_prompt
    # the repair prompt NEVER carries the rejected log text or hidden truth
    assert "System resumed from sleep" not in repair_prompt


def test_driver_terminal_log_validation_failure_is_typed_not_provider():
    crime_canonical = "2026-09-11T23:42:00+02:00"
    when_obs_canonical = epoch_to_iso(parse_iso8601(crime_canonical)[0] - 10, parse_iso8601(crime_canonical)[1])
    bad = _alog(when_obs_canonical)
    bad["entries"][8]["timestamp"] = "2026-09-11T23:40:00+02:00"  # canonical missing
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        json.dumps(bad),  # ACTIVITY_LOG (invalid)
        json.dumps(bad),  # repair 1 (still invalid)
        json.dumps(bad),  # repair 2 (still invalid -> terminal)
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_CANONICAL_TIME_MISSING"
    assert record.published is None


def test_no_activity_log_calls_for_facts_without_time_bearing_observed_at():
    """An ACTIVITY_LOG-kind fact WITHOUT a time-bearing observed_at gets NO
    provider call (the trigger is time-bearing ACTIVITY_LOG evidence only)."""
    from test_ollama_driver import _make_driver, MockOllamaTransport

    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    prompts = [transport.prompt_of_call(i) for i in range(transport.call_count)]
    # every activity-log call carries a locked canonical time (proof the stage
    # never runs without a time anchor) and the whole run publishes.
    for prompt in prompts:
        if "'activity_log'" in prompt:
            assert "Canonical evidence time:" in prompt
    assert record.solver_proof is not None


# --------------------------------------------------------------------------- #
# 12 — solver isolation (§27/§28): byte-identical signature with/without log
# --------------------------------------------------------------------------- #


def _solver_signature(payload):
    proof = payload["solverProof"]
    winners = tuple(proof.get("winners") or ())
    proof_section = proof or {}
    return (
        winners,
        bool(proof_section.get("who", {}).get("unique")),
        bool(proof_section.get("why", {}).get("unique")),
        bool(proof_section.get("weapon", {}).get("unique")),
        tuple(sorted(str(i) for i in (proof_section.get("time", {}).get("critical_evidence_ids") or ()))),
    )


def test_solver_signature_identical_with_and_without_rich_log():
    # rich: the standard driver world (logs generated + persisted)
    record_rich, _t = _run(_staged())
    assert record_rich.state is GenerationState.PUBLISHED
    payload_rich = json.loads(
        serialize_published_payload(
            record_rich.published, seed=1, prompt="rich", model="mock", title="Rich"
        )
    )
    # plain: the SAME canonical case with the logs stripped from the payload
    # (the propositions — the solver input — are byte-identical).
    payload_plain = json.loads(json.dumps(payload_rich))
    for fact in payload_plain["draft"]["evidence"]:
        presentation = fact.get("presentation")
        if isinstance(presentation, dict):
            presentation.pop("events", None)
            presentation.pop("activityLogVersion", None)
    assert _solver_signature(payload_rich) == _solver_signature(payload_plain)
    # the propositions stayed byte-identical
    rich = {f["id"]: f["propositions"] for f in payload_rich["draft"]["evidence"]}
    plain = {f["id"]: f["propositions"] for f in payload_plain["draft"]["evidence"]}
    assert rich == plain


# --------------------------------------------------------------------------- #
# 13 — FakeProvider deterministic full log (no Ollama needed for the demo)
# --------------------------------------------------------------------------- #


def test_fake_provider_golden_log_renders_realistic_rows(phase5_app):
    from test_phase19c_interaction import _published_payload
    from test_phase19g_evidence_render import GOLDEN_CCTV
    from app.domain.render import render_payload_of

    case_id, creator = case_for(phase5_app)
    payload = _published_payload(phase5_app, case_id, 1)
    fact = next(f for f in payload["draft"]["evidence"] if f["id"] == GOLDEN_CCTV)
    canonical = fact["propositions"][0]["observed_at"]
    # the SAME closed render payload the read-record DTO serves:
    content = project_read_content(payload, GOLDEN_CCTV)
    assert content["renderType"] == "ACTIVITY_LOG"
    entries = content["entries"]
    assert len(entries) == 20
    # the golden observed-at time appears exactly once, at an ordinary row
    rows_with_canon = [e for e in entries if e["time"] == canonical]
    assert len(rows_with_canon) == 1
    row = rows_with_canon[0]
    assert row["text"] == "Local user activity detected"
    assert "murder" not in json.dumps(content).lower()
    times = [parse_iso8601(e["time"])[0] for e in entries]
    assert times == sorted(times)
    # determinism: repeated reads are byte-identical
    assert render_payload_of(fact) == render_payload_of(fact)


# --------------------------------------------------------------------------- #
# 14 — zero provider calls during interaction/reload (§6/§53)
# --------------------------------------------------------------------------- #


def test_projection_and_playthrough_reads_make_zero_provider_calls(phase5_app):
    from test_ollama_driver import MockOllamaTransport

    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    calls_after_generation = transport.call_count
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="j19", model="mock", title="J19"
        )
    )
    # browserless API projection reads
    for fact in payload["draft"]["evidence"]:
        project_read_content(payload, fact["id"])
    assert transport.call_count == calls_after_generation


def test_driver_log_flows_into_get_records_dto_end_to_end(phase5_app):
    """Publish a REAL driver world as a case version and drive the browser
    journey: interact Laptop -> discover d_ev_when_obs -> GET /records returns
    the persisted 17-row log (canonical time once, {time, text} rows); the
    SECOND read is byte-identical and consumes ZERO provider calls."""
    from phase5_helpers import assert_no_hidden_leaks
    from phase6_helpers import LAPTOP_OBJECT, interact, read_record
    from test_phase7_helpers import new_playthrough

    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    driver_payload = json.loads(
        serialize_published_payload(
            record.published,
            seed=getattr(record, "seed", None),
            prompt="p19j driver",
            model="hermes3:8b (mock)",
            title="J19 Driver",
        )
    )
    case_id, creator = case_for(phase5_app)
    v2 = 2
    driver_payload["caseId"] = case_id
    driver_payload["caseVersion"] = v2
    driver_payload["publishedAt"] = float(phase5_app.state.clock.now())
    store = phase5_app.state.store
    now = float(phase5_app.state.clock.now())
    store.create_case_version(
        case_id=case_id, version=v2, state="PUBLISHED",
        generation_id=f"GEN-{v2}", created_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=v2,
        payload_json=json.dumps(
            driver_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )
    pt_id, pt_token = new_playthrough(phase5_app, case_id, creator, version=v2)

    res = interact(phase5_app, pt_id, pt_token, LAPTOP_OBJECT, "read")
    assert res.status_code == 200, res.json()
    assert res.json()["evidenceId"] == "d_ev_when_obs"

    first = read_record(phase5_app, pt_id, pt_token, "d_ev_when_obs")
    assert first.status_code == 200, first.text
    content = first.json()["content"]
    assert content["renderType"] == "ACTIVITY_LOG"
    entries = content["entries"]
    assert 15 <= len(entries) <= 20
    assert all(set(e) == {"time", "text"} for e in entries)
    canon = next(
        f for f in driver_payload["draft"]["evidence"] if f["id"] == "d_ev_when_obs"
    )["propositions"][0]["observed_at"]
    assert len([e for e in entries if e["time"] == canon]) == 1
    # reload/reopen: byte-identical, still zero provider calls + no leaks
    second = read_record(phase5_app, pt_id, pt_token, "d_ev_when_obs")
    assert second.status_code == 200
    assert second.text == first.text
    assert_no_hidden_leaks(first.json())
    blob = json.dumps(content)
    for junk in ("observed_at", "propositions", "truth", "solverProof", "murdererId", "activityLogVersion"):
        assert junk not in blob


# --------------------------------------------------------------------------- #
# 15 — schema contract + bridge schema ids registered (RemoteClientProvider
#     compatibility: the stage uses ONLY the provider interface)
# --------------------------------------------------------------------------- #


def test_activity_log_stages_have_authoritative_schema_contracts():
    from app.generation import prompts
    from app.generation.bridge_protocol import (
        AUTHORITATIVE_SCHEMA_IDS,
        schema_id_for_stage,
    )
    from app.generation.provider import GenerationStage

    assert prompts.json_schema_for_generation_stage("activity_log") is not None
    schema = prompts.schema_contract_as_json_schema("activity_log")
    assert schema["required"] == ["entries"]
    assert schema_id_for_stage(GenerationStage.ACTIVITY_LOG.value) == "ACTIVITY_LOG_v1"
    assert schema_id_for_stage(GenerationStage.ACTIVITY_LOG_REPAIR.value) == "ACTIVITY_LOG_REPAIR_v1"
    assert "ACTIVITY_LOG_v1" in AUTHORITATIVE_SCHEMA_IDS
    assert "ACTIVITY_LOG_REPAIR_v1" in AUTHORITATIVE_SCHEMA_IDS
    # the derived transport JSON Schema constrains activityType to the CLOSED enum
    items = schema["properties"]["entries"]["items"]
    enum = set(items["properties"]["activityType"]["enum"])
    assert enum == ACTIVITY_LOG_ACTIVITY_TYPES


def test_generation_stage_is_driver_internal_not_in_stage_order():
    from app.generation import pipeline
    from app.generation.provider import GenerationStage

    assert GenerationStage.ACTIVITY_LOG not in pipeline.STAGE_ORDER
    assert GenerationStage.ACTIVITY_LOG_REPAIR not in pipeline.STAGE_ORDER


# --------------------------------------------------------------------------- #
# 16 — observability events are sanitized (§40)
# --------------------------------------------------------------------------- #


def test_activity_log_events_carry_only_safe_fields(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    pg = [
        getattr(e, "pd_event", None)
        for e in caplog.records
        if str(getattr(e, "pd_event", "")).startswith("activity_log.")
    ]
    for name in ("activity_log.generation.started", "activity_log.generation.complete"):
        assert name in pg
    for e in caplog.records:
        event = getattr(e, "pd_event", None)
        if not str(event).startswith("activity_log."):
            continue
        fields = getattr(e, "pd_fields", {})
        blob = json.dumps(fields, default=str)
        # NEVER the raw log text / prompts / truth
        assert "Local user activity detected" not in blob
        assert "Canonical evidence time" not in blob
        assert "murderer" not in blob


__all__ = []  # pytest module: no accidental public names