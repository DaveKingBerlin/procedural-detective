"""Phase 13 — golden UNKNOWN-object declarative AssetSpec fixtures.

The four deterministic examples for the procedural asset path (objects NOT in
the catalog): antique ceremonial letter opener, unusual laboratory sample rack,
custom trophy, distinctive desk award. Each is a SAFE, RECOGNIZABLE multi-part
declarative spec that compiles through the trusted compiler; the rack and
trophy/award exercise the parented (depth-2) composition path.

The specs deliberately stress the bounded surface:
- the sample rack parents 4 rails to a frame (depth 1, bounded symmetry);
- the trophy chains base -> stem -> cup and the award chains base -> plaque ->
  figure (DEPTH 2 — the maximum nesting depth);
- every rotation/position/scale/dimension stays inside the documented bounds;
- all strings are ASCII, control-free, URL/path/script-free.

``GOLDEN_SPEC_CONTENT`` maps the casefold-normalized requestedName to the raw
spec JSON — exactly the script ``FakeAssetSpecProvider`` is built from.
Each spec's normalized-spec hash is content-addressed and stable across runs
(pinned by ``test_asset_compiler`` / ``test_generated_integration``).
"""

from __future__ import annotations

from typing import Mapping

# --------------------------------------------------------------------------- #
# 1. Antique Ceremonial Letter Opener (decor) — 3 parts, no parents
# --------------------------------------------------------------------------- #

ANTIQUE_LETTER_OPENER_NAME = "Antique Ceremonial Letter Opener"
ANTIQUE_LETTER_OPENER_SPEC = """{
  "canonicalName": "Antique Ceremonial Letter Opener",
  "category": "decor",
  "subtype": "ceremonial_letter_opener",
  "dimensions": {"x": 0.12, "y": 0.1, "z": 0.28},
  "parts": [
    {
      "id": "part_00",
      "role": "blade",
      "primitive": "box",
      "transform": {
        "position": {"x": 0.0, "y": 0.0, "z": 0.09},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.06, "y": 0.09, "z": 0.2}
      },
      "material": "metal.brass"
    },
    {
      "id": "part_01",
      "role": "guard",
      "primitive": "box",
      "transform": {
        "position": {"x": 0.0, "y": 0.0, "z": -0.02},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.14, "y": 0.06, "z": 0.06}
      },
      "material": "metal.brass"
    },
    {
      "id": "part_02",
      "role": "handle",
      "primitive": "cylinder",
      "transform": {
        "position": {"x": 0.0, "y": 0.0, "z": -0.14},
        "rotation": {"x": 1.5707963267948966, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.06, "y": 0.06, "z": 0.16}
      },
      "material": "wood.dark"
    }
  ]
}"""

# --------------------------------------------------------------------------- #
# 2. Unusual Laboratory Sample Rack (utility) — 5 parts, 4 rails parented to
#    the frame (bounded symmetry, depth 1)
# --------------------------------------------------------------------------- #

LAB_SAMPLE_RACK_NAME = "Unusual Laboratory Sample Rack"
LAB_SAMPLE_RACK_SPEC = """{
  "canonicalName": "Unusual Laboratory Sample Rack",
  "category": "utility",
  "subtype": "sample_rack",
  "dimensions": {"x": 0.5, "y": 0.4, "z": 0.4},
  "parts": [
    {
      "id": "part_00",
      "role": "frame",
      "primitive": "box",
      "transform": {
        "position": {"x": 0.0, "y": -0.1, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.5, "y": 0.08, "z": 0.4}
      },
      "material": "metal.steel"
    },
    {
      "id": "part_01",
      "role": "rail",
      "primitive": "cylinder",
      "transform": {
        "position": {"x": -0.18, "y": 0.0, "z": 0.0},
        "rotation": {"x": 1.5707963267948966, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.07, "y": 0.07, "z": 0.44}
      },
      "material": "metal.steel",
      "parentId": "part_00"
    },
    {
      "id": "part_02",
      "role": "rail",
      "primitive": "cylinder",
      "transform": {
        "position": {"x": -0.06, "y": 0.0, "z": 0.0},
        "rotation": {"x": 1.5707963267948966, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.07, "y": 0.07, "z": 0.44}
      },
      "material": "metal.steel",
      "parentId": "part_00"
    },
    {
      "id": "part_03",
      "role": "rail",
      "primitive": "cylinder",
      "transform": {
        "position": {"x": 0.06, "y": 0.0, "z": 0.0},
        "rotation": {"x": 1.5707963267948966, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.07, "y": 0.07, "z": 0.44}
      },
      "material": "metal.steel",
      "parentId": "part_00"
    },
    {
      "id": "part_04",
      "role": "rail",
      "primitive": "cylinder",
      "transform": {
        "position": {"x": 0.18, "y": 0.0, "z": 0.0},
        "rotation": {"x": 1.5707963267948966, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.07, "y": 0.07, "z": 0.44}
      },
      "material": "metal.steel",
      "parentId": "part_00"
    }
  ]
}"""

# --------------------------------------------------------------------------- #
# 3. Custom Trophy (decor) — 3 parts, base -> stem -> cup (DEPTH 2)
# --------------------------------------------------------------------------- #

