"""Phase 10 — Asset Oracle resolver behavior.

Covers the Phase 10 required resolution tests: exact-id, canonical-name,
alias (including legacy dot-aliases), semantic match with explicit
confidence, ambiguous ties (never an arbitrary winner), explicit fallback,
case/Unicode normalization, deterministic versions/provenances, dict-order
stability, golden-case coverage through the catalog (NOT the legacy switch),
and the internal ``resolve_placements_provenance`` diagnostic (golden mapping
+ dataclass/dict parity + published-service wiring).
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.assets import (
    AssetRequest,
    AssetResolution,
    Provenance,
    load_catalog,
    load_catalog_from_repo,
    normalize,
    resolve,
    resolve_placements_provenance,
)
from app.assets.resolver import AssetResolver

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "assets" / "catalog" / "catalog.json"
DEV_MODE_CASE = BACKEND_DIR / "app" / "services" / "dev_mode_case.json"

GOLDEN_ASSET_IDS = (
    "PROP_KITCHEN_KNIFE_01",
    "PROP_LETTER_OPENER_01",
    "PROP_SCISSORS_01",
    "PROP_VASE_01",
    "PROP_LAPTOP_01",
    "PROP_TABLE_01",
    "DOOR_APARTMENT_01",
    "PROP_LAMP_01",
    "PROP_BODY_PLACEHOLDER_01",
)


def _knife() -> AssetResolution:
    return resolve({"requestedName": "PROP_KITCHEN_KNIFE_01"})


def _golden_placements() -> list[dict]:
    raw = json.loads(DEV_MODE_CASE.read_text(encoding="utf-8"))
    return json.loads(raw["world_graph"][0])["worldGraph"]["placements"]


# --------------------------------------------------------------------------- #
# exact / canonical / alias
# --------------------------------------------------------------------------- #


def test_exact_id_resolution_catalog_exact():
    result = _knife()
    assert result.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert result.provenance is Provenance.CATALOG_EXACT
    assert result.resolved is True
    assert result.ambiguous is False
    assert result.version == 1
    assert result.matched_alias is None


def test_canonical_name_resolution_catalog_exact():
    for name in ("Kitchen Knife", "kitchen knife"):
        result = resolve({"requestedName": name})
        assert result.asset_id == "PROP_KITCHEN_KNIFE_01"
        assert result.provenance is Provenance.CATALOG_EXACT
        assert result.resolved is True


def test_alias_resolution_catalog_alias():
    result = resolve({"requestedName": "chef knife"})
    assert result.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert result.provenance is Provenance.CATALOG_ALIAS
    assert result.matched_alias == "chef knife"
    assert result.resolved is True


def test_legacy_dot_alias_resolution():
    result = resolve({"requestedName": "apartment.laptop.basic"})
    assert result.asset_id == "PROP_LAPTOP_01"
    assert result.provenance is Provenance.CATALOG_ALIAS
    assert result.matched_alias == "apartment.laptop.basic"


# --------------------------------------------------------------------------- #
# semantic match / ambiguity / fallback
# --------------------------------------------------------------------------- #


def test_semantic_match_weapon_sharp_blade():
    """Tags/category request -> knife with the documented confidence."""
    result = resolve(
        {
            "requestedName": "weapon sharp blade",
            "categoryHint": "evidence",
            "subtypeHint": "sharp",
            "tags": ["weapon", "sharp", "blade"],
        }
    )
    assert result.asset_id == "PROP_KITCHEN_KNIFE_01"
    assert result.provenance is Provenance.SEMANTIC_MATCH
    assert result.resolved is True
    assert result.confidence == 9.0  # 2 tags (+6) + category (+2) + subtype (+1)


def test_semantic_match_electronics_device_computer():
    result = resolve(
        {
            "requestedName": "electronics device computer",
            "categoryHint": "electronics",
            "subtypeHint": "computer",
            "tags": ["electronics", "device", "computer"],
        }
    )
    assert result.asset_id == "PROP_LAPTOP_01"
    assert result.provenance is Provenance.SEMANTIC_MATCH
    assert result.confidence == 12.0  # 3 tags (+9) + category (+2) + subtype (+1)


def test_semantic_ambiguous_tie_never_arbitrary_winner():
    """Three evidence-sharp items tie at 6.0 -> AMBIGUOUS, no winner."""
    result = resolve(
        {
            "requestedName": "sharp thing",
            "categoryHint": "evidence",
            "subtypeHint": "sharp",
            "tags": ["blade"],
        }
    )
    assert result.ambiguous is True
    assert result.resolved is False
    assert result.asset_id == ""
    assert len(result.candidates) >= 2
    assert result.candidates == (
        "PROP_KITCHEN_KNIFE_01",
        "PROP_LETTER_OPENER_01",
        "PROP_SCISSORS_01",
    )
    assert result.confidence == 6.0


def test_unknown_object_falls_back_explicitly():
    result = resolve({"requestedName": "ancient dragon relic"})
    assert result.asset_id == "PROP_FALLBACK_01"
    assert result.provenance is Provenance.FALLBACK
    assert result.resolved is True
    assert result.ambiguous is False


def test_semantic_below_threshold_falls_back():
    """categoryHint alone can score below the 3.0 threshold -> FALLBACK."""
    result = resolve({"requestedName": "some decor", "categoryHint": "decor"})
    assert result.provenance is Provenance.FALLBACK
    assert result.asset_id == "PROP_FALLBACK_01"


def test_required_interaction_filters_candidates():
    """No sharp evidence asset supports 'open' -> no valid candidate."""
    result = resolve(
        {
            "requestedName": "sharp",
            "categoryHint": "evidence",
            "subtypeHint": "sharp",
            "tags": ["blade"],
            "requiredInteraction": "open",
        }
    )
    assert result.provenance is Provenance.FALLBACK


def test_required_evidence_capabilities_filters_candidates():
    result = resolve(
        {
            "requestedName": "sharp",
            "categoryHint": "evidence",
            "subtypeHint": "sharp",
            "tags": ["blade"],
            "requiredEvidenceCapabilities": ["digital"],
        }
    )
    assert result.provenance is Provenance.FALLBACK
    result = resolve(
        {
            "requestedName": "laptop stuff",
            "categoryHint": "electronics",
            "subtypeHint": "computer",
            "tags": ["computer"],
            "requiredEvidenceCapabilities": ["digital"],
        }
    )
    assert result.asset_id == "PROP_LAPTOP_01"
    assert result.provenance is Provenance.SEMANTIC_MATCH


# --------------------------------------------------------------------------- #
# normalization
# --------------------------------------------------------------------------- #


def test_case_and_unicode_normalization():
    # Uses an explicit escape so the test file stays ASCII-safe on Windows.
    for name in ("KITCHEN KNIFE", "K\u00eftchen knife"):
        result = resolve({"requestedName": name})
        assert result.asset_id == "PROP_KITCHEN_KNIFE_01"
        assert result.provenance is Provenance.CATALOG_EXACT


def test_normalize_shares_constraints_concept():
    assert normalize("KITCHEN KNIFE") == normalize("kitchen_knife") == "kitchenknife"
    assert normalize("€240,000 embezzlement") == "€240000embezzlement"


# --------------------------------------------------------------------------- #
# golden-case coverage + provenance diagnostics
# --------------------------------------------------------------------------- #


def test_golden_dev_case_asset_ids_resolve_through_catalog():
    """Every dev_mode_case assetId resolves via the resolver to the SAME id
    with CATALOG_EXACT/ALIAS provenance — the golden never needs the legacy
    ad-hoc registry switch."""
    for asset_id in GOLDEN_ASSET_IDS:
        result = resolve({"requestedName": asset_id})
        assert result.resolved is True, asset_id
        assert result.provenance in (
            Provenance.CATALOG_EXACT,
            Provenance.CATALOG_ALIAS,
        ), asset_id
        assert result.asset_id == asset_id


def test_resolve_placements_provenance_golden_mapping():
    """The internal diagnostic returns the expected objectId -> provenance."""
    placements = _golden_placements()
    expected = {
        "kitchen_knife": "CATALOG_EXACT",
        "letter_opener": "CATALOG_EXACT",
        "scissors": "CATALOG_EXACT",
        "vase_01": "CATALOG_EXACT",
        "apartment_laptop": "CATALOG_EXACT",
        "apartment_table": "CATALOG_EXACT",
        "apartment_door": "CATALOG_EXACT",
        "apartment_lamp": "CATALOG_EXACT",
        "victim_body_placeholder": "CATALOG_EXACT",
    }
    assert len(placements) == 9
    assert resolve_placements_provenance(placements) == expected


def test_resolve_placements_provenance_accepts_typed_placements():
    """The same diagnostic accepts dataclass placements (PlacementSpec)."""
    from app.generation.schemas import PlacementSpec

    placements = _golden_placements()
    typed = [
        PlacementSpec(
            object_id=str(p["objectId"]),
            asset_id=str(p["assetId"]),
            location_id=str(p["locationId"]),
            anchor=str(p["anchor"]),
            interaction=str(p["interaction"]),
            evidence_id=p.get("evidenceId"),
        )
        for p in placements
    ]
    assert resolve_placements_provenance(typed) == (
        resolve_placements_provenance(placements)
    )


def test_published_golden_provenance_via_service_wiring(generation_service):
    """The generation/publication wiring records provenance for the golden."""
    session = generation_service.create_anonymous_quota_session()
    started = generation_service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert started.status == "PUBLISHED"
    provenance = generation_service._last_publish_provenance
    assert provenance is not None
    assert len(provenance) == 9
    assert all(value == "CATALOG_EXACT" for value in provenance.values())
    assert set(provenance) == {
        "kitchen_knife",
        "letter_opener",
        "scissors",
        "vase_01",
        "apartment_laptop",
        "apartment_table",
        "apartment_door",
        "apartment_lamp",
        "victim_body_placeholder",
    }


def test_published_payload_bytes_unaffected_by_wiring(generation_service):
    """The provenance wiring leaves the public payload byte-identical."""
    session = generation_service.create_anonymous_quota_session()
    started = generation_service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    from app.services.publication import public_case_dict_from_payload

    row = generation_service._store.get_published(started.case_id, 1)
    payload = json.loads(row.payload_json)
    dto = public_case_dict_from_payload(payload)
    asset_ids = {
        placement["assetId"]
        for placement in dto["worldGraph"]["placements"]
    }
    assert asset_ids == set(GOLDEN_ASSET_IDS)
    # The DTO carries only assetId + safe metadata (no catalog colors etc.).
    assert all(
        set(placement) == {
            "objectId",
            "assetId",
            "locationId",
            "anchor",
            "interaction",
            "evidenceId",
        }
        for placement in dto["worldGraph"]["placements"]
    )


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #


def test_catalog_version_and_provenance_deterministic():
    """Two loads -> identical versions/provenances for a request battery."""
    catalog_a = load_catalog(MANIFEST_PATH)
    catalog_b = load_catalog(MANIFEST_PATH)
    assert catalog_a.catalog_version == catalog_b.catalog_version == 1
    requests = [
        AssetRequest(requested_name="PROP_LAMP_01"),
        AssetRequest(requested_name="chef knife"),
        AssetRequest(requested_name="dragon"),
        AssetRequest(
            requested_name="weapon sharp blade",
            category_hint="evidence",
            subtype_hint="sharp",
            tags=("weapon", "sharp", "blade"),
        ),
    ]
    for request in requests:
        from app.assets.resolver import AssetResolver

        first = AssetResolver(catalog_a).resolve_request(request)
        second = AssetResolver(catalog_b).resolve_request(request)
        assert first == second
        assert first.catalog_version == 1


def test_resolution_order_stable_under_shuffled_dicts():
    """Raw request dicts in any key order resolve to identical results."""
    base = {
        "requestedName": "chef knife",
        "categoryHint": "evidence",
        "subtypeHint": "sharp",
        "tags": ["weapon", "sharp", "blade"],
        "requiredEvidenceCapabilities": [],
    }
    shuffled = {
        "subtypeHint": "sharp",
        "tags": ["weapon", "sharp", "blade"],
        "requiredEvidenceCapabilities": [],
        "categoryHint": "evidence",
        "requestedName": "chef knife",
    }
    assert resolve(base) == resolve(shuffled)
    snake = {
        "requested_name": "chef knife",
        "category_hint": "evidence",
        "subtype_hint": "sharp",
        "tags": ["weapon", "sharp", "blade"],
        "required_evidence_capabilities": [],
    }
    assert resolve(base) == resolve(snake)


def test_identical_inputs_resolve_identically_across_process_calls():
    """Repeated calls with equal inputs return equal resolutions."""
    first = resolve({"requestedName": "KITCHEN KNIFE"})
    second = resolve({"requestedName": "KITCHEN KNIFE"})
    assert first == second
    assert first.asset_id == "PROP_KITCHEN_KNIFE_01"


def test_fallback_resolution_is_always_neutral():
    """DEF-058: a FALLBACK resolution can only ever yield the catalog's
    neutral (utility, non-interactable) placeholder — the invariant is
    enforced at load/construction, so it is guaranteed at resolution time."""
    result = resolve({"requestedName": "totally unknown relic"})
    assert result.provenance is Provenance.FALLBACK
    assert result.asset_id == "PROP_FALLBACK_01"
    catalog = load_catalog_from_repo()
    descriptor = catalog.by_id[result.asset_id]
    assert descriptor.category == "utility"
    assert descriptor.interactable is False


# --------------------------------------------------------------------------- #
# Phase 12 — 100-object showcase: resolution coverage + critical distinctness
# --------------------------------------------------------------------------- #


def _dimension_signature(dimensions):
    """The deterministic silhouette signature: sorted axis lengths."""
    return tuple(sorted((dimensions.x, dimensions.y, dimensions.z)))


def _materially_distinct(a, b) -> bool:
    """Distinct templateIds AND materially different dimension signatures
    (length ratio > 1.2 on the longest axes, or a different axis spread)."""
    if a.template_id == b.template_id:
        return False
    sa, sb = _dimension_signature(a.dimensions), _dimension_signature(b.dimensions)
    ratio = max(max(sa), max(sb)) / min(max(sa), max(sb))
    return ratio > 1.2 or sa != sb


def test_phase12_every_catalog_asset_resolves_exact_or_alias():
    """Required Phase 12 test: every catalog asset can resolve through the
    Asset Oracle (CATALOG_EXACT / CATALOG_ALIAS, never fallback/ambiguous)."""
    catalog = load_catalog(MANIFEST_PATH)
    assert len(catalog.assets) >= 100
    resolver = AssetResolver(catalog)
    for descriptor in catalog.assets:
        result = resolver.resolve_request(
            AssetRequest(requested_name=descriptor.asset_id)
        )
        assert result.resolved and not result.ambiguous, descriptor.asset_id
        assert result.asset_id == descriptor.asset_id
        assert result.provenance in (
            Provenance.CATALOG_EXACT,
            Provenance.CATALOG_ALIAS,
        ), descriptor.asset_id


def test_phase12_critical_evidence_pairs_distinct_template_and_silhouette():
    """Required Phase 12 test: the critical evidence pairs must have distinct
    templateIds AND materially different dimension signatures (the knife,
    letter opener, scissors and screwdriver family must never be visually
    interchangeable; key vs usb and phone vs camera too)."""
    catalog = load_catalog(MANIFEST_PATH)
    pairs = (
        ("PROP_KITCHEN_KNIFE_01", "PROP_BREAD_KNIFE_01"),
        ("PROP_LETTER_OPENER_01", "PROP_SCISSORS_01"),
        ("PROP_SCREWDRIVER_01", "PROP_HAMMER_01"),
        ("PROP_KEY_01", "PROP_USB_STICK_01"),
        ("PROP_PHONE_01", "PROP_CAMERA_01"),
    )
    for aid_a, aid_b in pairs:
        a = catalog.by_id[aid_a]
        b = catalog.by_id[aid_b]
        assert _materially_distinct(a, b), (
            f"{aid_a} and {aid_b} are not materiallly distinct: "
            f"templates {a.template_id}/{b.template_id}, dims "
            f"{a.dimensions}/{b.dimensions}"
        )


def test_phase12_golden_published_asset_ids_still_resolve_non_fallback():
    """Required Phase 12 test (resource substitution across asset versions):
    the golden Playthrough render data's assetIds all still resolve TODAY
    through the grown catalog, and NONE of them is the neutral fallback."""
    catalog = load_catalog(MANIFEST_PATH)
    assert len(catalog.assets) >= 100
    resolved = {}
    for asset_id in GOLDEN_ASSET_IDS + ("PROP_KEY_01", "PROP_PHONE_01", "PROP_WALLET_01"):
        result = resolve({"requestedName": asset_id}, catalog=catalog)
        assert result.resolved and not result.ambiguous, asset_id
        assert result.asset_id == asset_id
        assert result.provenance not in (Provenance.FALLBACK,), asset_id
        resolved[asset_id] = result.provenance.value
    assert all(value in ("CATALOG_EXACT", "CATALOG_ALIAS") for value in resolved.values())


def test_phase12_old_published_case_remains_renderable_after_catalog_growth(
    generation_service,
):
    """Required Phase 12 test: republish the golden via the dev provider and
    resolve EVERY placement assetId through the grown Phase 12 catalog — an
    old published CaseVersion stays renderable (every asset resolves to its
    own non-fallback id, provenance exact/alias)."""
    catalog = load_catalog(MANIFEST_PATH)
    session = generation_service.create_anonymous_quota_session()
    started = generation_service.start_case_generation(
        "Victim: sarah_miller\nMurderer: thomas_reed\n",
        anonymous_quota_session_id=session.anonymous_quota_session_id,
    )
    assert started.status == "PUBLISHED"
    row = generation_service._store.get_published(started.case_id, 1)
    payload = json.loads(row.payload_json)
    placements = payload["draft"]["world_graph"]["placements"]
    assert len(placements) >= 9
    for placement in placements:
        # The stored draft's world graph uses snake_case spec keys; the public
        # DTO (published payload) converts to camelCase. Accept both.
        asset_id = placement.get("assetId", placement.get("asset_id"))
        assert asset_id is not None, placement
        assert asset_id in catalog.by_id, asset_id
        result = resolve({"requestedName": asset_id}, catalog=catalog)
        assert result.resolved and not result.ambiguous, asset_id
        assert result.asset_id == asset_id
        assert result.provenance not in (Provenance.FALLBACK,), asset_id