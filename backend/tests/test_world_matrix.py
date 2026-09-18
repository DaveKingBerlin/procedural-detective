"""Phase 15 — Showcase Matrix diversity, demo-honesty and asset-quality gate.

Backend/test half of Phase 15 Track A:

1. **Fixture contract** — the ``world_matrix`` fixture carries TEN deterministic
   prompts (2 per kit) whose extraction matches the pinned expectations,
   including the "at least one non-golden/unseen object via the procedural path
   in MOST rows" diversity requirement (7/10 rows are procedural).
2. **Demo-mode honesty** — the DEV/demo provider produces byte-visible
   DIFFERENT published worlds for the 10 matrix prompts: distinct
   environmentId + world-graph object sets; same-kit pairs also differ (the
   "fake provider does not pretend to honor arbitrary prompts while returning
   the same case" proof, Phase15).
3. **Metrics harness** — ``tools/showcase_matrix.py --out <tmp>/matrix.json``
   exits 0 and emits the versioned JSON report (``demoMode`` /
   ``providerHonesty`` markers, per-row metrics, summary assertions, quality
   gate).
4. **Asset-resolution quality gate** — no cross-class semantic winners
   ("ice pick" never resolves to a kitchen knife via semantic), fallback usage
   is rare and explicit (0 required-object FALLBACKs across the matrix),
   procedural objects stay recognizable (multi-part, distinct silhouette
   signatures for the unseen trio), and no two critical evidence classes
   collapse to identical geometry.
5. **ADV-151** — hostile CLI invocations (``--tmp`` pointing at an existing
   FILE; a missing/import-failing ``fixtures.world_matrix``; a tampered
   unknown matrix row id) exit 2 with a sanitized one-line stderr message and
   NEVER a traceback (the catalog_report DEF-064 pattern); the normal run
   still exits 0.

Deterministic, offline, in-process (scratch SQLite under tmp_path; zero network).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from app.assets.compiler import HITBOX_MAX, HITBOX_MIN  # noqa: E402
from app.assets.catalog import load_catalog_from_repo  # noqa: E402
from app.assets.oracle import resolve_or_generate  # noqa: E402
from app.assets.resolver import AssetRequest, Provenance  # noqa: E402
from app.assets.specs import parse_asset_spec  # noqa: E402
from app.environments.placer import EVIDENCE_CAPABLE_TYPES  # noqa: E402
from app.generation.pipeline import normalize_prompt  # noqa: E402
from app.world.composer import (  # noqa: E402
    CRITICAL_MIN_SEMANTIC_CONFIDENCE,
    _KNOWN_PROCEDURAL_SPECS,
)
from app.world.extract import extract_world_requirements  # noqa: E402
from app.world.requirements import (  # noqa: E402
    CRITICALITY_REQUIRED,
    ObjectRequest,
    WorldRequirements,
)

from fixtures.asset_specs_unseen import (  # noqa: E402
    UNSEEN_SPEC_CONTENT,
    UNSEEN_SPEC_NAMES,
)
from fixtures.world_matrix import (  # noqa: E402
    MATRIX_EXPECTED,
    MATRIX_KITS,
    MATRIX_ORDER,
    MATRIX_PROMPTS,
    MATRIX_VERSION,
    matrix_requirements,
)
from phase5_helpers import seed_session  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# shared service helper (the DEV demo provider: builtin fake case + builtin
# procedural spec provider — the app's Production dev-mode defaults)
# --------------------------------------------------------------------------- #


def _service_ctx(url):
    from app.persistence.store import Store

    from app.services.generation import GenerationService
    from conftest import upgrade_db
    from phase5_helpers import _phase5_settings_for

    upgrade_db(url)
    store = Store(url)
    settings = _phase5_settings_for(url)
    service = GenerationService(settings=settings, store=store)
    return store, service


def _publish_all_rows(store, service, seed_prefix="SESS-"):
    from app.persistence.timebase import EpochClock

    clock = EpochClock()
    payloads = {}
    started_by_row = {}
    for index, row_id in enumerate(MATRIX_ORDER):
        session_id = f"{seed_prefix}{index}"
        seed_session(store, session_id, clock)
        started = service.start_case_generation(
            MATRIX_PROMPTS[row_id], anonymous_quota_session_id=session_id
        )
        started_by_row[row_id] = started
        assert started.status == "PUBLISHED", (row_id, started)
        payloads[row_id] = json.loads(
            store.get_published(started.case_id, 1).payload_json
        )
    return payloads, started_by_row


def _solver(payload):
    from app.domain.solver import solve_case
    from app.generation.pipeline import _draft_to_phase3
    from app.validation.solution import evaluate_solution

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
    draft = GeneratedDraft(
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
                         discoverable=e.get("discoverable"),
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
    public, facts, truth = _draft_to_phase3(draft, case_id="CASE-X", title="T")
    proof = solve_case(public, facts)
    return proof, evaluate_solution(proof, truth)


def _world_material(payload):
    """Byte-comparable world identity: (environmentId, object ids, placements)."""
    draft = payload["draft"]
    placements = tuple(
        sorted(
            (
                p.get("object_id"),
                p.get("asset_id"),
                p.get("anchor"),
                p.get("interaction"),
                p.get("evidence_id"),
                (p.get("generated_definition") or {}).get("assetId"),
            )
            for p in draft["world_graph"]["placements"]
        )
    )
    return (draft["scene"].get("environment_id"), placements)


# --------------------------------------------------------------------------- #
# 1. fixture contract: 10 prompts, 2 per kit, pinned extraction, determinism
# --------------------------------------------------------------------------- #


def test_matrix_has_ten_prompts_two_per_kit():
    assert set(MATRIX_ORDER) == set(MATRIX_PROMPTS) == set(MATRIX_EXPECTED)
    assert len(MATRIX_ORDER) == 10
    assert len(MATRIX_PROMPTS) == 10
    assert MATRIX_VERSION == 1
    assert len(set(MATRIX_EXPECTED[row].kit for row in MATRIX_ORDER)) == 5
    for kit in MATRIX_KITS:
        rows = [r for r in MATRIX_ORDER if MATRIX_EXPECTED[r].kit == kit]
        assert len(rows) == 2, kit


@pytest.mark.parametrize("row_id", MATRIX_ORDER)
def test_matrix_extraction_matches_pinned_expectations(row_id):
    from app.domain.time_interval import (
        parse_iso8601_to_epoch,
        parse_locked_time_to_epoch,
    )
    from app.generation.constraints import normalize_identity, normalize_motive_text

    locked, _note = normalize_prompt(MATRIX_PROMPTS[row_id], max_chars=4000)
    extracted = extract_world_requirements(MATRIX_PROMPTS[row_id], locked)
    expectation = MATRIX_EXPECTED[row_id]
    assert extracted.environment_hint == expectation.environment_hint, row_id
    names = tuple(o.requested_name for o in extracted.objects)
    assert set(names) == set(expectation.expected_object_names), (row_id, names)
    assert extracted.unsafe_unsupported == ()
    # the six lock fields are all present and golden-equivalent (frozen truth)
    assert locked.victim and locked.murderer and locked.motive
    assert locked.weapon and locked.crime_time and locked.witness
    assert normalize_identity(locked.victim) == "sarahmiller"
    assert normalize_identity(locked.murderer) == "thomasreed"
    assert normalize_identity(locked.weapon) == "kitchenknife"
    assert normalize_identity(locked.witness) == "emilyreed"
    needle = normalize_motive_text(locked.motive)
    assert len(needle) >= 4
    golden_motive = "coverupthe\u20ac240000embezzlement"
    assert needle in golden_motive or golden_motive in needle
    canonical = "2026-09-11T22:17:00+02:00"
    assert parse_locked_time_to_epoch(locked.crime_time, canonical) == (
        parse_iso8601_to_epoch(canonical)
    )


@pytest.mark.parametrize("row_id", MATRIX_ORDER)
def test_matrix_extraction_is_deterministic(row_id):
    first = matrix_requirements(row_id)
    second = matrix_requirements(row_id)
    assert first == second
    assert repr(first) == repr(second)


def test_matrix_relation_targets_match_extraction():
    for row_id, expectation in MATRIX_EXPECTED.items():
        extracted = matrix_requirements(row_id)
        for kind, targets in expectation.relation_targets.items():
            got = {r.target for r in extracted.relations if r.kind == kind}
            assert got == set(targets), (row_id, kind, got)


def test_procedural_rows_cover_more_than_half_the_matrix():
    procedural_rows = [
        row_id
        for row_id, exp in MATRIX_EXPECTED.items()
        if exp.proc_assets_expected
    ]
    assert len(procedural_rows) >= 6  # "MOST rows" (7 of 10 in practice)
    assert len(procedural_rows) == 7


# --------------------------------------------------------------------------- #
# 2. demo-mode honesty: the DEV provider publishes 10 DISTINCT worlds
# --------------------------------------------------------------------------- #


def test_matrix_prompts_publish_distinct_worlds_through_dev_provider(database_url):
    store, service = _service_ctx(database_url)
    try:
        payloads, started = _publish_all_rows(store, service)
    finally:
        store.dispose()
    for row_id in MATRIX_ORDER:
        assert started[row_id].status == "PUBLISHED", row_id

    materials = {row_id: _world_material(payload) for row_id, payload in payloads.items()}
    # every combination is BYTE-distinct (environmentId + placement identity)
    assert len(set(materials.values())) == 10
    # environmentId spans the five documented kits
    assert {m[0] for m in materials.values()} == set(MATRIX_KITS)
    # distinct object sets exist; no two prompts collapse to one world graph
    object_sets = {
        row_id: tuple(sorted(p["object_id"] for p in payload["draft"]["world_graph"]["placements"]))
        for row_id, payload in payloads.items()
    }
    assert len({object_sets[r] for r in object_sets}) == 10
    # same-kit pairs ALSO differ (the anti-collapse proof inside one kit)
    for kit in MATRIX_KITS:
        rows = [r for r in MATRIX_ORDER if MATRIX_EXPECTED[r].kit == kit]
        assert rows[0] in object_sets and rows[1] in object_sets
        assert object_sets[rows[0]] != object_sets[rows[1]], kit

    # expected prompt-specific objects actually entered the world
    for row_id, expectation in MATRIX_EXPECTED.items():
        object_ids = set(object_sets[row_id])
        for prompt_id in expectation.prompt_object_ids:
            assert prompt_id in object_ids, (row_id, prompt_id)
        assets = {
            p["asset_id"]
            for p in payloads[row_id]["draft"]["world_graph"]["placements"]
        }
        for required in expectation.required_in_world:
            assert required in assets, (row_id, required)
        if expectation.proc_assets_expected:
            assert any(a.startswith("proc.") for a in assets), row_id
        else:
            assert not any(a.startswith("proc.") for a in assets), row_id

    # solver: the FROZEN golden truth holds on every row (all_true + unique)
    for row_id, payload in payloads.items():
        proof, verdict = _solver(payload)
        assert proof.who.winner == "thomas_reed"
        assert proof.why.winner == "cover_up_embezzlement"
        assert proof.weapon.winner == "kitchen_knife"
        assert proof.who.unique and proof.why.unique and proof.weapon.unique
        assert verdict.all_true is True, row_id


def test_same_kit_prompts_differ_byte_visibly(database_url):
    """THE demo-honesty driver: apartment_a and apartment_b are two different
    published worlds from the SAME provider — not cosmetic reskins of one case."""
    store, service = _service_ctx(database_url)
    try:
        payloads, _started = _publish_all_rows(store, service)
    finally:
        store.dispose()
    a = payloads["apartment_a"]
    b = payloads["apartment_b"]
    assert a["draft"]["scene"]["environment_id"] == "apartment"
    assert b["draft"]["scene"]["environment_id"] == "apartment"
    # the frozen truth NEVER changes — only the world does
    assert a["truth"]["crime"] == b["truth"]["crime"]

    def _placement_keys(payload):
        return {
            (p["object_id"], p["asset_id"], p["interaction"], p.get("evidence_id"))
            for p in payload["draft"]["world_graph"]["placements"]
        }

    keys_a = _placement_keys(a)
    keys_b = _placement_keys(b)
    assert keys_a != keys_b
    # concrete byte-visible diff: the desk award row has the proc.* award + rope;
    # the hammer row has the catalog hammer + watch — different provenances too.
    assert ("distinctive_desk_award", "proc.decor.4d402d9108486a56", "", None) in keys_b
    assert ("rope", "PROP_ROPE_01", "", None) in keys_b
    assert ("claw_hammer", "PROP_HAMMER_01", "", None) in keys_a
    assert ("wristwatch", "PROP_WATCH_01", "", None) in keys_a
    sort_key = lambda p: (p["object_id"], p["asset_id"], p["anchor"])  # noqa: E731
    assert sorted(a["draft"]["world_graph"]["placements"], key=sort_key) != sorted(
        b["draft"]["world_graph"]["placements"], key=sort_key
    )


def test_harness_report_demo_honesty_markers(tmp_path):
    """The harness report documents demoMode + providerHonesty explicitly."""
    report = _run_harness(tmp_path)
    assert report["demoMode"] == "deterministic_showcase"
    assert report["providerHonesty"] == "distinct_worlds_per_prompt"
    assert report["summary"]["distinctWorldGraphs"] == 10
    assert report["summary"]["collisions"] == []


# --------------------------------------------------------------------------- #
# 3. metrics harness
# --------------------------------------------------------------------------- #


def _run_harness(tmp_path, out_name="matrix.json"):
    import tools.showcase_matrix as harness

    out = tmp_path / out_name
    scratch = tmp_path / "scratch"
    rc = harness.main(["--out", str(out), "--tmp", str(scratch)])
    assert rc == 0, rc
    assert out.exists()
    return json.loads(out.read_text(encoding="utf-8"))


def test_harness_report_contains_all_phase15_metrics(tmp_path):
    report = _run_harness(tmp_path)
    rows = report["rows"]
    assert len(rows) == 10
    for row in rows:
        assert set(row) >= {
            "rowId",
            "generationSuccess",
            "validationRepairCount",
            "environmentCorrect",
            "assetResolutionProvenance",
            "unresolvedOrFallbackAssetCount",
            "evidenceReachable",
            "sceneReadyEstimateMs",
            "solver",
            "solverUniqueResolved",
            "worldGraphSha256",
        }
        assert row["generationSuccess"] is True
        assert isinstance(row["validationRepairCount"], int)
        assert isinstance(row["validationRepairSteps"], list)
        assert row["environmentCorrect"] is True
        assert row["unresolvedOrFallbackAssetCount"] == 0
        assert row["evidenceReachableAll"] is True
        assert row["sceneReadyEstimateMs"] > 0
        assert row["solverUniqueResolved"] is True
        assert row["worldGraphRecomputedIdentical"] is True
        # evidence reachability tri-state
        for item in row["evidenceReachable"]:
            assert item["interaction"]
            assert item["evidenceInPayload"]
            assert item["evidenceCapable"]

    s = report["summary"]
    assert s["generationSuccessRate"] == 1.0
    assert s["totalScenes"] == 10
    assert s["environmentCorrectCount"] == 10
    assert s["solutionAllTrueCount"] == 10
    assert s["solverUniqueResolvedCount"] == 10
    assert s["distinctEnvironments"] == 5
    assert s["distinctObjectSets"] == 10
    assert s["distinctWorldGraphs"] == 10
    assert s["collisions"] == []
    assert s["requiredObjectFallbackCountTotal"] == 0
    assert s["unresolvedOrFallbackAssetCountTotal"] == 0
    assert all(s["assertions"].values())
    assert report["qualityGate"]["pass"] is True
    assert all(report["qualityGate"]["assertions"].values())
    for prov in s["provenanceDistribution"]:
        assert prov in {
            "CATALOG_EXACT",
            "CATALOG_ALIAS",
            "SEMANTIC_MATCH",
            "PARAMETRIC_VARIANT",
            "PROCEDURAL_GENERATED",
            "FALLBACK",
        }


def test_harness_rerun_is_byte_deterministic(tmp_path):
    from tools.showcase_matrix import build_report

    first = json.dumps(build_report(tmp_path / "scratch1"), sort_keys=True)
    second = json.dumps(build_report(tmp_path / "scratch2"), sort_keys=True)
    assert first == second


# --------------------------------------------------------------------------- #
# 4. asset-resolution quality gate
# --------------------------------------------------------------------------- #


# -- 4a. cross-class semantic: "ice pick" never resolves to a kitchen knife ----


@pytest.mark.parametrize(
    "requested_name,hints,cross_class_asset",
    [
        ("ice pick", {"tags": ("weapon",)}, "PROP_KITCHEN_KNIFE_01"),
        ("stiletto", {"tags": ("weapon", "sharp")}, "PROP_KITCHEN_KNIFE_01"),
        ("tire iron", {"tags": ("tool", "blunt")}, "PROP_WRENCH_01"),
    ],
)
def test_semantic_cross_class_never_wins(requested_name, hints, cross_class_asset):
    outcome = resolve_or_generate(AssetRequest(requested_name=requested_name, **hints))
    resolution = outcome.resolution
    # an ambiguous tie is fine (never an arbitrary cross-class winner)
    assert resolution is not None
    assert resolution.asset_id != cross_class_asset, (
        requested_name,
        resolution.asset_id,
    )
    # the REQUIRED composer path escalates lossy semantics to the provider
    # instead of substituting (Phase 14_5): a REQUIRED ice pick becomes the
    # real procedural object, never a knife.
    from app.assets.generated_cache import GeneratedAssetCache
    from app.assets.spec_provider import FakeAssetSpecProvider
    from app.environments.manifests import load_all_environments
    from app.environments.resolver import resolve_environment
    from app.world.composer import compose_world

    provider = FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT)
    request = ObjectRequest(
        requested_name="bronze ceremonial ice pick",
        tags=("weapon",),
        criticality=CRITICALITY_REQUIRED,
    )
    kit = next(k for k in load_all_environments() if k.environment_id == "office")
    composition = compose_world(
        WorldRequirements(objects=(request,)),
        env_resolver=resolve_environment,
        spec_provider=provider,
        evidence_placements=(),
        catalog=load_catalog_from_repo(),
        kit=kit,
        cache=GeneratedAssetCache(),
    )
    assert composition.issues == ()
    record = composition.resolution_record["resolved"]["bronze ceremonial ice pick"]
    assert record["assetId"].startswith("proc.decor.")
    assert record["provenance"] == Provenance.PROCEDURAL_GENERATED.value
    knife_placements = [
        p for p in composition.placements if p.asset_id == "PROP_KITCHEN_KNIFE_01"
    ]
    assert len(knife_placements) == 1  # the golden base knife, never a substitute


def test_critical_min_semantic_confidence_is_the_documented_full_vector():
    # the composer's REQUIRED-semantic threshold = tag+category+subtype vector
    assert CRITICAL_MIN_SEMANTIC_CONFIDENCE == 6.0


# -- 4b. fallback rarity across the matrix ------------------------------------


def test_zero_fallback_assets_across_the_matrix(database_url):
    """Required-object FALLBACK usage across the matrix is 0 (documented
    ceiling). Every placed object resolves through EXACT/ALIAS/SEMANTIC/
    PARAMETRIC or the PROCEDURAL path — never the neutral fallback."""
    store, service = _service_ctx(database_url)
    try:
        payloads, _started = _publish_all_rows(store, service)
    finally:
        store.dispose()
    catalog = load_catalog_from_repo()
    total_fallback = 0
    for row_id, payload in payloads.items():
        for placement in payload["draft"]["world_graph"]["placements"]:
            asset_id = placement.get("asset_id")
            assert asset_id != "PROP_FALLBACK_01", (row_id, asset_id)
            if asset_id.startswith("proc."):
                continue
            assert asset_id in catalog.by_id, (row_id, asset_id)
        # every requested object in the matrix resolved to a real asset
        expected = MATRIX_EXPECTED[row_id]
        assets = {p.get("asset_id") for p in payload["draft"]["world_graph"]["placements"]}
        for required in expected.required_in_world:
            assert required in assets, (row_id, required)
        # the neutral fallback descriptor never appears in any world material
        joined = json.dumps(payload["draft"]["world_graph"])
        assert "PROP_FALLBACK_01" not in joined
        assert "PROP_FALLBACK_02" not in joined
    assert total_fallback == 0


def test_provenance_distribution_is_all_exact_or_procedural(database_url):
    """The composition provenance used by the harness report only ever uses the
    documented exact/procedural paths for the matrix (never SEMANTIC lossy, never
    FALLBACK). Base kit objects are CATALOG_EXACT; the builtin procedural
    showcases are PROCEDURAL_GENERATED."""
    from app.assets.catalog import load_catalog_from_repo
    from app.assets.generated_cache import GeneratedAssetCache
    from app.world.composer import KnownObjectSpecProvider, compose_world

    store, service = _service_ctx(database_url)
    try:
        payloads, _started = _publish_all_rows(store, service)
    finally:
        store.dispose()
    catalog = load_catalog_from_repo()
    provenance_seen: set[str] = set()
    for row_id, payload in payloads.items():
        env_id = payload["draft"]["scene"]["environment_id"]
        locked, _note = normalize_prompt(MATRIX_PROMPTS[row_id], max_chars=4000)
        world_reqs = extract_world_requirements(MATRIX_PROMPTS[row_id], locked)
        from app.environments.manifests import load_environment

        composition = compose_world(
            world_reqs,
            env_resolver=None,
            spec_provider=KnownObjectSpecProvider(),
            evidence_placements=(),
            catalog=catalog,
            kit=load_environment(env_id),
            cache=GeneratedAssetCache(),
        )
        assert composition.issues == (), row_id
        provenance_seen.update(composition.provenance_by_object_id.values())
    assert provenance_seen <= {
        "CATALOG_EXACT",
        "CATALOG_ALIAS",
        "SEMANTIC_MATCH",
        "PARAMETRIC_VARIANT",
        "PROCEDURAL_GENERATED",
    }
    # the matrix exercises both living paths today
    assert "CATALOG_EXACT" in provenance_seen
    assert "PROCEDURAL_GENERATED" in provenance_seen


# -- 4c. procedural objects stay recognizable (multi-part, distinct shape) -----


def _compiled(asset_id, content):
    from app.assets.compiler import compile_asset_spec

    return compile_asset_spec(parse_asset_spec(content, non_throwing=False))


def _signature(definition):
    hitbox = definition.hitbox
    return (
        round(float(hitbox.scale.x), 4),
        round(float(hitbox.scale.y), 4),
        round(float(hitbox.scale.z), 4),
        len(definition.parts),
        tuple(sorted(part.primitive for part in definition.parts)),
    )


def test_unseen_trio_is_multi_part_and_silhouette_distinct():
    """Procedurally generated objects must be recognizable: every unseen example
    is MULTI-PART (>= 3 parts) and the trio's silhouette signatures (hitbox
    extents + part composition) are pairwise distinct."""
    definitions = {
        name: _compiled("ignored", UNSEEN_SPEC_CONTENT[name.casefold().strip()])
        for name in UNSEEN_SPEC_NAMES
    }
    signatures = {name: _signature(defn) for name, defn in definitions.items()}
    for name, definition in definitions.items():
        assert len(definition.parts) >= 3, name
        assert all(
            HITBOX_MIN <= v <= HITBOX_MAX
            for v in (
                definition.hitbox.scale.x,
                definition.hitbox.scale.y,
                definition.hitbox.scale.z,
            )
        )
        # every rendered primitive is one of the four renderer primitives
        assert {part.primitive for part in definition.parts} <= {
            "box",
            "cylinder",
            "sphere",
            "plane",
        }
    assert len({signatures[name] for name in signatures}) == 3


def test_builtin_procedural_specs_stay_distinct_from_the_unseen_trio():
    builtin = {
        "custom trophy": _KNOWN_PROCEDURAL_SPECS["custom trophy"],
        "antique ceremonial letter opener": _KNOWN_PROCEDURAL_SPECS[
            "antique ceremonial letter opener"
        ],
        "unusual laboratory sample rack": _KNOWN_PROCEDURAL_SPECS[
            "unusual laboratory sample rack"
        ],
        "distinctive desk award": _KNOWN_PROCEDURAL_SPECS["distinctive desk award"],
    }
    all_definitions = {
        canonical: _compiled("ignored", content)
        for canonical, content in {**builtin, **UNSEEN_SPEC_CONTENT}.items()
    }
    signatures = {
        canonical: _signature(defn)
        for canonical, defn in all_definitions.items()
    }
    assert len({v for v in signatures.values()}) == len(signatures)
    # the pin: the compiled asset ids equal the fixture's pinned ids
    pins = {
        "proc.decor.77ff70dc4f50b4f9": "antique ceremonial letter opener",
        "proc.decor.4d402d9108486a56": "distinctive desk award",
        "proc.decor.1d169cf74462fdf4": "custom trophy",
        "proc.utility.41bd92f58c068c25": "unusual laboratory sample rack",
    }
    for asset_id, canonical in pins.items():
        assert all_definitions[canonical].asset_id == asset_id, canonical


# -- 4d. no two critical evidence classes collapse to identical geometry --------


def test_critical_evidence_classes_have_distinct_geometry_signatures():
    """The knife / letter opener / scissors / wrench / hammer / bottle / rope /
    watch / jewelry box / medication classes must stay pairwise geometry-distinct
    (the "no collapse to identical generic geometry" gate)."""
    catalog = load_catalog_from_repo()
    classes = (
        "PROP_KITCHEN_KNIFE_01",
        "PROP_LETTER_OPENER_01",
        "PROP_SCISSORS_01",
        "PROP_HAMMER_01",
        "PROP_WRENCH_01",
        "PROP_GLASS_BOTTLE_01",
        "PROP_ROPE_01",
        "PROP_WATCH_01",
        "PROP_JEWELRY_BOX_01",
        "PROP_MEDICATION_BOTTLE_01",
    )
    signatures = []
    for asset_id in classes:
        asset = catalog.by_id[asset_id]
        dims = asset.dimensions
        signatures.append(
            (
                round(float(dims.x), 4),
                round(float(dims.y), 4),
                round(float(dims.z), 4),
            )
        )
    assert len(set(signatures)) == len(classes)
    # and the golden requested-name surface resolves to the SAME distinct ids
    resolved_ids = {
        catalog_resolved_asset_id(catalog, name)
        for name in (
            "kitchen knife",
            "letter opener",
            "scissors",
            "claw hammer",
            "adjustable wrench",
            "glass bottle",
            "rope",
            "wristwatch",
            "jewelry box",
            "medication bottle",
        )
    }
    assert len(resolved_ids) == 10


def catalog_resolved_asset_id(catalog, requested_name):
    from app.assets.resolver import resolve

    return resolve(AssetRequest(requested_name=requested_name), catalog=catalog).asset_id


def test_evidence_placements_are_reachable_on_every_matrix_row(database_url):
    """Required critical-evidence placements all reachable: non-empty
    interaction + payload evidence id + evidence-capable anchor."""
    store, service = _service_ctx(database_url)
    try:
        payloads, _started = _publish_all_rows(store, service)
    finally:
        store.dispose()
    from app.environments.manifests import load_all_environments

    kits = {k.environment_id: k for k in load_all_environments()}
    for row_id, payload in payloads.items():
        kit = kits[payload["draft"]["scene"]["environment_id"]]
        evidence_ids = {f["id"] for f in payload["draft"]["evidence"]}
        for placement in payload["draft"]["world_graph"]["placements"]:
            if placement.get("evidence_id") is None:
                continue
            assert placement.get("interaction"), (row_id, placement)
            assert placement.get("evidence_id") in evidence_ids, (
                row_id,
                placement,
            )
            anchor = kit.by_id[placement["anchor"]]
            assert anchor.type in EVIDENCE_CAPABLE_TYPES, (
                row_id,
                placement,
            )
            # the golden knife evidence is present and clickable on EVERY row
            if placement.get("object_id") == "kitchen_knife":
                assert placement["evidence_id"] == "forensic_knife_match_01"
                assert placement["interaction"] == "inspect"


# --------------------------------------------------------------------------- #
# 5. frozen truth never alters (solver across the matrix)
# --------------------------------------------------------------------------- #


def test_frozen_golden_truth_across_the_whole_matrix(database_url):
    store, service = _service_ctx(database_url)
    try:
        payloads, _started = _publish_all_rows(store, service)
    finally:
        store.dispose()
    from fixtures.world_showcase import GOLDEN_DEFAULT_PROMPT as _  # noqa: F401

    goldens = {}
    for row_id, payload in payloads.items():
        crime = payload["truth"]["crime"]
        assert crime["murderer_id"] == "thomas_reed"
        assert crime["victim_id"] == "sarah_miller"
        assert crime["motive_id"] == "cover_up_embezzlement"
        assert crime["weapon_id"] == "kitchen_knife"
        assert crime["crime_time"]["canonical"] == "2026-09-11T22:17:00+02:00"
        goldens[row_id] = crime
        proof, verdict = _solver(payload)
        assert verdict.all_true is True
    # identical truth across all rows (frozen; NOT re-derived per prompt)
    assert len({json.dumps(c, sort_keys=True) for c in goldens.values()}) == 1


# --------------------------------------------------------------------------- #
# 6. harness exit code + matrix report path (required Verification 5)
# --------------------------------------------------------------------------- #


def test_harness_exits_zero_and_writes_report(tmp_path):
    import tools.showcase_matrix as harness

    out = tmp_path / "matrix.json"
    rc = harness.main(["--out", str(out), "--tmp", str(tmp_path / "scratch")])
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["reportVersion"] == 1
    assert report["summary"]["generationSuccessRate"] == 1.0
    assert report["summary"]["solverUniqueResolvedCount"] == 10
    assert report["qualityGate"]["pass"] is True


# --------------------------------------------------------------------------- #
# 7. ADV-151: hostile CLI invocations exit 2 with a SANITIZED one-line stderr
#    message — never a traceback (the catalog_report DEF-064 pattern)
# --------------------------------------------------------------------------- #


def _invoke_cli(capsys, args):
    import tools.showcase_matrix as harness

    rc = harness.main(args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_cli_exits_2_when_tmp_is_an_existing_file(tmp_path, capsys):
    blocker = tmp_path / "occupied.txt"
    blocker.write_text("not a directory", encoding="utf-8")
    rc, out, err = _invoke_cli(capsys, ["--tmp", str(blocker)])
    assert rc == 2
    assert "Traceback" not in out and "Traceback" not in err
    assert "existing FILE" in err
    assert "traceback" not in err.lower()


def test_cli_exits_2_when_matrix_fixtures_import_fails(tmp_path, monkeypatch, capsys):
    # Simulate the fixtures.world_matrix module being missing/broken: importing
    # it now raises ImportError (a ModuleNotFoundError subclass) inside the
    # harness's build_report -> the CLI must exit 2 WITHOUT a traceback.
    import sys as _sys

    import tools.showcase_matrix as harness  # noqa: F401 (module must import OK)

    monkeypatch.setitem(_sys.modules, "fixtures.world_matrix", None)
    rc, out, err = _invoke_cli(capsys, ["--tmp", str(tmp_path / "scratch")])
    assert rc == 2
    assert "Traceback" not in out and "Traceback" not in err
    assert "fixtures" in err.lower()
    assert "traceback" not in err.lower()


def test_cli_exits_2_when_matrix_row_id_is_tampered(tmp_path, monkeypatch, capsys):
    from fixtures import world_matrix as world_matrix_mod

    # Tampered fixture: MATRIX_ORDER references an unknown row id -> the
    # harness's matrix_prompt/matrix_expectation raise KeyError.
    monkeypatch.setattr(
        world_matrix_mod, "MATRIX_ORDER", ("apartment_a", "does_not_exist_row")
    )
    rc, out, err = _invoke_cli(capsys, ["--tmp", str(tmp_path / "scratch")])
    assert rc == 2
    assert "Traceback" not in out and "Traceback" not in err
    assert "unknown matrix row" in err
    assert "traceback" not in err.lower()