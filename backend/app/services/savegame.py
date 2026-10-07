"""Phase 32 — SavegameV1 server-side export projection (REPLAYABLE SAVES).

The export is a **REVEAL-GATED server-side projection** of the frozen
published payload (Phase32-SALC-R §2/§5/§6/§8/§23). The browser never receives
the full public case document during play and the playthrough state lives
server-side, so a portable ``.pdcase`` can only be assembled from the
``published_versions`` row — never client-side.

Responsibilities of this module:

1. ``project_savegame_v1`` — the PURE, STRICT-ALLOWLIST projection from the
   pinned published payload to the ``SavegameV1`` document (a fresh Python
   ``dict``). Every value is derived through the EXISTING player-safe
   projection helpers (``app.services.publication`` /
   ``app.services.reveal`` / ``app.domain.render``); the raw payload is NEVER
   serialized wholesale, and the hidden sections (``truth`` / ``solverProof``
   / ``universes`` / ``report`` / ``seed`` / ``model`` / ``prompt`` /
   ``locked``) are never copied.

2. ``SavegameService`` — the REVEAL-GATED orchestration over one ``Store``:
   the playthrough lifecycle must be ``{ACCUSED, REVEALED}`` (the SAME gate as
   the reveal endpoint) BEFORE the export is produced; a missing/unpublished
   pinned version answers the 404 envelope. Purely a read; it never mutates
   the payload or the database.

3. The frozen FORMAT constants: ``.pdcase`` extension, the JSON
   ``application/vnd.procedural-detective.case+json`` MIME type,
   ``formatVersion: 1`` and ``MAX_EXPORT_BYTES`` (documented in
   ``docs/adr/ADR-003-savegame-replay.md``).

Trust model (Phase32 §18): the exported document's ``replayTruth`` is a NEW
explicit allowlist DTO derived ONLY from the already-revealed truth
representation (``app.services.reveal.truth_labels`` + the published
``accusation_tolerance_seconds`` — which the reveal DTO deliberately omits).
The internal ``CaseTruth`` object is NEVER serialized. On import the replay
truth is untrusted, replay-scoped data.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any, Mapping

from app.models.playthroughs import REVEAL_ELIGIBLE_STATES
from app.persistence.store import Store
from app.persistence.timebase import EpochClock
from app.services import publication as pub
from app.services import reveal as reveal_projection
from app.services.accusation import AccusationNotFoundError

# --------------------------------------------------------------------------- #
# frozen SavegameV1 format constants (ADR-003)
# --------------------------------------------------------------------------- #

# The canonical JSON document type marker (phase32 §8).
SAVEGAME_FORMAT = "procedural-detective-case"
# The ONLY supported format version; unknown versions fail closed on import.
SAVEGAME_FORMAT_VERSION = 1
# Portable file extension (lowercase, no dot in the constant).
SAVEGAME_EXTENSION = ".pdcase"
# Preferred MIME type of the exported JSON document.
SAVEGAME_MIME_TYPE = "application/vnd.procedural-detective.case+json"

# Hard size bound of one exported document (phase32 §20): a realistic demo
# export is ~45 KiB; 5 MiB gives three orders of magnitude headroom while
# keeping the transport bounded (defense-in-depth over the source payload's
# own 8 MiB publication bound).
MAX_EXPORT_BYTES = 5 * 1024 * 1024

# The closed metadata.source vocabulary (phase32 §10): a save from a case the
# server generated with a real model is "generated"; the deterministic
# fixture-based paths (default fake provider and every demo fixture) publish
# ``payload.model is None`` so their export is labelled "demo" — the honest,
# payload-derivable distinction (documented in ADR-003).
SAVEGAME_SOURCES = frozenset({"generated", "demo"})

# The closed metadata.difficulty vocabulary (REQUIREMENTS 35 / frontend
# ``SAVEGAME_DIFFICULTIES``). Any other value is normalized to ``null`` at
# projection time (DEF-052): the demo path and every real case row belong to
# this vocabulary, and an out-of-vocabulary label must never be exported
# verbatim (the frontend import would reject the file).
SAVEGAME_DIFFICULTIES = frozenset({"easy", "medium", "hard"})

# Lifecycle states from which the reveal/export gate may pass (REQUIREMENTS
# 40.12 / Phase7 E — the SAME closed set as the reveal endpoint). Imported
# from the playthroughs model so the gate vocabulary has ONE source of truth:
# "ACCUSED" | "REVEALED".

# Deterministic neutral title fallback (mirrors the publication serializer).
_UNTITLED = "Untitled Case"

# Only these ASCII characters are kept in an export filename derived from a
# server-owned case id (defense-in-depth for Content-Disposition). Dots are
# deliberately EXCLUDED so no path/shape (``..``, ``.``, ``u.`) can ever
# survive into a header; the ``.pdcase`` extension is appended separately.
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


class SavegameError(Exception):
    """Base class for savegame export errors."""


class SavegameUnavailableError(SavegameError):
    """The export was requested before the playthrough reached a reveal-
    eligible lifecycle state (-> 403 REVEAL_NOT_AVAILABLE, mirroring the
    reveal gate philosophy)."""


class SavegameTooLargeError(SavegameError):
    """The projected document exceeds ``MAX_EXPORT_BYTES`` (sanitized 500)."""


class SavegameProjectionError(SavegameError):
    """The frozen published payload cannot be projected into an importable
    SavegameV1 document (a required section is absent/malformed). Raised
    BEFORE any document bytes exist — the export pipeline FAILS CLOSED instead
    of emitting a shape the frontend import would reject (DEF-051 /
    ADV-32F-06). Not reachable through the validated publication pipeline
    (every published case carries a scene); defence-in-depth for tampered
    rows/bugs. Answers the sanitized 500 envelope at the API."""


# --------------------------------------------------------------------------- #
# deterministic ISO-8601 UTC helpers
# --------------------------------------------------------------------------- #


def _now_utc() -> str:
    """Deterministic ISO-8601 UTC timestamp for ``exportedAt`` (second
    precision, trailing ``Z``)."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _source_of(payload: Mapping[str, Any]) -> str:
    """metadata.source: ``generated`` when the frozen payload recorded a real
    provider model, else ``demo`` (the deterministic fixture/fake path records
    ``model: null``). Always one of the closed ``SAVEGAME_SOURCES`` set. The
    model VALUE is NEVER exported — only this boolean predicate outcome is
    (DEF-046: the ADR §5 secret-exclusion wording now states exactly this)."""
    model = payload.get("model")
    if isinstance(model, str) and model:
        return "generated"
    return "demo"


