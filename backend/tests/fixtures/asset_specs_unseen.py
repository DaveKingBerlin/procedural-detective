"""Phase 14_5 — the three genuinely UNSEEN declarative AssetSpec fixtures.

The three project examples for the unseen-prompt-object path (objects that
MUST NOT exist in assets/catalog, KNOWN_OBJECT_TABLE, aliases or any other
production lookup):
- bronze ceremonial ice pick
- unusual forensic sample press
- carved ivory desk seal

They are FIXTURES ONLY (never imported by production code, never part of a
production lookup table): ``UNSEEN_SPEC_CONTENT`` keyed by the
casefold-normalized requested-name is exactly the script a dev-provider /
``FakeAssetSpecProvider`` is built from, so QA's dev-mode pipeline and the
Phase 14_5 tests resolve these unseen nouns through the GENERAL provider
mechanism (the extractor never special-cases them).

The three specs are deterministic, safe and multi-part and jointly exercise
ALL FOUR renderer primitives (box, cylinder, sphere, plane):
- the ice pick     (decor, 3 parts) — box + cylinder; its EVIDENCE identity
  comes from the DEV composition seam (placement evidenceId + forensic fact),
  explained inline below;
- the sample press (evidence, 4 parts, DEPTH-2 parent chain — the Phase 13
  depth-2 parent rule) — box + plane + cylinder + sphere;
- the desk seal    (decor, 3 parts) — box + cylinder + sphere.

Every string is ASCII, control-free and contains no URL/path/script tokens;
every number is finite and inside the documented AssetSpec bounds; colors are
bounded (#RRGGBB literals plus the frozen material table's resolved colors).
"""

from __future__ import annotations

from typing import Mapping

# --------------------------------------------------------------------------- #
# 1. Bronze Ceremonial Ice Pick (decor) — 3 parts
#
# PLACEMENT NOTE (documented Phase 14_5 choice): the spec's RENDER/anchor
# category is "decor" even though the prompt arks it as the killing weapon.
# Every kit's base golden set ALREADY occupies the four hard-evidence anchors
# (kitchen knife / letter opener / scissors + laptop's computer anchor), so a
# FIFTH "evidence"-category generated asset can NEVER be placed behind the
# golden truth-independence contract. The unseen weapon is therefore placed as
# a prompt-specific DECORATIVE-category prop and the DEV composition seam
# labels it EVIDENCE afterwards: the published placement carries the forensic
# evidenceId + inspect interaction and sits on an evidence-capable anchor
# (TABLE_PROP/GENERIC), and the solver never widens its weapon universe.
# --------------------------------------------------------------------------- #

BRONZE_ICE_PICK_NAME = "bronze ceremonial ice pick"
BRONZE_ICE_PICK_SPEC = """{
  "canonicalName": "Bronze Ceremonial Ice Pick",
  "category": "decor",
  "subtype": "ceremonial_ice_pick",
  "dimensions": {"x": 0.08, "y": 0.06, "z": 0.26},
  "parts": [
    {
      "id": "part_00",
      "role": "shaft",
      "primitive": "cylinder",
      "transform": {
        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.05, "y": 0.18, "z": 0.05}
      },
      "material": "metal.brass"
    },
    {
      "id": "part_01",
      "role": "point",
      "primitive": "box",
      "transform": {
        "position": {"x": 0.0, "y": 0.2, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.05, "y": 0.05, "z": 0.05}
      },
      "material": "metal.brass"
    },
    {
      "id": "part_02",
      "role": "handle",
      "primitive": "box",
      "transform": {
        "position": {"x": 0.0, "y": -0.19, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.05, "y": 0.06, "z": 0.05}
      },
      "material": "wood.dark"
    }
  ]
}"""

# --------------------------------------------------------------------------- #
# 2. Unusual Forensic Sample Press (evidence) — 4 parts, DEPTH-2 parent chain
#    frame -> platen -> ram/knob (the Phase 13 maximum nesting depth)
# --------------------------------------------------------------------------- #

