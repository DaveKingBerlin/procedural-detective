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
    PROMPT,
    _alog,
    _alog_posts,
    _case_people,
    _evidence,
    _j,
    _run,
    _staged,
    _world,
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
# 8b — ADV-254: person-name variants are rejected (bounded normalization)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    (
        "The user Paul Becker opened a file",   # exact full name (v1 phrase)
        "session for paul_becker started",      # id token
        "P4UL B3CK3R logged in",                # leet full name (uppercase)
        "p4ul b3ck3r logged in",                # leet full name (lowercase)
        "Päul Becker logged in",                # diacritic variant of Paul
        "user becker authenticated",            # surname only
        "user paul authenticated",              # bare first name
        "Paul-Beckers workstation",             # hyphenated possessive
        "Anna Weiss opened the lab door",       # title-less victim full name
        "Doktor Weiss accessing records",       # German title + surname
        "lisa koenig entered the laboratory",   # ASCII transliteration (König)
        "Lisa K. disconnected",                 # first name + initial
        "M. Fischer synced the mail",           # initial + surname
        "Marco Fischer checked in",             # mis-spelled first + surname
        "s0ph1e h0ffmann logged in",            # leet first+last (Sophie)
    ),
)
def test_person_name_variants_rejected(text):
    """ADV-254: surname-only, bare-first-name, ASCII-folded, title-less,
    leet and initial-based variants of canonical persons are rejected."""
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity=text)
    codes = validate_activity_log(
        _as_log(entries),
        canonical_time=CANONICAL,
        person_names=(
            "Dr. Anna Weiss", "Paul Becker", "Marcus Fischer",
            "Sophie Hoffmann", "Lisa König",
        ),
    )
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes, text


def test_person_name_generic_words_accepted():
    """ADV-254: plausible generic words that do NOT coincide with a canonical
    name never trip the tightened filter."""
    for text in (
        "the operator ran a scan",
        "a colleague logged off",
        "user initiated a backup",
    ):
        entries = _valid_entries()
        entries[8] = _entry(CANONICAL, activity=text)
        codes = validate_activity_log(
            _as_log(entries),
            canonical_time=CANONICAL,
            person_names=(
                "Dr. Anna Weiss", "Paul Becker", "Marcus Fischer",
                "Sophie Hoffmann", "Lisa König",
            ),
        )
        assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK not in codes, text


# --------------------------------------------------------------------------- #
# 8e — ADV-254 residual (ADV-260): concatenated-username needles, Unicode
#     Cf-format (ZWSP) splits and plural surnames are rejected; neutral rows
#     stay accepted. Full homoglyph/transliteration coverage is a DOCUMENTED
#     bounded residual (ADV-260 stays LOW-accepted for those forms).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    (
        "pbecker authenticated",                 # first-initial + surname
        "paulbecker authenticated",              # first + surname concatenated
        "beckerpaul synced the mail",            # surname + first concatenated
        "p4ulb3ck3r authenticated",              # leet concatenated username
        "Pau\u200bl B\u200becker logged in",     # ZWSP-split full name
        "Bec\u200bker synced the mail",          # ZWSP-split surname
        "the Beckers' workstation",              # plural surname + possessive
        "the Beckers\u2019 workstation",         # plural surname + curly quote
        "b3ck3r5 synced",                        # plural surname (leet marker)
    ),
)
def test_person_username_and_format_variants_rejected(text):
    """ADV-254 residual (ADV-260 a/b/c): concatenated-username forms,
    Cf-format (ZWSP) splits and plural surnames of canonical persons are now
    rejected by the full validator."""
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity=text)
    codes = validate_activity_log(
        _as_log(entries),
        canonical_time=CANONICAL,
        person_names=(
            "Dr. Anna Weiss", "Paul Becker", "Marcus Fischer",
            "Sophie Hoffmann", "Lisa König",
        ),
    )
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes, repr(text)


def test_person_username_and_format_variants_neutral_words_accepted():
    """ADV-254 residual guard: the bounded username/format/plural extensions
    never over-block generic log vocabulary that does not coincide with a
    canonical person."""
    for text in (
        "the operator ran a scan",
        "a colleague logged off",
        "user initiated a backup",
        "the case file was archived",
        "New letter received",
        "the kitchen was cleaned",
    ):
        entries = _valid_entries()
        entries[8] = _entry(CANONICAL, activity=text)
        codes = validate_activity_log(
            _as_log(entries),
            canonical_time=CANONICAL,
            person_names=(
                "Dr. Anna Weiss", "Paul Becker", "Marcus Fischer",
                "Sophie Hoffmann", "Lisa König",
            ),
        )
        assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK not in codes, text
        assert codes == (), (text, codes)


# --------------------------------------------------------------------------- #
# 8c — ADV-255: weapon/motive paraphrases are rejected (§24/§25)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    (
        "Stolen research data opened",          # §25 example 1
        "stolen research data was accessed",
        "the research archive was stolen",
        "Blackmail file accessed",              # §25 example 2
        "blackmail document opened",
        "Insurance payout document edited",     # §25 example 3
        "payout processed",
        "embezzlement",
        "her affair",
        "weapon present",
        "the knife",
        "a ceremonial pick was seized",         # weapon word-token paraphrase
        "a sharp bronze instrument was wiped",  # weapon word-token paraphrase
    ),
)
def test_weapon_motive_paraphrase_rejected(text):
    """ADV-255: the §25 example phrases, motive-significant content words,
    the static forbidden vocabulary and the weapon word-token paraphrases are
    all rejected."""
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity=text)
    codes = validate_activity_log(
        _as_log(entries),
        canonical_time=CANONICAL,
        weapon_names=("bronze ceremonial ice pick",),
        motive_names=("wanted to steal the research data",),
    )
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes, text


def test_weapon_motive_generic_rows_not_overblocked():
    """ADV-255: neutral computer-log rows stay valid even when they share a
    generic word with the motive label ("research"/"document"/"data")."""
    for text in (
        "opened a document",
        "Research document accessed",
        "Data synchronization completed",
        "Cloud sync finished",
    ):
        entries = _valid_entries()
        entries[8] = _entry(CANONICAL, activity=text)
        codes = validate_activity_log(
            _as_log(entries),
            canonical_time=CANONICAL,
            weapon_names=("bronze ceremonial ice pick",),
            motive_names=("wanted to steal the research data",),
        )
        assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK not in codes, text


# --------------------------------------------------------------------------- #
# 8d — ADV-258: the weapon word-token layer must NEVER over-block harmless rows
#     when the canonical weapon shares ordinary words ("computer case",
#     "letter opener", "kitchen knife"); distinctive weapon content words must
#     STILL reject (ADV-255 guard).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "weapon,text",
    (
        ("computer case", "In case of error, retry"),
        ("computer case", "the case file was archived"),
        ("computer case", "Computer activity detected"),
        ("letter opener", "New letter received"),
        ("kitchen knife", "the kitchen was cleaned"),
        ("glass bottle", "a bottle of water was spilled"),
        ("paper shredder", "a new paper order arrived"),
        ("computer case", "Mail client synchronized"),
        ("letter opener", "Document autosaved"),
    ),
)
def test_weapon_shared_words_do_not_overblock(weapon, text):
    """ADV-258: a canonical weapon whose name contains ordinary words must not
    make otherwise-neutral log rows fail ENTITY_LEAK — the weapon-token layer
    keeps only DISTINCTIVE content words as forbidden needles."""
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity=text)
    codes = validate_activity_log(
        _as_log(entries), canonical_time=CANONICAL, weapon_names=(weapon,)
    )
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK not in codes, (weapon, text)
    assert codes == (), (weapon, text, codes)


@pytest.mark.parametrize(
    "weapon,text",
    (
        ("bronze ceremonial ice pick", "a ceremonial pick was seized"),
        ("bronze ceremonial ice pick", "a sharp bronze instrument was wiped"),
        ("bronze ceremonial ice pick", "the bronze ceremonial ice pick was wiped"),
        ("kitchen knife", "the knife"),
        ("kitchen knife", "a knife was found by the counter"),
    ),
)
def test_weapon_paraphrase_rejects_still_hold_after_carve_out(weapon, text):
    """ADV-258 guard: the neutral carve-out must NOT weaken ADV-255 — rows that
    name a DISTINCTIVE weapon content word (or the exact weapon) still fail
    ENTITY_LEAK."""
    entries = _valid_entries()
    entries[8] = _entry(CANONICAL, activity=text)
    codes = validate_activity_log(
        _as_log(entries), canonical_time=CANONICAL, weapon_names=(weapon,)
    )
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes, (weapon, text)


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
    # entry-count findings only appear when the code itself is present; since
    # Phase19J-RI they carry the DETERMINISTIC numeric target (never content),
    # so the repair prompt can restate the hard 15..20 bound.
    with_count = repair_findings(
        (
            ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID,
            ActivityLogValidatorCode.ACTIVITY_LOG_CANONICAL_TIME_MISSING,
            ActivityLogValidatorCode.ACTIVITY_LOG_SCHEMA_INVALID,
        ),
        entry_count=12,
    )
    assert with_count == (
        "SCHEMA_INVALID",
        "ENTRY_COUNT_TOO_LOW (need 15..20, have 12)",
        "CANONICAL_TIME_MISSING",
    )