def _bounded_difficulty(difficulty: object) -> str | None:
    """Bound ``metadata.difficulty`` to the documented closed vocabulary
    (REQUIREMENTS 35 / ``SAVEGAME_DIFFICULTIES``); anything else — a non-enum
    label (e.g. ``"extreme"``) or a non-string value — is normalized to
    ``null`` deterministically (DEF-052 / ADV-32F-07). The frontend import
    only accepts the closed set or ``null``, so a bounded export is always
    importable."""
    if isinstance(difficulty, str) and difficulty in SAVEGAME_DIFFICULTIES:
        return difficulty
    return None


def _scene_spec_or_raise(draft: Mapping[str, Any]) -> Mapping[str, Any]:
    """The ``draft.scene`` mapping as a required section of the export.

    FAILS CLOSED with ``SavegameProjectionError`` when the scene is absent,
    not a mapping, or lacks a non-empty ``location_id``/``name`` — the exact
    fields the frontend ``validateScene`` requires. The pipeline must never
    emit ``scene.location: {locationId: null, name: null}`` (a shape the
    import rejects), so a scene-less/malformed draft raises a typed
    projection error BEFORE any document is produced (DEF-051 / ADV-32F-06).
    """
    scene_spec = draft.get("scene")
    if not isinstance(scene_spec, Mapping):
        raise SavegameProjectionError("payload carries no scene document")
    location_id = scene_spec.get("location_id")
    name = scene_spec.get("name")
    if not isinstance(location_id, str) or not location_id:
        raise SavegameProjectionError("scene carries no non-empty location_id")
    if not isinstance(name, str) or not name:
        raise SavegameProjectionError("scene carries no non-empty name")
    return scene_spec


# --------------------------------------------------------------------------- #
# the strict allowlist projection (pure — no store, no clock, no mutation)
# --------------------------------------------------------------------------- #


