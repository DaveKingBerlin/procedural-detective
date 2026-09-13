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

from pydantic import BaseModel, Field


class PlayerKnowledgeDTO(BaseModel):
    """Snapshot of the player-observable state (REQUIREMENTS 36)."""

    discoveredEvidenceIds: list[str] = Field(default_factory=list)
    readEvidenceIds: list[str] = Field(default_factory=list)
    visitedLocationIds: list[str] = Field(default_factory=list)


class WorldObjectDTO(BaseModel):
    """One player-safe world object (Phase6 D; WorldGraph rendering contract).

    ``assetType`` is the public object's ``subtype`` when present, otherwise a
    safe generic type label. ``anchor`` is a semantic anchor identifier (never
    raw coordinates). ``interaction`` comes from the published interaction
    allowlist.
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


class SceneLocationDTO(BaseModel):
    locationId: str
    name: str


class InvestigationSceneDTO(BaseModel):
    """The investigation scene: the starting location + rendered objects."""

    location: SceneLocationDTO
    worldObjects: list[WorldObjectDTO] = Field(default_factory=list)


class InvestigationBootstrapResponse(BaseModel):
    """GET /api/v1/playthroughs/{playthrough_id}/investigation -> 200."""

    playthroughId: str
    caseId: str
    caseVersion: int
    state: str
    playerKnowledge: PlayerKnowledgeDTO
    scene: InvestigationSceneDTO


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