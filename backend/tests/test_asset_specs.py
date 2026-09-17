"""Phase 13 — declarative AssetSpec schema + strict parser tests.

Covers the documented strict rules: the renderer-supported primitive allowlist
(reject, never coerce), part/role id grammars, numeric finiteness + bounds,
parent ordering + depth <= 2, duplicate ids, string-safety scans (URLs, paths,
scripts), unknown keys, array bounds, and the deterministic canonical form.

The adversarial battery (code strings, JS URLs, huge arrays, 1e308 numbers,
negative scale, recursion/parent chains, material injection, duplicate ids)
is asserted REJECTED — parse returns None / raises a typed ``AssetSpecError``.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.assets.specs import (
    AssetSpecError,
    AssetSpecPart,
    MAX_CANONICAL_NAME_LENGTH,
    MAX_PARTS,
    normalize_spec,
    parse_asset_spec,
    spec_hash,
    validate_asset_spec,
)
from app.assets.glyphs import FORMAT_GLYPH_RANGES


def _valid_part(index: int = 0, **overrides) -> dict:
    part_id = f"part_{index:02d}"
    base: dict = {
        "id": part_id,
        "role": "base",
        "primitive": "box",
        "transform": {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
            "scale": {"x": 0.2, "y": 0.2, "z": 0.2},
        },
        "material": "plastic",
    }
    base.update(overrides)
    return base


def _valid_spec(parts: list | None = None, **overrides) -> dict:
    base: dict = {
        "canonicalName": "Test Prop",
        "category": "decor",
        "subtype": None,
        "dimensions": {"x": 0.2, "y": 0.2, "z": 0.2},
        "parts": parts if parts is not None else [_valid_part(0)],
    }
    base.update(overrides)
    return base


def _part(spec: dict, index: int, **overrides) -> dict:
    """Return a deep copy of the spec's parts[index] with overrides applied."""
    return copy.deepcopy(_valid_part(index, **{**spec["parts"][index], **overrides}))


# --------------------------------------------------------------------------- #
# valid spec parses
# --------------------------------------------------------------------------- #


def test_valid_simple_spec_parses():
    spec = _valid_spec()
    parsed = parse_asset_spec(spec, non_throwing=False)
    assert parsed is not None
    assert parsed.canonical_name == "Test Prop"
    assert parsed.category == "decor"
    assert parsed.subtype is None
    assert parsed.dimensions == (0.2, 0.2, 0.2)
    assert len(parsed.parts) == 1
    assert parsed.parts[0].id == "part_00"
    assert parsed.parts[0].primitive == "box"
    assert parsed.parts[0].material == "plastic"
    assert parsed.parts[0].source_color is None
    assert parsed.parts[0].parent_id is None


def test_valid_spec_accepts_json_string_form():
    spec = json.dumps(_valid_spec())
    parsed = parse_asset_spec(spec, non_throwing=False)
    assert parsed is not None


def test_valid_spec_accepts_wrapper_object():
    spec = {"assetSpec": _valid_spec()}
    parsed = parse_asset_spec(spec, non_throwing=False)
    assert parsed is not None and parsed.category == "decor"


def test_multi_part_parented_object_parses():
    """The trophy-style object (base -> stem -> cup) is at the max depth-2."""
    parts = [
        _valid_part(0, role="base", material="wood.dark"),
        _valid_part(1, role="stem", parentId="part_00"),
        _valid_part(2, role="cup", parentId="part_01"),
    ]
    parsed = parse_asset_spec(_valid_spec(parts=parts), non_throwing=False)
    assert len(parsed.parts) == 3
    assert parsed.parts[2].parent_id == "part_01"


def test_all_supported_primitives_parse():
    for primitive in ("box", "cylinder", "sphere", "plane"):
        parsed = parse_asset_spec(
            _valid_spec(canonicalName=f"P {primitive}", parts=[_valid_part(0, primitive=primitive)]),
            non_throwing=False,
        )
        assert parsed.parts[0].primitive == primitive


