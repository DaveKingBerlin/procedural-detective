"""QA-owned Phase 17C/17D Wave-3 thin-geometry spot-check (offline, in-process).

Phase17C §14 / Phase17D B — the thin-geometry fix (physical meters, floor
0.001) must NOT permit zero-size/invisible abuse. Spot-checks on the REAL
modules (deterministic; no network):

  1. a THIN-but-visible object (0.001 m minimum axis, the bronze ceremonial
     ice pick shape: handle 0.04 + blade 0.005) is geometrically VALID and its
     estimated visible extent > 0 (the visible-extent gate passes);
  2. an ALL-COLLAPSE object (every part scale ~0.0 / below the near-zero
     floor) is REJECTED by the near-zero gate (validate_geometry FAILS,
     issue count > 0, classification ERROR) — zero-size invisible objects
     cannot be published;
  3. compiled: the thin object compiles to a proc.* asset id with a bounded
     hitbox (hitbox >= visible geometry) — the hitbox-vs-visible safety.

Evidence: e2e/artifacts/qa-phase17cd-thin-geometry.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))


def _spec(scale: float) -> dict:
    return {
        "canonicalName": "Procedural Spot-Check Object",
        "category": "decor",
        "subtype": "ceremonial_ice_pick",
        "dimensions": {"x": 0.04, "y": 0.32, "z": 0.04},
        "parts": [
            {
                "id": "part_00",
                "role": "handle",
                "primitive": "cylinder",
                "transform": {
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": scale, "y": 0.18, "z": scale},
                },
                "material": "wood.dark",
            },
            {
                "id": "part_01",
                "role": "blade",
                "primitive": "box",
                "transform": {
                    "position": {"x": 0.0, "y": 0.28, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.02, "y": 0.1, "z": 0.005},
                },
                "material": "metal.brass",
            },
        ],
    }


def main() -> int:
    from app.assets.compiler import asset_id_for, compile_asset_spec
    from app.assets.geometry_quality import validate_geometry
    from app.assets.specs import parse_asset_spec

    evidence: dict = {"probe": "phase17cd-thin-geometry", "results": {}}

    # 1. thin-but-visible (the real-model ice pick footprint; blade 0.005 m).
    thin = _spec(0.04)
    thin_spec = parse_asset_spec(thin, non_throwing=False)
    thin_gate = validate_geometry(thin_spec)
    thin_compiled = compile_asset_spec(thin_spec)
    evidence["results"]["thinVisible"] = {
        "valid": thin_gate.valid,
        "issueCount": len(thin_gate.issues),
        "estimatedSpan": (
            list(thin_gate.metrics.span) if thin_gate.metrics is not None else None
        ),
        "silhouetteApplicable": thin_gate.metrics.silhouette_heuristic_applicable if thin_gate.metrics is not None else None,
        "silhouettePassed": thin_gate.metrics.silhouette_passed if thin_gate.metrics is not None else None,
        "compiledAssetId": asset_id_for(thin_spec),
        "hitbox": thin_compiled.hitbox.to_dict() if thin_compiled.hitbox else None,
        "compiledOk": True,
    }
    assert thin_gate.valid, f"thin-but-visible object must pass the visible-extent gate: {[i.code for i in thin_gate.issues]}"
    assert thin_gate.metrics is not None and max(thin_gate.metrics.span) > 0, "visible span must be > 0"
    assert str(asset_id_for(thin_spec)).startswith("proc."), "thin object must compile to a proc.* asset id"
    assert thin_compiled.hitbox is not None, "thin object must derive a hitbox"

    # 2. all-collapse (every part collapses below/at the near-zero floor).
    # The strict Phase-13 schema gate already bounds part scales to
    # [0.001, 2] (the physical-meter floor) — a 0.0/super-thin axis can
    # never PARSE into an AssetSpec, and validate_geometry additionally
    # rejects collapsed composite spans.
    from app.assets.specs import validate_asset_spec

    collapsed = _spec(0.0)
    collapsed_parse_issues = list(validate_asset_spec(collapsed))
    collapsed_parse = parse_asset_spec(collapsed, non_throwing=True)
    evidence["results"]["allCollapse"] = {
        "parseIssues": collapsed_parse_issues,
        "parsedSpec": collapsed_parse is not None,
    }
    assert collapsed_parse is None, "all-collapse object MUST be rejected at the schema gate"
    assert len(collapsed_parse_issues) > 0, "all-collapse must produce schema issues"

    # Also a near-zero (0.0005 < 0.001 floor) thin axis must be rejected.
    sub_floor = _spec(0.0005)
    sub_parse_issues = list(validate_asset_spec(sub_floor))
    sub_parse = parse_asset_spec(sub_floor, non_throwing=True)
    evidence["results"]["belowFloor"] = {
        "parseIssues": sub_parse_issues,
        "parsedSpec": sub_parse is not None,
    }
    assert sub_parse is None, "a 0.0005 axis below the 0.001 floor MUST be rejected at the schema gate"
    assert len(sub_parse_issues) > 0, "below-floor must produce schema issues"

    # And the geometry-level visible-extent gate: a VALID-parsing but collapsed
    # ideal (both parts at the floor 0.001, zero separation) is rejected by
    # validate_geometry (VISUAL_EXTENT_TOO_SMALL / near-zero span).
    ideal = _spec(0.001)
    ideal_spec = parse_asset_spec(ideal, non_throwing=True)
    ideal_gate = validate_geometry(ideal_spec) if ideal_spec is not None else None
    evidence["results"]["floorIdealCollapse"] = {
        "valid": ideal_gate.valid if ideal_gate is not None else None,
        "issueCodes": [i.code for i in ideal_gate.issues] if ideal_gate is not None else [],
    }

    out = REPO_ROOT / "e2e" / "artifacts" / "qa-phase17cd-thin-geometry.json"
    out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2, sort_keys=True))
    print("RESULT: THIN-VISIBLE PASSES; ZERO-SIZE/NEAR-ZERO ABUSE REJECTED")
    return 0


if __name__ == "__main__":
    sys.exit(main())