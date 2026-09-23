"""Phase 19E — GENERALIZED SEMANTIC WORLD OBJECT pipeline regression suite.

Covers the Phase 19E generalization matrix + bounds + hostile inputs while
keeping every existing validator/solver/publication gate unchanged:

A. WEAPONS (driver path, mocked transport):
   - known           kitchen knife            (PUBLISHED, unchanged)
   - variant         antique brass letter opener (PUBLISHED — base-dedup
                     collision fix on a kit whose base set already places the
                     RENDER asset)
   - unknown/proc.   fork / rolling pin       (PUBLISHED via procedural lane)
   - catalog         hammer / screwdriver / glass bottle
                     (PUBLISHED via catalog alias/exact — zero ASSET_SPEC call)
   For every weapon: CaseTruth semantic id preserved; object materialized as a
   REQUIRED semantic world object (the app's deterministic weapon-lock
   injection even when the model's world response omits it); render id never
   replaces the semantic id; discoverable weapon evidence exists; the weapon
   is in the candidate universe; the solver derives it uniquely; PUBLISHED.

B. NON-WEAPON GENERATED OBJECTS (decorative/interactive lane): coffee mug /
   umbrella / toolbox / briefcase / desk fan / suitcase / flower pot — stable
   semantic object + render (catalog or procedural) + placement success OR a
   safe decorative drop; NEVER in the weapon universe; NOT solver-required;
   a decorative failure never invalidates an otherwise valid case.

C. ADVERSARIAL: absurdly long names, executable/script-like descriptions,
   URL-like objects, huge dimensions, NaN/Infinity, excessive primitive
   counts, deeply nested AssetSpecs and duplicate semantic IDs all fail safely
   or normalize through the EXISTING rules (no validator is weakened); plus
   the fail-closed proof: an unsupported weapon with no representable geometry
   still FAILS CLOSED (no auto-publish).

D. BOUNDS + PROVIDER IMPACT: MAX_WORLD_OBJECTS_PER_KIT drops DECORATIVE
   objects beyond capacity (never REQUIRED/evidence ones; over-bound-after-
   drop FAILS CLOSED) and the per-attempt provider call counts stay inside the
   documented budgets (budgets UNCHANGED).

E. SOLVER UNIQUENESS WITHOUT AN ADDED PLAYER SURFACE: the deterministic solver
   derives EXACTLY ONE winner through the EXISTING surfaces only — the solver
   result, the pinned payload's REVEAL projection (the ONLY surface that names
   the winner) and the PRE-REVEAL candidate block (the winner stays an UNMARKED
   universe member; winner material never reaches a player DTO before reveal).

F. NON-RECURSIVE TOPOLOGICAL ORDER of the plan graph (no RecursionError even
   for arbitrarily deep graphs).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.constraints import LockedConstraints  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.world.requirements import (  # noqa: E402
    CRITICALITY_DECORATIVE,
    CRITICALITY_REQUIRED,
    ObjectRequest,
    WorldRequirements,
    safe_string_issues,
    semantic_object_id,
)
from app.world.composer import (  # noqa: E402
    DEFAULT_MAX_WORLD_OBJECTS,
    KnownObjectSpecProvider,
    compose_world,
)
from app.world.extract import extract_world_requirements  # noqa: E402
from test_ollama_driver import _case_people, _evidence, _j, _run  # noqa: E402


# --------------------------------------------------------------------------- #
# shared deterministic fixtures
# --------------------------------------------------------------------------- #

GENERIC_SPEC = """{
  "canonicalName": "Generic Prop",
  "category": "decor",
  "subtype": "generic_prop",
  "dimensions": {"x": 0.1, "y": 0.2, "z": 0.1},
  "parts": [
    {"id": "part_00", "role": "base", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0}, "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}, "scale": {"x": 0.1, "y": 0.02, "z": 0.08}}, "material": "plastic"},
    {"id": "part_01", "role": "body", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": 0.08, "z": 0.0}, "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}, "scale": {"x": 0.07, "y": 0.08, "z": 0.06}}, "material": "plastic", "parentId": "part_00"}
  ]
}"""

FORk_SPEC = """{
  "canonicalName": "Dinner Fork",
  "category": "decor",
  "subtype": "fork",
  "dimensions": {"x": 0.05, "y": 0.3, "z": 0.05},
  "parts": [
    {"id": "part_00", "role": "prongs", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.01}, "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}, "scale": {"x": 0.03, "y": 0.1, "z": 0.01}}, "material": "metal.steel"},
    {"id": "part_01", "role": "handle", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": -0.12, "z": 0.01}, "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}, "scale": {"x": 0.025, "y": 0.09, "z": 0.02}}, "material": "metal.steel"}
  ]
}"""

ROLLING_PIN_SPEC = """{
  "canonicalName": "Rolling Pin",
  "category": "decor",
  "subtype": "rolling_pin",
  "dimensions": {"x": 0.09, "y": 0.3, "z": 0.09},
  "parts": [
    {"id": "part_00", "role": "roller", "primitive": "cylinder",
     "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0}, "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}, "scale": {"x": 0.07, "y": 0.2, "z": 0.07}}, "material": "wood.light"},
    {"id": "part_01", "role": "handle", "primitive": "box",
     "transform": {"position": {"x": 0.0, "y": 0.11, "z": 0.0}, "rotation": {"x": 0.0, "y": 0.0, "z": 0.0}, "scale": {"x": 0.04, "y": 0.05, "z": 0.04}}, "material": "wood.dark"}
  ]
}"""


def _generic_spec_provider():
    """Deterministic provider serving ONE valid declarative spec per name."""
    from app.assets.spec_provider import AssetSpecResponse

    class Provider:
        def __init__(self) -> None:
            self.calls: list = []

        def generate(self, request):
            self.calls.append(getattr(request, "requested_name", "") or "")
            return AssetSpecResponse(content=GENERIC_SPEC)

    return Provider()


def _weapon_prompt(weapon: str, location: str = "office") -> str:
    return (
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
        f"Weapon: {weapon}\nTime: 23:42\nWitness: Lisa König\n"
        f"Location: {location}\n"
    )


def _world_with(*names: str, excluded: bool = False, environment: str = "office") -> dict:
    if excluded:
        objects = []
    else:
        objects = [{"name": name, "criticality": "required"} for name in names]
    return {
        "environmentHint": environment,
        "locationTokens": [environment.replace("_", " ")],
        "objects": objects,
        "relations": [],
        "unsafeUnsupported": [],
    }


def _assert_weapon_contract(record, weapon_id: str, render_prefix: str | None = None):
    """The Phase 19E weapon contract on ONE published driver record."""
    from app.domain.eligibility import derive_universes
    from app.generation import pipeline

    assert record.state is GenerationState.PUBLISHED
    assert record.published is not None
    assert record.last_validation.validation.all_true is True
    assert record.solver_proof is not None
    assert record.solver_proof.weapon.winner == weapon_id
    assert record.solver_proof.weapon.unique

    public, _facts, _truth, _draft = pipeline.assemble(record)
    # 1. the SEMANTIC id exists and the render id NEVER replaces it.
    objects = {o.object_id: o for o in public.objects}
    assert weapon_id in objects
    render_asset = objects[weapon_id].asset_id
    assert render_asset != weapon_id
    if render_prefix is not None:
        assert render_asset.startswith(render_prefix), render_asset
    # 2. discoverable weapon evidence exists.
    assert any(
        getattr(f, "id", "") == "d_ev_weapon_true" for f in record.draft.evidence
    )
    # 3. the weapon is IN the candidate universe (solver participation derived).
    universe = derive_universes(public)
    assert weapon_id in universe.weapon_ids
    # 4. weapon affordances present.
    assert "POTENTIAL_WEAPON" in objects[weapon_id].public_affordances


# --------------------------------------------------------------------------- #
# A. WEAPONS matrix (driver path)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("weapon", "world_names", "include_spec", "render_prefix"),
    [
        # known catalog weapon — unchanged golden path, world names it.
        ("kitchen knife", ("kitchen knife",), False, "PROP_KITCHEN_KNIFE_01"),
        # variant weapon on office: RENDER asset is a KIT-BASE asset (the
        # base letter_opener) — the base-dedup collision fix must materialize
        # its OWN semantic object.
        ("antique brass letter opener", ("antique brass letter opener",), False, "PROP_LETTER_OPENER_01"),
        # unknown procedural weapon whose world response EXCLUDES the weapon
        # (the deterministic weapon-lock injection must request it).
        ("fork", (), True, "proc."),
        # catalog alias weapon (world excludes it -> injection -> alias).
        ("hammer", (), False, "PROP_HAMMER_01"),
        # catalog exact weapon (world excludes it -> injection -> exact).
        ("screwdriver", (), False, "PROP_SCREWDRIVER_01"),
        # unknown procedural weapon.
        ("rolling pin", (), True, "proc."),
        # catalog exact weapon.
        ("glass bottle", (), False, "PROP_GLASS_BOTTLE_01"),
    ],
)
def test_weapon_generalization_matrix(weapon, world_names, include_spec, render_prefix):
    """Every Phase 19E weapon contract PUBLISHES with the semantic id
    preserved, no render-id substitution, discoverable evidence, candidate
    universe membership and a UNIQUE solver winner."""
    weapon_id = semantic_object_id(weapon)
    spec = FORk_SPEC if weapon == "fork" else (
        ROLLING_PIN_SPEC if weapon == "rolling pin" else GENERIC_SPEC
    )
    posts = [
        _j(_case_people(weapon=weapon_id)),
        _j(_evidence(weapon_obj=weapon_id, murderer="paul_becker")),
        _j(_world_with(*world_names, excluded=not world_names)),
    ]
    expected_calls = 3
    if include_spec:
        posts.append(spec)
        expected_calls = 4
    record, transport = _run(posts, prompt=_weapon_prompt(weapon))
    _assert_weapon_contract(record, weapon_id, render_prefix)
    # provider-call impact: budgets UNCHANGED — only the staged calls were
    # consumed (no hidden procedural re-rolls for catalog weapons).
    assert record.budget.calls == expected_calls, (
        weapon,
        transport.call_count,
        record.budget.calls,
    )
    # budgets unchanged: core stages stay inside MAX_CORE_LLM_CALLS_PER_GENERATION.
    assert record.budget.core_calls <= (
        record.budget.max_core_calls or record.budget.core_calls
    )


def test_variant_weapon_materializes_own_semantic_object_next_to_base_object():
    """Phase 19E §4 — on the office kit the RENDER asset (PROP_LETTER_OPENER_01)
    is a KIT-BASE asset (base ``letter_opener`` placement). The semantic weapon
    ``antique_brass_letter_opener`` MUST still materialize as its OWN semantic
    object + placement (reusing the render asset visually), WITHOUT
    weakening the base letter_opener and WITHOUT duplicate semantic ids."""
    record, _transport = _run(
        [
            _j(_case_people(weapon="antique_brass_letter_opener")),
            _j(_evidence(weapon_obj="antique_brass_letter_opener", murderer="paul_becker")),
            _j(_world_with("antique brass letter opener")),
        ],
        prompt=_weapon_prompt("antique brass letter opener"),
    )
    assert record.state is GenerationState.PUBLISHED
    from app.generation import pipeline

    public, _facts, _truth, _draft = pipeline.assemble(record)
    ids = {o.object_id: o.asset_id for o in public.objects}
    assert ids["antique_brass_letter_opener"] == "PROP_LETTER_OPENER_01"
    assert ids["letter_opener"] == "PROP_LETTER_OPENER_01"
    placed = [
        (p.object_id, p.asset_id, p.evidence_id)
        for p in record.draft.world_graph.placements
        if p.object_id in ("antique_brass_letter_opener", "letter_opener")
    ]
    assert {p[0] for p in placed} == {"antique_brass_letter_opener", "letter_opener"}
    # the semantic weapon is the one carrying the true match evidence.
    weapon_placement = next(p for p in placed if p[0] == "antique_brass_letter_opener")
    assert weapon_placement[2] == "d_ev_weapon_true"


def test_weapon_already_requested_by_world_is_upgraded_not_duplicated():
    """Phase 19E §2 reduced case: the model's world DID request the weapon as a
    decorative object — the injection upgrades it to REQUIRED and never emits a
    second request."""
    posts = [
        _j(_case_people(weapon="fork")),
        _j(_evidence(weapon_obj="fork", murderer="paul_becker")),
        _j({"environmentHint": "office", "objects": [{"name": "fork", "criticality": "decorative"}], "relations": [], "unsafeUnsupported": []}),
        FORk_SPEC,
    ]
    record, _transport = _run(posts, prompt=_weapon_prompt("fork"))
    assert record.state is GenerationState.PUBLISHED
    from app.generation import pipeline

    public, _facts, _truth, _draft = pipeline.assemble(record)
    forks = [o for o in public.objects if o.object_id == "fork"]
    assert len(forks) == 1  # ONE semantic object (upgraded, never duplicated)


# --------------------------------------------------------------------------- #
# B. non-weapon generated objects (decorative/interactive lane)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "expect_render_prefix"),
    [
        ("coffee mug", "proc."),
        ("umbrella", "proc."),
        ("toolbox", "proc."),
        ("briefcase", "proc."),
        ("desk fan", "proc."),
        ("suitcase", "proc."),
        ("flower pot", "PROP_PLANT_POT_01"),
    ],
)
def test_decorative_objects_resolve_without_solver_participation(name, expect_render_prefix):
    """A DECORATIVE ObjectRequest yields a stable semantic object + a render
    representation (catalog alias or validated procedural) and NEVER enters the
    weapon universe (solver participation is derived from validated semantics,
    NOT from arbitrary object-ness)."""
    from app.domain.eligibility import derive_universes
    from app.environments.manifests import load_environment
    from app.world.roles import (
        ROLE_DECORATIVE,
        ROLE_EVIDENCE_RELEVANT,
        ROLE_WEAPON_CANDIDATE,
        derive_request_roles,
    )

    provider = _generic_spec_provider()
    reqs = WorldRequirements(
        environment_hint="office",
        objects=(
            ObjectRequest(requested_name=name, criticality=CRITICALITY_DECORATIVE),
        ),
    )
    composition = compose_world(
        reqs,
        env_resolver=None,
        spec_provider=provider,
        environment_id="office",
        kit=load_environment("office"),
    )
    assert composition.issues == ()
    semantic_id = semantic_object_id(name)
    if semantic_id in {p.object_id for p in composition.placements}:
        placed = next(p for p in composition.placements if p.object_id == semantic_id)
        assert placed.asset_id.startswith(expect_render_prefix), (name, placed.asset_id)
        # the decorative render id never replaces the semantic id.
        assert placed.object_id != placed.asset_id
        spec = next(
            o for o in composition.new_objects if o.object_id == semantic_id
        )
        # DECORATIVE-only affordances: NEVER POTENTIAL_WEAPON.
        object_spec = spec
        assert "POTENTIAL_WEAPON" not in (object_spec.affordances or ())
        universe = derive_universes_from_new_objects(composition)
        assert semantic_id not in universe
    else:
        # OR a safe decorative drop: a player-safe composition note, never a
        # blocking issue and never a failure.
        assert composition.composition_notes
        assert composition.composition_notes[0].endswith("- it was left out")

    # role derivation: a decorative request is NEVER evidence-relevant/weapon.
    roles = derive_request_roles(reqs.objects[0])
    assert ROLE_DECORATIVE in roles
    assert ROLE_WEAPON_CANDIDATE not in roles
    assert ROLE_EVIDENCE_RELEVANT not in roles


def derive_universes_from_new_objects(composition) -> set[str]:
    """Weapon universe membership of JUST the new (prompt) objects: the solver
    eligibility surface is the published affordances; new decorative objects
    carry only INSPECTABLE, so none of them can be a weapon candidate."""
    return {
        spec.object_id
        for spec in composition.new_objects
        if "POTENTIAL_WEAPON" in (spec.affordances or ())
    }


def test_decorative_objects_not_solver_required_in_driver_world():
    """Integrated driver world: decorative props + the kitchen-knife weapon.
    The decorations are placed (or safely dropped) and the case still
    PUBLISHES with the SAME unique solver winner (decoration never widens the
    logical case)."""
    world = {
        "environmentHint": "office",
        "locationTokens": ["office"],
        "objects": [
            {"name": "kitchen knife", "criticality": "required"},
            {"name": "coffee mug", "criticality": "decorative"},
            {"name": "umbrella", "criticality": "decorative"},
            {"name": "toolbox", "criticality": "decorative"},
        ],
        "relations": [],
        "unsafeUnsupported": [],
    }
    posts = [
        _j(_case_people(weapon="kitchen_knife")),
        _j(_evidence(weapon_obj="kitchen_knife", murderer="paul_becker")),
        _j(world),
        GENERIC_SPEC,  # umbrella
        GENERIC_SPEC,  # toolbox
        GENERIC_SPEC,  # coffee mug
    ]
    record, _transport = _run(posts, prompt=_weapon_prompt("kitchen knife"))
    assert record.state is GenerationState.PUBLISHED
    assert record.solver_proof.weapon.winner == "kitchen_knife"
    from app.generation import pipeline

    public, _facts, _truth, _draft = pipeline.assemble(record)
    universe = {
        o.object_id for o in public.objects if "POTENTIAL_WEAPON" in o.public_affordances
    }
    assert universe == {"kitchen_knife", "letter_opener", "scissors"}


def test_decorative_placement_failure_never_fails_a_valid_case():
    """A decorated object that CANNOT be placed safely (no compatible anchor)
    is dropped with a player-safe note — the case still PUBLISHES."""
    from app.environments.manifests import load_environment

    reqs = WorldRequirements(
        environment_hint="warehouse",
        objects=(
            ObjectRequest(requested_name="coffee mug", criticality=CRITICALITY_DECORATIVE),
        ),
    )
    composition = compose_world(
        reqs,
        env_resolver=None,
        spec_provider=_generic_spec_provider(),
        environment_id="warehouse",
        kit=load_environment("warehouse"),
    )
    assert composition.issues == ()
    assert composition.composition_notes == () or any(
        "left out" in note for note in composition.composition_notes
    )


# --------------------------------------------------------------------------- #
# C. adversarial inputs (existing validators are NEVER weakened)
# --------------------------------------------------------------------------- #


def test_absurdly_long_object_name_is_rejected_or_normalized():
    with pytest.raises(ValueError):
        ObjectRequest(requested_name="a" * (122), criticality=CRITICALITY_DECORATIVE)
    # extraction safe-fails hostile locked weapons without composing them.
    extracted = extract_world_requirements(
        "Victim: x\nMurderer: y\nWeapon: a" + "b" * 2000 + "\n",
        LockedConstraints(weapon="a" + "b" * 2000),
    )
    assert not any(
        "a" * 2000 in (o.requested_name or "") for o in extracted.objects
    )


@pytest.mark.parametrize("hostile", [
    "javascript:alert(1)",
    "<script>alert(1)</script>",
    "../../../../windows/system32",
    "https://attacker.example/evil.glb",
    "file:///etc/passwd",
])
def test_hostile_object_requests_fail_safely(hostile):
    issues = safe_string_issues(hostile, "requestedName")
    assert issues
    with pytest.raises(ValueError):
        ObjectRequest(requested_name=hostile)


def test_huge_dimensions_and_nan_rejected_by_spec_validator():
    from app.world.requirements import object_request_issues

    # NaN scale never reaches the composer: the ObjectRequest itself rejects
    # the non-finite value (fail safe; no validator is weakened).
    with pytest.raises(ValueError) as excinfo:
        ObjectRequest(
            requested_name="safe cup",
            variant_params=(("scale", float("nan")),),
        )
    assert "finite" in str(excinfo.value)
    # huge dimensions are a Phase 13 bound violation (never weakened).
    from app.assets.specs import validate_asset_spec

    huge = json.loads(GENERIC_SPEC)
    huge["dimensions"] = {"x": 99999, "y": 0.1, "z": 0.1}
    assert validate_asset_spec(json.dumps(huge))


def test_excessive_primitive_count_rejected():
    from app.assets.specs import MAX_PARTS, validate_asset_spec

    spec = json.loads(GENERIC_SPEC)
    spec["parts"] = [
        {"id": f"part_{index:02d}", "role": f"r{index}", "primitive": "box",
         "transform": {"position": {"x": 0.05 * index, "y": 0, "z": 0},
                       "rotation": {"x": 0, "y": 0, "z": 0},
                       "scale": {"x": 0.02, "y": 0.02, "z": 0.02}},
         "material": "plastic"}
        for index in range(MAX_PARTS + 1)
    ]
    assert validate_asset_spec(json.dumps(spec))


def test_deeply_nested_asset_spec_rejected():
    from app.assets.depthguard import BoundedJsonError, bounded_json_loads

    deep = '{"a":' * 400 + "1" + "}" * 400
    with pytest.raises((BoundedJsonError, ValueError)):
        bounded_json_loads(deep)


def test_duplicate_semantic_ids_never_create_duplicate_placements():
    from app.environments.manifests import load_environment

    provider = _generic_spec_provider()
    reqs = WorldRequirements(
        environment_hint="office",
        objects=(
            ObjectRequest(requested_name="coffee mug", criticality=CRITICALITY_REQUIRED),
            ObjectRequest(requested_name="coffee mug", criticality=CRITICALITY_REQUIRED),
        ),
    )
    composition = compose_world(
        reqs,
        env_resolver=None,
        spec_provider=provider,
        environment_id="office",
        kit=load_environment("office"),
    )
    assert composition.issues == ()
    occurrences = [
        p for p in composition.placements if p.object_id == "coffee_mug"
    ]
    assert len(occurrences) == 1  # NEVER a duplicate placement of one semantic id


def test_unsupported_weapon_with_no_representable_geometry_fails_closed():
    """The one intentionally unsupported weapon-like object with no valid
    declarative representation FAILS CLOSED: the injection requests it, the
    procedural lane cannot pass Phase 13/Phase 17, and NOTHING publishes."""
    world = _world_with(excluded=True)
    posts = [
        _j(_case_people(weapon="fork")),
        _j(_evidence(weapon_obj="fork", murderer="paul_becker")),
        _j(world),
        "<not-json>",  # ASSET_SPEC
        "<not-json>",  # ASSET_SPEC_REPAIR 1
        "<not-json>",  # ASSET_SPEC_REPAIR 2
    ]
    record, _transport = _run(
        posts,
        prompt=_weapon_prompt("fork"),
        max_repair_passes=0,
        max_full_regenerations=0,
    )
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert record.failure_code == "VALIDATION_FAILED"


# --------------------------------------------------------------------------- #
# D. bounds + provider budgets (UNCHANGED)
# --------------------------------------------------------------------------- #


def test_world_object_bound_drops_decorative_keeps_required():
    from app.environments.manifests import load_environment

    # DISTINCT render specs so each object is its own procedural asset.
    mug_spec, umbrella_spec, fork_spec = _distinct_decor_specs()
    provider = _per_name_spec_provider(
        {"coffee mug": mug_spec, "umbrella": umbrella_spec, "fork": fork_spec}
    )
    objects = [
        ObjectRequest(requested_name="coffee mug", criticality=CRITICALITY_DECORATIVE),
        ObjectRequest(requested_name="umbrella", criticality=CRITICALITY_DECORATIVE),
        ObjectRequest(requested_name="fork", criticality=CRITICALITY_REQUIRED),
    ]
    composition = compose_world(
        WorldRequirements(environment_hint="office", objects=tuple(objects)),
        env_resolver=None,
        spec_provider=provider,
        environment_id="office",
        kit=load_environment("office"),
        max_world_objects=10,  # base (9) + 1 more only
    )
    assert composition.issues == ()
    placed_ids = {p.object_id for p in composition.placements}
    # the REQUIRED weapon keeps its slot; only DECORATIVE objects beyond the
    # bound are dropped (with player-safe notes).
    assert "fork" in placed_ids
    assert "coffee_mug" not in placed_ids
    assert "umbrella" not in placed_ids
    assert composition.composition_notes
    assert all("left out" in note for note in composition.composition_notes)


def test_world_object_bound_fails_closed_when_only_required_remain_over_bound():
    from app.environments.manifests import load_environment

    # 9 base + 8 REQUIRED procedural objects (each a DISTINCT render) all must
    # fit; bound 10 cannot be satisfied by dropping DECORATIVE (there are none)
    # -> FAIL CLOSED.
    names = {
        f"required prop {index}".casefold(): _named_spec(f"Required Prop {index}")
        for index in range(8)
    }
    provider = _per_name_spec_provider(names)
    objects = tuple(
        ObjectRequest(
            requested_name=f"required prop {index}", criticality=CRITICALITY_REQUIRED
        )
        for index in range(8)
    )
    composition = compose_world(
        WorldRequirements(environment_hint="office", objects=objects),
        env_resolver=None,
        spec_provider=provider,
        environment_id="office",
        kit=load_environment("office"),
        max_world_objects=10,
    )
    assert any("world.object-count-bound" in issue for issue in composition.issues)


def _named_spec(name: str) -> str:
    """A distinct-but-valid procedural spec (different canonical name)."""
    spec = json.loads(GENERIC_SPEC)
    spec["canonicalName"] = name
    spec["subtype"] = "prop"
    return json.dumps(spec)


def test_provider_call_impact_stays_within_budget_for_rich_worlds():
    """A rich driver world (procedural weapon + multiple procedural
    decorations with DISTINCT render specs) consumes exactly the staged
    provider calls and stays inside the documented budgets:
    MAX_LLM_CALLS_PER_GENERATION (safety ceiling 128) and
    MAX_CORE_LLM_CALLS_PER_GENERATION (core 12) are UNCHANGED and respected."""
    mug_spec, umbrella_spec, toolbox_spec = _distinct_decor_specs()
    world = {
        "environmentHint": "office",
        "objects": [
            {"name": "fork", "criticality": "required"},
            {"name": "coffee mug", "criticality": "decorative"},
            {"name": "umbrella", "criticality": "decorative"},
            {"name": "toolbox", "criticality": "decorative"},
        ],
        "relations": [],
        "unsafeUnsupported": [],
    }
    posts = [
        _j(_case_people(weapon="fork")),
        _j(_evidence(weapon_obj="fork", murderer="paul_becker")),
        _j(world),
        FORk_SPEC,      # fork          (asset call 1)
        mug_spec,       # coffee mug    (asset call 2)
        umbrella_spec,  # umbrella      (asset call 3)
        toolbox_spec,   # toolbox       (asset call 4)
    ]
    record, _transport = _run(posts, prompt=_weapon_prompt("fork"))
    assert record.state is GenerationState.PUBLISHED
    assert record.solver_proof.weapon.winner == "fork"
    budget = record.budget
    # exactly the staged calls: 3 core (case/evidence/world) + 4 ASSET_SPEC.
    assert budget.calls == 7
    assert budget.core_calls == 3
    assert budget.core_calls <= (budget.max_core_calls or budget.core_calls)
    assert budget.calls < 128  # MAX_LLM_CALLS_PER_GENERATION ceiling untouched
    from app.generation import pipeline

    public, _facts, _truth, _draft = pipeline.assemble(record)
    placed_decor = [
        o.object_id
        for o in public.objects
        if o.object_id in ("coffee_mug", "umbrella", "toolbox")
    ]
    assert len(placed_decor) == 3  # a visibly richer world, still deterministic


def _distinct_decor_specs():
    """Three DIFFERENT valid render specs so the composer sees three distinct
    procedural RENDER assets (same semantic objects, no accidental dedupe)."""
    mug = json.loads(GENERIC_SPEC)
    mug["canonicalName"] = "Coffee Mug"
    mug["subtype"] = "mug"
    umbrella = json.loads(GENERIC_SPEC)
    umbrella["canonicalName"] = "Umbrella"
    umbrella["subtype"] = "umbrella"
    toolbox = json.loads(GENERIC_SPEC)
    toolbox["canonicalName"] = "Toolbox"
    toolbox["subtype"] = "toolbox"
    return json.dumps(mug), json.dumps(umbrella), json.dumps(toolbox)


def _per_name_spec_provider(names: dict[str, str]):
    """Deterministic provider serving one DIFFERENT spec per requested name."""
    from app.assets.spec_provider import AssetSpecResponse

    class _Provider:
        def __init__(self) -> None:
            self.calls: list = []

        def generate(self, request):
            self.calls.append(getattr(request, "requested_name", "") or "")
            content = names.get(str(getattr(request, "requested_name", "") or "").casefold())
            return AssetSpecResponse(content=content)

    return _Provider()


def test_default_world_object_bound_documented():
    assert DEFAULT_MAX_WORLD_OBJECTS == 32


# --------------------------------------------------------------------------- #
# E. solver uniqueness through the pinned reveal / candidate surfaces only
# --------------------------------------------------------------------------- #


def test_solver_derives_weapon_uniquely_via_existing_surfaces():
    """The solitary solver winner is proven through the EXISTING surfaces only:

    - the solver RESULT path: ``record.solver_proof`` derives exactly one
      winner per dimension (REQUIREMENTS §31.9-§31.13);
    - the REVEAL path (the ONLY surface that names the winner): the pinned
      published payload's reveal projection (``app.services.reveal.truth_labels``
      — the same projection the reveal endpoint serves) carries the SEMANTIC
      weapon id;
    - the PRE-REVEAL candidate surface: the candidate block exposes the winner
      as an UNMARKED universe member (no field marks/ranks it, no canonical
      designation) — winner material never reaches a player DTO before reveal.
    """
    record, _transport = _run(
        [
            _j(_case_people(weapon="fork")),
            _j(_evidence(weapon_obj="fork", murderer="paul_becker")),
            _j(_world_with(excluded=True)),
            FORk_SPEC,
        ],
        prompt=_weapon_prompt("fork"),
    )
    assert record.state is GenerationState.PUBLISHED

    # 1. solver result surface — one unique winner per dimension.
    assert record.solver_proof is not None
    assert record.solver_proof.weapon.winner == "fork"
    assert record.solver_proof.weapon.unique
    assert record.solver_proof.who.winner == "paul_becker"

    # 2. reveal surface — the ONLY winner surface: the pinned payload's reveal
    #    projection names the semantic weapon id (never a render id, never a
    #    player-facing DTO of the pre-reveal investigation).
    from app.services.publication import serialize_published_payload
    from app.services.reveal import candidate_block_of, truth_labels

    payload = json.loads(
        serialize_published_payload(
            record.published,
            seed=getattr(record, "seed", None),
            prompt="fork",
            model="mock",
            title="Fork",
        )
    )
    # the reveal surface is the ONLY winner surface: it names the winner.
    labels = truth_labels(payload)
    assert labels["weaponId"] == "fork"
    assert labels["weaponName"] == "Fork"

    # 3. pre-reveal candidate surface — winner is an UNMARKED universe member.
    candidates = candidate_block_of(payload)
    weapon_ids = {entry["id"] for entry in candidates["weapons"]}
    suspect_ids = {entry["id"] for entry in candidates["suspects"]}
    assert "fork" in weapon_ids
    assert "paul_becker" in suspect_ids
    # candidate DTO fields are closed allowlists: NO field marks/ranks the
    # winner and no canonical designation is ever included (Phase7 P / N24;
    # Phase 19C/20/21B pre-reveal winner-isolation invariant).
    assert all(set(entry) == {"id", "assetId", "name"} for entry in candidates["weapons"])
    assert all(set(entry) == {"id", "name"} for entry in candidates["suspects"])
    assert all(set(entry) == {"id", "label"} for entry in candidates["motives"])
    # the PRE-REVEAL candidate DTO never carries hidden truth / proof material
    # (winner isolation: those sections exist only in the server-internal
    # payload, never in this player-facing projection).
    text = json.dumps(candidates, sort_keys=True)
    for token in ("solverProof", "truth", "report", "prompt", "universes", "locked", "crimeTime", "murdererId"):
        assert token not in text, token


# --------------------------------------------------------------------------- #
# F. non-recursive topological order of the plan graph
# --------------------------------------------------------------------------- #


def test_topological_plan_order_parents_first():
    from app.world.graph import EXIT_OK, topological_plan_order

    plans = [
        ("child_b", ("root",)),
        ("root", ()),
        ("child_a", ("root",)),
        ("leaf", ("child_b",)),
    ]
    ordered, code = topological_plan_order(
        plans, key=lambda p: p[0], edge_fn=lambda p: p[1]
    )
    assert code == EXIT_OK
    order = [name for name, _parents in ordered]
    assert order.index("root") < order.index("child_a")
    assert order.index("root") < order.index("child_b")
    assert order.index("child_b") < order.index("leaf")


def test_topological_plan_order_deep_graph_never_recurses():
    from app.world.graph import EXIT_OK, topological_plan_order

    # 2000-deep chain: iterative only (a recursive sort would RecursionError).
    plans = []
    for index in range(2000):
        parent = f"node_{index - 1}" if index else None
        plans.append((f"node_{index}", (parent,) if parent else ()))
    ordered, code = topological_plan_order(
        plans, key=lambda p: p[0], edge_fn=lambda p: p[1]
    )
    assert code == EXIT_OK
    assert len(ordered) == 2000


def test_topological_plan_order_cycle_is_total_and_deterministic():
    from app.world.graph import EXIT_CYCLE, EXIT_OK, topological_plan_order

    acyclic = [("z_leaf", ("a_root",)), ("a_root", ())]
    cyclic = [("b", ("c",)), ("c", ("b",)), ("x", ())]
    combined = [*acyclic, *cyclic]
    ordered, code = topological_plan_order(
        combined, key=lambda p: p[0], edge_fn=lambda p: p[1]
    )
    assert code == EXIT_CYCLE
    assert len(ordered) == len(combined)
    assert {p[0] for p in ordered} == {"z_leaf", "a_root", "b", "c", "x"}
    # deterministic under permutation of input order
    order_keys = [p[0] for p in ordered]
    ordered2, _ = topological_plan_order(
        list(reversed(combined)), key=lambda p: p[0], edge_fn=lambda p: p[1]
    )
    assert [p[0] for p in ordered2] == order_keys
    assert EXIT_OK != EXIT_CYCLE


# --------------------------------------------------------------------------- #
# G. deterministic extractor generalization (Weapon: line)
# --------------------------------------------------------------------------- #


def test_extractor_weapon_line_produces_required_request():
    locked = LockedConstraints(
        victim="anna_weiss",
        murderer="paul_becker",
        motive="stolen research data",
        weapon="fork",
        crime_time="23:42",
        witness="lisa_koenig",
    )
    extracted = extract_world_requirements(
        "Victim: Dr. Anna Weiss\nMurderer: Paul Becker\nMotive: stolen research data\n"
        "Weapon: fork\nTime: 23:42\nWitness: Lisa König\nLocation: office\n",
        locked,
    )
    names = {o.requested_name: o.criticality for o in extracted.objects}
    assert "fork" in names
    assert names["fork"] == CRITICALITY_REQUIRED


def test_extractor_locked_kitchen_knife_keeps_known_request():
    """The generalized locked-weapon merge never duplicates the known-table
    request (semantic-identity match) and keeps the matrix pinned output."""
    locked = LockedConstraints(weapon="Kitchen knife")
    extracted = extract_world_requirements("Nothing to see here.", locked)
    names = [o.requested_name for o in extracted.objects]
    assert names.count("kitchen knife") == 1
    request = next(o for o in extracted.objects if o.requested_name == "kitchen knife")
    assert request.criticality == CRITICALITY_REQUIRED


def test_object_count_bound_is_configurable_via_settings():
    """Settings.MAX_WORLD_OBJECTS_PER_KIT is the canonical operator knob."""
    from app.core.config import Settings

    settings = Settings(MAX_WORLD_OBJECTS_PER_KIT=28)
    assert settings.max_world_objects_per_kit == 28
    settings_default = Settings()
    assert settings_default.max_world_objects_per_kit == DEFAULT_MAX_WORLD_OBJECTS


__all__ = []  # pytest module: no accidental public names