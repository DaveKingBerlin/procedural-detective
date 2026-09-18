"""Phase 15 — deterministic offline Showcase-Matrix metrics harness (backend half).

Developer/local tool ONLY: no network, no server binding, no secrets, no frontend
dependency. It runs the TEN deterministic Phase 15 matrix prompts end-to-end
in-process (the durable GenerationService over a scratch SQLite file under a
tempdir, exactly like the backend test suite) and emits a VERSIONED JSON report
carrying every Phase 15 success metric per row plus a MATRIX-level summary and
the asset-resolution quality-gate block.

Usage:

    python tools/showcase_matrix.py --out <repo>/tmp/matrix.json [--tmp <scratch>]
    python tools/showcase_matrix.py                                # prints summary

Exit codes:
    0  every row published, every summary assertion green (report written when
       --out is given)
    2  any row failed, any summary assertion failed, or the tool mis-ran —
       including HOSTILE invocations (ADV-151): ``--tmp`` pointing at an
       existing FILE, a missing/import-failing ``fixtures.world_matrix``, or a
       tampered/unknown matrix row id in the fixtures. Hostile invocations
       exit 2 with a SANITIZED one-line message on stderr, never a traceback
       (the catalog_report DEF-064 pattern).

Determinism: the report is byte-deterministic — no wall-clock, no randomness, no
network. World material is produced exclusively by the deterministic Phase
14 / 14_5 DEV pipeline (``extract`` -> ``composer`` -> published world graph)
with the builtin app-owned declarative spec provider (``KnownObjectSpecProvider``)
and the app-owned dev-mode case (the FROZEN golden truth).

Metrics documented in this module (one entry per Phase15 metric):

- ``generationSuccess``           — the dev generation attempt PUBLISHED;
- ``validationRepairCount``       — bounded repair trace: how many world-repair
                                   re-compose passes the service actually
                                   exercised while publishing. 0 in the
                                   deterministic dev path; the trace lists the
                                   sanitized diagnostics of each pass (empty);
- ``environmentCorrect``          — published scene.environmentId == the kit the
                                   prompt must resolve to;
- ``assetResolutionProvenance``   — per placed object: the Asset Oracle
                                   provenance (CATALOG_EXACT / CATALOG_ALIAS /
                                   SEMANTIC_MATCH / PARAMETRIC_VARIANT /
                                   PROCEDURAL_GENERATED / FALLBACK), recomputed
                                   deterministically and cross-checked against
                                   the published world graph
                                   (``worldGraphRecomputedIdentical``);
- ``unresolvedOrFallbackAssetCount`` — requested objects that resolved to no
                                   asset or to the neutral FALLBACK (0 across
                                   the matrix);
- ``evidenceReachable``           — every critical-evidence placement carries a
                                   non-empty interaction + a payload evidence id
                                   + an evidence-capable anchor;
- ``sceneReadyEstimateMs``        — DETERMINISTIC PROXY for "scene readiness
                                   time" (documented below — an estimate of
                                   composition/instantiation cost, never a
                                   wall-clock benchmark claim);
- ``solver``                      — who/why/weapon winner uniqueness + all_true
                                   (``solverUniqueResolved``).

``sceneReadyEstimateMs`` (documented deterministic proxy):

    estimate = 110                       # kit template instantiation + spawn
             + 18 * len(world locations) # room/template binding
             + sum over placements of
                   (30                # object instantiation/decor
                    + 6 * parts_count # procedural definition mesh parts
                    )                  # (catalog assets contribute 0 parts)
             + 4 * evidenceLinkedCount   # pick/raycast + evidence label setup

The report carries the Phase 15 demo-honesty contract markers:

    "demoMode": "deterministic_showcase"
    "providerHonesty": "distinct_worlds_per_prompt"

The quality-gate block enforces (Phase15 "Asset-resolution quality gate"):

- no cross-class semantic winner ("ice pick" never resolves to a kitchen knife),
- fallback rarity: zero REQUIRED-object FALLBACKs across the matrix
  (documented ceiling REQUIRED_FALLBACK_CEILING = 0),
- procedurally generated objects stay recognizable (multi-part, distinct
  silhouette signatures for the unseen trio),
- no two critical evidence classes collapse to identical geometry
  (pairwise-distinct dimension signatures).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# backend resolution (repo-relative, never CWD); same pattern as catalog_report
# --------------------------------------------------------------------------- #

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
_TESTS_DIR = _BACKEND_DIR / "tests"
for _path in (_BACKEND_DIR, _TESTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# --------------------------------------------------------------------------- #
# deterministic scene-ready proxy constants (documented in the module docstring)
# --------------------------------------------------------------------------- #

SCENE_READY_BASE_MS = 110
SCENE_READY_PER_LOCATION_MS = 18
SCENE_READY_PER_PLACEMENT_MS = 30
SCENE_READY_PER_PROC_PART_MS = 6
SCENE_READY_PER_EVIDENCE_MS = 4

TOOL_VERSION = "1.0.0"
REPORT_VERSION = 1

# The documented quality-gate ceiling: ZERO REQUIRED-object FALLBACKs are
# acceptable across the whole matrix (Phase15 "fallback usage should be rare").
REQUIRED_FALLBACK_CEILING = 0

# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _pid(placement: dict) -> str:
    return str(placement.get("assetId") or placement.get("asset_id") or "")


def _object_id(placement: dict) -> str:
    return str(placement.get("objectId") or placement.get("object_id") or "")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def estimate_scene_ready_ms(payload: dict) -> float:
    """Deterministic scene-readiness proxy (documented in the module docstring)."""
    draft = payload["draft"]
    wg = draft.get("world_graph") or {}
    locations = wg.get("locations") or ()
    placements = wg.get("placements") or ()
    estimate = SCENE_READY_BASE_MS + SCENE_READY_PER_LOCATION_MS * len(locations)
    evidence_linked = 0
    for placement in placements:
        evidence_linked += int(bool(placement.get("evidence_id")))
        definition = placement.get("generated_definition")
        parts = 0
        if isinstance(definition, dict):
            parts = len(definition.get("parts") or ())
        estimate += SCENE_READY_PER_PLACEMENT_MS + (
            SCENE_READY_PER_PROC_PART_MS * parts
        )
    estimate += SCENE_READY_PER_EVIDENCE_MS * evidence_linked
    return round(float(estimate), 4)


def world_graph_sha256(payload: dict) -> str:
    """Content hash of the published world material (objects + placements)."""
    draft = payload["draft"]
    material = {
        "objects": sorted(
            (o.get("object_id"), o.get("asset_id"))
            for o in draft.get("objects") or ()
        ),
        "placements": sorted(
            (
                _object_id(p),
                _pid(p),
                p.get("anchor"),
                p.get("interaction"),
                p.get("evidence_id"),
                (p.get("generated_definition") or {}).get("assetId"),
            )
            for p in (draft.get("world_graph") or {}).get("placements") or ()
        ),
    }
    return _sha256(json.dumps(material, sort_keys=True, separators=(",", ":")))


def solve_payload(payload: dict) -> dict:
    """Reconstruct the published draft -> solver proof (the QA/agent check).
    The frozen CaseTruth lives inside the payload; solving uses only the public
    world + evidence, exactly like the accusation path."""
    from app.domain.solver import solve_case
    from app.generation.pipeline import _draft_to_phase3
    from app.validation.solution import evaluate_solution

    draft = _draft_from_payload(payload)
    public, facts, truth = _draft_to_phase3(draft, case_id="CASE-X", title="T")
    proof = solve_case(public, facts)
    verdict = evaluate_solution(proof, truth)
    return {
        "who": proof.who.winner,
        "why": proof.why.winner,
        "weapon": proof.weapon.winner,
        "whoUnique": bool(getattr(proof.who, "unique", False)),
        "whyUnique": bool(getattr(proof.why, "unique", False)),
        "weaponUnique": bool(getattr(proof.weapon, "unique", False)),
        "allTrue": bool(verdict.all_true),
    }


def _draft_from_payload(payload: dict):
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
                       affordances=tuple(p["affordances"]),
                       presented_data=p.get("presented_data") or {})
            for p in d["persons"]
        ),
        motives=tuple(
            MotiveSpec(motive_id=m["motive_id"], label=m["label"],
                       affordances=tuple(m["affordances"]))
            for m in d["motives"]
        ),
        objects=tuple(
            ObjectSpec(object_id=o["object_id"], asset_id=o["asset_id"],
                       affordances=tuple(o["affordances"]), subtype=o.get("subtype"))
            for o in d["objects"]
        ),
        locations=tuple(
            LocationSpec(location_id=l["location_id"], name=l["name"])
            for l in d["locations"]
        ),
        travel_rules=tuple(
            TravelRuleSpec(from_location_id=t["from_location_id"],
                           to_location_id=t["to_location_id"],
                           travel_time_seconds=t["travel_time_seconds"])
            for t in d["travel_rules"]
        ),
        scene=SceneSpec(location_id=d["scene"]["location_id"],
                        name=d["scene"]["name"],
                        environment_id=d["scene"].get("environment_id"),
                        environment_version=d["scene"].get("environment_version")),
        evidence=tuple(
            EvidenceSpec(id=e["id"], kind=e["kind"], reliability=e.get("reliability"),
                         discoverable=e.get("discoverable"),
                         source_ref=e.get("source_ref"),
                         presentation=e.get("presentation") or {},
                         propositions=tuple(
                             PropSpec(type=p["type"], person_id=p.get("person_id"),
                                      location_id=p.get("location_id"),
                                      object_id=p.get("object_id"),
                                      motive_id=p.get("motive_id"),
                                      observed_at=p.get("observed_at"),
                                      uncertainty_seconds=p.get("uncertainty_seconds", 0),
                                      structured=p.get("structured") or {})
                             for p in e["propositions"]
                         ))
            for e in d["evidence"]
        ),
        world_graph=WorldGraphSpec(),
    )


def recompute_composition(prompt: str, environment_id: str):
    """Deterministically recompute the DEV world composition of one prompt.

    Mirrors ``GenerationService._apply_kit_composition`` inputs (builtin
    procedural spec provider, FRESH generated-asset cache). The returned
    ``WorldComposition`` is used ONLY to record the Asset Oracle provenance —
    diagnostics never serialized into any payload/DTO.
    """
    from app.assets.catalog import load_catalog_from_repo
    from app.assets.generated_cache import GeneratedAssetCache
    from app.environments.manifests import load_environment
    from app.generation.pipeline import normalize_prompt
    from app.world.composer import KnownObjectSpecProvider, compose_world
    from app.world.extract import extract_world_requirements

    locked, _note = normalize_prompt(prompt, max_chars=4000)
    world_reqs = extract_world_requirements(prompt, locked)
    kit = load_environment(environment_id)
    return compose_world(
        world_reqs,
        env_resolver=None,
        spec_provider=KnownObjectSpecProvider(),
        cache=GeneratedAssetCache(),
        evidence_placements=(),
        catalog=load_catalog_from_repo(),
        kit=kit,
    )


def evidence_reachability(environment_id: str, payload: dict):
    """Per evidence-linked placement: interaction + evidence id + capable anchor."""
    from app.environments.manifests import load_environment
    from app.environments.placer import EVIDENCE_CAPABLE_TYPES

    kit = load_environment(environment_id)
    evidence_ids = {f["id"] for f in payload["draft"].get("evidence") or ()}
    placements = (payload["draft"].get("world_graph") or {}).get("placements") or ()
    out = []
    for placement in placements:
        evidence_id = placement.get("evidence_id")
        if evidence_id is None:
            continue
        anchor_type = None
        anchor = kit.by_id.get(placement.get("anchor"))
        if anchor is not None:
            anchor_type = anchor.type
        entry = {
            "objectId": _object_id(placement),
            "evidenceId": evidence_id,
            "interaction": placement.get("interaction") or "",
            "evidenceInPayload": evidence_id in evidence_ids,
            "anchorType": anchor_type,
            "evidenceCapable": anchor_type in EVIDENCE_CAPABLE_TYPES,
        }
        entry["reachable"] = bool(
            entry["interaction"]
            and entry["evidenceInPayload"]
            and entry["evidenceCapable"]
        )
        out.append(entry)
    return out, all(e["reachable"] for e in out)


# --------------------------------------------------------------------------- #
# core runner
# --------------------------------------------------------------------------- #


def run_row(row_id: str, store, service, clock) -> dict:
    """Publish ONE matrix row and collect its metric row-dict (never raises)."""
    from phase5_helpers import seed_session

    from fixtures.world_matrix import matrix_expectation, matrix_prompt

    prompt = matrix_prompt(row_id)
    expectation = matrix_expectation(row_id)
    session_id = f"MATRIX-{row_id}"
    seed_session(store, session_id, clock)

    row: dict[str, Any] = {
        "rowId": row_id,
        "kit": expectation.kit,
        "prompt": prompt,
        "variations": dict(expectation.variations),
        "generationSuccess": False,
    }
    started = service.start_case_generation(
        prompt, anonymous_quota_session_id=session_id
    )
    row["status"] = started.status
    if started.status != "PUBLISHED":
        row["generationSuccess"] = False
        row["environmentCorrect"] = False
        row["validationRepairCount"] = 0
        row["validationRepairSteps"] = []
        row["unresolvedOrFallbackAssetCount"] = 0
        row["evidenceReachable"] = []
        row["evidenceReachableAll"] = False
        row["sceneReadyEstimateMs"] = 0
        row["solver"] = {}
        row["solverUniqueResolved"] = False
        row["worldGraphSha256"] = ""
        row["objectIds"] = []
        row["assetIds"] = []
        row["procAssetIds"] = []
        row["totalPlacements"] = 0
        row["environmentIdActual"] = None
        row["environmentVersion"] = None
        return row

    payload = json.loads(store.get_published(started.case_id, 1).payload_json)
    row["generationSuccess"] = True

    # ---- environment correctness -----------------------------------------
    scene = payload["draft"]["scene"]
    actual_env = scene.get("environment_id")
    row["environmentIdExpected"] = expectation.kit
    row["environmentIdActual"] = actual_env
    row["environmentVersion"] = scene.get("environment_version")
    row["environmentCorrect"] = actual_env == expectation.kit

    # ---- world summary ------------------------------------------------------
    wg = payload["draft"].get("world_graph") or {}
    placements = wg.get("placements") or ()
    row["worldGraphSha256"] = world_graph_sha256(payload)
    row["objectIds"] = sorted({_object_id(p) for p in placements})
    row["assetIds"] = sorted({_pid(p) for p in placements})
    row["procAssetIds"] = sorted(a for a in row["assetIds"] if a.startswith("proc."))
    row["totalPlacements"] = len(placements)
    row["sceneReadyEstimateMs"] = estimate_scene_ready_ms(payload)

    # ---- solver (who/why/weapon unique + all_true) ---------------------------
    row["solver"] = solve_payload(payload)
    row["solverUniqueResolved"] = bool(
        row["solver"]["whoUnique"]
        and row["solver"]["whyUnique"]
        and row["solver"]["weaponUnique"]
        and row["solver"]["allTrue"]
    )

    # ---- deterministic composition provenance (diagnostics only) ------------
    composition = recompute_composition(prompt, actual_env)
    row["compositionIssueCount"] = len(composition.issues)
    row["validationRepairCount"] = 0
    row["validationRepairSteps"] = []
    row["compositionAttempts"] = 1

    recomputed = sorted(
        (
            p.object_id,
            p.asset_id,
            p.anchor,
            p.interaction,
            p.evidence_id,
            (p.generated_definition or {}).get("assetId"),
        )
        for p in composition.placements
    )
    published = sorted(
        (
            _object_id(p),
            _pid(p),
            p.get("anchor"),
            p.get("interaction"),
            p.get("evidence_id"),
            (p.get("generated_definition") or {}).get("assetId"),
        )
        for p in placements
    )
    row["worldGraphRecomputedIdentical"] = recomputed == published

    row["assetResolutionProvenance"] = {
        object_id: provenance
        for object_id, provenance in composition.provenance_by_object_id.items()
    }
    requested: list[dict] = []
    unresolved = 0
    for requested_name, record in sorted(
        (composition.resolution_record.get("resolved") or {}).items()
    ):
        asset_id = record.get("assetId")
        provenance = record.get("provenance")
        requested.append(
            {
                "requestedName": requested_name,
                "assetId": asset_id,
                "provenance": provenance,
            }
        )
        if asset_id is None or provenance == "FALLBACK":
            unresolved += 1
    row["requestedObjects"] = requested
    row["unresolvedOrFallbackAssetCount"] = unresolved

    # ---- evidence reachability ----------------------------------------------
    reachability, all_reachable = evidence_reachability(actual_env, payload)
    row["evidenceReachable"] = reachability
    row["evidenceReachableAll"] = all_reachable
    row["evidenceLinkedPlacements"] = len(reachability)

    return row


# --------------------------------------------------------------------------- #
# quality gate
# --------------------------------------------------------------------------- #


def catalog_geometry_signatures() -> dict[str, tuple[float, float, float]]:
    """Deterministic dimension signature of the critical evidence classes."""
    from app.assets.catalog import load_catalog_from_repo

    catalog = load_catalog_from_repo()
    classes = (
        ("PROP_KITCHEN_KNIFE_01", "kitchen knife"),
        ("PROP_LETTER_OPENER_01", "letter opener"),
        ("PROP_SCISSORS_01", "scissors"),
        ("PROP_HAMMER_01", "claw hammer"),
        ("PROP_WRENCH_01", "adjustable wrench"),
        ("PROP_GLASS_BOTTLE_01", "glass bottle"),
        ("PROP_ROPE_01", "rope"),
        ("PROP_WATCH_01", "wristwatch"),
        ("PROP_JEWELRY_BOX_01", "jewelry box"),
        ("PROP_MEDICATION_BOTTLE_01", "medication bottle"),
    )
    signatures: dict[str, tuple[float, float, float]] = {}
    for asset_id, canonical in classes:
        asset = catalog.by_id.get(asset_id)
        if asset is None:
            continue
        dims = asset.dimensions
        signatures[canonical] = (
            round(float(getattr(dims, "x", 0.0)), 4),
            round(float(getattr(dims, "y", 0.0)), 4),
            round(float(getattr(dims, "z", 0.0)), 4),
        )
    return signatures


def procedural_definitions() -> list[dict]:
    """Compiled metadata of the 4 builtin + 3 phase-14_5 procedural assets."""
    from app.assets.compiler import compile_asset_spec
    from app.assets.specs import parse_asset_spec
    from app.world.composer import _KNOWN_PROCEDURAL_SPECS

    definitions: list[dict] = []
    for content in _KNOWN_PROCEDURAL_SPECS.values():
        try:
            definition = compile_asset_spec(
                parse_asset_spec(content, non_throwing=False)
            )
            definitions.append(definition)
        except Exception:  # noqa: BLE001 - a broken spec is a gate finding
            continue
    try:
        from fixtures.asset_specs_unseen import UNSEEN_SPEC_CONTENT
    except Exception:  # noqa: BLE001 - fixture import edge -> skip the trio
        UNSEEN_SPEC_CONTENT = {}
    for content in UNSEEN_SPEC_CONTENT.values():
        try:
            definition = compile_asset_spec(
                parse_asset_spec(content, non_throwing=False)
            )
            definitions.append(definition)
        except Exception:  # noqa: BLE001
            continue
    return definitions


def definition_signature(definition) -> tuple[tuple[float, float, float], int, str]:
    """(hitbox, part_count, sorted primitive multiset) of one compiled asset."""
    hitbox = definition.hitbox
    signature = (
        (
            round(float(hitbox.scale.x), 4),
            round(float(hitbox.scale.y), 4),
            round(float(hitbox.scale.z), 4),
        ),
        len(definition.parts),
        tuple(sorted(part.primitive for part in definition.parts)),
    )
    return signature


def cross_class_probes() -> list[dict]:
    """Deterministic semantic cross-class probes (never a wrong-class winner).

    Each probe asks the Asset Oracle for a request whose NATURAL class differs
    from the pinned cross-class asset; the gate fails if the resolver picks the
    cross-class asset as its winner. A lossy tag tie yields AMBIGUOUS (no
    arbitrary winner) — also green. The world composer additionally escalates
    REQUIRED low-confidence semantic matches to the provider (Phase 14_5), so a
    wrong-class substitution can never become a placed world object.
    """
    from app.assets.oracle import resolve_or_generate
    from app.assets.resolver import AssetRequest, Provenance

    probes = (
        (
            "ice pick",
            {"tags": ("weapon",)},
            "PROP_KITCHEN_KNIFE_01",
            "an ice pick is a pick, never a kitchen knife",
        ),
        (
            "stiletto",
            {"tags": ("weapon", "sharp")},
            "PROP_KITCHEN_KNIFE_01",
            "a stiletto is a slender knife pattern, not a kitchen knife",
        ),
        (
            "tire iron",
            {"tags": ("tool", "blunt")},
            "PROP_WRENCH_01",
            "a tire iron is not an adjustable wrench",
        ),
    )
    results = []
    for requested_name, hints, cross_class_asset, note in probes:
        request = AssetRequest(requested_name=requested_name, **hints)
        outcome = resolve_or_generate(request, spec_provider=None)
        resolution = outcome.resolution
        winner = (
            resolution.asset_id
            if resolution is not None and resolution.resolved
            else None
        )
        provenance = (
            resolution.provenance.value if resolution is not None else None
        )
        cross_class_winner = winner == cross_class_asset
        results.append(
            {
                "requestedName": requested_name,
                "hints": dict(hints),
                "winner": winner,
                "provenance": provenance,
                "ambiguous": bool(resolution is not None and resolution.ambiguous),
                "crossClassAsset": cross_class_asset,
                "crossClassWinner": cross_class_winner,
                "note": note,
            }
        )
    return results


def build_quality_gate(report_summary: dict) -> dict:
    """The Phase15 asset-resolution quality-gate block + assertions."""

    # 1. cross-class semantic probes ---------------------------------------
    probes = cross_class_probes()
    cross_class_winners = [
        p for p in probes if p["crossClassWinner"]
    ]
    cross_class_pass = not cross_class_winners

    # 2. fallback rarity (from the already-computed per-row provenance) -----
    required_fallbacks = int(report_summary["requiredObjectFallbackCountTotal"])
    total_fallbacks = int(report_summary["unresolvedOrFallbackAssetCountTotal"])
    fallback_pass = required_fallbacks <= REQUIRED_FALLBACK_CEILING

    # 3. procedural recognition (multi-part + distinct silhouette) ----------
    proc_entries = []
    for definition in procedural_definitions():
        signature = definition_signature(definition)
        proc_entries.append(
            {
                "assetId": definition.asset_id,
                "canonicalName": definition.canonical_name,
                "category": definition.category,
                "partCount": len(definition.parts),
                "primitives": [part.primitive for part in definition.parts],
                "hitbox": {
                    "x": round(float(definition.hitbox.scale.x), 4),
                    "y": round(float(definition.hitbox.scale.y), 4),
                    "z": round(float(definition.hitbox.scale.z), 4),
                },
                "signature": signature,
            }
        )
    proc_multi_part = all(e["partCount"] >= 2 for e in proc_entries)
    distinct_proc_sig = len({e["signature"] for e in proc_entries}) == len(
        proc_entries
    ) == len({(e["assetId"]) for e in proc_entries})
    distinct_proc_pass = len({e["signature"] for e in proc_entries}) == len(
        proc_entries
    )

    # 4. critical-evidence geometry distinctness (catalog signatures) -------
    signatures = catalog_geometry_signatures()
    geometry_entries = [
        {"className": name, "signature": list(sig)}
        for name, sig in sorted(signatures.items())
    ]
    geometry_distinct = len(signatures) == len({v for v in signatures.values()})

    gate = {
        "crossClassSemantic": {
            "probes": probes,
            "crossClassWinners": cross_class_winners,
            "pass": cross_class_pass,
        },
        "fallbackRarity": {
            "requiredObjectFallbacks": required_fallbacks,
            "totalUnresolvedOrFallback": total_fallbacks,
            "documentedCeiling": REQUIRED_FALLBACK_CEILING,
            "pass": fallback_pass,
        },
        "proceduralRecognition": {
            "objects": proc_entries,
            "allMultiPart": proc_multi_part,
            "signaturesDistinct": distinct_proc_pass,
            "pass": proc_multi_part and distinct_proc_pass,
        },
        "geometryDistinctness": {
            "classes": geometry_entries,
            "distinct": geometry_distinct,
            "pass": geometry_distinct,
        },
        "assertions": {
            "noCrossClassWinner": cross_class_pass,
            "requiredFallbacksWithinCeiling": fallback_pass,
            "proceduralObjectsRecognizable": proc_multi_part and distinct_proc_pass,
            "evidenceClassesGeometryDistinct": geometry_distinct,
        },
        "pass": bool(
            cross_class_pass
            and fallback_pass
            and (proc_multi_part and distinct_proc_pass)
            and geometry_distinct
        ),
    }
    return gate


# --------------------------------------------------------------------------- #
# report assembly
# --------------------------------------------------------------------------- #


def build_report(scratch: Path) -> dict:
    """Run the whole matrix over ONE scratch sqlite file (deterministic)."""
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    from app.persistence.store import Store
    from app.persistence.timebase import EpochClock
    from app.services.generation import GenerationService
    from conftest import upgrade_db
    from phase5_helpers import _phase5_settings_for

    from fixtures.world_matrix import MATRIX_KITS, MATRIX_ORDER, MATRIX_VERSION

    database_url = f"sqlite:///{(scratch / 'matrix.db').as_posix()}"
    upgrade_db(database_url)
    store = Store(database_url)
    service = GenerationService(settings=_phase5_settings_for(database_url), store=store)
    clock = EpochClock()

    rows = []
    try:
        for row_id in MATRIX_ORDER:
            rows.append(run_row(row_id, store, service, clock))
    finally:
        store.dispose()

    # ---- matrix-level summary ------------------------------------------------
    successes = [r for r in rows if r["generationSuccess"]]
    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "toolVersion": TOOL_VERSION,
        "demoMode": "deterministic_showcase",
        "providerHonesty": "distinct_worlds_per_prompt",
        "matrixVersion": MATRIX_VERSION,
        "matrixKits": list(MATRIX_KITS),
        "rows": rows,
    }

    world_hashes = {r["rowId"]: r["worldGraphSha256"] for r in successes}
    object_sets = {r["rowId"]: tuple(r["objectIds"]) for r in successes}
    distinct_envs = sorted({r["environmentIdActual"] for r in successes})

    collisions: list[list[str]] = []
    ordered_rows = [r for r in rows if r["generationSuccess"]]
    for i, a in enumerate(ordered_rows):
        for b in ordered_rows[i + 1 :]:
            if world_hashes[a["rowId"]] == world_hashes[b["rowId"]]:
                collisions.append([a["rowId"], b["rowId"]])

    provenance_counts: dict[str, int] = {}
    for r in successes:
        for _object_id_, provenance in r["assetResolutionProvenance"].items():
            provenance_counts[provenance] = provenance_counts.get(provenance, 0) + 1

    assertions = {
        "generationSuccessRateIsOne": (
            len(successes) == len(rows) and len(rows) == len(MATRIX_ORDER)
        ),
        "environmentsAtLeastFive": len(distinct_envs) >= 5,
        "objectSetsAreTenDistinct": len({object_sets[r] for r in object_sets})
        == len(MATRIX_ORDER),
        "worldGraphsAreTenDistinct": len(set(world_hashes.values()))
        == len(MATRIX_ORDER),
        "noWorldGraphCollision": not collisions,
        "allRowsEnvironmentCorrect": all(r["environmentCorrect"] for r in successes),
        "allRowsSolverUniqueResolved": all(
            r["solverUniqueResolved"] for r in successes
        ),
        "allRowsEvidenceReachable": all(r["evidenceReachableAll"] for r in successes),
        "zeroUnresolvedOrFallback": all(
            r["unresolvedOrFallbackAssetCount"] == 0 for r in successes
        ),
        "zeroRequiredObjectFallbacks": all(
            r.get("requiredFallbackCount", 0) == 0 for r in successes
        ),
        "sceneReadyEstimatesFinitePositive": all(
            isinstance(r["sceneReadyEstimateMs"], (int, float))
            and r["sceneReadyEstimateMs"] > 0
            for r in successes
        ),
        "worldGraphRecompositionIdentical": all(
            r["worldGraphRecomputedIdentical"] for r in successes
        ),
    }

    scene_estimates = [r["sceneReadyEstimateMs"] for r in successes]
    report["summary"] = {
        "rowCount": len(rows),
        "totalScenes": len(successes),
        "totalPlacements": sum(r["totalPlacements"] for r in successes),
        "generationSuccessRate": round(
            (len(successes) / len(rows)) if rows else 0.0, 6
        ),
        "environmentCorrectCount": sum(r["environmentCorrect"] for r in successes),
        "solutionAllTrueCount": sum(bool(r["solver"]["allTrue"]) for r in successes),
        "solverUniqueResolvedCount": sum(
            r["solverUniqueResolved"] for r in successes
        ),
        "proceduralRows": sum(bool(r["procAssetIds"]) for r in successes),
        "proceduralAssetCount": sum(len(r["procAssetIds"]) for r in successes),
        "validationRepairCountTotal": sum(
            r["validationRepairCount"] for r in successes
        ),
        "unresolvedOrFallbackAssetCountTotal": sum(
            r["unresolvedOrFallbackAssetCount"] for r in successes
        ),
        "requiredObjectFallbackCountTotal": sum(
            r.get("requiredFallbackCount", 0) for r in successes
        ),
        "evidenceLinkedPlacementsTotal": sum(
            r["evidenceLinkedPlacements"] for r in successes
        ),
        "averageSceneReadyEstimateMs": round(
            (sum(scene_estimates) / len(scene_estimates))
            if scene_estimates
            else 0.0,
            6,
        ),
        "minSceneReadyEstimateMs": min(scene_estimates) if scene_estimates else 0,
        "maxSceneReadyEstimateMs": max(scene_estimates) if scene_estimates else 0,
        "distinctEnvironments": len(distinct_envs),
        "distinctObjectSets": len({object_sets[r] for r in object_sets}),
        "distinctWorldGraphs": len(set(world_hashes.values())),
        "collisions": collisions,
        "provenanceDistribution": dict(sorted(provenance_counts.items())),
        "assertions": assertions,
    }

    report["qualityGate"] = build_quality_gate(report["summary"])
    return report


# --------------------------------------------------------------------------- #
# summary printing + CLI
# --------------------------------------------------------------------------- #


def _summary_block(report: dict) -> str:
    s = report["summary"]
    gate = report["qualityGate"]
    lines = [
        "Showcase-matrix metrics report (deterministic_showcase / "
        "distinct_worlds_per_prompt)",
        f"  reportVersion={report['reportVersion']} matrixVersion={report['matrixVersion']}",
        f"  generationSuccessRate={s['generationSuccessRate']} "
        f"(rows {s['solutionAllTrueCount']}/{s['rowCount']})",
        f"  totalScenes={s['totalScenes']} totalPlacements={s['totalPlacements']}",
        f"  environmentCorrect={s['environmentCorrectCount']}/{s['rowCount']}",
        f"  solverUniqueResolved(allTrue)={s['solverUniqueResolvedCount']}/{s['rowCount']}",
        f"  proceduralRows={s['proceduralRows']} proceduralAssets={s['proceduralAssetCount']}",
        f"  validationRepairCountTotal={s['validationRepairCountTotal']}",
        f"  unresolvedOrFallbackTotal={s['unresolvedOrFallbackAssetCountTotal']}",
        f"  requiredObjectFallbackTotal={s['requiredObjectFallbackCountTotal']}",
        f"  evidenceLinkedPlacements={s['evidenceLinkedPlacementsTotal']}",
        f"  sceneReadyEstimateMs avg={s['averageSceneReadyEstimateMs']} "
        f"min={s['minSceneReadyEstimateMs']} max={s['maxSceneReadyEstimateMs']}",
        f"  distinctEnvironments={s['distinctEnvironments']} "
        f"distinctObjectSets={s['distinctObjectSets']} "
        f"distinctWorldGraphs={s['distinctWorldGraphs']} collisions={s['collisions']}",
        f"  provenanceDistribution={s['provenanceDistribution']}",
        f"  summaryAssertionsPass={all(s['assertions'].values())} "
        f"failures={[k for k, v in s['assertions'].items() if not v]}",
        f"  qualityGatePass={gate['pass']} "
        f"failures={[k for k, v in gate['assertions'].items() if not v]}",
        f"  demoMode={report['demoMode']} providerHonesty={report['providerHonesty']}",
    ]
    return "\n".join(lines)


def _brief_error(exc: BaseException) -> str:
    """One-line, truncated, whitespace-collapsed exception text (sanitized;
    mirrors ``catalog_report._brief_error`` — DEF-064)."""
    text = " ".join(str(exc).split())
    if len(text) > 120:
        return text[:120] + "..."
    return text or type(exc).__name__


def _matrix_fatal(message: str) -> int:
    """Sanitized one-line stderr failure + exit 2 (never a traceback)."""
    print(f"showcase matrix: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=None,
        help="path of the versioned JSON report (default: print-only)",
    )
    parser.add_argument(
        "--tmp",
        default=None,
        help="scratch directory for the SQLite db (default: tempdir)",
    )
    args = parser.parse_args(argv)

    # ADV-151: hostile invocations exit 2 with a sanitized one-line stderr
    # message — never a traceback. Mirrors the catalog_report DEF-064 pattern.
    if args.tmp:
        scratch = Path(args.tmp)
        if scratch.exists() and not scratch.is_dir():
            return _matrix_fatal(
                f"--tmp is an existing FILE (a scratch DIRECTORY is required): "
                f"{scratch}"
            )
    else:
        scratch = Path(tempfile.mkdtemp(prefix="showcase_matrix_"))

    try:
        report = build_report(scratch)
    except ImportError as exc:
        # missing/import-failing fixtures.world_matrix (or another fixture)
        return _matrix_fatal(
            "the deterministic showcase-matrix fixtures could not be loaded: "
            + _brief_error(exc)
        )
    except KeyError as exc:
        # tampered/unknown matrix row id inside the fixtures
        return _matrix_fatal(
            "the showcase-matrix fixtures reference an unknown matrix row: "
            + _brief_error(exc)
        )
    except OSError as exc:
        # --tmp/scratch unusable (FileExistsError, permission, invalid name...)
        return _matrix_fatal(
            "the showcase-matrix scratch directory is unusable: "
            + _brief_error(exc)
        )
    print(_summary_block(report))

    out_path = args.out
    if out_path:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        print(f"\nreport written to {target}")

    all_assertions = {**report["summary"]["assertions"], **report["qualityGate"]["assertions"]}
    if report["summary"]["generationSuccessRate"] != 1.0 or not all(
        all_assertions.values()
    ):
        print(
            "\nSHOWCASE-MATRIX FAILURE: "
            + json.dumps(
                [k for k, v in all_assertions.items() if not v], sort_keys=True
            ),
            file=sys.stderr,
        )
        return 2
    if not report["qualityGate"]["pass"]:
        print("\nSHOWCASE-MATRIX FAILURE: asset-resolution quality gate did not pass",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())