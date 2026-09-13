"""Investigation service (Phase6 B/C/I, REQUIREMENTS 40.7-40.9).

The service owns the PLAYER-SAFE investigation mechanics:

- ``get_investigation_bootstrap`` — pinned bootstrap + PlayerKnowledge +
  projected world objects;
- ``discover_evidence``      — server-authoritative evidence discovery
  (membership in the pinned CaseVersion + reachability via a world-graph
  placement; idempotent; marks the placement's location visited);
- ``interact_with_object``   — validated world-graph interaction (placement
  membership + exact interaction match; 409 on mismatch, NO state change);
- ``read_record``            — read of a DISCOVERED record (403 when not
  discovered; idempotent; never opens undiscovered content).

All read paths reconstruct the pinned published payload EXCLUSIVELY from the
immutable ``published_versions`` row for the playthrough's exact
``(case_id, case_version)`` (``store.get_published``) — never from "latest"
and never from process memory (Phase6 B / 40.7, REQUIREMENTS 7.1/40.5).

Player safety: every mutation originates from validated placements /
interactions — the client can never claim arbitrary evidence as known
(Phase6 I). Evidence content/propositions/truth/proof never leave the
projection allowlists (REQUIREMENTS 41.4).
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Mapping

from app.persistence.store import PlayerKnowledgeError, Store
from app.persistence.timebase import EpochClock
from app.services import publication as pub


class InvestigationError(Exception):
    """Base class for investigation service domain errors."""


class InvestigationNotFoundError(InvestigationError):
    """Unknown/fabricated evidence or object id, missing pinned version, or
    evidence not reachable through any valid placement.

    Every such case is answered 404 NOT_FOUND with a generic envelope: the
    response never reveals whether a particular id exists (Phase6 B / 40.7).
    """


class InvestigationStateError(InvestigationError):
    """The playthrough state does not permit investigation actions (40.7)."""


class EvidenceNotDiscoveredError(InvestigationError):
    """Reading an undiscovered record (-> 403 EVIDENCE_NOT_DISCOVERED)."""


class InteractionNotAllowedError(InvestigationError):
    """The requested interaction does not match the placement's published
    interaction (-> 409 INTERACTION_NOT_ALLOWED; NO state change)."""


def _iso_utc(epoch: float) -> str:
    """Deterministic ISO-8601 UTC representation of an epoch float."""
    return datetime.datetime.fromtimestamp(
        float(epoch), tz=datetime.timezone.utc
    ).isoformat()


class InvestigationService:
    """Owns player-safe investigation reads/mutations over one Store."""

    def __init__(self, store: Store, clock: Any = None) -> None:
        if not isinstance(store, Store):
            raise TypeError("InvestigationService requires a Store")
        self._store = store
        self._clock = clock if clock is not None else EpochClock()

    @property
    def store(self) -> Store:
        return self._store

    # ------------------------------------------------------------------ #
    # pinned payload reconstruction (never "latest", never memory)
    # ------------------------------------------------------------------ #

    def _pinned_payload(self, playthrough: Any) -> dict[str, Any]:
        row = self._store.get_published(
            playthrough.case_id, playthrough.case_version
        )
        if row is None:
            # The pinned version is missing/unpublished -> clean 404.
            raise InvestigationNotFoundError("pinned published version unavailable")
        try:
            payload = json.loads(row.payload_json)
        except (ValueError, TypeError):
            raise InvestigationNotFoundError("pinned published payload unreadable") from None
        if not isinstance(payload, Mapping):
            raise InvestigationNotFoundError("pinned published payload malformed") from None
        return dict(payload)

    def _require_playing(self, playthrough: Any) -> None:
        """Action-validity guard (REQUIREMENTS 40.7): only a PLAYING
        playthrough may run investigation actions."""
        if playthrough.state != "PLAYING":
            raise InvestigationStateError("playthrough is not currently playable")

    def _now(self) -> float:
        return float(self._clock.now())

    # ------------------------------------------------------------------ #
    # bootstrap
    # ------------------------------------------------------------------ #

    def get_investigation_bootstrap(self, playthrough: Any) -> dict[str, Any]:
        """200 InvestigationBootstrapResponse for one validated playthrough.

        Contains ONLY player-observable material: the pin + state, the
        PlayerKnowledge snapshot, the scene (starting location + projected
        world objects). No coordinates, no evidence content, no hidden fields.
        """
        payload = self._pinned_payload(playthrough)
        self._require_playing(playthrough)
        now = self._now()
        self._store.get_or_create_player_knowledge(
            playthrough.playthrough_id,
            playthrough.case_id,
            playthrough.case_version,
            at=now,
        )
        snapshot = self._store.snapshot_player_knowledge(playthrough.playthrough_id)
        scene = pub.scene_spec_of(payload)
        return {
            "playthroughId": playthrough.playthrough_id,
            "caseId": playthrough.case_id,
            "caseVersion": playthrough.case_version,
            "state": playthrough.state,
            "playerKnowledge": pub.player_knowledge_snapshot(snapshot),
            "scene": {
                "location": {
                    "locationId": scene.get("location_id"),
                    "name": scene.get("name"),
                },
                "worldObjects": pub.project_world_objects(
                    payload,
                    discovered=set(snapshot.discovered),
                    read=set(snapshot.read),
                ),
            },
        }

    # ------------------------------------------------------------------ #
    # discovery
    # ------------------------------------------------------------------ #

    def discover_evidence(self, playthrough: Any, evidence_id: str) -> dict[str, Any]:
        """Server-authoritative discovery of one evidence record (40.8).

        - evidence must belong to the pinned CaseVersion (404 when unknown);
        - the evidence must be REACHABLE via a valid world-graph placement
          (404 when not — the response never reveals whether the id exists);
        - idempotent: repeating returns state ``already-discovered``;
        - the linked placement's location is marked visited server-side.
        """
        payload = self._pinned_payload(playthrough)
        self._require_playing(playthrough)
        seen_id = str(evidence_id)
        if seen_id not in pub.evidence_ids_of(payload):
            # Unknown/fabricated -> generic 404 (no leak).
            raise InvestigationNotFoundError("unknown evidence id")
        discovery = pub.project_discovery(payload, seen_id)
        if discovery is None:
            # Known id but no valid interaction/placement links it -> generic
            # 404 (do NOT reveal whether the id exists).
            raise InvestigationNotFoundError("evidence is not reachable")
        now = self._now()
        self._store.get_or_create_player_knowledge(
            playthrough.playthrough_id,
            playthrough.case_id,
            playthrough.case_version,
            at=now,
        )
        snapshot = self._store.snapshot_player_knowledge(playthrough.playthrough_id)
        state = "already-discovered" if seen_id in snapshot.discovered else "discovered"
        locations = sorted(
            {
                str(placement.get("location_id"))
                for placement in pub.placements_for_evidence(payload, seen_id)
            }
        )
        if state == "discovered":
            first_location = locations[0] if locations else None
            self._store.mark_discovered(
                playthrough.playthrough_id,
                seen_id,
                first_location,
                at=now,
            )
            for location_id in locations[1:]:
                self._store.mark_visited(playthrough.playthrough_id, location_id, at=now)
        return {
            "evidenceId": seen_id,
            "kind": discovery["kind"],
            "title": discovery["title"],
            "interaction": discovery["interaction"],
            "state": state,
        }

    # ------------------------------------------------------------------ #
    # interaction
    # ------------------------------------------------------------------ #

    def interact_with_object(
        self,
        playthrough: Any,
        object_id: str,
        interaction: str,
    ) -> dict[str, Any]:
        """One validated world interaction (REQUIREMENTS 27, Phase6 I).

        - the object must exist in the pinned version's placements (404);
        - the interaction must equal the placement's allowed interaction
          (409 INTERACTION_NOT_ALLOWED, NO state change);
        - a placement that maps to evidence runs the discovery logic;
        - successful interactions mark the placement's location visited.
        """
        payload = self._pinned_payload(playthrough)
        self._require_playing(playthrough)
        seen_object_id = str(object_id)
        placement = pub.placement_for_object(payload, seen_object_id)
        if placement is None:
            raise InvestigationNotFoundError("unknown object id")
        if str(interaction) != str(placement.get("interaction")):
            raise InteractionNotAllowedError(
                "interaction does not match the placement"
            )
        now = self._now()
        self._store.get_or_create_player_knowledge(
            playthrough.playthrough_id,
            playthrough.case_id,
            playthrough.case_version,
            at=now,
        )
        evidence_id = placement.get("evidence_id")
        evidence_id_out = (
            str(evidence_id) if evidence_id is not None else None
        )
        if evidence_id_out is None:
            self._store.mark_visited(
                playthrough.playthrough_id,
                str(placement.get("location_id")),
                at=now,
            )
            return {
                "objectId": seen_object_id,
                "interaction": str(interaction),
                "evidenceId": None,
                "discovery": None,
                "result": "interacted",
            }
        # Evidence-linked placement: discovery logic runs (reachable by
        # construction — the placement itself is the reachability proof).
        discovery = self.discover_evidence(playthrough, evidence_id_out)
        return {
            "objectId": seen_object_id,
            "interaction": str(interaction),
            "evidenceId": evidence_id_out,
            "discovery": discovery,
            "result": "interacted",
        }

    # ------------------------------------------------------------------ #
    # record read
    # ------------------------------------------------------------------ #

    def read_record(self, playthrough: Any, record_id: str) -> dict[str, Any]:
        """Read/inspect one DISCOVERED record (REQUIREMENTS 40.9).

        - unknown/missing record in the pinned version -> 404;
        - known but NOT yet discovered          -> 403 EVIDENCE_NOT_DISCOVERED
          (generic message, no content);
        - idempotent: repeat reads return the byte-identical DTO (the first
          ``openedAt`` is persisted);
        - ``content`` is the kind-allowlisted public mapping.
        """
        payload = self._pinned_payload(playthrough)
        self._require_playing(playthrough)
        seen_id = str(record_id)
        fact = pub.evidence_fact_of(payload, seen_id)
        if fact is None:
            raise InvestigationNotFoundError("unknown record id")
        now = self._now()
        self._store.get_or_create_player_knowledge(
            playthrough.playthrough_id,
            playthrough.case_id,
            playthrough.case_version,
            at=now,
        )
        snapshot = self._store.snapshot_player_knowledge(playthrough.playthrough_id)
        if seen_id not in snapshot.discovered:
            raise EvidenceNotDiscoveredError("record is not discovered")
        self._store.mark_read(playthrough.playthrough_id, seen_id, at=now, opened_at=now)
        snapshot = self._store.snapshot_player_knowledge(playthrough.playthrough_id)
        presentation = pub.presentation_of(fact)
        opened_epoch = snapshot.opened_at.get(seen_id)
        if opened_epoch is None:
            opened_epoch = snapshot.updated_at
        return {
            "evidenceId": seen_id,
            "kind": str(fact.get("kind")),
            "title": presentation.get("title"),
            "description": presentation.get("description"),
            "openedAt": _iso_utc(opened_epoch),
            "readByPlayer": True,
            "content": pub.project_read_content(payload, seen_id),
        }


__all__ = [
    "EvidenceNotDiscoveredError",
    "InteractionNotAllowedError",
    "InvestigationError",
    "InvestigationNotFoundError",
    "InvestigationService",
    "InvestigationStateError",
]