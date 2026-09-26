"""Phase 23 — witness interview service (deterministic, server-authoritative).

Adds the structured WITNESS interview mechanic on top of the existing
PLAYING-gated investigation surface:

- ``get_witness``       — player-safe public witness view (identity + closed
                          presence + the six always-offered questions), PLAYING
                          only (Phase23 §14 keeps the read PLAYING for
                          consistency);
- ``interview_witness`` — one structured question -> a deterministic,
                          ZERO-provider witness statement. When the question is
                          GROUNDED on linked witness-kind evidence that is
                          discoverable, the interview DISCOVERS that evidence
                          through the existing PlayerKnowledge machinery
                          (``mark_discovered`` + ``mark_read``), idempotent and
                          server-authoritative — exactly like object discovery.
                          A NEUTRAL answer performs ZERO state mutation.

Boundaries:

- pinned payload resolution is EXCLUSIVELY from the playthrough's exact
  ``(case_id, case_version)`` published_versions row (never "latest");
- the interview response reuses the deterministic witness projection
  (``app.domain.witness`` — pure, bounded, allowlisted); NO provider/LLM call
  exists anywhere in this path (asserted by the zero-provider-call test);
- witness-kind discovery reuses the store discovery machinery, so
  interview-discovered evidence survives reload and lands in the same
  discoveredEvidenceIds/readEvidenceIds the notebook reads;
- IDOR/enumeration: an unknown witness id, a non-witness public person, a
  wrong playthrough, or a playthrough pinned to a different case all answer
  the same generic NOT_FOUND envelope; the response never reveals internal
  person ids or hidden state.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from app.core.observability import emit_event
from app.domain import witness as witness_domain
from app.persistence.store import Store
from app.persistence.timebase import EpochClock
from app.services import publication as pub

logger = logging.getLogger("app.services.witnesses")


class WitnessError(Exception):
    """Base class for witness-service domain errors."""


class WitnessNotFoundError(WitnessError):
    """Unknown/fabricated witness id, non-witness person, missing pinned
    version, or a playthrough whose pinned case has no such witness.

    Every such case is answered 404 NOT_FOUND with the generic envelope (the
    response never reveals whether a particular id exists or what role it has).
    """


class WitnessStateError(WitnessError):
    """The playthrough is not PLAYING (-> 409 NOT_PLAYING envelope)."""


def _iso_utc(epoch: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(
        float(epoch), tz=datetime.timezone.utc
    ).isoformat()


class WitnessService:
    """Owns the witness view + interview over one Store."""

    def __init__(self, store: Store, clock: Any = None) -> None:
        if not isinstance(store, Store):
            raise TypeError("WitnessService requires a Store")
        self._store = store
        self._clock = clock if clock is not None else EpochClock()

    @property
    def store(self) -> Store:
        return self._store

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _pinned_payload(self, playthrough: Any) -> dict[str, Any]:
        row = self._store.get_published(
            playthrough.case_id, playthrough.case_version
        )
        if row is None:
            raise WitnessNotFoundError("pinned published version unavailable")
        try:
            payload = json.loads(row.payload_json)
        except (ValueError, TypeError):
            raise WitnessNotFoundError("pinned published payload unreadable") from None
        if not isinstance(payload, Mapping):
            raise WitnessNotFoundError("pinned published payload malformed") from None
        return dict(payload)

    def _require_playing(self, playthrough: Any) -> None:
        """PLAYING-only gate (Phase23 §14: the interview mutates PlayerKnowledge
        when it discovers; the read view is kept PLAYING for consistency)."""
        if playthrough.state != "PLAYING":
            raise WitnessStateError("playthrough is not currently playable")

    def _now(self) -> float:
        return float(self._clock.now())

    def _witness_or_404(
        self, payload: Mapping[str, Any], witness_id: str
    ) -> Mapping[str, Any]:
        witness = witness_domain.witness_person_of(payload, witness_id)
        if witness is None:
            # Same generic 404 for unknown ids AND non-witness public persons
            # (capture per IDOR contract).
            raise WitnessNotFoundError("unknown witness id")
        return witness

    # ------------------------------------------------------------------ #
    # witness view
    # ------------------------------------------------------------------ #

    def get_witness(self, playthrough: Any, witness_id: str) -> dict[str, Any]:
        """200 WitnessViewResponse: the player-safe witness identity view.

        The six questions are ALWAYS offered (bounded availability model —
        never a clue-semantics leak; every question answers grounded or
        neutral at interview time).
        """
        payload = self._pinned_payload(playthrough)
        self._require_playing(playthrough)
        witness = self._witness_or_404(payload, witness_id)
        presence, at_scene = witness_domain.witness_presence(payload, witness_id)
        name = witness.get("name") or str(witness.get("person_id") or witness_id)
        return {
            "witnessId": str(witness.get("person_id")),
            "displayName": name[: witness_domain.MAX_NAME_CHARS],
            "presence": presence.value,
            "atScene": at_scene,
            "questions": [
                {
                    "questionType": q.value,
                    "label": witness_domain.QUESTION_LABELS[q],
                }
                for q in witness_domain.ALL_QUESTIONS
            ],
        }

    # ------------------------------------------------------------------ #
    # interview
    # ------------------------------------------------------------------ #

    def interview_witness(
        self, playthrough: Any, witness_id: str, question_type: str
    ) -> dict[str, Any]:
        """POST .../witnesses/{witness_id}/interview.

        - questionType must be one of the CLOSED enum values (the API schema
          already 422s anything else; the service re-checks defensively);
        - the answer is a deterministic projection with ZERO provider calls;
        - a GROUNDED answer whose linked witness-kind evidence is discoverable
          discovers it (idempotent; repeat interviews no-op and return the
          same statement + the same record with newlyDiscovered=False);
        - a NEUTRAL answer performs ZERO discovery/state mutation.
        """
        payload = self._pinned_payload(playthrough)
        self._require_playing(playthrough)
        emit_event("witness.interview.started", questionType=str(question_type))
        try:
            question = witness_domain.WitnessQuestionType(str(question_type))
        except ValueError:
            raise WitnessNotFoundError("unknown question type") from None
        witness = self._witness_or_404(payload, witness_id)

        projection = witness_domain.project_witness_statement(payload, witness, question)
        statement = projection.statement
        discovery: dict[str, Any] | None = None
        if statement.grounded:
            discovery = self._discover_grounding(
                playthrough,
                payload,
                witness_id,
                statement,
                question_type=question.value,
            )
        new_discovery_count = 1 if (discovery and discovery.get("newlyDiscovered")) else 0
        emit_event(
            "witness.interview.completed",
            questionType=question.value,
            witnessId=str(witness.get("person_id")),
            newDiscoveryCount=new_discovery_count,
        )

        return {
            "witnessId": str(witness.get("person_id")),
            "displayName": str(witness.get("name") or witness.get("person_id"))[
                : witness_domain.MAX_NAME_CHARS
            ],
            "questionType": question.value,
            "statement": {
                "summary": statement.summary,
                "observations": [
                    {"time": obs.time, "text": obs.text} for obs in statement.observations
                ],
            },
            "discovery": discovery,
        }

    def _discover_grounding(
        self,
        playthrough: Any,
        payload: Mapping[str, Any],
        witness_id: str,
        statement: Any,
        question_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Discover the linked discoverable witness-kind evidence (idempotent).

        Only evidence that:
          - exists in the pinned payload,
          - is ``discoverable`` (published flag),
          - was attributed to this witness AND grounded this question
        is ever touched. Repeat interviews add nothing (set semantics) and the
        primary record is returned so the panel can render it.
        """
        evidence_ids = {
            eid
            for eid in statement.evidence_ids
            if eid in pub.evidence_ids_of(payload)
        }
        if not evidence_ids:
            return None
        now = self._now()
        self._store.get_or_create_player_knowledge(
            playthrough.playthrough_id,
            playthrough.case_id,
            playthrough.case_version,
            at=now,
        )
        before = self._store.snapshot_player_knowledge(playthrough.playthrough_id)
        newly = False
        primary: str | None = None
        for eid in sorted(evidence_ids):
            fact = pub.evidence_fact_of(payload, eid)
            if fact is None or fact.get("discoverable") is False:
                continue
            primary = primary or eid
            if eid not in before.discovered:
                newly = True
            self._store.mark_discovered(
                playthrough.playthrough_id, eid, None, at=now
            )
            self._store.mark_read(
                playthrough.playthrough_id, eid, at=now, opened_at=now
            )
        if primary is None:
            return None
        return {
            "newlyDiscovered": newly,
            "record": self._read_dto(
                payload,
                playthrough,
                primary,
                question_type=question_type,
                witness_id=witness_id,
            ),
        }

    def _read_dto(
        self,
        payload: Mapping[str, Any],
        playthrough: Any,
        evidence_id: str,
        *,
        question_type: str | None = None,
        witness_id: str | None = None,
    ) -> dict[str, Any]:
        """The read-record DTO of one discovered witness-kind evidence
        (byte-identical shape to the existing record-read endpoint).

        Phase 23 — the DTO's ``content`` additionally carries the interview-
        source tags: ``witnessId`` (the interviewed witness) and
        ``questionType`` (the type that was ACTUALLY asked when the interview
        path built this record). For a replay of the SAME discovery through
        the ordinary record-read endpoint, the deterministic payload-derived
        tags (``publication.witness_statement_content_tags``) flow instead —
        the reload notebook re-derives the same group either way.
        """
        fact = pub.evidence_fact_of(payload, evidence_id)
        if fact is None:
            raise WitnessNotFoundError("unknown record id")
        snapshot = self._store.snapshot_player_knowledge(playthrough.playthrough_id)
        opened_epoch = snapshot.opened_at.get(evidence_id)
        if opened_epoch is None:
            opened_epoch = snapshot.updated_at
        presentation = pub.presentation_of(fact)
        content = pub.project_read_content(payload, evidence_id)
        tags = pub.witness_statement_content_tags(payload, evidence_id)
        if tags or question_type is not None or witness_id is not None:
            tags = dict(tags or {})
            if witness_id is not None:
                tags["witnessId"] = str(witness_id)
            if question_type is not None:
                tags["questionType"] = str(question_type)
            content = {**content, **tags}
        return {
            "evidenceId": evidence_id,
            "kind": str(fact.get("kind")),
            "title": presentation.get("title"),
            "description": presentation.get("description"),
            "openedAt": _iso_utc(opened_epoch),
            "readByPlayer": True,
            "content": content,
        }


__all__ = [
    "WitnessError",
    "WitnessNotFoundError",
    "WitnessService",
    "WitnessStateError",
]
