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

from pydantic import BaseModel, ConfigDict, Field


class FrontierBlock(BaseModel):
    """Phase 30 — browser BYOK frontier selection block (exactly 3 fields).

    The browser supplies ONLY the logical trusted provider ID + the transient
    API key + the model. The server owns the endpoint mapping (trusted
    registry) — the block MUST NOT carry a URL/endpoint/headers/proxy/timeout/
    TLS field, and ``extra="forbid"`` rejects any unknown key (422 validation
    envelope) per Phase30 §4/§6.

    The three fields are schema-OPTIONAL on purpose (mirroring the Phase 25
    ``ollamaModel`` pattern): the service validators are the SINGLE
    authoritative gate, so a missing/unknown/disabled provider/key/model
    answers the canonical 400 INVALID_FRONTIER_CONFIG envelope (the offending
    value is never echoed) instead of a generic FastAPI 422.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(
        default=None,
        description="Logical trusted provider ID (must be an enabled member of "
        "the server-owned registry; validated centrally, 400 "
        "INVALID_FRONTIER_CONFIG otherwise — the offending value is never "
        "echoed).",
    )
    apiKey: str | None = Field(
        default=None,
        description="The user's transient provider API key (opaque validated "
        "secret; no schema-level bound — the service validator is the single "
        "authoritative gate, 400 INVALID_FRONTIER_CONFIG).",
    )
    model: str | None = Field(
        default=None,
        description="The user-supplied provider model identifier (validated by "
        "the hardened central model-string validator, 400 "
        "INVALID_FRONTIER_CONFIG).",
    )


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
    # Phase 25 — optional browser-supplied provider selection. Omitted/null =>
    # the server-configured DEFAULT provider (backward compatibility). The
    # browser may NEVER supply URLs/credentials/endpoints — only these safe
    # logical ids; the service validates every value centrally (unknown values
    # are rejected with the canonical envelope, never silently ignored/echoed).
    generationProvider: str | None = Field(
        default=None,
        max_length=64,
        description="Phase 25 optional logical generation provider "
        "(fake | ollama | frontier | null). The service validates the fixed "
        "server-side allowlist and rejects unknown values (400 "
        "INVALID_GENERATION_PROVIDER).",
    )
    ollamaTransport: str | None = Field(
        default=None,
        max_length=64,
        description="Phase 25 optional Ollama transport (server | bridge | "
        "null). Required when generationProvider=ollama (400 otherwise).",
    )
    ollamaModel: str | None = Field(
        default=None,
        description="Phase 25 optional Ollama model identifier (e.g. "
        "qwen2.5:1.5b). Required when generationProvider=ollama; validated by "
        "the central Phase 25 model-string validator (400 INVALID_OLLAMA_MODEL "
        "on malformed/URL-like/overlong values). Ignored for fake/frontier. "
        "No schema-level length cap: the central validator is the single "
        "authoritative gate (max 256 chars, canonical 400 envelope).",
    )
    # Phase 28 — optional demo-fixture selection. Omitted/null/blank => the
    # current single-fixture demo behavior (backward compatible with the
    # Demo-#1-only journey). The service validates the id against the FIXED
    # server-side registry (demo-apartment | demo-gallery |
    # demo-laboratory); an unknown id is rejected with the canonical 400
    # INVALID_DEMO_CASE envelope (never echoed) and a demoCaseId with a
    # non-fake provider is rejected the same way (fail-closed: a browser value
    # can never select fixtures on a real LLM path). The field is
    # top-level (beside generationProvider/ollamaTransport/ollamaModel)
    # because it is a sibling of the Phase 25 per-attempt selection, not a
    # property of the prompt or the case-storage model.
    demoCaseId: str | None = Field(
        default=None,
        max_length=64,
        description="Phase 28 optional built-in demo case id (demo-apartment "
        "| demo-gallery | demo-laboratory). Only meaningful on the fake/demo "
        "provider path; unknown values are rejected (400 INVALID_DEMO_CASE).",
    )
    # Phase 30 — optional browser BYOK frontier block (provider + apiKey +
    # model). Required when generationProvider=frontier; REJECTED on any other
    # provider (strict request-schema convention, 400 INVALID_FRONTIER_CONFIG);
    # the nested block accepts exactly those three keys (extra="forbid"). The
    # provider ID maps to a trusted server-owned endpoint — the browser never
    # supplies a URL.
    frontier: FrontierBlock | None = Field(
        default=None,
        description="Phase 30 optional BYOK frontier selection (provider + "
        "apiKey + model). Only meaningful when generationProvider=frontier; "
        "the provider ID is resolved against the trusted server-owned "
        "registry and the key is used only for the current attempt.",
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
    failureCode: str | None = None


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

    Phase 20 (PD-SEC-01): the shared schema is served by TWO scopes with
    different evidence visibility:

    - ``GET /cases/{caseId}`` (creator credential) is the CASE-scoped
      dossier: REQUIREMENTS 41.2 mandates the full public-case evidence list
      there, so the ``evidence`` array + placement ``evidenceId`` are kept
      (the case owner generated the case — that data is already legitimately
      known to their role);
    - ``GET /playthroughs/{playthrough_id}/public-case`` (playthrough
      credential) is the PLAYTHROUGH-scoped DTO: it carries ONLY what the
      player has actually discovered — an empty ``evidence`` array and null
      placement ``evidenceId`` before discovery (see
      ``publication.public_case_dict_from_payload(..., discovered=...)``).
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