# --------------------------------------------------------------------------- #
# rejection (never coerce)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "primitive",
    ["capsule", "extruded_polygon", "fancy_chunk", "unknown", "Box", "boxx"],
)
def test_unknown_primitive_rejected(primitive):
    issues = validate_asset_spec(_valid_spec(parts=[_valid_part(0, primitive=primitive)]))
    assert any("primitive" in issue and ("not supported" in issue or "not in" in issue) for issue in issues)
    assert parse_asset_spec(_valid_spec(parts=[_valid_part(0, primitive=primitive)])) is None


def test_too_many_parts_rejected():
    parts = [_valid_part(i) for i in range(MAX_PARTS + 1)]
    issues = validate_asset_spec(_valid_spec(parts=parts))
    assert any(f"maximum of {MAX_PARTS}" in issue for issue in issues)


def test_empty_parts_rejected():
    issues = validate_asset_spec(_valid_spec(parts=[]))
    assert any("at least 1 part" in issue for issue in issues)


@pytest.mark.parametrize(
    "axis,value",
    [
        ("x", float("nan")),
        ("y", float("inf")),
        ("z", float("-inf")),
    ],
)
def test_nan_inf_rejected(axis, value):
    transform = copy.deepcopy(_valid_part(0)["transform"])
    transform["position"][axis] = value
    spec = _valid_spec(parts=[{**_valid_part(0), "transform": transform}])
    assert validate_asset_spec(spec)
    assert parse_asset_spec(spec) is None


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda p: p.update({"transform": {**p["transform"], "scale": {"x": 0.001, "y": 0.001, "z": 0.001}}}), "scale"),  # too small
        (lambda p: p.update({"transform": {**p["transform"], "scale": {"x": 5.0, "y": 5.0, "z": 5.0}}}), "scale"),  # too big
        (lambda p: p.update({"transform": {**p["transform"], "position": {"x": 5.0, "y": 0.0, "z": 0.0}}}), "position"),  # too far
        (lambda p: p.update({"transform": {**p["transform"], "position": {"x": 1e308, "y": 0.0, "z": 0.0}}}), "within"),  # 1e308
        (lambda p: p.update({"transform": {**p["transform"], "scale": {"x": -0.5, "y": 0.2, "z": 0.2}}}), "negative"),  # negative scale
    ],
)
def test_extreme_dimensions_rejected(mutate, needle):
    part = _valid_part(0)
    mutate(part)
    spec = _valid_spec(parts=[part])
    assert validate_asset_spec(spec)
    assert parse_asset_spec(spec) is None


def test_extreme_overall_dimensions_rejected():
    spec = _valid_spec(dimensions={"x": 20.0, "y": 0.2, "z": 0.2})
    assert validate_asset_spec(spec)
    assert parse_asset_spec(spec) is None
    spec = _valid_spec(dimensions={"x": 0.001, "y": 0.2, "z": 0.2})
    assert validate_asset_spec(spec)
    assert parse_asset_spec(spec) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("canonicalName", "C:\\x"),
        ("canonicalName", "/etc/passwd"),
        ("canonicalName", "http://evil.example/x"),
        ("canonicalName", "javascript:alert(1)"),
        ("canonicalName", "../.."),
    ],
)
def test_path_and_url_fields_rejected(field, value):
    spec = _valid_spec()
    spec[field] = value
    assert validate_asset_spec(spec)
    assert parse_asset_spec(spec) is None


def test_path_and_url_in_part_fields_rejected():
    # position embedded with a path string
    part = _valid_part(0)
    part["transform"]["position"] = "/etc/passwd"
    assert validate_asset_spec(_valid_spec(parts=[part]))
    # color "url(...)"
    part2 = _valid_part(0)
    part2["primitive"] = "box"
    part2["material"] = "plastic"
    part2["sourceColor"] = "url(https://evil/x)"
    # built a variant through the sourceColor field (rejected: not hex)
    spec2 = _valid_spec(parts=[part2])
    assert validate_asset_spec(spec2)


@pytest.mark.parametrize(
    "field,value",
    [
        ("canonicalName", "<script>alert(1)</script>"),
        ("subtype", "function(){x}"),
        ("canonicalName", "eval('x')"),
    ],
)
def test_script_and_shader_fields_rejected(field, value):
    spec = _valid_spec()
    spec[field] = value
    assert validate_asset_spec(spec)
    assert parse_asset_spec(spec) is None


