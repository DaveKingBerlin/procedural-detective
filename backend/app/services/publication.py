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

Phase 19G adds RICH EVIDENCE RENDERING: ``project_read_content`` now returns the
closed-render-model payload (``renderType`` + ``summary`` + type-specific
fields, e.g. concrete ``entries`` with observed-at-derived times for
ACTIVITY_LOG/TIMELINE records), so a discovered evidence panel shows the actual
player-visible clue content — not only a generic one-line summary. The render
projection is pure and deterministic (``app.domain.render``); zero provider
calls, zero truth/solver material (Phase19G §3/§9/§10).
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


def public_case_dict_from_payload(
    payload: Mapping[str, Any],
    *,
    discovered: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Map the stored frozen payload into the PublicCaseResponse DTO dict.

    THE explicit allowlist: only the documented public fields are read and an
    entirely new dict is returned. The stored payload (schemaVersion 1)
    serializes the frozen dataclasses with their field names (snake_case);
    this mapper reads ONLY those documented fields and converts them into the
    camelCase DTO keys. Hidden sections (`truth`, `solverProof`, `universes`,
    `report`, `prompt`, `seed`, `model`, `locked`) are never read here.

    Phase 20 (PD-SEC-01) parameter ``discovered`` — the PLAYTHROUGH-scoped
    public-case is only allowed to carry evidence the player already knows:

    - when ``discovered`` is None (the CASE-scoped dossier, REQUIREMENTS
      41.2 public-case evidence list served by ``GET /cases/{id}`` under the
      creator credential): the full evidence list + full placement evidenceIds
      are kept (the case owner generated the case — that data is already
      legitimately known to their role; the Luna audit flagged the
      playthrough-scoped route, not the creator-scoped one);
    - when ``discovered`` is a set of evidence ids (a PLAYTHROUGH-scoped
      ``GET /playthroughs/{id}/public-case``): the ``evidence`` list is
      filtered to ONLY those ids and every world_graph placement's
      ``evidenceId`` is present only for a discovered evidence (null
      otherwise). A fresh playthrough therefore exposes ZERO evidence
      ids/titles/descriptions pre-discovery.
    """
    draft = payload.get("draft")
    if not isinstance(draft, Mapping):
        raise ValueError("payload carries no draft document")
    scene_spec = draft.get("scene")
    discovered_ids = frozenset(discovered) if discovered is not None else None

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
            evidence_id = str(fact.get("id"))
            # PD-SEC-01: the playthrough-scoped public-case carries ONLY the
            # evidence the player has actually discovered (id/title/description
            # are then player-known); nothing undiscovered ever appears.
            if discovered_ids is not None and evidence_id not in discovered_ids:
                continue
            presentation = fact.get("presentation") or {}
            out.append(
                {
                    "id": evidence_id,
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
        for placement in world_graph.get("placements") or ():
            placement_evidence_id = placement.get("evidence_id")
            exposed_evidence_id = (
                str(placement_evidence_id)
                if placement_evidence_id is not None
                else None
            )
            # PD-SEC-01: an undiscovered placement never leaks its evidence id
            # on the playthrough-scoped DTO (null when the player has not
            # discovered it, or in the full creator dossier when discovered is
            # not supplied).
            if discovered_ids is not None and (
                exposed_evidence_id is None
                or exposed_evidence_id not in discovered_ids
            ):
                exposed_evidence_id = None
            world_graph_dto["placements"].append(
                {
                    "objectId": placement.get("object_id"),
                    "assetId": placement.get("asset_id"),
                    "locationId": placement.get("location_id"),
                    "anchor": placement.get("anchor"),
                    "interaction": placement.get("interaction"),
                    "evidenceId": exposed_evidence_id,
                }
            )

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
                # Phase 14 additive: the pinned environment kit version.
                "environmentVersion": scene_spec.get("environment_version"),
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
        # ADV-153 — the player-safe bounded composition notes (sanitized
        # "left out" warnings for decorative unseen objects). Browser seam:
        # surfaced as a small optional note; never solver input.
        "compositionNotes": [
            str(note)
            for note in (draft.get("composition_notes") or ())
            if isinstance(note, str) and note
        ],
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
# explicit allowlist. Phase 19G: ``project_read_content`` additionally consumes
# ONLY the parsed ``observed_at`` strings of the published propositions to
# synthesize concrete player-visible time entries — the proposition objects
# themselves are never exposed.


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
    """THE placement of ``object_id`` (the FIRST in published placement order,
    WITHOUT a visibility filter).

    Returns None when the object has no placement in the pinned version.

    DEF-050: for a legacy/crafted payload with duplicate placements of one
    objectId, this resolves the FIRST published entry. NOTE (ADV-242): the
    runtime interact gate and the bootstrap projection use
    ``visible_placement_for_object`` / ``project_world_objects`` — the FIRST
    VISIBLE placement — so a crafted payload whose first duplicate is
    non-visible projects/interacts on a LATER visible entry. This raw
    first-entry resolver is kept for the tests/owners that need the raw
    published entry; it is NOT the interact gate.
    """
    for placement in _placements_of(payload):
        if str(placement.get("object_id")) == object_id:
            return placement
    return None


def _placement_is_visible(
    payload: Mapping[str, Any], placement: Mapping[str, Any]
) -> bool:
    """True when ``project_world_objects`` would EMIT this placement as a
    player-visible WorldObjectDTO (the Phase 19F visibility gate).

    Mirrors the bootstrap projection's per-placement validity rule exactly:
    the object must exist in the published payload, the assetId must be
    registered (AssetRegistry / catalog) or a valid CURRENT-version procedural
    definition must exist, and a referenced evidenceId must exist in the
    published evidence set. ANY mismatch means the object would NOT appear in
    the player's scene -> it has no player-visible representation and is
    therefore never inspectable (fail-closed; see rule 4 of Phase19F.md).
    """
    objects = _objects_by_id(payload)
    evidence = _evidence_by_id(payload)
    object_id = str(placement.get("object_id"))
    if object_id not in objects:
        return False
    asset_id = placement.get("asset_id")
    if not isinstance(asset_id, str):
        return False

    from app.generation.safety import AssetRegistry, is_procedural_asset_id

    if is_procedural_asset_id(asset_id):
        from app.assets.compiler import validate_embedded_definition

        if validate_embedded_definition(
            asset_id, placement.get("generated_definition")
        ) is None:
            return False
    elif not AssetRegistry.is_allowed(asset_id):
        from app.assets.catalog import load_catalog_from_repo

        catalog = load_catalog_from_repo()
        if asset_id not in catalog.by_id:
            return False
    evidence_id = placement.get("evidence_id")
    if evidence_id is not None and str(evidence_id) not in evidence:
        return False
    return True


def visible_placement_for_object(
    payload: Mapping[str, Any], object_id: str
) -> dict[str, Any] | None:
    """THE placement of ``object_id`` (first in published order) IFF the
    bootstrap projection would emit it as a player-visible WorldObjectDTO.

    Phase 19F UNIVERSAL OBJECT INSPECTION gate: inspectability is derived from
    PUBLICATION SEMANTICS — a published placement with a player-visible
    representation is inspectable by default. A placement with NO player-
    visible representation (unknown object / unregistered or unprojectable
    asset / dangling evidence reference — e.g. an invisible collision-helper /
    non-object placement in a crafted payload) is NEVER interactable and must
    answer the same generic 404 as any unknown id (no inspect-arbitrary-ID
    oracle). ADV-242: this gate resolves IDENTICALLY to the bootstrap
    projection — a NON-VISIBLE duplicate placement is SKIPPED and the FIRST
    VISIBLE placement of the object is returned, so exactly the placement the
    bootstrap shows is the one the interact endpoint accepts (VISIBLE WORLD
    OBJECT => INSPECTABLE). Properly published payloads contain only
    semantic-object placements and NEVER duplicate objectIds
    (``pipeline._world_graph_resolution_issues`` /
    ``safety.validate_world_graph`` prove every placement references a known
    object + registered asset and reject duplicate placements at publish
    time), so this gate is defensive fail-closed depth.
    """
    for placement in _placements_of(payload):
        if str(placement.get("object_id")) != object_id:
            continue
        if not _placement_is_visible(payload, placement):
            # ADV-242: skip a NON-VISIBLE duplicate exactly the way
            # ``project_world_objects`` skips it — the FIRST VISIBLE
            # placement is the one the bootstrap emits, so it is the one that
            # must be interactable. (Before this fix the first non-visible
            # entry returned None and a player-visible object answered 404.)
            continue
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
    - every placement is re-validated (the SAME rule as
      ``visible_placement_for_object``, which the Phase 19F interact gate
      mirrors): the object must exist in the published payload, the assetId
      must be registered (AssetRegistry) or carry a valid CURRENT-version
      procedural definition, and a referenced evidenceId must exist in the
      published evidence set — otherwise the placement is SKIPPED (never
      leak, never crash);
    - DEF-050: duplicate placements for one objectId (legacy/crafted payloads)
      fold into ONE WorldObjectDTO — the FIRST *VISIBLE* placement in
      published order wins (a non-visible first duplicate is SKIPPED),
      exactly matching ``visible_placement_for_object`` (ADV-242: what the
      bootstrap shows is precisely the placement the interact gate accepts);
    - ``evidenceId`` is exposed ONLY as a nullable id (never its content), and
      — PD-SEC-01 (Phase 20) — ONLY for evidence the player has already
      discovered: an UNDISCOVERED object's ``evidenceId`` is null (the id is
      not player-known before investigation; the object keeps its ``discovered``
      / ``read`` flags, which mirror the caller's PlayerKnowledge, and its
      ``interaction`` affordance so the interact endpoint keeps working);
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
            # one WorldObjectDTO per objectId (keep the FIRST VISIBLE
            # placement in published order, matching
            # ``visible_placement_for_object`` — ADV-242: a non-visible
            # first duplicate is skipped, exactly as the interact gate skips
            # it, so every shown object is inspectable).
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
            # Phase 10+ authority: the Asset Oracle catalog owns asset
            # identity. A catalog asset referenced by a placement is projected
            # (the Phase 4 registry is the MVP-era static set predating it).
            from app.assets.catalog import load_catalog_from_repo

            catalog = load_catalog_from_repo()
            if asset_id not in catalog.by_id:
                continue  # unregistered asset -> skip the placement
        evidence_id = placement.get("evidence_id")
        if evidence_id is not None and str(evidence_id) not in evidence:
            continue  # dangling evidence reference -> skip the placement
        obj = objects[object_id]
        exposed_evidence_id: str | None = None
        if evidence_id is not None:
            evidence_str = str(evidence_id)
            # PD-SEC-01: an UNDISCOVERED evidence id is not player-known and
            # must never be exposed pre-discovery. The evidenceId is only
            # emitted once the player has actually discovered it (the id then
            # belongs to PlayerKnowledge and the frontend binds it to the
            # object after a successful interact).
            if evidence_str in discovered_ids:
                exposed_evidence_id = evidence_str
        item: dict[str, Any] = {
            "objectId": object_id,
            "assetId": asset_id,
            "assetType": _asset_type_for(obj),
            "subtype": obj.get("subtype"),
            "locationId": str(placement.get("location_id")),
            "anchor": str(placement.get("anchor")),
            "interaction": str(placement.get("interaction")),
            "evidenceId": exposed_evidence_id,
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
# Phase 19G adds the MESSAGE/DOCUMENT allowlisted readable fields for the
# document/digital record kinds (sender/time/subject/body — Phase19G §8); the
# same strict keys-only rule applies (absent fields are omitted).
# Phase 23 adds ``questionType`` / ``witnessId`` to the witness-STATEMENT-kind
# allowlists ONLY (the interview-source kinds the notebook classifies, Phase23
# §25): a published statement record may tag the question it answers and the
# witness it is attributed to. Every OTHER kind's allowlist is untouched. The
# interview read path additionally OVERRIDES ``questionType`` with the type
# the player actually asked (``WitnessService._read_dto``) and the reload
# record-read path derives both fields deterministically from the pinned
# payload (``witness_statement_content_tags``) — never weaker, additive only.
_READ_CONTENT_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "object": ("subtype", "locationId"),
    "email": ("fromPersonId", "toPersonIds", "subject", "body", "timestamp"),
    "financial": ("rows", "suspicious"),
    "document": ("fromPersonId", "toPersonIds", "subject", "body", "timestamp"),
    "digital": ("fromPersonId", "toPersonIds", "subject", "body", "timestamp"),
    "cctv": ("events", "cameraId"),
    "cctv_observation": ("events", "cameraId"),
    "view_record": ("events", "cameraId"),
    "testimonial": ("speakerName", "statement", "questionType", "witnessId"),
    "witness_statement": ("speakerName", "statement", "questionType", "witnessId"),
    "statement": ("speakerName", "statement", "questionType", "witnessId"),
    "suspect_statement": ("speakerName", "statement", "questionType", "witnessId"),
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


def _allowlisted_content_of(fact: Mapping[str, Any]) -> dict[str, Any]:
    """The kind-allowlisted public ``content`` dict of ONE evidence fact.

    Only the documented public presentation fields of the evidence may appear
    (one key per allowlist entry); every missing field is omitted, never
    fabricated. Kinds without an allowlist map to ``{}``. Phase 19G: this is
    the ALLOWLIST HALF of the read projection — the render envelope built on
    top of it lives in ``app.domain.render`` (the closed render-type model).
    """
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


def project_read_content(payload: Mapping[str, Any], evidence_id: str) -> dict[str, Any]:
    """The Phase 19G player-safe render payload of one DISCOVERED evidence.

    Deterministic and closed (Phase19G §3/§10):

    - ``renderType`` — one of the frozen ``EvidenceRenderType`` values, derived
      from the evidence kind (never from provider output);
    - ``summary`` — the safe title/description-level text;
    - type-specific fields (``entries`` for ACTIVITY_LOG/TIMELINE — whose
      concrete times come from the ALLOWLISTED events/observed-at anchors, so a
      WHEN record never renders as only "around the locked time"; ``comparison``
      for FORENSIC_COMPARISON);
    - the kind-allowlisted public keys (Phase 6 exact allowlist, no more).

    Only the public presentation fields + parsed ``observed_at`` strings of the
    published payload are read; the hidden sections (truth / solverProof /
    universes / report / seed / model / prompt / locked) and the proposition
    objects themselves are never touched. Unknown evidence maps to ``{}``
    (the record-read boundary 404s long before this is reachable).
    """
    fact = _evidence_by_id(payload).get(evidence_id)
    if fact is None:
        return {}
    from app.domain.render import render_payload_of

    return render_payload_of(fact, content=_allowlisted_content_of(fact))


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


def project_witnesses(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Phase 23 — the player-safe witness list of the investigation bootstrap.

    Projects ONLY the published persons with ``role == "witness"`` from the
    pinned payload (doors are closed: a hidden person can never appear, and no
    witness exists outside the pinned case). Each entry carries ONLY the
    public identity + the closed presence enum and the optional ON_SCENE
    object linkage — NEVER statements, never question-availability material.

    ``presence`` uses the SAME deterministic rule as
    ``app.domain.witness.witness_presence`` (ON_SCENE iff the published world
    graph contains a semantic person placement for the witness);
    ``sceneObjectId`` is that placement's object_id when ON_SCENE else null.
    ``displayName`` is the public person name (human-readable), bounded.
    Sorted by witnessId for determinism.
    """
    from app.domain import witness as witness_domain

    draft = _draft_of(payload)
    out: list[dict[str, Any]] = []
    for person in draft.get("persons") or ():
        if not isinstance(person, Mapping):
            continue
        if str(person.get("role")) != "witness":
            continue
        witness_id = str(person.get("person_id") or "")
        if not witness_id:
            continue
        presence, _at_scene = witness_domain.witness_presence(payload, witness_id)
        out.append(
            {
                "witnessId": witness_id,
                "displayName": str(person.get("name") or witness_id)[
                    : witness_domain.MAX_NAME_CHARS
                ],
                "presence": presence.value,
                "sceneObjectId": witness_domain.witness_scene_object_id(
                    payload, witness_id
                ),
            }
        )
    return sorted(out, key=lambda item: item["witnessId"])


def witness_statement_content_tags(
    payload: Mapping[str, Any], evidence_id: str
) -> dict[str, Any] | None:
    """Deterministic Phase 23 interview-source tags of one published record,
    or None when the record is NOT an interview-sourced witness statement.

    Returns ``{"witnessId", "questionType"}`` for a record that:
      - is witness-kind evidence ATTRIBUTED to a role=='witness' person
        (``app.domain.witness.witness_attributed_to`` — the SAME attribution
        rule as the interview grounding), AND
      - grounds the deterministic witness projection for at least one CLOSED
        question (``witness_question_types_of``).

    ``questionType`` is the FIRST grounded closed question in the frozen
    ``ALL_QUESTIONS`` order — the deterministic value a RE-READ record carries
    after a reload (the interview path overrides it with the type actually
    asked — ``WitnessService._read_dto``). Never reads hidden sections; zero
    provider calls.
    """
    fact = _evidence_by_id(payload).get(evidence_id)
    if fact is None:
        return None
    from app.domain import witness as witness_domain

    witness_id = witness_domain.witness_attributed_to(payload, fact)
    if witness_id is None:
        return None
    question_types = witness_domain.witness_question_types_of(payload, fact)
    if not question_types:
        return None
    return {
        "witnessId": str(witness_id),
        "questionType": question_types[0].value,
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
    "project_witnesses",
    "project_world_objects",
    "public_case_dict_from_payload",
    "scene_spec_of",
    "serialize_published_payload",
    "visible_placement_for_object",
    "witness_statement_content_tags",
]
