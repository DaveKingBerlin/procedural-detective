"""Generated-content safety + asset/interaction registries (Phase4 I, REQUIREMENTS 6.x / 26).

Treat all generated text as inert data:

- ``validate_content_safety`` scans every generated string (and mapping key) and
  rejects script/eval/event-handler/URL/path/backtick-code-fence payloads.
- ``AssetRegistry`` is the application-owned logical asset-ID registry
  (REQUIREMENTS 6.3 + 26): providers may reference only these ids; arbitrary
  URLs / filesystem paths / data: URLs are never assets.
- ``validate_world_graph`` enforces the interaction allowlist (6.4), the
  semantic-anchor allowlist (26) and referential integrity of placements.
- ``sanitize_for_repair`` produces the stable, safe textual projection handed
  to a repair provider (no hidden internals beyond the crime facts that ARE
  part of the generated draft, and no solver diagnostics except the sanitized
  issue strings).
"""

from __future__ import annotations

import dataclasses
import html
import json
import re
import unicodedata
from typing import Any, Iterable, Mapping

from app.generation.schemas import GeneratedDraft, WorldGraphSpec

_ASSET_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+$")

# URL-denoting token: `<scheme>://`. Every such token in generated content has
# a scheme that is checked against SAFE_URL_SCHEMES.
_URL_SCHEME_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]{1,31}://")

# No URL scheme is currently allowlisted: any `scheme://` token in generated
# content is rejected (REQUIREMENTS 6.5: MVP must not fetch arbitrary URLs).
SAFE_URL_SCHEMES: frozenset[str] = frozenset()

_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "<script",
    "</script",
    "eval(",
    "new Function(",
    "javascript:",
    "data:",
    "file://",
    "http://",
    "https://",
    "onerror=",
    "onclick=",
    "onload=",
    "onmouseover=",
    "<img",
    "<svg",
    "<iframe",
    "<object",
    "<embed",
    "<style",
    "<link",
    "import(",
    "exec(",
    "shader",
    ".glb-url",
    ".gltf-url",
    "--input",
    "powershell",
    "cmd.exe",
    "os.system",
    "subprocess",
    # path traversal (REQUIREMENTS 6.6 example "../../../../windows/system32")
    # in both slash spellings
    "../",
    "..\\",
)

# ---------------------------------------------------------------------------
# canonicalization layer (DEF-040 / ADV-127)
# ---------------------------------------------------------------------------
#
# The RAW token scan above is defense-in-depth only: encoded/mangled payloads
# (HTML entities, fullwidth/homoglyph lookalikes, embedded control characters,
# literal backslash escapes, zero-width format characters, URL-encoded path
# traversal, whitespace-split tokens) evade a plain substring scan. For every
# generated string we therefore ALSO build a single normalized form and run
# anchored token regexes on it:
#
#   _normalized_form(text):
#     1. unicodedata.normalize("NFKC", text)  - fullwidth ＜  ->> <, fullwidth
#        letters ->> ASCII, other homoglyph lookalikes
#     2. .lower()                             - all tokens are matched lowercase
#     3. html.unescape                        - &lt; &#x3C; &#60; &LT; ...
#     4. unescape literal backslash escapes   - \\u003c ->> < ; JSON-rendered
#        control escapes (\\n, \\t, \\x-style \u0000) back to control chars —
#        the pipeline scans the deterministic JSON projection of the draft, so
#        a real newline in a generated string arrives as the two characters
#        backslash-n and a NUL byte arrives as the six characters \\u0000
#     5. html.unescape again                  - double-encoded entities
#        (\\u0026lt; ->> &lt; ->> <)
#     6. remove control (< 0x20) and zero-width/format characters
#        (U+200B U+200C U+200D U+FEFF)        - java\\nscript ->> javascript,
#        <scr\\x00ipt ->> <script
#
# Tokens are deliberately ANCHORED/FOCUSED: bare prose words such as "script"
# or ".constructor" are NOT tokens — only executable forms (a "<" preceding
# script, a constructor chain/invocation, an event-handler assignment, a URL
# scheme, a traversal prefix, a code fence) are rejected. The regression
# guards "The victim left a script for the accountant." and
# "schema.constructor details" MUST still pass.
# ---------------------------------------------------------------------------


