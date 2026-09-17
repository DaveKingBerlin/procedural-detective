"""Phase 12 — bounded parametric variants (§Variant): apply/resolve behavior.

Covers the Phase 12 required variant tests: parametric variants stay within
allowed bounds, unknown variant parameters are rejected, extreme scale cannot
escape the declared bounds, color/material/state values are allowlist-only,
the variant view is frozen + merged, and ``resolve_with_variant`` emits the
reserved ``PARAMETRIC_VARIANT`` provenance (never for fallback/ambiguous
resolutions or assets without declared variants).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.assets.catalog import (
    MATERIAL_VOCABULARY,
    STATE_VOCABULARY,
    load_catalog,
)
from app.assets.resolver import (
    AssetVariantError,
    Provenance,
    apply_variant,
    resolve_with_variant,
)

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "assets" / "catalog" / "catalog.json"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(MANIFEST_PATH)


def _any_variant_asset(catalog):
    """The first composite asset that declares variants (deterministic)."""
    for asset in catalog.assets:
        if asset.variants:
            return asset
    raise AssertionError("catalog has no composite asset with variants")


# --------------------------------------------------------------------------- #
# apply_variant — view construction + bounds
# --------------------------------------------------------------------------- #


def test_apply_variant_returns_bounded_frozen_view(catalog):
    asset = catalog.by_id["PROP_KITCHEN_KNIFE_01"]
    view = apply_variant(asset, {"material": "metal.brass", "state": "weathered"})
    assert view.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert view.template_id == "blade_chef"
    assert view.material == "metal.brass"
    assert view.state == "weathered"
    assert 0.9 <= view.scale <= 1.1
    assert view.colors["blade"] == "#c8ccd4"
    assert view.colors["handle"] == "#5a3b22"


def test_apply_variant_defaults_when_params_empty(catalog):
    asset = catalog.by_id["PROP_KITCHEN_KNIFE_01"]
    view = apply_variant(asset, {})
    assert view.material == "metal.steel"  # first declared default
    assert view.state == "clean"
    assert view.scale == 1.0


def test_apply_variant_accepts_asset_id_string(catalog):
    view = apply_variant("PROP_KITCHEN_KNIFE_01", {"state": "weathered"}, catalog=catalog)
    assert view.asset_id == "PROP_KITCHEN_KNIFE_01"


def test_apply_variant_unknown_parameter_rejected(catalog):
    asset = catalog.by_id["PROP_KITCHEN_KNIFE_01"]
    with pytest.raises(AssetVariantError) as excinfo:
        apply_variant(asset, {"texture": "grain"})
    assert "unknown variant parameter 'texture'" in str(excinfo.value)


def test_apply_variant_value_not_in_allowlist_rejected(catalog):
    asset = catalog.by_id["PROP_KITCHEN_KNIFE_01"]
    with pytest.raises(AssetVariantError):
        apply_variant(asset, {"material": "obsidian"})
    with pytest.raises(AssetVariantError):
        apply_variant(asset, {"state": "glowing"})
    with pytest.raises(AssetVariantError):
        apply_variant(
            catalog.by_id["PROP_USB_STICK_01"],
            {"color": "#123456"},  # not in the declared color allowlist
        )


def test_apply_variant_extreme_scale_cannot_escape_declared_bounds(catalog):
    """Extreme scale is rejected in effect: the applied value is clamped into
    the asset's declared [min, max] and NEVER reaches the view."""
    asset = catalog.by_id["PROP_TABLE_01"]  # scale bounds [0.8, 1.0]
    assert apply_variant(asset, {"scale": 1000.0}).scale == 1.0
    assert apply_variant(asset, {"scale": -1000.0}).scale == 0.8
    assert apply_variant(asset, {"scale": 0.9}).scale == 0.9
    knife = catalog.by_id["PROP_KITCHEN_KNIFE_01"]  # scale bounds [0.9, 1.1]
    assert apply_variant(knife, {"scale": 10.0}).scale == 1.1


