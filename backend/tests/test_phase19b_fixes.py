"""Phase 19B — adversarial-finding regression suite (ADV-213/214/216/217/218/219).

Each accepted finding gets a focused regression test through the REAL driver +
Asset Oracle path where applicable (mocked Ollama transport; never a network
call — the autouse backend network block is active):

- ADV-213 (MEDIUM): typed ``StageDriverProviderFailure`` survives the Asset
  Oracle and surfaces the NARROW budget code (per-asset exhaustion), global
  exhaustion stays terminal, essential/evidence assets fail closed sanitized,
  decorative budget-exhausted objects never install a partial world silently,
  and failed-asset accounting runs on exception-style budget failures.
- ADV-214 (MEDIUM): ``environmentHint`` stays a REQUIRED key in the transport
  JSON Schema with the closed enum (prose with the word "null" no longer
  demotes it); a missing hint at parse level still falls back safely.
- ADV-216 (LOW): the CORE bucket is a non-string sentinel — a semantic asset
  id literally ``"core"`` is charged to its OWN per-asset bucket (per-asset
  ceiling + attribution apply; core_calls untouched), while a real core call
  still charges CORE and all counters stay monotonic/exact.
- ADV-217 (LOW): harmless leading/trailing whitespace incl. newline/tab is
  stripped BEFORE the canonical-id safety gate; interior control chars are
  still rejected.
- ADV-218 (LOW): hostile semantic object ids (URLs / control chars) are
  sanitized before embedding in the typed ``SemanticObjectResolutionError``
  message; the player-facing failure stays the constant sanitized code.
- ADV-219 (LOW): the weapon-candidate universe mirrors the objects ACTUALLY
  placed in the published world (letter_opener/scissors are not candidates on
  hotel_suite/warehouse where they are never placed), with solver uniqueness
  and forensic/evidence references intact.

No defect file / REQUIREMENTS.md / .rad/ / adapter / e2e file is touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation import prompts  # noqa: E402
from app.generation.failure_codes import (  # noqa: E402
    PUBLIC_FAILURE_CODES,
    GenerationFailureCode,
)
from app.generation.state_machine import GenerationState  # noqa: E402
from app.world.environment import canonicalize_environment_hint  # noqa: E402
from test_ollama_driver import (  # noqa: E402
    ICEPICK_SPEC,
    PROMPT,
    _case_people,
    _evidence,
    _j,
    _run,
    _alog_posts,
)
from test_phase19_budgets import _tracker  # noqa: E402


# --------------------------------------------------------------------------- #
# ADV-213 — typed supplier-budget failures survive the Asset Oracle
# --------------------------------------------------------------------------- #

def _world_with(name, criticality="required"):
    return {
        "environmentHint": "office", "locationTokens": ["office"],
        "objects": [{"name": name, "categoryHint": "decor", "criticality": criticality}],
        "relations": [], "unsafeUnsupported": [],
    }


def _budgeted_controller_kwargs(**overrides):
    kwargs = dict(
        max_llm_calls_per_generation=60,
        max_core_llm_calls=12,
        max_llm_calls_per_procedural_asset=1,
        max_procedural_assets_per_generation=20,
        max_failed_assets_per_generation=3,
        max_repair_passes=0,
        max_full_regenerations=0,
    )
    kwargs.update(overrides)
    return kwargs


def test_adv213a_per_asset_exhaustion_surfaces_narrow_code_via_real_path():
    """A REQUIRED procedural asset exhausting its PER-ASSET call budget now
    fails the attempt with ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED through the
    real driver+oracle path (it previously degraded to VALIDATION_FAILED via
    the presence guard, and the budget code was structurally unreachable)."""
    posts = [
        _j(_case_people(weapon="mystery_weapon")),
        _j(_evidence(weapon_obj="mystery_weapon", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world_with("mystery weapon", criticality="required")),
        "<not-json>",  # ASSET_SPEC (invalid -> repair needed)
        "<not-json>",
    ]
    record, _transport = _run(
        posts,
        prompt=(
            "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
            "Motive: stolen research data\nWeapon: mystery weapon\nTime: 23:42\n"
            "Witness: Lisa K\u00f6nig\nLocation: office\n"
        ),
        **_budgeted_controller_kwargs(),
    )
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert record.failure_code != "VALIDATION_FAILED"
    assert record.published is None


def test_adv213b_global_exhaustion_remains_terminal():
    """A GLOBAL ceiling hit during the asset path stays the generic terminal
    PROVIDER_CALL_BUDGET_EXHAUSTED (never a partial world, never the per-asset
    code — the narrower cause is unknown)."""
    posts = [
        _j(_case_people(weapon="mystery_weapon")),
        _j(_evidence(weapon_obj="mystery_weapon", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world_with("mystery weapon", criticality="required")),
        "<not-json>",  # ASSET_SPEC (would be the 4th call -- global ceiling 4)
        "<not-json>",
    ]
    record, _transport = _run(
        posts,
        prompt=(
            "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
            "Motive: stolen research data\nWeapon: mystery weapon\nTime: 23:42\n"
            "Witness: Lisa K\u00f6nig\nLocation: office\n"
        ),
        **_budgeted_controller_kwargs(
            max_llm_calls_per_generation=4,
            max_core_llm_calls=12,
        ),
    )
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert record.published is None
    assert record.budget.calls == 4


def test_adv213c_decorative_budget_exhaustion_follows_bounded_fallback():
    """A DECORATIVE object exhausting its per-asset budget does NOT fail the
    attempt on the FIRST occurrence (ADV-220): the failed asset is counted
    (mark_failed_asset, exactly once), the object is left OUT with the
    player-safe composition note exactly like the return-style fallback path
    (``test_decorative_asset_failure_falls_back_safely``), and nothing is
    published SILENTLY — the terminal condition is
    MAX_FAILED_ASSETS_PER_GENERATION, not the first decorative failure."""
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world_with("unusual trinket", criticality="decorative")),
        "<not-json>",  # ASSET_SPEC (invalid -> repair -> per-asset budget)
        "<not-json>",
    ]
    record, _transport = _run(
        posts,
        prompt=_KITCHEN_KNIFE_PROMPT,
        **_budgeted_controller_kwargs(),
    )
    assert record.state is GenerationState.PUBLISHED
    published = record.published.draft if record.published else record.draft
    assert all(o.object_id != "unusual_trinket" for o in published.objects)
    assert any(
        "unusual trinket" in note for note in (published.composition_notes or ())
    )
    assert record.budget.failed_asset_count == 1
    assert "unusual trinket" in record.budget.failed_assets


def test_adv213d_essential_evidence_fails_closed_sanitized():
    """An essential/evidence (locked-weapon) asset that exhausts still fails
    closed with the SANITIZED constant reason and the canonical narrow code —
    nothing published, no raw internals in the attempt record."""
    posts = [
        _j(_case_people(weapon="mystery_weapon")),
        _j(_evidence(weapon_obj="mystery_weapon", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world_with("mystery weapon", criticality="required")),
        "<not-json>",
        "<not-json>",
    ]
    record, _transport = _run(
        posts,
        prompt=(
            "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
            "Motive: stolen research data\nWeapon: mystery weapon\nTime: 23:42\n"
            "Witness: Lisa K\u00f6nig\nLocation: office\n"
        ),
        **_budgeted_controller_kwargs(),
    )
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.failure_code == "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED"
    # the constant sanitized player-safe reason is what the controller stores.
    assert record.reason == "provider failure: generator unavailable"
    assert "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED" not in (record.reason or "")
    assert "mystery weapon" not in (record.reason or "")


def test_adv213e_failed_asset_accounting_increments_on_budget_exception():
    """Exception-style per-asset budget failures now run mark_failed_asset: the
    failed-asset set/count increments exactly as for return-style failures."""
    posts = [
        _j(_case_people(weapon="mystery_weapon")),
        _j(_evidence(weapon_obj="mystery_weapon", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world_with("mystery weapon", criticality="required")),
        "<not-json>",
        "<not-json>",
    ]
    record, _transport = _run(
        posts,
        prompt=(
            "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
            "Motive: stolen research data\nWeapon: mystery weapon\nTime: 23:42\n"
            "Witness: Lisa K\u00f6nig\nLocation: office\n"
        ),
        **_budgeted_controller_kwargs(),
    )
    assert record.budget.failed_asset_count == 1
    assert "mystery weapon" in record.budget.failed_assets


def test_adv213_oracle_reraises_typed_failure_keeps_generic_sanitization():
    """The Asset Oracle re-raises the TYPED provider failure (narrow code
    preserved) while UNKNOWN exceptions still sanitize to the generic string —
    the oracle never leaks internals for unknown failures."""
    from app.assets.oracle import GeneratedAssetOracle, resolve_or_generate
    from app.assets.resolver import AssetRequest
    from app.generation.provider import StageDriverProviderFailure

    class TypedBudgetProvider:
        def generate(self, request):
            raise StageDriverProviderFailure(
                "asset model call budget exhausted for mystery weapon",
                code=GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED,
            )

    with pytest.raises(StageDriverProviderFailure) as excinfo:
        resolve_or_generate(
            AssetRequest(requested_name="mystery weapon"),
            spec_provider=TypedBudgetProvider(),
            cache=GeneratedAssetOracle().cache,
        )
    assert excinfo.value.code == "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED"

    class UnknownProvider:
        def generate(self, request):
            raise RuntimeError("boom: some raw provider internals")

    outcome = resolve_or_generate(
        AssetRequest(requested_name="mystery weapon"),
        spec_provider=UnknownProvider(),
        cache=GeneratedAssetOracle().cache,
    )
    assert outcome.generated is None
    assert outcome.error is not None
    assert outcome.error.startswith("spec provider failed: RuntimeError")
    assert "boom" not in outcome.error


# --------------------------------------------------------------------------- #
# ADV-220 — DECORATIVE per-asset budget exhaustion follows the bounded fallback
# (skip + player-safe note + MAX_FAILED_ASSETS_PER_GENERATION ceiling); a
# REQUIRED/essential budget failure stays fail-closed with the narrow code;
# GLOBAL exhaustion is always terminal regardless of criticality.
# --------------------------------------------------------------------------- #

def _decor_world(*names):
    return {
        "environmentHint": "office", "locationTokens": ["office"],
        "objects": [
            {"name": name, "categoryHint": "decor", "criticality": "decorative"}
            for name in names
        ],
        "relations": [], "unsafeUnsupported": [],
    }


def test_adv220a_decorative_budget_exhaustion_below_ceiling_publishes():
    """(a) ONE decorative object exhausting its per-asset budget below the
    failed-asset ceiling does NOT fail the attempt: the case publishes without
    that object, per the bounded fallback policy (round-trip skip by the
    composer, identical to the return-style fallback path)."""
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_decor_world("unusual trinket")),
        "<not-json>",  # ASSET_SPEC (invalid -> repair -> per-asset budget)
        "<not-json>",
    ]
    record, _transport = _run(
        posts,
        prompt=_KITCHEN_KNIFE_PROMPT,
        **_budgeted_controller_kwargs(),
    )
    assert record.state is GenerationState.PUBLISHED
    assert record.failure_code is None
    published = record.published.draft if record.published else record.draft
    assert all(o.object_id != "unusual_trinket" for o in published.objects)
    assert any(
        "unusual trinket" in note for note in (published.composition_notes or ())
    )


def test_adv220b_budget_decorative_over_ceiling_fails_with_max_failed():
    """(b) More than MAX_FAILED_ASSETS_PER_GENERATION (3) DECORATIVE objects
    exhausting their per-asset budgets fails the attempt with the CEILING code
    (MAX_FAILED_ASSETS_EXCEEDED) — now REACHABLE via exception-style budget
    failures — never the narrow per-asset code, never a silent publish."""
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(
            _decor_world(
                "trinket one", "trinket two", "trinket three", "trinket four"
            )
        ),
    ]
    record, _transport = _run(
        posts,
        prompt=_KITCHEN_KNIFE_PROMPT,
        **_budgeted_controller_kwargs(max_failed_assets_per_generation=3),
    )
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.failure_code == "MAX_FAILED_ASSETS_EXCEEDED"
    assert record.failure_code != "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert record.budget.failed_asset_count == 3
    assert record.budget.failed_assets == {
        "trinket one", "trinket two", "trinket three",
    }


def test_adv220c_required_budget_exhaustion_still_fails_closed_narrow():
    """(c) A DECORATIVE sibling never weakens the ESSENTIAL path: a REQUIRED
    procedural asset whose per-asset budget is exhausted STILL fails the
    attempt closed with the narrow ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED code
    and no partial publish, even when other decorative objects were skipped
    below their own ceiling first."""
    posts = [
        _j(_case_people(weapon="mystery_weapon")),
        _j(_evidence(weapon_obj="mystery_weapon", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(
            {
                "environmentHint": "office", "locationTokens": ["office"],
                "objects": [
                    {"name": "unusual trinket", "categoryHint": "decor", "criticality": "decorative"},
                    {"name": "mystery weapon", "categoryHint": "decor", "criticality": "required"},
                ],
                "relations": [], "unsafeUnsupported": [],
            }
        ),
        "<not-json>",  # ASSET_SPEC (decorative trinket -> exhausted, skipped)
        "<not-json>",  # ASSET_SPEC for the REQUIRED mystery weapon
        "<not-json>",  # its repair -> per-asset budget exhausted
    ]
    record, _transport = _run(
        posts,
        prompt=(
            "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
            "Motive: stolen research data\nWeapon: mystery weapon\nTime: 23:42\n"
            "Witness: Lisa K\u00f6nig\nLocation: office\n"
        ),
        **_budgeted_controller_kwargs(),
    )
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.failure_code == "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert record.reason == "provider failure: generator unavailable"
    # the decorative trinket was ALSO counted (once) before the essential fail.
    assert record.budget.failed_asset_count == 2
    assert "unusual trinket" in record.budget.failed_assets
    assert "mystery weapon" in record.budget.failed_assets


def test_adv220d_global_exhaustion_still_terminal_for_decorative_objects():
    """(d) GLOBAL ceiling exhaustion during a DECORATIVE object's asset path
    stays the generic terminal PROVIDER_CALL_BUDGET_EXHAUSTED — never
    attributed to one asset, never skipped/absorbed by the decorative tolerance
    regardless of criticality."""
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_decor_world("unusual trinket")),
        "<not-json>",  # ASSET_SPEC (would be the 4th call -- global ceiling 4)
        "<not-json>",
    ]
    record, _transport = _run(
        posts,
        prompt=_KITCHEN_KNIFE_PROMPT,
        **_budgeted_controller_kwargs(
            max_llm_calls_per_generation=4,
            max_core_llm_calls=12,
        ),
    )
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.failure_code == "PROVIDER_CALL_BUDGET_EXHAUSTED"
    # never attributed to the decorative object (no failed-asset accounting).
    assert record.budget.failed_asset_count == 0
    assert record.budget.calls == 4


def test_adv220e_failed_asset_accounting_exactly_once_per_decorative_asset():
    """(e) Failed-asset accounting is monotonic on the budget-exception path:
    each distinct DECORATIVE failing asset is recorded exactly once (count ==
    number of distinct failed objects; the tracker's set-membership no-op never
    double counts), and below the ceiling the attempt still publishes."""
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_decor_world("trinket one", "trinket two")),
    ]
    record, _transport = _run(
        posts,
        prompt=_KITCHEN_KNIFE_PROMPT,
        **_budgeted_controller_kwargs(),
    )
    assert record.state is GenerationState.PUBLISHED
    assert record.budget.failed_asset_count == 2
    assert record.budget.failed_assets == {"trinket one", "trinket two"}
    # every decorative failure consumed exactly its own per-asset allowance.
    assert record.budget.asset_call_count("trinket one") == 1
    assert record.budget.asset_call_count("trinket two") == 1


def test_adv220_failed_asset_flag_gates_exception_style_failures_at_oracle():
    """The ceiling flag the provider records for an exception-style failure is
    what the composer reads to convert the per-asset code into
    MAX_FAILED_ASSETS_EXCEEDED — the converted typed failure propagates through
    the Oracle+dependency chain un-mangled (probe at the oracle boundary)."""
    from app.assets.oracle import GeneratedAssetOracle, resolve_or_generate
    from app.assets.resolver import AssetRequest
    from app.generation.provider import StageDriverProviderFailure

    class CeilingHitProvider:
        """Claims the driver adapter raised the ceiling flag while raising the
        narrow per-asset code — the typed propagation must stay intact."""
        failed_asset_threshold_hit = True

        def generate(self, request):
            raise StageDriverProviderFailure(
                "asset model call budget exhausted for trinket four",
                code=GenerationFailureCode.ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED,
            )

    with pytest.raises(StageDriverProviderFailure) as excinfo:
        resolve_or_generate(
            AssetRequest(requested_name="trinket four"),
            spec_provider=CeilingHitProvider(),
            cache=GeneratedAssetOracle().cache,
        )
    # the narrow code itself still reaches the boundary unchanged (the
    # composer is what classifies); this probe only guards the propagation.
    assert excinfo.value.code == "ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED"


# --------------------------------------------------------------------------- #
# ADV-214 — environmentHint stays REQUIRED in the transport JSON Schema
# --------------------------------------------------------------------------- #

def test_adv214_environment_hint_required_with_closed_enum():
    schema = prompts.schema_contract_as_json_schema("world_requirements")
    assert "environmentHint" in schema["required"]
    assert schema["properties"]["environmentHint"] == {
        "type": "string",
        "enum": sorted(["apartment", "office", "hotel_suite", "warehouse", "mansion"]),
    }


def test_adv214_enum_hint_never_triggers_nullable_detection():
    # the hint prose no longer contains the literal word "null", so the
    # prose-based nullable detector can never demote requiredness again.
    assert "null" not in prompts._ENVIRONMENT_ENUM_HINT.casefold()
    assert prompts._hint_nullable(prompts._ENVIRONMENT_ENUM_HINT) is False
    assert prompts._key_required(prompts._ENVIRONMENT_ENUM_HINT) is True


def test_adv214_missing_hint_at_parse_level_still_falls_back_safely():
    """The transport schema REQUIRES environmentHint (structured output can
    no longer omit it), but a MISSING/rejected hint that still slips through
    is handled safely by the deterministic local fallback (existing
    behavior, zero provider calls)."""
    from app.services.ollama_driver import parse_world_requirements

    # (a) a MISSING/null hint parses to None -> the environment resolver
    # falls back to the default kit (existing safe behavior).
    parsed_missing = parse_world_requirements(
        json.dumps(
            {
                "environmentHint": None,
                "locationTokens": ["office"],
                "objects": [],
                "relations": [],
                "unsafeUnsupported": [],
            },
            sort_keys=True,
        ),
        canonical_fallback="office",
    )
    assert parsed_missing.environment_hint is None
    # (b) a REJECTED hint uses the authoritative user-location fallback.
    parsed_rejected = parse_world_requirements(
        json.dumps(
            {
                "environmentHint": "hotel/suite",
                "locationTokens": ["hotel", "suite"],
                "objects": [],
                "relations": [],
                "unsafeUnsupported": [],
            },
            sort_keys=True,
        ),
        canonical_fallback="mansion",
    )
    assert parsed_rejected.environment_hint == "mansion"


# --------------------------------------------------------------------------- #
# ADV-216 — CORE bucket is structurally namespaced (never a string alias)
# --------------------------------------------------------------------------- #

def test_adv216_tracker_object_named_core_charges_its_own_asset_bucket():
    from app.generation.budgets import CORE_BUCKET

    budget = _tracker(max_core=3, max_asset=1)
    # the literal asset id "core" goes to its OWN per-asset bucket.
    assert budget.consume_call(bucket="core")
    assert budget.core_calls == 0
    assert budget.asset_calls == 1
    assert budget.asset_call_count("core") == 1
    # the per-asset ceiling applies to "core" exactly like any other id.
    assert not budget.consume_call(bucket="core")
    # a REAL core call still charges the CORE bucket.
    assert budget.consume_call(bucket=CORE_BUCKET)
    assert budget.core_calls == 1
    assert budget.asset_calls == 1
    # both counters + global stay monotonic and exact.
    assert budget.calls == budget.core_calls + budget.asset_calls == 2
    assert budget.remaining_global_calls() == 128 - 2


def test_adv216_driver_object_named_core_attributed_to_asset_bucket():
    """Through the real driver, a procedural object whose requested_name is
    literally ``"core"`` is charged to the ASSET bucket (core_calls stays at
    the 7 staged core calls — case/evidence + 4 Phase 19J activity logs +
    world), never to the CORE bucket."""
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(
            {
                "environmentHint": "office", "locationTokens": ["office"],
                "objects": [{"name": "core", "criticality": "decorative"}],
                "relations": [], "unsafeUnsupported": [],
            }
        ),
        ICEPICK_SPEC,  # valid procedural spec for the "core" object
    ]
    record, _transport = _run(posts, prompt=_KITCHEN_KNIFE_PROMPT)
    assert record.state is GenerationState.PUBLISHED
    # 7 core calls: case + evidence + 4 Phase 19J activity logs + world.
    assert record.budget.core_calls == 7
    assert record.budget.asset_calls == 1
    assert record.budget.asset_calls_by_object == {"core": 1}
    assert record.budget.calls == 8


# --------------------------------------------------------------------------- #
# ADV-217 — canonical whitespace behavior for environment hints
# --------------------------------------------------------------------------- #

def test_adv217_leading_trailing_whitespace_is_stripped_before_canonical_id_validation():
    for raw, canonical in (
        ("hotel_suite\n", "hotel_suite"),
        ("hotel_suite\t", "hotel_suite"),
        (" hotel_suite ", "hotel_suite"),
        ("\noffice\n", "office"),
        ("\twarehouse\r\n", "warehouse"),
        ("hotel_suite\u2003", "hotel_suite"),  # unicode em-space (other ws)
    ):
        value, issues = canonicalize_environment_hint(raw)
        assert issues == (), raw
        assert value == canonical, raw


def test_adv217_interior_control_characters_still_rejected():
    for hostile in (
        "hotel_\nsuite",  # embedded newline (interior control char)
        "hotel\x00suite",  # NUL
        "off\tice",  # interior tab
    ):
        value, issues = canonicalize_environment_hint(hostile)
        assert value is None, hostile
        assert issues, hostile


# --------------------------------------------------------------------------- #
# ADV-218 — sanitized object ids in the typed guard message
# --------------------------------------------------------------------------- #

def test_adv218_sanitizer_never_echoes_hostile_ids():
    from app.services.ollama_driver import _sanitize_object_id_for_message

    assert _sanitize_object_id_for_message("http://evil.example/x") == "<url-suppressed>"
    assert _sanitize_object_id_for_message("https:evil.example/path") == "<url-suppressed>"
    assert _sanitize_object_id_for_message("weapon_\x01id") == "weapon_id"
    assert _sanitize_object_id_for_message("weapon\x00\x1f") == "weapon"
    assert _sanitize_object_id_for_message("") == "<unknown>"
    # normal semantic ids stay verbatim (operator diagnostics stay useful).
    assert _sanitize_object_id_for_message("antique_brass_letter_opener") == (
        "antique_brass_letter_opener"
    )


def test_adv218_hostile_weapon_id_yields_sanitized_guard_failure():
    """A hostile weaponId (URL + control chars) referenced by the crime/
    evidence algebra is never echoed raw into the attempt record; the
    player-facing failure is still the constant sanitized code."""
    hostile = "http://evil.example/x\x01control"
    posts = [
        _j(_case_people(weapon=hostile)),
        _j(_evidence(weapon_obj=hostile, murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(
            {
                "environmentHint": "office", "locationTokens": ["office"],
                "objects": [],
                "relations": [], "unsafeUnsupported": [],
            }
        ),
    ]
    record, _transport = _run(posts, prompt=PROMPT)
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.failure_code == "VALIDATION_FAILED"
    assert record.failure_code in PUBLIC_FAILURE_CODES
    reason = record.reason or ""
    assert "http" not in reason
    assert "evil" not in reason
    assert "\x01" not in reason
    assert any(ord(ch) < 0x20 for ch in reason) is False


# --------------------------------------------------------------------------- #
# ADV-219 — candidate universe mirrors the objects actually placed
# --------------------------------------------------------------------------- #

_HOTEL_SUITE_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
    "Motive: stolen research data\nWeapon: kitchen knife\nTime: 23:42\n"
    "Witness: Lisa K\u00f6nig\nLocation: hotel suite\n"
)

_KITCHEN_KNIFE_PROMPT = (
    "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\n"
    "Motive: stolen research data\nWeapon: kitchen knife\nTime: 23:42\n"
    "Witness: Lisa K\u00f6nig\nLocation: office\n"
)


def _hotel_knife_world():
    return {
        "environmentHint": "hotel suite",
        "locationTokens": ["hotel", "suite"],
        "objects": [{"name": "kitchen knife", "criticality": "decorative"}],
        "relations": [],
        "unsafeUnsupported": [],
    }


def test_adv219_hotel_candidates_only_include_placed_objects():
    from app.domain.eligibility import derive_universes
    from app.domain.evidence import validate_evidence
    from app.generation import pipeline

    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_hotel_knife_world()),
    ]
    record, _transport = _run(posts, prompt=_HOTEL_SUITE_PROMPT)
    assert record.state is GenerationState.PUBLISHED
    assert record.draft.scene.environment_id == "hotel_suite"

    public, _facts, _truth, _draft = pipeline.assemble(record)
    universe = derive_universes(public)
    # the only weapon candidate is the one the hotel kit actually places.
    assert universe.weapon_ids == ("kitchen_knife",)
    assert "letter_opener" not in universe.weapon_ids
    assert "scissors" not in universe.weapon_ids

    # candidate ids correspond to objects actually placed in the published world.
    placed_ids = {
        p.object_id for p in record.draft.world_graph.placements if p.object_id
    }
    assert set(universe.weapon_ids) <= placed_ids

    # the identity objects are still in the WORLD (forensic refs stay valid)…
    object_ids = {o.object_id for o in public.objects}
    assert {"letter_opener", "scissors"} <= object_ids
    # …but they are not weapon candidates.
    assert validate_evidence(public, tuple(record.draft.evidence)) == ()

    # solver uniqueness + evidence integrity are not weakened.
    assert record.solver_proof is not None
    assert record.solver_proof.weapon.unique is True
    assert record.solver_proof.weapon.winner == "kitchen_knife"
    assert record.last_validation.validation.all_true is True


def test_adv219_office_kit_keeps_spare_sharp_weapons_as_candidates():
    """Kits that DO place letter_opener/scissors (office/apartment/mansion)
    keep them as weapon candidates — the fix only removes candidates whose
    objects are never in the scene; it never broadens or shifts the pub schema."""
    from app.domain.eligibility import derive_universes
    from app.generation import pipeline

    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(
            {
                "environmentHint": "office", "locationTokens": ["office"],
                "objects": [{"name": "kitchen knife", "criticality": "decorative"}],
                "relations": [], "unsafeUnsupported": [],
            }
        ),
    ]
    record, _transport = _run(posts, prompt=_KITCHEN_KNIFE_PROMPT)
    assert record.state is GenerationState.PUBLISHED
    public, _facts, _truth, _draft = pipeline.assemble(record)
    universe = derive_universes(public)
    assert universe.weapon_ids == ("kitchen_knife", "letter_opener", "scissors")
    placed_ids = {
        p.object_id for p in record.draft.world_graph.placements if p.object_id
    }
    assert set(universe.weapon_ids) <= placed_ids


__all__ = ["_hotel_knife_world", "_world_with"]