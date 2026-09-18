"""Case DTOs — creation request, started response, public case response.

The public case response (REQUIREMENTS 41.2/48B, Phase5 F.4) is an EXPLICIT
allowlist: every field of every nested model is declared here, and the API
builds these objects ONLY from the frozen published payload through
``app.services.publication.public_case_dict_from_payload``. No ORM/domain/
internal object is ever serialized directly (Phase5 INVARIANT 5).

The DTO contains NO CaseTruth: no murdererId/victimId/weaponId/crimeTime/
canonical time/proof/truthfulness anywhere — including deeply nested material.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CaseCreateRequest(BaseModel):
    """POST /api/v1/cases request body (REQUIREMENTS 40.3)."""

    prompt: str = Field(
        ...,
        description="Generation prompt (structured keys or free text; the "
        "service enforces MAX_PROMPT_CHARS).",
    )
    difficulty: str | None = Field(
        default=None,
        max_length=64,
        description="Optional difficulty label (stored; the pipeline is "
        "deterministic and ignores it).",
    )
    environment: str | None = Field(
        default=None,
        max_length=40,
        description="Phase 11 optional environment hint (apartment / office / "
        "hotel_suite / warehouse / mansion or any canonical/alias/semantic "
        "form; unknown values fall back to 'apartment'; the service enforces "
        "the same input-safety bounds as the prompt).",
    )


class CaseStartedDTO(BaseModel):
    """POST /api/v1/cases -> 201 (REQUIREMENTS 40.3 / Phase5 F.2).

    ``creatorAccessToken`` appears ONLY here, at creation time. It is a case
    access credential, never the generation quota identity (REQUIREMENTS
    32.9/40.3).
    """

    caseId: str
    generationId: str
    generationAttemptId: str
    creatorAccessToken: str
    status: str


class SceneDTO(BaseModel):
    locationId: str
    name: str
    # Phase 11 additive: the environment kit identity of the published scene
    # (player-safe metadata; the frontend uses it to pick the kit builder).
    environmentId: str | None = None
    # Phase 14 additive: the pinned environment kit version of the published
    # scene (player-safe metadata; deterministic kit-version pinning).
    environmentVersion: int | None = None


class PersonDTO(BaseModel):
    personId: str
    name: str
    role: str
    affordances: list[str] = Field(default_factory=list)


class MotiveDTO(BaseModel):
    motiveId: str
    label: str
    affordances: list[str] = Field(default_factory=list)


class ObjectDTO(BaseModel):
    objectId: str
    assetId: str
    affordances: list[str] = Field(default_factory=list)
    subtype: str | None = None


class LocationDTO(BaseModel):
    locationId: str
    name: str


class TravelRuleDTO(BaseModel):
    fromLocationId: str
    toLocationId: str
    travelTimeSeconds: int


class EvidenceDTO(BaseModel):
    id: str
    kind: str
    reliability: Literal["high", "medium", "low"]
    title: str | None = None
    description: str | None = None


class WorldGraphLocationDTO(BaseModel):
    locationId: str
    template: str
    rooms: list[str] = Field(default_factory=list)


class WorldGraphPlacementDTO(BaseModel):
    objectId: str
    assetId: str
    locationId: str
    anchor: str
    interaction: str
    evidenceId: str | None = None


class WorldGraphDTO(BaseModel):
    locations: list[WorldGraphLocationDTO] = Field(default_factory=list)
    placements: list[WorldGraphPlacementDTO] = Field(default_factory=list)


class PublicCaseResponse(BaseModel):
    """GET /api/v1/cases/{caseId} and GET .../public-case (REQUIREMENTS 41.2).

    Public world material ONLY. Never the hidden truth.
    """

    caseId: str
    caseVersion: int
    title: str
    scene: SceneDTO | None = None
    persons: list[PersonDTO] = Field(default_factory=list)
    motives: list[MotiveDTO] = Field(default_factory=list)
    objects: list[ObjectDTO] = Field(default_factory=list)
    locations: list[LocationDTO] = Field(default_factory=list)
    travelRules: list[TravelRuleDTO] = Field(default_factory=list)
    evidence: list[EvidenceDTO] = Field(default_factory=list)
    worldGraph: WorldGraphDTO = Field(default_factory=WorldGraphDTO)
    # ADV-153 — player-safe bounded composition notes (sanitized "left out"
    # warnings for decorative unseen objects; browser surface). Bounded: at
    # most MAX_DRAFT_COMPOSITION_NOTES, each <= MAX_DRAFT_COMPOSITION_NOTE_CHARS.
    compositionNotes: list[str] = Field(default_factory=list)