"""Phase 37 — Fix Activity-Log Entity Repair (backend regression).

Phase37-Fix-Activity-Log-Entity-Repair.md §7/§10/§11/§13/§15/§16/§17/§22/§23/§24/§25/§26/§27.

The proven defect: ``ACTIVITY_LOG_ENTITY_LEAK`` repairs received the code
``ACTIVITY_LOG_ENTITY_LEAK`` / ``ENTITY_LEAK_DETECTED`` but never a positive
proven-safe output vocabulary — "regenerate and hope" repeated the same
failure. The validator uses a case-derived HIDDEN forbidden vocabulary that
neither the initial nor the repair prompt may receive (time-only privacy
design, §6/§19).

Phase 37 fix contract (these tests pin it):

1. A DETERMINISTIC collision-checked safe neutral vocabulary builder
   (``prompts.activity_log_safe_neutral_vocabulary``) filters the canonical
   pools through the EXACT existing entity-leak collider with the case's
   hidden tokens; primary pool when sufficient, else extends from the
   collision-checked fallback pool (§16/§17); fail-closed when pathological.
2. The INITIAL prompt receives the same safe vocabulary via the positive
   ``ACTIVITY TEXT RULES`` block (§10).
3. ``ACTIVITY_LOG_ENTITY_LEAK`` gets a DEDICATED repair directive and never
   falls through the generic "other" path (§11).
4. The REPAIR prompt receives the SAME vocabulary (same builder, §25) and
   never the hidden forbidden values, rejection reasons or rejected text
   (§6/§12/§24/§19).
5. The strict validator is UNCHANGED: every person/weapon/motive/location/
   static-term/name-variant collision still fails (§26) and ordinary neutral
   rows still pass (§15).
6. Driver-level RED→GREEN: initial → ``ACTIVITY_LOG_ENTITY_LEAK`` → repair →
   valid replacement → validator PASS → PUBLISHED (§22).
7. Bounded safe diagnostics (rowIndex / matchCategory / safeVocabularySize)
   with a closed match-category enum (§13/§27).

Deterministic-testing policy: no wall-clock sleeps, no real LLM — canned
responses through the existing ``MockOllamaTransport`` driver harness; the
prompt builders are pure.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.activity_log import (  # noqa: E402
    MIN_ACTIVITY_LOG_ENTRIES,
    ActivityLogValidatorCode,
    parse_activity_log,
    validate_activity_log,
)
from app.domain.time_interval import epoch_to_iso, parse_iso8601  # noqa: E402
from app.generation import prompts  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402

from test_ollama_driver import (  # noqa: E402
    ICEPICK_SPEC,
    _ALOG_TEXTS,
    _ALOG_TYPES,
    _alog,
    _alog_posts,
    _case_people,
    _evidence,
    _j,
    _run,
    _world,
)

CANONICAL = "2026-09-11T23:42:00+02:00"

# The case's HIDDEN forbidden vocabulary (the exact token families the
# driver derives from ``_activity_log_forbidden_tokens`` for this fixture).
PERSON_TOKENS = (
    "Dr. Anna Weiss", "anna_weiss", "Paul Becker", "paul_becker",
    "Marcus Fischer", "marcus_fischer", "Sophie Hoffmann", "sophie_hoffmann",
    "Lisa König", "lisa_koenig",
)
WEAPON_TOKENS = ("bronze ceremonial ice pick", "bronze_ceremonial_ice_pick")
MOTIVE_TOKENS = (
    "Wanted to steal the research data", "stolen_research_data",
    "A financial settlement dispute", "financial_settlement",
    "A personal grudge over a promotion", "personal_grudge",
)
LOCATION_ID_TOKENS = (
    "konsortium_office", "research_lab", "motor_lodge",
)
LOCATION_NAME_TOKENS = (
    "Konsortium Office", "Research Laboratory", "Motor Lodge",
)

# Sentinel hidden values that must NEVER appear in a rendered prompt (SECURITY
# GATE, §24) — real hidden case truth + a unique synthetic sentinel.
HIDDEN_VALUE_SENTINELS = (
    *PERSON_TOKENS,
    *WEAPON_TOKENS,
    *MOTIVE_TOKENS,
    *LOCATION_ID_TOKENS,
    *LOCATION_NAME_TOKENS,
    "solverProof", "caseTruth", "crimeTime", "murdererId", "weaponId",
    "motiveId", "victimId", "konsortium",
)

VOCAB_MARKER = "SAFE NEUTRAL ACTIVITY VOCABULARY (use ONLY these for every activity): "


def _forbidden(**extra):
    return dict(
        person_names=PERSON_TOKENS,
        weapon_names=WEAPON_TOKENS,
        motive_names=MOTIVE_TOKENS,
        location_ids=LOCATION_ID_TOKENS,
        location_names=LOCATION_NAME_TOKENS,
        **extra,
    )


def _extract_vocab(blob: str) -> tuple[str, ...]:
    """The comma-separated safe-vocabulary phrases rendered into a prompt."""
    assert VOCAB_MARKER in blob
    tail = blob.split(VOCAB_MARKER, 1)[1]
    line = tail.split("\n", 1)[0]
    return tuple(p.strip() for p in line.split(",") if p.strip())


def _valid_rows(canonical: str, activity_text: str, at_row: int) -> list[dict]:
    """15 strictly-increasing rows around ``canonical`` +3 minutes, closed
    enum types, neutral texts; row ``at_row`` carries ``activity_text``."""
    tick, offset = parse_iso8601(canonical)
    start = tick - 7 * 180
    rows: list[dict] = []
    for i in range(MIN_ACTIVITY_LOG_ENTRIES):
        is_canon = (start + i * 180) == tick
        rows.append(
            {
                "timestamp": canonical if is_canon else epoch_to_iso(start + i * 180, offset),
                "activityType": _ALOG_TYPES[i % len(_ALOG_TYPES)],
                "activity": (
                    activity_text
                    if i == at_row
                    else _ALOG_TEXTS[i % len(_ALOG_TEXTS)]
                ),
            }
        )
    return rows


def _valid_doc(canonical: str, activity_text: str, at_row: int = 3) -> str:
    return _j({"entries": _valid_rows(canonical, activity_text, at_row)})


def _first_activity_log_prompt(transport) -> str:
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


def _when_obs() -> str:
    tick, offset = parse_iso8601(CANONICAL)
    return epoch_to_iso(tick - 10, offset)


# --------------------------------------------------------------------------- #
# §16/§17/§23 — deterministic collision-safe vocabulary builder
# --------------------------------------------------------------------------- #


def test_safe_vocabulary_deterministic_and_pure():
    """Same hidden inputs ⇒ same output; pure (no provider/randomness)."""
    v1 = prompts.activity_log_safe_neutral_vocabulary(**_forbidden())
    v2 = prompts.activity_log_safe_neutral_vocabulary(**_forbidden())
    assert v1 == v2
    assert all(isinstance(p, str) and p for p in v1)


def test_safe_vocabulary_default_uses_full_primary_pool():
    """No forbidden tokens → the SAFE vocabulary is the full primary pool."""
    simple = prompts.activity_log_safe_neutral_vocabulary()
    assert simple == prompts.ACTIVITY_LOG_NEUTRAL_TEXT_POOL
    assert len(simple) == 17 >= prompts.ACTIVITY_LOG_SAFE_VOCABULARY_MIN_SIZE


def test_safe_vocabulary_filters_primary_colliders_and_extends_fallback():
    """Hostile hidden names covering common computer-log words (Mail/User/
    Local/Workspace/ServerWorkspace) filter the colliding PRIMARY phrases and
    extend from the collision-checked FALLBACK pool (§17). Every supplied
    phrase passes the canonical checker."""
    hostile = dict(
        person_names=("Mail", "User", "Local", "Workspace", "ServerWorkspace"),
        location_names=("ServerWorkspace",),
    )
    vocab = prompts.activity_log_safe_neutral_vocabulary(**hostile)
    assert len(vocab) >= prompts.ACTIVITY_LOG_SAFE_VOCABULARY_MIN_SIZE
    # every colliding primary phrase is filtered OUT
    for phrase in (
        "User session login recorded", "Mail client synchronized",
        "User session unlocked", "Local file written",
        "File copied to local workspace", "Local user activity detected",
    ):
        assert phrase not in vocab, phrase
    # collision-checked fallback phrases ARE used (primary was insufficient)
    assert any(p in prompts.ACTIVITY_LOG_NEUTRAL_FALLBACK_POOL for p in vocab)
    # every supplied phrase passes the SAME canonical checker
    for phrase in vocab:
        assert not prompts._neutral_text_collides(phrase, **hostile), phrase


def test_safe_vocabulary_filters_fallback_colliders_too():
    """Colliding FALLBACK phrases are filtered with the same canonical
    checker; only safe fallback phrases extend the vocabulary."""
    hostile = dict(
        person_names=(
            "Mail", "User", "Local", "Workspace", "System", "Network", "Storage",
        )
    )
    vocab = prompts.activity_log_safe_neutral_vocabulary(**hostile)
    assert "System clock synchronization heartbeat" not in vocab
    assert "Network daemon handshake completed" not in vocab
    assert "Storage controller cache flushed" not in vocab
    # the non-colliding fallback phrases ARE supplied
    for phrase in (
        "Peripheral input device reconnected",
        "Display framebuffer refresh scheduled",
        "Process scheduler tick recorded",
    ):
        assert phrase in vocab, phrase
    for phrase in vocab:
        assert not prompts._neutral_text_collides(phrase, **hostile), phrase


def test_safe_vocabulary_real_case_full_pool():
    """The real fixture hidden tokens (persons/weapons/motives/locations) do
    NOT collide with the canonical pool → the full primary pool is supplied
    (this is what the driver RED→GREEN path renders)."""
    vocab = prompts.activity_log_safe_neutral_vocabulary(**_forbidden())
    assert vocab == prompts.ACTIVITY_LOG_NEUTRAL_TEXT_POOL
    for phrase in vocab:
        assert not prompts._neutral_text_collides(phrase, **_forbidden()), phrase


def test_safe_vocabulary_fail_closed_on_pathological_exhaustion():
    """A pathological forbidden set covering (nearly) the whole bounded
    vocabulary fails-closed deterministically rather than ever supplying a
    phrase the strict validator would reject (§16/§17)."""
    with pytest.raises(ValueError):
        prompts.activity_log_safe_neutral_vocabulary(
            person_names=(
                "Mail", "User", "Local", "Workspace", "ServerWorkspace",
                "System", "Network", "Storage", "Display", "Peripheral",
            )
        )


# --------------------------------------------------------------------------- #
# §26 — validator preserved: mutations still fail, safe phrases still pass
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "injected, category",
    [
        ("Paul Becker used the workstation", "PERSON_TOKEN"),
        ("mail from paul_becker opened", "PERSON_TOKEN"),
        ("Konsortium Office cleaning job queued", "LOCATION_TOKEN"),
        ("file copied to konsortium_office", "LOCATION_TOKEN"),
        ("the bronze ceremonial ice pick was dusted", "WEAPON_TOKEN"),
        ("a bronze_ceremonial_ice_pick inventory row", "WEAPON_TOKEN"),
        ("Wanted to steal the research data leaked", "MOTIVE_TOKEN"),
        ("stolen_research_data resolved", "MOTIVE_TOKEN"),
    ],
)
def test_mutation_entity_collisions_still_rejected(injected, category):
    """Person / location / weapon / motive tokens injected into one row still
    produce ACTIVITY_LOG_ENTITY_LEAK (the strict matcher is UNCHANGED)."""
    entries = parse_activity_log(_valid_doc(_when_obs(), injected))
    codes = validate_activity_log(entries, canonical_time=_when_obs(), **_forbidden())
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes, codes
    diag = prompts.activity_log_entity_leak_diagnostics(
        entries, **_forbidden()
    )
    assert diag["matchCategory"] == category, diag
    assert diag["rowIndex"] == 3


@pytest.mark.parametrize(
    "injected",
    [
        "payouts arranged for the contractor",
        "blackmail notes removed from the tray",
        "embezzlement ledger scanned",
        "the knife drawer was inventoried",
        "weapon casing disposed of",
    ],
)
def test_mutation_static_forbidden_terms_still_rejected(injected):
    """STATIC forbidden-content terms (ADV-255 c) still fail via
    STATIC_FORBIDDEN_TERM."""
    entries = parse_activity_log(_valid_doc(_when_obs(), injected))
    codes = validate_activity_log(entries, canonical_time=_when_obs(), **_forbidden())
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes, codes
    diag = prompts.activity_log_entity_leak_diagnostics(entries, **_forbidden())
    assert diag["matchCategory"] == "STATIC_FORBIDDEN_TERM", diag


def test_collision_safe_phrase_accepted():
    """A collision-safe neutral vocabulary phrase validates with ZERO codes —
    the closed safe output contract actually passes the strict validator."""
    entries = parse_activity_log(
        _valid_doc(_when_obs(), "Browser tab opened")
    )
    assert validate_activity_log(
        entries, canonical_time=_when_obs(), **_forbidden()
    ) == ()


def test_safe_vocabulary_phrases_all_validate_under_canonical_checker():
    """EVERY phrase the builder supplies passes the strict validator for the
    case (the canonical checker is the acceptance authority)."""
    vocab = prompts.activity_log_safe_neutral_vocabulary(**_forbidden())
    for phrase in vocab:
        entries = parse_activity_log(_valid_doc(_when_obs(), phrase))
        assert validate_activity_log(
            entries, canonical_time=_when_obs(), **_forbidden()
        ) == (), phrase


# --------------------------------------------------------------------------- #
# §15 — false-positive boundaries (short/common names, static prefixes)
# --------------------------------------------------------------------------- #


def test_short_common_person_name_does_not_overblock_ordinary_rows():
    """A short/common canonical person name ('Jo') that coincides with an
    ordinary word must NOT over-block ordinary non-entity rows, while a real
    collision still fails."""
    short = dict(
        person_names=("Jo",),
        weapon_names=(),
        motive_names=(),
        location_ids=(),
        location_names=(),
    )
    entries = parse_activity_log(_valid_doc(_when_obs(), "joined the daily call"))
    assert validate_activity_log(
        entries, canonical_time=_when_obs(), **short
    ) == ()
    # even a direct row mentioning 'Jo' as a name IS rejected (person leak)
    entries2 = parse_activity_log(_valid_doc(_when_obs(), "Jo requested access"))
    codes = validate_activity_log(entries2, canonical_time=_when_obs(), **short)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes


def test_static_prefix_boundary_no_false_positive_for_ordinary_length_words():
    """A static forbidden PREFIX ('payout') must not over-block ordinary
    words that merely share letters ('payment'), while real prefix forms
    ('payouts') still fail."""
    short = dict(
        person_names=(),
        weapon_names=(),
        motive_names=(),
        location_ids=(),
        location_names=(),
    )
    entries = parse_activity_log(_valid_doc(_when_obs(), "payment gateway synced"))
    assert validate_activity_log(
        entries, canonical_time=_when_obs(), **short
    ) == ()
    entries2 = parse_activity_log(_valid_doc(_when_obs(), "payouts processed"))
    codes = validate_activity_log(entries2, canonical_time=_when_obs(), **short)
    assert ActivityLogValidatorCode.ACTIVITY_LOG_ENTITY_LEAK in codes


# --------------------------------------------------------------------------- #
# §24/§25 — prompt alignment + secrecy (both prompts share the safe builder's
# vocabulary; hidden values + rejected text never reach the provider)
# --------------------------------------------------------------------------- #


def test_your_prompt_alignment_initial_and_repair_share_safe_vocabulary():
    """The INITIAL and the REPAIR prompt receive the SAME collision-checked
    safe vocabulary (same deterministic builder, §7/§16/§25)."""
    initial = prompts.build_activity_log_prompt(CANONICAL, **_forbidden())
    repair = prompts.build_activity_log_repair_prompt(
        CANONICAL, ("ENTITY_LEAK_DETECTED",),
        validator_code="ACTIVITY_LOG_ENTITY_LEAK",
        **_forbidden(),
    )
    initial_vocab = _extract_vocab(initial)
    repair_vocab = _extract_vocab(repair)
    assert initial_vocab == repair_vocab
    assert initial_vocab == prompts.activity_log_safe_neutral_vocabulary(
        **_forbidden()
    )


def test_prompt_initial_has_activity_text_rules_and_closed_vocabulary():
    blob = prompts.build_activity_log_prompt(CANONICAL, **_forbidden())
    assert "ACTIVITY TEXT RULES" in blob
    assert "- Use ONLY the supplied neutral activity phrases for every \"activity\" value." in blob
    assert "- Do not introduce names, locations, weapons, motives, organizations, or other proper entities." in blob
    assert "- Do not invent synonyms or additional named entities." in blob
    assert VOCAB_MARKER in blob
    for phrase in prompts.ACTIVITY_LOG_NEUTRAL_TEXT_POOL:
        assert phrase in blob, phrase


def test_prompt_entity_leak_has_dedicated_directive_not_generic():
    """§11/§25: the leak repair carries the DEDICATED directive and does NOT
    fall through the generic \"other\" path."""
    blob = prompts.build_activity_log_repair_prompt(
        CANONICAL, ("ENTITY_LEAK_DETECTED",),
        validator_code="ACTIVITY_LOG_ENTITY_LEAK",
        **_forbidden(),
    )
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_ENTITY_LEAK" in blob
    assert "- ENTITY_LEAK_DETECTED" in blob
    assert (
        "- ACTIVITY_LOG_ENTITY_LEAK: regenerate the COMPLETE activity log using "
        "ONLY the supplied safe neutral vocabulary" in blob
    )
    assert (
        "do NOT invent activity phrases or introduce named people, places, "
        "weapons, motives, organizations." in blob
    )
    # the generic fallback directive is NOT used for the leak path
    assert "- other: preserve ALL simultaneous rules" not in blob