# Matches a literal backslash-u-4hex sequence EITHER directly (single
# backslash, raw string) OR JSON-rendered (double backslash — the pipeline scans
# the deterministic JSON projection where a literal backslash is escaped as
# ``\\``). ``\\{1,2}`` covers both forms; the exact backslash count is consumed.
_BACKSLASH_U_ANY_RE = re.compile(r"\\{1,2}u([0-9a-fA-F]{4})")
_BACKSLASH_ESCAPES: dict[str, str] = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "b": "\b",
    "f": "\f",
    "\\": "\\",
    "/": "/",
    '"': '"',
    "'": "'",
}


def _unescape_backslashes(text: str) -> str:
    """Unescape literal JSON-style backslash escapes.

    Operates on the ACTUAL characters received:

    - ``\\u003c`` (single backslash + u + 4 hex) -> the corresponding character
      (covers raw generated strings);
    - ``\\\\u003c`` (double backslash + u + 4 hex) -> the same character
      (covers JSON-rendered projections, where json.dumps escapes every literal
      backslash as ``\\\\``);
    - single-character JSON control escapes (``\\n``, ``\\t`` ...) -> their
      control character (removed by the next stage; json.dumps renders a real
      newline in a generated string as ``\\n`` and a NUL byte as ``\\u0000``).

    Unescapable backslashes stay verbatim.
    """
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        ch = text[index]
        if ch == "\\":
            match = _BACKSLASH_U_ANY_RE.match(text, index)
            if match:
                try:
                    out.append(chr(int(match.group(1), 16)))
                except ValueError:  # lone surrogate etc. -> keep verbatim
                    out.append(text[index : index + match.end() - index])
                index = match.end()
                continue
            if index + 1 < length and text[index + 1] in _BACKSLASH_ESCAPES:
                out.append(_BACKSLASH_ESCAPES[text[index + 1]])
                index += 2
                continue
        out.append(ch)
        index += 1
    return "".join(out)


_ZERO_WIDTH_AND_FORMAT = frozenset("\u200b\u200c\u200d\ufeff")


def _remove_control_and_format(text: str) -> str:
    return "".join(
        c
        for c in text
        if not (ord(c) < 0x20 or c in _ZERO_WIDTH_AND_FORMAT)
    )


def _normalized_form(text: str) -> str:
    """Canonicalized lowercase scan form of one generated string (DEF-040)."""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = html.unescape(text)
    text = _unescape_backslashes(text)
    text = html.unescape(text)
    return _remove_control_and_format(text)


# Anchored, whitespace-tolerant tokens matched against the NORMALIZED form.
_NORMALIZED_TOKENS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"<\s*script"), "script start tag"),
    (re.compile(r"</\s*script"), "script end tag"),
    (re.compile(r"(?:eval|new\s+function)\s*\("), "eval/new Function call"),
    (re.compile(r"javascript\s*:"), "javascript: URL"),
    (re.compile(r"data\s*:\s*text"), "data:text URL"),
    (re.compile(r"file\s*://"), "file:// URL"),
    (re.compile(r"file\s*:\s*[/\\]+"), "file: filesystem path"),
    (re.compile(r"https?\s*://"), "http(s):// URL"),
    (re.compile(r"on[a-z]+\s*="), "event handler attribute"),
    (re.compile(r"globalThis\b"), "globalThis gadget"),
    (re.compile(r"\.constructor\s*\.\s*constructor"), "constructor chain"),
    (re.compile(r"\.constructor\s*\("), "constructor invocation"),
    (re.compile(r"\bparent\b\s*\.\s*constructor"), "parent constructor traversal"),
    (re.compile(r"\x60"), "backtick code fence"),
    (re.compile(r"\.\.[/\\]+"), "path traversal"),
    (re.compile(r"%2e%2e[/\\%]*"), "URL-encoded path traversal"),
)


