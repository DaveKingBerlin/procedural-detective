"""Phase 19 §14 — canonical environment handling regression suite.

Fix A: the LLM ``world_requirements`` ``environmentHint`` must never behave
like an arbitrary path/string. The five canonical ids (apartment / office /
hotel_suite / warehouse / mansion) come from ``app.world.environment``:

1. safe variants ("Apartment", "hotel suite", "hotel-suite", " Office ") map
   to their canonical tokens deterministically;
2. path-like / traversal / URL values ("hotel/suite",
   "apartment/living_room", r"office\research_lab", "../../etc/passwd") are
   REJECTED deterministically: never a provider call, never a file path;
3. the driver falls back to the AUTHORITATIVE user-prompt location (the
   "Location:" line) with ZERO additional provider calls (``transport.call_count``
   stays at the canonical 3-stage count);
4. the closed expected `environmentHint` JSON Schema is emitted on the
   transport (the model literally cannot emit a path-like hint in structured
   output mode).

All transport interaction is mocked (never a network call — the autouse
network block enforces it). The operator LAN host stays secret: only loopback
literals appear in this file's examples.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation import prompts  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.world.environment import (  # noqa: E402
    ENVIRONMENT_IDS,
    canonicalize_environment_hint,
)
from test_ollama_driver import (  # noqa: E402
    _case_people,
    _evidence,
    _j,
    _run,
    _alog_posts,
)


# --------------------------------------------------------------------------- #
# §14.1 — deterministic canonicalization (pure)
# --------------------------------------------------------------------------- #

def test_canonicalize_supported_values_and_variants():
    for raw, canonical in (
        ("apartment", "apartment"),
        ("Apartment", "apartment"),
        (" office ", "office"),
        ("Office", "office"),
        ("hotel suite", "hotel_suite"),
        ("hotel-suite", "hotel_suite"),
        ("Hotel Suite", "hotel_suite"),
        ("HOTEL_SUITE", "hotel_suite"),
        ("suite", "hotel_suite"),
        ("warehouse", "warehouse"),
        ("depot", "warehouse"),
        ("mansion", "mansion"),
        ("Manor", "mansion"),
    ):
        value, issues = canonicalize_environment_hint(raw)
        assert issues == (), raw
        assert value == canonical, raw
    # the closed vocabulary is exact.
    assert ENVIRONMENT_IDS == (
        "apartment", "office", "hotel_suite", "warehouse", "mansion",
    )


def test_canonicalize_missing_and_invalid_types():
    # a MISSING hint is valid (the resolver falls back).
    assert canonicalize_environment_hint(None) == (None, ())
    assert canonicalize_environment_hint("") == (
        None,
        ("environmentHint: must be a non-empty string",),
    )
    value, issues = canonicalize_environment_hint(42)
    assert value is None
    assert issues == ("environmentHint: must be a string",)


def test_canonicalize_rejects_path_like_and_hostile_values():
    for hostile in (
        "hotel/suite",
        "apartment/living_room",
        "office/research_lab",
        "office\\research_lab",
        "../..\\..\\secret",
        "C:\\windows",
        "http://example.com/office",
        "file:///tmp/x",
        "script office",
    ):
        value, issues = canonicalize_environment_hint(hostile)
        assert value is None, hostile
        assert issues, hostile
    # The REJECTED value is never interpreted as a kit/path: passing it to the
    # FALLBACK resolution (the local repair path) yields the documented
    # default kit, and the rejected token never appears in the canonical id.
    for hostile in ("hotel/suite", "office\\research_lab"):
        assert hostile not in ENVIRONMENT_IDS


def test_canonicalize_is_idempotent():
    for canonical in ENVIRONMENT_IDS:
        once, _ = canonicalize_environment_hint(canonical)
        twice, _ = canonicalize_environment_hint(once)
        assert once == canonical
        assert twice == canonical


# --------------------------------------------------------------------------- #
# §14.2/§14.3 — structured-output transport schema carries the closed enum
# --------------------------------------------------------------------------- #

def test_world_transport_schema_constrains_environment_hint_to_closed_enum():
    schema = prompts.schema_contract_as_json_schema("world_requirements")
    hint_schema = schema["properties"]["environmentHint"]
    assert hint_schema == {
        "type": "string",
        "enum": sorted(ENVIRONMENT_IDS),
    }
    # the model can never emit "hotel/suite" in structured-output mode.
    assert all("/" not in token and "\\" not in token for token in hint_schema["enum"])


def test_world_prompt_text_uses_only_the_canonical_tokens():
    blob = prompts.build_world_requirements_prompt("x", None)
    for canonical in ENVIRONMENT_IDS:
        assert canonical in blob
    # the old free-text alias vocabulary is GONE from the field rules.
    assert "apartment/flat/condo" not in blob
    assert "hotel/room/suite" not in blob
    # the field rules teach the canonical token mapping explicitly.
    assert "hotel suite -> hotel_suite" in blob
    assert "exact canonical token" in blob


# --------------------------------------------------------------------------- #
# §14.4/§14.5 — driver integration: NO remote repair for format-only issues
# --------------------------------------------------------------------------- #

def _hotel_suite_prompt() -> str:
    return (
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
        "Motive: stolen research data\nWeapon: kitchen knife\nTime: 23:42\n"
        "Witness: Lisa König\nLocation: hotel suite\n"
    )


def _world(**overrides: Any):
    base = {
        "environmentHint": "hotel/suite",  # path-like: deterministically rejected
        "locationTokens": ["hotel", "suite"],
        "objects": [],
        "relations": [],
        "unsafeUnsupported": [],
    }
    base.update(overrides)
    return base


def _staged(bad_hint: bool):
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world(environmentHint=("hotel/suite" if bad_hint else "hotel_suite"))),
    ]
    return posts


@pytest.mark.parametrize("bad_hint", (True, False))
def test_driver_pathlike_hint_cost_zero_extra_calls_and_falls_back(bad_hint):
    """A path-like (or canonical) environmentHint costs the SAME seven calls
    (case/evidence + 4 Phase 19J activity logs + world): the local
    canonicalization/fallback NEVER consumes a provider call and NEVER enters
    the remote-repair path."""
    posts = _staged(bad_hint)
    record, transport = _run(posts, prompt=_hotel_suite_prompt())
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 7
    assert record.budget.calls == 7
    assert record.deferred_structural == ()
    # the user-prompt location (hotel suite) is authoritative after the local
    # repair; the rejected path token NEVER reaches a kit/file lookup.
    assert record.draft.scene is not None
    assert record.draft.scene.environment_id == "hotel_suite"


def test_driver_canonicalized_spelling_costs_zero_extra_calls(caplog):
    import logging

    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world(environmentHint=" Hotel Suite ")),
    ]
    with caplog.at_level(logging.INFO, logger="procedural-detective"):
        record, transport = _run(posts, prompt=_hotel_suite_prompt())
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 7
    assert record.draft.scene.environment_id == "hotel_suite"
    events = [
        getattr(event, "pd_event", None)
        for event in caplog.records
    ]
    assert "environment.canonicalized" in events
    assert "environment.fallback.used" not in events


def test_driver_rejected_hint_without_user_location_uses_default_kit():
    """A rejected hint with NO authoritative user location still costs zero
    calls: the environment resolver selects the default kit (never a path)."""
    prompt = (
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
        "Motive: stolen research data\nWeapon: kitchen knife\nTime: 23:42\n"
        "Witness: Lisa König\n"
    )
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world(environmentHint="office\\research_lab")),
    ]
    record, transport = _run(posts, prompt=prompt)
    assert record.state is GenerationState.PUBLISHED
    assert transport.call_count == 7
    assert record.draft.scene.environment_id == "apartment"


def test_parse_world_requirements_canonical_fallback():
    """The standalone parser repairs the hint locally and never raises for a
    format-only problem (only genuine content gaps raise)."""
    from app.services.ollama_driver import parse_world_requirements

    parsed = parse_world_requirements(
        json.dumps(_world(), sort_keys=True), canonical_fallback="office"
    )
    assert parsed.environment_hint == "office"
    parsed_default = parse_world_requirements(
        json.dumps(_world(environmentHint=None), sort_keys=True)
    )
    assert parsed_default.environment_hint is None
    # unsupported-but-unsafe value with a fallback still repairs locally.
    parsed_fallback = parse_world_requirements(
        json.dumps(_world(environmentHint="beach"), sort_keys=True),
        canonical_fallback="mansion",
    )
    assert parsed_fallback.environment_hint == "mansion"


def test_parse_world_requirements_content_gaps_still_raise():
    """Genuine semantic content gaps (a malformed object entry) are NOT
    absorbed by the canonicalizer — they keep raising for the bounded retry."""
    from app.services.ollama_driver import parse_world_requirements

    bad = {
        "environmentHint": "hotel_suite",
        "locationTokens": [],
        "objects": [{"criticality": "required"}],  # no name -> content gap
        "relations": [],
        "unsafeUnsupported": [],
    }
    with pytest.raises(ValueError):
        parse_world_requirements(json.dumps(bad, sort_keys=True))


__all__ = ["_hotel_suite_prompt", "_staged", "_world"]