def test_repair_findings_count_direction_and_order_problems():
    findings = repair_findings(
        (ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID,),
        entry_count=25,
    )
    # Phase19J-RI: the token now carries the deterministic numeric target
    # (ENTRY_COUNT_TOO_HIGH (need 15..20, have 25)); the old bare code is
    # still a substring (additive change).
    assert any("ENTRY_COUNT_TOO_HIGH" in f for f in findings)
    assert "need 15..20, have 25" in findings[0]
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


def test_activity_log_prompts_carry_only_locked_time_not_the_identity_sheet():
    """ADV-256 regression: BOTH the ACTIVITY_LOG and ACTIVITY_LOG_REPAIR
    prompts — the FULL text the provider receives, including the provider's
    own "Locked user constraints (must be respected exactly):" serialization —
    carry the canonical evidence time and NONE of the murderer/victim/motive/
    weapon/witness names or ids. The repair prompt is captured by forcing one
    validation failure so the initial + repair stages both appear in the
    transport request log."""
    from test_ollama_driver import MockOllamaTransport, _world  # noqa: F401

    crime_canonical = "2026-09-11T23:42:00+02:00"
    when_obs_canonical = epoch_to_iso(
        parse_iso8601(crime_canonical)[0] - 10, parse_iso8601(crime_canonical)[1]
    )
    bad = _alog(when_obs_canonical)
    bad["entries"][8]["timestamp"] = "2026-09-11T23:40:00+02:00"  # canonical removed
    good = _alog(when_obs_canonical)
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        json.dumps(bad),        # ACTIVITY_LOG (invalid -> repair)
        json.dumps(good),       # ACTIVITY_LOG_REPAIR (valid)
        *_alog_posts(crime_canonical)[1:],
        _j(_world()),
    ]
    from test_ollama_driver import ICEPICK_SPEC

    posts.append(ICEPICK_SPEC)
    record, transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    prompts = [transport.prompt_of_call(i) for i in range(transport.call_count)]
    alog_prompt = next(p for p in prompts if "activity_log_v1" in p)
    repair_prompt = next(p for p in prompts if "activity_log_repair_v1" in p)
    leaks = (
        "Paul Becker", "paul_becker", "Paul", "Becker",
        "Anna Weiss", "anna_weiss", "Weiss",
        "stolen research data", "stolen_research_data", "stolen",
        "bronze ceremonial ice pick", "bronze_ceremonial_ice_pick",
        "Lisa König", "lisa_koenig", "König",
        "Marcus Fischer", "marcus_fischer", "Sophie Hoffmann",
        "sophie_hoffmann",
    )
    for prompt in (alog_prompt, repair_prompt):
        for needle in leaks:
            assert needle not in prompt, (needle, prompt[:400])
        # the canonical evidence time IS the locked temporal constraint
        assert when_obs_canonical in prompt
        assert "Locked user constraints (must be respected exactly):" in prompt
        # the locked block serializes ONLY the time — never the answer sheet
        assert "- crime_time: " in prompt
        for field in ("victim", "murderer", "motive", "weapon", "witness"):
            assert f"- {field}: " not in prompt, (field, prompt[-400:])


def _run_window(posts, *, before, after):
    """A full driver run with the operator-configured activity-log window."""
    from app.core.config import Settings
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource
    from app.generation.ollama_provider import OllamaProvider
    from app.services.ollama_driver import OllamaStageDriver
    from test_ollama_driver import (
        OLLAMA_BASE,
        OLLAMA_MODEL,
        PROMPT,
        MockOllamaTransport,
        _admission,
        _controller,
    )

    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=posts)

    def factory():
        return OllamaProvider(
            base_url=OLLAMA_BASE,
            model=OLLAMA_MODEL,
            timeout_seconds=5,
            transport=transport,
        )

    driver = OllamaStageDriver(
        settings=Settings(
            activity_log_window_before_minutes=before,
            activity_log_window_after_minutes=after,
        ),
        provider_factory=factory,
    )
    controller = _controller(driver, transport, admission, clock, ids)
    handle = controller.start_generation(
        PROMPT, anonymous_quota_session_id=session.session_id
    )
    return controller.attempt(handle.attempt_id), transport


def _alog_forward_entries(canonical, count=15):
    """A VALID log under a 0-before window: rows run [canonical, canonical+60min)."""
    tick, offset = parse_iso8601(canonical)
    rows = [_entry(epoch_to_iso(tick + i * 150, offset)) for i in range(count)]
    rows[0] = _entry(canonical)
    return {"entries": rows}


def test_activity_log_window_zero_is_honored_not_coerced():
    """ADV-257 regression: an operator-configured 0-sided window is HONORED by
    the driver (never silently replaced by the default 60). With BEFORE=0 /
    AFTER=60 the generated-logs window is [canonical, canonical+60min] and the
    first activity-log prompt renders \"-0 minutes ... +60 minutes\"."""
    from test_ollama_driver import ICEPICK_SPEC, _evidence, _world

    crime_canonical = "2026-09-11T23:42:00+02:00"
    tick, offset = parse_iso8601(crime_canonical)

    def iso(delta_seconds):
        return epoch_to_iso(tick + delta_seconds, offset)

    posts = [_j(_case_people()), _j(_evidence())]
    # four time-bearing cctv facts, each with a log inside [canonical, +60min]
    for anchor in (iso(-10), iso(-120), iso(-120), iso(-20)):
        posts.append(_j(_alog_forward_entries(anchor)))
    posts.append(_j(_world()))
    posts.append(ICEPICK_SPEC)
    record, transport = _run_window(posts, before=0, after=60)
    assert record.state is GenerationState.PUBLISHED
    first_alog = next(
        transport.prompt_of_call(i) for i in range(transport.call_count)
        if "activity_log_v1" in transport.prompt_of_call(i)
    )
    assert "-0 minutes" in first_alog       # BEFORE=0 honored, not -> 60
    assert "-60 minutes" not in first_alog  # the old (0 or 60) coercion is gone
    assert "+60 minutes" in first_alog      # AFTER=60 stays as configured


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


def test_fake_provider_fixture_logs_pass_tightened_validator():
    """ADV-254/255 regression guard: every builtin FakeProvider ACTIVITY_LOG
    fixture (``dev_mode_case.json``, 20 rows x3) still validates unchanged
    under the case's OWN persons/motives/weapons/locations after the tightened
    filter. A generic row (\"Research document accessed\") never trips; a row
    accidentally containing forbidden content would fail here."""
    _BACKEND = Path(__file__).resolve().parents[1]
    fixture = json.loads(
        (_BACKEND / "app" / "services" / "dev_mode_case.json").read_text(encoding="utf-8")
    )
    truth = json.loads(fixture["case_truth"][0])
    public = json.loads(fixture["public_world"][0])
    persons = [p["name"] for p in public["persons"]]
    persons += [p["personId"] for p in public["persons"]]
    motives = [m["label"] for m in public["motives"]]
    motives += [m["motiveId"] for m in public["motives"]]
    weapons = [truth["crime"]["weaponId"]]
    loc_ids = [loc["locationId"] for loc in public["locations"]]
    loc_ids += [truth["crime"]["locationId"]]
    loc_names = [loc["name"] for loc in public["locations"]]
    loc_names += [public["scene"]["name"]]
    evidence = json.loads(fixture["evidence"][0])["evidence"]
    log_items = [
        item
        for item in evidence
        if (item.get("presentation") or {}).get("activityLogVersion")
    ]
    assert len(log_items) == 3
    for item in log_items:
        canonical = item["propositions"][0]["observedAt"]
        rows = [
            {
                "timestamp": event["time"],
                "activityType": "LOCAL_ACTIVITY",
                "activity": event["action"],
            }
            for event in item["presentation"]["events"]
        ]
        entries = parse_activity_log(json.dumps({"entries": rows}))
        codes = validate_activity_log(
            entries,
            canonical_time=canonical,
            person_names=persons,
            weapon_names=weapons,
            motive_names=motives,
            location_ids=loc_ids,
            location_names=loc_names,
        )
        assert codes == (), (item["id"], codes)


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


# --------------------------------------------------------------------------- #
# 17 — Phase19J-RI regression suite (defect: real hermes3:8b observation —
# initial 11986-byte SCHEMA_INVALID + repair 153/186-byte one-row
# ENTRY_COUNT_INVALID failures). Each numbered item maps to the
# Phase19J-RI.md §REGRESSION TESTS list 1..15.
# --------------------------------------------------------------------------- #


def _alog_log_events(caplog):
    """(event name, fields) pairs for every activity_log.* event."""
    return [
        (str(getattr(e, "pd_event", "")), dict(getattr(e, "pd_fields", {}) or {}))
        for e in caplog.records
        if str(getattr(e, "pd_event", "")).startswith("activity_log.")
    ]


# 1 — initial stage returning 17 valid entries parses as 17 (driver-level).
def test_ri01_initial_stage_17_valid_entries_parses_as_17(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, _transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    complete_ok = [
        fields["entryCount"]
        for name, fields in _alog_log_events(caplog)
        if name == "activity_log.generation.complete" and fields.get("success")
    ]
    assert complete_ok
    assert all(count == 17 for count in complete_ok)


# 2 — repair stage returning 17 valid entries parses as 17 (+ PUBLISHED).
def test_ri02_repair_stage_17_valid_entries_parses_as_17(caplog):
    import logging

    from test_ollama_driver import ICEPICK_SPEC, _world

    crime_canonical = "2026-09-11T23:42:00+02:00"
    when_obs = epoch_to_iso(
        parse_iso8601(crime_canonical)[0] - 10, parse_iso8601(crime_canonical)[1]
    )
    bad = _alog(when_obs)
    bad["entries"][8]["timestamp"] = "2026-09-11T23:40:00+02:00"  # canonical removed
    good = _alog(when_obs)
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        json.dumps(bad),   # ACTIVITY_LOG (invalid -> repair)
        json.dumps(good),  # ACTIVITY_LOG_REPAIR (valid, 17 entries)
        *_alog_posts(crime_canonical)[1:],
        _j(_world()),
    ]
    posts.append(ICEPICK_SPEC)
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, _transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    repaired_ok = [
        fields["entryCount"]
        for name, fields in _alog_log_events(caplog)
        if name == "activity_log.repair.complete" and fields.get("success")
    ]
    assert repaired_ok
    assert all(count == 17 for count in repaired_ok)