class AssetRegistry:
    """Deterministic logical asset-ID registry, owned by the application.

    Each id resolves server-side to a repository-hosted/local approved asset;
    providers never supply URLs or paths (REQUIREMENTS 6.3 / 26).
    """

    ASSET_IDS: frozenset[str] = frozenset(
        {
            "PROP_KITCHEN_KNIFE_01",
            "PROP_LETTER_OPENER_01",
            "PROP_SCISSORS_01",
            "PROP_VASE_01",
            "PROP_HEAVY_VASE_01",
            "PROP_TABLE_01",
            "PROP_CHAIR_01",
            "PROP_LAPTOP_01",
            "PROP_BOTTLE_01",
            "CAMERA_HALL_01",
            "DOOR_APARTMENT_01",
        }
    )

    @classmethod
    def is_allowed(cls, asset_id: str) -> bool:
        """Strict equality against the registered set (else False)."""
        return asset_id in cls.ASSET_IDS


# REQUIREMENTS 6.4 — MVP interaction identifier allowlist.
INTERACTION_ALLOWLIST: tuple[str, ...] = (
    "inspect",
    "collect",
    "open",
    "read",
    "activate",
    "talk",
    "view_record",
    "add_to_evidence_board",
)

# REQUIREMENTS 26 — semantic anchors (never raw coordinates).
ANCHOR_ALLOWLIST: tuple[str, ...] = (
    "desk_main",
    "kitchen_counter",
    "dining_table",
    "bedside_table",
    "floor_body_position",
    "shelf_01",
    "hall_wall_01",
    "office_desk_01",
)


