"""Phase 11 — environment RESOLVER contract + request-field security.

- resolution order: exact environmentId -> EXACT; canonical-name (normalized)
  -> EXACT; alias -> ALIAS (verbatim matched alias); semantic word-token
  tags -> SEMANTIC_TYPE (unique top score; tie -> AMBIGUOUS with candidates,
  NO arbitrary winner); unknown -> FALLBACK (the documented `apartment`
  default);
- the generation request field `environment` is bounded and safety-scanned:
  unsafe strings (URLs, paths, control chars, traversal, > 40 chars) are
  rejected with 422 ENVIRONMENT_ERROR; safe unknown strings resolve to the
  fallback; the golden dev-mode alias table resolves as documented.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.environments.manifests import EnvironmentKit, Vec3, ZoneSpec, load_all_environments
from app.environments.resolver import (
    EnvironmentProvenance,
    FALLBACK_ENVIRONMENT_ID,
    resolve_environment,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ENVIRONMENTS_DIR = REPO_ROOT / "assets" / "environments"

from phase5_helpers import create_session  # noqa: E402


@pytest.fixture(scope="module")
def kits():
    return load_all_environments(directory=ENVIRONMENTS_DIR)


def test_exact_and_canonical_resolution(kits):
    exact = resolve_environment("apartment")
    assert exact.environment_id == "apartment"
    assert exact.provenance is EnvironmentProvenance.EXACT
    assert exact.resolved is True
    canonical = resolve_environment("Hotel Suite")
    # canonical names normalize to the same token as the ids' aliases... the
    # canonical NAME of hotel_suite is "Hotel Suite" -> EXACT provenance.
    assert canonical.environment_id == "hotel_suite"
    assert canonical.provenance is EnvironmentProvenance.EXACT


def test_alias_resolution_reports_verbatim_alias(kits):
    resolution = resolve_environment("flat")
    assert resolution.environment_id == "apartment"
    assert resolution.provenance is EnvironmentProvenance.ALIAS
    assert resolution.matched_alias == "flat"
    resolution = resolve_environment("company office")
    assert resolution.environment_id == "office"
    assert resolution.provenance is EnvironmentProvenance.ALIAS
    assert resolution.matched_alias == "company office"


def test_semantic_resolution(kits):
    resolution = resolve_environment("room 312")
    assert resolution.environment_id == "hotel_suite"
    assert resolution.provenance is EnvironmentProvenance.SEMANTIC_TYPE
    resolution = resolve_environment("storage depot")
    assert resolution.environment_id == "warehouse"
    assert resolution.provenance is EnvironmentProvenance.SEMANTIC_TYPE


def test_unknown_resolves_to_documented_fallback(kits):
    resolution = resolve_environment("total nonsense words")
    assert resolution.environment_id == FALLBACK_ENVIRONMENT_ID == "apartment"
    assert resolution.provenance is EnvironmentProvenance.FALLBACK
    assert resolution.resolved is True


def test_ambiguous_semantic_match_reports_candidates_no_winner():
    """A request matching two kits' vocabularies EQUALLY is AMBIGUOUS with
    the deterministic tied candidate ids (never an arbitrary winner)."""

    def _kit(id_, words):
        zones = tuple(
            ZoneSpec(zone_id=f"{id_}_z{i}", label="Zone", rooms=("room",))
            for i in range(5)
        )
        from app.environments.manifests import (
            AnchorSpec,
            EnvironmentKit,
            LightingSpec,
            SpawnSpec,
        )
        anchors = []
        positions = [(1.0, 0.0, float(1 + i)) for i in range(12)]
        for i, (x, y, z) in enumerate(positions):
            # The 12 anchors cover PLAYER_SPAWN + DESK_EVIDENCE +
            # GENERIC_PROP + two secondary types (TABLE_PROP + STORAGE) so the
            # coverage invariants hold for the synthetic kit.
            anchor_type = (
                "PLAYER_SPAWN"
                if i == 0
                else "DESK_EVIDENCE"
                if i == 1
                else "TABLE_PROP"
                if i == 4
                else "STORAGE"
                if i == 5
                else "GENERIC_PROP"
            )
            anchors.append(
                AnchorSpec(
                    anchor_id=f"{id_}_a{i:02d}",
                    type=anchor_type,
                    zone_id=f"{id_}_z{i % 5}",
                    position=Vec3(x, y, z),
                    rotation=Vec3(0.0, 0.0, 0.0),
                    allowed_categories=("decor", "furniture", "utility", "evidence"),
                    exclusive=(anchor_type == "PLAYER_SPAWN"),
                    required=anchor_type != "GENERIC_PROP",
                )
            )
        coverage = {}
        for t in (
            "PLAYER_SPAWN",
            "BODY",
            "FLOOR_EVIDENCE",
            "DESK_EVIDENCE",
            "GENERIC_PROP",
            "DOOR",
            "WINDOW",
            "COMPUTER",
            "DOCUMENT",
            "TABLE_PROP",
            "STORAGE",
            "CCTV",
            "ACCESS_CONTROL",
            "WALL_EVIDENCE",
        ):
            matching = [a.anchor_id for a in anchors if a.type == t]
            coverage[t] = matching[:1]
        # satisfy coverage invariants: we need at least one BODY etc. — reuse a
        # simpler approach: every required type gets the first GENERIC anchor.
        for t in ("BODY", "FLOOR_EVIDENCE", "DESK_EVIDENCE", "GENERIC_PROP", "DOOR", "WINDOW"):
            if not coverage[t]:
                coverage[t] = [anchors[0].anchor_id]
        kit = EnvironmentKit(
            environment_id=id_,
            version=1,
            canonical_name=id_,
            aliases=(),
            tags=words,
            zones=zones,
            anchors=tuple(anchors),
            spawn=SpawnSpec(
                anchor_id=anchors[0].anchor_id,
                position=Vec3(0.2, 0.0, 0.2),
                rotation=Vec3(0.0, 0.0, 0.0),
            ),
            lighting=LightingSpec(
                profile="neutral", key_intensity=1.0,
                hemi_intensity=0.5, accent_color="#c0c0c0",
            ),
            structural_assets=("DOOR_APARTMENT_01", "PROP_WINDOW_01", "PROP_WALL_01",
                               "PROP_DESK_01", "PROP_LAMP_01", "PROP_TABLE_01"),
            style_hint=None,
            default_anchor_coverage=coverage,
        )
        return kit

    alpha = _kit("alpha", ("marsh", "bank"))
    beta = _kit("beta", ("marsh",))
    # "marsh island" matches alpha (marsh + bank? no — island not in vocab).
    resolution = resolve_environment("marsh island", kits=(alpha, beta))
    assert resolution.ambiguous is True
    assert resolution.resolved is False
    assert resolution.candidates == ("alpha", "beta")
    assert resolution.environment_id == ""


def test_resolution_is_order_independent(kits):
    reversed_kits = tuple(reversed(kits))
    for name in ("flat", "room 312", "villa", "storage", "marsh"):
        first = resolve_environment(name, kits=kits)
        second = resolve_environment(name, kits=reversed_kits)
        assert first.environment_id == second.environment_id
        assert first.provenance is second.provenance


def test_environment_hint_safety_validator(phase5_migrated_client):
    """Unsafe environment values are rejected 422 ENVIRONMENT_ERROR; safe
    unknown values fall back; no offending value is echoed back."""
    from app.services.generation import environment_hint_safety

    assert environment_hint_safety(None) == ()
    assert environment_hint_safety("hotel suite") == ()
    assert environment_hint_safety("x" * 41)
    assert environment_hint_safety("https://evil.example")
    assert environment_hint_safety("../etc/passwd")
    assert environment_hint_safety("C:\\windows\\system32")
    assert environment_hint_safety("script tag")
    assert environment_hint_safety("java\tscript")  # control char
    assert environment_hint_safety("data:text/html")

    client = phase5_migrated_client
    session_token, _ = create_session(client)
    for unsafe in ("https://evil.example", "../x", "a" * 41, "script tag"):
        res = client.post(
            "/api/v1/cases",
            json={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n",
                  "environment": unsafe},
            headers={"Authorization": f"Bearer {session_token}"},
        )
        # 422 either way: the length bound is enforced by the request schema
        # (VALIDATION_ERROR), the content-safety bounds by the service
        # (ENVIRONMENT_ERROR); the offending value is never echoed back.
        assert res.status_code == 422, (unsafe, res.text)
        body = res.json()
        assert body["error"]["code"] in (
            "ENVIRONMENT_ERROR",
            "VALIDATION_ERROR",
        ), (unsafe, body)
        assert unsafe not in res.text  # never echoed back


def test_environment_field_is_unknown_safe(phase5_migrated_client):
    """A safe-but-unknown environment value does NOT 422: it resolves to the
    documented fallback (apartment) with provenance FALLBACK."""
    client = phase5_migrated_client
    session_token, _ = create_session(client)
    res = client.post(
        "/api/v1/cases",
        json={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n",
              "environment": "tumble house at the seaside"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert res.status_code == 201, res.text
    service = client.app.state.generation_service
    resolution = service._last_environment_resolution
    assert resolution is not None
    assert resolution["environmentId"] == "apartment"
    assert resolution["provenance"] == "FALLBACK"
    assert resolution["compositionFailed"] is False