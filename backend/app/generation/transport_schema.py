"""Phase43B (P43B-FR-11/12/13) — bounded local transport-schema validator.

The Frontier adapter sends a server-owned JSON Schema as native structured
output and then MUST validate the returned content against the EXACT schema
that was sent (never a more permissive one). No JSON-Schema library is
installed (``jsonschema`` / ``fastjsonschema`` are absent), so this module
implements a small, deterministic, BOUNDED validator for exactly the keyword
subset the transport schemas actually emit:

    type (string OR list incl. "null"), properties, required,
    additionalProperties (true/false), items, enum, minItems, maxItems,
    pattern (ECMA-262 subset), format (IGNORED), maxLength, description
    (IGNORED)

Design guarantees:

- ITERATIVE, never recursive: an explicit work stack bounded by
  ``MAX_STRUCT_NESTING`` (the repo-wide depth guard) and a total node-visit
  budget, so a hostile/deep document can never stack-blow or spin.
- DETERMINISTIC + content-safe issues: the returned tuple is sorted, deduped
  and contains only schema-derived tokens (property names that come from the
  server schema, array indices, keyword names and the JSON type names). Raw
  instance VALUES and unknown instance keys are NEVER echoed.
- An empty return tuple means the instance is valid.
- An object node declared ``additionalProperties: true`` (an intentionally
  OPEN object — the canonical evidence proposition ``structured`` node and the
  Cohere-compat synthetic-key projection) does NOT enforce its ``required``
  list: the open-shape projection is a provider-compatibility artifact and the
  canonical parser remains the sole acceptance authority. Closed nodes
  (``additionalProperties: false``) DO enforce required.

This module deliberately imports only ``app.assets.depthguard``; it has no
domain, registry or provider knowledge.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from app.assets.depthguard import MAX_STRUCT_NESTING

# A hard cap on the number of (instance, schema) pairs inspected in ONE
# validation call. The decoded document is already transport-bounded
# (``MAX_FRONTIER_RESPONSE_BYTES``) and depth-bounded, so this is a
# belt-and-braces termination guarantee.
MAX_TRANSPORT_VALIDATION_NODES = 100_000

# Bounded path rendering (never echo unbounded content in an issue string).
_MAX_PATH_SEGMENTS = 24
_MAX_PATH_TOKEN_CHARS = 64

# JSON type names produced by ``_json_type_name``.
_TYPE_NULL = "null"
_TYPE_BOOLEAN = "boolean"
_TYPE_INTEGER = "integer"
_TYPE_NUMBER = "number"
_TYPE_STRING = "string"
_TYPE_ARRAY = "array"
_TYPE_OBJECT = "object"
_TYPE_UNKNOWN = "unknown"


def _json_type_name(value: Any) -> str:
    """The JSON type name of a decoded Python value (bool != int)."""
    if value is None:
        return _TYPE_NULL
    if isinstance(value, bool):
        return _TYPE_BOOLEAN
    if isinstance(value, int):
        return _TYPE_INTEGER
    if isinstance(value, float):
        return _TYPE_NUMBER
    if isinstance(value, str):
        return _TYPE_STRING
    if isinstance(value, list):
        return _TYPE_ARRAY
    if isinstance(value, dict):
        return _TYPE_OBJECT
    return _TYPE_UNKNOWN


def _matches_type(value: Any, type_name: str) -> bool:
    """JSON-Schema ``type`` membership (bool is never a number/integer)."""
    if type_name == _TYPE_NULL:
        return value is None
    if type_name == _TYPE_BOOLEAN:
        return isinstance(value, bool)
    if type_name == _TYPE_INTEGER:
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, float) and value.is_integer()
    if type_name == _TYPE_NUMBER:
        return (not isinstance(value, bool)) and isinstance(value, (int, float))
    if type_name == _TYPE_STRING:
        return isinstance(value, str)
    if type_name == _TYPE_ARRAY:
        return isinstance(value, list)
    if type_name == _TYPE_OBJECT:
        return isinstance(value, dict)
    return False


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality for ``enum`` membership (bool is distinct from int)."""
    if isinstance(left, bool) or isinstance(right, bool):
        return (
            isinstance(left, bool)
            and isinstance(right, bool)
            and left == right
        )
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return type(left) is type(right) and left == right


def _format_path(path: tuple[str, ...]) -> str:
    if not path:
        return "$"
    segments = path[-_MAX_PATH_SEGMENTS:]
    return "/".join(segment[:_MAX_PATH_TOKEN_CHARS] for segment in segments)


