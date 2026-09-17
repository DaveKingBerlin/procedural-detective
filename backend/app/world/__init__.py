"""Phase 14 — Prompt-to-World Asset Integration (backend world package).

- ``requirements`` — the typed ``WorldRequirements`` contract (bounded object
  requests, placement relations, bounds validation);
- ``extract`` — the deterministic prompt -> ``WorldRequirements`` extractor
  (environment families / known-object table / unsafe terms / relations);
- ``composer`` — the deterministic ``compose_world`` (Environment Resolver +
  Asset Oracle + placer -> ``WorldComposition`` with provenance, relation
  satisfaction and the sanitized WORld validation bucket).
"""

from __future__ import annotations

from app.world.extract import (
    KNOWN_OBJECT_TABLE,
    TRIGGER_GAP_MAX,
    UNSAFE_OBJECT_TERMS,
    extract_world_requirements,
    is_base_object_request,
)
from app.world.requirements import (
    MAX_CAPABILITIES,
    MAX_OBJECT_REQUESTS,
    MAX_RELATIONS,
    MAX_REQUESTED_NAME_LENGTH,
    MAX_STRING_LENGTH,
    MAX_TAGS,
    ObjectRequest,
    PlacementRelation,
    RELATION_KINDS,
    RELATION_TO_ANCHOR_TYPES,
    WorldRequirements,
    object_request_issues,
    placement_relation_issues,
    safe_string_issues,
    world_requirements_issues,
)
from app.world.composer import (
    KIT_BASE_OBJECT_IDS,
    KnownObjectSpecProvider,
    NEAR_VICTIM_PROXIMITY,
    RELATION_PREFERENCE_TYPES,
    ResolvedObject,
    WorldComposition,
    compose_world,
)

__all__ = [
    "KIT_BASE_OBJECT_IDS",
    "KNOWN_OBJECT_TABLE",
    "MAX_CAPABILITIES",
    "MAX_OBJECT_REQUESTS",
    "MAX_RELATIONS",
    "MAX_REQUESTED_NAME_LENGTH",
    "MAX_STRING_LENGTH",
    "MAX_TAGS",
    "NEAR_VICTIM_PROXIMITY",
    "ObjectRequest",
    "PlacementRelation",
    "RELATION_KINDS",
    "RELATION_PREFERENCE_TYPES",
    "RELATION_TO_ANCHOR_TYPES",
    "ResolvedObject",
    "TRIGGER_GAP_MAX",
    "UNSAFE_OBJECT_TERMS",
    "WorldComposition",
    "WorldRequirements",
    "compose_world",
    "extract_world_requirements",
    "is_base_object_request",
    "KnownObjectSpecProvider",
    "object_request_issues",
    "placement_relation_issues",
    "safe_string_issues",
    "world_requirements_issues",
]