def test_apply_variant_non_numeric_scale_rejected(catalog):
    asset = catalog.by_id["PROP_KITCHEN_KNIFE_01"]
    with pytest.raises(AssetVariantError):
        apply_variant(asset, {"scale": "huge"})
    with pytest.raises(AssetVariantError):
        apply_variant(asset, {"scale": True})


def test_nan_scale_rejected_by_apply_variant(catalog):
    """DEF-065: NaN scale must raise AssetVariantError, never reach the view
    (Python's min/max propagate NaN and would violate the declared bounds)."""
    knife = catalog.by_id["PROP_KITCHEN_KNIFE_01"]
    with pytest.raises(AssetVariantError) as excinfo:
        apply_variant(knife, {"scale": float("nan")})
    assert "finite number" in str(excinfo.value)
    # An asset WITHOUT declared variants also raises (never returns nan).
    desk = catalog.by_id["PROP_DESK_01"]
    assert not desk.variants
    with pytest.raises(AssetVariantError):
        apply_variant(desk, {"scale": float("nan")})


def test_nan_scale_rejected_by_resolve_with_variant(catalog):
    """DEF-065: the resolve_with_variant path rejects NaN scale too."""
    with pytest.raises(AssetVariantError) as excinfo:
        resolve_with_variant(
            {"requestedName": "chef knife"},
            {"scale": float("nan")},
            catalog=catalog,
        )
    assert "finite number" in str(excinfo.value)
    with pytest.raises(AssetVariantError):
        resolve_with_variant(
            {"requestedName": "PROP_DESK_01"},
            {"scale": float("nan")},
            catalog=catalog,
        )


def test_infinite_scale_rejected_as_non_finite(catalog):
    """DEF-065: ±Inf are non-finite and are rejected before clamping, so they
    can never reach the view either."""
    knife = catalog.by_id["PROP_KITCHEN_KNIFE_01"]
    for infinite in (float("inf"), float("-inf")):
        with pytest.raises(AssetVariantError) as excinfo:
            apply_variant(knife, {"scale": infinite})
        assert "finite number" in str(excinfo.value)
        with pytest.raises(AssetVariantError):
            resolve_with_variant(
                {"requestedName": "PROP_KITCHEN_KNIFE_01"},
                {"scale": infinite},
                catalog=catalog,
            )


def test_finite_extreme_scale_still_clamps(catalog):
    """DEF-065 regression: large FINITE values keep the documented clamp —
    they can never escape the declared bounds."""
    knife = catalog.by_id["PROP_KITCHEN_KNIFE_01"]  # scale bounds [0.9, 1.1]
    assert apply_variant(knife, {"scale": 1e9}).scale == 1.1
    assert apply_variant(knife, {"scale": -1e9}).scale == 0.9
    assert 0.9 <= apply_variant(knife, {"scale": 1e9}).scale <= 1.1


def test_finite_in_bounds_scale_passes(catalog):
    """A finite in-bounds scale keeps passing through unchanged."""
    knife = catalog.by_id["PROP_KITCHEN_KNIFE_01"]
    assert apply_variant(knife, {"scale": 0.95}).scale == 0.95
    assert apply_variant(knife, {"scale": 1.0}).scale == 1.0


def test_apply_variant_color_merges_on_primary_tone(catalog):
    """A color param merges into the colors map on the deterministic primary
    tone key, keeping the multi-tone parts intact."""
    asset = catalog.by_id["PROP_USB_STICK_01"]  # colors {shell, cap}
    view = apply_variant(asset, {"color": "#d24d35"})
    assert view.colors["shell"] == "#d24d35"  # first key = primary
    assert view.colors["cap"] == "#d24d35"  # unchanged from base
    asset = catalog.by_id["PROP_PHONE_01"]  # colors {body, screen}
    view = apply_variant(asset, {"color": "#5a1830"})
    assert view.colors["body"] == "#5a1830"  # 'body' is a preferred primary key
    assert view.colors["screen"] == "#0e1116"


def test_apply_variant_asset_without_variants_rejected(catalog):
    for asset_id in ("PROP_FALLBACK_01", "PROP_DESK_01"):
        asset = catalog.by_id[asset_id]
        assert not asset.variants
        with pytest.raises(AssetVariantError) as excinfo:
            apply_variant(asset, {})
        assert "declares no variants" in str(excinfo.value)