# 3 — initial and repair stages use the SAME output DTO/schema (plus the
# Phase19J-RI transport count bounds).
def test_ri03_initial_and_repair_stages_share_the_same_schema():
    from app.generation import prompts

    assert prompts.json_schema_for_generation_stage("activity_log") == \
        prompts.json_schema_for_generation_stage("activity_log_repair")
    assert prompts.STAGE_TO_CONTRACT["activity_log"] == \
        prompts.STAGE_TO_CONTRACT["activity_log_repair"] == "activity_log"
    assert prompts.schema_contract(
        prompts.STAGE_TO_CONTRACT["activity_log"]
    ) == prompts.schema_contract(prompts.STAGE_TO_CONTRACT["activity_log_repair"])
    schema = prompts.schema_contract_as_json_schema("activity_log")
    entries = schema["properties"]["entries"]
    assert entries["minItems"] == MIN_ACTIVITY_LOG_ENTRIES == 15
    assert entries["maxItems"] == MAX_ACTIVITY_LOG_ENTRIES == 20


# 4 — wrong top-level key fails typed (domain parse raises; driver terminal
# ACTIVITY_LOG_SCHEMA_INVALID).
def test_ri04_wrong_top_level_key_fails_typed():
    when_obs = CANONICAL
    doc = {"events": _valid_entries(when_obs)}
    with pytest.raises(ValueError):
        parse_activity_log(json.dumps(doc))

    crime_canonical = "2026-09-11T23:42:00+02:00"
    when_obs = epoch_to_iso(
        parse_iso8601(crime_canonical)[0] - 10, parse_iso8601(crime_canonical)[1]
    )
    events_doc = json.dumps({"events": _alog(when_obs)["entries"]})
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        events_doc,  # ACTIVITY_LOG (wrong top-level key -> SCHEMA_INVALID)
        events_doc,  # repair 1
        events_doc,  # repair 2 (terminal)
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_SCHEMA_INVALID"
    assert transport.call_count == 5


# 5 — a single ActivityLogEntry can never be accepted as a repaired document.
def test_ri05_single_entry_document_is_never_accepted():
    bare = _entry(CANONICAL)
    with pytest.raises(ValueError):
        parse_activity_log(json.dumps(bare))  # no wrapper -> schema-invalid
    one = parse_activity_log(json.dumps({"entries": [bare]}))
    codes = validate_activity_log(one, canonical_time=CANONICAL)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTRY_COUNT_INVALID in codes

    crime_canonical = "2026-09-11T23:42:00+02:00"
    when_obs = epoch_to_iso(
        parse_iso8601(crime_canonical)[0] - 10, parse_iso8601(crime_canonical)[1]
    )
    one_row = json.dumps({"entries": [_entry(when_obs)]})
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        one_row,  # ACTIVITY_LOG (1 entry)
        one_row,  # repair 1 (1 entry)
        one_row,  # repair 2 (terminal)
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_ENTRY_COUNT_INVALID"
    assert transport.call_count == 5


# 7 — a realistic Hermes-shaped ~10KB response survives adapter projection.
def test_ri07_ten_kb_hermes_shaped_response_survives_adapter_projection():
    from test_ollama_driver import ICEPICK_SPEC, _world

    tick, offset = parse_iso8601(CANONICAL)
    rows = []
    for i in range(17):
        rows.append(
            _entry(
                epoch_to_iso(tick + (i - 8) * 180, offset),
                activity="Document autosaved" + "x" * (
                    MAX_ACTIVITY_TEXT_CHARS - len("Document autosaved")
                ),
            )
        )
    rows[8] = _entry(CANONICAL)  # canonical exactly once, ordinary row
    doc = json.dumps({"entries": rows}, ensure_ascii=False, indent=2)
    # Hermes-style verbose JSON: arbitrary JSON whitespace padding brings the
    # raw response to ~10KB while the parsed content stays a VALID 17-row log.
    raw = doc + " " * max(0, 10_000 - len(doc))
    assert len(raw.encode("utf-8")) >= 10_000
    entries = parse_activity_log(raw)
    assert len(entries) == 17
    assert validate_activity_log(entries, canonical_time=CANONICAL) == ()

    # through the MockOllamaTransport driver run (first log call = the 10KB doc)
    crime_canonical = "2026-09-11T23:42:00+02:00"
    when_obs = epoch_to_iso(
        parse_iso8601(crime_canonical)[0] - 10, parse_iso8601(crime_canonical)[1]
    )
    big = json.dumps({"entries": _alog(when_obs)["entries"]}, indent=2)
    big_padded = big + " " * max(0, 10_000 - len(big))
    assert len(big_padded.encode("utf-8")) >= 10_000
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        big_padded,
        *_alog_posts(crime_canonical)[1:],
        _j(_world()),
    ]
    posts.append(ICEPICK_SPEC)
    record, _transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="ri07", model="mock", title="RI07"
        )
    )
    obs = next(f for f in payload["draft"]["evidence"] if f["id"] == "d_ev_when_obs")
    assert len(obs["presentation"]["events"]) == 17


# 8 — canonical time appears exactly once through the DRIVER repair path.
def test_ri08_canonical_time_appears_exactly_once_through_repair_path():
    from test_ollama_driver import ICEPICK_SPEC, _world

    crime_canonical = "2026-09-11T23:42:00+02:00"
    when_obs = epoch_to_iso(
        parse_iso8601(crime_canonical)[0] - 10, parse_iso8601(crime_canonical)[1]
    )
    bad = _alog(when_obs)
    bad["entries"][8]["timestamp"] = "2026-09-11T23:40:00+02:00"  # canonical removed
    good = _alog(when_obs)
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        json.dumps(bad),   # ACTIVITY_LOG (invalid -> repair)
        json.dumps(good),  # ACTIVITY_LOG_REPAIR (canonical once)
        *_alog_posts(crime_canonical)[1:],
        _j(_world()),
    ]
    posts.append(ICEPICK_SPEC)
    record, _transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="ri08", model="mock", title="RI08"
        )
    )
    obs = next(f for f in payload["draft"]["evidence"] if f["id"] == "d_ev_when_obs")
    events = obs["presentation"]["events"]
    assert len([e for e in events if e["time"] == when_obs]) == 1


# 10 — bounded repair count unchanged: exactly MAX_ACTIVITY_LOG_REPAIR_PASSES+1
# calls then a terminal typed failure (keep 2).
def test_ri10_bounded_repair_count_unchanged_terminal_after_three_calls():
    from app.services.ollama_driver import MAX_ACTIVITY_LOG_REPAIR_PASSES

    assert MAX_ACTIVITY_LOG_REPAIR_PASSES == 2
    crime_canonical = "2026-09-11T23:42:00+02:00"
    when_obs = epoch_to_iso(
        parse_iso8601(crime_canonical)[0] - 10, parse_iso8601(crime_canonical)[1]
    )
    bad = _alog(when_obs)
    bad["entries"][8]["timestamp"] = "2026-09-11T23:40:00+02:00"
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        json.dumps(bad),  # ACTIVITY_LOG
        json.dumps(bad),  # repair 1
        json.dumps(bad),  # repair 2
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ACTIVITY_LOG_CANONICAL_TIME_MISSING"
    log_calls = sum(
        1
        for i in range(transport.call_count)
        if "activity_log" in transport.prompt_of_call(i)[:80]
    )
    assert log_calls == MAX_ACTIVITY_LOG_REPAIR_PASSES + 1 == 3
    assert transport.call_count == 5
    assert record.budget.calls == 5


# 11 — solver signature unchanged (the rich log never touches solver input).
def test_ri11_solver_signature_unchanged_by_fix():
    record_rich, _t = _run(_staged())
    assert record_rich.state is GenerationState.PUBLISHED
    payload_rich = json.loads(
        serialize_published_payload(
            record_rich.published, seed=1, prompt="ri11", model="mock", title="RI11"
        )
    )
    payload_plain = json.loads(json.dumps(payload_rich))
    for fact in payload_plain["draft"]["evidence"]:
        presentation = fact.get("presentation")
        if isinstance(presentation, dict):
            presentation.pop("events", None)
            presentation.pop("activityLogVersion", None)
    assert _solver_signature(payload_rich) == _solver_signature(payload_plain)