def _iter_strings(node: Any) -> Iterable[str]:
    """Yield every generated string (values AND mapping keys) in a tree."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, Mapping):
        for key, value in node.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(value)
    elif isinstance(node, (list, tuple, set, frozenset)):
        for value in node:
            yield from _iter_strings(value)


def _spec_to_plain(node: Any) -> Any:
    """Convert spec dataclasses into plain dicts/lists for scanning/serializing."""
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        if isinstance(node, Mapping):
            return {str(k): _spec_to_plain(v) for k, v in node.items()}
        return {
            f.name: _spec_to_plain(getattr(node, f.name))
            for f in dataclasses.fields(node)
        }
    if isinstance(node, Mapping):
        return {str(k): _spec_to_plain(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_spec_to_plain(v) for v in node]
    return node


def validate_content_safety(any_generated_string_or_tree: Any) -> tuple[str, ...]:
    """Scan every generated string; return sorted issue strings (empty when safe).

    Two passes per generated string:

    - RAW pass (defense-in-depth): case-insensitive substring scan plus the
      ``scheme://`` token scan plus backtick detection on the original text;
    - NORMALIZED pass (DEF-040): a canonicalized form of the string (NFKC,
      lowercase, HTML-entity unescape, literal backslash-escape unescape,
      control/zero-width removal) is scanned with anchored, whitespace-tolerant
      token regexes so encoded/mangled payloads are still rejected.

    NO generated string may become executable instructions; bare prose words
    like "script" or ".constructor" are deliberately NOT tokens.
    """
    issues: list[str] = []
    for value in _iter_strings(any_generated_string_or_tree):
        lower = value.lower()
        for token in _FORBIDDEN_TOKENS:
            if token.lower() in lower:
                issues.append(
                    f"unsafe generated content: contains {token!r} in {value!r:.100}"
                )
        for match in _URL_SCHEME_RE.finditer(value):
            scheme = match.group(0).split(":", 1)[0]
            if scheme.lower() not in SAFE_URL_SCHEMES:
                issues.append(
                    f"unsafe generated content: URL-like token with non-allowlisted "
                    f"scheme {scheme!r} in {value!r:.100}"
                )
        if "`" in value:
            issues.append(
                f"unsafe generated content: contains backtick/code-fence marker "
                f"in {value!r:.100}"
            )
        normalized = _normalized_form(value)
        for pattern, label in _NORMALIZED_TOKENS:
            if pattern.search(normalized):
                issues.append(
                    f"unsafe generated content: {label} "
                    f"(normalized token {pattern.pattern!r}) in {value!r:.100}"
                )
    return tuple(sorted(set(issues)))


def validate_asset_reference(asset_id: str) -> tuple[str, ...]:
    """An asset reference must be in the registry AND match the id pattern."""
    if not isinstance(asset_id, str) or not asset_id:
        return ("asset id must be a non-empty string",)
    issues: list[str] = []
    if not _ASSET_ID_PATTERN.match(asset_id):
        issues.append(
            f"asset id {asset_id!r} does not match the required pattern "
            r"^[A-Z][A-Z0-9_]+$"
        )
    if not AssetRegistry.is_allowed(asset_id):
        issues.append(f"asset id {asset_id!r} is not in the AssetRegistry")
    return tuple(sorted(set(issues)))


def validate_world_graph(
    wg: WorldGraphSpec, object_ids: set[str], evidence_ids: set[str]
) -> tuple[str, ...]:
    """Referential + allowlist validation of a world graph.

    Every placement must reference a known objectId, a registered assetId, a
    locationId declared by the world graph itself, an anchor from
    ANCHOR_ALLOWLIST, an interaction from INTERACTION_ALLOWLIST and (when
    present) a known evidenceId. Arbitrary URLs/paths/data-loading anywhere are
    rejected by the content-safety scan over the whole graph.
    """
    issues: list[str] = []
    wg_locations = {loc.location_id for loc in wg.locations}
    for index, placement in enumerate(wg.placements):
        where = f"placements[{index}]"
        if placement.object_id not in object_ids:
            issues.append(f"{where}: unknown objectId {placement.object_id!r}")
        issues.extend(validate_asset_reference(placement.asset_id))
        if placement.location_id not in wg_locations:
            issues.append(f"{where}: unknown locationId {placement.location_id!r}")
        if placement.anchor not in ANCHOR_ALLOWLIST:
            issues.append(f"{where}: anchor {placement.anchor!r} is not in ANCHOR_ALLOWLIST")
        if placement.interaction not in INTERACTION_ALLOWLIST:
            issues.append(
                f"{where}: interaction {placement.interaction!r} is not in "
                "INTERACTION_ALLOWLIST"
            )
        if placement.evidence_id is not None and placement.evidence_id not in evidence_ids:
            issues.append(
                f"{where}: evidenceId {placement.evidence_id!r} is not a known "
                "evidence id"
            )
    issues.extend(validate_content_safety(_spec_to_plain(wg)))
    return tuple(sorted(set(issues)))


def sanitize_for_repair(
    draft_material: Any, diagnostics: tuple[str, ...] = ()
) -> str:
    """Stable, safe textual projection of draft material for a repair provider.

    Accepts a ``GeneratedDraft``/spec tree (serialized deterministically) or a
    raw string. Contains NO hidden internals: the draft model carries only
    generated content (the crime facts ARE part of the generated draft), and
    ``diagnostics`` are the sanitized issue strings. Same input -> byte-identical
    output.
    """
    if isinstance(draft_material, str):
        text = draft_material
    else:
        text = json.dumps(
            _spec_to_plain(draft_material), indent=2, ensure_ascii=False, sort_keys=True
        )
    lines = [text]
    if diagnostics:
        lines.append("")
        lines.append("VALIDATION ISSUES (sanitized):")
        for issue in diagnostics:
            lines.append(f"- {issue}")
    return "\n".join(lines)