"""Publication service (Phase5 E/G, REQUIREMENTS 7.4/7.5).

Two responsibilities:

1. **Serialization of the frozen published CaseVersion payload** — a
   versioned, deterministic, bounded JSON document stored in
   ``published_versions.payload_json``. The document is the ONLY source from
   which later reads reconstruct the PUBLIC case for an exact published
   version (Phase5 G: never resolve "latest"; never read the draft).

2. **Transactional publication** — one store transaction that:
   - verifies the authoritative generation attempt row (status VALIDATING);
   - serializes the frozen payload (may raise -> rolls the WHOLE unit back,
     the injected-failure test depends on this);
   - inserts the immutable ``published_versions`` row;
   - flips ``case_versions.state`` and ``generation_attempts.status`` to
     PUBLISHED;
   - commits atomically.

   On a duplicate ``(case_id, case_version)`` the transaction rolls back and
   ``DuplicatePublication`` propagates (409 CASE_VERSION_CONFLICT at the API).
   A crash or failure can never leave "PUBLISHED without payload" or a
   "complete payload with contradictory lifecycle state".

The public DTO mapper (``public_case_dict_from_payload``) is the EXPLICIT
ALLOWLIST (Phase5 INVARIANT 5): it reads only the documented fields and builds
a fresh dict with exactly the PublicCaseResponse keys. It never copies
pass-through dicts and never touches the hidden sections (truth / solverProof
/ universes / report / prompt / seed / model / locked).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.generation.publish import PublishedCaseVersion
from app.generation.state_machine import GenerationState
from app.persistence.store import (
    DuplicatePublication,
    Store,
)

PAYLOAD_SCHEMA_VERSION = 1

# Terminal-state sanitized labels for the durable progress snapshot.
_STATE_PROGRESS = {
    "DRAFT": 5,
    "GENERATING": 25,
    "REPAIRING": 60,
    "VALIDATING": 80,
    "PUBLISHED": 100,
    "FAILED": 100,
}


class PublicationError(Exception):
    """Base class for publication-service domain errors."""


class SerializationError(PublicationError):
    """The frozen payload could not be serialized (nothing is persisted)."""


# --------------------------------------------------------------------------- #
# deterministic plain-tree serializer
# --------------------------------------------------------------------------- #


def _to_plain(node: Any) -> Any:
    """Deterministic plain-dict/list/str/int/float/bool/None projection.

    Handles frozen dataclasses (skipping private fields, e.g.
    ``PublicCase._persons`` lookups), FrozenDict/Mapping, tuples/lists,
    sorted sets/frozensets, enums and dates. Anything else fails loud.
    """
    if node is None or isinstance(node, (bool, int, float, str)):
        return node
    if isinstance(node, Enum):
        return node.value
    if isinstance(node, Mapping):
        return {str(key): _to_plain(value) for key, value in node.items()}
    if isinstance(node, (list, tuple)):
        return [_to_plain(value) for value in node]
    if isinstance(node, (set, frozenset)):
        values = [_to_plain(value) for value in node]
        try:
            return sorted(values)
        except TypeError:
            return sorted(values, key=repr)
    if isinstance(node, (datetime, date)):
        return node.isoformat()
    if dataclasses.is_dataclass(node) and not isinstance(node, type):
        result: dict[str, Any] = {}
        for field in dataclasses.fields(node):
            if field.name.startswith("_"):
                continue  # private caches never persist
            result[field.name] = _to_plain(getattr(node, field.name))
        return result
    raise TypeError(
        f"cannot serialize {type(node).__name__} into the frozen payload"
    )


def serialize_published_payload(
    published: PublishedCaseVersion,
    *,
    seed: int | None = None,
    prompt: str | None = None,
    model: str | None = None,
    title: str | None = None,
) -> str:
    """Build the versioned, deterministic, bounded payload JSON document.

    Includes ONLY the frozen aggregate members: the draft (which carries the
    public model + evidence + world graph + crime specification), the hidden
    CaseTruth, the candidate universes, the final solver proof, the locked
    constraints and generation metadata (seed/model/prompt per REQUIREMENTS
    43, passed by the caller from the authoritative attempt record). The
    document is server-internal: no HTTP DTO ever emits it directly.
    """
    if not isinstance(published, PublishedCaseVersion):
        raise SerializationError(
            "payload must be a PublishedCaseVersion; refusing to serialize "
            f"{type(published).__name__}"
        )
    if not isinstance(title, str) or not title:
        title = "Untitled Case"
    report = published.report
    document = {
        "schemaVersion": PAYLOAD_SCHEMA_VERSION,
        "caseId": published.case_id,
        "caseVersion": published.case_version,
        "title": title,
        "publishedAt": published.published_at,
        "generationAttemptId": published.generation_attempt_id,
        "seed": seed,
        "model": model,
        "prompt": prompt,
        "locked": _to_plain(published.locked),
        "draft": _to_plain(published.draft),
        "truth": _to_plain(published.truth),
        "universes": _to_plain(published.universes),
        "solverProof": _to_plain(published.solver_proof),
        "report": {
            "outcome": report.outcome.value if report is not None else None,
            "valid": report.valid if report is not None else False,
            "structuralIssueCount": (
                len(report.structural_issues) if report is not None else 0
            ),
            "safetyIssueCount": len(report.safety_issues) if report is not None else 0,
            "universeIssueCount": len(report.universe_issues) if report is not None else 0,
            "lockedViolationCount": (
                len(report.locked_violations) if report is not None else 0
            ),
        },
    }
    try:
        text = json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise SerializationError(f"payload serialization failed: {exc}") from None
    # Bounded: input bounds (REQUIREMENTS 32.8) bound the draft; add a hard
    # ceiling well above any valid configuration as defense-in-depth.
    if len(text.encode("utf-8")) > 8 * 1024 * 1024:
        raise SerializationError("published payload exceeds the 8 MiB bound")
    return text


def derive_title_from_prompt(prompt: str | None) -> str:
    """Deterministic title: the prompt's first non-empty line (max 120).

    Mirrors ``pipeline._title_for_prompt`` so the durable ``cases.title`` and
    the frozen payload ``title`` always agree.
    """
    if prompt is None:
        return "Untitled Case"
    for raw_line in str(prompt).splitlines():
        line = raw_line.strip()
        if line:
            return line[:120]
    return "Untitled Case"


# --------------------------------------------------------------------------- #
# explicit public-case allowlist mapper (Phase5 INVARIANT 5)
# --------------------------------------------------------------------------- #


def public_case_dict_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Map the stored frozen payload into the PublicCaseResponse DTO dict.

    THE explicit allowlist: only the documented public fields are read and an
    entirely new dict is returned. The stored payload (schemaVersion 1)
    serializes the frozen dataclasses with their field names (snake_case);
    this mapper reads ONLY those documented fields and converts them into the
    camelCase DTO keys. Hidden sections (`truth`, `solverProof`, `universes`,
    `report`, `prompt`, `seed`, `model`, `locked`) are never read here.
    """
    draft = payload.get("draft")
    if not isinstance(draft, Mapping):
        raise ValueError("payload carries no draft document")
    scene_spec = draft.get("scene")

    def _persons() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for person in draft.get("persons") or ():
            out.append(
                {
                    "personId": person.get("person_id"),
                    "name": person.get("name"),
                    "role": person.get("role"),
                    "affordances": sorted(person.get("affordances") or ()),
                }
            )
        return out

    def _motives() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for motive in draft.get("motives") or ():
            out.append(
                {
                    "motiveId": motive.get("motive_id"),
                    "label": motive.get("label"),
                    "affordances": sorted(motive.get("affordances") or ()),
                }
            )
        return out

    def _objects() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for obj in draft.get("objects") or ():
            item: dict[str, Any] = {
                "objectId": obj.get("object_id"),
                "assetId": obj.get("asset_id"),
                "affordances": sorted(obj.get("affordances") or ()),
            }
            if obj.get("subtype") is not None:
                item["subtype"] = obj.get("subtype")
            out.append(item)
        return out

    def _evidence() -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for fact in draft.get("evidence") or ():
            presentation = fact.get("presentation") or {}
            out.append(
                {
                    "id": fact.get("id"),
                    "kind": fact.get("kind"),
                    "reliability": fact.get("reliability"),
                    "title": presentation.get("title"),
                    "description": presentation.get("description"),
                }
            )
        return out

    world_graph = draft.get("world_graph")
    world_graph_dto: dict[str, Any] = {"locations": [], "placements": []}
    if isinstance(world_graph, Mapping):
        world_graph_dto["locations"] = [
            {
                "locationId": loc.get("location_id"),
                "template": loc.get("template"),
                "rooms": list(loc.get("rooms") or ()),
            }
            for loc in world_graph.get("locations") or ()
        ]
        world_graph_dto["placements"] = [
            {
                "objectId": placement.get("object_id"),
                "assetId": placement.get("asset_id"),
                "locationId": placement.get("location_id"),
                "anchor": placement.get("anchor"),
                "interaction": placement.get("interaction"),
                "evidenceId": placement.get("evidence_id"),
            }
            for placement in world_graph.get("placements") or ()
        ]

    return {
        "caseId": payload.get("caseId"),
        "caseVersion": payload.get("caseVersion"),
        "title": payload.get("title"),
        "scene": (
            None
            if not isinstance(scene_spec, Mapping)
            else {"locationId": scene_spec.get("location_id"), "name": scene_spec.get("name")}
        ),
        "persons": _persons(),
        "motives": _motives(),
        "objects": _objects(),
        "locations": [
            {"locationId": loc.get("location_id"), "name": loc.get("name")}
            for loc in draft.get("locations") or ()
        ],
        "travelRules": [
            {
                "fromLocationId": rule.get("from_location_id"),
                "toLocationId": rule.get("to_location_id"),
                "travelTimeSeconds": rule.get("travel_time_seconds"),
            }
            for rule in draft.get("travel_rules") or ()
        ],
        "evidence": _evidence(),
        "worldGraph": world_graph_dto,
    }