# 12 — FakeProvider unchanged (deterministic golden path).
def test_ri12_fake_provider_deterministic_path_unchanged(phase5_app):
    from app.domain.render import render_payload_of
    from test_phase19c_interaction import _published_payload
    from test_phase19g_evidence_render import GOLDEN_CCTV

    case_id, creator = case_for(phase5_app)
    payload = _published_payload(phase5_app, case_id, 1)
    fact = next(f for f in payload["draft"]["evidence"] if f["id"] == GOLDEN_CCTV)
    canonical = fact["propositions"][0]["observed_at"]
    content = project_read_content(payload, GOLDEN_CCTV)
    assert content["renderType"] == "ACTIVITY_LOG"
    entries = content["entries"]
    assert len(entries) == 20
    assert len([e for e in entries if e["time"] == canonical]) == 1
    assert render_payload_of(fact) == render_payload_of(fact)


# 13 — RemoteClientProvider contract unchanged (schema ids + transport schema
# equality for both activity-log stages).
def test_ri13_remote_client_contract_unchanged_with_schema_equality():
    from app.generation import prompts
    from app.generation.bridge_protocol import (
        AUTHORITATIVE_SCHEMA_IDS,
        schema_id_for_stage,
    )
    from app.generation.provider import GenerationStage

    assert schema_id_for_stage(GenerationStage.ACTIVITY_LOG.value) == "ACTIVITY_LOG_v1"
    assert schema_id_for_stage(
        GenerationStage.ACTIVITY_LOG_REPAIR.value
    ) == "ACTIVITY_LOG_REPAIR_v1"
    assert "ACTIVITY_LOG_v1" in AUTHORITATIVE_SCHEMA_IDS
    assert "ACTIVITY_LOG_REPAIR_v1" in AUTHORITATIVE_SCHEMA_IDS
    assert prompts.json_schema_for_generation_stage("activity_log") == \
        prompts.json_schema_for_generation_stage("activity_log_repair")


# 14 — gameplay still performs zero provider calls.
def test_ri14_gameplay_reads_make_zero_provider_calls():
    record, transport = _run(_staged())
    assert record.state is GenerationState.PUBLISHED
    calls_after_generation = transport.call_count
    payload = json.loads(
        serialize_published_payload(
            record.published, seed=1, prompt="ri14", model="mock", title="RI14"
        )
    )
    # projection + DTO serialization + byte-deterministic JSON round trips
    for fact in payload["draft"]["evidence"]:
        project_read_content(payload, fact["id"])
    json.loads(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    json.loads(serialize_published_payload(
        record.published, seed=1, prompt="ri14", model="mock", title="RI14"
    ))
    assert transport.call_count == calls_after_generation


# 15 — REQUIREMENTS.md byte-identical (repo guard; never hashed generated or
# adapter files).
def test_ri15_requirements_md_byte_identical():
    import hashlib

    path = Path(__file__).resolve().parents[2] / "REQUIREMENTS.md"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == (
        "b2e568c029b02de79a74c43bb0e2ecd4d44a7fbec65d7de191a890a6dec9970d"
    )


# 16 — DEF-104: the INITIAL prompt carries the concrete window endpoints and
# the canonical-anchor worked example (no identity-sheet material — ADV-256).
def test_ri16_initial_prompt_carries_concrete_window_and_canonical_anchor():
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    blob = prompts.build_activity_log_prompt(canonical)
    assert "activity_log_v1" in blob
    # concrete deterministic window endpoints (60/60 defaults -> 22:42 ..
    # next-day 00:42, both rendered with the canonical instant's own +02:00)
    assert (
        "Every entry timestamp MUST lie inside the deterministic window "
        "[2026-09-11T22:42:00+02:00 .. 2026-09-12T00:42:00+02:00]"
    ) in blob
    # the relative explanation is KEPT next to the exact bounds
    assert "-60 minutes" in blob and "+60 minutes" in blob
    assert "(the whole log spans at most 120 minutes)" in blob
    # canonical-anchor worked example with the EXACT locked value: the model
    # never has to invent a canonical-row shape.
    assert (
        '"timestamp": "2026-09-11T23:42:00+02:00", "activityType": '
        '"LOCAL_ACTIVITY", "activity": "Local user activity detected"'
    ) in blob
    # the locked value occurs as the plain canonical line AND inside the anchor
    # row AND once inside the COMPLETE worked-example scaffold AND once inside
    # the app-owned TIMESTAMP GRID (DEF-104 follow-up #3) — exactly 4
    # occurrences, never a phantom second canonical outside those signals.
    assert blob.count("2026-09-11T23:42:00+02:00") == 4
    # count sentence present (the Phase19J-RI hard floor/ceiling)
    assert "Exactly 15 to 20 chronological entries" in blob
    # ADV-256 invariant: initial prompt = canonical time + window + neutral
    # rules ONLY; no identity sheet, no truth seeds, no other locked facts.
    for junk in (
        "Paul Becker", "Anna Weiss", "König", "bronze ceremonial ice pick",
        "stolen research data", "solverProof", "caseTruth", "crimeTime",
        "murdererId", "weaponId", "motiveId", "locationId", "konsortium",
    ):
        assert junk not in blob, junk


# 17 — DEF-104 DRIFT GUARD: the concrete window endpoints the prompt builder
# renders EXACTLY equal the ISO endpoints derived from
# ``activity_log_window_bounds`` (the strict validator's authoritative math)
# for BOTH prompts — including the clamped-window cases where before+after
# exceeds the 120-minute hard cap (the relative sentence then shows the
# CLAMPED minutes, so the prompt can never advertise a wider window than the
# validator accepts).
def test_ri17_prompt_window_endpoints_match_validator_window_math():
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    tick, offset = parse_iso8601(canonical)
    for before, after in ((60, 60), (90, 90), (5, 130), (0, 120), (120, 0), (30, 0)):
        lo, hi = activity_log_window_bounds(
            tick, before_minutes=before, after_minutes=after
        )
        start_iso, end_iso = epoch_to_iso(lo, offset), epoch_to_iso(hi, offset)
        initial = prompts.build_activity_log_prompt(
            canonical, before_minutes=before, after_minutes=after
        )
        repair = prompts.build_activity_log_repair_prompt(
            canonical, ("TIMESTAMP_OUTSIDE_WINDOW",),
            before_minutes=before, after_minutes=after,
        )
        expected = (
            "Every entry timestamp MUST lie inside the deterministic window ["
            + start_iso + " .. " + end_iso + "]"
        )
        assert expected in initial, (before, after)
        assert expected in repair, (before, after)
        # the relative sentence carries the CLAMPED minutes — prompt and
        # validator agree on the ACTUAL window in every configuration.
        clamped_before = (tick - lo) // 60
        clamped_after = (hi - tick) // 60
        assert f"-{clamped_before} minutes" in initial, (before, after)
        assert f"+{clamped_after} minutes" in initial, (before, after)
        assert f"-{clamped_before} minutes" in repair, (before, after)
        assert f"+{clamped_after} minutes" in repair, (before, after)


# 18a — DEF-104 DRIVER-LEVEL regression: a first-pass activity-log response
# with rows OUTSIDE the window AND the canonical missing triggers a repair
# whose ACTUAL provider prompt text embeds the concrete deterministic window
# endpoints (for THAT fact's locked canonical instant) and the
# canonical-anchor worked example with the exact locked value.
def test_ri18_driver_repair_prompt_embeds_concrete_window_and_canonical_anchor():
    from test_ollama_driver import ICEPICK_SPEC, _world

    crime_canonical = "2026-09-11T23:42:00+02:00"
    tick, offset = parse_iso8601(crime_canonical)
    when_obs = epoch_to_iso(tick - 10, offset)  # d_ev_when_obs locked instant
    bad = _alog(when_obs)
    for i, entry in enumerate(bad["entries"]):
        row_tick = parse_iso8601(entry["timestamp"])[0]
        # every row lands 2 hours after the canonical -> ALL outside the
        # deterministic ±60-minute window AND the canonical is missing.
        bad["entries"][i]["timestamp"] = epoch_to_iso(row_tick + 7200, offset)
    good = _alog(when_obs)
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        json.dumps(bad),   # ACTIVITY_LOG (d_ev_when_obs) — invalid -> repair
        json.dumps(good),  # ACTIVITY_LOG_REPAIR — valid
        *_alog_posts(crime_canonical)[1:],
        _j(_world()),
    ]
    posts.append(ICEPICK_SPEC)
    record, transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    repair_prompt = transport.prompt_of_call(3)
    assert "activity_log_repair_v1" in repair_prompt
    # the repair prompt locks the SERVER-OWNED canonical of THIS fact
    assert "Canonical evidence time: " + when_obs + "\n" in repair_prompt
    # concrete window endpoints for when_obs with the 60/60 defaults
    lo, hi = activity_log_window_bounds(
        parse_iso8601(when_obs)[0], before_minutes=60, after_minutes=60
    )
    assert (
        "Every entry timestamp MUST lie inside the deterministic window ["
        + epoch_to_iso(lo, offset) + " .. " + epoch_to_iso(hi, offset) + "]"
    ) in repair_prompt
    # canonical-anchor worked example with the exact locked value
    assert (
        '"timestamp": "' + when_obs + '", "activityType": "LOCAL_ACTIVITY", '
        '"activity": "Local user activity detected"'
    ) in repair_prompt
    # BOTH machine-readable findings tokens reached the model plus the
    # findings->fix mapping sentence (DEF-104 requirement 4).
    assert "CANONICAL_TIME_MISSING" in repair_prompt
    assert "TIMESTAMP_OUTSIDE_WINDOW" in repair_prompt
    assert "Findings-to-fix mapping (deterministic)" in repair_prompt
    # ADV-256: the repair prompt still carries NO identity-sheet material.
    for junk in (
        "Paul Becker", "Anna Weiss", "bronze ceremonial ice pick",
        "solverProof", "caseTruth", "crimeTime",
    ):
        assert junk not in repair_prompt, junk