def test_prompt_generic_other_path_still_present_for_non_leak_codes():
    """Phase 26 behavior preserved: the generic \"other\" directive is still
    used for repairs whose findings carry NO entity leak."""
    blob = prompts.build_activity_log_repair_prompt(
        CANONICAL, ("SCHEMA_INVALID",),
        validator_code="ACTIVITY_LOG_SCHEMA_INVALID",
        **_forbidden(),
    )
    assert "- other: preserve ALL simultaneous rules; never trade one for another." in blob
    assert "ACTIVITY_LOG_ENTITY_LEAK: " not in blob


def test_prompt_secrecy_initial_and_repair_never_carry_hidden_values():
    """SECURITY GATE (§24): rendered INITIAL and REPAIR prompts never contain
    the case's hidden forbidden values or rejected text — sentinel-based."""
    initial = prompts.build_activity_log_prompt(CANONICAL, **_forbidden())
    repair = prompts.build_activity_log_repair_prompt(
        CANONICAL, ("ENTITY_LEAK_DETECTED",),
        validator_code="ACTIVITY_LOG_ENTITY_LEAK",
        **_forbidden(),
    )
    for blob in (initial, repair):
        for sentinel in HIDDEN_VALUE_SENTINELS:
            assert sentinel not in blob, f"{sentinel!r} leaked into a prompt"
        # the hidden values are used ONLY for the deterministic collision
        # check; they never reach the provider as text (§6/§19).
        assert "User session login recorded" in blob  # a safe phrase is there


