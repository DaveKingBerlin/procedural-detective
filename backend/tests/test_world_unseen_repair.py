"""Phase 14_5 — CASE-CRITICAL FAILURE SEMANTICS (repair / fail-publication).

Required Phase 14_5 deliverable 3 behavior for a CRITICALITY_REQUIRED unknown
object that cannot be generated:

(a) the successful procedural path publishes the unseen proc.* weapon;
(b) a FORCED provider failure repaired by the world-repair provider publishes
    (locked constraints unchanged) — the repair re-runs the COMPLETE
    validation pipeline;
(c) a FORCED provider failure with NO repair budget FAILS the attempt: never
    published, never a substituted catalog/tape asset;
(8) an AssetSpec that FAILS strict Phase 13 validation BLOCKS the critical
    publication the same way (never published with a substitute).

Deterministic, in-process, real SQLite files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.asset_specs_unseen import (  # noqa: E402
    BRONZE_ICE_PICK_NAME,
    UNSEEN_SPEC_CONTENT,
)
from fixtures.world_showcase import UNSEEN_WEAPON_PROMPT  # noqa: E402
from phase5_helpers import seed_session  # noqa: E402


def _service_ctx(url, spec_provider=None, world_repair_provider=None):
    from app.assets.generated_cache import GeneratedAssetCache
    from app.persistence.store import Store

    from app.services.generation import GenerationService
    from conftest import upgrade_db
    from phase5_helpers import _phase5_settings_for

    upgrade_db(url)
    store = Store(url)
    settings = _phase5_settings_for(url)
    # a FRESH generated-asset cache per service: the shared module-level
    # oracle cache must never leak a generated spec across tests (cache hits
    # would turn failure tests into success tests).
    service = GenerationService(
        settings=settings,
        store=store,
        spec_provider=spec_provider,
        world_repair_provider=world_repair_provider,
        generated_cache=GeneratedAssetCache(),
    )
    return store, service


def _run_via_service(service, prompt=UNSEEN_WEAPON_PROMPT, session="SESS-1"):
    from app.persistence.timebase import EpochClock

    clock = EpochClock()
    seed_session(service._store, session, clock)
    return service.start_case_generation(prompt, anonymous_quota_session_id=session)


def _unseen_provider():
    from app.assets.spec_provider import FakeAssetSpecProvider

    return FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT)


class FailingSpecProvider:
    """Deterministic scripted provider that NEVER serves the unseen name."""

    def __init__(self, scripted=None):
        self.scripted = scripted or {}
        self.calls = 0
        self.call_log = []

    def generate(self, request):
        from app.assets.spec_provider import AssetSpecResponse

        self.calls += 1
        self.call_log.append(request)
        content = self.scripted.get(request.requested_name.casefold().strip())
        return AssetSpecResponse(content=content)


class FlakyOnceSpecProvider:
    """Serves the unseen spec on the SECOND invocation of the same name.

    Simulates a transient provider failure that a deterministic repair pass
    can overcome (repair re-composes -> the same request succeeds).
    """

    def __init__(self, name, spec):
        self.name = name.casefold().strip()
        self.spec = spec
        self.calls = 0

    def generate(self, request):
        from app.assets.spec_provider import AssetSpecResponse

        self.calls += 1
        if request.requested_name.casefold().strip() != self.name:
            return AssetSpecResponse(content=None)
        if self.calls == 1:
            return AssetSpecResponse(content=None)  # first attempt fails
        return AssetSpecResponse(content=self.spec)


class InvalidSpecProvider:
    """Returns an INVALID (unbounded) AssetSpec — strict validation rejects it."""

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        from app.assets.spec_provider import AssetSpecResponse

        self.calls += 1
        parts = ",".join(
            (
                '{"id": "part_%02d", "role": "block", "primitive": "box",'
                ' "transform": {"position": {"x": 0, "y": 0, "z": 0},'
                ' "rotation": {"x": 0, "y": 0, "z": 0},'
                ' "scale": {"x": 0.2, "y": 0.2, "z": 0.2}},'
                ' "material": "plastic"}'
            )
            % index
            for index in range(30)  # 30 parts > MAX_PARTS (24)
        )
        spec = (
            '{"canonicalName": "Too Many Blocks", "category": "evidence",'
            ' "subtype": "block", "dimensions": {"x": 0.5, "y": 0.5, "z": 0.5},'
            ' "parts": [%s]}' % parts
        )
        return AssetSpecResponse(content=spec)


class KeepReqsRepair:
    """The world repair keeps the SAME bounded requirements (re-compose)."""

    def __init__(self, world_reqs):
        self.calls: list[tuple[str, ...]] = []
        self.world_reqs = world_reqs

    def __call__(self, diagnostics):
        self.calls.append(tuple(sorted(diagnostics or ())))
        return self.world_reqs


def _extract_unseen_world_reqs():
    from app.generation.pipeline import normalize_prompt
    from app.world.extract import extract_world_requirements

    locked, _note = normalize_prompt(UNSEEN_WEAPON_PROMPT, max_chars=4000)
    return locked, extract_world_requirements(UNSEEN_WEAPON_PROMPT, locked)


# --------------------------------------------------------------------------- #
# (a) successful procedural path publishes
# --------------------------------------------------------------------------- #


def test_required_unseen_object_publishes(database_url):
    store, service = _service_ctx(database_url, spec_provider=_unseen_provider())
    started = _run_via_service(service)
    assert started.status == "PUBLISHED", started
    payload = json.loads(store.get_published(started.case_id, 1).payload_json)
    proc_placements = [
        p for p in payload["draft"]["world_graph"]["placements"]
        if (p.get("assetId") or p.get("asset_id") or "").startswith("proc.")
    ]
    assert proc_placements
    first = proc_placements[0]
    assert first.get("objectId") or first.get("object_id") == "bronze_ceremonial_ice_pick"
    asset_id = first.get("assetId") or first.get("asset_id")
    assert asset_id.startswith("proc.decor.")
    assert first.get("generated_definition")["assetId"] == asset_id
    assert payload["draft"]["scene"]["environment_id"] == "office"


# --------------------------------------------------------------------------- #
# (b) forced provider failure + repair provider fixes -> PUBLISHED
# --------------------------------------------------------------------------- #


def test_forced_provider_failure_repair_provider_fixes_and_publishes(database_url):
    flaky = FlakyOnceSpecProvider(
        BRONZE_ICE_PICK_NAME, UNSEEN_SPEC_CONTENT[BRONZE_ICE_PICK_NAME]
    )
    locked, world_reqs = _extract_unseen_world_reqs()
    locked_before = dict(locked.locked_fields())
    repair = KeepReqsRepair(world_reqs)
    store, service = _service_ctx(
        database_url, spec_provider=flaky, world_repair_provider=repair
    )

    started = _run_via_service(service)
    assert started.status == "PUBLISHED", started
    assert flaky.calls >= 2, "the repair pass must re-attempt the provider"
    assert repair.calls, "the world repair provider must have been consulted"
    assert any("world.unresolved-object" in issue for issue in repair.calls[0])
    payload = json.loads(store.get_published(started.case_id, 1).payload_json)
    proc_placements = [
        p for p in payload["draft"]["world_graph"]["placements"]
        if (p.get("assetId") or p.get("asset_id") or "").startswith("proc.")
    ]
    assert proc_placements, "the repaired composition must include the proc.* object"
    # locked constraints are UNCHANGED: the published payload pins the SAME
    # locked fields the attempt started from (repair never saw them)
    assert payload["locked"] == {key: value for key, value in locked.locked_fields()}
    assert locked_before == {key: value for key, value in locked.locked_fields()}


# --------------------------------------------------------------------------- #
# (c) forced provider failure + NO repair budget -> FAILED, never published
# --------------------------------------------------------------------------- #


def test_forced_provider_failure_no_repair_fails_never_published(database_url):
    failing = FailingSpecProvider()  # no content for the unseen name
    store, service = _service_ctx(database_url, spec_provider=failing, world_repair_provider=None)
    started = _run_via_service(service)
    assert started.status == "FAILED", started
    # NEVER published: no published row exists for the attempt
    assert store.get_published(started.case_id, 1) is None
    # the provider was actually consulted for the unseen request
    assert failing.calls >= 1
    assert failing.call_log[0].requested_name == BRONZE_ICE_PICK_NAME


def test_forced_provider_failure_with_repair_cannot_fix_is_terminal(database_url):
    """A REQUIRED unresolved object with a repair provider that yields no
    revision stays FAILED when the budget is exhausted — the golden world is
    never silently swapped in, never substituted."""

    class NoOpRepair:
        def __init__(self):
            self.calls = 0

        def __call__(self, diagnostics):
            self.calls += 1
            return None  # no revision -> repair cannot fix

    failing = FailingSpecProvider()
    store, service = _service_ctx(
        database_url, spec_provider=failing, world_repair_provider=NoOpRepair()
    )
    started = _run_via_service(service)
    assert started.status == "FAILED", started
    assert store.get_published(started.case_id, 1) is None


# --------------------------------------------------------------------------- #
# (8) failed AssetSpec validation BLOCKS critical publication
# --------------------------------------------------------------------------- #


def test_failed_asset_spec_validation_blocks_critical_publication(database_url):
    invalid = InvalidSpecProvider()
    store, service = _service_ctx(database_url, spec_provider=invalid)
    started = _run_via_service(service)
    assert started.status == "FAILED", started
    # never published, never published WITH a substituted tape asset
    assert store.get_published(started.case_id, 1) is None
    assert invalid.calls >= 1


# --------------------------------------------------------------------------- #
# record-level check: golden record stays composable; a REQUIRED-missing world
# is detected by _has_unresolved_required
# --------------------------------------------------------------------------- #


def test_has_unresolved_required_marks_missing_required_object():
    from app.services.generation import _has_unresolved_required
    from app.world.requirements import (
        CRITICALITY_REQUIRED,
        ObjectRequest,
        WorldRequirements,
    )

    class _Composition:
        def __init__(self, resolution, placements):
            self.resolution_record = {"resolved": resolution}
            self.placements = placements

    # a REQUIRED request whose resolution is UNRESOLVED (assetId None)
    composition = _Composition(
        {BRONZE_ICE_PICK_NAME: {"assetId": None, "provenance": "UNRESOLVED"}}, ()
    )
    world_reqs = WorldRequirements(
        objects=(ObjectRequest(requested_name=BRONZE_ICE_PICK_NAME, criticality=CRITICALITY_REQUIRED),)
    )
    assert _has_unresolved_required(world_reqs, composition) is True
    # a REQUIRED request that resolved but was never placed is ALSO missing
    composition2 = _Composition(
        {BRONZE_ICE_PICK_NAME: {"assetId": "proc.evidence.deadbeef"}}, ()
    )
    assert _has_unresolved_required(world_reqs, composition2) is True
    # decorative defaults never trigger the terminal rule
    deco_reqs = WorldRequirements(
        objects=(ObjectRequest(requested_name=BRONZE_ICE_PICK_NAME),)
    )
    assert _has_unresolved_required(deco_reqs, composition) is False