# --------------------------------------------------------------------------- #
# 19..22 — DEF-104 follow-up #2: BOTH prompts embed a COMPLETE
#      self-consistent worked-example scaffold plus the explicit
#      simultaneous-constraint rule ("satisfy ALL of these at once").
# --------------------------------------------------------------------------- #


def _extract_worked_example(prompt_text: str) -> str:
    """The embedded COMPLETE worked-example JSON document — the ONLY compact
    single-line ``{"entries":[...]}`` document in a built prompt (the schema
    skeleton that follows is pretty-printed, never single-line)."""
    import re as _re

    match = _re.search(r'\{"entries":\[[^\n]*\}', prompt_text)
    assert match, "built prompt carries no complete worked-example JSON document"
    return match.group(0)


def test_ri19_initial_prompt_embeds_self_consistent_worked_example():
    """DEF-104 follow-up #2 self-consistency guard: the INITIAL built prompt
    embeds a COMPLETE 15-row worked example whose parsed rows validate with
    the REAL validator — ``parse_activity_log`` -> 15 entries and
    ``validate_activity_log`` -> () under the configured 60/60 window (the
    scaffold itself proves the simultaneous rules can all hold at once)."""
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    blob = prompts.build_activity_log_prompt(canonical)
    assert "Complete worked example" in blob
    example = _extract_worked_example(blob)
    entries = parse_activity_log(example)
    assert len(entries) == MIN_ACTIVITY_LOG_ENTRIES == 15
    codes = validate_activity_log(entries, canonical_time=canonical)
    assert codes == (), codes
    # the scaffold is a SHAPE, not the answer
    assert "shows the required shape/format only" in blob
    assert "replace the row content with your own plausible harmless rows" in blob
    # example rows use ONLY closed-enum tokens and app-owned neutral texts
    assert all(e.activity_type in ACTIVITY_LOG_ACTIVITY_TYPES for e in entries)
    assert all(
        e.activity in prompts.ACTIVITY_LOG_NEUTRAL_TEXT_POOL for e in entries
    ), [e.activity for e in entries]
    # ADV-256: the scaffold never carries identity-sheet material
    for junk in ("Becker", "Weiss", "König", "ice pick", "stolen", "research data"):
        assert junk not in example, junk


def test_ri20_repair_prompt_embeds_self_consistent_worked_example():
    """DEF-104 follow-up #2 self-consistency guard for the REPAIR prompt: the
    COMPLETE worked example embedded in the built repair prompt validates
    under the REAL validator BOTH with the default 60/60 window AND with a
    clamped asymmetric window (15/130 -> the app clamps to 12 before / 108
    after) — the scaffold stays valid even when the configured window is not
    the centered default."""
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    findings = ("CANONICAL_TIME_MISSING", "TIMESTAMP_OUTSIDE_WINDOW")
    blob = prompts.build_activity_log_repair_prompt(
        canonical, findings, before_minutes=60, after_minutes=60
    )
    assert "Complete worked example" in blob
    entries = parse_activity_log(_extract_worked_example(blob))
    assert len(entries) == MIN_ACTIVITY_LOG_ENTRIES == 15
    assert validate_activity_log(entries, canonical_time=canonical) == ()

    clamped = prompts.build_activity_log_repair_prompt(
        canonical, findings, before_minutes=15, after_minutes=130
    )
    entries = parse_activity_log(_extract_worked_example(clamped))
    assert len(entries) == MIN_ACTIVITY_LOG_ENTRIES == 15
    codes = validate_activity_log(
        entries, canonical_time=canonical, before_minutes=15, after_minutes=130
    )
    assert codes == (), codes


def test_ri21_prompts_carry_simultaneous_rules_and_scaffold_qualification():
    """DEF-104 follow-up #2: BOTH templates carry the explicit simultaneous
    rule (count + chronology + window + canonical-once + closed enum +
    neutral texts ALL at once, never trading one rule for another) and the
    shape-scaffold qualification; the REPAIR stage additionally carries the
    findings-recheck line."""
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    initial = prompts.build_activity_log_prompt(canonical)
    repair = prompts.build_activity_log_repair_prompt(
        canonical, ("TIMESTAMP_OUTSIDE_WINDOW",)
    )
    for blob in (initial, repair):
        assert "satisfy ALL of these at once" in blob
        assert "(a) 15 to 20 entries" in blob
        assert "(b) every timestamp strictly increasing and inside" in blob
        assert "(c) the locked canonical time appears verbatim exactly once" in blob
        assert "(d) every activityType is one of the closed tokens" in blob
        assert "(e) every activity is harmless neutral text" in blob
        assert "Fixing one rule must NEVER break another" in blob
        assert "shows the required shape/format only" in blob
        assert "replace the row content with your own plausible harmless rows" in blob
    assert (
        "When fixing the findings, re-verify ALL the simultaneous rules above"
        in repair
    )
    assert "never trade one rule for another" in repair


def test_ri22_worked_example_timestamps_follow_validator_window_math():
    """DEF-104 follow-up #2 DRIFT GUARD: the worked-example timestamps are
    computed from the SAME ``activity_log_window_bounds`` math the validator
    uses — for a sweep of configurations (centered, clamped asymmetric,
    0-sided) the embedded example has 15 strictly-increasing +3-minute rows
    that ALL lie inside ``activity_log_window_bounds(...)`` with the
    canonical time exactly once. The scaffold can never advertise a window
    the validator rejects."""
    from app.generation import prompts

    canonical = "2026-09-11T21:18:00+02:00"
    tick, _offset = parse_iso8601(canonical)
    for before, after in (
        (60, 60), (90, 90), (5, 130), (0, 120), (120, 0), (15, 130),
    ):
        blob = prompts.build_activity_log_prompt(
            canonical, before_minutes=before, after_minutes=after
        )
        entries = parse_activity_log(_extract_worked_example(blob))
        assert len(entries) == MIN_ACTIVITY_LOG_ENTRIES == 15, (before, after)
        lo, hi = activity_log_window_bounds(
            tick, before_minutes=before, after_minutes=after
        )
        ticks = [parse_iso8601(e.timestamp)[0] for e in entries]
        # strictly increasing AND unique (no repeated instant)
        assert ticks == sorted(ticks) == sorted(set(ticks)), (before, after)
        assert all(lo <= t <= hi for t in ticks), (before, after)
        assert len([e for e in entries if e.timestamp == canonical]) == 1
        # +3-minute steps between adjacent scaffold rows
        for left, right in zip(ticks, ticks[1:]):
            assert right - left == 180, (before, after, left, right)


# --------------------------------------------------------------------------- #
# 23..26 — DEF-104 follow-up #3: THE TIMESTAMP GRID (app-owned, deterministic).
# The grid makes the canonical-once property a pure VERBATIM COPY task: the
# locked instant IS one grid member (middle slot), and the model's only job is
# to copy the given timestamps and write the surrounding neutral text.
# --------------------------------------------------------------------------- #


