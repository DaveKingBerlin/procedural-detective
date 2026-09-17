"""Phase 6 investigation DTOs (REQUIREMENTS 40.7-40.9, Phase6 B; frozen
contract consumed by the parallel frontend track).

Every DTO is an EXPLICIT allowlist: only the documented player-safe fields are
declared here, and the API builds these objects ONLY from the pinned published
payload + PlayerKnowledge through ``app.services.investigation`` /
``app.services.publication`` projections.

- WorldObjectDTO carries NO coordinates (the frontend anchor registry derives
  geometry from anchor + assetId), NO evidence content, NO propositions and NO
  hidden fields.
- DiscoveryResultDTO / InteractionResultDTO / EvidenceReadResultDTO expose
  ONLY player-authorized material (kind/title/description per the evidence
  presentation allowlist; never raw propositions, never solver inputs, never
  CaseTruth/proof).
- ``openedAt`` is an ISO-8601 (UTC) string of the FIRST time the record was
  read — stable across repeat reads (idempotent read contract).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_serializer


class PlayerKnowledgeDTO(BaseModel):
    """Snapshot of the player-observable state (REQUIREMENTS 36)."""

    discoveredEvidenceIds: list[str] = Field(default_factory=list)
    readEvidenceIds: list[str] = Field(default_factory=list)
    visitedLocationIds: list[str] = Field(default_factory=list)


class GeneratedVec3DTO(BaseModel):
    """One bounded {x, y, z} render vector of a generated definition."""

    x: float
    y: float
    z: float


class GeneratedTransformDTO(BaseModel):
    """One bounded part transform (position / rotation / scale, local space)."""

    position: GeneratedVec3DTO
    rotation: GeneratedVec3DTO
    scale: GeneratedVec3DTO


class GeneratedPartDTO(BaseModel):
    """One resolved part of a generated definition (renderer-facing)."""

    id: str
    role: str
    primitive: str
    transform: GeneratedTransformDTO
    color: str
    parentId: str | None = None


class GeneratedAssetDefinitionDTO(BaseModel):
    """The FROZEN Phase 13 GeneratedAssetDefinition document (player-safe).

    Mirror of ``app.assets.compiler.GeneratedAssetDefinition.to_definition_json()``:
    declarative geometry ONLY (bounded primitives box/cylinder/sphere/plane,
    bounded transforms, resolved #RRGGBB colors, derived pickable hitbox) — NO
    executable content of any kind. The frontend track renders exactly this
    schema.
    """

    compilerVersion: int
    schemaVersion: int
    assetId: str
    canonicalName: str
    category: str
    subtype: str | None = None
    dimensions: GeneratedVec3DTO
    parts: list[GeneratedPartDTO] = Field(default_factory=list)
    hitbox: dict[str, GeneratedVec3DTO] = Field(default_factory=dict)


class WorldObjectDTO(BaseModel):
    """One player-safe world object (Phase6 D; WorldGraph rendering contract).

    ``assetType`` is the public object's ``subtype`` when present, otherwise a
    safe generic type label. ``anchor`` is a semantic anchor identifier (never
    raw coordinates). ``interaction`` comes from the published interaction
    allowlist.

    Phase 13 additive: ``generated`` carries the validated declarative
    ``GeneratedAssetDefinition`` (render metadata) of a procedural (proc.*)
    world object — present ONLY when the pinned payload's placement carried a
    definition that passed the current compiler/schema projection gate (any
    mismatch skips the placement). Player-safe by construction: bounded
    declarative geometry + resolved colors, NO executable content.
    """

    objectId: str
    assetId: str
    assetType: str
    subtype: str | None = None
    locationId: str
    anchor: str
    interaction: str
    evidenceId: str | None = None
    discovered: bool = False
    read: bool = False
    generated: GeneratedAssetDefinitionDTO | None = None

    @model_serializer
    def _serialize(self) -> dict[str, Any]:
        """Serialize the signal fields, OMITTING ``generated`` when absent.

        Phase 13 contract: ``generated`` is an OPTIONAL additive field — the
        golden (non-procedural) bootstrap response stays byte-identical
        (no ``generated`` key), while a procedural placement still carries its
        validated definition. Every other (nullable) field keeps its value as
        before (``subtype``/``evidenceId`` remain present-null).
        """
        result: dict[str, Any] = {}
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if field_name == "generated" and value is None:
                continue
            result[field_name] = value
        return result


class SceneLocationDTO(BaseModel):
    locationId: str
    name: str


class InvestigationSceneDTO(BaseModel):
    """The investigation scene: the starting location + rendered objects.

    ``environmentId`` (Phase 11 additive): the environment kit identity of the
    pinned published version. ``environmentVersion`` (Phase 14 additive): the
    pinned kit version. Player-safe metadata with NO truth; the frontend uses
    them to pick the kit builder for the scene.
    """

    location: SceneLocationDTO
    environmentId: str | None = None
    environmentVersion: int | None = None
    worldObjects: list[WorldObjectDTO] = Field(default_factory=list)


class CandidateSuspectDTO(BaseModel):
    """One SUSPECT_ELIGIBLE candidate (Phase7 J/K; player-safe: id + PUBLIC
    name from the pinned published payload — NO canonical designation)."""

    id: str
    name: str


class CandidateMotiveDTO(BaseModel):
    """One MOTIVE_CANDIDATE candidate (id + public label)."""

    id: str
    label: str


class CandidateWeaponDTO(BaseModel):
    """One POTENTIAL_WEAPON candidate (id + public assetId + display label)."""

    id: str
    assetId: str
    name: str


class AccusationCandidatesDTO(BaseModel):
    """Player-safe accusation candidate universes of the pinned CaseVersion
    (Phase7 J/K, REQUIREMENTS 31.1 / 41.2 addition).

    Built ONLY from the PUBLISHED universe snapshot + public persons/motives/
    objects; every list is sorted alphabetically by id. The winning candidate
    may appear but is NEVER marked or ranked (no positional marking — the
    frontend must treat all candidates equally).
    """

    suspects: list[CandidateSuspectDTO] = Field(default_factory=list)
    motives: list[CandidateMotiveDTO] = Field(default_factory=list)
    weapons: list[CandidateWeaponDTO] = Field(default_factory=list)


class InvestigationBootstrapResponse(BaseModel):
    """GET /api/v1/playthroughs/{playthrough_id}/investigation -> 200."""

    playthroughId: str
    caseId: str
    caseVersion: int
    state: str
    playerKnowledge: PlayerKnowledgeDTO
    scene: InvestigationSceneDTO
    candidates: AccusationCandidatesDTO = Field(
        default_factory=AccusationCandidatesDTO,
        description=(
            "Phase 7 addition: player-safe accusation candidate universes of "
            "the pinned CaseVersion (alphabetical, never winner-marked)."
        ),
    )


class DiscoveryResultDTO(BaseModel):
    """POST .../evidence/{evidence_id}/discover -> 200 (REQUIREMENTS 40.8)."""

    evidenceId: str
    kind: str
    title: str
    interaction: str
    state: Literal["discovered", "already-discovered"]


class ObjectInteractionRequest(BaseModel):
    """POST .../objects/{object_id}/interact request body."""

    interaction: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description=(
            "The interaction the player invoked; it must match the placement's "
            "published interaction exactly (mismatch -> 409)."
        ),
    )


class InteractionResultDTO(BaseModel):
    """POST .../objects/{object_id}/interact -> 200."""

    objectId: str
    interaction: str
    evidenceId: str | None = None
    discovery: DiscoveryResultDTO | None = None
    result: Literal["interacted"]


class EvidenceReadResultDTO(BaseModel):
    """GET /api/v1/playthroughs/{playthrough_id}/records/{record_id} -> 200
    (REQUIREMENTS 40.9).

    ``content`` is the kind-specific allowlisted public mapping (empty object
    for kinds without an allowlist). ``openedAt`` is ISO-8601 UTC (documented
    above).
    """

    evidenceId: str
    kind: str
    title: str
    description: str | None = None
    openedAt: str
    readByPlayer: Literal[True] = True
    content: dict[str, Any] = Field(default_factory=dict)