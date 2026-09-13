"""Thin transaction-safe persistence store (Phase5 D).

One ``Store`` owns ONE SQLite engine (a real FILE in tests — never
``:memory:`` so cross-connection locking and restart semantics are real) and
exposes small units of work, each committing atomically.

Design notes:

- **Foreign keys are ON**: the engine installs ``PRAGMA foreign_keys=ON`` on
  every connect (SQLite does not enforce FKs by default). The readiness
  engine in ``app/db/session.py`` is intentionally untouched; the store owns
  its own engine factory.
- **Writes are serialized per store instance** by ``self._lock`` (an
  ``RLock``). This makes SQLite write behaviour deterministic under threads.
  The semantics NEVER depend on this lock: the unique constraints / composite
  primary keys are the real guards, and the duplicate-publication /
  duplicate-version tests exercise exactly those constraints through
  independent store instances with independent locks.
- **Bounded retry**: write transactions retry a bounded number of times on
  SQLite ``OperationalError`` (database locked / busy) — the engine also
  sets a high SQLite busy timeout so the filesystem layer waits instead of
  failing instantly.
- **Never raises SQLAlchemy errors to callers**: ``IntegrityError`` on a
  declared unique identity is translated into a domain exception
  (``DuplicateSession``, ``DuplicateCaseVersion``, ``DuplicateAttempt``,
  ``DuplicatePublication``, ``DuplicateCredential``, ``DuplicatePlaythrough``)
  after a rollback.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence

from sqlalchemy import event, select, text
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models.accusations import Accusation
from app.models.cases import Case, CaseVersion
from app.models.credentials import CreatorCredential
from app.models.generation import GenerationAttempt
from app.models.knowledge import PlayerKnowledge
from app.models.playthroughs import Playthrough
from app.models.published import PublishedVersion
from app.models.quota import AnonymousQuotaSession

logger = logging.getLogger("procedural-detective.store")

_WRITE_RETRIES = 5


class StoreError(Exception):
    """Base class for store-level domain errors (never SQLAlchemy errors)."""


class DuplicateSession(StoreError):
    """An anonymous quota session_id already exists."""


class DuplicateCase(StoreError):
    """A case_id already exists."""


class DuplicateCaseVersion(StoreError):
    """A (case_id, version) row already exists (collision backstop)."""


class DuplicateAttempt(StoreError):
    """A generation attempt for (case_id, case_version) already exists."""


class DuplicatePublication(StoreError):
    """A published_versions row for (case_id, case_version) already exists.

    Raised after a full rollback of the publication transaction — the
    published row is IMMUTABLE and never overwritten (Phase5 INVARIANT 2).
    """


class DuplicateCredential(StoreError):
    """A creator credential with this verifier already exists."""


class DuplicatePlaythrough(StoreError):
    """A playthrough_id already exists."""


class DuplicateAccusation(StoreError):
    """The playthrough already has an authoritative accusation.

    Raised by ``insert_accusation_if_unaccused`` after a FULL rollback of the
    compare-and-set transaction when the playthrough is no longer a
    pre-accusation (CREATED/PLAYING) state — a later/concurrent accusation
    attempt loses and nothing is persisted (Phase7 C/I, REQUIREMENTS 40.11).
    """


class VersionAllocationError(StoreError):
    """Could not allocate the next case version (case row missing)."""


class VersionNotFoundError(StoreError):
    """The exact (case_id, case_version) row does not exist (-> 404)."""


class VersionNotPublishedError(StoreError):
    """The exact version exists but is not PUBLISHED (-> 409)."""


class PlayerKnowledgeError(StoreError):
    """PlayerKnowledge constraint/integrity failure (never SQLAlchemy errors).

    Raised after a rollback when the knowledge row cannot be created or
    mutated: unknown playthrough, a (case_id, case_version) mismatch against
    the pinned playthrough row, or any database constraint failure. Callers
    translate it into a clean sanitized API error.
    """


def _knowledge_json_load_set(raw: str | None) -> set[str]:
    """Parse a JSON-array text column into a set of strings (never raises)."""
    try:
        items = json.loads(raw or "[]")
    except (ValueError, TypeError):
        items = []
    if not isinstance(items, list):
        items = []
    return {str(item) for item in items}


def _knowledge_json_dump_set(values: Any) -> str:
    """Canonical deterministic JSON array (sorted) for a set of strings."""
    return json.dumps(sorted(str(v) for v in values), separators=(",", ":"))


def _knowledge_json_load_map(raw: str | None) -> dict[str, Any]:
    """Parse a JSON-object text column into a plain dict (never raises)."""
    try:
        obj = json.loads(raw or "{}")
    except (ValueError, TypeError):
        obj = {}
    return obj if isinstance(obj, dict) else {}


def _knowledge_json_dump_map(obj: Mapping[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@dataclasses.dataclass(frozen=True)
class PlayerKnowledgeSnapshot:
    """Frozen sorted snapshot of ONE playthrough's knowledge row.

    ``opened_at`` maps evidence_id -> epoch float of the FIRST read (stable
    across repeat reads; drives ``openedAt`` in the read DTO). ``updated_at``
    is the row's last structural change epoch.
    """

    discovered: tuple[str, ...] = ()
    read: tuple[str, ...] = ()
    visited: tuple[str, ...] = ()
    opened_at: Mapping[str, float] = dataclasses.field(default_factory=dict)
    updated_at: float = 0.0


def create_store_engine(url: str) -> Engine:
    """Create the store engine with ``PRAGMA foreign_keys=ON`` on connect.

    A separate engine from the readiness engine: it may only ever be used by
    the store (check_same_thread=False is required for FastAPI's threadpool
    workers; the pragma makes referential integrity real).
    """
    engine = create_engine(
        url,
        connect_args={
            "check_same_thread": False,
            "timeout": 30.0,
        },
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _is_locked_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


class Store:
    """Transaction-safe store over one SQLite engine."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._engine = create_store_engine(url)
        self._lock = threading.RLock()
        self._writes_logged = 0

    # ------------------------------------------------------------------ #
    # engine lifecycle
    # ------------------------------------------------------------------ #

    def dispose(self) -> None:
        """Close every pooled connection and release the file handle."""
        self._engine.dispose()

    @property
    def engine(self) -> Engine:
        """The underlying engine (readiness uses its own engine, not this)."""
        return self._engine

    # ------------------------------------------------------------------ #
    # transaction helpers
    # ------------------------------------------------------------------ #

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """One atomic write transaction (commit on success, rollback on error).

        Used by every public write method AND composed by callers
        (PublicationService.publish_transactionally) for multi-row atomic
        publication. Bounded retry on sqlite lock contention.
        """
        attempts = 0
        while True:
            with self._lock:
                session = Session(self._engine)
                try:
                    yield session
                    session.commit()
                    return
                except OperationalError as exc:  # sqlite lock contention
                    session.rollback()
                    attempts += 1
                    if _is_locked_error(exc) and attempts < _WRITE_RETRIES:
                        continue
                    raise
                except Exception:
                    session.rollback()
                    raise
                finally:
                    session.close()

    def _read_session(self) -> Session:
        """Read-only session (closed by the caller)."""
        return Session(self._engine)

    # ------------------------------------------------------------------ #
    # anonymous quota sessions
    # ------------------------------------------------------------------ #

    def create_session(
        self,
        *,
        session_id: str,
        token_verifier: str,
        quota_window_end: float,
        created_at: float,
        generations_count: int = 0,
    ) -> AnonymousQuotaSession:
        """Create one durable quota session. Raises ``DuplicateSession``."""
        with self.transaction() as session:
            row = AnonymousQuotaSession(
                session_id=session_id,
                token_verifier=token_verifier,
                quota_window_end=quota_window_end,
                created_at=created_at,
                generations_count=int(generations_count),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise DuplicateSession(
                    f"anonymous quota session {session_id!r} already exists"
                ) from None
            return AnonymousQuotaSession(
                session_id=row.session_id,
                token_verifier=row.token_verifier,
                quota_window_end=row.quota_window_end,
                created_at=row.created_at,
                generations_count=row.generations_count,
            )

    def get_session(self, session_id: str) -> AnonymousQuotaSession | None:
        with self._read_session() as session:
            row = session.get(AnonymousQuotaSession, session_id)
            return (
                AnonymousQuotaSession(
                    session_id=row.session_id,
                    token_verifier=row.token_verifier,
                    quota_window_end=row.quota_window_end,
                    created_at=row.created_at,
                    generations_count=row.generations_count,
                )
                if row is not None
                else None
            )

    def get_session_by_verifier(
        self, token_verifier: str
    ) -> AnonymousQuotaSession | None:
        with self._read_session() as session:
            row = session.execute(
                select(AnonymousQuotaSession).where(
                    AnonymousQuotaSession.token_verifier == token_verifier
                )
            ).scalar_one_or_none()
            return (
                AnonymousQuotaSession(
                    session_id=row.session_id,
                    token_verifier=row.token_verifier,
                    quota_window_end=row.quota_window_end,
                    created_at=row.created_at,
                    generations_count=row.generations_count,
                )
                if row is not None
                else None
            )

    def update_session_generations(self, session_id: str, count: int) -> bool:
        """Sync the durable generation counter (returns True when updated)."""
        with self.transaction() as session:
            row = session.get(AnonymousQuotaSession, session_id)
            if row is None:
                return False
            row.generations_count = int(count)
            return True

    def list_sessions(self) -> Sequence[AnonymousQuotaSession]:
        """All sessions (used at service startup to rehydrate admission)."""
        with self._read_session() as session:
            rows = list(
                session.scalars(
                    select(AnonymousQuotaSession).order_by(
                        AnonymousQuotaSession.created_at
                    )
                )
            )
            return [
                AnonymousQuotaSession(
                    session_id=r.session_id,
                    token_verifier=r.token_verifier,
                    quota_window_end=r.quota_window_end,
                    created_at=r.created_at,
                    generations_count=r.generations_count,
                )
                for r in rows
            ]

    # ------------------------------------------------------------------ #
    # cases / versions
    # ------------------------------------------------------------------ #

    def create_case(
        self,
        *,
        case_id: str,
        quota_session_id: str,
        title: str,
        difficulty: str | None,
        created_at: float,
        next_version: int = 1,
    ) -> Case:
        """Create the stable case row. Raises ``DuplicateCase``."""
        with self.transaction() as session:
            row = Case(
                case_id=case_id,
                quota_session_id=quota_session_id,
                title=title,
                difficulty=difficulty,
                next_version=next_version,
                created_at=created_at,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise DuplicateCase(f"case {case_id!r} already exists") from None
            return Case(
                case_id=row.case_id,
                quota_session_id=row.quota_session_id,
                title=row.title,
                difficulty=row.difficulty,
                next_version=row.next_version,
                created_at=row.created_at,
            )

    def get_case(self, case_id: str) -> Case | None:
        with self._read_session() as session:
            row = session.get(Case, case_id)
            return (
                Case(
                    case_id=row.case_id,
                    quota_session_id=row.quota_session_id,
                    title=row.title,
                    difficulty=row.difficulty,
                    next_version=row.next_version,
                    created_at=row.created_at,
                )
                if row is not None
                else None
            )

    def allocate_version(self, case_id: str) -> int:
        """Atomically allocate the next CaseVersion number.

        Primary path: ``UPDATE cases SET next_version = next_version + 1
        WHERE case_id = :cid RETURNING next_version`` (SQLite >= 3.35).
        Fallback path (when RETURNING is unavailable): a transaction re-read
        plus the (case_id, version) unique constraint as the collision
        backstop with bounded retry (a lost race maps to 409 at the API
        layer).
        """
        update_sql = text(
            "UPDATE cases SET next_version = next_version + 1 "
            "WHERE case_id = :cid RETURNING next_version"
        )
        try:
            with self.transaction() as session:
                raw = session.execute(update_sql, {"cid": case_id}).scalar_one_or_none()
                if raw is None:
                    raise VersionAllocationError(
                        f"cannot allocate a version for unknown case {case_id!r}"
                    )
                return int(raw) - 1
        except VersionAllocationError:
            raise
        except (OperationalError, NotImplementedError, TypeError):
            return self._allocate_version_fallback(case_id)

    def _allocate_version_fallback(self, case_id: str) -> int:
        """RETURNING-free allocation: unique (case_id, version) as backstop."""
        last_error: Exception | None = None
        for _ in range(_WRITE_RETRIES):
            try:
                with self.transaction() as session:
                    row = session.get(Case, case_id)
                    if row is None:
                        raise VersionAllocationError(
                            f"cannot allocate a version for unknown case {case_id!r}"
                        )
                    version = row.next_version
                    row.next_version = version + 1
                    try:
                        session.flush()
                    except IntegrityError:
                        # concurrent allocator won the version -> retry once
                        continue
                    return int(version)
            except StoreError:
                raise
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = exc
                continue
        raise StoreError(
            f"could not allocate a case version: {last_error or 'retries exhausted'}"
        )

    def create_case_version(
        self,
        *,
        case_id: str,
        version: int,
        state: str,
        generation_id: str,
        created_at: float,
        state_reason: str | None = None,
    ) -> CaseVersion:
        """Insert one CaseVersion. Raises ``DuplicateCaseVersion``."""
        with self.transaction() as session:
            row = CaseVersion(
                case_id=case_id,
                version=version,
                state=state,
                state_reason=state_reason,
                generation_id=generation_id,
                created_at=created_at,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise DuplicateCaseVersion(
                    f"case version ({case_id!r}, v{version}) already exists"
                ) from None
            return CaseVersion(
                case_id=row.case_id,
                version=row.version,
                state=row.state,
                state_reason=row.state_reason,
                generation_id=row.generation_id,
                created_at=row.created_at,
            )

    def get_case_version(self, case_id: str, version: int) -> CaseVersion | None:
        """EXACT-version lookup — never follows 'latest'."""
        with self._read_session() as session:
            row = session.get(CaseVersion, (case_id, version))
            return (
                CaseVersion(
                    case_id=row.case_id,
                    version=row.version,
                    state=row.state,
                    state_reason=row.state_reason,
                    generation_id=row.generation_id,
                    created_at=row.created_at,
                )
                if row is not None
                else None
            )

    def get_case_version_by_generation_id(
        self, generation_id: str, *, case_id: str | None = None
    ) -> CaseVersion | None:
        """Resolve a CaseVersion by its per-case public generation label.

        ``generation_id`` is per-case monotonic (GEN-1, GEN-2, ...): without a
        ``case_id`` scope the label is ambiguous, so unknown/ambiguous lookups
        return None (the API always scopes by the creator credential's case).
        """
        with self._read_session() as session:
            stmt = select(CaseVersion).where(
                CaseVersion.generation_id == generation_id
            )
            if case_id is not None:
                stmt = stmt.where(CaseVersion.case_id == case_id)
            rows = list(
                session.scalars(stmt.order_by(CaseVersion.created_at)).all()
            )
            if len(rows) != 1:
                return None
            row = rows[0]
            return CaseVersion(
                case_id=row.case_id,
                version=row.version,
                state=row.state,
                state_reason=row.state_reason,
                generation_id=row.generation_id,
                created_at=row.created_at,
            )

    def update_case_version_state(
        self,
        case_id: str,
        version: int,
        state: str,
        *,
        state_reason: str | None = None,
    ) -> bool:
        """Transition the (case_id, version) row (used by publication)."""
        with self.transaction() as session:
            row = session.get(CaseVersion, (case_id, version))
            if row is None:
                return False
            row.state = state
            row.state_reason = state_reason
            return True

    # ------------------------------------------------------------------ #
    # generation attempts
    # ------------------------------------------------------------------ #

    def upsert_generation_attempt(
        self,
        *,
        attempt_id: str,
        case_id: str,
        case_version: int,
        status: str,
        stage: str | None,
        progress: int,
        created_at: float,
        updated_at: float,
    ) -> GenerationAttempt:
        """Insert or update ONE attempt.

        The SAME attempt persisted twice is an idempotent update (a no-op
        duplicate-row test guard); a DIFFERENT attempt for the same
        (case_id, case_version) raises ``DuplicateAttempt`` via the UNIQUE
        constraint.
        """
        with self.transaction() as session:
            row = session.get(GenerationAttempt, attempt_id)
            if row is None:
                row = GenerationAttempt(
                    attempt_id=attempt_id,
                    case_id=case_id,
                    case_version=case_version,
                    status=status,
                    stage=stage,
                    progress=int(progress),
                    created_at=created_at,
                    updated_at=updated_at,
                )
                session.add(row)
                try:
                    session.flush()
                except IntegrityError:
                    raise DuplicateAttempt(
                        f"a generation attempt for ({case_id!r}, v{case_version}) "
                        "already exists"
                    ) from None
            else:
                row.status = status
                row.stage = stage
                row.progress = int(progress)
                row.updated_at = updated_at
            return GenerationAttempt(
                attempt_id=row.attempt_id,
                case_id=row.case_id,
                case_version=row.case_version,
                status=row.status,
                stage=row.stage,
                progress=row.progress,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    def get_generation_attempt_by_generation_id(
        self, generation_id: str, *, case_id: str | None = None
    ) -> GenerationAttempt | None:
        """Latest attempt whose CaseVersion carries ``generation_id``.

        Scoped by ``case_id`` when given (the API always has it from the
        creator credential). Per-case labels without a case scope are
        ambiguous -> None.
        """
        version_row = self.get_case_version_by_generation_id(
            generation_id, case_id=case_id
        )
        if version_row is None:
            return None
        with self._read_session() as session:
            row = session.execute(
                select(GenerationAttempt).where(
                    GenerationAttempt.case_id == version_row.case_id,
                    GenerationAttempt.case_version == version_row.version,
                )
            ).scalar_one_or_none()
            return (
                GenerationAttempt(
                    attempt_id=row.attempt_id,
                    case_id=row.case_id,
                    case_version=row.case_version,
                    status=row.status,
                    stage=row.stage,
                    progress=row.progress,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                if row is not None
                else None
            )

    def get_generation_attempt_by_id(
        self, attempt_id: str
    ) -> GenerationAttempt | None:
        with self._read_session() as session:
            row = session.get(GenerationAttempt, attempt_id)
            return (
                GenerationAttempt(
                    attempt_id=row.attempt_id,
                    case_id=row.case_id,
                    case_version=row.case_version,
                    status=row.status,
                    stage=row.stage,
                    progress=row.progress,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                if row is not None
                else None
            )

    # ------------------------------------------------------------------ #
    # published immutable versions
    # ------------------------------------------------------------------ #

    def insert_published(
        self,
        *,
        case_id: str,
        case_version: int,
        payload_json: str,
        published_at: float,
    ) -> PublishedVersion:
        """INSERT-only. A duplicate (case_id, version) rolls back and raises
        ``DuplicatePublication`` — the row is immutable and never updated."""
        with self.transaction() as session:
            row = PublishedVersion(
                case_id=case_id,
                case_version=case_version,
                payload_json=payload_json,
                published_at=published_at,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise DuplicatePublication(
                    f"published version ({case_id!r}, v{case_version}) already exists"
                ) from None
            return PublishedVersion(
                case_id=row.case_id,
                case_version=row.case_version,
                payload_json=row.payload_json,
                published_at=row.published_at,
            )

    def get_published(self, case_id: str, case_version: int) -> PublishedVersion | None:
        """EXACT version lookup on the immutable payload — never 'latest'."""
        with self._read_session() as session:
            row = session.get(PublishedVersion, (case_id, case_version))
            return (
                PublishedVersion(
                    case_id=row.case_id,
                    case_version=row.case_version,
                    payload_json=row.payload_json,
                    published_at=row.published_at,
                )
                if row is not None
                else None
            )

    def get_latest_published(self, case_id: str) -> PublishedVersion | None:
        """Highest published version for a case (GET /cases default only)."""
        with self._read_session() as session:
            row = session.execute(
                select(PublishedVersion)
                .where(PublishedVersion.case_id == case_id)
                .order_by(PublishedVersion.case_version.desc())
                .limit(1)
            ).scalar_one_or_none()
            return (
                PublishedVersion(
                    case_id=row.case_id,
                    case_version=row.case_version,
                    payload_json=row.payload_json,
                    published_at=row.published_at,
                )
                if row is not None
                else None
            )

    # ------------------------------------------------------------------ #
    # creator credentials
    # ------------------------------------------------------------------ #

    def create_creator_credential(
        self,
        *,
        case_id: str,
        token_verifier: str,
        created_at: float,
        expires_at: float,
    ) -> CreatorCredential:
        """Persist the verifier of one creatorAccessToken (token never stored)."""
        with self.transaction() as session:
            row = CreatorCredential(
                case_id=case_id,
                token_verifier=token_verifier,
                created_at=created_at,
                expires_at=expires_at,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise DuplicateCredential(
                    f"a creator credential for case {case_id!r} already exists"
                ) from None
            return CreatorCredential(
                id=row.id,
                case_id=row.case_id,
                token_verifier=row.token_verifier,
                created_at=row.created_at,
                expires_at=row.expires_at,
            )

    def get_creator_credential_by_verifier(
        self, token_verifier: str
    ) -> CreatorCredential | None:
        with self._read_session() as session:
            row = session.execute(
                select(CreatorCredential).where(
                    CreatorCredential.token_verifier == token_verifier
                )
            ).scalar_one_or_none()
            return (
                CreatorCredential(
                    id=row.id,
                    case_id=row.case_id,
                    token_verifier=row.token_verifier,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                )
                if row is not None
                else None
            )

    # ------------------------------------------------------------------ #
    # playthroughs
    # ------------------------------------------------------------------ #

    def create_playthrough(
        self,
        *,
        playthrough_id: str,
        case_id: str,
        case_version: int,
        token_verifier: str,
        state: str,
        created_at: float,
        expires_at: float,
    ) -> Playthrough:
        """Create one pinned playthrough row. Raises ``DuplicatePlaythrough``."""
        with self.transaction() as session:
            row = Playthrough(
                playthrough_id=playthrough_id,
                case_id=case_id,
                case_version=case_version,
                token_verifier=token_verifier,
                state=state,
                created_at=created_at,
                expires_at=expires_at,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise DuplicatePlaythrough(
                    f"playthrough {playthrough_id!r} already exists"
                ) from None
            return Playthrough(
                playthrough_id=row.playthrough_id,
                case_id=row.case_id,
                case_version=row.case_version,
                token_verifier=row.token_verifier,
                state=row.state,
                created_at=row.created_at,
                expires_at=row.expires_at,
            )

    def create_playthrough_if_published(
        self,
        *,
        playthrough_id: str,
        case_id: str,
        case_version: int,
        token_verifier: str,
        state: str,
        created_at: float,
        expires_at: float,
    ) -> Playthrough:
        """Atomically resolve the EXACT version and pin a new playthrough.

        Inside ONE transaction: the (case_id, case_version) row must exist
        (else ``VersionNotFoundError``) and be PUBLISHED (else
        ``VersionNotPublishedError``) before the playthrough row is inserted.
        Because PUBLISHED is terminal, this closes the TOCTOU gap during
        playthrough creation (Phase5 G/N): the pin can never reference a
        version that becomes not-published, and resolution NEVER follows
        "latest".
        """
        with self.transaction() as session:
            version_row = session.get(CaseVersion, (case_id, case_version))
            if version_row is None:
                raise VersionNotFoundError(
                    f"case version ({case_id!r}, v{case_version}) does not exist"
                )
            if version_row.state != "PUBLISHED":
                raise VersionNotPublishedError(
                    f"case version ({case_id!r}, v{case_version}) is not published"
                )
            row = Playthrough(
                playthrough_id=playthrough_id,
                case_id=case_id,
                case_version=case_version,
                token_verifier=token_verifier,
                state=state,
                created_at=created_at,
                expires_at=expires_at,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise DuplicatePlaythrough(
                    f"playthrough {playthrough_id!r} already exists"
                ) from None
            return Playthrough(
                playthrough_id=row.playthrough_id,
                case_id=row.case_id,
                case_version=row.case_version,
                token_verifier=row.token_verifier,
                state=row.state,
                created_at=row.created_at,
                expires_at=row.expires_at,
            )

    def get_playthrough_by_id(self, playthrough_id: str) -> Playthrough | None:
        with self._read_session() as session:
            row = session.get(Playthrough, playthrough_id)
            return (
                Playthrough(
                    playthrough_id=row.playthrough_id,
                    case_id=row.case_id,
                    case_version=row.case_version,
                    token_verifier=row.token_verifier,
                    state=row.state,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                )
                if row is not None
                else None
            )

    def get_playthrough_by_verifier(self, token_verifier: str) -> Playthrough | None:
        with self._read_session() as session:
            row = session.execute(
                select(Playthrough).where(
                    Playthrough.token_verifier == token_verifier
                )
            ).scalar_one_or_none()
            return (
                Playthrough(
                    playthrough_id=row.playthrough_id,
                    case_id=row.case_id,
                    case_version=row.case_version,
                    token_verifier=row.token_verifier,
                    state=row.state,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                )
                if row is not None
                else None
            )

    def get_playthrough_state(self, playthrough_id: str) -> str | None:
        """The playthrough's persisted lifecycle state (Phase7 A; None when the
        playthrough does not exist)."""
        with self._read_session() as session:
            row = session.get(Playthrough, playthrough_id)
            return row.state if row is not None else None

    # ------------------------------------------------------------------ #
    # accusations (Phase 7) — the FIRST authoritative accusation of one PT
    # ------------------------------------------------------------------ #

    def insert_accusation_if_unaccused(
        self,
        *,
        playthrough_id: str,
        case_id: str,
        case_version: int,
        murderer_id: str,
        motive_id: str,
        weapon_id: str,
        crime_time: str,
        created_at: float,
    ) -> Accusation:
        """Persist the first authoritative accusation ATOMICALLY (Phase7 C/I).

        ONE transaction performs:
        (1) ``UPDATE playthroughs SET state = 'ACCUSED' WHERE playthrough_id
            = :pid AND state IN ('CREATED','PLAYING')`` — the compare-and-set
            guard (REQUIREMENTS 40.11; only pre-accusation playthroughs may
            transition to ACCUSED);
        (2) the INSERT of the immutable ``accusations`` row.

        When the UPDATE affects 0 rows the concurrent/duplicate accusation
        attempt LOST (the playthrough already moved to ACCUSED/REVEALED): the
        WHOLE transaction rolls back — no row, no state change — and
        ``DuplicateAccusation`` propagates (the API answers the frozen
        ``409 CASE_ALREADY_SUBMITTED``). A failure of any kind inside the
        transaction leaves no half-ACCUSED state (Phase7 I/N27).
        """
        with self.transaction() as session:
            result = session.execute(
                text(
                    "UPDATE playthroughs SET state = 'ACCUSED' "
                    "WHERE playthrough_id = :pid "
                    "AND state IN ('CREATED', 'PLAYING')"
                ),
                {"pid": playthrough_id},
            )
            if result.rowcount == 0:
                raise DuplicateAccusation(
                    f"playthrough {playthrough_id!r} already has an accusation"
                )
            row = Accusation(
                playthrough_id=playthrough_id,
                case_id=case_id,
                case_version=int(case_version),
                murderer_id=murderer_id,
                motive_id=motive_id,
                weapon_id=weapon_id,
                crime_time=crime_time,
                created_at=float(created_at),
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:  # pragma: no cover - CAS guards this first
                raise DuplicateAccusation(
                    f"playthrough {playthrough_id!r} already has an accusation"
                ) from None
            return _copy_accusation(row)

    def get_accusation(self, playthrough_id: str) -> Accusation | None:
        """The immutable accusation row of one playthrough (or None).

        The row is INSERT-only by construction: this store exposes no update
        path, and the migration's BEFORE UPDATE/DELETE triggers abort raw SQL
        mutations at the database level (Phase7 H/N4)."""
        with self._read_session() as session:
            row = session.get(Accusation, playthrough_id)
            return _copy_accusation(row) if row is not None else None

    def mark_playthrough_revealed(self, playthrough_id: str) -> bool:
        """CAS reveal transition: ``ACCUSED -> REVEALED`` (Phase7 E).

        Idempotent from REVEALED (the WHERE set is ``('ACCUSED','REVEALED')``:
        a repeat reveal on an already-REVEALED playthrough still matches and
        stays REVEALED). Returns False when the playthrough is NOT reveal-
        eligible (missing/CREATED/PLAYING -> the API answers
        ``403 REVEAL_NOT_AVAILABLE``)."""
        with self.transaction() as session:
            result = session.execute(
                text(
                    "UPDATE playthroughs SET state = 'REVEALED' "
                    "WHERE playthrough_id = :pid "
                    "AND state IN ('ACCUSED', 'REVEALED')"
                ),
                {"pid": playthrough_id},
            )
            return result.rowcount > 0

    def get_accusation_and_mark_revealed(self, playthrough_id: str) -> Accusation | None:
        """Reveal's atomic unit of work (Phase7 E): read the accusation row AND
        persist the ACCUSED -> REVEALED transition in ONE transaction.

        Returns the immutable accusation row, or None when the playthrough is
        not reveal-eligible (missing row or non-revealable state -> the API
        answers ``403 REVEAL_NOT_AVAILABLE``). Concurrent reveal calls are
        idempotent: each transaction reads the SAME immutable accusation and
        the CAS leaves the state REVEALED.
        """
        with self.transaction() as session:
            row = session.get(Accusation, playthrough_id)
            if row is None:
                return None
            result = session.execute(
                text(
                    "UPDATE playthroughs SET state = 'REVEALED' "
                    "WHERE playthrough_id = :pid "
                    "AND state IN ('ACCUSED', 'REVEALED')"
                ),
                {"pid": playthrough_id},
            )
            if result.rowcount == 0:
                return None
            return _copy_accusation(row)

    # ------------------------------------------------------------------ #
    # PlayerKnowledge (Phase 6 A) — the ONLY state of one playthrough
    # ------------------------------------------------------------------ #

    def get_or_create_player_knowledge(
        self,
        playthrough_id: str,
        case_id: str,
        case_version: int,
        *,
        at: float | None = None,
    ) -> PlayerKnowledge:
        """Return the playthrough's knowledge row, creating it on first access
        with EMPTY sets.

        The ``(case_id, case_version)`` values are taken from the PINNED
        ``playthroughs`` row ONLY: when the caller's tuple does not match the
        row, or the playthrough row does not exist, the transaction rolls back
        and ``PlayerKnowledgeError`` is raised (a knowledge row can never be
        bound to a different case/version than its playthrough).
        """
        now = float(at) if at is not None else time.time()
        with self.transaction() as session:
            row = session.get(PlayerKnowledge, playthrough_id)
            if row is None:
                pt_row = session.get(Playthrough, playthrough_id)
                if pt_row is None:
                    raise PlayerKnowledgeError(
                        "unknown playthrough for player knowledge"
                    )
                if pt_row.case_id != case_id or pt_row.case_version != int(
                    case_version
                ):
                    raise PlayerKnowledgeError(
                        "player knowledge case/version does not match the "
                        "pinned playthrough"
                    )
                row = PlayerKnowledge(
                    playthrough_id=playthrough_id,
                    case_id=pt_row.case_id,
                    case_version=pt_row.case_version,
                    discovered_json="[]",
                    read_json="[]",
                    visited_json="[]",
                    notes_json="{}",
                    updated_at=now,
                )
                session.add(row)
                try:
                    session.flush()
                except IntegrityError:
                    raise PlayerKnowledgeError(
                        "could not create player knowledge row"
                    ) from None
            else:
                # Defense: an existing row must stay bound to its playthrough.
                pt_row = session.get(Playthrough, playthrough_id)
                if pt_row is None or (
                    pt_row.case_id != row.case_id
                    or pt_row.case_version != row.case_version
                ):
                    raise PlayerKnowledgeError(
                        "player knowledge row is inconsistent with its playthrough"
                    )
            return _copy_player_knowledge(row)

    def mark_discovered(
        self,
        playthrough_id: str,
        evidence_id: str,
        location_id: str | None = None,
        *,
        at: float | None = None,
    ) -> None:
        """Idempotent set-semantics marker: add ``evidence_id`` to the
        discovered set and (when given) ``location_id`` to the visited set.

        A missing knowledge row raises ``PlayerKnowledgeError`` (callers use
        ``get_or_create_player_knowledge`` first). Membership is a SET: a
        duplicate marker changes nothing and never creates a duplicate entry.
        """
        now = float(at) if at is not None else time.time()
        with self.transaction() as session:
            row = session.get(PlayerKnowledge, playthrough_id)
            if row is None:
                raise PlayerKnowledgeError(
                    "cannot mark discovery without a player knowledge row"
                )
            discovered = _knowledge_json_load_set(row.discovered_json)
            visited = _knowledge_json_load_set(row.visited_json)
            changed = False
            if evidence_id not in discovered:
                discovered.add(evidence_id)
                row.discovered_json = _knowledge_json_dump_set(discovered)
                changed = True
            if location_id is not None and location_id not in visited:
                visited.add(location_id)
                row.visited_json = _knowledge_json_dump_set(visited)
                changed = True
            if changed:
                row.updated_at = now

    def mark_visited(
        self,
        playthrough_id: str,
        location_id: str,
        *,
        at: float | None = None,
    ) -> None:
        """Idempotent marker: add ``location_id`` to the visited set."""
        now = float(at) if at is not None else time.time()
        with self.transaction() as session:
            row = session.get(PlayerKnowledge, playthrough_id)
            if row is None:
                raise PlayerKnowledgeError(
                    "cannot mark a visit without a player knowledge row"
                )
            visited = _knowledge_json_load_set(row.visited_json)
            if location_id not in visited:
                visited.add(location_id)
                row.visited_json = _knowledge_json_dump_set(visited)
                row.updated_at = now

    def mark_read(
        self,
        playthrough_id: str,
        evidence_id: str,
        *,
        at: float | None = None,
        opened_at: float | None = None,
    ) -> None:
        """Idempotent marker: add ``evidence_id`` to the read set.

        The FIRST read records ``opened_at`` (an epoch float defaulting to the
        current wall time when not given) in the row's ``notes_json`` under
        ``opened_at[evidence_id]``; repeat reads never change it, so repeat
        reads return byte-identical read DTOs.
        """
        now = float(at) if at is not None else time.time()
        opened = now if opened_at is None else float(opened_at)
        with self.transaction() as session:
            row = session.get(PlayerKnowledge, playthrough_id)
            if row is None:
                raise PlayerKnowledgeError(
                    "cannot mark a read without a player knowledge row"
                )
            read_set = _knowledge_json_load_set(row.read_json)
            changed = False
            if evidence_id not in read_set:
                read_set.add(evidence_id)
                row.read_json = _knowledge_json_dump_set(read_set)
                changed = True
                # Record the STABLE first-open time before persisting.
                notes = _knowledge_json_load_map(row.notes_json)
                opened_map = notes.get("opened_at")
                if not isinstance(opened_map, dict):
                    opened_map = {}
                    notes["opened_at"] = opened_map
                if evidence_id not in opened_map:
                    opened_map[evidence_id] = float(opened)
                    changed = True
                row.notes_json = _knowledge_json_dump_map(notes)
            if changed:
                row.updated_at = now

    def snapshot_player_knowledge(self, playthrough_id: str) -> PlayerKnowledgeSnapshot:
        """Frozen sorted snapshot of the playthrough's knowledge row.

        Returns an empty snapshot when no row exists yet (a fresh playthrough
        has empty PlayerKnowledge by definition).
        """
        with self._read_session() as session:
            row = session.get(PlayerKnowledge, playthrough_id)
            if row is None:
                return PlayerKnowledgeSnapshot()
            notes = _knowledge_json_load_map(row.notes_json)
            opened_map = notes.get("opened_at")
            opened: dict[str, float] = {}
            if isinstance(opened_map, dict):
                for key, value in opened_map.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        opened[str(key)] = float(value)
            return PlayerKnowledgeSnapshot(
                discovered=tuple(sorted(_knowledge_json_load_set(row.discovered_json))),
                read=tuple(sorted(_knowledge_json_load_set(row.read_json))),
                visited=tuple(sorted(_knowledge_json_load_set(row.visited_json))),
                opened_at=opened,
                updated_at=row.updated_at,
            )


def _copy_player_knowledge(row: PlayerKnowledge) -> PlayerKnowledge:
    """Detached plain copy of an ORM knowledge row (never the ORM instance)."""
    return PlayerKnowledge(
        playthrough_id=row.playthrough_id,
        case_id=row.case_id,
        case_version=row.case_version,
        discovered_json=row.discovered_json,
        read_json=row.read_json,
        visited_json=row.visited_json,
        notes_json=row.notes_json,
        updated_at=row.updated_at,
    )


def _copy_accusation(row: Accusation) -> Accusation:
    """Detached plain copy of an ORM accusation row (never the ORM instance).

    The copy is the immutable value the service/API may read; mutating it can
    never affect the persisted row (Phase7 H/N4: no update path exists)."""
    return Accusation(
        playthrough_id=row.playthrough_id,
        case_id=row.case_id,
        case_version=row.case_version,
        murderer_id=row.murderer_id,
        motive_id=row.motive_id,
        weapon_id=row.weapon_id,
        crime_time=row.crime_time,
        created_at=row.created_at,
    )


# Convenience aliases used by the auth layer and services.
AuthenticationError = StoreError  # auth deps translate store misses into 401/404