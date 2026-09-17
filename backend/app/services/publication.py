"""Publication service (Phase5 E/G, Phase6 C; REQUIREMENTS 7.4/7.5, 41).

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

Phase 6 adds the PLAYER-SAFE investigation projections (``project_world_objects``
/ ``project_discovery`` / ``project_read_content`` / ``player_knowledge_snapshot``):
each is an explicit allowlist over the SAME frozen payload; no hidden section
is ever read (REQUIREMENTS 41.4 — gameplay APIs return only PublicCase +
PlayerKnowledge).
"""

from __future__ import annotations

import dataclasses
import json
import logging
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

logger = logging.getLogger("app.services.publication")

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
            else {
                "locationId": scene_spec.get("location_id"),
                "name": scene_spec.get("name"),
                # Phase 11 additive kit identity (player-safe metadata).
                "environmentId": scene_spec.get("environment_id"),
            }
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


# --------------------------------------------------------------------------- #
# Phase 6 — player-safe investigation projections (explicit ALLOWLISTS only)
# --------------------------------------------------------------------------- #
#
# Every helper reads ONLY the documented public sections of the frozen payload
# (``draft.objects`` / ``draft.evidence`` / ``draft.world_graph`` /
# ``draft.scene``) and builds a FRESH dict. The hidden sections (``truth``,
# ``solverProof``, ``universes``, ``report``, ``seed``, ``model``, ``prompt``,
# ``locked``, ``reliability`` flags, ``source_ref`` / ``propositions`` of the
# evidence) are NEVER read here. No blacklist logic — the DTO keys are the
# explicit allowlist.


