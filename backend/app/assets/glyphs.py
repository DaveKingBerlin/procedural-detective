"""DEF-068 — shared Unicode format/zero-width/Bidi/line-separator glyph guard.

The Asset Oracle string-safety family (asset specs, catalog manifest, asset
requests, environment manifests) previously rejected only ``ord < 0x20``
control characters. Unicode also defines a family of INVISIBLE format/control
glyphs that exercise the same smash-the-validator/canonicalization surface
(zero-width spaces/joiners, LRM/RLM marks, bidi embedding/overrides, line and
paragraph separators, word joiners / invisible operators, the byte-order mark):

- U+200B–U+200F — zero-width space, zero-width non-joiner/joiner, LRM, RLM;
- U+2028 and U+2029 — line separator, paragraph separator;
- U+202A–U+202E — left-to-right / right-to-left embedding, override, pop;
- U+2060–U+2064 — word joiner, invisible times/separator, invisible plus,
  inhibit/parameter symmetric swapping;
- U+FEFF — byte-order mark / zero-width no-break space.

Neither NFKC normalization nor ``str.strip()`` removes these (they are format
controls, not compatibility characters), so they MUST be rejected explicitly.
Every validator in the family appends the same deterministic issue via
:func:`format_glyph_issues` after its existing control-character check.
"""

from __future__ import annotations

# Documented (start, end) INCLUSIVE codepoint ranges of the invisible
# format/zero-width/Bidi/line-separator glyph class (DEF-068).
FORMAT_GLYPH_RANGES: tuple[tuple[int, int], ...] = (
    (0x200B, 0x200F),  # zero-width space / non-joiner / joiner / LRM / RLM
    (0x2028, 0x2028),  # line separator
    (0x2029, 0x2029),  # paragraph separator
    (0x202A, 0x202E),  # LRE / RLE / PDF / LRO / RLO
    (0x2060, 0x2064),  # word joiner + invisible operators
    (0xFEFF, 0xFEFF),  # byte-order mark / zero-width no-break space
)

# The frozen glyph class as a set of single characters.
FORMAT_GLYPH_CHARS: frozenset[str] = frozenset(
    chr(codepoint)
    for low, high in FORMAT_GLYPH_RANGES
    for codepoint in range(low, high + 1)
)

# Canonical, deterministic issue label used by every validator in the family.
GLYPH_ISSUE_LABEL = "Unicode format/zero-width/Bidi/line-separator control glyph"


def format_glyph_in(value: str) -> str | None:
    """The FIRST offending glyph of ``value`` in string order (deterministic),
    or None when the value contains none of the format-control glyph class."""
    if not isinstance(value, str):
        return None
    for ch in value:
        if ch in FORMAT_GLYPH_CHARS:
            return ch
    return None


def format_glyph_issues(value: str, where: str) -> list[str]:
    """Deterministic issue strings for the glyph class (empty when safe).

    Reports the first offending glyph's codepoint (``U+XXXX``) so the issue is
    stable regardless of how many glyphs a payload smuggles.
    """
    glyph = format_glyph_in(value)
    if glyph is None:
        return []
    return [
        f"{where}: contains a {GLYPH_ISSUE_LABEL} U+{ord(glyph):04X}"
    ]


__all__ = [
    "FORMAT_GLYPH_CHARS",
    "FORMAT_GLYPH_RANGES",
    "GLYPH_ISSUE_LABEL",
    "format_glyph_in",
    "format_glyph_issues",
]