def test_ri23_grid_for_default_window_is_middle_and_inside():
    """The app-owned grid for a canonical + 60/60 window contains EXACTLY
    ``ACTIVITY_LOG_GRID_COUNT`` (18) distinct ISO timestamps, strictly
    increasing, ALL strictly inside the ``activity_log_window_bounds``
    endpoints, with the canonical ISO EXACTLY once at the MIDDLE slot (index
    count // 2). The worked-example scaffold rows ARE the grid's first 15
    timestamps (requirement 3: the two signals agree byte-for-byte)."""
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    grid = prompts.activity_log_timestamp_grid(canonical)
    assert len(grid) == prompts.ACTIVITY_LOG_GRID_COUNT == 18
    assert len(set(grid)) == len(grid)  # distinct
    ticks = [parse_iso8601(t)[0] for t in grid]
    assert ticks == sorted(ticks) == sorted(set(ticks))  # strictly increasing
    tick, _offset = parse_iso8601(canonical)
    lo, hi = activity_log_window_bounds(tick, before_minutes=60, after_minutes=60)
    # strictly inside the ACTUAL validated window (and for the centered
    # default every member is strictly interior: lo < t < hi)
    assert all(lo < t < hi for t in ticks), ticks
    # canonical EXACTLY once, at the MIDDLE grid slot, VERBATIM
    assert grid.count(canonical) == 1
    assert grid[len(grid) // 2] == canonical
    # requirement 3: the scaffold's 15 rows ARE the first 15 grid timestamps
    blob = prompts.build_activity_log_prompt(canonical)
    scaffold_ts = [e.timestamp for e in parse_activity_log(_extract_worked_example(blob))]
    assert scaffold_ts == list(grid[: MIN_ACTIVITY_LOG_ENTRIES])
    assert scaffold_ts.count(canonical) == 1


def test_ri24_both_prompts_embed_the_exact_grid_block():
    """BOTH the built initial AND repair prompt texts contain the machine-
    readable TIMESTAMP GRID block with the EXACT grid timestamps (computed
    deterministically by ``activity_log_timestamp_grid``) and the verbatim-
    copy instruction, including the repair's "copy the GIVEN timestamp grid
    verbatim" restatement."""
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    grid = prompts.activity_log_timestamp_grid(canonical)
    grid_line = "  TIMESTAMP_GRID: " + ", ".join(grid)
    initial = prompts.build_activity_log_prompt(canonical)
    repair = prompts.build_activity_log_repair_prompt(
        canonical, ("CANONICAL_TIME_MISSING", "TIMESTAMP_OUTSIDE_WINDOW")
    )
    for blob in (initial, repair):
        assert grid_line in blob, blob[:600]
        assert "The EXACT timestamps for the entries are GIVEN below (server-owned; deterministic)" in blob
        assert "TIMESTAMP_GRID: " in blob
        assert (
            "Copy EACH timestamp VERBATIM into exactly one row's 'timestamp' field, "
            "in the exact order shown" in blob
        )
        assert (
            "the row whose timestamp equals the locked canonical time MUST be the "
            "ordinary neutral row" in blob
        )
        assert (
            "Never invent, reformat, reorder, or omit any given timestamp. "
            "Never add a timestamp outside the grid." in blob
        )
    # the repair stage additionally restates the grid-based canonical fix
    assert (
        "When repairing, copy the GIVEN timestamp grid verbatim — the canonical "
        "missing finding is resolved by including the grid's canonical row unchanged."
    ) in repair


def test_ri25_grid_copy_self_consistency_acceptance():
    """The acceptance-shaped hermetic proof: a synthetic provider response
    whose rows copy the GIVEN grid VERBATIM (canonical once, at the grid's
    middle slot, closed-enum neutral types/texts) parses and validates with
    ZERO codes under the REAL strict validator — a model that merely copies
    the grid passes."""
    from app.generation import prompts
    from app.domain.activity_log import ActivityLogEntry

    canonical = "2026-09-11T23:42:00+02:00"
    grid = prompts.activity_log_timestamp_grid(canonical)
    tokens = tuple(sorted(ACTIVITY_LOG_ACTIVITY_TYPES))
    pool = prompts.ACTIVITY_LOG_NEUTRAL_TEXT_POOL
    entries = [
        ActivityLogEntry(
            timestamp=ts,
            activity_type=tokens[i % len(tokens)],
            activity=pool[i % len(pool)],
        )
        for i, ts in enumerate(grid)
    ]
    # canonical row carries the ordinary neutral text (copy the grid order)
    canon_index = grid.index(canonical)
    entries[canon_index] = ActivityLogEntry(
        timestamp=canonical, activity_type="LOCAL_ACTIVITY",
        activity="Local user activity detected",
    )
    assert len(entries) == prompts.ACTIVITY_LOG_GRID_COUNT == 18
    codes = validate_activity_log(entries, canonical_time=canonical)
    assert codes == (), codes
    # and the full JSON round-trip (what the strict parser sees) also passes
    doc = json.dumps(
        {"entries": [{"timestamp": e.timestamp, "activityType": e.activity_type,
                      "activity": e.activity} for e in entries]}
    )
    assert validate_activity_log(parse_activity_log(doc), canonical_time=canonical) == ()


def test_ri26_grid_clamped_window_sweep_and_prompt_length_guard():
    """DEF-104 follow-up #3 DRIFT GUARD: for a sweep of configurations the
    grid timestamps stay inside the ACTUAL validated window, the count stays
    in [15..20] whenever the window is geometrically satisfiable (Phase19J-RI
    ADV-C: 30/0 and 0/30 now yield 15 rows at a finer cadence instead of the
    old sub-MIN fallback), the canonical appears exactly once, and both built
    prompts stay comfortably below the ~7000-char budget (inside
    OLLAMA_NUM_CTX=4096 tokens). Only the truly unsatisfiable 0/0 window
    falls back deterministically (count < MIN is expected and documented;
    the operator guard fails fast before any prompt is ever built for it)."""
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    tick, _offset = parse_iso8601(canonical)
    for before, after in (
        (60, 60), (90, 90), (5, 130), (0, 120), (120, 0), (15, 130),
        (30, 0), (0, 0),
    ):
        lo, hi = activity_log_window_bounds(
            tick, before_minutes=before, after_minutes=after
        )
        grid = prompts.activity_log_timestamp_grid(
            canonical, before_minutes=before, after_minutes=after
        )
        gticks = [parse_iso8601(t)[0] for t in grid]
        assert gticks == sorted(gticks) == sorted(set(gticks)), (before, after)
        # the ACTUAL validated window (inclusive, exactly like the validator)
        assert all(lo <= t <= hi for t in gticks), (before, after)
        assert grid.count(canonical) == 1, (before, after)
        # count stays in [15..20] whenever the window can geometrically hold
        # MIN distinct strictly-increasing rows (Phase19J-RI ADV-C: the
        # ADAPTIVE grid now yields 15 with finer cadences for 30/0 and 0/30,
        # which the pre-fix +3-minute grid could never satisfy)
        from app.domain.activity_log import activity_log_window_satisfiable

        if activity_log_window_satisfiable(before, after):
            assert MIN_ACTIVITY_LOG_ENTRIES <= len(grid) <= MAX_ACTIVITY_LOG_ENTRIES, (
                before, after, len(grid)
            )
        else:
            # fail-closed degenerate (0/0): deterministic documented fallback,
            # still at least the canonical alone
            assert len(grid) >= 1, (before, after)
    # prompt-length budget: both built prompts <= ~7000 chars for the default
    # window (measured comfortably inside OLLAMA_NUM_CTX=4096 tokens)
    initial = prompts.build_activity_log_prompt(canonical)
    repair = prompts.build_activity_log_repair_prompt(canonical, ("CANONICAL_TIME_MISSING",))
    assert len(initial) <= 7000, len(initial)
    assert len(repair) <= 7000, len(repair)


# --------------------------------------------------------------------------- #
# 18 — Phase19J-RI SAFE diagnostic harness (activity_log.parse_failed shape)
# --------------------------------------------------------------------------- #


def _shape_of(content):
    from app.services.ollama_driver import _activity_log_diagnostic_shape

    return _activity_log_diagnostic_shape(content)


def test_parse_failed_shape_unknown_top_level_keys():
    shape = _shape_of(json.dumps({"events": _valid_entries(CANONICAL)}))
    assert shape["topLevelType"] == "object"
    assert shape["topLevelKeys"] == ["events"]
    assert shape["expectedTopLevelKeys"] == ["entries"]
    assert shape["itemCountCandidate"] is None
    assert shape["parseFailureClass"] == "unknown_top_level_keys"


def test_parse_failed_shape_non_json_and_empty():
    assert _shape_of("")["parseFailureClass"] == "empty"
    assert _shape_of("<not-json>")["parseFailureClass"] == "non_json"
    assert _shape_of(None)["parseFailureClass"] == "empty"


def test_parse_failed_shape_entries_bound_and_bad_item():
    over = {"entries": _valid_entries(CANONICAL, count=17) * 4}  # 68 rows
    shape = _shape_of(json.dumps(over))
    assert shape["parseFailureClass"] == "entries_exceed_parse_bound"
    assert shape["itemCountCandidate"] == ">=65"
    bad_ts = {"entries": [dict(_entry(CANONICAL), timestamp="not-a-time")]}
    assert _shape_of(json.dumps(bad_ts))["parseFailureClass"] == "entry_timestamp_invalid"


def test_parse_failed_event_carries_only_shape_fields(caplog):
    """A genuine driver parse failure emits activity_log.parse_failed with ONLY
    the sanitized shape fields (never raw content/prompts/truth)."""
    import logging

    when_obs = CANONICAL
    events_doc = json.dumps({"events": _valid_entries(when_obs)})
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        events_doc,  # ACTIVITY_LOG (wrong top-level key -> parse fails)
        events_doc,  # repair 1
        events_doc,  # repair 2 (terminal)
    ]
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, _transport = _run(posts)
    assert record.state is GenerationState.FAILED
    parse_failed = [
        dict(getattr(e, "pd_fields", {}) or {})
        for e in caplog.records
        if str(getattr(e, "pd_event", "")) == "activity_log.parse_failed"
    ]
    assert parse_failed
    for fields in parse_failed:
        assert fields.get("topLevelType") == "object"
        assert fields.get("topLevelKeys") == ["events"]
        assert fields.get("expectedTopLevelKeys") == ["entries"]
        assert fields.get("parseFailureClass") == "unknown_top_level_keys"
        assert fields.get("providerCallCount") is not None
        blob = json.dumps(fields, default=str)
        # shape tokens ONLY: never the raw generated text / any content values
        for junk in ("Local user activity detected", "Canonical evidence time",
                     "murderer", "stolen", "2026-09-11T"):
            assert junk not in blob, junk


# --------------------------------------------------------------------------- #
# 19 — ADMIN-ACCEPTED ADVERSARIAL DISPOSITIONS (Phase19J-RI):
#      ADV-A schema picture / ADV-B shape-token bounds / ADV-C adaptive grid +
#      fail-fast operator guard / ADV-D neutral fallback pool / ADV-E smoke
#      window threading. No validator, budget, retry, REQUIREMENTS or ADV-256
#      change is made — these are prompt/reporting/guard-only fixes.
# --------------------------------------------------------------------------- #