def test_script_in_role_and_material_injection_rejected():
    # role "onload"
    spec = _valid_spec(parts=[_valid_part(0, role="onload")])
    assert validate_asset_spec(spec)
    # material injection: not a real token -> rejected by the vocabulary AND the token is not a word-token script
    spec2 = _valid_spec(parts=[_valid_part(0, material="wood.dark;alarm(1)")])
    issues = validate_asset_spec(spec2)
    assert any("material" in issue and "not in MATERIAL_VOCABULARY" in issue for issue in issues)


def test_parent_chain_depth_over_2_rejected():
    parts = [
        _valid_part(0, role="a"),
        _valid_part(1, role="b", parentId="part_00"),
        _valid_part(2, role="c", parentId="part_01"),
        _valid_part(3, role="d", parentId="part_02"),  # depth 3
    ]
    spec = _valid_spec(parts=parts)
    issues = validate_asset_spec(spec)
    assert any("nesting depth of 2" in issue for issue in issues)
    assert parse_asset_spec(spec) is None


def test_parent_referencing_later_part_rejected():
    parts = [
        _valid_part(0, role="a", parentId="part_02"),  # later part
        _valid_part(1, role="b"),
        _valid_part(2, role="c"),
    ]
    spec = _valid_spec(parts=parts)
    issues = validate_asset_spec(spec)
    assert any("EARLIER" in issue for issue in issues)
    assert parse_asset_spec(spec) is None


def test_parent_referencing_unknown_or_self_rejected():
    spec = _valid_spec(parts=[_valid_part(0, parentId="part_99")])
    assert validate_asset_spec(spec)
    spec2 = _valid_spec(parts=[_valid_part(0, parentId="part_00")])
    issues = validate_asset_spec(spec2)
    # self reference: reported as "cannot reference itself" or earlier-part
    assert issues


def test_duplicate_part_ids_rejected():
    parts = [_valid_part(0), _valid_part(0)]
    issues = validate_asset_spec(_valid_spec(parts=parts))
    assert any("duplicate" in issue for issue in issues)


def test_unknown_keys_rejected():
    spec = _valid_spec()
    spec["evil"] = True
    assert validate_asset_spec(spec)
    part = _valid_part(0)
    part["handler"] = "onclick"
    assert validate_asset_spec(_valid_spec(parts=[part]))


def test_non_object_shapes_rejected():
    for bad in (None, 42, "not-json", ["x"], True):
        assert validate_asset_spec(bad)
        assert parse_asset_spec(bad) is None


def test_invalid_json_string_rejected():
    assert validate_asset_spec("{not json")
    assert parse_asset_spec("{not json") is None


def test_duplicate_json_keys_rejected():
    spec = _valid_spec()
    raw = json.dumps(spec)
    # Append a duplicate "category" key -> must be rejected, never last-win.
    dup = raw[:-1] + ',"category":"evidence"}'
    assert parse_asset_spec(dup) is None
    assert validate_asset_spec(dup)


def test_control_characters_rejected():
    spec = _valid_spec(canonicalName="bad\x00name\x1f")
    assert validate_asset_spec(spec)


def test_oversized_strings_rejected():
    spec = _valid_spec(canonicalName="a" * (MAX_CANONICAL_NAME_LENGTH + 1))
    assert validate_asset_spec(spec)
    spec2 = _valid_spec(parts=[_valid_part(0, role="r" * 25)])
    assert validate_asset_spec(spec2)


def test_part_id_out_of_range_rejected():
    spec = _valid_spec(parts=[_valid_part(99)])  # part_99 beyond part_23
    assert validate_asset_spec(spec)
    assert parse_asset_spec(spec) is None


def test_material_not_in_vocabulary_rejected():
    spec = _valid_spec(parts=[_valid_part(0, material="obsidian")])
    assert validate_asset_spec(spec)
    assert parse_asset_spec(spec) is None