CUSTOM_TROPHY_NAME = "Custom Trophy"
CUSTOM_TROPHY_SPEC = """{
  "canonicalName": "Custom Trophy",
  "category": "decor",
  "subtype": "trophy",
  "dimensions": {"x": 0.3, "y": 0.5, "z": 0.3},
  "parts": [
    {
      "id": "part_00",
      "role": "base",
      "primitive": "box",
      "transform": {
        "position": {"x": 0.0, "y": -0.22, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.3, "y": 0.08, "z": 0.22}
      },
      "material": "wood.dark"
    },
    {
      "id": "part_01",
      "role": "stem",
      "primitive": "cylinder",
      "transform": {
        "position": {"x": 0.0, "y": -0.06, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.08, "y": 0.3, "z": 0.08}
      },
      "material": "metal.brass",
      "parentId": "part_00"
    },
    {
      "id": "part_02",
      "role": "cup",
      "primitive": "cylinder",
      "transform": {
        "position": {"x": 0.0, "y": 0.17, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.18, "y": 0.14, "z": 0.18}
      },
      "material": "metal.brass",
      "parentId": "part_01"
    }
  ]
}"""

# --------------------------------------------------------------------------- #
# 4. Distinctive Desk Award (decor) — 3 parts, base -> plaque -> figure
#    (DEPTH 2)
# --------------------------------------------------------------------------- #

DESK_AWARD_NAME = "Distinctive Desk Award"
DESK_AWARD_SPEC = """{
  "canonicalName": "Distinctive Desk Award",
  "category": "decor",
  "subtype": "desk_award",
  "dimensions": {"x": 0.24, "y": 0.32, "z": 0.24},
  "parts": [
    {
      "id": "part_00",
      "role": "base",
      "primitive": "box",
      "transform": {
        "position": {"x": 0.0, "y": -0.11, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.24, "y": 0.08, "z": 0.18}
      },
      "material": "plastic"
    },
    {
      "id": "part_01",
      "role": "plaque",
      "primitive": "box",
      "transform": {
        "position": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.18, "y": 0.18, "z": 0.06}
      },
      "material": "metal.brass",
      "parentId": "part_00"
    },
    {
      "id": "part_02",
      "role": "figure",
      "primitive": "sphere",
      "transform": {
        "position": {"x": 0.0, "y": 0.16, "z": 0.02},
        "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 0.16, "y": 0.16, "z": 0.16}
      },
      "material": "metal.steel",
      "parentId": "part_01"
    }
  ]
}"""

# --------------------------------------------------------------------------- #
# provider script (casefold-normalized requestedName -> spec JSON)
# --------------------------------------------------------------------------- #

GOLDEN_SPEC_CONTENT: Mapping[str, str] = {
    ANTIQUE_LETTER_OPENER_NAME.casefold().strip(): ANTIQUE_LETTER_OPENER_SPEC,
    LAB_SAMPLE_RACK_NAME.casefold().strip(): LAB_SAMPLE_RACK_SPEC,
    CUSTOM_TROPHY_NAME.casefold().strip(): CUSTOM_TROPHY_SPEC,
    DESK_AWARD_NAME.casefold().strip(): DESK_AWARD_SPEC,
}

GOLDEN_SPEC_NAMES: tuple[str, ...] = (
    ANTIQUE_LETTER_OPENER_NAME,
    LAB_SAMPLE_RACK_NAME,
    CUSTOM_TROPHY_NAME,
    DESK_AWARD_NAME,
)

# Bounded unknown-object request list used by the integration tests + the fake
# provider (each mapping mirrors the service's ``unknown_asset_requests``).
GOLDEN_UNKNOWN_REQUESTS: tuple[dict[str, str], ...] = (
    {"objectId": "ceremonial_opener", "requestedName": ANTIQUE_LETTER_OPENER_NAME},
    {"objectId": "lab_rack", "requestedName": LAB_SAMPLE_RACK_NAME},
    {"objectId": "custom_trophy", "requestedName": CUSTOM_TROPHY_NAME},
    {"objectId": "desk_award", "requestedName": DESK_AWARD_NAME},
)

# Pinned canonical-spec hashes (sha256 of ``normalize_spec``) — content-addressed
# and stable across runs; asserted by ``test_asset_compiler`` /
# ``test_generated_integration`` so a change in the parser's canonical form is a
# LOUD failure (never a silent id drift).
NORMALIZED_SPEC_HASHES: Mapping[str, str] = {
    ANTIQUE_LETTER_OPENER_NAME: (
        "4e4f740ae23ce49a4f3f540fe709dac1319b454b56f847ea7d30c04052dbcce4"
    ),
    LAB_SAMPLE_RACK_NAME: (
        "706425d8c3db9cefa0db35b03c1406395db4d40fe0ee8ecfa7e7f4938dc511f7"
    ),
    CUSTOM_TROPHY_NAME: (
        "5407f8e9bac1ed38b1d7b99c09ed67e611b42b9b6aefcebe742852438ec18a7a"
    ),
    DESK_AWARD_NAME: (
        "af305d6d0090979c71353b2fbddd5fdab44819a79938ba13f5788bb3543c2373"
    ),
}

__all__ = [
    "ANTIQUE_LETTER_OPENER_NAME",
    "ANTIQUE_LETTER_OPENER_SPEC",
    "CUSTOM_TROPHY_NAME",
    "CUSTOM_TROPHY_SPEC",
    "DESK_AWARD_NAME",
    "DESK_AWARD_SPEC",
    "GOLDEN_SPEC_CONTENT",
    "GOLDEN_SPEC_NAMES",
    "GOLDEN_UNKNOWN_REQUESTS",
    "LAB_SAMPLE_RACK_NAME",
    "LAB_SAMPLE_RACK_SPEC",
    "NORMALIZED_SPEC_HASHES",
]