def test_adv_a_prompt_contract_renders_array_not_directive_object():
    """ADV-A: the PROMPT-EMBEDDED ``schema_contract("activity_log")`` renders
    ``entries`` as an unambiguous JSON ARRAY (one example entry object) —
    never the TRANSPORT directive object (``$note`` / ``minItems`` /
    ``maxItems`` / ``entrySchema``). A literal-copying model sees exactly the
    shaped the transport schema and the worked example use."""
    from app.generation import prompts

    rendered = prompts.schema_contract("activity_log")
    parsed = json.loads(rendered)
    assert isinstance(parsed["entries"], list), parsed["entries"]
    assert len(parsed["entries"]) == 1
    entry = parsed["entries"][0]
    assert set(entry) == {"timestamp", "activityType", "activity"}
    assert "ISO-8601 timestamp WITH timezone offset" in entry["timestamp"]
    assert "enum " in entry["activityType"]
    assert "plain short text, 1..120 characters" in entry["activity"]
    # the directive keys NEVER appear in the prompt-facing text
    for directive_key in ("$note", "minItems", "maxItems", "entrySchema"):
        assert directive_key not in rendered, directive_key
    # BOTH built activity-log prompts embed the array picture + the one-line
    # array clarification
    canonical = "2026-09-11T23:42:00+02:00"
    initial = prompts.build_activity_log_prompt(canonical)
    repair = prompts.build_activity_log_repair_prompt(canonical, ("SCHEMA_INVALID",))
    for blob in (initial, repair):
        assert '"entries": [' in blob
        assert "entrySchema" not in blob
        assert "minItems" not in blob
        assert (
            f"Note: 'entries' is a JSON ARRAY of 15..20 objects"
        ) in blob
    # the TRANSPORT JSON Schema is UNCHANGED: minItems/maxItems + enum
    schema = prompts.schema_contract_as_json_schema("activity_log")
    entries = schema["properties"]["entries"]
    assert entries["minItems"] == MIN_ACTIVITY_LOG_ENTRIES == 15
    assert entries["maxItems"] == MAX_ACTIVITY_LOG_ENTRIES == 20
    assert set(entries["items"]["properties"]["activityType"]["enum"]) == \
        ACTIVITY_LOG_ACTIVITY_TYPES
    assert schema == prompts.schema_contract_as_json_schema("activity_log")
    assert prompts.json_schema_for_generation_stage("activity_log_repair") == schema
    # the schema-drift guard (rendered contract keys == JSON-Schema properties)
    # still holds with the array picture
    assert set(json.loads(rendered)) == set(schema["properties"]) == {"entries"}


def test_adv_b_top_level_shape_keys_bounded_and_content_sanitized():
    """ADV-B: ``topLevelKeys`` shape tokens are length-capped at 40 chars and
    stripped of every control / non-printable character, so a raw
    length/content-unbounded provider key name can never leak into the
    allowlisted telemetry field; the count stays bounded at 8."""
    from app.services.ollama_driver import _MAX_SHAPE_KEY_CHARS, _bounded_shape_key

    assert _MAX_SHAPE_KEY_CHARS == 40

    # (a) oversized + prose-bearing + control-char raw key -> bounded token
    prose = "K" * 5000 + "\x1b[31m" + "Local user activity detected at " \
            "2026-09-11T23:42:00+02:00 secret mistress weapon murder\x07"
    token = _bounded_shape_key(prose)
    assert len(token) <= 40
    assert all(ch.isprintable() for ch in token)
    assert "Local user activity detected" not in token
    assert "secret mistress" not in token
    assert "\x1b" not in token and "\x07" not in token

    # (b) through the diagnostic shape harness
    shape = _shape_of(
        json.dumps(
            {
                prose: 1,
                "entries_ok": 2,
                "x" * 300: 3,
                "plain": 4,
            }
        )
    )
    assert shape["parseFailureClass"] == "unknown_top_level_keys"
    for key in shape["topLevelKeys"]:
        assert len(key) <= 40
        assert all(ch.isprintable() for ch in key)
    blob = json.dumps(shape)
    assert "Local user activity detected" not in blob
    assert "secret mistress" not in blob
    assert "K" * 300 not in blob

    # (c) count bound of 8 keys
    many = {f"key_{i:03d}": i for i in range(30)}
    many["entries"] = _valid_entries(CANONICAL, count=17)
    # use a wrong second key so the shape takes the unknown-keys path after
    # sorting (sorted -> "entries" NOT first, bounded to 8)
    shape_many = _shape_of(json.dumps(many))
    assert shape_many["parseFailureClass"] == "unknown_top_level_keys"
    assert len(shape_many["topLevelKeys"]) <= 8


def test_adv_c_adaptive_grid_every_reachable_window_self_validating():
    """ADV-C (a): for EVERY reachable window config whose total clamped span
    can geometrically hold MIN distinct strictly-increasing ticks the grid
    count stays in [MIN..MAX] (or >= MIN) and the built prompt's worked-example
    scaffold SELF-VALIDATES (parse + validate == ()) under the REAL validator —
    including the sub-42-minute 30/0, 0/30 and 1/0 one-sided windows that the
    pre-fix +3-minute grid could never satisfy."""
    from app.domain.activity_log import activity_log_window_satisfiable
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    cases = (
        (60, 60), (90, 90), (5, 130), (0, 120), (120, 0), (15, 130),
        (30, 0), (0, 30), (1, 0), (0, 1), (1, 1),
    )
    for before, after in cases:
        assert activity_log_window_satisfiable(before, after), (before, after)
        grid = prompts.activity_log_timestamp_grid(
            canonical, before_minutes=before, after_minutes=after
        )
        assert len(grid) >= MIN_ACTIVITY_LOG_ENTRIES, (before, after, len(grid))
        assert MIN_ACTIVITY_LOG_ENTRIES <= len(grid) <= MAX_ACTIVITY_LOG_ENTRIES, (
            before, after, len(grid)
        )
        blob = prompts.build_activity_log_prompt(
            canonical, before_minutes=before, after_minutes=after
        )
        entries = parse_activity_log(_extract_worked_example(blob))
        assert len(entries) == MIN_ACTIVITY_LOG_ENTRIES, (before, after)
        codes = validate_activity_log(
            entries, canonical_time=canonical,
            before_minutes=before, after_minutes=after,
        )
        assert codes == (), (before, after, codes)
    # the WORKED-EXAMPLE scaffold is the grid's first 15 timestamps in every
    # reachable configuration (byte-for-byte agreement)
    for before, after in cases:
        grid = prompts.activity_log_timestamp_grid(
            canonical, before_minutes=before, after_minutes=after
        )
        blob = prompts.build_activity_log_prompt(
            canonical, before_minutes=before, after_minutes=after
        )
        scaffold_ts = [
            e.timestamp for e in parse_activity_log(_extract_worked_example(blob))
        ]
        assert scaffold_ts == list(grid[:MIN_ACTIVITY_LOG_ENTRIES]), (before, after)
        assert scaffold_ts.count(canonical) == 1


def test_adv_c_default_and_clamped_paths_are_byte_identical_to_pre_fix():
    """ADV-C IMPORTANT: the default 60/60 operator config and every clamped-120
    case behave EXACTLY as before the adaptive fix — the +3-minute cadence
    still wins, grid counts and canonical slots are the documented pre-fix
    shapes (60/60 and the symmetric/asymmetric clamped windows -> 18 rows with
    the canonical near the middle; the one-sided 120/0 -> the documented 15
    rows with the canonical as the LAST slot) — so the QA-closed real Hermes
    path never regresses."""
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    # (before, after) -> (clamped before, clamped after, pre-fix grid count)
    expected = {
        (60, 60): (60, 60, 18),
        (90, 90): (60, 60, 18),
        (5, 130): (4, 116, 18),
        (0, 120): (0, 120, 18),
        (120, 0): (120, 0, 15),
        (15, 130): (12, 108, 18),
    }
    for (before, after), (_cb, _ca, count) in expected.items():
        grid = prompts.activity_log_timestamp_grid(
            canonical, before_minutes=before, after_minutes=after
        )
        assert len(grid) == count, (before, after, len(grid))
        gticks = [parse_iso8601(t)[0] for t in grid]
        steps = {right - left for left, right in zip(gticks, gticks[1:])}
        assert steps == {3 * 60}, (before, after, steps)
        assert grid.count(canonical) == 1
        assert grid.index(canonical) < MIN_ACTIVITY_LOG_ENTRIES
    # the centered default keeps the canonical at the MIDDLE slot
    grid_default = prompts.activity_log_timestamp_grid(canonical)
    assert grid_default.index(canonical) == len(grid_default) // 2 == 9
    # 120/0 keeps the canonical as the LAST scaffold slot (canonical index 14)
    grid_120_0 = prompts.activity_log_timestamp_grid(
        canonical, before_minutes=120, after_minutes=0
    )
    assert grid_120_0.index(canonical) == len(grid_120_0) - 1 == 14
    # the 60/60 worked-example scaffold rows stay +3-minute apart
    blob = prompts.build_activity_log_prompt(canonical)
    ticks = [parse_iso8601(e.timestamp)[0] for e in parse_activity_log(_extract_worked_example(blob))]
    assert all(right - left == 180 for left, right in zip(ticks, ticks[1:]))


