"""Phase 26 Fix-B — Hard-case Activity Log robustness (backend regression).

Reproduces the LIVE E2E defect chain (Phase26-Fix-B.md §1/§2) deterministically:

    normal activity_log → 27 entries → ACTIVITY_LOG_ENTRY_COUNT_INVALID
    repair #1 → parseFailureClass = entry_activity_type_invalid
                → ACTIVITY_LOG_SCHEMA_INVALID
    repair #2 → 19 entries → ACTIVITY_LOG_CANONICAL_TIME_MISSING
    → unpublished (fail-closed, CORRECT and required to remain)

Focused coverage (§9 items 1..23):

- the single canonical Activity Log contract (count bounds, closed
  ``activityType`` enum, canonical-time rule) is consistent across normal
  prompt, repair prompt, transport JSON Schema and validator constants;
- the NORMAL prompt carries a machine-verifiable count contract (H1);
- the REPAIR prompt carries the EXACT validator failure code + parse-failure
  class + per-code fix directives (§6 / H5);
- 27-entry / invalid-type / canonical-missing outputs are STILL rejected
  (validators unchanged, fail-closed);
- valid repaired output passes and remains bounded/unpublished correctly;
- the Bridge schema ids stay registered (Fix C drift guard remains green);
- CaseTruth stays server-only; Easy/Medium behavior is unchanged.

Deterministic-testing policy: no wall-clock sleeps, no real LLM — canned
responses through the existing ``MockOllamaTransport`` driver harness. Pre-fix
the repaired prompt did NOT carry the exact validator code, so the assertions
below are RED on the pre-fix prompts and GREEN after the hardening.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.activity_log import (  # noqa: E402
    ACTIVITY_LOG_ACTIVITY_TYPES,
    MAX_ACTIVITY_LOG_ENTRIES,
    MIN_ACTIVITY_LOG_ENTRIES,
    ActivityLogValidatorCode,
    parse_activity_log,
    validate_activity_log,
)
from app.domain.time_interval import epoch_to_iso, parse_iso8601  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402

from test_ollama_driver import (  # noqa: E402
    ICEPICK_SPEC,
    OLLAMA_MODEL,
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
# deterministic canned fixtures for the live chain (§8)
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


def _log27(canonical: str) -> str:
    """27 valid-shaped rows (strictly increasing, closed types, canonical once)
    — parses fine but fails ONLY the 15..20 count bound."""
    return _j({"entries": _entries(canonical, 27)})


def _log18_bad_type(canonical: str) -> str:
    """18 valid-shaped rows EXCEPT one entry whose ``activityType`` is outside
    the closed set → the strict parser raises and the driver classifies the
    response ``entry_activity_type_invalid`` (SCHEMA_INVALID)."""
    rows = _entries(canonical, 18)
    rows[3]["activityType"] = "BROWSER_ACTIVITY "
    return _j({"entries": rows})


def _log19_no_canonical(canonical: str) -> str:
    """19 valid-shaped rows that do NOT include the canonical instant → the
    strict validator reports ACTIVITY_LOG_CANONICAL_TIME_MISSING."""
    tick, offset = parse_iso8601(canonical)
    rows = []
    for i in range(19):
        # shifted +1 minute so the canonical instant is never a row
        t = tick + 60 + i * 180
        rows.append(
            {
                "timestamp": epoch_to_iso(t, offset),
                "activityType": _ALOG_TYPES[i % len(_ALOG_TYPES)],
                "activity": _ALOG_TEXTS[i % len(_ALOG_TEXTS)],
            }
        )
    return _j({"entries": rows})


def _when_obs_canonical() -> str:
    """The FIRST activity-log evidence fact's locked canonical instant
    (d_ev_when_obs = crime − 10s in the deterministic driver algebra)."""
    tick, offset = parse_iso8601(CANONICAL)
    return epoch_to_iso(tick - 10, offset)


# --------------------------------------------------------------------------- #
# §8 — deterministic regression reproducing the live failure chain.
# Pre-fix the failure path reproduces (fail-closed). Post-fix the corrected
# repair contract/orchestration is pinned (exact validator code + parse class
# + per-code directives in the repair prompts, hard count contract in the
# normal prompt).
# --------------------------------------------------------------------------- #


def test_8_prompt_text_analysis_27_mechanism():
    """The repeated live count of exactly 27 is a SYSTEMATIC mechanism, not a
    random over-run — proven by deterministic prompt-text analysis plus a
    canned 27-entry fixture:

    - the NORMAL prompt's TIMESTAMP_GRID injects exactly
      ``ACTIVITY_LOG_GRID_COUNT`` (18) server-owned timestamps AND the
      requirements bullet enumerates exactly 9 activity-kind groups
      (system resume/idle, session unlock/lock, login/logout, file/document,
      browser/mail/cloud/background sync, USB, network, backup, application
      open/close). 18 + 9 = 27 — a copy-heavy 8B model that "covers the grid"
      and then adds one row per listed kind lands on exactly 27 every time;
    - the strict validator rejects 27 with ACTIVITY_LOG_ENTRY_COUNT_INVALID
      (fail-closed — the 15..20 bound is unchanged);
    - on the LIVE Bridge transport the authoritative JSON Schema (minItems/
      maxItems on the activity-log contract) is NOT transmitted (the bridge
      job frame carries only ``schemaId`` + prompt and the local Ollama
      receives ``format: "json"`` free-form), so the 15..20 bound reaches the
      model ONLY through the prompt — the hardened MACHINE-VERIFIABLE CONTRACT
      block closes that channel (H2/H1).
    """
    import re

    from app.generation import prompts

    blob = prompts.build_activity_log_prompt(CANONICAL)
    match = re.search(r"TIMESTAMP_GRID: ([^\n]+)", blob)
    assert match is not None
    grid_members = len([t for t in match.group(1).split(", ") if t.strip()])
    assert grid_members == prompts.ACTIVITY_LOG_GRID_COUNT == 18
    kind_bullet = (
        "system resume/idle, session unlock/lock, login/logout, file/document "
        "activity, browser/mail/cloud/background sync, USB, network, backup, "
        "application open/close"
    )
    assert len([g for g in kind_bullet.split(",") if g.strip()]) == 9
    assert 18 + 9 == 27 > MAX_ACTIVITY_LOG_ENTRIES
    # the hardened normal prompt now explicitly warns against the 27 pattern
    assert "MACHINE-VERIFIABLE CONTRACT" in blob
    assert "never more" in blob
    # the canned 27-entry fixture is STILL rejected (validator unchanged)
    entries = parse_activity_log(_log27(CANONICAL))
    codes = validate_activity_log(entries, canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID in codes


def test_8_model_A_and_B_27_then_valid_repair_15_publishes():
    """§8 models A/B: a bounded repair that reduces 27→15 valid entries lets
    the whole attempt PUBLISH. The repair prompt carries the EXACT validator
    code (ACTIVITY_LOG_ENTRY_COUNT_INVALID) and the count finding."""
    crime = CANONICAL
    when_obs = _when_obs_canonical()
    other_logs = [_alog(anch) for anch in _other_anchors(crime)]
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _log27(when_obs),       # ACTIVITY_LOG normal → 27 → count invalid
        _j(_alog(when_obs, count=15)),  # ACTIVITY_LOG_REPAIR → valid 15
        *[_j(l) for l in other_logs],
        _j(_world()),
        ICEPICK_SPEC,
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    repair_prompt = _first_repair_prompt(transport)
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_ENTRY_COUNT_INVALID" in repair_prompt
    assert "have 27" in repair_prompt  # machine-readable finding reached the model
    normal = _first_normal_prompt(transport)
    assert "MACHINE-VERIFIABLE CONTRACT" in normal
    # the closed enum is spelled out in the normal prompt's contract block
    for token in sorted(ACTIVITY_LOG_ACTIVITY_TYPES):
        assert token in normal, token


def test_8_model_C_terminal_27_bad_type_then_canonical_missing_unpublished():
    """§8 model C — the FULL live chain for the terminal evidence item:

        normal → 27 → ENTRY_COUNT_INVALID
        repair#1 → invalid activity_type → SCHEMA_INVALID (parse class)
        repair#2 → 19 rows, canonical missing → terminal CANONICAL_TIME_MISSING

    The attempt FAILS and stays UNPUBLISHED (fail-closed). The repair#2 prompt
    receives the exact validator failure code AND the parse-failure class."""
    crime = CANONICAL
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _log27(when_obs),           # normal → 27 → ENTRY_COUNT_INVALID
        _log18_bad_type(when_obs),  # repair#1 → entry_activity_type_invalid
        _log19_no_canonical(when_obs),  # repair#2 → CANONICAL_TIME_MISSING (terminal)
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_CANONICAL_TIME_MISSING"
    assert record.published is None
    # the SECOND repair prompt carries the exact failure context (§6/H5):
    second_repair = _repair_prompts(transport)[1]
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_SCHEMA_INVALID" in second_repair
    assert "PARSE FAILURE CLASS: entry_activity_type_invalid" in second_repair
    # per-code fix directives are present in BOTH repair prompts
    for prompt in _repair_prompts(transport):
        assert "canonical time missing: MUST include the locked canonical time verbatim" in prompt
        assert "never trade one for another" in prompt


# --------------------------------------------------------------------------- #
# §9 — mandatory focused tests
# --------------------------------------------------------------------------- #

# 1 — canonical entry-count bounds are sourced consistently.
def test_9_1_entry_count_bounds_sourced_consistently():
    from app.generation import prompts

    assert prompts.MIN_ACTIVITY_LOG_ENTRIES == MIN_ACTIVITY_LOG_ENTRIES == 15
    assert prompts.MAX_ACTIVITY_LOG_ENTRIES == MAX_ACTIVITY_LOG_ENTRIES == 20
    schema = prompts.schema_contract_as_json_schema("activity_log")
    entries = schema["properties"]["entries"]
    assert entries["minItems"] == 15 and entries["maxItems"] == 20


# 2 — normal prompt contains the entry-count contract.
def test_9_2_normal_prompt_contains_entry_count_contract():
    from app.generation import prompts

    blob = prompts.build_activity_log_prompt(CANONICAL)
    assert "MACHINE-VERIFIABLE CONTRACT" in blob
    assert "MUST contain between 15 and 20 objects" in blob
    assert "never fewer, never more" in blob


# 3 — repair prompt contains the entry-count contract.
def test_9_3_repair_prompt_contains_entry_count_contract():
    from app.generation import prompts

    blob = prompts.build_activity_log_repair_prompt(
        CANONICAL, ("ENTRY_COUNT_TOO_HIGH (need 15..20, have 27)",)
    )
    assert "make it EXACTLY 15..20 rows" in blob
    assert "Exactly 15 to 20 chronological entries" in blob


# 4 — normal prompt contains the allowed activity_type vocabulary.
def test_9_4_normal_prompt_contains_activity_type_vocabulary():
    from app.generation import prompts

    blob = prompts.build_activity_log_prompt(CANONICAL)
    for token in sorted(ACTIVITY_LOG_ACTIVITY_TYPES):
        assert token in blob, token


# 5 — repair prompt contains the allowed activity_type vocabulary.
def test_9_5_repair_prompt_contains_activity_type_vocabulary():
    from app.generation import prompts

    blob = prompts.build_activity_log_repair_prompt(CANONICAL, ("SCHEMA_INVALID",))
    for token in sorted(ACTIVITY_LOG_ACTIVITY_TYPES):
        assert token in blob, token


# 6 — normal prompt preserves the canonical-time requirement.
def test_9_6_normal_prompt_preserves_canonical_time_requirement():
    from app.generation import prompts

    blob = prompts.build_activity_log_prompt(CANONICAL)
    assert "MUST appear verbatim in exactly ONE entry" in blob
    assert CANONICAL in blob


# 7 — repair prompt preserves the canonical-time requirement.
def test_9_7_repair_prompt_preserves_canonical_time_requirement():
    from app.generation import prompts

    blob = prompts.build_activity_log_repair_prompt(
        CANONICAL, ("CANONICAL_TIME_MISSING",),
        validator_code="ACTIVITY_LOG_CANONICAL_TIME_MISSING",
    )
    assert "MUST include the locked canonical time verbatim exactly once" in blob
    assert CANONICAL in blob


# 8 — schema expresses item bounds.
def test_9_8_schema_expresses_item_bounds():
    from app.generation import prompts

    # both stages share the ONE activity_log contract (STAGE_TO_CONTRACT)
    assert prompts.STAGE_TO_CONTRACT["activity_log"] == "activity_log"
    assert prompts.STAGE_TO_CONTRACT["activity_log_repair"] == "activity_log"
    assert (
        prompts.json_schema_for_generation_stage("activity_log")
        == prompts.json_schema_for_generation_stage("activity_log_repair")
    )
    schema = prompts.json_schema_for_generation_stage("activity_log")
    entries = schema["properties"]["entries"]
    assert entries["minItems"] == 15
    assert entries["maxItems"] == 20


# 9 — schema expresses the activity-type enum.
def test_9_9_schema_expresses_activity_type_enum():
    from app.generation import prompts

    schema = prompts.schema_contract_as_json_schema("activity_log")
    enum = set(
        schema["properties"]["entries"]["items"]["properties"]["activityType"]["enum"]
    )
    assert enum == set(ACTIVITY_LOG_ACTIVITY_TYPES)


# 10 — a 27-entry output is STILL rejected (validator unchanged).
def test_9_10_27_entries_still_rejected():
    entries = parse_activity_log(_log27(CANONICAL))
    codes = validate_activity_log(entries, canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID in codes


# 11 — an invalid activity_type is STILL rejected (parser unchanged).
def test_9_11_invalid_activity_type_still_rejected():
    with pytest.raises(ValueError):
        parse_activity_log(_log18_bad_type(CANONICAL))


# 12 — missing canonical time is STILL rejected (validator unchanged).
def test_9_12_missing_canonical_time_still_rejected():
    entries = parse_activity_log(_log19_no_canonical(CANONICAL))
    codes = validate_activity_log(entries, canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING in codes


# 13 — a valid repaired output passes.
def test_9_13_valid_repaired_output_passes():
    entries = parse_activity_log(_j(_alog(CANONICAL, count=15)))
    assert validate_activity_log(entries, canonical_time=CANONICAL) == ()


# 14 — a failed repair remains unpublished (see also §8 model C).
def test_9_14_failed_repair_remains_unpublished():
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _log27(when_obs),
        _log18_bad_type(when_obs),
        _log19_no_canonical(when_obs),
    ]
    record, _transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.published is None


# 15 — the repair loop remains bounded (initial + at most
#      MAX_ACTIVITY_LOG_REPAIR_PASSES repairs per fact).
def test_9_15_repair_loop_remains_bounded():
    from app.services.ollama_driver import MAX_ACTIVITY_LOG_REPAIR_PASSES

    assert MAX_ACTIVITY_LOG_REPAIR_PASSES == 2
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _log27(when_obs),
        _log18_bad_type(when_obs),
        _log19_no_canonical(when_obs),
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    # exactly 3 activity-log provider calls for the failing fact
    alog_calls = _activity_log_calls(transport)
    assert len(alog_calls) == 3  # 1 normal + 2 repairs, never a 4th


# 16 — deadline accounting remains intact (repair uses the CORE bucket, never
#      the global repair-pass budget).
def test_9_16_deadline_and_budget_accounting_intact():
    crime = CANONICAL
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _log27(when_obs),
        _j(_alog(when_obs, count=15)),
        *[_j(_alog(anch)) for anch in _other_anchors(crime)],
        _j(_world()),
        ICEPICK_SPEC,
    ]
    record, _transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    assert getattr(record.budget, "repair_passes", 0) == 0  # separate-stage accounting


# 17 — provider timeout semantics remain intact (a timed-out activity-log call
#      is a typed provider failure, never a silent validator bypass).
def test_9_17_provider_timeout_semantics_intact(caplog):
    import logging

    from test_ollama_driver import MockOllamaTransport, _make_driver
    from app.core.config import Settings

    when_obs = _when_obs_canonical()

    class TimeoutLogTransport(MockOllamaTransport):
        def post_json(self, url, payload, timeout):
            prompt = payload.get("messages", [{}])[0].get("content", "")
            if "'activity_log'" in prompt and not self._timeout_used:
                self._timeout_used = True
                raise TimeoutError("ollama request timed out")
            return super().post_json(url, payload, timeout)

    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _j(_alog(when_obs)),
        _j(_alog(when_obs)),
        _j(_alog(when_obs)),
        _j(_world()),
        ICEPICK_SPEC,
    ]
    transport = TimeoutLogTransport(posts=posts)
    transport._timeout_used = False
    driver = _make_driver(transport)
    clock = __import__("app.generation.clock", fromlist=["ManualClock"]).ManualClock()
    ids = __import__("app.generation.ids", fromlist=["IdSource"]).IdSource()
    from test_ollama_driver import _admission, _controller

    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    controller = _controller(driver, transport, admission, clock, ids)
    handle = controller.start_generation(
        "x", anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "PROVIDER_TIMEOUT"


# 18/19 — the ACTIVITY_LOG_v1 and ACTIVITY_LOG_REPAIR_v1 Bridge paths remain
# synchronized with the server vocabulary (Fix C drift guard stays green).
def test_9_18_19_activity_log_bridge_schema_ids_registered():
    from app.generation.bridge_protocol import (
        AUTHORITATIVE_SCHEMA_IDS,
        schema_id_for_stage,
    )
    from app.generation.provider import GenerationStage

    assert schema_id_for_stage(GenerationStage.ACTIVITY_LOG.value) == "ACTIVITY_LOG_v1"
    assert (
        schema_id_for_stage(GenerationStage.ACTIVITY_LOG_REPAIR.value)
        == "ACTIVITY_LOG_REPAIR_v1"
    )
    assert "ACTIVITY_LOG_v1" in AUTHORITATIVE_SCHEMA_IDS
    assert "ACTIVITY_LOG_REPAIR_v1" in AUTHORITATIVE_SCHEMA_IDS


# 20 — Fix C protocol vocabulary/drift guard remains green (client copy accepts
#      both activity-log schema ids).
def test_9_20_fixc_drift_guard_still_green():
    from app.generation.bridge_protocol import AUTHORITATIVE_SCHEMA_IDS as server_ids

    # The client copy (bridge tree) is a superset of the server set. Importing
    # the bridge protocol here must be possible (same sys.path trick used by the
    # Phase 22/24 Fix C suites).
    bridge_root = Path(__file__).resolve().parents[2] / "bridge"
    sys.path.insert(0, str(bridge_root))
    try:
        from pd_ollama_bridge.protocol import (  # noqa: E402
            AUTHORITATIVE_SCHEMA_IDS as client_ids,
        )
    finally:
        sys.path.remove(str(bridge_root))
    missing = set(server_ids) - set(client_ids)
    assert not missing, sorted(missing)
    assert {"ACTIVITY_LOG_v1", "ACTIVITY_LOG_REPAIR_v1"} <= set(client_ids)


# 21 — CaseTruth remains server-only in the hardened prompts.
def test_9_21_casetruth_remains_server_only():
    from app.generation import prompts

    for blob in (
        prompts.build_activity_log_prompt(CANONICAL),
        prompts.build_activity_log_repair_prompt(CANONICAL, ("SCHEMA_INVALID",)),
    ):
        for junk in (
            # persons (canonical scaffold names + ids from test_ollama_driver)
            "Paul Becker", "Anna Weiss", "Dr. Anna Weiss",
            "Marcus Fischer", "Sophie Hoffmann", "Lisa König",
            "paul_becker", "anna_weiss",
            "marcus_fischer", "sophie_hoffmann", "lisa_koenig",
            # locations (scaffold names + ids from test_ollama_driver)
            "Konsortium Office", "Research Laboratory", "Motor Lodge",
            "konsortium_office", "research_lab", "motor_lodge",
            "konsortium", "König",
            # weapon / motive material
            "bronze ceremonial ice pick", "bronze_ceremonial_ice_pick",
            "stolen research data", "stolen_research_data",
            "Wanted to steal the research data",
            "A financial settlement dispute",
            "A personal grudge over a promotion",
            # truth / protocol / transport internals
            "solverProof", "caseTruth", "crimeTime",
            "murdererId", "weaponId", "motiveId",
            "locationId", "victimId", "personId",
            # drill-regression: the classic 27-mechanism objects never leak
            "kitchen knife", "kitchen_knife",
            "letter opener", "letter_opener",
            "scissors",
        ):
            assert junk not in blob, junk


# 22 — Easy/Medium behavior remains unchanged (the standard staged run with
#      valid 15..20-entry logs still publishes).
def test_9_22_easy_medium_behavior_unchanged():
    from test_ollama_driver import _staged

    record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED


# 23 — Hard-example deterministic regression (the full §8 A/B/C scenario +
#      validator rejections are pinned above; this asserts the whole chain
#      plays out with the corrected contract/orchestration).
def test_9_23_hard_example_deterministic_regression():
    from test_ollama_driver import _staged

    # Easy/Medium-style run (unchanged baseline) still publishes
    record, _ = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    # the live Hard terminal chain is reproduced by §8 model C
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _log27(when_obs),
        _log18_bad_type(when_obs),
        _log19_no_canonical(when_obs),
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_CANONICAL_TIME_MISSING"
    assert record.published is None
    prompts = _repair_prompts(transport)
    assert len(prompts) == 2
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_ENTRY_COUNT_INVALID" in prompts[0]
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_SCHEMA_INVALID" in prompts[1]
    assert "PARSE FAILURE CLASS: entry_activity_type_invalid" in prompts[1]


# 24 — adversarial F-2: per-pass ``parse_shape`` reset semantics. A pass whose
# response FAILS to parse (parse_failure_class set) followed by a pass whose
# response PARSES but fails validation must reset the parse context: the NEXT
# repair prompt says ``PARSE FAILURE CLASS: none`` (the stale class from the
# previous unparsable pass never bleeds into a parseable-but-invalid pass).
def test_9_24_parse_shape_reset_after_parseable_pass():
    when_obs = _when_obs_canonical()
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _j({"events": _entries(when_obs, 15)}),  # normal -> UNPARSABLE
        _log27(when_obs),        # repair#0 -> PARSES, count-invalid (27 rows)
        _log19_no_canonical(when_obs),  # repair#1 -> PARSES, canonical-missing
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    rep = _repair_prompts(transport)
    assert len(rep) == 2
    # the FIRST repair follows the unparsable pass -> carries the real class
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_SCHEMA_INVALID" in rep[0]
    assert "PARSE FAILURE CLASS: unknown_top_level_keys" in rep[0]
    assert "PARSE FAILURE CLASS: none" not in rep[0]
    # the SECOND repair follows a PARSEABLE-but-invalid pass -> parse_shape was
    # reset at the top of the pass, so the class is explicitly "none" while the
    # EXACT validator code for the actual (count) failure is carried.
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_ENTRY_COUNT_INVALID" in rep[1]
    assert "PARSE FAILURE CLASS: none" in rep[1]
    # the stale class never reappears anywhere in the second repair prompt
    assert "unknown_top_level_keys" not in rep[1]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _other_anchors(crime_canonical: str) -> tuple[str, str, str]:
    """The canonical instants of the REMAINING three activity-log evidence
    facts (d_ev_opp_* x2, d_ev_presence) in driver evidence order."""
    tick, offset = parse_iso8601(crime_canonical)
    return (
        epoch_to_iso(tick - 120, offset),
        epoch_to_iso(tick - 120, offset),
        epoch_to_iso(tick - 20, offset),
    )


def _activity_log_calls(transport) -> list[int]:
    return [
        i
        for i in range(transport.call_count)
        if "activity_log" in transport.prompt_of_call(i)[:120]
    ]


def _first_normal_prompt(transport) -> str:
    return next(
        transport.prompt_of_call(i)
        for i in range(transport.call_count)
        if "activity_log_v1" in transport.prompt_of_call(i)
    )


def _repair_prompts(transport) -> list[str]:
    return [
        transport.prompt_of_call(i)
        for i in range(transport.call_count)
        if "activity_log_repair_v1" in transport.prompt_of_call(i)
    ]


def _first_repair_prompt(transport) -> str:
    prompts = _repair_prompts(transport)
    assert prompts, "no activity-log repair prompt was built"
    return prompts[0]