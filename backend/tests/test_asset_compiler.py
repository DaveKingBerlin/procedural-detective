"""Phase 13 — trusted procedural asset compiler tests.

Covers the compiler contract: content-addressed deterministic ids, deterministic
part ordering by id, resolved colors (explicit sourceColor wins, else the
material table), bounded derived hitbox, byte-identical output, golden-fixture
hash pinning and the immutability of every compiled definition.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from fixtures.asset_specs import (
    CUSTOM_TROPHY_SPEC,
    GOLDEN_SPEC_CONTENT,
    GOLDEN_SPEC_NAMES,
    NORMALIZED_SPEC_HASHES,
)
from app.assets.compiler import (
    HITBOX_MIN,
    asset_id_for,
    compile_asset_spec,
    definition_json_issues,
    is_procedural_asset_id,
    spec_hash_for_cache,
    validate_embedded_definition,
)
from app.assets.materials import MATERIAL_COLORS
from app.assets.specs import (
    AssetSpec,
    AssetSpecError,
    normalize_spec,
    parse_asset_spec,
    spec_hash,
)

import app.assets.compiler as compiler_mod


def _part(role="base", primitive="box", **overrides):
    base = {
        "id": "part_00",
        "role": role,
        "primitive": primitive,
        "transform": {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
            "scale": {"x": 0.2, "y": 0.2, "z": 0.2},
        },
        "material": "plastic",
    }
    base.update(overrides)
    return base


def _part_by_id(index, **overrides):
    part = _part()
    part["id"] = "part_%02d" % index
    part.update(overrides)
    return part


def _valid_spec(**spec_overrides):
    base = {
        "canonicalName": "Test Prop",
        "category": "decor",
        "subtype": None,
        "dimensions": {"x": 0.2, "y": 0.2, "z": 0.2},
        "parts": [_part()],
    }
    base.update(spec_overrides)
    return base


def _compile(raw):
    spec = parse_asset_spec(raw, non_throwing=False)
    return compile_asset_spec(spec)


def test_valid_simple_spec_compiles():
    definition = _compile(_valid_spec())
    assert definition.compiler_version == compiler_mod.COMPILER_VERSION
    assert definition.schema_version == compiler_mod.SCHEMA_VERSION
    assert is_procedural_asset_id(definition.asset_id)
    assert definition.asset_id.startswith("proc.decor.")
    assert len(definition.asset_id) == len("proc.decor.") + 16
    assert definition.canonical_name == "Test Prop"
    assert definition.dimensions.to_dict() == {"x": 0.2, "y": 0.2, "z": 0.2}
    assert definition.subtype is None
    assert len(definition.parts) == 1
    part = definition.parts[0]
    assert part.id == "part_00"
    assert part.role == "base"
    assert part.primitive == "box"
    assert part.parent_id is None


def test_multi_part_parented_object_compiles():
    """Trophy-style object: base -> stem -> cup (depth 2). Every parent appears
    EARLIER in the input (the parse rule); the compiler emits id-ordered parts."""
    parts = [
        _part_by_id(0, role="base"),
        _part_by_id(1, role="stem", parentId="part_00"),
        _part_by_id(2, role="cup", parentId="part_01"),
    ]
    definition = _compile(_valid_spec(parts=parts))
    assert [p.id for p in definition.parts] == ["part_00", "part_01", "part_02"]
    assert definition.parts[1].parent_id == "part_00"
    assert definition.parts[2].parent_id == "part_01"


def test_parts_ordered_by_id_regardless_of_input():
    raw = _valid_spec(
        parts=[_part_by_id(2, role="c"), _part_by_id(1, role="b"), _part_by_id(0, role="a")]
    )
    definition = _compile(raw)
    assert [p.id for p in definition.parts] == ["part_00", "part_01", "part_02"]


def test_deterministic_asset_id_and_hash_formula():
    a = _compile(_valid_spec())
    b = _compile(_valid_spec())
    assert a.asset_id == b.asset_id
    spec = parse_asset_spec(_valid_spec(), non_throwing=False)
    assert spec_hash(spec) == spec_hash(parse_asset_spec(_valid_spec(), non_throwing=False))

    digest = hashlib.sha256(
        (
            f"{normalize_spec(spec)}:compilerVersion:{compiler_mod.COMPILER_VERSION}:"
            f"schemaVersion:{compiler_mod.SCHEMA_VERSION}"
        ).encode("utf-8")
    ).hexdigest()[:16]
    assert a.asset_id == "proc.decor.%s" % digest


def test_different_category_yields_different_id():
    a = _compile(_valid_spec(category="decor"))
    b = _compile(_valid_spec(category="utility"))
    assert a.asset_id != b.asset_id


def test_different_compiler_version_yields_different_id():
    spec = parse_asset_spec(_valid_spec(), non_throwing=False)
    assert asset_id_for(spec) != asset_id_for(spec, compiler_version=2, schema_version=1)


def test_same_input_identical_output_bytes():
    a = _compile(_valid_spec())
    b = _compile(_valid_spec())
    assert a.to_json_bytes() == b.to_json_bytes()
    assert a.to_definition_json() == b.to_definition_json()
    parsed_back = json.loads(a.to_json_bytes().decode("utf-8"))
    assert parsed_back["assetId"] == a.asset_id
    assert parsed_back["parts"][0]["color"] == MATERIAL_COLORS["plastic"]["color"]


def test_definition_json_contract_keys():
    data = _compile(_valid_spec()).to_definition_json()
    assert set(data) == {
        "compilerVersion", "schemaVersion", "assetId", "canonicalName",
        "category", "subtype", "dimensions", "parts", "hitbox",
    }
    part = data["parts"][0]
    assert set(part) == {"id", "role", "primitive", "transform", "color", "parentId"}
    assert set(part["transform"]) == {"position", "rotation", "scale"}
    assert set(part["transform"]["position"]) == {"x", "y", "z"}
    assert set(data["hitbox"]) == {"scale"}


def test_color_resolution_prefers_source_color():
    definition = _compile(_valid_spec(parts=[_part(sourceColor="#ff0000")]))
    assert definition.parts[0].color == "#ff0000"


def test_color_resolution_falls_back_to_material():
    definition = _compile(_valid_spec(parts=[_part(material="wood.dark")]))
    assert definition.parts[0].color == MATERIAL_COLORS["wood.dark"]["color"]


def test_hitbox_derived_and_bounded():
    definition = _compile(_valid_spec(parts=[_part()]))
    h = definition.hitbox.scale.to_dict()
    assert h["x"] == pytest.approx(0.2, abs=1e-9)
    for value in h.values():
        assert 0.15 <= value <= 10.0


def test_hitbox_minimum_pickable_clamp():
    raw = _valid_spec(
        dimensions={"x": 0.05, "y": 0.05, "z": 0.05},
        parts=[{
            **_part(),
            "transform": {
                "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                "scale": {"x": 0.05, "y": 0.05, "z": 0.05},
            },
        }],
    )
    definition = _compile(raw)
    h = definition.hitbox.scale.to_dict()
    assert h["x"] >= HITBOX_MIN and h["y"] >= HITBOX_MIN and h["z"] >= HITBOX_MIN


def test_compile_rejects_non_spec_input():
    with pytest.raises(TypeError):
        compile_asset_spec({"nope": True})


def test_direct_construction_cannot_bypass_validation():
    with pytest.raises(AssetSpecError):
        AssetSpec(
            canonical_name="X",
            category="not-a-category",
            subtype=None,
            dimensions=(0.2, 0.2, 0.2),
            parts=(),
        )


# --------------------------------------------------------------------------- #
# golden fixture compilation + pinned hashes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", GOLDEN_SPEC_NAMES)
def test_golden_example_compiles_and_hash_is_pinned(name):
    raw = GOLDEN_SPEC_CONTENT[name.casefold().strip()]
    spec = parse_asset_spec(raw, non_throwing=False)
    assert spec_hash(spec) == NORMALIZED_SPEC_HASHES[name]  # stable across runs
    definition = compile_asset_spec(spec)
    assert is_procedural_asset_id(definition.asset_id)
    assert definition.asset_id.startswith("proc." + spec.category + ".")
    assert len(definition.parts) == len(spec.parts)
    assert definition_json_issues(definition.to_definition_json()) == ()
    assert validate_embedded_definition(definition.asset_id, definition.to_definition_json()) is not None


def test_trophy_example_part_count_and_depth():
    spec = parse_asset_spec(CUSTOM_TROPHY_SPEC, non_throwing=False)
    assert len(spec.parts) == 3
    definition = compile_asset_spec(spec)
    assert [p.id for p in definition.parts] == ["part_00", "part_01", "part_02"]
    assert definition.parts[2].parent_id == "part_01"  # depth-2 chain exercised


def test_compiler_id_stable_across_fresh_compile_pipelines():
    a = _compile(_valid_spec(canonicalName="Stable Name"))
    b = _compile(_valid_spec(canonicalName="Stable Name"))
    assert a.asset_id == b.asset_id
    assert a.to_json_bytes() == b.to_json_bytes()


def test_definition_is_immutable():
    definition = _compile(_valid_spec())
    with pytest.raises(AttributeError):
        definition.asset_id = "proc.evil.deadbeef"
    with pytest.raises(AttributeError):
        definition.parts[0].color = "#000000"


def test_cached_definition_is_the_same_frozen_instance():
    from app.assets.compiler import GeneratedAssetDefinition
    from app.assets.generated_cache import GeneratedAssetCache

    raw = _valid_spec(canonicalName="shared instance")
    spec = parse_asset_spec(raw, non_throwing=False)
    definition = compile_asset_spec(spec)
    cache = GeneratedAssetCache(max_entries=8)
    key = spec_hash_for_cache(spec)
    cache.put(key, definition)
    hit = cache.get(key)
    assert hit is not None
    cached_def, _meta = hit
    assert cached_def is definition  # SAME frozen instance, never copied
    assert isinstance(cached_def, GeneratedAssetDefinition)


# --------------------------------------------------------------------------- #
# DEF-069 — embedded-definition gate: bounded proc.* assetId grammar
# --------------------------------------------------------------------------- #


def _definition_with_asset_id(asset_id: str):
    """A valid definition document with a tampered (oversized) assetId."""
    raw = _valid_spec()
    spec = parse_asset_spec(raw, non_throwing=False)
    document = compile_asset_spec(spec).to_definition_json()
    document["assetId"] = asset_id
    return document


def test_embedded_definition_gate_rejects_oversized_asset_id():
    """DEF-069: a tampered 222-char assetId yields a CLEAN issue from
    ``definition_json_issues`` and the placement's definition is skipped
    (``validate_embedded_definition`` returns None) — never projected."""
    from app.assets.compiler import PROCEDURAL_ASSET_ID_MAX_LENGTH

    assert PROCEDURAL_ASSET_ID_MAX_LENGTH == 128
    long_id = "proc.decor." + "a" * 222
    document = _definition_with_asset_id(long_id)
    issues = definition_json_issues(document, expected_asset_id=long_id)
    assert any("assetId exceeds" in issue and "128" in issue for issue in issues)
    assert validate_embedded_definition(long_id, document) is None


def test_embedded_definition_gate_rejects_long_category_segment():
    """DEF-069: a proc.* category segment above 64 chars is a clean issue (the
    whole id must still fit the client grammar `proc.<cat>.<16hex>`)."""
    from app.assets.compiler import PROCEDURAL_ASSET_CATEGORY_MAX_LENGTH

    assert PROCEDURAL_ASSET_CATEGORY_MAX_LENGTH == 64
    long_category = "x" * 80
    asset_id = "proc." + long_category + "." + "abcdef0123456789"
    document = _definition_with_asset_id(asset_id)
    issues = definition_json_issues(document, expected_asset_id=asset_id)
    assert any(
        "category segment exceeds" in issue and "64" in issue for issue in issues
    )
    assert validate_embedded_definition(asset_id, document) is None


def test_real_compiled_asset_ids_bounded_and_match_grammar():
    """DEF-069 compiler guarantee: EVERY real compiled assetId (all catalog
    categories + the golden fixtures) stays <= 33 chars and matches the client
    grammar — the longest possible real id (proc.electronics. + 16 hex) is
    exactly 33."""
    import re

    from fixtures.asset_specs import GOLDEN_SPEC_CONTENT, GOLDEN_SPEC_NAMES
    from app.assets.compiler import (
        PROCEDURAL_ASSET_PATTERN,
        PROCEDURAL_ASSET_ID_MAX_LENGTH,
    )

    grammar = re.compile(r"^proc\.[a-z0-9_]+\.[a-f0-9]{16}$")
    assert PROCEDURAL_ASSET_PATTERN.pattern == grammar.pattern
    ids: list[str] = []
    for name in GOLDEN_SPEC_NAMES:
        spec = parse_asset_spec(GOLDEN_SPEC_CONTENT[name.casefold().strip()], non_throwing=False)
        ids.append(compile_asset_spec(spec).asset_id)
    for category in ("evidence", "electronics", "furniture", "structural",
                     "character", "decor", "utility"):
        raw = _valid_spec(canonicalName=f"{category} prop", category=category)
        ids.append(_compile(raw).asset_id)
    for asset_id in ids:
        assert len(asset_id) <= 33, asset_id
        assert grammar.match(asset_id), asset_id
    assert len(ids) == 4 + 7
    assert max(len(i) for i in ids) == 33  # proc.electronics.<16hex>