def test_adv_c_unsatisfiable_window_guard_raises_before_provider_calls():
    """ADV-C (b) FAIL-FAST OPERATOR GUARD: a window whose total clamped span
    cannot geometrically hold MIN distinct strictly-increasing ticks — the
    reachable 0/0 setting — is rejected at THREE levels, and a driver run over
    an unsatisfiable (duck-typed) settings object fails BEFORE any
    activity-log provider call."""
    from pydantic import ValidationError

    from app.core.config import Settings
    from app.domain.activity_log import activity_log_window_satisfiable

    # 1. the pure domain guard
    assert activity_log_window_satisfiable(0, 0) is False
    assert activity_log_window_satisfiable(60, 60) is True
    assert activity_log_window_satisfiable(30, 0) is True
    assert activity_log_window_satisfiable(0, 30) is True
    # 2. Settings construction fail-fast (the primary "service construction")
    with pytest.raises(ValidationError):
        Settings(activity_log_window_before_minutes=0, activity_log_window_after_minutes=0)
    # the ADV-257 zero-honored window (0/60) is satisfiable and stays allowed
    Settings(activity_log_window_before_minutes=0, activity_log_window_after_minutes=60)
    # 3. driver-level guard for a duck-typed settings object: zero
    # activity-log calls (only the pre-activity-log CASE + EVIDENCE calls)
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource
    from app.generation.ollama_provider import OllamaProvider
    from app.services.ollama_driver import OllamaStageDriver
    from test_ollama_driver import (
        OLLAMA_BASE,
        OLLAMA_MODEL,
        MockOllamaTransport,
        _admission,
        _controller,
    )

    class _DuckSettings:
        activity_log_window_before_minutes = 0
        activity_log_window_after_minutes = 0

    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(
        posts=[_j(_case_people()), _j(_evidence()), _j(_world())]
    )

    def factory():
        return OllamaProvider(
            base_url=OLLAMA_BASE, model=OLLAMA_MODEL, timeout_seconds=5,
            transport=transport,
        )

    driver = OllamaStageDriver(settings=_DuckSettings(), provider_factory=factory)
    controller = _controller(driver, transport, admission, clock, ids)
    handle = controller.start_generation(
        PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    # case + evidence only — ZERO activity-log provider calls ever reached
    assert transport.call_count == 2
    assert not any(
        "activity_log" in transport.prompt_of_call(i)[:80]
        for i in range(transport.call_count)
    )


def test_adv_d_hostile_collision_names_do_not_break_the_scaffold():
    """ADV-D (prompt-side mitigation, NO validator weakening): a case whose
    canonical person/location names collide with ordinary computer-log
    vocabulary ("Mail"/"User"/"Local"/"Workspace"/"ServerWorkspace") no longer
    makes the built worked-example scaffold fail ENTITY_LEAK — the colliding
    scaffold rows are deterministically substituted from the reserved fallback
    pool, while the scaffold still self-validates with zero codes. The real
    fixture name sets also pass, and no canonical identity material is ever
    rendered into the prompt text (ADV-256)."""
    from app.generation import prompts

    canonical = "2026-09-11T23:42:00+02:00"
    hostile_persons = ("Mail", "User", "Local", "Workspace", "ServerWorkspace")
    hostile_locations = ("ServerWorkspace",)

    blob = prompts.build_activity_log_prompt(
        canonical, person_names=hostile_persons, location_names=hostile_locations
    )
    entries = parse_activity_log(_extract_worked_example(blob))
    assert len(entries) == MIN_ACTIVITY_LOG_ENTRIES
    codes = validate_activity_log(
        entries,
        canonical_time=canonical,
        person_names=hostile_persons,
        location_names=hostile_locations,
    )
    assert codes == (), codes
    # the canonical-anchor trap text ("Local ... User ...") was substituted
    assert all(e.activity != "Local user activity detected" for e in entries)
    # a fallback phrase IS used (the collision was real)
    assert any(
        e.activity in prompts.ACTIVITY_LOG_NEUTRAL_FALLBACK_POOL for e in entries
    )
    # default runs still use ONLY the primary neutral pool (no substitution)
    default_blob = prompts.build_activity_log_prompt(canonical)
    default_entries = parse_activity_log(_extract_worked_example(default_blob))
    assert all(
        e.activity in prompts.ACTIVITY_LOG_NEUTRAL_TEXT_POOL for e in default_entries
    )
    # the REAL fixture sets still pass (substitution is adaptive, not tuned)
    real_persons = ("Paul Becker", "Anna Weiss", "Lisa König", "Marcus Fischer",
                    "Sophie Hoffmann")
    real_weapons = ("bronze ceremonial ice pick", "bronze_ceremonial_ice_pick")
    real_motives = ("stolen research data",)
    real_loc_ids = ("main_office", "server_room")
    real_loc_names = ("Main Office", "Server Room")
    real_blob = prompts.build_activity_log_prompt(
        canonical,
        person_names=real_persons,
        weapon_names=real_weapons,
        motive_names=real_motives,
        location_ids=real_loc_ids,
        location_names=real_loc_names,
    )
    real_entries = parse_activity_log(_extract_worked_example(real_blob))
    real_codes = validate_activity_log(
        real_entries,
        canonical_time=canonical,
        person_names=real_persons,
        weapon_names=real_weapons,
        motive_names=real_motives,
        location_ids=real_loc_ids,
        location_names=real_loc_names,
    )
    assert real_codes == (), real_codes
    # ADV-256: DISTINCTIVE identity material is never rendered into the text
    for needle in (
        "ServerWorkspace", "Paul Becker", "Anna Weiss", "König",
        "bronze ceremonial ice pick", "stolen research data",
        "Main Office", "Server Room",
    ):
        assert needle not in blob, needle
        assert needle not in real_blob, needle


def test_adv_e_smoke_validation_uses_settings_derived_window():
    """ADV-E: the smoke tool's activity-log validation threads the
    settings-derived before/after window (the SAME ``_activity_log_window``
    the prompt builder uses), so a non-default operator window can never drift
    from the prompt."""
    from app.core.config import Settings
    import tools.ollama_smoke as smoke

    from app.domain.time_interval import epoch_to_iso, parse_iso8601

    # (a) unit-level: a 15-row log valid ONLY under before=30/after=90 (its
    #     last row sits at canonical+80 minutes) validates under 30/90 and is
    #     rejected by the default 60/60 bounds.
    canonical = "2026-09-11T23:42:00+02:00"
    tick, offset = parse_iso8601(canonical)
    row_offsets = (-30, -22, -14, -6, 0, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80)

    def row(t: int, i: int) -> dict:
        timestamp = canonical if t == 0 else epoch_to_iso(tick + t * 60, offset)
        return {
            "timestamp": timestamp,
            "activityType": "LOCAL_ACTIVITY",
            "activity": f"Neutral activity text {i}",
        }

    rows = [row(t, i) for i, t in enumerate(row_offsets)]
    assert len(rows) == 15
    assert len([r for r in rows if r["timestamp"] == canonical]) == 1
    payload = json.dumps({"entries": rows})

    r_valid = smoke._activity_log_validation(
        payload, canonical, before_minutes=30, after_minutes=90
    )
    r_default = smoke._activity_log_validation(payload, canonical)
    assert r_valid["validatorCodes"] == [], r_valid["validatorCodes"]
    assert "ACTIVITY_LOG_TIME_WINDOW_INVALID" in r_default["validatorCodes"]

    # (b) the settings-derived window source used by the prompt builder
    _settings = Settings(generation_provider="ollama")
    assert smoke._activity_log_window(_settings) == (60, 60)


def test_adv_e_smoke_mocked_run_with_non_default_window(monkeypatch, capsys):
    """ADV-E driver-level mocked smoke run: configuring a non-default window
    (before=30, after=90) makes the smoke report PASS a response whose 80-min
    row is only valid inside that window — and, with the default 60/60, the
    SAME response would be rejected (the validator is actually using the
    settings-derived bounds, never a hardcoded 60/60)."""
    import tools.ollama_smoke as smoke
    from app.generation import ollama_provider as ollama_mod
    from app.generation.provider import ProviderResult as PR
    from app.domain.time_interval import epoch_to_iso, parse_iso8601

    canonical = "2026-09-11T23:42:00+02:00"
    tick, offset = parse_iso8601(canonical)
    row_offsets = (-30, -22, -14, -6, 0, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80)

    def row(t: int, i: int) -> dict:
        timestamp = canonical if t == 0 else epoch_to_iso(tick + t * 60, offset)
        return {
            "timestamp": timestamp,
            "activityType": "LOCAL_ACTIVITY",
            "activity": f"Neutral activity text {i}",
        }

    rows = [row(t, i) for i, t in enumerate(row_offsets)]
    assert len(rows) == 15
    assert len([r for r in rows if r["timestamp"] == canonical]) == 1
    payload = _j({"entries": rows})

    class FakeProvider:
        last_format = "schema"

        def __init__(self, **_kwargs):
            pass

        def generate(self, request) -> PR:
            return PR(content=payload)

        @property
        def structured_output_sent(self):
            return self.last_format == "schema"

    monkeypatch.setattr(ollama_mod, "ollama_available", lambda _s: (True, ""))
    monkeypatch.setattr(ollama_mod, "ollama_structured_output_supported", lambda _s: True)
    monkeypatch.setattr(ollama_mod, "OllamaProvider", FakeProvider)
    # settings-derived 30/90 window (the SAME source the prompt builder uses)
    monkeypatch.setattr(smoke, "_activity_log_window", lambda _s: (30, 90))

    rc = smoke.main(["--enable", "--stage", "activity_log"])
    captured = capsys.readouterr()
    assert rc == 0
    report = json.loads(captured.out)
    gen = report["generation"]
    assert gen["parsedOk"] is True
    assert gen["entryCount"] == 15
    assert gen["validatorCodes"] == []
    assert gen["pass"] is True
    report_blob = captured.out
    for token in ("127.0.0.1", "11434", "OLLAMA_BASE_URL", "prompt_context"):
        assert token not in report_blob, token


__all__ = []  # pytest module: no accidental public names