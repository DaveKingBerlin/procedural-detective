"""Phase 14_5 + Phase 26C5 — CASE-CRITICAL FAILURE SEMANTICS.

Required Phase 14_5 deliverable 3 behavior for a CRITICALITY_REQUIRED unknown
object, as amended by Phase 26C5 (robust asset fallback):

(a) the successful procedural path publishes the unseen proc.* weapon;
(b) a REQUIRED object whose provider FAILS resolves through the DOCUMENTED
    trusted category taxonomy (stabbing_weapon -> the approved sharp prop) and
    PUBLISHES — the case never fails solely because the exact phrase is absent
    from the catalog (Phase26C5-Fix-AssetFallback §3/§18); the fallback
    provenance is explicit (CATEGORY_FALLBACK), locked constraints unchanged;
(c) the same holds with NO repair budget and with an invalid-asset-spec
    provider: the trusted fallback stands in (never a failing publication,
    never an arbitrary substitute);
(8) an AssetSpec that FAILS strict Phase 13 validation is still sanitized and
    the trusted fallback stands in — never published WITH the invalid asset.

Fail-closed behavior still exists for objects with NO safe representation
(firearm/explosive classes -> UNRESOLVED -> ``WORLD_ASSET_UNRESOLVED``), which
is covered by the resolver tests and the driver failure-code tests.

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
# (b) forced provider failure -> the TRUSTED category fallback publishes
# --------------------------------------------------------------------------- #


def test_forced_provider_failure_publishes_via_trusted_fallback(database_url):
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
    # Phase 26C5: the first provider attempt is consulted (same pre-existing
    # bounded budget) but its failure NEVER blocks the case — the documented
    # trusted fallback resolves the REQUIRED weapon. No repair pass is
    # required to rescue the case, so the flaky SECOND call is not made.
    assert flaky.calls >= 1, "the bounded provider attempt must be consulted"
    assert repair.calls == [], "the trusted fallback needs no repair rescue"
    payload = json.loads(store.get_published(started.case_id, 1).payload_json)
    placed_assets = {
        p.get("assetId") or p.get("asset_id")
        for p in payload["draft"]["world_graph"]["placements"]
    }
    assert "PROP_KITCHEN_KNIFE_01" in placed_assets
    # locked constraints are UNCHANGED: the published payload pins the SAME
    # locked fields the attempt started from (repair never saw them)
    assert payload["locked"] == {key: value for key, value in locked.locked_fields()}
    assert locked_before == {key: value for key, value in locked.locked_fields()}


def test_no_semantic_truth_alteration_in_fallback(database_url):
    """The fallback only substitutes the VISUAL asset — CaseTruth / evidence
    labels / solver input stay byte-identical (Phase26C5 §4)."""
    failing = FailingSpecProvider()
    store, service = _service_ctx(database_url, spec_provider=failing, world_repair_provider=None)
    _locked, world_reqs = _extract_unseen_world_reqs()
    started = _run_via_service(service)
    assert started.status == "PUBLISHED", started
    payload = json.loads(store.get_published(started.case_id, 1).payload_json)
    # the semantic display identity survives: the weapon object carries the
    # semantic slug id; the RESOLUTION record marks the explicit fallback.
    object_ids = {
        o.get("object_id") for o in payload["draft"].get("objects", ())
    }
    assert "bronze_ceremonial_ice_pick" in object_ids
    # NOTE: the DEV/service lane publishes the GOLDEN crime (weapon id
    # ``kitchen_knife``); the unseen weapon materializes as its OWN semantic
    # world object (Phase 14_5) whose RENDER asset is the trusted fallback.
    # The fallback asset id is never injected into the crime/evidence text.
    serialized = json.dumps(payload["draft"]["crime"]) + json.dumps(
        payload["draft"].get("evidence", ())
    )
    assert "PROP_KITCHEN_KNIFE_01" not in serialized


# --------------------------------------------------------------------------- #
# (c) no repair budget / invalid AssetSpec -> the trusted fallback still
#     publishes (never a failing publication, never an arbitrary substitute)
# --------------------------------------------------------------------------- #


def test_forced_provider_failure_no_repair_publishes_via_fallback(database_url):
    failing = FailingSpecProvider()  # no content for the unseen name
    store, service = _service_ctx(database_url, spec_provider=failing, world_repair_provider=None)
    started = _run_via_service(service)
    assert started.status == "PUBLISHED", started
    payload = json.loads(store.get_published(started.case_id, 1).payload_json)
    placed_assets = {
        p.get("assetId") or p.get("asset_id")
        for p in payload["draft"]["world_graph"]["placements"]
    }
    assert "PROP_KITCHEN_KNIFE_01" in placed_assets
    # the provider was actually consulted for the unseen request (same budget)
    assert failing.calls >= 1
    assert failing.call_log[0].requested_name == BRONZE_ICE_PICK_NAME


def test_forced_provider_failure_with_repair_cannot_fix_publishes_via_fallback(
    database_url,
):
    """A repair provider that yields no revision is IRRELEVANT for the C5
    fallback: the REQUIRED weapon is already resolved through the trusted
    category taxonomy, so the case publishes without needing the repair path."""

    class NoOpRepair:
        def __init__(self):
            self.calls = 0

        def __call__(self, diagnostics):
            self.calls += 1
            return None  # no revision

    failing = FailingSpecProvider()
    store, service = _service_ctx(
        database_url, spec_provider=failing, world_repair_provider=NoOpRepair()
    )
    started = _run_via_service(service)
    assert started.status == "PUBLISHED", started
    payload = json.loads(store.get_published(started.case_id, 1).payload_json)
    placed_assets = {
        p.get("assetId") or p.get("asset_id")
        for p in payload["draft"]["world_graph"]["placements"]
    }
    assert "PROP_KITCHEN_KNIFE_01" in placed_assets


# --------------------------------------------------------------------------- #
# (8) invalid AssetSpec validation -> sanitized; the trusted fallback stands in
# --------------------------------------------------------------------------- #


def test_invalid_asset_spec_falls_back_to_trusted_catalog_asset(database_url):
    invalid = InvalidSpecProvider()
    store, service = _service_ctx(database_url, spec_provider=invalid)
    started = _run_via_service(service)
    assert started.status == "PUBLISHED", started
    # never published WITH a broken spec-derived asset (sanitized first)
    payload = json.loads(store.get_published(started.case_id, 1).payload_json)
    placed_assets = {
        p.get("assetId") or p.get("asset_id")
        for p in payload["draft"]["world_graph"]["placements"]
    }
    assert not any(a.startswith("proc.") for a in placed_assets)
    assert "PROP_KITCHEN_KNIFE_01" in placed_assets
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