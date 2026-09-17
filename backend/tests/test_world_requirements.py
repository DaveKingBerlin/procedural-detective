"""Phase 14 — WorldRequirements contract tests (pure unit; no DB, no network).

Covers the typed ``WorldRequirements`` / ``ObjectRequest`` / ``PlacementRelation``
contract: the documented bounds (name <= 120, tags <= 8, objects <= 24,
relations <= 12), the no-URL/path/control/format-glyph string gate, the variant
params bounded surface and the relation -> anchor TYPE mapping.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assets.glyphs import FORMAT_GLYPH_CHARS  # noqa: E402
from app.world.requirements import (  # noqa: E402
    MAX_CAPABILITIES,
    MAX_OBJECT_REQUESTS,
    MAX_RELATIONS,
    MAX_REQUESTED_NAME_LENGTH,
    MAX_TAGS,
    ObjectRequest,
    PlacementRelation,
    RELATION_KINDS,
    RELATION_TO_ANCHOR_TYPES,
    WorldRequirements,
    object_request_issues,
    safe_string_issues,
)


def _dummy_request(name="kitchen knife"):
    return ObjectRequest(requested_name=name)


# --------------------------------------------------------------------------- #
# ObjectRequest bounds
# --------------------------------------------------------------------------- #


def test_object_request_valid_construction():
    request = ObjectRequest(
        requested_name="kitchen knife",
        category_hint="evidence",
        subtype_hint="sharp",
        tags=("weapon", "kitchen"),
        required_interaction="inspect",
        evidence_id="forensic_knife_match_01",
        required_evidence_capabilities=("match",),
        variant_params=(("material", "metal.steel"), ("scale", 1.1)),
    )
    assert request.requested_name == "kitchen knife"
    assert request.variant_params_dict() == {"material": "metal.steel", "scale": 1.1}


def test_object_request_name_exceeding_120_rejected():
    with pytest.raises(ValueError):
        ObjectRequest(requested_name="x" * (MAX_REQUESTED_NAME_LENGTH + 1))


def test_object_request_tags_exceeding_8_rejected():
    with pytest.raises(ValueError):
        ObjectRequest(requested_name="cup", tags=tuple(f"t{i}" for i in range(MAX_TAGS + 1)))


def test_object_request_capabilities_exceeding_8_rejected():
    with pytest.raises(ValueError):
        ObjectRequest(
            requested_name="cup",
            required_evidence_capabilities=tuple(
                f"c{i}" for i in range(MAX_CAPABILITIES + 1)
            ),
        )


def test_object_request_empty_name_rejected():
    with pytest.raises(ValueError):
        ObjectRequest(requested_name="")


@pytest.mark.parametrize(
    "bad",
    [
        "http://evil.example/a",
        "https://cdn.example/x.png",
        "data:text/html;base64,AAAA",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "../../windows/system32",
        "..\\..\\secret",
        "C:\\secret\\file",
        "/etc/passwd",
        r"cmd\script",
        r"sp\u65b0city",
        ".\\evil.sh",
    ],
)
def test_object_request_rejects_url_path_traversal_and_glyphs(bad):
    with pytest.raises(ValueError):
        ObjectRequest(requested_name=bad)
    assert object_request_issues(ObjectRequest(requested_name="safe cup")) == ()


def test_object_request_rejects_control_and_zero_width():
    assert safe_string_issues("a\x00b", "where")
    assert safe_string_issues("a\x1fb", "where")
    if FORMAT_GLYPH_CHARS:
        glyph = sorted(FORMAT_GLYPH_CHARS)[0]
        assert safe_string_issues(f"knife{glyph}", "where")


# --------------------------------------------------------------------------- #
# PlacementRelation
# --------------------------------------------------------------------------- #


def test_relation_kinds_vocabulary_is_the_six_documented_kinds():
    assert RELATION_KINDS == (
        "on_desk",
        "on_table",
        "near_victim",
        "inside_cabinet",
        "floor_area",
        "on_wall",
    )


def test_relation_to_anchor_types_mapping_is_documented():
    assert RELATION_TO_ANCHOR_TYPES == {
        "on_desk": "DESK_EVIDENCE",
        "on_table": "TABLE_PROP",
        "near_victim": "BODY",  # proximity semantics
        "inside_cabinet": "STORAGE",
        "floor_area": "FLOOR_EVIDENCE",
        "on_wall": "WALL_EVIDENCE",
    }


def test_relation_unknown_kind_rejected():
    with pytest.raises(ValueError):
        PlacementRelation(kind="behind_the_sofa", target="book")


def test_relation_valid_kind_ok():
    relation = PlacementRelation(kind="on_desk", target="custom trophy")
    assert relation.kind == "on_desk"
    assert relation.target == "custom trophy"


# --------------------------------------------------------------------------- #
# WorldRequirements bounds
# --------------------------------------------------------------------------- #


def test_world_requirements_exceeding_object_count_rejected():
    requests = tuple(
        ObjectRequest(requested_name=f"prop_{index}") for index in range(MAX_OBJECT_REQUESTS + 1)
    )
    with pytest.raises(ValueError):
        WorldRequirements(objects=requests)


def test_world_requirements_exceeding_relation_count_rejected():
    relations = tuple(
        PlacementRelation(kind=RELATION_KINDS[0], target=f"o{index}")
        for index in range(MAX_RELATIONS + 1)
    )
    with pytest.raises(ValueError):
        WorldRequirements(relations=relations)


def test_world_requirements_bounds_are_pinned():
    assert MAX_OBJECT_REQUESTS == 24
    assert MAX_RELATIONS == 12
    assert MAX_REQUESTED_NAME_LENGTH == 120
    assert MAX_TAGS == 8


def test_world_requirements_unsafe_notes_are_bounded():
    notes = tuple(f"unsafeUnsupported: term {index}" for index in range(9))
    with pytest.raises(ValueError):
        WorldRequirements(unsafe_unsupported=notes)


# --------------------------------------------------------------------------- #
# determinism / equality
# --------------------------------------------------------------------------- #


def test_equal_inputs_produce_equal_frozen_objects():
    first = WorldRequirements(
        environment_hint="office",
        location_tokens=("office",),
        objects=(_dummy_request(),),
        relations=(PlacementRelation(kind="on_desk", target="kitchen knife"),),
    )
    second = WorldRequirements(
        environment_hint="office",
        location_tokens=("office",),
        objects=(_dummy_request(),),
        relations=(PlacementRelation(kind="on_desk", target="kitchen knife"),),
    )
    assert first == second
    assert first.objects == second.objects
    assert isinstance(first, WorldRequirements)


def test_world_requirements_issues_never_raise_double_build():
    issues = object_request_issues(_dummy_request())
    assert issues == ()
    # non-raising sibling agrees
    assert WorldRequirements(objects=(_dummy_request(),), relations=()).relations == ()