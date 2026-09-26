"""Phase 23 — witness interview DTOs (player-safe wire contract).

Every DTO is an EXPLICIT allowlist built ONLY from pins + the published
payload's public sections through ``app.services.witnesses`` projections:

- ``WitnessViewResponse`` — the public witness view (id / display name /
  closed presence enum / at-scene flag + the ALWAYS-offered six questions
  with their allowlisted display labels). Question availability never leaks
  clue semantics (all six are always offered; each returns a grounded or a
  neutral statement).
- ``WitnessInterviewRequest`` — the CLOSED question-type body: only the six
  enum values are accepted (anything else -> 422).
- ``WitnessStatementDTO`` — bounded allowlisted statement text +
  observations ({time ISO optional, text}), all control-char-stripped and
  length-capped.
- ``WitnessDiscoveryDTO`` — ``{newlyDiscovered, record}``: the discovery
  block returned when a grounded interview legitimately discovers the linked
  witness-kind evidence. ``record`` is the SAME read-record DTO shape the
  existing record-read endpoint returns, so the frontend notebook can display
  the discovered statement through the existing evidence panel.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.investigation import EvidenceReadResultDTO

# The CLOSED wire-level question values (mirrors the frozen domain enum in
# app/domain/witness.py). Schemas MUST NOT import domain (boundary contract),
# so the allowlist is re-declared here as the frozen literal.
QUESTION_TYPE_VALUES = Literal[
    "OBSERVATION", "TIME", "PERSON", "OBJECT", "LOCATION", "SOUND"
]

WITNESS_PRESENCE_VALUES = Literal["ON_SCENE", "REMOTE_STATEMENT"]


class WitnessQuestionDTO(BaseModel):
    """One available question (closed type + allowlisted display label)."""

    questionType: QUESTION_TYPE_VALUES
    label: str


class WitnessViewResponse(BaseModel):
    """GET /api/v1/playthroughs/{playthrough_id}/witnesses/{witness_id}
    -> 200. The player-safe witness view: identity + presence + the six
    available questions (always all six; grounded/neutral at interview time)."""

    witnessId: str
    displayName: str
    presence: WITNESS_PRESENCE_VALUES
    atScene: bool
    questions: list[WitnessQuestionDTO] = Field(default_factory=list)


class WitnessInterviewRequest(BaseModel):
    """POST .../witnesses/{witness_id}/interview request body.

    ``questionType`` is a CLOSED enum: any other value -> 422. There is no
    free-form question text in v1 (Phase23 §4).
    """

    questionType: QUESTION_TYPE_VALUES


class WitnessObservationDTO(BaseModel):
    """One observation of a statement: ``time`` is the concrete anchor the
    published evidence carried — a full ISO-8601-with-offset timestamp OR a
    bare ``HH:MM[:SS]`` clock token reproduced verbatim from the allowlisted
    witness text (Phase23 §21: concrete times must be visible); ``text`` is
    the bounded allowlisted statement/description text."""

    time: str | None = None
    text: str


class WitnessStatementDTO(BaseModel):
    """The bounded, player-safe statement payload (Phase23 §5/§13)."""

    summary: str
    observations: list[WitnessObservationDTO] = Field(default_factory=list)


class WitnessDiscoveryDTO(BaseModel):
    """The discovery block of a grounded interview.

    ``newlyDiscovered`` — True only for the call that actually added the
    evidence to PlayerKnowledge (repeat interviews are no-ops). ``record`` is
    the read-record DTO of the primary discovered witness-kind evidence (the
    same shape GET .../records/{record_id} returns), so the notebook can
    render the discovered statement through the existing evidence panel.
    """

    newlyDiscovered: bool
    record: EvidenceReadResultDTO


class WitnessInterviewResponse(BaseModel):
    """POST .../witnesses/{witness_id}/interview -> 200.

    - ``questionType`` echoes the asked question; the STATEMENT is either
      grounded (allowlisted evidence text / concrete times) or the neutral
      ``No. Nothing stood out to me.`` (empty observations).
    - ``discovery`` is null for neutral statements and for grounded statements
      whose grounding evidence is NOT discoverable; otherwise it carries
      ``{newlyDiscovered, record}`` (a repeat interview returns the same
      statement with ``newlyDiscovered: false``).
    """

    witnessId: str
    displayName: str
    questionType: QUESTION_TYPE_VALUES
    statement: WitnessStatementDTO
    discovery: WitnessDiscoveryDTO | None = None