# --------------------------------------------------------------------------- #
# §22/§24 — DRIVER-level RED→GREEN: initial leak → repair → valid → PASS
# --------------------------------------------------------------------------- #


def test_driver_entity_leak_repair_red_to_green_publishes():
    """A REAL driver round-trip: the initial activity log collides with a
    hidden person token → ACTIVITY_LOG_ENTITY_LEAK → the repair prompt carries
    the DEDICATED directive + the SAME safe vocabulary → the repaired log is
    valid → the whole attempt PUBLISHES."""
    when_obs = _when_obs()
    leaky = _alog(when_obs)
    leaky["entries"][7]["activity"] = (
        "SENTINEL-LEAKY-9142: Paul Becker requested the log"
    )
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _j(leaky),
        _j(_alog(when_obs)),               # the VALID repaired log
        *_alog_posts(CANONICAL)[1:],       # remaining valid activity logs
        _j(_world()),
        ICEPICK_SPEC,
    ]
    record, transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    initial = _first_activity_log_prompt(transport)
    repairs = _repair_prompts(transport)
    assert len(repairs) == 1
    repair = repairs[0]

    # -- the repair was guided by the EXACT leak code ------------------------
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_ENTITY_LEAK" in repair

    # -- the repair carries the DEDICATED directive (not the generic path) ---
    assert (
        "- ACTIVITY_LOG_ENTITY_LEAK: regenerate the COMPLETE activity log using "
        "ONLY the supplied safe neutral vocabulary" in repair
    )
    assert "- other: preserve ALL simultaneous rules" not in repair

    # -- both prompts share the SAME safe vocabulary (same builder) ----------
    initial_vocab = _extract_vocab(initial)
    repair_vocab = _extract_vocab(repair)
    assert initial_vocab == repair_vocab
    assert initial_vocab == prompts.ACTIVITY_LOG_NEUTRAL_TEXT_POOL
    for phrase in initial_vocab:
        assert phrase in repair

    # -- the initial prompt carries the positive closed contract -------------
    assert "ACTIVITY TEXT RULES" in initial

    # -- SECURITY GATE: hidden values + rejected text never reached the
    #    provider (§24-forbidden values, §12 rejected text) ------------------
    for blob in (initial, repair):
        for sentinel in HIDDEN_VALUE_SENTINELS:
            assert sentinel not in blob, f"{sentinel!r} leaked into a prompt"
        assert "SENTINEL-LEAKY-9142" not in blob
        assert "Paul Becker requested the log" not in blob

    # -- every supplied safe phrase passes the canonical checker -------------
    for phrase in repair_vocab:
        assert not prompts._neutral_text_collides(phrase, **_forbidden()), phrase


