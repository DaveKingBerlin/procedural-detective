"""Phase 13 — bounded backend-authoritative material table.

The declarative AssetSpec may reference material TOKENS from the frozen Phase 12
literal vocabulary (``MATERIAL_VOCABULARY`` in ``app.assets.catalog`` — the
same 8 tokens the composite template renderer understands). The trusted
compiler needs ONE backend-owned table mapping every token to resolved render
values:

- ``color``    — the resolved base #RRGGBB pigment the compiler embeds into the
  generated definition (the client NEVER interprets tokens, so the definition
  carries the resolved hex instead);
- ``emissive`` — a reserved #RRGGBB emissive tone kept server-side for future
  render stages; Phase 13 definitions do not carry emissive (the frozen
  ``GeneratedAssetDefinition`` schema only declares ``color``).

The table is bounded and app-owned: every entry is validated at import time
(6-hex color grammar, exact token set). ``MATERIAL_VOCAB`` mirrors
``MATERIAL_VOCABULARY`` so callers can import either spelling.
"""

from __future__ import annotations

import re
from typing import Mapping

from app.assets.catalog import MATERIAL_VOCABULARY

# RGB hex color grammar (same as catalog/validation layers).
_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

# The bounded backend-authoritative material table:
# token -> {color: #RRGGBB, emissive: #RRGGBB}.
MATERIAL_COLORS: Mapping[str, Mapping[str, str]] = {
    "wood.dark": {"color": "#5b3a29", "emissive": "#000000"},
    "wood.light": {"color": "#c8a87c", "emissive": "#000000"},
    "metal.brass": {"color": "#c9a227", "emissive": "#000000"},
    "metal.steel": {"color": "#b8bcc2", "emissive": "#000000"},
    "plastic": {"color": "#d7d7dc", "emissive": "#000000"},
    "fabric": {"color": "#8d8d93", "emissive": "#000000"},
    "leather": {"color": "#6d4c33", "emissive": "#000000"},
    "ceramic": {"color": "#e8e6e3", "emissive": "#000000"},
}

MATERIAL_VOCAB: frozenset[str] = frozenset(MATERIAL_COLORS)

# Import-time invariant: the table must cover EXACTLY the frozen vocabulary and
# every entry must resolve to two valid #RRGGBB colors (a drift between the
# token vocabulary and the material table is a programming error).
if set(MATERIAL_COLORS) != set(MATERIAL_VOCABULARY):
    raise RuntimeError(
        "material table drift: MATERIAL_COLORS keys differ from "
        "MATERIAL_VOCABULARY"
    )
for _token, _values in MATERIAL_COLORS.items():
    if set(_values) != {"color", "emissive"}:
        raise RuntimeError(f"material entry {_token!r} must declare color+emissive")
    for _key, _hex in _values.items():
        if not isinstance(_hex, str) or not _COLOR_PATTERN.match(_hex):
            raise RuntimeError(
                f"material entry {_token!r}.{_key} is not a #RRGGBB color"
            )


def material_color(token: str) -> str:
    """Resolved base color of one validated material token.

    Raises ``KeyError`` for tokens outside the frozen vocabulary — callers are
    expected to validate ``material`` membership through ``AssetSpec`` parsing
    (the compiler therefore never falls back silently).
    """
    return MATERIAL_COLORS[token]["color"]


__all__ = [
    "MATERIAL_COLORS",
    "MATERIAL_VOCAB",
    "material_color",
]