def test_apply_variant_unknown_asset_id_rejected(catalog):
    with pytest.raises(AssetVariantError) as excinfo:
        apply_variant("PROP_NOPE_01", {}, catalog=catalog)
    assert "unknown asset" in str(excinfo.value)


def test_parametric_variants_within_allowed_bounds(catalog):
    """Every declared variant of every composite asset, when applied with its
    own declared defaults, stays inside the frozen safe bounds."""
    checked = 0
    for asset in catalog.assets:
        if not asset.variants:
            continue
        for variant in asset.variants:
            params = {
                key: spec.default for key, spec in variant.params.items() if spec is not None
            }
            view = apply_variant(asset, params)
            s = view.scale
            spec = next(
                (v.params[key] for v in asset.variants
                 for key in v.params if key == "scale" and v.params[key] is not None),
                None,
            )
            if spec is not None:
                assert spec.min_value <= s <= spec.max_value
            else:
                assert s == 1.0
            if view.material is not None:
                assert view.material in MATERIAL_VOCABULARY
            if view.state is not None:
                assert view.state in STATE_VOCABULARY
            for hex_value in view.colors.values():
                assert len(hex_value) == 7 and hex_value.startswith("#")
            checked += 1
    assert checked >= 70  # the Phase 12 catalog declares a rich variant set


def test_every_composite_variant_default_is_allowlisted(catalog):
    """Schema/behavior link: each declared variant's defaults are all
    allowlist members (a variant can always be materialized without error)."""
    for asset in catalog.assets:
        for variant in asset.variants:
            for key, spec in variant.params.items():
                if spec is None or key == "scale":
                    continue
                assert spec.default in spec.allowlist, (asset.asset_id, variant.name, key)


# --------------------------------------------------------------------------- #
# resolve_with_variant — PARAMETRIC_VARIANT provenance
# --------------------------------------------------------------------------- #


def test_resolve_with_variant_reports_parametric_provenance(catalog):
    resolution = resolve_with_variant(
        {"requestedName": "PROP_KITCHEN_KNIFE_01"},
        {"state": "damaged"},
        catalog=catalog,
    )
    assert resolution.resolved is True
    assert resolution.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert resolution.provenance is Provenance.PARAMETRIC_VARIANT
    assert resolution.version == 1
    assert resolution.view.state == "damaged"
    assert resolution.view.template_id == "blade_chef"


def test_resolve_with_variant_accepts_alias_and_semantic_requests(catalog):
    alias = resolve_with_variant(
        {"requestedName": "chef knife"},
        {"state": "weathered"},
        catalog=catalog,
    )
    assert alias.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert alias.provenance is Provenance.PARAMETRIC_VARIANT

    semantic = resolve_with_variant(
        {
            "requestedName": "weapon sharp blade",
            "categoryHint": "evidence",
            "subtypeHint": "sharp",
            "tags": ["weapon", "sharp", "blade"],
        },
        {"material": "metal.brass"},
        catalog=catalog,
    )
    assert semantic.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert semantic.view.material == "metal.brass"


def test_resolve_with_variant_fallback_rejected(catalog):
    with pytest.raises(AssetVariantError) as excinfo:
        resolve_with_variant({"requestedName": "ancient dragon relic"}, {}, catalog=catalog)
    assert "FALLBACK" in str(excinfo.value)


def test_resolve_with_variant_ambiguous_rejected(catalog):
    with pytest.raises(AssetVariantError) as excinfo:
        resolve_with_variant(
            {
                "requestedName": "sharp thing",
                "categoryHint": "evidence",
                "subtypeHint": "sharp",
                "tags": ["blade"],
            },
            {},
            catalog=catalog,
        )
    assert "unresolved" in str(excinfo.value)


def test_resolve_with_variant_invalid_params_rejected(catalog):
    with pytest.raises(AssetVariantError):
        resolve_with_variant(
            {"requestedName": "PROP_KITCHEN_KNIFE_01"},
            {"state": "glowing"},
            catalog=catalog,
        )