# --------------------------------------------------------------------------- #
# §13/§27 — bounded safe diagnostics (never hidden values / raw strings)
# --------------------------------------------------------------------------- #


def test_diagnostics_bounded_safe_fields_only():
    diag = prompts.activity_log_entity_leak_diagnostics(
        parse_activity_log(
            _valid_doc(_when_obs(), "Konsortium Office sweeper queued")
        ),
        **_forbidden(),
    )
    assert set(diag) == {"rowIndex", "matchCategory", "safeVocabularySize"}
    assert diag["rowIndex"] == 3
    assert diag["matchCategory"] == "LOCATION_TOKEN"
    assert diag["safeVocabularySize"] == len(
        prompts.activity_log_safe_neutral_vocabulary(**_forbidden())
    )
    assert "Paul Becker" not in str(diag)
    assert "Konsortium Office" not in str(diag)


def test_diagnostics_no_match_returns_none_row_index():
    diag = prompts.activity_log_entity_leak_diagnostics(
        parse_activity_log(_valid_doc(_when_obs(), "Browser tab opened")),
        **_forbidden(),
    )
    assert diag["rowIndex"] is None
    assert diag["matchCategory"] is None
    assert diag["safeVocabularySize"] > 0


def test_driver_username_form_person_leak_repair_publishes(caplog):
    """Driver-level person NAME-VARIANT leak (username form ``paul_becker``)
    → ACTIVITY_LOG_ENTITY_LEAK → dedicated repair → valid → PUBLISHED; the
    driver's `validation_failed` event still carries the EXACT leak code
    (safe allowlisted fields only)."""
    when_obs = _when_obs()
    leaky = _alog(when_obs)
    leaky["entries"][2]["activity"] = "mail from paul_becker opened"
    posts = [
        _j(_case_people()),
        _j(_evidence()),
        _j(leaky),
        _j(_alog(when_obs)),
        *_alog_posts(CANONICAL)[1:],
        _j(_world()),
        ICEPICK_SPEC,
    ]
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, transport = _run(posts)
    assert record.state is GenerationState.PUBLISHED
    repair = _repair_prompts(transport)[0]
    assert "VALIDATOR FAILURE CODE: ACTIVITY_LOG_ENTITY_LEAK" in repair
    assert "paul_becker" not in repair
    events = [
        (str(getattr(e, "pd_event", "")), dict(getattr(e, "pd_fields", {}) or {}))
        for e in caplog.records
        if str(getattr(e, "pd_event", "")).startswith("activity_log.")
    ]
    failed = [f for name, f in events if name == "activity_log.validation_failed"]
    assert failed
    assert failed[0]["validatorCode"] == "ACTIVITY_LOG_ENTITY_LEAK"
    # no hidden value ever crossed the safe event boundary
    assert "paul_becker" not in str(failed[0])
    assert "Paul Becker" not in str(failed[0])
    assert "mail from" not in str(failed[0])


# --------------------------------------------------------------------------- #
# helper sanity — the driver derives the same hidden tokens used above
# --------------------------------------------------------------------------- #


def test_driver_forbidden_token_derivation_matches_fixture():
    """Prove the hidden token sets the tests model match what the driver
    actually derives for this fixture (secrecy assertions are REAL)."""
    from app.generation import prompts as p  # noqa: F401 (import side effect)
    from app.services.ollama_driver import OllamaStageDriver

    # static sanity: the fixture's own canonical tokens are what we assert
    assert all(t in HIDDEN_VALUE_SENTINELS for t in PERSON_TOKENS)
    assert "Konsortium Office" in HIDDEN_VALUE_SENTINELS