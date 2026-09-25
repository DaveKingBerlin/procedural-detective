"""Phase 19D/E adversarial findings — backend fixes (ADV-235..240).

Regression suite for the accepted Phase 19D/E adversarial findings:

- ADV-235 (HIGH): the weapon-lock injection (and ANY merge/materialization of a
  CaseTruth weapon) must respect ``UNSAFE_OBJECT_TERMS`` BEFORE composing — an
  unsafe locked weapon is recorded as a sanitized safe-fail note, NEVER
  composed/upgraded, and the attempt FAILS CLOSED (VALIDATION_FAILED, nothing
  published, no proc object, no d_ev). Safe arbitrary weapons (knife/fork/
  hammer/...) keep publishing; a hostile prompt cannot smuggle an unsafe noun
  past the gate (extractor scan, driver world-stage parse and the shared
  ``unsafe_object_match`` word-boundary matching).
- ADV-236 (MEDIUM): the weapon-arc REQUIRED classification is narrowed to
  genuinely weapon-adjacent strong signals; ``with``/``used`` ordinary
  instrument prose ("with a tray", "with a cup of coffee", "used a spatula")
  is DECORATIVE and can never fail a case. The locked ``Weapon:`` line stays
  the authority; the bronze-ice-pick precedent survives via the strong
  ``killer`` signal.
- ADV-237 (MEDIUM): a >40-char locked weapon publishes with its truncated
  semantic id (+ human label); the injected request id ALWAYS equals the
  resolved semantic object id (single slug source in the id sheet, the
  injection, the presence guard, evidence matching, weapon enhancement and
  the locked-constraint equivalence layer).
- ADV-238 (MEDIUM, decision): deterministic (fake/demo) path — the golden demo
  case is IMMUTABLE. A prompt substituting a different weapon is NOT the demo
  case and FAILS SAFELY (VALIDATION_FAILED, the honest player-safe message);
  pre-19E evidence: the same prompt already FAILED at the locked-weapon-vs-
  golden mismatch (verified against commit 0081baa), so the 19E injection did
  not regress the demo path; the demo case publishes only unchanged.
- ADV-239 (LOW, decision): ``MAX_PROCEDURAL_ASSETS_PER_GENERATION`` is PER
  ATTEMPT (REQUIREMENTS "per generation attempt"; ADR-001 "Richness bound:
  distinct procedural assets per generation") — repair passes share it. Within
  a single composition the per-composition provider budget plus the player-
  safe decorative drops guarantee the property below.
- ADV-240 (LOW): composition of a decorative-rich world is byte-identical
  regardless of process cache state (fresh cache == warm cache == ``None``);
  the cache only memoizes and never changes the placed/dropped decision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.constraints import LockedConstraints  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.world.extract import (  # noqa: E402
    UNSAFE_OBJECT_TERMS,
    extract_world_requirements,
    unsafe_object_match,
)
from app.world.requirements import (  # noqa: E402
    CRITICALITY_DECORATIVE,
    CRITICALITY_REQUIRED,
    ObjectRequest,
    WorldRequirements,
    semantic_object_id,
)
from fixtures.world_showcase import UNSEEN_WEAPON_PROMPT  # noqa: E402
from test_ollama_driver import (  # noqa: E402
    _case_people,
    _evidence,
    _j,
    _run,
    _alog_posts,
)
from test_phase19e_semantic_pipeline import (  # noqa: E402
    FORk_SPEC,
    GENERIC_SPEC,
    _weapon_prompt,
    _world_with,
)


# --------------------------------------------------------------------------- #
# ADV-235 — unsafe locked weapons FAIL CLOSED (extractor lane)
# --------------------------------------------------------------------------- #


def _extract_weapon(weapon: str) -> WorldRequirements:
    locked = LockedConstraints(weapon=weapon)
    return extract_world_requirements(f"Weapon: {weapon}\n", locked)


@pytest.mark.parametrize(
    "term", ["gun", "bomb", "rifle", "pistol", "grenade", "dynamite", "shotgun", "revolver", "explosive"]
)
def test_adv235_unsafe_locked_weapon_never_composed(term):
    """ADV-235: the weapon-lock injection must NOT create the unsafe semantic
    object — it is recorded as a sanitized safe-fail note and the world keeps
    NO object for the locked weapon (the presence guard/constraint gate then
    fails the attempt closed)."""
    assert term in UNSAFE_OBJECT_TERMS
    extracted = _extract_weapon(term)
    names = [o.requested_name for o in extracted.objects]
    assert not any(term == o.requested_name or term in semantic_object_id(o.requested_name) for o in extracted.objects)
    assert term not in " ".join(x.casefold() for x in names)
    assert extracted.unsafe_unsupported
    assert any("known-unsafe object term" in note for note in extracted.unsafe_unsupported)


def test_adv235_unsafe_matching_request_is_not_upgraded():
    """A request whose semantic id equals the unsafe locked weapon is NEVER
    upgraded by the injection (nothing unsafe is materialized)."""
    locked = LockedConstraints(weapon="GUN")
    extracted = extract_world_requirements(
        "Weapon: GUN\nThe killer used it.", locked
    )
    assert not any("gun" == semantic_object_id(o.requested_name) for o in extracted.objects)


def test_adv235_safe_locked_weapons_still_injected():
    """The general rule stays intact for safe arbitrary weapons: the locked
    weapon is materialized as REQUIRED."""
    for weapon in ("fork", "hammer"):
        extracted = _extract_weapon(weapon)
        ids = {semantic_object_id(o.requested_name) for o in extracted.objects}
        assert semantic_object_id(weapon) in ids
        request = next(
            o for o in extracted.objects
            if semantic_object_id(o.requested_name) == semantic_object_id(weapon)
        )
        assert request.criticality == CRITICALITY_REQUIRED


def test_adv235_hostile_prompt_cannot_smuggle_unsafe_noun():
    """A hostile prompt cannot smuggle an unsafe noun past the gate: the
    extractor's unsafe scan uses the SAME word-boundary NFKC-casefold matching
    as ``unsafe_object_match``, so neither the unseen-noun path nor the
    weapon-lock injection can compose an unsafe term (any casing)."""
    for surface in ("gun", "GUN", "a gun with a silencer", "bomb"):
        match = unsafe_object_match(surface)
        assert match, surface
    assert unsafe_object_match("fork") == ""
    assert unsafe_object_match("knife") == ""
    # a prompt mixing a SAFE locked weapon with an unsafe noun keeps the safe
    # weapon and records the unsafe noun as a sanitized note — never composed.
    hostile = (
        "Weapon: fork\nThe killer also hid a GUN under the desk and dropped "
        "a pistol in the office.\n"
    )
    extracted = extract_world_requirements(hostile, LockedConstraints(weapon="fork"))
    names = [o.requested_name for o in extracted.objects]
    assert "fork" in names
    assert not any("gun" in semantic_object_id(n) or "pistol" in semantic_object_id(n) for n in names)
    joined_notes = " ".join(extracted.unsafe_unsupported)
    assert "gun" in joined_notes
    assert "pistol" in joined_notes


def test_adv235_driver_unsafe_locked_weapon_fails_closed():
    """ADV-235 end-to-end (Ollama lane, mocked transport): `Weapon: gun` fails
    closed with VALIDATION_FAILED — nothing published, no proc object, no
    weapon evidence fact (the model's own world request for ``gun`` is also
    stripped by the world-stage unsafe gate)."""
    world_smuggled = {
        "environmentHint": "office",
        "objects": [{"name": "gun", "criticality": "required"}],
        "relations": [],
        "unsafeUnsupported": [],
    }
    record, _transport = _run(
        [
            _j(_case_people(weapon="gun")),
            _j(_evidence(weapon_obj="gun", murderer="paul_becker")),
            *_alog_posts('2026-09-11T23:42:00+02:00'),
            _j(world_smuggled),
        ],
        prompt=_weapon_prompt("gun"),
        max_repair_passes=0,
        max_full_regenerations=0,
    )
    assert record.state is GenerationState.FAILED
    assert record.failure_code == "VALIDATION_FAILED"
    assert record.published is None
    payload_text = (
        json.dumps(record.published.draft.__dict__ if record.published is not None else {})
        if record.published is not None
        else ""
    )
    assert "proc." not in payload_text
    if record.draft is not None:
        assert not any(getattr(f, "id", "") == "d_ev_weapon_true" for f in record.draft.evidence)


@pytest.mark.parametrize("weapon", ["fork", "knife"])
def test_adv235_driver_safe_locked_weapons_still_publish(weapon):
    """ADV-235 keeps the general behavior: `Weapon: fork` and `Weapon: knife`
    still PUBLISH through the generalized driver lane."""
    weapon_id = semantic_object_id(weapon)
    spec = FORk_SPEC if weapon == "fork" else GENERIC_SPEC
    posts = [
        _j(_case_people(weapon=weapon_id)),
        _j(_evidence(weapon_obj=weapon_id, murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(_world_with("kitchen knife")),
    ]
    if weapon == "fork":
        posts.append(spec)
    record, _transport = _run(posts, prompt=_weapon_prompt(weapon))
    assert record.state is GenerationState.PUBLISHED, (weapon, record.reason)
    assert record.solver_proof.weapon.winner == weapon_id
    assert any(
        getattr(f, "id", "") == "d_ev_weapon_true" for f in record.draft.evidence
    )


def test_adv235_world_stage_cannot_smuggle_unsafe_noun():
    """The driver WORLD stage parser applies the SAME KNOWN-UNSAFE gate: a
    model-invented unsafe request is skipped with a sanitized note and never
    composes (a safe locked weapon in the same world still publishes)."""
    from app.services.ollama_driver import parse_world_requirements

    world = {
        "environmentHint": "office",
        "objects": [
            {"name": "fork", "criticality": "required"},
            {"name": "PISTOL", "criticality": "decorative"},
            {"name": "gun", "criticality": "decorative"},
        ],
        "relations": [],
        "unsafeUnsupported": [],
    }
    parsed = parse_world_requirements(_j(world))
    assert [o.requested_name for o in parsed.objects] == ["fork"]
    assert any("pistol" in note for note in parsed.unsafe_unsupported)
    assert any("gun" in note for note in parsed.unsafe_unsupported)

    record, _transport = _run(
        [
            _j(_case_people(weapon="fork")),
            _j(_evidence(weapon_obj="fork", murderer="paul_becker")),
            *_alog_posts('2026-09-11T23:42:00+02:00'),
            _j(world),
            FORk_SPEC,
        ],
        prompt=_weapon_prompt("fork"),
        max_repair_passes=0,
        max_full_regenerations=0,
    )
    assert record.state is GenerationState.PUBLISHED
    from app.generation import pipeline

    public, _facts, _truth, _draft = pipeline.assemble(record)
    assert "fork" in {o.object_id for o in public.objects}
    assert not any("pistol" in o.object_id or o.object_id == "gun" for o in public.objects)


# --------------------------------------------------------------------------- #
# ADV-236 — `with`/`used` ordinary prose is DECORATIVE (never REQUIRED)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "prose",
    [
        "The waiter came with a tray.",
        "The detective sat down with a cup of coffee near the desk.",
        "She used a spatula and a whisk in the kitchen at the office.",
    ],
)
def test_adv236_with_used_prose_nouns_are_decorative(prose):
    """ADV-236: ordinary instrument prose must classify the noun DECORATIVE
    (or omit it) — never REQUIRED, so the noun can never fail the case and can
    never appear in the weapon universe."""
    extracted = extract_world_requirements(prose)
    for request in extracted.objects:
        assert request.criticality != CRITICALITY_REQUIRED, request
        assert request.criticality == CRITICALITY_DECORATIVE
    # composing these DECORATIVE objects never produces a blocking world issue
    from app.assets.generated_cache import GeneratedAssetCache
    from app.environments.manifests import load_environment
    from app.world.composer import compose_world

    composition = compose_world(
        WorldRequirements(environment_hint="office", objects=extracted.objects),
        env_resolver=None,
        spec_provider=None,
        environment_id="office",
        kit=load_environment("office"),
        cache=GeneratedAssetCache(),
    )
    assert not any(
        issue.startswith("world.unresolved-object") for issue in composition.issues
    ), composition.issues


def test_adv236_bronze_ice_pick_precedent_preserved():
    """The bronze-ice-pick precedent SURVIVES the narrowing: the strong
    ``killer`` signal keeps "The killer used a bronze ceremonial ice pick..."
    REQUIRED (it is a genuinely weapon-adjacent arc, not bare instrument
    prose)."""
    extracted = extract_world_requirements(UNSEEN_WEAPON_PROMPT)
    request = next(
        o for o in extracted.objects if o.requested_name == "bronze ceremonial ice pick"
    )
    assert request.criticality == CRITICALITY_REQUIRED


def test_adv236_weapon_line_is_authority():
    """`Weapon: fork` still yields the REQUIRED locked weapon (the locked sheet
    is the authority, independent of the prose classification)."""
    extracted = extract_world_requirements(
        "Weapon: fork\n", LockedConstraints(weapon="fork")
    )
    request = next(o for o in extracted.objects if o.requested_name == "fork")
    assert request.criticality == CRITICALITY_REQUIRED


# --------------------------------------------------------------------------- #
# ADV-237 — no slug divergence for >40-char locked weapons
# --------------------------------------------------------------------------- #

_LONG_WEAPON = "antique solid silver engraved ceremonial presentation letter opener"
_LONG_ID = "antique_solid_silver_engraved_ceremonial"


def test_adv237_identity_slug_matches_semantic_object_id_for_long_names():
    """The id sheet and the pipeline use ONE slug source: a >40-char weapon is
    truncated identically by ``_identity_slug`` and ``semantic_object_id``."""
    from app.services.ollama_driver import _identity_slug, _locked_id_sheet

    assert semantic_object_id(_LONG_WEAPON) == _LONG_ID
    assert len(_LONG_ID) == 40
    assert _identity_slug(_LONG_WEAPON) == _LONG_ID

    sheet = _locked_id_sheet(
        type("A", (), {"locked": LockedConstraints(weapon=_LONG_WEAPON)})()
    )
    assert f"weapon_id: {_LONG_ID}" in sheet
    assert semantic_object_id(_LONG_WEAPON) == _LONG_ID == _LONG_ID
    assert any(line.strip() == f"- weapon_id: {_LONG_ID}" for line in sheet.splitlines())


def test_adv237_locked_constraint_matches_truncated_semantic_id():
    """The DEF-054 equivalence layer reduces the locked weapon through the
    pipeline's semantic id FIRST: a >40-char locked weapon MATCHES its
    truncated composed id (and a genuinely different weapon still FAILS)."""
    draft = _draft_with_weapon(_LONG_ID)
    assert LockedConstraints(weapon=_LONG_WEAPON).violations_against(draft) == ()
    # control: a genuinely different lock still fails TERMINAL.
    other = _draft_with_weapon("kitchen_knife")
    assert LockedConstraints(weapon=_LONG_WEAPON).violations_against(other)


def _draft_with_weapon(weapon_id: str):
    from fixtures.golden_generation import GOLDEN_FULL_DRAFT

    from app.generation.parser import parse_full_draft
    from app.generation.schemas import CrimeSpec

    draft = parse_full_draft(GOLDEN_FULL_DRAFT)
    crime = CrimeSpec(
        type=draft.crime.type,
        victim_id=draft.crime.victim_id,
        murderer_id=draft.crime.murderer_id,
        motive_id=draft.crime.motive_id,
        weapon_id=weapon_id,
        location_id=draft.crime.location_id,
        crime_time=draft.crime.crime_time,
    )
    import dataclasses

    return dataclasses.replace(draft, crime=crime)


def test_adv237_long_weapon_publishes_with_truncated_semantic_id():
    """A >40-char weapon name PUBLISHES: the composed object id == the injected
    semantic id (no presence-guard failure), the weapon is a candidate universe
    member with POTENTIAL_WEAPON, the render id stays a separate proc.* token
    and the human label (picker/reveal) is derived from the truncated semantic
    id."""
    record, _transport = _run(
        [
            _j(_case_people(weapon=_LONG_ID)),
            _j(_evidence(weapon_obj=_LONG_ID, murderer="paul_becker")),
            *_alog_posts('2026-09-11T23:42:00+02:00'),
            _j(_world_with(_LONG_WEAPON)),
            GENERIC_SPEC,
        ],
        prompt=_weapon_prompt(_LONG_WEAPON),
    )
    assert record.state is GenerationState.PUBLISHED, record.reason
    assert record.solver_proof.weapon.winner == _LONG_ID
    assert record.solver_proof.weapon.unique

    from app.domain.eligibility import derive_universes
    from app.generation import pipeline
    from app.services.reveal import weapon_label_of

    public, _facts, _truth, _draft = pipeline.assemble(record)
    objects = {o.object_id: o for o in public.objects}
    assert _LONG_ID in objects
    weapon = objects[_LONG_ID]
    assert weapon.asset_id.startswith("proc.")
    assert "POTENTIAL_WEAPON" in weapon.public_affordances
    assert _LONG_ID in derive_universes(public).weapon_ids
    assert _LONG_ID != weapon.asset_id
    # human label (picker/reveal) derived from the SEMANTIC id, never proc.*
    label = weapon_label_of(weapon.asset_id, object_id=_LONG_ID)
    assert label == "Antique Solid Silver Engraved Ceremonial"
    assert "proc." not in label
    assert any(
        getattr(f, "id", "") == "d_ev_weapon_true" for f in record.draft.evidence
    )
    # presence guard: the composed id is referencing the SAME token
    assert any(p.object_id == _LONG_ID for p in record.draft.world_graph.placements)


# --------------------------------------------------------------------------- #
# ADV-238 — decision: the golden demo case is immutable (fake/demo path)
# --------------------------------------------------------------------------- #


def _demo_service(url):
    """The DEFAULT deterministic demo path: GenerationService with NO spec
    provider (the builtin dev-mode golden case) — the exact setup a visitor
    without an Ollama box uses."""
    from app.assets.generated_cache import GeneratedAssetCache
    from app.persistence.store import Store
    from app.services.generation import GenerationService
    from conftest import upgrade_db
    from phase5_helpers import _phase5_settings_for

    upgrade_db(url)
    store = Store(url)
    service = GenerationService(
        settings=_phase5_settings_for(url),
        store=store,
        spec_provider=None,
        generated_cache=GeneratedAssetCache(),
    )
    return store, service


def _demo_generate(url, prompt):
    from app.persistence.timebase import EpochClock
    from phase5_helpers import seed_session

    store, service = _demo_service(url)
    seed_session(store, "SESS-DEMO", EpochClock())
    started = service.start_case_generation(
        prompt, anonymous_quota_session_id="SESS-DEMO"
    )
    published = store.get_published(started.case_id, 1)
    return started, (published is not None), service


@pytest.mark.parametrize(
    "prompt",
    [
        "Weapon: fork\n",
        "Weapon: gun\n",
        "Weapon: kitchen knife\nVictim: Dave Smith\n",
    ],
)
def test_adv238_demo_path_substituted_weapon_fails_safely(database_url, prompt):
    """ADV-238 (decision): the golden demo case is immutable — a prompt that
    substitutes a different weapon is NOT the demo case: it FAILS SAFELY
    (VALIDATION_FAILED, nothing published). It neither silently ignores the
    weapon substitution nor publishes a non-validated world."""
    started, published, _service = _demo_generate(database_url, prompt)
    assert started.status == "FAILED", (prompt, started.status)
    assert started.failure_code == "VALIDATION_FAILED"
    assert published is False


def test_adv238_demo_path_golden_case_publishes_unchanged(database_url):
    """The demo case itself (no substitution) PUBLISHES — the demo path stays
    truthful and functional."""
    started, published, _service = _demo_generate(
        database_url, "A simple murder case.\n"
    )
    assert started.status == "PUBLISHED"
    assert published is True


# --------------------------------------------------------------------------- #
# ADV-239 — decision: per-attempt procedural-asset ceiling; decorative-rich
# worlds degrade to drop (never fail a case that would otherwise publish)
# --------------------------------------------------------------------------- #


def test_adv239_decorative_over_ceiling_never_fails_a_publishable_case():
    """ADV-239 (decision): ``MAX_PROCEDURAL_ASSETS_PER_GENERATION`` is a
    PER-ATTEMPT distinct-procedural richness bound (REQUIREMENTS "per
    generation attempt"; ADR-001). Within one composition the per-composition
    provider budget (``SPEC_PROVIDER_CALL_LIMIT``) plus the player-safe
    decorative drops guarantee the documented contract: a decorative-only
    over-ceiling never invalidates REQUIRED objects and never fails a case
    that would otherwise publish — a fork + 22-decoration world PUBLISHES with
    the weapon placed and every dropped decoration recorded as a player-safe
    note."""
    decor = [f"decorative prop {index}" for index in range(22)]
    world = {
        "environmentHint": "office",
        "objects": [
            {"name": "fork", "criticality": "required"},
            *[{"name": name, "criticality": "decorative"} for name in decor],
        ],
        "relations": [],
        "unsafeUnsupported": [],
    }
    posts = [
        _j(_case_people(weapon="fork")),
        _j(_evidence(weapon_obj="fork", murderer="paul_becker")),
        *_alog_posts('2026-09-11T23:42:00+02:00'),
        _j(world),
    ]
    posts.extend([GENERIC_SPEC] * 6)
    record, _transport = _run(
        posts, prompt=_weapon_prompt("fork"), max_repair_passes=0,
        max_full_regenerations=0, max_llm_calls_per_generation=20,
    )
    assert record.state is GenerationState.PUBLISHED, record.reason
    assert record.solver_proof.weapon.winner == "fork"
    # 7 core (case/evidence/4 activity logs/world) + 6 asset.
    assert record.budget.calls == 13
    assert record.published is not None
    assert record.published.draft.composition_notes
    assert all("left out" in note for note in record.published.draft.composition_notes)


def test_adv239_ceiling_semantics_documented_as_per_attempt():
    """The ceiling contract is a per-generation-attempt distinct richness
    bound the repair passes of ONE attempt share (documented in
    ``app.generation.budgets.BudgetTracker.consume_procedural_asset``)."""
    from app.generation.budgets import BudgetTracker
    from app.generation.clock import ManualClock

    budget = BudgetTracker(
        clock=ManualClock(),
        deadline_seconds=60,
        max_calls=8,
        max_repairs=2,
        max_regenerations=1,
        max_procedural_assets=2,
    )
    assert budget.consume_procedural_asset("a") is True
    assert budget.consume_procedural_asset("b") is True
    assert budget.consume_procedural_asset("c") is False  # ceiling (distinct)
    assert budget.consume_procedural_asset("a") is True  # dup = no reservation
    assert sorted(budget.procedural_assets) == ["a", "b"]


# --------------------------------------------------------------------------- #
# ADV-240 — compose_world is deterministic regardless of process cache state
# --------------------------------------------------------------------------- #


def _decorative_world():
    names = [
        "fork", "coffee mug", "umbrella", "toolbox", "briefcase", "desk fan",
        "suitcase", "flower pot", "wand", "lantern", "candlestick", "spatula",
        "whisk", "ladle", "broom",
    ]
    return WorldRequirements(
        environment_hint="office",
        objects=tuple(
            ObjectRequest(requested_name=name, criticality=CRITICALITY_DECORATIVE)
            for name in names
        ),
    )


class _GenericSpecProvider:
    """Deterministic provider serving ONE valid procedural spec per name."""

    def __init__(self):
        self.calls: list = []

    def generate(self, request):
        from app.assets.spec_provider import AssetSpecResponse

        self.calls.append(getattr(request, "requested_name", ""))
        return AssetSpecResponse(content=GENERIC_SPEC)


_BASE_IDS = {
    "kitchen_knife", "letter_opener", "scissors", "vase_01",
    "apartment_table", "apartment_door", "apartment_lamp",
    "apartment_laptop", "victim_body_placeholder",
}


def _compose_snapshot(cache):
    from app.environments.manifests import load_environment
    from app.world.composer import compose_world

    composition = compose_world(
        _decorative_world(),
        env_resolver=None,
        spec_provider=_GenericSpecProvider(),
        environment_id="office",
        kit=load_environment("office"),
        cache=cache,
    )
    placed = tuple(
        sorted(p.object_id for p in composition.placements if p.object_id not in _BASE_IDS)
    )
    return placed, tuple(composition.composition_notes), tuple(composition.issues)


def test_adv240_empty_cache_equals_warm_cache():
    """The same rich decorative world composes byte-identical placed/dropped/
    notes whether the generated-asset cache is EMPTY, WARM (pre-populated by a
    prior identical composition) or the process-shared module default — the
    cache only memoizes and never changes the decision."""
    from app.assets.generated_cache import GeneratedAssetCache

    empty = _compose_snapshot(GeneratedAssetCache())
    warm_holder = GeneratedAssetCache()
    _compose_snapshot(warm_holder)  # pre-warm with ALL names
    warm = _compose_snapshot(warm_holder)
    assert warm == empty, (empty, warm)
    # a second EMPTY cache run is also byte-identical
    assert _compose_snapshot(GeneratedAssetCache()) == empty
    # cache=None falls back to the process-shared module oracle: still the
    # same deterministic decision.
    module_shared = _compose_snapshot(None)
    assert module_shared == empty, (empty, module_shared)


def test_adv240_two_compositions_same_request_identical_in_process():
    """Two in-process compositions of the SAME request (module-level oracle
    cache shared across them — the ADV-240 observed divergence) produce the
    SAME placed/dropped/notes."""
    assert _compose_snapshot(None) == _compose_snapshot(None)


__all__ = []  # pytest module: no accidental public names