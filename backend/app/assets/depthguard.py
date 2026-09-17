"""Phase 13 / DEF-067 — bounded-structure + bounded-JSON depth guard.

Deep nesting bombs (``"[ "*10000``-style JSON or Python dicts nested
hundreds/thousands of levels) made ``json.loads`` (and recursive walkers) blow
the interpreter recursion limit with an UNCAUGHT ``RecursionError`` — violating
the "reject with issues, never raise" contract of the spec parser and the
oracle entry points.

Two complementary, ITERATIVE (never recursive) defenses live here:

- ``MAX_STRUCT_NESTING = 32`` — a spec document (and catalog/environment
  manifests) must never nest lists/dicts deeper than this. The documented
  asset schemas nest at a *handful* of levels; 32 is far above any legitimate
  document and far below Python's recursion ceiling, so a depth-checked
  structure can later be walked recursively without recursion risk.
- ``bounded_structure_depth`` — a stack-based walker over an ALREADY-PARSED
  Python dict/list tree; returns the max nesting depth and stops early as soon
  as the limit is crossed.
- ``bracket_depth`` — an iterative, string-aware character scan (quotes and
  backslash escapes handled; `[`/`{` vs `]`/`}` counted only OUTSIDE string
  literals, which is where JSON's semantic nesting lives) that returns the max
  bracket nesting WITHOUT ever building the tree. This runs BEFORE
  ``json.loads`` so the decoder can never be fed a nesting bomb.
- ``bounded_json_loads`` — ``bracket_depth`` pre-scan, then the standard
  decoder; a too-deep document raises the typed ``BoundedJsonError`` (never a
  ``RecursionError``).

Callers map ``BoundedJsonError`` into a deterministic, sanitized issue/error
(the "reject, never raise" contract) — see ``app.assets.specs`` /
``app.assets.catalog`` / ``app.environments.manifests``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

# The documented maximum structure nesting depth of ANY declarative document
# accepted by the Asset Oracle family (specs, catalog manifest, environment
# manifest, embedded generated definitions). Far above every legitimate
# document; far below the CPython recursion ceiling.
MAX_STRUCT_NESTING = 32


class BoundedJsonError(ValueError):
    """A JSON document exceeded the documented nesting-depth bound.

    Raised by ``bounded_json_loads`` BEFORE the JSON decoder runs, so a deep
    nesting bomb is rejected cleanly (never a ``RecursionError``).
    """

    def __init__(self, message: str) -> None:
        self.message = str(message)
        super().__init__(self.message)


# --------------------------------------------------------------------------- #
# iterative structure walker (already-parsed Python trees)
# --------------------------------------------------------------------------- #


def bounded_structure_depth(
    value: Any, *, limit: int = MAX_STRUCT_NESTING
) -> int:
    """The maximum dict/list nesting depth of ``value`` (ITERATIVE, no recursion).

    A dict/list at the root is depth 1; each nested container adds 1. Stops
    early once a nesting depth EXCEEDS ``limit`` and returns that depth (an
    integer > ``limit``), so deep trees are bounded in time and never recurse.
    """
    if not isinstance(value, (dict, list)):
        return 0
    max_depth = 0
    stack = [(value, 0)]  # (node, parent_depth)
    while stack:
        node, parent_depth = stack.pop()
        child_depth = parent_depth + 1
        if child_depth > limit:
            return child_depth
        if child_depth > max_depth:
            max_depth = child_depth
        if isinstance(node, dict):
            children = node.values()
        else:
            children = node
        for child in children:
            if isinstance(child, (dict, list)):
                stack.append((child, child_depth))
    return max_depth


# --------------------------------------------------------------------------- #
# string-aware bracket scan + bounded JSON decode
# --------------------------------------------------------------------------- #

_ESCAPE_FOLLOW_RE = re.compile(r'\\["\\/bfnrtu]')


def _iter_json_text_units(text: str):
    """Yield (char, in_string) units so the bracket scan never interprets
    brackets inside string literals (escapes handled)."""
    in_string = False
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if in_string:
            if ch == "\\":
                # Skip the escaped char (\" or \\uXXXX etc.).
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
    # Trailing content inside an unterminated string still contributes
    # nothing to bracket depth (invalid JSON rejected later by the decoder).


def bracket_depth(text: str) -> int:
    """Max bracket nesting depth of JSON text (``[``/``{`` opening vs
    ``]``/``}`` closing OUTSIDE string literals). Iterative, no recursion.

    Unbalanced closing brackets floor at 0 (the decoder reports the real JSON
    error later); the scan returns the maximum OPEN depth observed, which is
    exactly the nesting the JSON decoder would recurse into.
    """
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
    object_pairs_hook: Callable[[list[tuple[str, Any]]], Any] | None = None,
    limit: int = MAX_STRUCT_NESTING,
) -> Any:
    """Decode JSON only after a bracket-depth pre-scan.

    Raises ``BoundedJsonError`` (clean) when ``raw``'s bracket nesting exceeds
    ``limit`` BEFORE ``json.loads`` is invoked — the decoder can therefore
    never recurse beyond the documented bound. All other behavior (error
    translation, ``object_pairs_hook``) matches ``json.loads``.
    """
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BoundedJsonError(
                "document is not valid UTF-8 JSON"
            ) from None
    else:
        text = str(raw)
    depth = bracket_depth(text)
    if depth > limit:
        raise BoundedJsonError(
            f"document nesting depth {depth} exceeds the maximum nesting depth "
            f"{limit}"
        )
    try:
        return json.loads(text, object_pairs_hook=object_pairs_hook)
    except RecursionError:  # pragma: no cover - belt-and-braces only
        raise BoundedJsonError(
            f"document nesting depth exceeds the maximum nesting depth {limit}"
        ) from None


__all__ = [
    "BoundedJsonError",
    "MAX_STRUCT_NESTING",
    "bounded_json_loads",
    "bounded_structure_depth",
    "bracket_depth",
]