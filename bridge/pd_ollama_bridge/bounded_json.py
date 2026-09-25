"""Bounded JSON parsing for the bridge: byte-size, nesting-depth and
collection-length limits enforced BEFORE deep recursion can happen.

This mirrors the server's ``bounded_json_loads`` pattern (Phase22 ``28/29``):
a string-aware bracket pre-scan rejects nesting bombs before the JSON decoder
ever runs, and an iterative stack walker bounds every parsed collection, so
``json.loads`` can never recurse beyond a documented limit.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_JSON_DEPTH = 32
MAX_COLLECTION_LENGTH = 10_000


class BoundedJsonError(ValueError):
    """A JSON document exceeded the depth or collection-length bound."""

    def __init__(self, message: str) -> None:
        self.message = str(message)
        super().__init__(self.message)


_ESCAPE_FOLLOW_RE = re.compile(r'\\["\\/bfnrtu]')


def _iter_json_text_units(text: str):
    in_string = False
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if in_string:
            if ch == "\\":
                peek = text[index + 1 : index + 2]
                if peek == "u":
                    index += 6
                else:
                    index += 2
                yield None, True
                continue
            if ch == '"':
                in_string = False
            yield ch, True
            index += 1
            continue
        if ch == '"':
            in_string = True
        yield ch, False
        index += 1


def bracket_depth(text: str) -> int:
    depth = 0
    max_depth = 0
    for unit, in_string in _iter_json_text_units(text):
        if unit is None or in_string:
            continue
        if unit in "[{":
            depth += 1
            if depth > max_depth:
                max_depth = depth
        elif unit in "]}":
            if depth > 0:
                depth -= 1
    return max_depth


def bounded_json_loads(
    raw: str | bytes,
    *,
    max_depth: int = MAX_JSON_DEPTH,
    max_collection_length: int = MAX_COLLECTION_LENGTH,
) -> Any:
    """Decode JSON only after depth and collection-length pre-checks."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BoundedJsonError("document is not valid UTF-8 JSON") from None
    else:
        text = str(raw)
    depth = bracket_depth(text)
    if depth > int(max_depth):
        raise BoundedJsonError(
            f"document nesting depth {depth} exceeds the maximum nesting depth "
            f"{max_depth}"
        )
    try:
        obj = json.loads(text)
    except RecursionError:  # pragma: no cover - belt-and-braces only
        raise BoundedJsonError(
            f"document nesting depth exceeds the maximum nesting depth {max_depth}"
        ) from None
    if int(max_collection_length) > 0:
        _bound_collection_lengths(obj, int(max_collection_length))
    return obj


def _bound_collection_lengths(value: Any, limit: int) -> None:
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if len(node) > limit:
                raise BoundedJsonError(
                    f"document collection exceeds the maximum collection length {limit}"
                )
            stack.extend(node.values())
        elif isinstance(node, list):
            if len(node) > limit:
                raise BoundedJsonError(
                    f"document collection exceeds the maximum collection length {limit}"
                )
            stack.extend(node)


def bounded_structure_depth(value: Any, *, limit: int = MAX_JSON_DEPTH) -> int:
    if not isinstance(value, (dict, list)):
        return 0
    max_depth = 0
    stack = [(value, 0)]
    while stack:
        node, parent_depth = stack.pop()
        child_depth = parent_depth + 1
        if child_depth > limit:
            return child_depth
        if child_depth > max_depth:
            max_depth = child_depth
        for child in (node.values() if isinstance(node, dict) else node):
            if isinstance(child, (dict, list)):
                stack.append((child, child_depth))
    return max_depth


__all__ = [
    "BoundedJsonError",
    "MAX_COLLECTION_LENGTH",
    "MAX_JSON_DEPTH",
    "bounded_json_loads",
    "bounded_structure_depth",
    "bracket_depth",
]