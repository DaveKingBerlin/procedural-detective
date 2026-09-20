"""Phase 7 accusation + reveal DTOs (REQUIREMENTS 40.10-40.12, Phase7 B/E).

EXPLICIT ALLOWLISTS ONLY (REQUIREMENTS 41.4/41.5):

- ``AccusationRequest`` is the FROZEN typed body:
  ``{"murdererId", "motiveId", "weaponId", "crimeTime"}`` with
  ``extra="forbid"`` — unknown/extra fields are rejected (422). The three id
  fields are bounded ``str`` (1..256, Pydantic-level) and ``crimeTime`` is a
  bounded ``str`` (the exact grammar — full ISO-8601-with-offset OR a bare
  ``HH:MM[:SS]`` time-of-day — is validated by the accusation service per
  DEC-003 because the canonical date/offset anchoring is truth-aware and
  must stay OUT of the schema layer).
- ``AccusationResponseDTO`` echoes ONLY the accepted submission
  (REQUIREMENTS 40.10/41.4: the response MUST NOT reveal truth or per-
  dimension correctness).
- ``RevealResponseDTO`` is the frozen reveal allowlist: canonical truth
  labels, the player's own accusation, per-dimension correctness booleans,
  score, a player-safe evidence-based timeline and the player-safe
  explanation. It NEVER carries SolverProof / SolutionProof internals,
  acceptedScoring, prompts, diagnostics, tokens, verifiers,
  generationAttemptId, private admission/quota state or internal database id
  material (Phase7 E/F).

Neither module imports ``app.domain`` / ``app.validation`` /
``app.generation`` (boundary contract in test_boundaries.py) — the response
dicts are built by ``app.services.reveal`` from the pinned published payload.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Bounded id/text lengths of the frozen accusation contract (Phase7 B).
ACCUSATION_ID_MIN = 1
ACCUSATION_ID_MAX = 256
CRIME_TIME_MAX = 64


class AccusationRequest(BaseModel):
    """POST /api/v1/playthroughs/{playthrough_id}/accusation body (frozen)."""

    model_config = ConfigDict(extra="forbid")

    murdererId: str = Field(
        ...,
        min_length=ACCUSATION_ID_MIN,
        max_length=ACCUSATION_ID_MAX,
        description="A suspect from the pinned CaseVersion SUSPECT_ELIGIBLE universe.",
    )
    motiveId: str = Field(
        ...,
        min_length=ACCUSATION_ID_MIN,
        max_length=ACCUSATION_ID_MAX,
        description="A motive from the pinned CaseVersion MOTIVE_CANDIDATE universe.",
    )
    weaponId: str = Field(
        ...,
        min_length=ACCUSATION_ID_MIN,
        max_length=ACCUSATION_ID_MAX,
        description="A weapon from the pinned CaseVersion POTENTIAL_WEAPON universe.",
    )
    crimeTime: str = Field(
        ...,
        min_length=4,
        max_length=CRIME_TIME_MAX,
        description=(
            "Full ISO-8601-with-offset timestamp OR a bare 24h time-of-day "
            "\"HH:MM[:SS]\" (anchored to the canonical crime date + timezone "
            "offset at evaluation time — DEC-003)."
        ),
    )


class AccusationEchoDTO(BaseModel):
    """The submitted — never corrected — accusation value object."""

    murdererId: str
    motiveId: str
    weaponId: str
    crimeTime: str


class AccusationResponseDTO(BaseModel):
    """POST .../accusation -> 200 (REQUIREMENTS 40.10, Phase7 C).

    The response carries NO truth and NO per-dimension correctness: the
    separation contract keeps accusation and reveal apart.
    """

    playthroughId: str
    caseId: str
    caseVersion: int
    status: Literal["ACCUSED"] = "ACCUSED"
    accusation: AccusationEchoDTO


# --------------------------------------------------------------------------- #
# Reveal allowlist (REQUIREMENTS 40.12 / 3.5, Phase7 E/F)
# --------------------------------------------------------------------------- #


class RevealTruthDTO(BaseModel):
    """Canonical truth labels at reveal time (Phase7 E; player-safety: only
    ids + PUBLIC labels — the DESIGNATION comes from the truth section, the
    names/labels come from the published public persons/motives/objects)."""

    murdererId: str
    murdererName: str
    motiveId: str
    motiveLabel: str
    weaponId: str
    weaponName: str
    crimeTime: str


class RevealPlayerAccusationDTO(BaseModel):
    """The player's immutable submitted accusation (echoed verbatim)."""

    accusation: AccusationEchoDTO


class RevealResultDTO(BaseModel):
    """Per-dimension correctness + overall verdict (Phase7 D/E)."""

    murdererCorrect: bool
    motiveCorrect: bool
    weaponCorrect: bool
    timeCorrect: bool
    overall: Literal["solved", "incorrect"]


class RevealScoreDTO(BaseModel):
    """Simple deterministic scoring (REQUIREMENTS 3.5 score)."""

    correctDimensions: int
    totalDimensions: Literal[4] = 4


class TimelineEntryDTO(BaseModel):
    """One player-safe timeline point derived from PUBLIC discoverable
    evidence (Phase7 E; NEVER canonical truth internals)."""

    time: str
    description: str


class EvidencePointDTO(BaseModel):
    """One player-safe explainer reference (Phase7 F).

    ``point`` is a SHORT phrase mapped deterministically from the evidence
    kind/role by the frozen rule-evidence mapping in
    ``app.services.reveal`` — never raw rule/proof fields.
    """

    evidenceId: str
    title: str
    point: str


class ProofDimensionMapDTO(BaseModel):
    """Phase18C per-dimension proof board: WHO / WHY / WEAPON / WHEN -> the
    supporting discovered evidence (each entry an EvidencePointDTO with a
    public evidence id, its public title and a frozen MAP phrase)."""

    who: list[EvidencePointDTO] = Field(default_factory=list)
    why: list[EvidencePointDTO] = Field(default_factory=list)
    weapon: list[EvidencePointDTO] = Field(default_factory=list)
    when: list[EvidencePointDTO] = Field(default_factory=list)


class RevealExplanationDTO(BaseModel):
    """The player-safe explanation of why the solution is correct (Phase7 F +
    Phase18C proof board).

    ``evidence`` is the FLAT list of explainer references (frozen Phase7 F
    shape, unchanged). ``dimensions`` is the Phase18C per-dimension proof map:
    for every proof-board dimension (WHO/WHY/WEAPON/WHEN) the supporting
    DISCOVERED evidence points. Both are built ONLY from public evidence ids +
    public titles + frozen MAP phrases — never from raw proof/rule internals.
    The union of the four dimension id lists equals the flat ``evidence`` id
    set whenever the flat list is non-empty (empty lists only when the flat
    list is also empty), so the two views are cache-consistent by contract.
    """

    evidence: list[EvidencePointDTO] = Field(default_factory=list)
    dimensions: ProofDimensionMapDTO = Field(default_factory=ProofDimensionMapDTO)


class RevealResponseDTO(BaseModel):
    """GET .../reveal -> 200 (REQUIREMENTS 40.12, Phase7 E — frozen DTO)."""

    playthroughId: str
    caseId: str
    caseVersion: int
    status: Literal["REVEALED"] = "REVEALED"
    truth: RevealTruthDTO
    player: RevealPlayerAccusationDTO
    result: RevealResultDTO
    score: RevealScoreDTO
    timeline: list[TimelineEntryDTO] = Field(default_factory=list)
    explanation: RevealExplanationDTO