def project_savegame_v1(
    payload: Mapping[str, Any],
    *,
    difficulty: str | None = None,
    exported_at: str | None = None,
) -> dict[str, Any]:
    """Project the published payload into the ``SavegameV1`` dict.

    STRICT ALLOWLIST ONLY. The returned document contains exactly:

    - ``format`` / ``formatVersion`` / ``exportedAt`` (top level);
    - ``case.metadata`` — title, difficulty (external, optional), source
      (``generated`` | ``demo``), ``sourceCaseId`` (display-only, never
      authority) and the optional pinned ``environmentId``;
    - ``case.publicCase`` — the EXACT ``PublicCaseResponse`` allowance DTO
      shape via ``public_case_dict_from_payload`` (full creator dossier:
      scene, persons, motives, objects, locations, travel rules, world graph
      with placement evidence ids and the evidence summary list);
    - ``case.scene`` — the EXACT bootstrap scene shape
      (``{location, environmentId, environmentVersion, worldObjects}``) with
      the FULL world-object projection (every published evidence considered
      discovered/read — the document is an honest spoiler archive);
    - ``case.candidates`` — the player-safe accusation candidates block;
    - ``case.witnesses`` — the player-safe witness list;
    - ``case.evidence`` — one enrichment record per published fact carrying
      the EXACT read-record content shape (``project_read_content`` render
      payload + the deterministic Phase 23 interview-source tags), so a
      replay runtime can serve record reads with zero new parsing;
    - ``case.replayTruth`` — ``ReplayTruthV1`` (four canonical ids + the
      canonical crime time + ``accusationToleranceSeconds`` + the public
      winner labels derived through ``truth_labels``).

    Raises ``ValueError`` on a payload without a ``draft`` section,
    ``SavegameProjectionError`` on a scene-less/malformed ``draft.scene``
    (DEF-051 — never a null-location scene) and ``RevealProjectionError`` on
    an invalid/missing truth section (fail closed: no partial replay truth is
    ever exported).
    """
    draft = payload.get("draft")
    if not isinstance(draft, Mapping):
        raise ValueError("payload carries no draft document")

    now = exported_at if isinstance(exported_at, str) and exported_at else _now_utc()

    # Full knowledge set: every published evidence id is considered
    # discovered/read for the exported player-facing projections.
    all_evidence_ids = pub.evidence_ids_of(payload)

    truth: dict[str, str] = dict(reveal_projection.truth_labels(payload))
    tolerance = _tolerance_seconds_of(payload)

    # DEF-051 / ADV-32F-06: a scene-less/malformed ``draft.scene`` FAILS
    # CLOSED with a typed ``SavegameProjectionError`` — the pipeline NEVER
    # emits ``scene.location: {locationId: null, name: null}`` (a shape the
    # frontend import rejects). Resolved AFTER the truth so a payload without
    # a truth section still fails with the reveal projection error first.
    scene_spec = _scene_spec_or_raise(draft)

    return {
        "format": SAVEGAME_FORMAT,
        "formatVersion": SAVEGAME_FORMAT_VERSION,
        "exportedAt": now,
        "case": {
            "metadata": {
                "title": _title_of(payload),
                "difficulty": _bounded_difficulty(difficulty),
                "source": _source_of(payload),
                "sourceCaseId": str(payload.get("caseId") or ""),
                "environmentId": scene_spec.get("environment_id"),
            },
            # The exact PublicCaseResponse allowance shape (creator dossier).
            "publicCase": pub.public_case_dict_from_payload(
                payload, discovered=frozenset(all_evidence_ids)
            ),
            # The exact bootstrap scene shape, fully known (spoilered archive).
            "scene": {
                "location": {
                    "locationId": scene_spec.get("location_id"),
                    "name": scene_spec.get("name"),
                },
                "environmentId": scene_spec.get("environment_id"),
                "environmentVersion": scene_spec.get("environment_version"),
                "worldObjects": pub.project_world_objects(
                    payload,
                    discovered=frozenset(all_evidence_ids),
                    read=frozenset(all_evidence_ids),
                ),
            },
            "candidates": reveal_projection.candidate_block_of(payload),
            "witnesses": pub.project_witnesses(payload),
            "evidence": _evidence_records_of(payload, draft),
            "replayTruth": {
                # ReplayTruthV1 — the minimal solution data to evaluate a
                # replay and reveal it locally. Four canonical ids + the
                # canonical crime time + tolerance (the reveal DTO omits
                # tolerance, so the save MUST carry it for ``timeCorrect``)
                # + the public winner labels for rendering THE TRUTH.
                "murdererId": truth["murdererId"],
                "motiveId": truth["motiveId"],
                "weaponId": truth["weaponId"],
                "crimeTime": truth["crimeTime"],
                "accusationToleranceSeconds": tolerance,
                "murdererName": truth["murdererName"],
                "motiveLabel": truth["motiveLabel"],
                "weaponName": truth["weaponName"],
            },
        },
    }