FORENSIC_SAMPLE_PRESS_NAME = "unusual forensic sample press"
FORENSIC_SAMPLE_PRESS_SPEC = """{
  "canonicalName": "Unusual Forensic Sample Press",
  "category": "evidence",
  "subtype": "sample_press",
  "dimensions": {"x": 0.3, "y": 0.4, "z": 0.3},
  "parts": [
    {
      "id": "part_00",
      "role": "frame",
      "primitive": "box",
      "transform": {
        "position": {"x": 0.0, "y": -0.15, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.28, "y": 0.1, "z": 0.28}
      },
      "material": "metal.steel"
    },
    {
      "id": "part_01",
      "role": "platen",
      "primitive": "plane",
      "transform": {
        "position": {"x": 0.0, "y": 0.02, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.24, "y": 0.05, "z": 0.24}
      },
      "material": "metal.steel",
      "parentId": "part_00"
    },
    {
      "id": "part_02",
      "role": "ram",
      "primitive": "cylinder",
      "transform": {
        "position": {"x": 0.0, "y": 0.14, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.06, "y": 0.22, "z": 0.06}
      },
      "material": "metal.steel",
      "parentId": "part_01"
    },
    {
      "id": "part_03",
      "role": "knob",
      "primitive": "sphere",
      "transform": {
        "position": {"x": 0.0, "y": 0.36, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.08, "y": 0.08, "z": 0.08}
      },
      "material": "plastic",
      "parentId": "part_01"
    }
  ]
}"""

# --------------------------------------------------------------------------- #
# 3. Carved Ivory Desk Seal (decor) — 3 parts
# --------------------------------------------------------------------------- #

CARVED_IVORY_DESK_SEAL_NAME = "carved ivory desk seal"
CARVED_IVORY_DESK_SEAL_SPEC = """{
  "canonicalName": "Carved Ivory Desk Seal",
  "category": "decor",
  "subtype": "desk_seal",
  "dimensions": {"x": 0.16, "y": 0.2, "z": 0.16},
  "parts": [
    {
      "id": "part_00",
      "role": "base",
      "primitive": "box",
      "transform": {
        "position": {"x": 0.0, "y": -0.06, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.16, "y": 0.06, "z": 0.16}
      },
      "material": "wood.light"
    },
    {
      "id": "part_01",
      "role": "body",
      "primitive": "cylinder",
      "transform": {
        "position": {"x": 0.0, "y": 0.04, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.08, "y": 0.1, "z": 0.08}
      },
      "material": "wood.light",
      "sourceColor": "#f5ebdd"
    },
    {
      "id": "part_02",
      "role": "finial",
      "primitive": "sphere",
      "transform": {
        "position": {"x": 0.0, "y": 0.14, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.05, "y": 0.05, "z": 0.05}
      },
      "material": "ceramic"
    }
  ]
}"""

# --------------------------------------------------------------------------- #
# dev-provider script (casefold-normalized requested-name -> spec JSON)
# --------------------------------------------------------------------------- #

UNSEEN_SPEC_CONTENT: Mapping[str, str] = {
    BRONZE_ICE_PICK_NAME.casefold().strip(): BRONZE_ICE_PICK_SPEC,
    FORENSIC_SAMPLE_PRESS_NAME.casefold().strip(): FORENSIC_SAMPLE_PRESS_SPEC,
    CARVED_IVORY_DESK_SEAL_NAME.casefold().strip(): CARVED_IVORY_DESK_SEAL_SPEC,
}

UNSEEN_SPEC_NAMES: tuple[str, ...] = (
    BRONZE_ICE_PICK_NAME,
    FORENSIC_SAMPLE_PRESS_NAME,
    CARVED_IVORY_DESK_SEAL_NAME,
)

__all__ = [
    "BRONZE_ICE_PICK_NAME",
    "BRONZE_ICE_PICK_SPEC",
    "CARVED_IVORY_DESK_SEAL_NAME",
    "CARVED_IVORY_DESK_SEAL_SPEC",
    "FORENSIC_SAMPLE_PRESS_NAME",
    "FORENSIC_SAMPLE_PRESS_SPEC",
    "UNSEEN_SPEC_CONTENT",
    "UNSEEN_SPEC_NAMES",
]