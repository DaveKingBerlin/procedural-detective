"""Phase 14_5 — unseen prompt object -> procedural asset generation tests.

Closes the REQUIRED Phase 14_5 test list at the composer/oracle/service/data
level (repair/failure semantics live in ``test_world_unseen_repair.py``):

1.  unseen prompt object reaches the AssetSpecProvider
2.  provider called only after Oracle paths fail
3.  successful AssetSpec becomes a proc.* asset
4.  object present in the published WorldGraph
5.  survives restart/reload (same DB, fresh service -> same proc.* object)
6.  remains pinned after catalog/compiler changes
7.  evidence-bearing proc.* object is directly clickable (data level)
8.  (failed AssetSpec validation blocks critical publication -> repair file)
9.  no semantic wrong-object substitution (SEMANTIC-BUT-LOSSY escalation)
10. same prompt produces identical spec/id/world (two full runs)
11. provider-call budgets remain bounded (cap + cache-hit-zero-calls)
12. hostile noun / injection cannot bypass validation
13. CaseTruth never reaches AssetSpecProvider
14. AssetSpec never reaches the solver
15. no generated implementation/code reaches the frontend

Plus the defensive "unseen examples are NOT in any production lookup" test and
the dev-provider E2E seam test (unseen weapon discoverable + clickable WITHOUT
breaking solver uniqueness).

Deterministic, no network, real SQLite files (restart/pinning honesty).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assets.catalog import load_catalog_from_repo  # noqa: E402
from app.assets.compiler import (  # noqa: E402
    COMPILER_VERSION,
    HITBOX_MAX,
    HITBOX_MIN,
    asset_id_for,
    validate_embedded_definition,
)
from app.assets.oracle import GeneratedAssetOracle  # noqa: E402
from app.assets.resolver import AssetRequest, Provenance  # noqa: E402
from app.assets.spec_provider import (  # noqa: E402
    CountingSpecProvider,
    FakeAssetSpecProvider,
    MAX_SPEC_PROVIDER_CALLS_PER_GENERATION,
)
from app.assets.specs import parse_asset_spec  # noqa: E402
from app.environments.manifests import load_all_environments  # noqa: E402
from app.environments.placer import EVIDENCE_CAPABLE_TYPES  # noqa: E402
from app.generation.safety import INTERACTION_ALLOWLIST  # noqa: E402
from app.world.composer import (  # noqa: E402
    CRITICAL_MIN_SEMANTIC_CONFIDENCE,
    compose_world,
)
from app.world.extract import KNOWN_OBJECT_TABLE, extract_world_requirements  # noqa: E402
from app.world.requirements import (  # noqa: E402
    CRITICALITY_REQUIRED,
    ObjectRequest,
    WorldRequirements,
)

from fixtures.asset_specs_unseen import (  # noqa: E402
    BRONZE_ICE_PICK_NAME,
    CARVED_IVORY_DESK_SEAL_NAME,
    UNSEEN_SPEC_CONTENT,
)
from fixtures.world_showcase import UNSEEN_WEAPON_PROMPT  # noqa: E402
from phase5_helpers import seed_session  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


def _unseen_provider():
    return FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT)


def _counting_provider():
    return CountingSpecProvider(_unseen_provider())


def _catalog():
    return load_catalog_from_repo()


def _kits():
    return {k.environment_id: k for k in load_all_environments()}


def _compose(prompt, provider, environment_id="apartment"):
    from app.assets.generated_cache import GeneratedAssetCache
    from app.environments.resolver import resolve_environment

    world_reqs = extract_world_requirements(prompt)
    kit = _kits()[environment_id]
    # a FRESH generated-asset cache per composition: the SHARED module-level
    # oracle cache would serve a previously-cached spec without calling the
    # test's own provider (order-dependent results) — the tests isolate it.
    return compose_world(
        world_reqs,
        env_resolver=resolve_environment,
        spec_provider=provider,
        evidence_placements=(),
        catalog=_catalog(),
        kit=kit,
        cache=GeneratedAssetCache(),
    )


def _service_ctx(url, spec_provider=None, world_repair_provider=None, cache=None):
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
        generated_cache=cache if cache is not None else GeneratedAssetCache(),
    )
    return store, service


def _publish_unseen(url, *, spec_provider=None, world_repair_provider=None, seed="SESS-1"):
    from app.persistence.timebase import EpochClock

    store, service = _service_ctx(
        url,
        spec_provider=spec_provider if spec_provider is not None else _unseen_provider(),
        world_repair_provider=world_repair_provider,
    )
    clock = EpochClock()
    seed_session(store, seed, clock)
    started = service.start_case_generation(
        UNSEEN_WEAPON_PROMPT,
        anonymous_quota_session_id=seed,
    )
    return store, service, started, clock


def _payload(store, case_id, version=1):
    return json.loads(store.get_published(case_id, version).payload_json)


def _proc_placements(payload):
    result = []
    for p in payload["draft"]["world_graph"]["placements"]:
        asset_id = _pid(p)
        if asset_id and str(asset_id).startswith("proc."):
            result.append(p)
    return result


def _pid(placement):
    return placement.get("assetId") or placement.get("asset_id") or ""


def _spec_for(name):
    return parse_asset_spec(UNSEEN_SPEC_CONTENT[name.casefold().strip()], non_throwing=False)


def _draft_from_payload(payload):
    from app.generation.schemas import (
        CrimeSpec,
        CrimeTimeSpec,
        EvidenceSpec,
        GeneratedDraft,
        LocationSpec,
        MotiveSpec,
        ObjectSpec,
        PersonSpec,
        PropSpec,
        SceneSpec,
        TravelRuleSpec,
        WorldGraphSpec,
    )

    d = payload["draft"]
    return GeneratedDraft(
        crime=CrimeSpec(
            type=d["crime"]["type"], victim_id=d["crime"]["victim_id"],
            murderer_id=d["crime"]["murderer_id"], motive_id=d["crime"]["motive_id"],
            weapon_id=d["crime"]["weapon_id"], location_id=d["crime"]["location_id"],
            crime_time=CrimeTimeSpec(
                canonical=d["crime"]["crime_time"]["canonical"],
                accusation_tolerance_seconds=d["crime"]["crime_time"]["accusation_tolerance_seconds"],
            ),
        ),
        persons=tuple(
            PersonSpec(person_id=p["person_id"], name=p["name"], role=p["role"],
                       affordances=tuple(p["affordances"]), presented_data=p.get("presented_data") or {})
            for p in d["persons"]
        ),
        motives=tuple(
            MotiveSpec(motive_id=m["motive_id"], label=m["label"], affordances=tuple(m["affordances"]))
            for m in d["motives"]
        ),
        objects=tuple(
            ObjectSpec(object_id=o["object_id"], asset_id=o["asset_id"],
                       affordances=tuple(o["affordances"]), subtype=o.get("subtype"))
            for o in d["objects"]
        ),
        locations=tuple(LocationSpec(location_id=l["location_id"], name=l["name"]) for l in d["locations"]),
        travel_rules=tuple(
            TravelRuleSpec(from_location_id=t["from_location_id"], to_location_id=t["to_location_id"],
                           travel_time_seconds=t["travel_time_seconds"])
            for t in d["travel_rules"]
        ),
        scene=SceneSpec(location_id=d["scene"]["location_id"], name=d["scene"]["name"],
                        environment_id=d["scene"].get("environment_id"),
                        environment_version=d["scene"].get("environment_version")),
        evidence=tuple(
            EvidenceSpec(id=e["id"], kind=e["kind"], reliability=e.get("reliability"),
                         discoverable=e.get("discoverable") if e.get("discoverable") is not None else True,
                         source_ref=e.get("source_ref"),
                         presentation=e.get("presentation") or {},
                         propositions=tuple(
                             PropSpec(type=p["type"], person_id=p.get("person_id"),
                                      location_id=p.get("location_id"), object_id=p.get("object_id"),
                                      motive_id=p.get("motive_id"), observed_at=p.get("observed_at"),
                                      uncertainty_seconds=p.get("uncertainty_seconds", 0),
                                      structured=p.get("structured") or {})
                             for p in e["propositions"]
                         ))
            for e in d["evidence"]
        ),
        world_graph=WorldGraphSpec(),
    )


def _solve_payload(payload):
    from app.domain.solver import solve_case

    from app.generation.pipeline import _draft_to_phase3

    draft = _draft_from_payload(payload)
    public, facts, truth = _draft_to_phase3(draft, case_id="CASE-X", title="T")
    return public, facts, truth, solve_case(public, facts)


# --------------------------------------------------------------------------- #
# 0. defensive contract: the three unseen examples are NOT in production lookup
# --------------------------------------------------------------------------- #


def test_unseen_examples_absent_from_production_lookups():
    """The three golden unseen examples exist in NO production lookup: not in
    KNOWN_OBJECT_TABLE, not in the catalog manifest/aliases, not in the Phase
    13 fixtures file. This is the Phase 14_5 anti-gaming guard (grep-proof:
    the strings appear ONLY in tests/fixtures/asset_specs_unseen.py and the
    defensive prompts here)."""
    examples = (
        "bronze ceremonial ice pick",
        "unusual forensic sample press",
        "carved ivory desk seal",
    )
    # 1. KNOWN_OBJECT_TABLE (requested names + triggers)
    table_text = " ".join(
        [entry.requested_name for entry in KNOWN_OBJECT_TABLE]
        + [trigger for entry in KNOWN_OBJECT_TABLE for trigger in entry.triggers]
    ).casefold()
    for example in examples:
        assert example not in table_text, example
    # 2. catalog manifest (assetIds/canonicalNames/aliases + any text fields)
    catalog_path = REPO_ROOT / "assets" / "catalog" / "catalog.json"
    catalog_text = catalog_path.read_text(encoding="utf-8").casefold()
    for example in examples:
        assert example not in catalog_text, example
        assert example.replace(" ", "_") not in catalog_text, example
    # 3. Phase 13 fixtures + the builder's shipped dev-mode provider specs
    for fixture in (
        Path(__file__).resolve().parent / "fixtures" / "asset_specs.py",
        Path(__file__).resolve().parent / ".." / "app" / "world" / "composer.py",
    ):
        text = fixture.read_text(encoding="utf-8").casefold()
        for example in examples:
            assert example not in text, (fixture.name, example)
    # 4. the extractor's noun vocabulary does not CONTAIN the full phrases
    from app.world.extract import NOUN_HEAD_VOCABULARY

    assert "ice pick" not in NOUN_HEAD_VOCABULARY
    assert "sample press" not in NOUN_HEAD_VOCABULARY
    assert "desk seal" not in NOUN_HEAD_VOCABULARY
    # 5. extraction treats them as UNSEEN (bounded ObjectRequirements, full
    #    phrase requestedName — never a catalog name)
    extracted = extract_world_requirements(
        "A carved ivory desk seal and an unusual forensic sample press were found "
        "in the office."
    )
    names = {o.requested_name for o in extracted.objects}
    assert "carved ivory desk seal" in names
    assert "unusual forensic sample press" in names
    # 6. catalog resolution of the unseen names is a MISS (falls back)
    resolution = __import__("app.assets.resolver", fromlist=["resolve"]).resolve(
        AssetRequest(requested_name=BRONZE_ICE_PICK_NAME)
    )
    assert resolution.provenance is Provenance.FALLBACK


# --------------------------------------------------------------------------- #
# 1. unseen prompt object reaches the AssetSpecProvider
# --------------------------------------------------------------------------- #


def test_unseen_prompt_object_reaches_asset_spec_provider():
    counting = _counting_provider()
    composition = _compose(UNSEEN_WEAPON_PROMPT, counting, "office")
    assert counting.call_log, "the unseen object must reach the AssetSpecProvider"
    assert any(
        request.requested_name == BRONZE_ICE_PICK_NAME
        for request in counting.call_log
    ), counting.call_log
    assert composition.issues == ()


# --------------------------------------------------------------------------- #
# 2. provider called only after Oracle paths fail
# --------------------------------------------------------------------------- #


def test_provider_called_only_after_oracle_paths_fail():
    counting = _counting_provider()
    # a CATALOG/alias name resolves EXACTLY -> ZERO provider calls
    catalog_composition = _compose("A wrench was left behind in the depot.", counting, "warehouse")
    assert catalog_composition.issues == ()
    assert counting.call_count == 0, counting.call_log
    # an unseen name misses the catalog -> provider called (once)
    _compose(UNSEEN_WEAPON_PROMPT, counting, "office")
    assert [r.requested_name for r in counting.call_log] == [BRONZE_ICE_PICK_NAME]


# --------------------------------------------------------------------------- #
# 3. successful AssetSpec becomes a proc.* asset
# --------------------------------------------------------------------------- #


def test_successful_asset_spec_becomes_proc_asset():
    counting = _counting_provider()
    composition = _compose(UNSEEN_WEAPON_PROMPT, counting, "office")
    assert composition.issues == ()
    proc_placements = [
        p for p in composition.placements if p.asset_id.startswith("proc.")
    ]
    assert proc_placements
    placement = proc_placements[0]
    assert placement.generated_definition is not None
    definition = placement.generated_definition
    assert definition["assetId"] == placement.asset_id
    assert definition["assetId"].startswith("proc.decor.")
    assert definition["compilerVersion"] == COMPILER_VERSION
    assert definition["schemaVersion"] == 1
    # provenance recorded
    assert (
        composition.provenance_by_object_id[placement.object_id]
        == Provenance.PROCEDURAL_GENERATED.value
    )
    # frozen definition = declarative render metadata only (no code, no URLs)
    assert set(definition) == {
        "compilerVersion", "schemaVersion", "assetId", "canonicalName",
        "category", "subtype", "dimensions", "parts", "hitbox",
    }
    assert definition["canonicalName"] == "Bronze Ceremonial Ice Pick"


# --------------------------------------------------------------------------- #
# 4. object present in the published WorldGraph
# --------------------------------------------------------------------------- #


def test_object_present_in_published_world_graph(database_url):
    store, service, started, _clock = _publish_unseen(database_url)
    assert started.status == "PUBLISHED", started
    payload = _payload(store, started.case_id)
    proc_placements = _proc_placements(payload)
    assert proc_placements, "the unseen proc.* object must be in the published world graph"
    placement = proc_placements[0]
    assert placement.get("objectId") or placement.get("object_id") == "bronze_ceremonial_ice_pick"
    asset_id = placement.get("assetId") or placement.get("asset_id")
    assert asset_id.startswith("proc.decor.")
    definition = placement.get("generated_definition")
    assert definition["assetId"] == asset_id
    # the object is a real public world object (INSPECTABLE, never a weapon)
    objects_by_id = {o["object_id"]: o for o in payload["draft"]["objects"]}
    ice_pick = objects_by_id["bronze_ceremonial_ice_pick"]
    assert ice_pick["asset_id"] == asset_id
    assert "POTENTIAL_WEAPON" not in ice_pick["affordances"]
    assert "INSPECTABLE" in ice_pick["affordances"]
    # UNSEEN_WEAPON_EXPECTED contract: huge parts of the expected summary hold
    assert payload["draft"]["scene"]["environment_id"] == "office"


# --------------------------------------------------------------------------- #
# 5. survives restart/reload on the same DB
# --------------------------------------------------------------------------- #


def test_generated_object_survives_restart_reload(database_url):
    store, service, started, _clock = _publish_unseen(database_url)
    before = _payload(store, started.case_id)
    asset_id_before = _pid(_proc_placements(before)[0])
    store.dispose()
    # fresh process: a NEW Store + service over the SAME file
    reopened_store = _service_ctx(database_url)[0]
    reopened = json.loads(reopened_store.get_published(started.case_id, 1).payload_json)
    assert _pid(_proc_placements(reopened)[0]) == asset_id_before
    assert json.dumps(reopened, sort_keys=True) == json.dumps(before, sort_keys=True)
    reopened_store.dispose()


# --------------------------------------------------------------------------- #
# 6. remains pinned after catalog/compiler changes
# --------------------------------------------------------------------------- #


def test_generated_object_remains_pinned_after_catalog_compiler_changes(database_url):
    store, service, started, _clock = _publish_unseen(database_url)
    payload_a = _payload(store, started.case_id)
    before_bytes = json.dumps(payload_a, sort_keys=True)

    # (a) a MUTATED catalog copy (recolour + bump a descriptor version) changes
    #     neither the re-composition nor the stored pinned payload
    import tempfile

    catalog_path = REPO_ROOT / "assets" / "catalog" / "catalog.json"
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    for asset in data["assets"]:
        if asset["assetId"] == "PROP_KITCHEN_KNIFE_01":
            asset["version"] += 1
            asset["colors"]["shade"] = "#ff0000"
    with tempfile.TemporaryDirectory() as tmpdir:
        mutated = Path(tmpdir) / "catalog.json"
        mutated.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        from app.assets.catalog import load_catalog_from_repo as load_cat

        mutated_catalog = load_cat(path=mutated)
        counting = _counting_provider()
        normal = _compose_mutated(_catalog(), _counting_provider())
        re_composed = _compose_mutated(mutated_catalog, counting)
        assert re_composed == normal  # catalog mutation does not change the world
    # (b) a DIFFERENT compiler version would change the id — the published
    #     definition is version-locked to the CURRENT compiler
    spec = _spec_for(BRONZE_ICE_PICK_NAME)
    pinned_id = _pid(_proc_placements(payload_a)[0])
    assert pinned_id == asset_id_for(spec, compiler_version=COMPILER_VERSION)
    assert pinned_id != asset_id_for(spec, compiler_version=COMPILER_VERSION + 1)
    # (c) the stored payload bytes are UNCHANGED by all the above
    assert json.dumps(_payload(store, started.case_id), sort_keys=True) == before_bytes


def _compose_mutated(catalog, provider):
    from app.assets.generated_cache import GeneratedAssetCache
    from app.environments.resolver import resolve_environment

    world_reqs = extract_world_requirements(UNSEEN_WEAPON_PROMPT)
    kit = _kits()["office"]
    return json.dumps(
        [
            (p.object_id, p.asset_id, p.anchor, p.interaction, p.evidence_id)
            for p in compose_world(
                world_reqs,
                env_resolver=resolve_environment,
                spec_provider=provider,
                evidence_placements=(),
                catalog=catalog,
                kit=kit,
                cache=GeneratedAssetCache(),
            ).placements
        ],
        sort_keys=True,
    )


# --------------------------------------------------------------------------- #
# 7. evidence-bearing proc.* object is directly clickable (data level)
# --------------------------------------------------------------------------- #


def test_evidence_bearing_proc_object_is_directly_clickable(database_url):
    from app.services.publication import project_discovery, project_world_objects

    store, service, started, _clock = _publish_unseen(database_url)
    payload = _payload(store, started.case_id)
    # PD-SEC-01: the projection exposes evidenceId ONLY for player-DISCOVERED
    # evidence. Undiscovered (empty discovered set) -> null evidenceId; once
    # the player has discovered the forensic record the id is player-known and
    # appears (that is exactly what the projection does for the placeholder
    # post-discovery bootstrap below).
    undiscovered = next(
        item for item in project_world_objects(payload) if item["assetId"].startswith("proc.")
    )
    assert undiscovered["interaction"] in INTERACTION_ALLOWLIST
    assert undiscovered["evidenceId"] is None
    discovered_obj = next(
        item
        for item in project_world_objects(
            payload, discovered={"bronze_ceremonial_ice_pick_fp_01"}
        )
        if item["assetId"].startswith("proc.")
    )
    ice_pick = discovered_obj
    # non-empty interaction + evidence link + discoverable evidence
    assert ice_pick["interaction"] in INTERACTION_ALLOWLIST
    assert ice_pick["evidenceId"] == "bronze_ceremonial_ice_pick_fp_01"
    assert ice_pick["generated"] is not None
    # pickable hitbox bounds (data level)
    hitbox = ice_pick["generated"]["hitbox"]["scale"]
    for axis in ("x", "y", "z"):
        value = hitbox[axis]
        assert math.isfinite(value)
        assert HITBOX_MIN <= value <= HITBOX_MAX
    # evidence-capable anchor (data level)
    placement = next(
        p for p in payload["draft"]["world_graph"]["placements"]
        if (p.get("objectId") or p.get("object_id")) == ice_pick["objectId"]
    )
    kit = _kits()["office"]
    anchor = kit.by_id[placement.get("anchor") or placement.get("anchor")]
    assert anchor.type in EVIDENCE_CAPABLE_TYPES
    # the forensic evidence is directly discoverable
    discovery = project_discovery(payload, "bronze_ceremonial_ice_pick_fp_01")
    assert discovery is not None
    assert discovery["kind"] == "forensic"
    assert discovery["interaction"] == "inspect"
    assert "fingerprint" in str(discovery["title"]).casefold()


# --------------------------------------------------------------------------- #
# 9. no semantic wrong-object substitution (SEMANTIC-BUT-LOSSY escalation)
# --------------------------------------------------------------------------- #


def test_no_semantic_wrong_object_substitution():
    from app.assets.generated_cache import GeneratedAssetCache

    counting = _counting_provider()
    # the request carries a WEAPON tag that ties it to the kitchen knife at
    # confidence 3.0 (one tag) — BELOW CRITICAL_MIN_SEMANTIC_CONFIDENCE (6.0).
    # A REQUIRED object is NEVER substituted by that unrelated catalog asset:
    # it escalates to the provider and gets the REAL proc.* object.
    request = ObjectRequest(
        requested_name=BRONZE_ICE_PICK_NAME,
        tags=("weapon",),
        criticality=CRITICALITY_REQUIRED,
    )
    composition = compose_world(
        WorldRequirements(objects=(request,)),
        env_resolver=_resolver(),
        spec_provider=counting,
        evidence_placements=(),
        catalog=_catalog(),
        kit=_kits()["office"],
        cache=GeneratedAssetCache(),
    )
    assert composition.issues == ()
    assert [r.requested_name for r in counting.call_log] == [BRONZE_ICE_PICK_NAME]
    record = composition.resolution_record["resolved"][BRONZE_ICE_PICK_NAME]
    assert record["assetId"].startswith("proc.decor.")
    assert record["provenance"] == Provenance.PROCEDURAL_GENERATED.value
    # the kitchen knife in the world is the GOLDEN base knife (never a new
    # substitution for the request: the unexpected extra placement is absent)
    knife_placements = [
        p for p in composition.placements
        if p.asset_id == "PROP_KITCHEN_KNIFE_01"
    ]
    assert len(knife_placements) == 1  # the base only

    # control: a DECORATIVE request with the SAME lossy tag match is NOT
    # escalated (decorative props may degrade to the semantic match)
    deco = CountingSpecProvider(FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT))
    deco_request = ObjectRequest(
        requested_name=BRONZE_ICE_PICK_NAME,
        tags=("weapon",),
        criticality="decorative",
    )
    _compose_request(deco_request, deco)
    assert deco.call_count == 0, "decorative lossy matches do not call the provider"
    # failure side: REQUIRED + provider miss -> world.unresolved-object (the
    # lossy substitution is the ONLY alternative and is rejected as well)
    miss_provider = CountingSpecProvider(FakeAssetSpecProvider({}))
    failed = compose_world(
        WorldRequirements(objects=(request,)),
        env_resolver=_resolver(),
        spec_provider=miss_provider,
        evidence_placements=(),
        catalog=_catalog(),
        kit=_kits()["office"],
        cache=GeneratedAssetCache(),
    )
    assert failed.issues
    assert any("world.unresolved-object" in issue for issue in failed.issues)
    assert "PROP_KITCHEN_KNIFE_01" not in {
        p.asset_id for p in failed.placements
    } or len([p for p in failed.placements if p.asset_id == "PROP_KITCHEN_KNIFE_01"]) == 1


def _resolver():
    from app.environments.resolver import resolve_environment

    return resolve_environment


def _compose_request(request, provider, environment_id="office"):
    from app.assets.generated_cache import GeneratedAssetCache
    from app.environments.resolver import resolve_environment

    return compose_world(
        WorldRequirements(objects=(request,)),
        env_resolver=resolve_environment,
        spec_provider=provider,
        evidence_placements=(),
        catalog=_catalog(),
        kit=_kits()[environment_id],
        cache=GeneratedAssetCache(),
    )


def test_critical_min_semantic_confidence_documented():
    # the documented threshold is the full-vector agreement score
    assert CRITICAL_MIN_SEMANTIC_CONFIDENCE == 6.0


# --------------------------------------------------------------------------- #
# 10. same prompt/seed -> identical spec / id / world (two full runs)
# --------------------------------------------------------------------------- #


def test_same_prompt_seed_produces_identical_spec_id_world(database_url):
    store_a, _s_a, started_a, _c_a = _publish_unseen(database_url, seed="SESS-A")
    payload_a = _payload(store_a, started_a.case_id)
    spec_a = _spec_for(BRONZE_ICE_PICK_NAME)
    id_a = _pid(_proc_placements(payload_a)[0])
    assert id_a == asset_id_for(spec_a)  # content-addressed, deterministic

    store_b, _s_b, started_b, _c_b = _publish_unseen(database_url, seed="SESS-B")
    payload_b = _payload(store_b, started_b.case_id)
    id_b = _pid(_proc_placements(payload_b)[0])
    assert id_b == id_a
    # draft (world graph + objects + evidence + generated definitions) is
    # byte-identical; only the per-attempt opaque ids differ
    assert payload_a["draft"] == payload_b["draft"]
    assert payload_a["truth"]["crime"] == payload_b["truth"]["crime"]


# --------------------------------------------------------------------------- #
# 11. provider-call budgets remain bounded
# --------------------------------------------------------------------------- #


class _EagerSpecProvider:
    """Responds to ANY requested name with the desk-seal spec (bounded)."""

    def __init__(self):
        self.calls: list = []
        self._inner = FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT)

    def generate(self, request):
        self.calls.append(request)
        content = UNSEEN_SPEC_CONTENT[CARVED_IVORY_DESK_SEAL_NAME.casefold()]
        from app.assets.spec_provider import AssetSpecResponse

        return AssetSpecResponse(content=content)


def test_provider_call_budgets_remain_bounded():
    # (a) cap enforcement: EIGHT different unseen names -> AT MOST the
    #     documented per-generation cap of provider calls (6)
    from app.assets.generated_cache import GeneratedAssetCache

    provider = _EagerSpecProvider()
    requests = tuple(
        ObjectRequest(requested_name=f"carved ivory desk seal {index}")  # all valid unseen
        for index in range(8)
    )
    from app.environments.resolver import resolve_environment

    composition = compose_world(
        WorldRequirements(objects=requests),
        env_resolver=resolve_environment,
        spec_provider=provider,
        evidence_placements=(),
        catalog=_catalog(),
        kit=_kits()["office"],
        cache=GeneratedAssetCache(),
    )
    assert len(provider.calls) <= MAX_SPEC_PROVIDER_CALLS_PER_GENERATION
    assert len(provider.calls) == MAX_SPEC_PROVIDER_CALLS_PER_GENERATION, (
        len(provider.calls),
        "the cap must bite exactly at the documented limit",
    )
    # the budget-exhausted DECORATIVE requests are recorded as player-safe
    # compositionNotes — never a crash, never a blocking issue, never a
    # fabricated substitution (ADV-153)
    assert len(composition.composition_notes) == 2
    assert all(
        "left out" in note for note in composition.composition_notes
    )
    # the two budget-exhausted names were NOT placed into the world
    placed_objects = {p.object_id for p in composition.placements}
    assert not any(
        f"desk seal {index}" in object_id for object_id in placed_objects
        for index in (6, 7)
    )

    # (b) cache-hit -> ZERO provider calls within one oracle instance
    oracle = GeneratedAssetOracle(catalog=_catalog())
    counting = CountingSpecProvider(FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT))
    first = oracle.resolve_or_generate(
        AssetRequest(requested_name=BRONZE_ICE_PICK_NAME),
        spec_provider=counting,
        force_generate=True,
    )
    assert first.generated is not None
    assert counting.call_count == 1
    second = oracle.resolve_or_generate(
        AssetRequest(requested_name=BRONZE_ICE_PICK_NAME),
        spec_provider=counting,
        force_generate=True,
    )
    assert second.generated is not None
    assert second.generated.asset_id == first.generated.asset_id
    assert counting.call_count == 1  # cache hit -> zero additional calls


# --------------------------------------------------------------------------- #
# 12. hostile noun / injection cannot bypass validation
# --------------------------------------------------------------------------- #


def test_hostile_noun_cannot_bypass_validation():
    # "script" / "function" are executable-word tokens: a candidate phrase
    # containing them fails the string-safety gate (never a provider request).
    # NOTE: "hammer" IS a known catalog trigger, so the hostile phrase must
    # not contain a known object word — "razor" is unseen and vocabulary-gated.
    hostile = (
        "The killer used a script blade and a function razor near the body "
        "in the office."
    )
    extracted = extract_world_requirements(hostile)
    assert extracted.objects == ()
    assert any("rejected" in note for note in extracted.unsafe_unsupported)

    url_hostile = (
        "The killer left a https://evil.example/bin near the body in the office."
    )
    extracted_url = extract_world_requirements(url_hostile)
    assert extracted_url.objects == ()

    # hostile prompts can never reach the provider
    counting = _counting_provider()
    _compose(hostile, counting, "office")
    _compose(url_hostile, counting, "office")
    assert counting.call_count == 0
    # and the world stays free of the hostile tokens
    composition = _compose(hostile, counting, "office")
    joined = json.dumps([p.asset_id for p in composition.placements])
    assert "script" not in joined.lower()


# --------------------------------------------------------------------------- #
# 13. CaseTruth never reaches the AssetSpecProvider
# --------------------------------------------------------------------------- #


def test_casetruth_never_reaches_asset_spec_provider():
    # (a) import boundary: the provider/oracle/composer modules never import
    #     app.domain.truth (AST scan — the same enforcement as boundaries)
    import ast

    boundary_files = (
        Path(__file__).resolve().parents[1] / "app" / "assets" / "spec_provider.py",
        Path(__file__).resolve().parents[1] / "app" / "assets" / "oracle.py",
        Path(__file__).resolve().parents[1] / "app" / "world" / "composer.py",
    )
    for path in boundary_files:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(
            name == "app.domain.truth" or name.startswith("app.domain.truth")
            for name in imports
        ), f"{path.name} must never import CaseTruth"

    # (b) value-level: the recorded provider requests carry ONLY the bounded
    #     ObjectRequirement surface — no truth fields, no golden values
    seen: list = []

    class RecordingProvider:
        def generate(self, request):
            seen.append(request)
            from app.assets.spec_provider import AssetSpecResponse

            content = UNSEEN_SPEC_CONTENT.get(request.requested_name.casefold())
            return AssetSpecResponse(content=content)

    _compose(UNSEEN_WEAPON_PROMPT, RecordingProvider(), "office")
    assert seen, "the unseen request must reach the provider"
    for request in seen:
        serialized = " ".join(
            str(part)
            for part in (request.requested_name, request.category_hint, request.tags)
            if part is not None
        )
        for leak in (
            "thomas_reed",
            "sarah_miller",
            "cover_up_embezzlement",
            "kitchen_knife",
            "2026-09-11",
            "22:17",
            "murdererId",
            "victimId",
            "weaponId",
            "crimeTime",
        ):
            assert leak not in serialized
    # provider requests contain exactly the documented bounded fields
    fields = {"requested_name", "category_hint", "tags"}
    for request in seen:
        assert set(vars(request)) == fields, set(vars(request))


# --------------------------------------------------------------------------- #
# 14. AssetSpec never reaches the solver
# --------------------------------------------------------------------------- #


def _to_plain(obj):
    """Plain-JSON-safe projection of dataclass/typed-object graphs (handles
    frozen MappingProxy / frozensets that dataclasses.asdict cannot copy)."""
    import dataclasses

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        return {str(key): _to_plain(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(value) for value in obj]
    if isinstance(obj, (frozenset, set)):
        return sorted(_to_plain(value) for value in obj)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


def test_asset_spec_never_reaches_solver(database_url):
    _SPEC_TOKENS = (
        "canonicalName", "primitive", "material", "dimensions", "hitbox",
        "parts", "transform", "generated_definition", "compilerVersion",
        "schemaVersion", "sourceColor",
    )
    store, service, started, _clock = _publish_unseen(database_url)
    payload = _payload(store, started.case_id)
    public, facts, truth, proof = _solve_payload(payload)
    # solver all_true + golden winners hold (the unseen object is present but
    # the SOLVER facts stay on the catalog knife)
    from app.validation.solution import evaluate_solution

    assert evaluate_solution(proof, truth).all_true is True
    assert proof.weapon.winner == "kitchen_knife"
    # solver-visible inputs (public + evidence) carry NO AssetSpec material
    solver_input_text = json.dumps(
        {
            "public": _to_plain(public),
            "evidence": [_to_plain(fact) for fact in facts],
        },
        sort_keys=True,
    )
    for token in _SPEC_TOKENS:
        assert token not in solver_input_text, token
    # the proc.* identity IS present (geometry reference — allowed), but no
    # generatedDefinition document is
    assert "proc.decor." in solver_input_text
    assert "generatedDefinition" not in solver_input_text


# --------------------------------------------------------------------------- #
# 15. no generated implementation/code reaches the frontend
# --------------------------------------------------------------------------- #


def test_no_generated_implementation_reaches_frontend(database_url):
    from app.services.publication import project_world_objects

    store, service, started, _clock = _publish_unseen(database_url)
    payload = _payload(store, started.case_id)
    world_objects = project_world_objects(payload)
    (ice_pick,) = [item for item in world_objects if item["assetId"].startswith("proc.")]
    generated = ice_pick["generated"]
    # declarative-only: the exact definition key surface
    assert set(generated) == {
        "compilerVersion", "schemaVersion", "assetId", "canonicalName",
        "category", "subtype", "dimensions", "parts", "hitbox",
    }
    for part in generated["parts"]:
        assert set(part) == {
            "id", "role", "primitive", "transform", "color", "parentId",
        }
        assert part["parentId"] is None  # the ice pick defines no parents
    # the backend projection gate (the strict validator the frontend shares)
    # accepts the block for the CURRENT schema/compiler versions
    assert validate_embedded_definition(ice_pick["assetId"], generated) is not None
    # data scan: no executable token / URL / path / handler in ANY generated
    # field or key, and every numeric is finite and bounded
    text = json.dumps(generated)
    for marker in (
        "<script", "eval(", "new Function", "javascript:", "data:",
        "file://", "http://", "https://", "onclick=", "onload=", "onerror=",
        "import(", "exec(", "shader", "../", "..\\",
    ):
        assert marker not in text, marker
    for part in generated["parts"]:
        transform = part["transform"]
        for axis in ("x", "y", "z"):
            for vec in ("position", "rotation", "scale"):
                value = transform[vec][axis]
                assert isinstance(value, (int, float)) and math.isfinite(value)


# --------------------------------------------------------------------------- #
# API verification (required Verification 4): POST /cases -> PUBLISHED,
# bootstrap shows the proc.* object (generated block) + evidence clickability,
# byte-identical repeat runs, restart persistence.
# --------------------------------------------------------------------------- #


def _unseen_app(database_url):
    """Migrated FastAPI app whose generation service uses the unseen provider."""
    from app.assets.generated_cache import GeneratedAssetCache
    from app.assets.spec_provider import FakeAssetSpecProvider as _Fake
    from app.core.config import Settings
    from app.main import create_app

    from app.services.generation import GenerationService
    from conftest import DEFAULT_CORS, upgrade_db

    upgrade_db(database_url)
    application = create_app(
        Settings(database_url=database_url, cors_allowed_origins=DEFAULT_CORS)
    )
    application.state.generation_service = GenerationService(
        settings=application.state.settings,
        store=application.state.store,
        clock=application.state.clock,
        publication=application.state.publication_service,
        spec_provider=_Fake(UNSEEN_SPEC_CONTENT),
        generated_cache=GeneratedAssetCache(),
    )
    return application


def _api_publish(client, prompt=UNSEEN_WEAPON_PROMPT):
    token = client.post("/api/v1/sessions/anonymous").json()["anonymousSessionToken"]
    res = client.post(
        "/api/v1/cases",
        json={"prompt": prompt},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _api_bootstrap(client, created):
    from phase5_helpers import create_playthrough

    status, body = create_playthrough(
        client, created["creatorAccessToken"], created["caseId"], 1
    )
    assert status == 201, body
    res = client.get(
        f"/api/v1/playthroughs/{body['playthroughId']}/investigation",
        headers={"Authorization": f"Bearer {body['playthroughAccessToken']}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_api_unseen_weapon_publishes_bootstrap_clickable_and_survives_restart(database_url):
    from fastapi.testclient import TestClient

    # ---- RUN 1 ----
    app1 = _unseen_app(database_url)
    with TestClient(app1) as client1:
        created1 = _api_publish(client1)
        assert created1["status"] == "PUBLISHED", created1
        boot1 = _api_bootstrap(client1, created1)
        ice_pick = next(
            item for item in boot1["scene"]["worldObjects"]
            if item["assetId"].startswith("proc.")
        )
        # bootstrap: generated block + interaction present; PD-SEC-01 — the
        # evidenceId of an UNDISCOVERED record is NOT exposed pre-reveal.
        assert ice_pick["objectId"] == "bronze_ceremonial_ice_pick"
        assert ice_pick["generated"] is not None
        assert ice_pick["generated"]["hitbox"] is not None
        assert ice_pick["interaction"] in INTERACTION_ALLOWLIST
        assert ice_pick["evidenceId"] is None
        # the public case DTO exposes the same object
        dto = client1.get(
            f"/api/v1/cases/{created1['caseId']}?version=1",
            headers={"Authorization": f"Bearer {created1['creatorAccessToken']}"},
        )
        assert dto.status_code == 200
        dto_ice = next(
            o for o in dto.json()["objects"]
            if o["assetId"].startswith("proc.")
        )
        assert dto_ice["objectId"] == "bronze_ceremonial_ice_pick"
    app1.state.engine.dispose()
    app1.state.store.dispose()

    # ---- RUN 2 (byte-identical repeat) ----
    app2 = _unseen_app(database_url)
    with TestClient(app2) as client2:
        created2 = _api_publish(client2)
        assert created2["status"] == "PUBLISHED", created2
        boot2 = _api_bootstrap(client2, created2)
        ice_pick2 = next(
            item for item in boot2["scene"]["worldObjects"]
            if item["assetId"].startswith("proc.")
        )
        assert ice_pick2["assetId"] == ice_pick["assetId"]  # content-addressed id
        assert ice_pick2["generated"] == ice_pick["generated"]  # identical definition
        # restart persistence: the SAME DB still serves the run-1 case with the
        # same generated object after a fresh app over the same file
        reopened = client2.get(
            f"/api/v1/cases/{created1['caseId']}?version=1",
            headers={"Authorization": f"Bearer {created1['creatorAccessToken']}"},
        )
        assert reopened.status_code == 200, reopened.text
        reopened_ice = next(
            o for o in reopened.json()["objects"]
            if o["assetId"].startswith("proc.")
        )
        assert reopened_ice["assetId"] == ice_pick["assetId"]
        assert reopened.json()["caseId"] == created1["caseId"]
    app2.state.engine.dispose()
    app2.state.store.dispose()


def test_unseen_weapon_evidence_discoverable_without_breaking_solver(database_url):
    from app.services.publication import project_discovery, project_world_objects

    store, service, started, _clock = _publish_unseen(database_url)
    assert started.status == "PUBLISHED", started
    payload = _payload(store, started.case_id)
    # the published world contains the unseen proc.* weapon
    ice_pick_placements = _proc_placements(payload)
    assert ice_pick_placements
    placement = ice_pick_placements[0]
    assert placement.get("evidenceId") or placement.get("evidence_id") == "bronze_ceremonial_ice_pick_fp_01"
    assert placement.get("interaction") == "inspect"
    # the evidence fact exists and labels the unseen object
    evidence_ids = {fact["id"] for fact in payload["draft"]["evidence"]}
    assert "bronze_ceremonial_ice_pick_fp_01" in evidence_ids
    # discoverable + clickable in the bootstrap projection (PD-SEC-01: the
    # evidenceId appears once the record is DISCOVERED — it is then
    # player-known)
    world_objects = project_world_objects(
        payload, discovered={"bronze_ceremonial_ice_pick_fp_01"}
    )
    ice_pick = next(item for item in world_objects if item["assetId"].startswith("proc."))
    assert ice_pick["interaction"] == "inspect"
    assert ice_pick["evidenceId"] == "bronze_ceremonial_ice_pick_fp_01"
    assert ice_pick["generated"] is not None
    discovery = project_discovery(payload, "bronze_ceremonial_ice_pick_fp_01")
    assert discovery is not None and discovery["interaction"] == "inspect"
    # solver uniqueness is NOT broken: golden winner + three-wealth universe
    public, facts, truth, proof = _solve_payload(payload)
    from app.validation.solution import evaluate_solution

    assert proof.weapon.winner == "kitchen_knife"
    assert tuple(sorted(proof.weapon.universe)) == (
        "kitchen_knife",
        "letter_opener",
        "scissors",
    )
    assert evaluate_solution(proof, truth).all_true is True
    # the ice pick never entered the candidate universe
    assert "bronze_ceremonial_ice_pick" not in proof.weapon.universe


# --------------------------------------------------------------------------- #
# ADV-153 — a decorative unseen noun no longer discards the whole composition:
# the RESOLVED objects stay, the unresolved decorative object is left out with
# a player-safe bounded composition note (stored + surfaced as the browser
# seam), while REQUIRED unresolved items keep the fail-safe semantics.
# --------------------------------------------------------------------------- #

DECORATIVE_NOTE_PROMPT = (
    "A claw hammer and a box are on the floor in the apartment."
)
DECORATIVE_NOTE_TEXT = (
    "the 'box' you described is not currently available - it was left out"
)


def _publish_decorative_note(database_url, seed="SESS-NOTE"):
    from app.persistence.timebase import EpochClock

    from phase5_helpers import seed_session

    store, service = _service_ctx(database_url)  # default builtin provider
    clock = EpochClock()
    seed_session(store, seed, clock)
    started = service.start_case_generation(
        DECORATIVE_NOTE_PROMPT, anonymous_quota_session_id=seed
    )
    return store, service, started


def test_decorative_noun_prompt_publishes_with_resolved_objects_and_note(database_url):
    store, _service, started = _publish_decorative_note(database_url)
    assert started.status == "PUBLISHED", started
    payload = _payload(store, started.case_id)
    placements = payload["draft"]["world_graph"]["placements"]
    placed_ids = {p.get("object_id") or p.get("object_id") for p in placements}
    # the hammer that resolved fine is PRESENT — never discarded with the box
    assert "claw_hammer" in placed_ids
    assert "box" not in placed_ids
    # the player-safe bounded note is STORED with the published draft
    notes = tuple(payload["draft"].get("composition_notes") or ())
    assert notes
    assert notes[0] == DECORATIVE_NOTE_TEXT
    assert len(notes) <= 3
    # the note is surfaced on the PUBLIC DTO (the browser seam)
    from app.services.publication import public_case_dict_from_payload

    dto = public_case_dict_from_payload(payload)
    assert dto["compositionNotes"] == list(notes)


def test_composition_note_sanitized_and_absent_from_solver_input(database_url):
    store, _service, started = _publish_decorative_note(database_url)
    assert started.status == "PUBLISHED", started
    payload = _payload(store, started.case_id)
    notes = tuple(payload["draft"].get("composition_notes") or ())
    assert notes
    note = notes[0]
    # sanitized: bounded length, no executable/URL/path tokens
    assert len(note) <= 120
    for marker in (
        "http://", "https://", "javascript:", "file://", "data:",
        "<script", "eval(", "onload=", "onclick=", "/", "\\", "..",
        "Traceback",
    ):
        assert marker not in note, marker
    # the note is NEVER solver-visible (public + evidence stay clean)
    public, facts, truth, proof = _solve_payload(payload)
    solver_input = json.dumps(
        {
            "public": _to_plain(public),
            "evidence": [_to_plain(fact) for fact in facts],
        },
        sort_keys=True,
    )
    assert note not in solver_input
    assert "compositionNotes" not in solver_input
    assert "composition_notes" not in solver_input


def test_decorative_only_unresolved_publishes_and_required_still_fails(database_url):
    # (a) decorative-only unresolved -> PUBLISHED (no FAILED)
    store, _service, started = _publish_decorative_note(database_url)
    assert started.status == "PUBLISHED", started
    assert store.get_published(started.case_id, 1) is not None
    store.dispose()  # release the file before the second service (fresh write)
    # (b) a REQUIRED unresolved object with a failing provider -> FAILED,
    #     never published, never a substituted tape asset (unchanged)
    from app.assets.spec_provider import FakeAssetSpecProvider as _Fake
    from app.persistence.timebase import EpochClock

    from phase5_helpers import seed_session

    store2, service2 = _service_ctx(database_url, spec_provider=_Fake({}))
    clock = EpochClock()
    seed_session(store2, "SESS-REQ", clock)
    failed = service2.start_case_generation(
        UNSEEN_WEAPON_PROMPT, anonymous_quota_session_id="SESS-REQ"
    )
    assert failed.status == "FAILED", failed
    assert store2.get_published(failed.case_id, 1) is None