def _title_of(payload: Mapping[str, Any]) -> str:
    title = payload.get("title")
    if isinstance(title, str) and title:
        return title
    return _UNTITLED


def _tolerance_seconds_of(payload: Mapping[str, Any]) -> int:
    """The published accusation tolerance of the canonical crime time.

    The reveal DTO deliberately omits tolerance; the save MUST carry it
    explicitly so a browser-local replay can evaluate ``timeCorrect`` with the
    EXACT server scoring time set (REQUIREMENTS 31.7 / DEC-003).
    """
    truth = payload.get("truth")
    crime = truth.get("crime") if isinstance(truth, Mapping) else None
    crime_time = crime.get("crime_time") if isinstance(crime, Mapping) else None
    tolerance = crime_time.get("accusation_tolerance_seconds") if isinstance(crime_time, Mapping) else None
    try:
        return int(tolerance)
    except (TypeError, ValueError):
        raise ValueError("payload truth carries no numeric accusation tolerance") from None


def _evidence_records_of(
    payload: Mapping[str, Any], draft: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """One per-record enrichment document per published evidence fact.

    Each record carries the EXACT ``EvidenceReadResultDTO`` content shape the
    player receives from ``read_record`` after discovery:
    ``project_read_content`` (closed render payload) PLUS the deterministic
    Phase 23 interview-source tags (``questionType`` / ``witnessId``) when the
    record is an interview-sourced witness statement. Title/description come
    from the SAME public presentation allowlist; ``openedAt``/``readByPlayer``
    are playthrough state and deliberately NOT exported (the replay runtime
    owns those). Published order is preserved (the frozen payload is
    immutable, so the projection is byte-deterministic).
    """
    records: list[dict[str, Any]] = []
    for fact in draft.get("evidence") or ():
        if not isinstance(fact, Mapping):
            continue
        evidence_id = str(fact.get("id"))
        if not evidence_id:
            continue
        content = pub.project_read_content(payload, evidence_id)
        tags = pub.witness_statement_content_tags(payload, evidence_id)
        if tags:
            content = {**content, **tags}
        presentation = fact.get("presentation")
        records.append(
            {
                "evidenceId": evidence_id,
                "kind": str(fact.get("kind")),
                "reliability": _reliability_of(fact),
                "title": pub.read_dto_title(fact),
                "description": (
                    presentation.get("description")
                    if isinstance(presentation, Mapping)
                    else None
                ),
                "content": content,
            }
        )
    return records


def _reliability_of(fact: Mapping[str, Any]) -> str | None:
    reliability = fact.get("reliability")
    return str(reliability) if isinstance(reliability, str) and reliability else None


# --------------------------------------------------------------------------- #
# deterministic serialization + filename helpers
# --------------------------------------------------------------------------- #


def serialize_savegame_v1(
    document: Mapping[str, Any],
    *,
    max_bytes: int = MAX_EXPORT_BYTES,
) -> str:
    """Deterministic compact JSON serialization of a SavegameV1 document.

    ``sort_keys=True`` makes repeated exports byte-identical. Raises
    ``SavegameTooLargeError`` when the UTF-8 bytes exceed ``max_bytes`` so the
    transport stays bounded (defense-in-depth).
    """
    text = json.dumps(
        document,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(text.encode("utf-8")) > int(max_bytes):
        raise SavegameTooLargeError("savegame export exceeds the size bound")
    return text


def savegame_filename(
    case_id: object,
    *,
    extension: str = SAVEGAME_EXTENSION,
) -> str:
    """A filesystem/HTTP safe suggested filename for one export.

    The case id is a server-owned opaque value but is sanitized anyway
    (defense-in-depth): only ``[A-Za-z0-9._-]`` survive, empty results fall
    back to ``case``. Never contains a path separator or control character.
    """
    raw = str(case_id or "")
    safe = _FILENAME_SAFE_RE.sub("_", raw)
    safe = re.sub(r"_+", "_", safe).strip("_-")
    if not safe:
        safe = "case"
    return f"procedural-detective-case-{safe}{extension}"


# --------------------------------------------------------------------------- #
# reveal-gated orchestration over one Store
# --------------------------------------------------------------------------- #


class SavegameService:
    """REVEAL-GATED export orchestration (read-only; zero mutation)."""

    def __init__(self, store: Store, clock: Any = None) -> None:
        if not isinstance(store, Store):
            raise TypeError("SavegameService requires a Store")
        self._store = store
        self._clock = clock if clock is not None else EpochClock()

    @property
    def store(self) -> Store:
        return self._store

    def _pinned_payload(self, playthrough: Any) -> dict[str, Any]:
        """The pinned published payload (never "latest", never memory)."""
        row = self._store.get_published(
            playthrough.case_id, playthrough.case_version
        )
        if row is None:
            raise AccusationNotFoundError("pinned published version unavailable")
        try:
            payload = json.loads(row.payload_json)
        except (ValueError, TypeError):
            raise AccusationNotFoundError("pinned published payload unreadable") from None
        if not isinstance(payload, Mapping):
            raise AccusationNotFoundError("pinned published payload malformed") from None
        return dict(payload)

    def get_export_dict(
        self,
        playthrough: Any,
        *,
        exported_at: str | None = None,
    ) -> dict[str, Any]:
        """The SavegameV1 dict for a REVEAL-ELIGIBLE playthrough.

        Raised errors: ``AccusationNotFoundError`` (missing/unreadable pinned
        version -> 404) and ``SavegameUnavailableError`` (lifecycle not in
        ``{ACCUSED, REVEALED}`` -> 403 REVEAL_NOT_AVAILABLE — the SAME gate as
        the reveal endpoint). The difficulty label is read from the owned
        ``cases`` row (optional metadata; never authoritative).
        """
        self._require_reveal_eligible(playthrough)
        payload = self._pinned_payload(playthrough)
        difficulty = None
        case_row = self._store.get_case(playthrough.case_id)
        if case_row is not None:
            difficulty = case_row.difficulty
        return project_savegame_v1(
            payload,
            difficulty=difficulty,
            exported_at=exported_at,
        )

    def get_export(
        self,
        playthrough: Any,
        *,
        exported_at: str | None = None,
    ) -> str:
        """The serialized SavegameV1 JSON text for a reveal-eligible PT."""
        document = self.get_export_dict(playthrough, exported_at=exported_at)
        return serialize_savegame_v1(document)

    def _require_reveal_eligible(self, playthrough: Any) -> None:
        """Mirror the reveal gate (REQUIREMENTS 40.12 / Phase7 E): the export
        is ONLY available once the truth has legitimately been revealed — the
        playthrough lifecycle is ``{ACCUSED, REVEALED}``. Before that the
        endpoint answers 403 REVEAL_NOT_AVAILABLE and the replay truth is
        NEVER projected (no truth preload is possible pre-reveal)."""
        if getattr(playthrough, "state", None) not in REVEAL_ELIGIBLE_STATES:
            raise SavegameUnavailableError(
                "savegame export is only available after the truth is revealed"
            )


__all__ = [
    "MAX_EXPORT_BYTES",
    "REVEAL_ELIGIBLE_STATES",
    "SAVEGAME_DIFFICULTIES",
    "SAVEGAME_EXTENSION",
    "SAVEGAME_FORMAT",
    "SAVEGAME_FORMAT_VERSION",
    "SAVEGAME_MIME_TYPE",
    "SAVEGAME_SOURCES",
    "SavegameError",
    "SavegameProjectionError",
    "SavegameService",
    "SavegameTooLargeError",
    "SavegameUnavailableError",
    "project_savegame_v1",
    "savegame_filename",
    "serialize_savegame_v1",
]