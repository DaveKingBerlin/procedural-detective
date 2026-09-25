import json

import pytest

from pd_ollama_bridge.bounded_json import (
    BoundedJsonError,
    bounded_json_loads,
    bracket_depth,
)


def test_plain_object_roundtrip():
    assert bounded_json_loads('{"a": 1}') == {"a": 1}


def test_nesting_bomb_rejected_before_decode():
    raw = "[" * 2000 + "1" + "]" * 2000
    with pytest.raises(BoundedJsonError):
        bounded_json_loads(raw)


def test_depth_within_bound_accepted():
    value = {"a": {"b": {"c": {"d": {"e": 1}}}}}
    assert bounded_json_loads(json.dumps(value)) == value


def test_collection_too_long_rejected():
    big = {"items": list(range(20_000))}
    with pytest.raises(BoundedJsonError):
        bounded_json_loads(json.dumps(big))


def test_collection_within_bound_accepted():
    small = {"items": list(range(3))}
    assert bounded_json_loads(json.dumps(small)) == small


def test_brackets_inside_string_ignored():
    raw = '{"a": "[[[[[", "b": "]]]]]" }'
    assert bracket_depth(raw) == 1
    assert bounded_json_loads(raw) == {"a": "[[[[[", "b": "]]]]]"}


def test_invalid_utf8_bytes_rejected():
    with pytest.raises(BoundedJsonError):
        bounded_json_loads(b"\xff\xfe\x00")


def test_malformed_json_rejected():
    with pytest.raises(ValueError):
        bounded_json_loads("this is not json")