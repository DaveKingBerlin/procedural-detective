"""Generated-content safety + asset/interaction registries (Phase4 I, REQUIREMENTS 6.x / 26).

Treat all generated text as inert data:

- ``validate_content_safety`` scans every generated string (and mapping key) and
  rejects script/eval/event-handler/URL/path/backtick-code-fence payloads.
- ``AssetRegistry`` is the application-owned logical asset-ID registry
  (REQUIREMENTS 6.3 + 26): providers may reference only these ids; arbitrary
  URLs / filesystem paths / data: URLs are never assets.
- ``validate_world_graph`` enforces the interaction allowlist (6.4; the empty
  string is additionally legal and means "decorative / not interactable",
  DEF-062), the semantic-anchor allowlist (26) and referential integrity of
  placements.
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

# Phase 13 — procedural (declarative) asset id grammar. Mirrors
# ``app.assets.compiler.PROCEDURAL_ASSET_PATTERN`` LOCALLY (like ``_ASSET_ID_PATTERN``
# mirrors ``app.assets.catalog``) because ``app.assets.catalog`` imports this
# module — a direct import of ``app.assets.compiler`` here would create an
# import cycle. A lockstep test pins both patterns to the same grammar.
_PROCEEDURAL_ASSET_ID_RE = re.compile(r"^proc\.[a-z0-9_]+\.[a-f0-9]{16}$")


def is_procedural_asset_id(asset_id: object) -> bool:
    """True when ``asset_id`` is a procedural (proc.*) generated asset id."""
    return isinstance(asset_id, str) and bool(_PROCEEDURAL_ASSET_ID_RE.match(asset_id))


def validate_procedural_placement(placement: Any, where: str) -> tuple[str, ...]:
    """Phase 13 world-graph rule: a proc.* placement MUST carry an embedded
    generatedDefinition whose ``assetId`` matches the placement's assetId.

    A proc.* assetId WITHOUT a matching ``generated_definition`` is a world-graph
    STRUCTURAL issue (never silently accepted, never projected).
    """
    if not is_procedural_asset_id(getattr(placement, "asset_id", None)):
        return ()
    definition = getattr(placement, "generated_definition", None)
    if not isinstance(definition, Mapping) or not definition:
        return (
            f"{where}: procedural asset {getattr(placement, 'asset_id', '')!r} "
            "requires an embedded generatedDefinition",
        )
    embedded_id = definition.get("assetId")
    if embedded_id != getattr(placement, "asset_id", None):
        return (
            f"{where}: embedded generatedDefinition.assetId does not match the "
            "placement assetId",
        )
    return ()

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
            # Phase 6 Milestone-1 scene assets (additive; REQUIREMENTS 25 / 26,
            # Phase6 E): apartment lamp + victim/body placeholder.
            "PROP_LAMP_01",
            "PROP_BODY_PLACEHOLDER_01",
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
# Phase 11 (Five Environment Kits & Semantic Anchors): the TWO namespace halves
# are the Milestone-1 anchors and the anchorIds of the five Phase 11 kits
# (assets/environments/*.json). The union is kept static here (importing the
# environment manifests would create a load cycle with app.assets.catalog);
# test_environments_manifests.py asserts the allowlist stays a superset of the
# shipped kits' anchorIds so the two can never drift apart.
ANCHOR_ALLOWLIST: tuple[str, ...] = (
    "desk_main",
    "kitchen_counter",
    "dining_table",
    "bedside_table",
    "floor_body_position",
    "shelf_01",
    "hall_wall_01",
    "office_desk_01",
    # Phase 11 apartment kit
    "spawn",
    "window_main",
    "floor_evidence_01",
    "document_01",
    "generic_prop_01",
    # Phase 11 office kit
    "office_desk_a",
    "office_desk_b",
    "office_meeting_table",
    "office_floor_01",
    "office_body_01",
    "office_door_01",
    "office_window_01",
    "office_window_02",
    "office_computer_01",
    "office_document_01",
    "office_shelf_01",
    "office_cctv_01",
    "office_access_01",
    "office_generic_01",
    "office_break_01",
    "office_spawn_01",
    # Phase 11 hotel_suite kit
    "hotel_bedside_01",
    "hotel_desk_01",
    "hotel_desk_02",
    "hotel_floor_01",
    "hotel_body_01",
    "hotel_door_01",
    "hotel_window_01",
    "hotel_minibar_01",
    "hotel_computer_01",
    "hotel_document_01",
    "hotel_cctv_01",
    "hotel_access_01",
    "hotel_generic_01",
    "hotel_bathroom_01",
    "hotel_spawn_01",
    # Phase 11 warehouse kit
    "warehouse_desk_01",
    "warehouse_workbench_01",
    "warehouse_floor_01",
    "warehouse_floor_02",
    "warehouse_body_01",
    "warehouse_door_01",
    "warehouse_door_02",
    "warehouse_window_01",
    "warehouse_shelf_01",
    "warehouse_shelf_02",
    "warehouse_computer_01",
    "warehouse_document_01",
    "warehouse_cctv_01",
    "warehouse_access_01",
    "warehouse_generic_01",
    "warehouse_spawn_01",
    # Phase 11 mansion kit
    "mansion_desk_01",
    "mansion_desk_02",
    "mansion_table_01",
    "mansion_sideboard_01",
    "mansion_floor_01",
    "mansion_body_01",
    "mansion_door_01",
    "mansion_window_01",
    "mansion_window_02",
    "mansion_computer_01",
    "mansion_document_01",
    "mansion_shelf_01",
    "mansion_cctv_01",
    "mansion_access_01",
    "mansion_generic_01",
    "mansion_spawn_01",
    "mansion_corridor_01",
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
    """An asset reference must match the id pattern and be KNOWN.

    The Phase 4 ``AssetRegistry`` is the MVP-era static registry; phases 10-13
    moved asset identity authority to the Asset Oracle catalog
    (``assets/catalog/catalog.json``). A reference is ACCEPTED when it is in
    the legacy registry OR in the Oracle catalog (lazy + cached lookup so this
    module never creates an import cycle), and rejected otherwise — arbitrary
    URLs/paths/unknown ids never pass. Existing unknown-id diagnostics keep
    the documented "not in the AssetRegistry" message when the id is unknown
    to BOTH authorities.
    """
    if not isinstance(asset_id, str) or not asset_id:
        return ("asset id must be a non-empty string",)
    issues: list[str] = []
    if not _ASSET_ID_PATTERN.match(asset_id):
        issues.append(
            f"asset id {asset_id!r} does not match the required pattern "
            r"^[A-Z][A-Z0-9_]+$"
        )
    if not AssetRegistry.is_allowed(asset_id):
        from app.assets.catalog import load_catalog_from_repo

        catalog = load_catalog_from_repo()
        if asset_id not in catalog.by_id:
            issues.append(f"asset id {asset_id!r} is not in the AssetRegistry")
    return tuple(sorted(set(issues)))


def validate_world_graph(
    wg: WorldGraphSpec, object_ids: set[str], evidence_ids: set[str]
) -> tuple[str, ...]:
    """Referential + allowlist validation of a world graph.

    Every placement must reference a known objectId, a registered assetId, a
    locationId declared by the world graph itself, an anchor from
    ANCHOR_ALLOWLIST, an interaction that is EITHER the empty string
    (``""`` = decorative / NOT interactable, DEF-062) OR a value from
    INTERACTION_ALLOWLIST, and (when present) a known evidenceId. An
    evidence-linked placement MUST carry a non-empty interaction (otherwise
    the evidence could never be reached through a player interaction).
    Arbitrary URLs/paths/data-loading anywhere are rejected by the
    content-safety scan over the whole graph.

    DEF-050: an objectId may be placed AT MOST ONCE — a duplicate placement
    would make the same world object render twice (and let a player's valid
    interaction on one placement answer 409 against the other). Every
    duplicate is reported deterministically in placement order, naming the
    FIRST occurrence it duplicates.
    """
    issues: list[str] = []
    wg_locations = {loc.location_id for loc in wg.locations}
    seen_object_ids: dict[str, int] = {}
    for index, placement in enumerate(wg.placements):
        where = f"placements[{index}]"
        if placement.object_id in seen_object_ids:
            issues.append(
                f"{where}: duplicate objectId {placement.object_id!r} "
                f"(first seen at placements[{seen_object_ids[placement.object_id]}])"
            )
        else:
            seen_object_ids[placement.object_id] = index
        if placement.object_id not in object_ids:
            issues.append(f"{where}: unknown objectId {placement.object_id!r}")
        if is_procedural_asset_id(placement.asset_id):
            # Phase 13: a procedural placement must carry a matching embedded
            # generatedDefinition (structural rule; the projection gate further
            # re-validates the definition per the current compiler/schema).
            issues.extend(validate_procedural_placement(placement, where))
        else:
            issues.extend(validate_asset_reference(placement.asset_id))
        if placement.location_id not in wg_locations:
            issues.append(f"{where}: unknown locationId {placement.location_id!r}")
        if placement.anchor not in ANCHOR_ALLOWLIST:
            issues.append(f"{where}: anchor {placement.anchor!r} is not in ANCHOR_ALLOWLIST")
        if placement.interaction:
            # Non-empty interactions must be a documented identifier.
            if placement.interaction not in INTERACTION_ALLOWLIST:
                issues.append(
                    f"{where}: interaction {placement.interaction!r} is not in "
                    "INTERACTION_ALLOWLIST"
                )
        elif placement.evidence_id is not None:
            # DEF-062: "" means decorative / not interactable; an evidence
            # link would then be unreachable through any interaction.
            issues.append(
                f"{where}: evidence-linked placement {placement.object_id!r} "
                "requires a non-empty interaction"
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