def test_non_throwing_and_raising_behavior():
    assert parse_asset_spec(_valid_spec(parts=[_valid_part(0, primitive="nope")])) is None
    with pytest.raises(AssetSpecError):
        parse_asset_spec(_valid_spec(parts=[_valid_part(0, primitive="nope")]), non_throwing=False)
    with pytest.raises(AssetSpecError):
        # Direct constructor cannot bypass validation (catalog.py pattern).
        AssetSpecPart(id="part_00", role="base", primitive="not-a-prime",
                      transform=None, material="plastic")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# deterministic canonical form
# --------------------------------------------------------------------------- #


def test_normalize_spec_deterministic_and_order_independent():
    # Two valid sibling-only specs whose parts are ordered differently MUST normalize to
    # the SAME canonical form (the canonical dict re-sorts parts by id).
    parts_a = [_valid_part(0, role="a"), _valid_part(1, role="b")]
    parts_shuffled = [_valid_part(1, role="b"), _valid_part(0, role="a")]
    spec_a = parse_asset_spec(_valid_spec(parts=parts_a), non_throwing=False)
    spec_b = parse_asset_spec(_valid_spec(parts=parts_shuffled), non_throwing=False)
    assert normalize_spec(spec_a) == normalize_spec(spec_b)
    assert spec_hash(spec_a) == spec_hash(spec_b)
    # A parented spec is canonicalized the same way: parse once, normalize twice,
    # and build a second spec with the SAME content (identical canonical text).
    parented = _valid_spec(
        parts=[_valid_part(0, role="a"), _valid_part(1, role="b", parentId="part_00")]
    )
    once = parse_asset_spec(parented, non_throwing=False)
    twice = parse_asset_spec(copy.deepcopy(parented), non_throwing=False)
    assert normalize_spec(once) == normalize_spec(twice)


def test_normalize_spec_changes_with_category():
    a = parse_asset_spec(_valid_spec(category="decor"), non_throwing=False)
    b = parse_asset_spec(_valid_spec(category="utility"), non_throwing=False)
    assert normalize_spec(a) != normalize_spec(b)


# --------------------------------------------------------------------------- #
# adversarial battery (hostile AssetSpec)
# --------------------------------------------------------------------------- #


HOSTILE_BATTERY: list[tuple[str, dict]] = []


def _battery(label: str, mutator) -> None:
    spec = _valid_spec()
    mutator(spec)
    HOSTILE_BATTERY.append((label, spec))