# --------------------------------------------------------------------------- #
# atomic publication transaction
# --------------------------------------------------------------------------- #


class PublicationService:
    """Owns the atomic publication unit of work over one Store."""

    def __init__(self, store: Store) -> None:
        if not isinstance(store, Store):
            raise TypeError("PublicationService requires a Store")
        self._store = store

    @property
    def store(self) -> Store:
        return self._store

    def publish_transactionally(
        self,
        published: PublishedCaseVersion,
        *,
        seed: int | None = None,
        prompt: str | None = None,
        model: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Persist ONE frozen published version atomically.

        The durable generation attempt for ``(case_id, case_version)`` MUST
        be the authoritative VALIDATING attempt (Phase5 G): DRAFT /
        GENERATING / REPAIRING / FAILED attempts are refused with a clean
        ``PublicationError`` and NO mutation. An already-published version
        raises ``DuplicatePublication`` (rolls the whole transaction back; no
        mutation). ``SerializationError`` is raised when the frozen payload
        cannot be built (rolls back; no half state).
        """
        case_id = published.case_id
        case_version = published.case_version
        with self._store.transaction() as session:
            attempt = _load_attempt_or_raise(session, case_id, case_version)
            if attempt.status == GenerationState.PUBLISHED.value:
                raise DuplicatePublication(
                    f"published version ({case_id!r}, v{case_version}) already exists"
                )
            if attempt.status != GenerationState.VALIDATING.value:
                raise PublicationError(
                    f"attempt {attempt.attempt_id!r} for ({case_id!r}, "
                    f"v{case_version}) has status {attempt.status!r}; "
                    "publication requires the authoritative VALIDATING status"
                )
            try:
                payload_json = serialize_published_payload(
                    published,
                    seed=seed,
                    prompt=prompt,
                    model=model,
                    title=title,
                )
            except SerializationError:
                raise
            except Exception as exc:  # noqa: BLE001 - never half-publish
                raise SerializationError(
                    f"payload serialization failed: {type(exc).__name__}"
                ) from None
            published_at = float(published.published_at)
            try:
                session.execute(
                    text(
                        "INSERT INTO published_versions "
                        "(case_id, case_version, payload_json, published_at) "
                        "VALUES (:cid, :ver, :payload, :at)"
                    ),
                    {
                        "cid": case_id,
                        "ver": case_version,
                        "payload": payload_json,
                        "at": published_at,
                    },
                )
                session.flush()  # fires the INSERT inside the transaction
            except IntegrityError:
                raise DuplicatePublication(
                    f"published version ({case_id!r}, v{case_version}) already exists"
                ) from None
            session.execute(
                text(
                    "UPDATE case_versions SET state = :state, state_reason = NULL "
                    "WHERE case_id = :cid AND version = :ver"
                ),
                {"state": GenerationState.PUBLISHED.value, "cid": case_id, "ver": case_version},
            )
            session.execute(
                text(
                    "UPDATE generation_attempts SET status = :state, "
                    "stage = :stage, progress = :progress, updated_at = :at "
                    "WHERE case_id = :cid AND case_version = :ver"
                ),
                {
                    "state": GenerationState.PUBLISHED.value,
                    "stage": "published",
                    "progress": _STATE_PROGRESS[GenerationState.PUBLISHED.value],
                    "at": published_at,
                    "cid": case_id,
                    "ver": case_version,
                },
            )
            return {"caseId": case_id, "caseVersion": case_version}


def _load_attempt_or_raise(session: Session, case_id: str, case_version: int) -> Any:
    """Authoritative attempt lookup inside the publication transaction."""
    row = session.execute(
        text(
            "SELECT attempt_id, status FROM generation_attempts "
            "WHERE case_id = :cid AND case_version = :ver"
        ),
        {"cid": case_id, "ver": case_version},
    ).mappings().first()
    if row is None:
        raise PublicationError(
            "no authoritative generation attempt exists for the version being published"
        )
    return row


__all__ = [
    "DuplicatePublication",
    "PAYLOAD_SCHEMA_VERSION",
    "PublicationError",
    "PublicationService",
    "SerializationError",
    "derive_title_from_prompt",
    "public_case_dict_from_payload",
    "serialize_published_payload",
]