def validate_transport_schema(
    instance: Any, schema: Mapping[str, Any]
) -> tuple[str, ...]:
    """Validate ``instance`` against the transport ``schema``.

    Returns a SORTED, deduplicated tuple of content-safe issue strings; an
    EMPTY tuple means the instance is valid. Never raises for malformed
    content/schema (unknown keywords are ignored; an invalid ``pattern`` is
    skipped — the canonical parser remains the acceptance authority).
    """
    if not isinstance(schema, Mapping):
        return ("schema: not_a_mapping",)
    issues: set[str] = set()
    stack: list[tuple[Any, Mapping[str, Any], tuple[str, ...], int]] = [
        (instance, schema, (), 1)
    ]
    visited = 0
    while stack:
        value, node, path, depth = stack.pop()
        visited += 1
        if visited > MAX_TRANSPORT_VALIDATION_NODES:
            issues.add("validation: node_budget_exceeded")
            break
        if depth > MAX_STRUCT_NESTING:
            issues.add(f"depth_exceeded@{_format_path(path)}")
            continue
        if not isinstance(node, Mapping):
            continue

        # 1) type (string or list incl. "null"). On mismatch do not descend.
        declared = node.get("type")
        allowed: tuple[str, ...]
        if isinstance(declared, str):
            allowed = (declared,)
        elif isinstance(declared, list):
            allowed = tuple(item for item in declared if isinstance(item, str))
        else:
            allowed = ()
        if allowed and not any(_matches_type(value, t) for t in allowed):
            issues.add(
                f"type@{_format_path(path)}: expected "
                + "|".join(allowed)
                + f" got {_json_type_name(value)}"
            )
            continue

        # 2) enum (JSON equality; bool distinct from int).
        enum = node.get("enum")
        if isinstance(enum, list) and enum and not any(
            _json_equal(value, candidate) for candidate in enum
        ):
            issues.add(f"enum@{_format_path(path)}")

        # 3) object constraints.
        if isinstance(value, dict):
            raw_props = node.get("properties")
            props: Mapping[str, Any] = (
                raw_props if isinstance(raw_props, Mapping) else {}
            )
            additional = node.get("additionalProperties", True)
            open_object = additional is True
            required = node.get("required")
            if (
                not open_object
                and isinstance(required, list)
            ):
                for key in required:
                    if isinstance(key, str) and key not in value:
                        issues.add(
                            f"missing_required@{_format_path(path)}:{key}"
                        )
            if additional is False:
                for key in value:
                    if key not in props:
                        issues.add(
                            f"additional_property@{_format_path(path)}"
                        )
            for key, subschema in props.items():
                if key in value and isinstance(subschema, Mapping):
                    stack.append(
                        (value[key], subschema, path + (str(key),), depth + 1)
                    )

        # 4) array constraints.
        if isinstance(value, list):
            min_items = node.get("minItems")
            if (
                isinstance(min_items, int)
                and not isinstance(min_items, bool)
                and len(value) < min_items
            ):
                issues.add(f"minItems@{_format_path(path)}")
            max_items = node.get("maxItems")
            if (
                isinstance(max_items, int)
                and not isinstance(max_items, bool)
                and len(value) > max_items
            ):
                issues.add(f"maxItems@{_format_path(path)}")
            items = node.get("items")
            if isinstance(items, Mapping):
                for index, item in enumerate(value):
                    stack.append(
                        (
                            item,
                            items,
                            path + (f"[{index}]",),
                            depth + 1,
                        )
                    )

        # 5) string constraints.
        if isinstance(value, str):
            max_length = node.get("maxLength")
            if (
                isinstance(max_length, int)
                and not isinstance(max_length, bool)
                and len(value) > max_length
            ):
                issues.add(f"maxLength@{_format_path(path)}")
            pattern = node.get("pattern")
            if isinstance(pattern, str) and pattern:
                try:
                    matched = re.search(pattern, value) is not None
                except re.error:
                    matched = True  # invalid pattern: no false rejection
                if not matched:
                    issues.add(f"pattern@{_format_path(path)}")

        # ``format`` / ``description`` are intentionally IGNORED.

    return tuple(sorted(issues))


def transport_schema_is_valid(instance: Any, schema: Mapping[str, Any]) -> bool:
    """``True`` iff ``instance`` satisfies the bounded transport ``schema``."""
    return not validate_transport_schema(instance, schema)


__all__ = [
    "MAX_TRANSPORT_VALIDATION_NODES",
    "transport_schema_is_valid",
    "validate_transport_schema",
]