_battery("code string canonicalName", lambda s: s.update(canonicalName="import os; eval('boom')"))
_battery(
    "JS in sourceColor",
    lambda s: s.update(parts=[{**_valid_part(0), "sourceColor": "javascript:window.onerror=alert"}]),  # type: ignore[dict-item]
)
_battery("JS url subtype", lambda s: s.update(subtype="https://evil/payload.js"))
HOSTILE_BATTERY.append(
    ("huge array (25 parts)",
     _valid_spec(parts=[_valid_part(i) for i in range(25)]))
)
_battery("1e308 position", lambda s: s.update(
    parts=[{**_valid_part(0), "transform": {"position": {"x": 1e308, "y": 0.0, "z": 0.0},
                                            "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                                            "scale": {"x": 0.2, "y": 0.2, "z": 0.2}}}]
))
_battery("negative scale", lambda s: s.update(
    parts=[{**_valid_part(0), "transform": {"position": {"x": 0.0, "y": 0.0, "z": 0.0},
                                            "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                                            "scale": {"x": -2.0, "y": 0.2, "z": 0.2}}}]
))
_battery("recursion parent chain", lambda s: s.update(
    parts=[
        _valid_part(0, role="a"),
        _valid_part(1, role="b", parentId="part_00"),
        _valid_part(2, role="c", parentId="part_01"),
        _valid_part(3, role="d", parentId="part_02"),
    ]
))
_battery("material injection", lambda s: s.update(parts=[_valid_part(0, material="wood.dark;drop table x")]))
_battery("duplicate ids", lambda s: s.update(parts=[_valid_part(0), _valid_part(0)]))
_battery("shader token role", lambda s: s.update(parts=[_valid_part(0, role="shader")]))


@pytest.mark.parametrize("label,spec", HOSTILE_BATTERY, ids=[h[0] for h in HOSTILE_BATTERY])
def test_hostile_asset_spec_battery_rejected(label, spec):
    assert validate_asset_spec(spec), f"{label} should be REJECTED"
    assert parse_asset_spec(spec) is None, f"{label} should NOT parse"


# --------------------------------------------------------------------------- #
# DEF-067 — deep nesting bombs (reject cleanly; NEVER RecursionError)
# --------------------------------------------------------------------------- #

_DEEP_JSON_BOMB = "[" * 10000 + "]" * 10000  # 20 KB, well under the 262144 cap


def _bomb_spec_provider(content: str = _DEEP_JSON_BOMB):
    """A spec provider returning a deep-nesting JSON bomb (QA probe shape)."""
    from app.assets.spec_provider import AssetSpecResponse

    class _BombProvider:
        def generate(self, request):  # noqa: ANN001
            return AssetSpecResponse(content=content)

    return _BombProvider()


def test_nesting_bomb_string_rejected_with_clean_issue():
    """DEF-067: ``"[ "*10000``-style JSON yields a clean issue from BOTH
    validator and parser (never a RecursionError)."""
    issues = validate_asset_spec(_DEEP_JSON_BOMB)
    assert issues, "deep nesting bomb must produce issues"
    assert any("exceeds the maximum" in issue for issue in issues)
    assert parse_asset_spec(_DEEP_JSON_BOMB, non_throwing=True) is None
    with pytest.raises(AssetSpecError):
        parse_asset_spec(_DEEP_JSON_BOMB, non_throwing=False)


def test_nesting_bomb_rejected_cleanly_at_oracle_entry_points():
    """DEF-067: resolve_or_generate DEGRADES (original FALLBACK + sanitized
    error) and generate_and_stage raises the TYPED error — never a raw
    RecursionError (the QA recursion probe shape)."""
    from app.assets.catalog import load_catalog_from_repo
    from app.assets.oracle import AssetGenerationError, GeneratedAssetOracle

    oracle = GeneratedAssetOracle(catalog=load_catalog_from_repo())
    outcome = oracle.resolve_or_generate(
        {"requestedName": "bomb"},
        spec_provider=_bomb_spec_provider(),
        force_generate=True,
    )
    assert outcome.resolution is not None
    assert outcome.generated is None
    assert outcome.error is not None and "nesting" in outcome.error
    with pytest.raises(AssetGenerationError):
        oracle.generate_and_stage({"requestedName": "bomb"}, _bomb_spec_provider())


def test_deeply_nested_python_structure_rejected_cleanly():
    """DEF-067: a ~3000-deep PYTHON dict is rejected with a deterministic issue
    (the ``bounded_structure_depth`` pre-check) — never a RecursionError —
    from both the validator and the parser."""
    deep: dict = {}
    cursor = deep
    for _ in range(3000):
        cursor["a"] = {}
        cursor = cursor["a"]
    issues = validate_asset_spec(deep)
    assert issues
    assert any("nesting depth" in issue and "exceeds the maximum" in issue for issue in issues)
    assert parse_asset_spec(deep, non_throwing=True) is None


def test_depth_32_spec_still_accepted_by_the_guard():
    """DEF-067: the guard fires ONLY above MAX_STRUCT_NESTING (32) — a
    structure nesting EXACTLY 32 levels deep produces NO nesting-depth issue;
    a 33-level structure fires the deterministic issue."""
    from app.assets.depthguard import MAX_STRUCT_NESTING

    assert MAX_STRUCT_NESTING == 32

    def _deep_parts(extra_levels: int):
        """A nested-array chain under ``parts``; returns the OUTER list. The
        dict root contributes 1 level; each nested array adds exactly 1."""
        tail: list = []
        current = tail
        for _ in range(extra_levels):
            nxt: list = []
            current.append(nxt)
            current = nxt
        return tail

    # 30 extra array levels -> total nesting 32 (accepted: no depth issue).
    at_limit = _valid_spec(canonicalName="Edge", parts=_deep_parts(30))
    issues = validate_asset_spec(at_limit)
    assert not any("nesting depth" in issue for issue in issues)
    # one more level -> total nesting 33 (rejected by the guard).
    over_limit = _valid_spec(canonicalName="Edge", parts=_deep_parts(31))
    issues2 = validate_asset_spec(over_limit)
    assert any("nesting depth" in issue and "exceeds the maximum" in issue for issue in issues2)


def test_bounded_json_loads_rejects_before_the_decoder():
    """DEF-067: ``bounded_json_loads`` rejects a deep JSON string with a clean
    ``BoundedJsonError`` BEFORE ``json.loads`` ever runs (the bracket scan is
    iterative and string-aware)."""
    from app.assets.depthguard import (
        BoundedJsonError,
        bounded_json_loads,
        bracket_depth,
    )

    assert bracket_depth(_DEEP_JSON_BOMB) > 32
    with pytest.raises(BoundedJsonError):
        bounded_json_loads(_DEEP_JSON_BOMB)
    # a normal spec JSON string still decodes fine.
    normal = json.dumps(_valid_spec())
    assert isinstance(bounded_json_loads(normal), dict)


def test_bracket_depth_ignores_brackets_inside_string_literals():
    from app.assets.depthguard import bracket_depth

    # Only the top-level { } pairs bracket semantically: the [ and {{{
    # inside string literals must NOT count.
    payload = '{"name": "a[b]c", "note": "[[[", "x": 1}'
    assert bracket_depth(payload) == 1
    payload2 = '{"k": "escaped \\" quote [ still string", "n": [[1]]}'
    assert bracket_depth(payload2) == 3


# --------------------------------------------------------------------------- #
# DEF-068 — Unicode format/zero-width/Bidi/line-separator glyphs rejected
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "glyph,glyph_name",
    [
        ("\u200b", "U+200B"),  # zero-width space
        ("\u202e", "U+202E"),  # right-to-left override (bidi)
        ("\u2028", "U+2028"),  # line separator
        ("\ufeff", "U+FEFF"),  # byte-order mark
    ],
)
def test_unicode_format_control_glyphs_rejected(glyph, glyph_name):
    """DEF-068: U+200B / U+202E / U+2028 / U+FEFF in any spec string is a
    deterministic parse issue — the string-safety scan rejects the WHOLE
    format/zero-width/Bidi/line-separator glyph class (never a silent pass)."""
    poisoned = f"Name{glyph}WithGlyph"
    spec = _valid_spec(canonicalName=poisoned)
    issues = validate_asset_spec(spec)
    matches = [i for i in issues if "Unicode format/zero-width" in i and glyph_name in i]
    assert matches, f"expected a {glyph_name} glyph issue; got {issues}"
    assert parse_asset_spec(spec) is None
    # a role and subtype carrying a glyph are equally rejected.
    part = _valid_part(0, role="base")
    part["role"] = f"base{glyph}"
    issues2 = validate_asset_spec(_valid_spec(parts=[part]))
    assert any("Unicode format/zero-width" in i for i in issues2)


def test_unicode_glyph_class_is_frozen_and_clean_names_pass():
    """DEF-068: the documented glyph class is exactly the spec'd codepoint
    ranges, and a clean ASCII spec never triggers the glyph issue."""
    assert FORMAT_GLYPH_RANGES == (
        (0x200B, 0x200F),
        (0x2028, 0x2028),
        (0x2029, 0x2029),
        (0x202A, 0x202E),
        (0x2060, 0x2064),
        (0xFEFF, 0xFEFF),
    )
    assert validate_asset_spec(_valid_spec()) == ()


def test_unicode_glyph_class_rejects_every_documented_chart_range():
    """Every codepoint in the frozen ranges is rejected (boundary coverage)."""
    for low, high in FORMAT_GLYPH_RANGES:
        for codepoint in (low, high):
            glyph = chr(codepoint)
            spec = _valid_spec(canonicalName=f"g{glyph}")
            issues = validate_asset_spec(spec)
            assert any(
                "Unicode format/zero-width" in i and f"U+{codepoint:04X}".lower() in i.lower()
                for i in issues
            ), f"codepoint U+{codepoint:04X} not rejected"
