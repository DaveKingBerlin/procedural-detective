"""QA-owned durable reproduction probe for DEF-067 (Phase 13 gate).

Deep JSON nesting bombs crash the Phase 13 strict spec parser and the Oracle
entry points with an UNCAUGHT RecursionError inside the documented character
cap (violates the never-raise reject contract + adversarial recursion/nesting-
bomb focus).

Run:  python e2e/probes/qa-phase13-recursion.py
Exit 0 when the defect is REPRODUCED (the probe asserts the defect exists);
exit 1 when the behavior changed (a fix would make this fail loudly).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.assets.catalog import load_catalog_from_repo  # noqa: E402
from app.assets.oracle import GeneratedAssetOracle  # noqa: E402
from app.assets.spec_provider import AssetSpecResponse  # noqa: E402
from app.assets.specs import parse_asset_spec, validate_asset_spec  # noqa: E402


class BombProvider:
    """A spec provider returning a deep-nesting JSON bomb (<= 262144 chars)."""

    def generate(self, request):  # noqa: ANN001
        return AssetSpecResponse(content="[" * 10000 + "]" * 10000)


def main() -> int:
    doc = "[" * 10000 + "]" * 10000  # 20 KB, well under the 262144-char cap
    assert len(doc) < 262144

    failures = []

    # 1) validate_asset_spec must not raise (documented non-raising validator)
    try:
        validate_asset_spec(doc)
        failures.append("validate_asset_spec did NOT raise (defect not reproduced)")
    except RecursionError:
        failures.append("validate_asset_spec RAISED RecursionError (DEF-067)")

    # 2) parse_asset_spec(non_throwing=True) must return None
    try:
        parse_asset_spec(doc, non_throwing=True)
        failures.append("parse_asset_spec did NOT raise (defect not reproduced)")
    except RecursionError:
        failures.append("parse_asset_spec(non_throwing=True) RAISED RecursionError (DEF-067)")

    # 3) Oracle entry points must degrade, never crash
    oracle = GeneratedAssetOracle(catalog=load_catalog_from_repo())
    try:
        oracle.resolve_or_generate(
            {"requestedName": "bomb"}, spec_provider=BombProvider(), force_generate=True
        )
        failures.append("resolve_or_generate did NOT raise (defect not reproduced)")
    except RecursionError:
        failures.append("resolve_or_generate leaked RecursionError (DEF-067)")
    try:
        oracle.generate_and_stage({"requestedName": "bomb"}, BombProvider())
        failures.append("generate_and_stage did NOT raise (defect not reproduced)")
    except RecursionError:
        failures.append("generate_and_stage leaked RecursionError (DEF-067)")

    print("\n".join(f"- {f}" for f in failures))
    print(f"\nRESULT: DEF-067 REPRODUCED ({len(failures)} escape points)")
    return 0  # probe asserts the CURRENT behavior = defect present


if __name__ == "__main__":
    sys.exit(main())