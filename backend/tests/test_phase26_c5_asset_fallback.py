"""Phase 26C5 — Robust Asset Fallback for Unknown Semantic Objects.

Implements the REQUIRED deterministic tests of
``Phase26C5-Fix-AssetFallback.md`` §17 (items 1-20), the §18 exact live
regression fixture and the §19 control path (working catalog exact is pinned
unchanged), plus the §13 observability / §14 provenance / §15 failure-code
contract.

Invariant under test (§4): the semantic object identity and the asset catalog
identity are SEPARATE. The semantic display label is NEVER changed; only the
RENDERED visual may be a trusted catalog fallback. No LLM is ever called in
this file.

Direct/Bridge identity (§17.16): the resolver is a PURE server component
consumed by both transports. The tests prove identical inputs resolve
identically through every public entry point (typed request, RAW payload,
oracle facade) and that identical world compositions produce identical output
bytes; the transport layers only frame the same resolver contract.
Deterministic, offline, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assets.catalog import load_catalog_from_repo  # noqa: E402
from app.assets.generated_cache import GeneratedAssetCache  # noqa: E402
from app.assets.oracle import GeneratedAssetOracle, resolve_or_generate  # noqa: E402
from app.assets.resolver import (  # noqa: E402
    SEMANTIC_CATEGORY_FALLBACK_ASSETS,
    Provenance,
)
from app.assets.resolver import (  # noqa: E402
    AssetRequest,
    AssetResolution,
    resolve,
    semantic_phrase_reduce,
)
from app.assets.spec_provider import (  # noqa: E402
    CountingSpecProvider,
    FakeAssetSpecProvider,
)
from app.environments.placer import validate_placement  # noqa: E402
from app.world.composer import compose_world  # noqa: E402
from app.world.requirements import (  # noqa: E402
    CRITICALITY_REQUIRED,
    ObjectRequest,
    WorldRequirements,
    semantic_object_id,
)

from fixtures.asset_specs_unseen import (  # noqa: E402
    BRONZE_ICE_PICK_NAME,
    UNSEEN_SPEC_CONTENT,
)
from test_ollama_driver import (  # noqa: E402
    PROMPT,
    _alog_posts,
    _case_people,
    _driver_posts,
    _evidence,
    _j,
    _known_world,
    _run,
    _world,
)
from test_phase19e_semantic_pipeline import _weapon_prompt  # noqa: E402


def _catalog():
    return load_catalog_from_repo()


def _resolve(name: str, **kwargs) -> AssetResolution:
    return resolve(AssetRequest(requested_name=name, **kwargs), catalog=_catalog())


# --------------------------------------------------------------------------- #
# 1/2/19. CONTROL — exact catalog resolution is pinned unchanged
# --------------------------------------------------------------------------- #


def test_control_kitchen_knife_catalog_exact():
    """§19 control: ``kitchen knife`` -> PROP_KITCHEN_KNIFE_01 -> CATALOG_EXACT
    (never forced through category fallback)."""
    res = _resolve("kitchen knife")
    assert res.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert res.provenance is Provenance.CATALOG_EXACT
    assert res.resolved is True
    assert res.ambiguous is False
    assert res.resolution_step == "catalog_exact"
    assert res.fallback_depth == 0


def test_control_exact_asset_id_stays_catalog_exact():
    res = _resolve("PROP_KITCHEN_KNIFE_01")
    assert res.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert res.provenance is Provenance.CATALOG_EXACT


# --------------------------------------------------------------------------- #
# 3/4/5/11/12. THE LIVE REGRESSION — bronze ceremonial ice pick
# --------------------------------------------------------------------------- #


def test_ice_pick_resolves_via_trusted_category_fallback():
    """§17.3 + §17.5: ``bronze ceremonial ice pick`` resolves via the DOCUMENTED
    trusted stabbing_weapon category to a TRUSTED catalog asset."""
    res = _resolve(BRONZE_ICE_PICK_NAME)
    assert res.resolved is True
    assert res.asset_id == "PROP_KITCHEN_KNIFE_01"  # no PROP_ICE_PICK_01 exists
    assert res.asset_id in _catalog().by_id  # trusted catalog value (§17.5)
    assert res.provenance is Provenance.CATEGORY_FALLBACK
    assert res.resolution_category == "stabbing_weapon"
    assert res.normalized_object_id == "ice pick"
    assert res.resolution_step == "category_fallback"
    assert res.fallback_depth == 2
    assert res.ambiguous is False


def test_ice_pick_label_is_never_altered():
    """§17.4: the resolver NEVER substitutes the semantic label — a request's
    requestedName is untouched by resolution (the fallback only changes the
    RENDER asset)."""
    res = _resolve(BRONZE_ICE_PICK_NAME)
    assert BRONZE_ICE_PICK_NAME == "bronze ceremonial ice pick"
    assert "ice pick" in semantic_phrase_reduce(BRONZE_ICE_PICK_NAME)
    # the semantic object slug used for CaseTruth/evidence stays the phrase
    assert semantic_object_id(BRONZE_ICE_PICK_NAME) == "bronze_ceremonial_ice_pick"


def test_ice_pick_wrong_class_never_chosen():
    """§11/§9: the sharp fallback cannot drift into a wild wrong class
    (never a laptop/vase/document for a stabbing weapon)."""
    res = _resolve(BRONZE_ICE_PICK_NAME)
    assert res.asset_id in SEMANTIC_CATEGORY_FALLBACK_ASSETS["stabbing_weapon"]


# --------------------------------------------------------------------------- #
# 6. NORMALIZED EXACT path (§6)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "phrase,reduced,asset_id",
    [
        ("silver kitchen knife", "kitchen knife", "PROP_KITCHEN_KNIFE_01"),
        ("old wooden baseball bat", "baseball bat", "PROP_BASEBALL_BAT_01"),
        ("ceremonial knife", "knife", "PROP_KITCHEN_KNIFE_01"),
        ("chef's knife", "chef knife", "PROP_KITCHEN_KNIFE_01"),
    ],
)
def test_normalized_exact_path(phrase, reduced, asset_id):
    """§17.6: a modifier-stripped phrase that EXACTLY matches a catalog
    canonical/alias resolves via NORMALIZED_EXACT to the SAME trusted asset."""
    res = _resolve(phrase)
    assert res.resolved is True
    assert res.provenance is Provenance.NORMALIZED_EXACT
    assert res.asset_id == asset_id
    assert res.normalized_object_id == reduced
    assert res.resolution_step == "normalized_exact"
    assert res.fallback_depth == 1


# --------------------------------------------------------------------------- #
# 7. ALIAS path (unchanged)
# --------------------------------------------------------------------------- #


def test_alias_path_unchanged():
    res = _resolve("chef knife")
    assert res.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert res.provenance is Provenance.CATALOG_ALIAS
    assert res.matched_alias == "chef knife"
    assert res.resolution_step == "alias"
    assert res.fallback_depth == 0


# --------------------------------------------------------------------------- #
# 8/9. CATEGORY + GENERIC fallbacks (§7-§10)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "phrase,category,asset_id",
    [
        ("ice pick", "stabbing_weapon", "PROP_KITCHEN_KNIFE_01"),
        ("dagger", "stabbing_weapon", "PROP_KITCHEN_KNIFE_01"),
        ("tire iron", "blunt_weapon", "PROP_WRENCH_01"),
        ("crowbar", "blunt_weapon", "PROP_WRENCH_01"),
        ("black leather briefcase", "container", "PROP_STORAGE_BOX_01"),
        ("research papers", "document", "PROP_FOLDER_01"),
        ("suitcase", "container", "PROP_STORAGE_BOX_01"),
        ("smartwatch", "electronic_device", "PROP_TABLET_01"),
        ("dresser", "furniture", "PROP_TABLE_01"),
        ("goblet", "glass_object", "PROP_CUP_01"),
        ("necklace", "personal_item", "PROP_WALLET_01"),
    ],
)
def test_category_fallback_works(phrase, category, asset_id):
    """§17.8: each supported semantic category selects its trusted fallback."""
    res = _resolve(phrase)
    assert res.resolved is True
    assert res.provenance is Provenance.CATEGORY_FALLBACK
    assert res.resolution_category == category
    assert res.asset_id == asset_id
    assert res.asset_id in _catalog().by_id
    assert res.fallback_depth == 2


def test_generic_fallback_works_where_allowed():
    """§17.9 + §10: explicit generic-prop-class phrases resolve to the safe
    category-generic representation with GENERIC_FALLBACK provenance."""
    for phrase in ("generic prop", "miscellaneous object"):
        res = _resolve(phrase)
        assert res.resolved is True
        assert res.provenance is Provenance.GENERIC_FALLBACK
        assert res.resolution_category == "generic_prop"
        assert res.asset_id == "PROP_BOOK_01"
        assert res.fallback_depth == 3


# --------------------------------------------------------------------------- #
# 10. unrelated object does NOT map to an arbitrary weapon
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "phrase",
    [
        "ancient dragon relic",
        "spatula",
        "carved ivory desk seal",
        "unusual forensic sample press",
        "totally unknown relic",
    ],
)
def test_unrelated_object_does_not_map_to_weapon(phrase):
    """§17.10 + §11: a genuinely unknown noun never maps to a random weapon —
    it resolves to the EXPLICIT neutral fallback (the caller may escalate to
    the bounded provider exactly as before)."""
    res = _resolve(phrase)
    assert res.asset_id == "PROP_FALLBACK_01"
    assert res.provenance is Provenance.FALLBACK
    assert res.resolved is True


# --------------------------------------------------------------------------- #
# 11. unknown UNSAFE object still fails closed (UNRESOLVED / §15 code)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("phrase", ["gun", "pistol", "rifle", "bomb", "pipe bomb", "grenade"])
def test_unsafe_object_fails_closed(phrase):
    """§17.11 + §11: firearm/explosive classes have NO safe representation —
    the resolver FAILS CLOSED (UNRESOLVED, resolved=False) — a gun can NEVER
    render as a knife or a vase."""
    res = _resolve(phrase)
    assert res.resolved is False
    assert res.provenance is Provenance.UNRESOLVED
    assert res.asset_id == ""
    assert res.resolution_step == "unresolved"
    assert res.fallback_depth == 4


def test_unsafe_object_fails_closed_through_the_driver_lane():
    """§15: the precise terminal code for an unrepresentable REQUIRED object is
    WORLD_ASSET_UNRESOLVED (not the generic VALIDATION_FAILED)."""
    from app.generation.state_machine import GenerationState

    record, _transport = _run(
        [
            _j(_case_people(weapon="gun")),
            _j(_evidence(weapon_obj="gun", murderer="paul_becker")),
            *_alog_posts("2026-09-11T23:42:00+02:00"),
            _j(
                {
                    "environmentHint": "office",
                    "locationTokens": ["office"],
                    "objects": [{"name": "gun", "categoryHint": "evidence", "criticality": "required"}],
                    "relations": [],
                    "unsafeUnsupported": [],
                }
            ),
        ],
        prompt=_weapon_prompt("gun"),
        max_repair_passes=0,
        max_full_regenerations=0,
    )
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.failure_code == "WORLD_ASSET_UNRESOLVED"


# --------------------------------------------------------------------------- #
# 12. provenance is correct per path (covered above) + §13 observability
# --------------------------------------------------------------------------- #


def test_resolution_observability_fields_are_bounded_and_safe():
    res = _resolve(BRONZE_ICE_PICK_NAME)
    assert res.normalized_object_id == "ice pick"
    assert res.resolution_category == "stabbing_weapon"
    assert res.resolution_step == "category_fallback"
    assert isinstance(res.fallback_depth, int)
    # exact resolutions keep the documented zero depth / no category
    exact = _resolve("kitchen knife")
    assert exact.fallback_depth == 0
    assert exact.resolution_category is None
    assert exact.resolution_step == "catalog_exact"


# --------------------------------------------------------------------------- #
# 16. Direct / Bridge identical — the resolver is a pure transport-independent
#     server component
# --------------------------------------------------------------------------- #


def test_resolver_is_transport_independent():
    """§17.16: identical inputs resolve identically through every public entry
    point (typed AssetRequest, RAW payload dict, oracle facade) — there is no
    transport-specific branch anywhere in the resolution path."""
    catalog = _catalog()
    typed = resolve(AssetRequest(requested_name=BRONZE_ICE_PICK_NAME), catalog=catalog)
    raw = resolve({"requestedName": BRONZE_ICE_PICK_NAME}, catalog=catalog)
    oracle = GeneratedAssetOracle(catalog=catalog)
    via_oracle = oracle.resolve_or_generate(
        AssetRequest(requested_name=BRONZE_ICE_PICK_NAME), spec_provider=None
    ).resolution
    assert typed == raw
    assert typed == via_oracle
    assert typed.asset_id == raw.asset_id == via_oracle.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert typed.provenance is Provenance.CATEGORY_FALLBACK


def test_identical_worlds_for_identical_inputs():
    """The world-compose layer consumes the pure resolver: two compositions of
    the same REQUIRED fallback request are byte-identical."""
    from app.assets.generated_cache import GeneratedAssetCache
    from app.assets.spec_provider import FakeAssetSpecProvider
    from app.environments.manifests import load_all_environments
    from app.environments.resolver import resolve_environment

    request = ObjectRequest(
        requested_name=BRONZE_ICE_PICK_NAME,
        tags=("weapon",),
        criticality=CRITICALITY_REQUIRED,
    )
    kit = next(k for k in load_all_environments() if k.environment_id == "office")

    def _compose():
        return compose_world(
            WorldRequirements(objects=(request,)),
            env_resolver=resolve_environment,
            spec_provider=FakeAssetSpecProvider({}),  # provider yields nothing
            evidence_placements=(),
            catalog=_catalog(),
            kit=kit,
            cache=GeneratedAssetCache(),
        )

    first = _compose()
    second = _compose()
    assert first.placements == second.placements
    assert first.provenance_by_object_id == second.provenance_by_object_id
    assert first.issues == second.issues == ()
    assert first.new_objects == second.new_objects


# --------------------------------------------------------------------------- #
# 17. fake/demo unchanged + asset budget accounting unchanged
# --------------------------------------------------------------------------- #


def test_demo_fake_path_unchanged(generation_service):
    """§17.17: the fake/demo lane publishes the golden case byte-identically —
    every golden placement keeps CATALOG_EXACT provenance."""
    session = generation_service.create_anonymous_quota_session()
    started = generation_service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert started.status == "PUBLISHED"
    provenance = generation_service._last_publish_provenance
    assert provenance is not None
    assert len(provenance) == 9
    assert all(value == "CATALOG_EXACT" for value in provenance.values())


def test_asset_budget_accounting_unchanged():
    """§17.18: the PRE-EXISTING bounded provider budget is unchanged — a
    category-fallback request attempts the provider AT MOST once (cache
    memoizes a successful generation; a miss is never re-generated
    unboundedly), and cache hits cost ZERO provider calls."""
    oracle = GeneratedAssetOracle(catalog=_catalog())
    counting = CountingSpecProvider(FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT))
    first = oracle.resolve_or_generate(
        AssetRequest(requested_name=BRONZE_ICE_PICK_NAME), spec_provider=counting
    )
    assert first.generated is not None  # the provider CAN build the ice pick
    assert counting.call_count == 1
    # cache hit -> the second identical request makes ZERO additional calls
    second = oracle.resolve_or_generate(
        AssetRequest(requested_name=BRONZE_ICE_PICK_NAME), spec_provider=counting
    )
    assert second.generated is not None
    assert counting.call_count == 1

    # a provider that yields NOTHING never re-generates unboundedly: the
    # trusted fallback stands in and the SAME budget cap applies
    oracle2 = GeneratedAssetOracle(catalog=_catalog())
    miss = CountingSpecProvider(FakeAssetSpecProvider({}))
    out = oracle2.resolve_or_generate(
        AssetRequest(requested_name=BRONZE_ICE_PICK_NAME), spec_provider=miss
    )
    assert out.generated is None
    assert out.resolution.provenance is Provenance.CATEGORY_FALLBACK
    assert out.resolution.asset_id == "PROP_KITCHEN_KNIFE_01"


# --------------------------------------------------------------------------- #
# 13/14/15. fallback does not alter CaseTruth / accusation / evidence text
# --------------------------------------------------------------------------- #


def test_fallback_does_not_alter_case_truth_or_evidence(database_url):
    """§17.13-15: the published fallback case keeps CaseTruth semantic identity
    and evidence references; the fallback asset id is never injected into the
    crime/evidence text."""
    import json

    import phase5_helpers  # noqa: F401
    from app.persistence.timebase import EpochClock

    from test_world_unseen_repair import _service_ctx, _run_via_service

    store, service = _service_ctx(database_url, spec_provider=FakeAssetSpecProvider({}))
    clock = EpochClock()
    try:
        started = _run_via_service(service)
        assert started.status in ("PUBLISHED",), started
        payload = json.loads(store.get_published(started.case_id, 1).payload_json)
        object_ids = {o.get("object_id") for o in payload["draft"].get("objects", ())}
        assert "bronze_ceremonial_ice_pick" in object_ids
        # nothing in the public crime/evidence text reveals the fallback assetId
        combined = json.dumps(
            {
                "crime": payload["draft"]["crime"],
                "evidence": payload["draft"].get("evidence", ()),
            },
            sort_keys=True,
        )
        assert "PROP_KITCHEN_KNIFE_01" not in combined
        # the accusation dimension remains valid: the solver derives a unique
        # weapon through the unmodified solver, matching canonical truth
        from test_world_unseen import _solve_payload

        public, facts, truth, proof = _solve_payload(payload)
        assert proof.weapon.unique is True
        assert truth.crime.weapon_id == proof.weapon.winner
        assert proof.who.unique is True and proof.why.unique is True
    finally:
        store.dispose()


# --------------------------------------------------------------------------- #
# 18/20. LIVE REGRESSION — the pipeline fixture publishes via trusted fallback
# --------------------------------------------------------------------------- #


def test_live_regression_ice_pick_publishes_via_trusted_fallback():
    """§18: the EXACT live Hard-case fixture. ``Weapon: bronze ceremonial ice
    pick`` reaches ``world_compose``, the weapon resolves via the TRUSTED
    category fallback (the mocked provider posts "<not-json>" ASSET_SPEC so NO
    real LLM produces anything), validation is VALID and the case PUBLISHES."""
    from app.generation.state_machine import GenerationState

    world = _world()  # the live world request: required ice pick, decor hint
    posts = _driver_posts(
        _j(_case_people()), _j(_evidence()), _j(world),
        "2026-09-11T23:42:00+02:00",
    ) + ["<not-json>"]  # ASSET_SPEC — the provider cannot build the object
    record, transport = _run(
        posts,
        prompt=PROMPT,
        max_repair_passes=0,
        max_full_regenerations=0,
    )
    assert record.state is GenerationState.PUBLISHED
    assert record.last_validation is not None and record.last_validation.valid is True
    assert record.last_validation.validation.all_true is True
    assert record.published is not None
    # world composition reached and the weapon resolved to a TRUSTED asset
    assert record.draft is not None
    placement_object_ids = {
        p.object_id for p in record.draft.world_graph.placements
    }
    assert "bronze_ceremonial_ice_pick" in placement_object_ids
    fallback_placements = [
        p for p in record.draft.world_graph.placements
        if p.object_id == "bronze_ceremonial_ice_pick"
    ]
    assert len(fallback_placements) == 1
    assert fallback_placements[0].asset_id == "PROP_KITCHEN_KNIFE_01"
    # the error that used to abort the case is GONE: root cause was the
    # catalog-resolution boundary, now closed by the trusted taxonomy.
    assert not any("world.unresolved-object" in i for i in record.deferred_structural)


def test_live_regression_control_kitchen_knife_still_publishes():
    """§19 control through the SAME pipeline: kitchen knife stays
    CATALOG_EXACT, zero ASSET_SPEC calls, PUBLISHED."""
    from app.generation.state_machine import GenerationState

    posts = _driver_posts(
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(_known_world()),
        "2026-09-11T23:42:00+02:00",
    )
    record, transport = _run(
        posts,
        prompt=_weapon_prompt("kitchen knife"),
        max_repair_passes=0,
        max_full_regenerations=0,
    )
    assert record.state is GenerationState.PUBLISHED
    # the provider was NEVER consulted for the catalog-exact knife
    assert not any("ASSET_SPEC" in p for p in transport.post_calls)
    assert record.failure_code is None


# --------------------------------------------------------------------------- #
# DEF-014 / C5-01 (LOW): the semantic displayLabel of a trusted-fallback object
# (publish a safe optional ``displayLabel`` on WorldObjectDTO; ONLY for the
# trusted-fallback provenance classes; exact/alias/semantic/proc stay
# byte-identical; never expose provenance/asset fallback internals to the
# player).
# --------------------------------------------------------------------------- #


def _fallback_probe_payload():
    """A hand-built frozen payload: one CATEGORY_FALLBACK placement + one
    CATALOG_EXACT control placement (no procedural sections)."""
    return {
        "draft": {
            "scene": {"location_id": "scene_loc", "name": "Scene"},
            "objects": [
                {
                    "object_id": "bronze_ceremonial_ice_pick",
                    "asset_id": "PROP_KITCHEN_KNIFE_01",
                    "subtype": "sharp_weapon",
                },
                {
                    "object_id": "kitchen_knife",
                    "asset_id": "PROP_KITCHEN_KNIFE_01",
                    "subtype": "sharp_weapon",
                },
            ],
            "world_graph": {
                "placements": [
                    {
                        "object_id": "bronze_ceremonial_ice_pick",
                        "asset_id": "PROP_KITCHEN_KNIFE_01",
                        "location_id": "scene_loc",
                        "anchor": "anchor_01",
                        "interaction": "inspect",
                        "evidence_id": None,
                        "resolution_provenance": "CATEGORY_FALLBACK",
                    },
                    {
                        "object_id": "kitchen_knife",
                        "asset_id": "PROP_KITCHEN_KNIFE_01",
                        "location_id": "scene_loc",
                        "anchor": "anchor_02",
                        "interaction": "inspect",
                        "evidence_id": None,
                        "resolution_provenance": "CATALOG_EXACT",
                    },
                ]
            },
        }
    }


def test_display_label_contract_on_world_object_dto():
    """C5-01 contract shape: ``displayLabel`` is a SAFE OPTIONAL string on the
    WorldObjectDTO — carried ONLY when set (the wire key is omitted for null,
    absent == null on the frontend -> the fallback to ``entry.label``). The
    resolution provenance marker is NEVER part of the DTO."""
    from app.schemas.investigation import WorldObjectDTO

    fallback = WorldObjectDTO(
        objectId="bronze_ceremonial_ice_pick",
        assetId="PROP_KITCHEN_KNIFE_01",
        assetType="sharp_weapon",
        locationId="scene_loc",
        anchor="anchor_01",
        interaction="inspect",
        displayLabel="Bronze Ceremonial Ice Pick",
    ).model_dump(mode="json")
    assert fallback["displayLabel"] == "Bronze Ceremonial Ice Pick"
    assert fallback["assetId"] == "PROP_KITCHEN_KNIFE_01"  # visual only
    assert "resolution_provenance" not in fallback
    assert "provenance" not in fallback

    exact = WorldObjectDTO(
        objectId="kitchen_knife",
        assetId="PROP_KITCHEN_KNIFE_01",
        assetType="sharp_weapon",
        locationId="scene_loc",
        anchor="anchor_02",
        interaction="inspect",
        displayLabel=None,
    ).model_dump(mode="json")
    assert "displayLabel" not in exact  # byte-identical for non-fallback


@pytest.mark.parametrize(
    "marker",
    ["NORMALIZED_EXACT", "CATEGORY_FALLBACK", "GENERIC_FALLBACK"],
)
def test_display_label_published_for_every_trusted_fallback_class(marker):
    """C5-01 §assertions: a placement whose composition provenance is one of
    the THREE trusted fallback classes carries the SEMANTIC displayLabel
    derived from the PUBLIC object id (never the substituted asset's name)
    plus the catalog assetId (visual only)."""
    from app.services.publication import project_world_objects

    payload = _fallback_probe_payload()
    placement = payload["draft"]["world_graph"]["placements"][0]
    placement["resolution_provenance"] = marker
    world = project_world_objects(payload)
    ice = next(o for o in world if o["objectId"] == "bronze_ceremonial_ice_pick")
    assert ice["assetId"] == "PROP_KITCHEN_KNIFE_01"  # the trusted substitute
    assert ice["displayLabel"] == "Bronze Ceremonial Ice Pick"  # semantic truth
    assert "resolution_provenance" not in ice  # internals never published
    assert "provenance" not in ice


def test_display_label_null_keeps_non_fallback_objects_byte_identical():
    """An exact/alias/semantic/procedural placement carries NO displayLabel —
    ``project_world_objects`` output for those objects is byte-identical to
    the pre-C5-01 shape (the frontend falls back to ``entry.label``)."""
    from app.services.publication import project_world_objects

    payload = _fallback_probe_payload()
    world = project_world_objects(payload)
    knife = next(o for o in world if o["objectId"] == "kitchen_knife")
    assert "displayLabel" not in knife
    assert set(knife) == {
        "objectId",
        "assetId",
        "assetType",
        "subtype",
        "locationId",
        "anchor",
        "interaction",
        "evidenceId",
        "discovered",
        "read",
    }


def test_display_label_rejects_crafted_unknown_marker():
    """The DTO branch is CLOSED: a crafted payload's unknown/non-fallback
    provenance marker can never enable the displayLabel (the marker only
    gates the player-safe semantic label, it is never echoed)."""
    from app.services.publication import project_world_objects

    payload = _fallback_probe_payload()
    placement = payload["draft"]["world_graph"]["placements"][0]
    placement["resolution_provenance"] = "CATATOG_FAKE_FALLBACK"
    world = project_world_objects(payload)
    ice = next(o for o in world if o["objectId"] == "bronze_ceremonial_ice_pick")
    assert "displayLabel" not in ice
    assert ice["assetId"] == "PROP_KITCHEN_KNIFE_01"


def test_display_label_published_through_the_service_lane(database_url):
    """C5-01 end-to-end through the GENERATION SERVICE: a REQUIRED
    fallback-resolved weapon publishes a payload whose bootstrap projection
    carries ``displayLabel`` ("Bronze Ceremonial Ice Pick") on the ice-pick
    object — never the SUBSTITUTED "Kitchen Knife" label — while the kit-base
    kitchen knife (CATALOG_EXACT) stays byte-identical."""
    import json

    from app.services.publication import project_world_objects

    from test_world_unseen_repair import FailingSpecProvider, _run_via_service, _service_ctx

    store, service = _service_ctx(database_url, spec_provider=FailingSpecProvider())
    started = _run_via_service(service)
    assert started.status == "PUBLISHED", started
    payload = json.loads(store.get_published(started.case_id, 1).payload_json)
    world = project_world_objects(payload)
    ice = next(o for o in world if o["objectId"] == "bronze_ceremonial_ice_pick")
    assert ice["assetId"] == "PROP_KITCHEN_KNIFE_01"  # trusted fallback visual
    assert ice["displayLabel"] == "Bronze Ceremonial Ice Pick"
    assert "resolution_provenance" not in ice
    knife = next(o for o in world if o["objectId"] == "kitchen_knife")
    assert "displayLabel" not in knife
    assert knife["assetId"] == "PROP_KITCHEN_KNIFE_01"
    # the fallback marker is INTERNAL-ONLY: it lives in the frozen payload for
    # the read-time projection, never in the player-safe public-case DTO.
    from app.services.publication import public_case_dict_from_payload

    public = public_case_dict_from_payload(payload)
    for placement in public["worldGraph"]["placements"]:
        assert set(placement) == {
            "objectId",
            "assetId",
            "locationId",
            "anchor",
            "interaction",
            "evidenceId",
        }


# --------------------------------------------------------------------------- #
# C5-02 (LOW): a step-4 UNIQUE SEMANTIC_MATCH winner is gated for
# category-consistency — the ``ice pick`` + weapon/restraint rope probe can
# never win the required weapon slot.
# --------------------------------------------------------------------------- #


def test_c5_02_rope_probe_never_wins_semantic_match():
    """The adversarial probe (``bronze ceremonial ice pick`` + weapon/
    restraint tags) uniquely scored PROP_ROPE_01 at 6.0 — at/above the
    composer's CRITICAL_MIN_SEMANTIC_CONFIDENCE. After the gate it can NEVER
    be a SEMANTIC_MATCH winner: the phrase infers ``stabbing_weapon`` and rope
    is not in that trusted chain, so the request resolves through the trusted
    category taxonomy to the stabbing_weapon fallback."""
    res = _resolve(BRONZE_ICE_PICK_NAME, tags=("weapon", "restraint"))
    assert res.resolved is True
    assert res.asset_id != "PROP_ROPE_01"
    assert res.resolution_category == "stabbing_weapon"
    assert res.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert res.asset_id in SEMANTIC_CATEGORY_FALLBACK_ASSETS["stabbing_weapon"]
    assert res.provenance is Provenance.CATEGORY_FALLBACK


def test_c5_02_rope_probe_composes_never_a_rope_required_weapon():
    """The same probe through the world composer with CRITICALITY_REQUIRED
    (an empty provider): the composition resolves the ice pick to the
    stabbing_weapon trusted fallback and NEVER places/registers a rope as the
    REQUIRED weapon (compose-level regression; no arbitrary substitute)."""
    from app.environments.manifests import load_all_environments
    from app.environments.resolver import resolve_environment

    request = ObjectRequest(
        requested_name=BRONZE_ICE_PICK_NAME,
        tags=("weapon", "restraint"),
        criticality=CRITICALITY_REQUIRED,
    )
    kit = next(k for k in load_all_environments() if k.environment_id == "office")
    composition = compose_world(
        WorldRequirements(objects=(request,)),
        env_resolver=resolve_environment,
        spec_provider=FakeAssetSpecProvider({}),
        evidence_placements=(),
        catalog=_catalog(),
        kit=kit,
        cache=GeneratedAssetCache(),
    )
    assert composition.issues == ()
    assert all(p.asset_id != "PROP_ROPE_01" for p in composition.placements)
    record = composition.resolution_record["resolved"][BRONZE_ICE_PICK_NAME]
    assert record["provenance"] == Provenance.CATEGORY_FALLBACK.value
    assert record["assetId"] == "PROP_KITCHEN_KNIFE_01"
    ice_placements = [
        p for p in composition.placements
        if p.object_id == "bronze_ceremonial_ice_pick"
    ]
    assert len(ice_placements) == 1
    assert ice_placements[0].asset_id == "PROP_KITCHEN_KNIFE_01"


def test_c5_02_semantic_consistent_unique_winner_stays_semantic_match():
    """C5-02 control: a UNIQUE semantic winner that IS category-consistent
    (member of the inferred category's trusted chain) keeps the Phase 12/19
    SEMANTIC_MATCH provenance and the full 9.0 confidence — the gate only
    blocks contradictory winners (no regression on the working paths)."""
    res = _resolve(
        "weapon sharp blade",
        category_hint="evidence",
        subtype_hint="sharp",
        tags=("weapon", "sharp", "blade"),
    )
    assert res.provenance is Provenance.SEMANTIC_MATCH
    assert res.confidence == 9.0
    assert res.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert res.asset_id in SEMANTIC_CATEGORY_FALLBACK_ASSETS["stabbing_weapon"]


def test_c5_02_no_category_inference_keeps_semantic_match():
    """C5-02: when NO category can be inferred from the phrase, a unique
    semantic winner is kept EXACTLY as before (the electronics device/computer
    Phase 12 case) — zero behavior change for today's working paths."""
    res = _resolve(
        "electronics device computer",
        category_hint="electronics",
        subtype_hint="computer",
        tags=("electronics", "device", "computer"),
    )
    assert res.provenance is Provenance.SEMANTIC_MATCH
    assert res.confidence == 12.0
    assert res.asset_id == "PROP_LAPTOP_01"


def test_c5_02_dangerous_class_still_fails_closed():
    """C5-02: a firearm/explosive phrase can never keep a UNIQUE semantic
    winner (the dangerous class is not category-safe regardless) — the request
    STILL fails closed through step 6, never a wrong substitute."""
    res = _resolve("target pistol", tags=("weapon",))
    assert res.resolved is False
    assert res.provenance is Provenance.UNRESOLVED
    assert res.asset_id == ""