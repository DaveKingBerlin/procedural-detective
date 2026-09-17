"""Phase 11 — Five Environment Kits & Semantic Anchors (backend half).

Public surface:
- ``manifests``   — typed kit descriptors + strict manifest validation
  (``assets/environments/*.json`` is the single source of truth);
- ``resolver``    — deterministic environment-hint resolution with provenance
  (EXACT / ALIAS / SEMANTIC_TYPE / FALLBACK) and explicit ambiguity;
- ``placer``      — deterministic placement validation + ``place_objects``
  (first-fit, exclusive-once, category/anchor compatibility, evidence
  accessibility/spacing, spawn + BODY navigability rules);
- ``compose``     — the dev-provider composition that re-anchors the golden
  object set onto a resolved kit's world graph + scene.
"""

from __future__ import annotations

from app.environments.manifests import (
    ANCHOR_TYPES,
    AnchorSpec,
    EnvironmentKit,
    EnvironmentNotFoundError,
    EnvironmentValidationError,
    LightingSpec,
    SpawnSpec,
    Vec3,
    ZoneSpec,
    load_all_environments,
    load_environment,
    validate_environment_data,
)
from app.environments.placer import (
    BODY_SPAWN_CLEARANCE,
    EVIDENCE_CAPABLE_TYPES,
    MIN_EVIDENCE_SPACING,
    PlacedObject,
    PlacementError,
    PlacementRequest,
    SPAWN_ANCHOR_CLEARANCE,
    place_objects,
    validate_placement,
)
from app.environments.resolver import (
    EnvironmentProvenance,
    EnvironmentResolution,
    FALLBACK_ENVIRONMENT_ID,
    normalize,
    resolve_environment,
)

__all__ = [
    "ANCHOR_TYPES",
    "AnchorSpec",
    "BODY_SPAWN_CLEARANCE",
    "EVIDENCE_CAPABLE_TYPES",
    "EnvironmentKit",
    "EnvironmentNotFoundError",
    "EnvironmentProvenance",
    "EnvironmentResolution",
    "EnvironmentValidationError",
    "FALLBACK_ENVIRONMENT_ID",
    "LightingSpec",
    "MIN_EVIDENCE_SPACING",
    "PlacedObject",
    "PlacementError",
    "PlacementRequest",
    "SPAWN_ANCHOR_CLEARANCE",
    "SpawnSpec",
    "Vec3",
    "ZoneSpec",
    "load_all_environments",
    "load_environment",
    "normalize",
    "place_objects",
    "resolve_environment",
    "validate_environment_data",
    "validate_placement",
]