def _draft_of(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The payload's serialized draft section (raises on malformed data)."""
    draft = payload.get("draft")
    if not isinstance(draft, Mapping):
        raise ValueError("payload carries no draft document")
    return draft


def _objects_by_id(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    draft = _draft_of(payload)
    return {str(obj.get("object_id")): obj for obj in draft.get("objects") or ()}


def _evidence_by_id(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    draft = _draft_of(payload)
    return {str(fact.get("id")): fact for fact in draft.get("evidence") or ()}


def _placements_of(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    draft = _draft_of(payload)
    world_graph = draft.get("world_graph")
    if not isinstance(world_graph, Mapping):
        return ()
    placements = world_graph.get("placements") or ()
    return tuple(placements)


def _asset_type_for(obj: dict[str, Any] | None) -> str:
    """assetType = the public object's subtype or a safe generic type label."""
    if obj is None:
        return SAFE_ASSET_TYPE_LABEL
    subtype = obj.get("subtype")
    if isinstance(subtype, str) and subtype:
        return subtype
    return SAFE_ASSET_TYPE_LABEL


def _sort_placements_for_evidence(
    placements: tuple[dict[str, Any], ...], evidence_id: str
) -> tuple[dict[str, Any], ...]:
    """Placements linking ``evidence_id``, deterministically ordered by objectId."""
    return tuple(
        sorted(
            (
                placement
                for placement in placements
                if str(placement.get("evidence_id")) == evidence_id
            ),
            key=lambda p: str(p.get("object_id")),
        )
    )


def evidence_ids_of(payload: Mapping[str, Any]) -> set[str]:
    """The evidence-id set of the pinned payload (membership checks only)."""
    return set(_evidence_by_id(payload).keys())


def evidence_fact_of(payload: Mapping[str, Any], evidence_id: str) -> dict[str, Any] | None:
    """The serialized evidence fact document of ``evidence_id`` (or None)."""
    return _evidence_by_id(payload).get(evidence_id)


def scene_spec_of(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The pinned payload's scene spec ({location_id, name}), possibly empty."""
    scene = _draft_of(payload).get("scene")
    return scene if isinstance(scene, Mapping) else {}


def presentation_of(fact: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """The evidence's public presentation map (never None)."""
    return _presentation_of(fact)


def placements_for_evidence(
    payload: Mapping[str, Any], evidence_id: str
) -> tuple[dict[str, Any], ...]:
    """Every placement linking ``evidence_id`` (deterministic by objectId)."""
    return _sort_placements_for_evidence(_placements_of(payload), evidence_id)


def placement_for_object(
    payload: Mapping[str, Any], object_id: str
) -> dict[str, Any] | None:
    """THE placement of ``object_id`` (the FIRST in published placement order).

    Returns None when the object has no placement in the pinned version.

    DEF-050: for a legacy/crafted payload with duplicate placements of one
    objectId, this resolves to the SAME placement that ``project_world_objects``
    keeps (first in published order), so the interaction shown in the bootstrap
    DTO is exactly the interaction the interact endpoint accepts.
    """
    for placement in _placements_of(payload):
        if str(placement.get("object_id")) == object_id:
            return placement
    return None


def _presentation_of(fact: dict[str, Any] | None) -> Mapping[str, Any]:
    if fact is None:
        return {}
    presentation = fact.get("presentation")
    return presentation if isinstance(presentation, Mapping) else {}


def project_world_objects(
    payload: Mapping[str, Any],
    *,
    discovered: set[str] | frozenset[str] | None = None,
    read: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Project the pinned payload's world-graph placements into WorldObjectDTOs.

    Player-safe by construction:

    - NO coordinates, NO evidence content, NO propositions, NO hidden truth;
    - every placement is re-validated: the object must exist in the published
      payload, the assetId must be registered (AssetRegistry), and a
      referenced evidenceId must exist in the published evidence set —
      otherwise the placement is SKIPPED (never leak, never crash);
    - DEF-050: duplicate placements for one objectId (legacy/crafted payloads)
      fold into ONE WorldObjectDTO — the FIRST placement in published order
      wins, exactly matching ``placement_for_object``;
    - ``evidenceId`` is exposed ONLY as a nullable id (never its content);
    - ``discovered`` / ``read`` flags come from the caller's PlayerKnowledge.

    The output is deterministic: sorted by ``objectId`` with unique objectIds.
    """
    discovered_ids = discovered if discovered is not None else frozenset()
    read_ids = read if read is not None else frozenset()

    from app.generation.safety import AssetRegistry, is_procedural_asset_id

    objects = _objects_by_id(payload)
    evidence = _evidence_by_id(payload)
    out: list[dict[str, Any]] = []
    emitted_object_ids: set[str] = set()
    for placement in _placements_of(payload):
        object_id = str(placement.get("object_id"))
        if object_id in emitted_object_ids:
            # DEF-050: a legacy/crafted payload may contain duplicate
            # placements for one objectId — the bootstrap MUST emit exactly
            # one WorldObjectDTO per objectId (keep the FIRST placement in
            # published order, matching ``placement_for_object``).
            continue
        if object_id not in objects:
            continue  # unknown object -> skip the placement
        asset_id = placement.get("asset_id")
        if not isinstance(asset_id, str):
            continue
        generated: dict[str, Any] | None = None
        if is_procedural_asset_id(asset_id):
            # Phase 13: a placement for a procedural asset is projected ONLY
            # when its payload block carries a validated generatedDefinition
            # (per the CURRENT compiler/schema versions). Any mismatch (missing
            # definition, schema-version confusion, tampered geometry) skips
            # the placement with a sanitized log — never a leak, never a crash.
            from app.assets.compiler import validate_embedded_definition

            definition = validate_embedded_definition(asset_id, placement.get("generated_definition"))
            if definition is None:
                logger.warning(
                    "skipping procedural placement %r: embedded generatedDefinition "
                    "failed current-version validation",
                    object_id,
                )
                continue
            generated = definition
        elif not AssetRegistry.is_allowed(asset_id):
            continue  # unregistered asset -> skip the placement
        evidence_id = placement.get("evidence_id")
        if evidence_id is not None and str(evidence_id) not in evidence:
            continue  # dangling evidence reference -> skip the placement
        obj = objects[object_id]
        item: dict[str, Any] = {
            "objectId": object_id,
            "assetId": asset_id,
            "assetType": _asset_type_for(obj),
            "subtype": obj.get("subtype"),
            "locationId": str(placement.get("location_id")),
            "anchor": str(placement.get("anchor")),
            "interaction": str(placement.get("interaction")),
            "evidenceId": str(evidence_id) if evidence_id is not None else None,
            "discovered": evidence_id is not None and str(evidence_id) in discovered_ids,
            "read": evidence_id is not None and str(evidence_id) in read_ids,
        }
        if generated is not None:
            item["generated"] = generated
        emitted_object_ids.add(object_id)
        out.append(item)
    return sorted(out, key=lambda item: item["objectId"])


def project_discovery(
    payload: Mapping[str, Any], evidence_id: str
) -> dict[str, Any] | None:
    """Project the discovery DTO contents: {kind, title, interaction}.

    ``kind``/``title`` come from the evidence's presentation; ``interaction``
    comes from THE placement that links the evidence to the world. Returns
    ``None`` when the evidence is missing OR no placement links it (the
    discovery path refuses those — see InvestigationService).
    """
    fact = _evidence_by_id(payload).get(evidence_id)
    if fact is None:
        return None
    placements = _sort_placements_for_evidence(_placements_of(payload), evidence_id)
    if not placements:
        return None
    presentation = _presentation_of(fact)
    return {
        "kind": str(fact.get("kind")),
        "title": presentation.get("title") or "",
        "interaction": str(placements[0].get("interaction")),
    }


# Kind-specific read-content allowlists (frozen Phase 6 contract). Each entry
# is the EXACT key set of the public ``content`` mapping for that kind; any
# other key — raw proposition material included — can never reach the client.
_READ_CONTENT_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "object": ("subtype", "locationId"),
    "email": ("fromPersonId", "toPersonIds", "subject", "body", "timestamp"),
    "financial": ("rows", "suspicious"),
    "cctv": ("events", "cameraId"),
    "cctv_observation": ("events", "cameraId"),
    "view_record": ("events", "cameraId"),
    "testimonial": ("speakerName", "statement"),
    "witness_statement": ("speakerName", "statement"),
    "statement": ("speakerName", "statement"),
}

_READ_ROW_ALLOWLIST: tuple[str, ...] = ("date", "from", "to", "amount", "currency", "description")
_READ_EVENT_ALLOWLIST: tuple[str, ...] = ("time", "personId", "action")


def _filter_list_of_mappings(
    value: Any, allowed: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Keep only the documented keys of each row/event mapping (present only)."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        out.append(
            {key: entry[key] for key in allowed if key in entry and entry[key] is not None}
        )
    return out


def project_read_content(payload: Mapping[str, Any], evidence_id: str) -> dict[str, Any]:
    """The kind-allowlisted public ``content`` dict of one evidence record.

    Only the documented public presentation fields of the evidence may appear
    (one key per allowlist entry); every missing field is omitted, never
    fabricated. Kinds without an allowlist map to ``{}`` (title/description
    only appear at the DTO level, never inside ``content``).
    """
    fact = _evidence_by_id(payload).get(evidence_id)
    if fact is None:
        return {}
    kind = str(fact.get("kind"))
    allowed = _READ_CONTENT_ALLOWLIST.get(kind)
    if allowed is None:
        return {}
    presentation = _presentation_of(fact)
    content: dict[str, Any] = {}
    for key in allowed:
        if key not in presentation or presentation[key] is None:
            continue
        value = presentation[key]
        if key == "rows":
            content[key] = _filter_list_of_mappings(value, _READ_ROW_ALLOWLIST)
        elif key == "events":
            content[key] = _filter_list_of_mappings(value, _READ_EVENT_ALLOWLIST)
        elif key == "toPersonIds":
            content[key] = (
                [str(v) for v in value] if isinstance(value, list) else [str(value)]
            )
        else:
            content[key] = value
    return content


def player_knowledge_snapshot(snapshot: Any) -> dict[str, list[str]]:
    """PlayerKnowledge DTO dict: sorted frozen id tuples -> public lists.

    The input is a ``PlayerKnowledgeSnapshot`` (frozen sorted tuples); the
    output contains ONLY the three player-observable id lists.
    """
    return {
        "discoveredEvidenceIds": list(snapshot.discovered),
        "readEvidenceIds": list(snapshot.read),
        "visitedLocationIds": list(snapshot.visited),
    }


SAFE_ASSET_TYPE_LABEL = "prop"


__all__ = [
    "DuplicatePublication",
    "PAYLOAD_SCHEMA_VERSION",
    "PublicationError",
    "PublicationService",
    "SAFE_ASSET_TYPE_LABEL",
    "SerializationError",
    "derive_title_from_prompt",
    "evidence_fact_of",
    "evidence_ids_of",
    "placement_for_object",
    "placements_for_evidence",
    "player_knowledge_snapshot",
    "presentation_of",
    "project_discovery",
    "project_read_content",
    "project_world_objects",
    "public_case_dict_from_payload",
    "scene_spec_of",
    "serialize_published_payload",
]