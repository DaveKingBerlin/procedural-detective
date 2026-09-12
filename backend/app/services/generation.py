"""Generation service (Phase5 E) — durable, creator-private case generation.

The service owns the lifecycle seam between the (immutable) Phase 4
deterministic pipeline and the durable Phase 5 persistence model:

- `start_case_generation` builds a FRESH per-request `GenerationController`
  (isolated lifetime preserving Phase 4 CAS semantics) over a SHARED
  durable admission controller and a provider factory. Admission runs BEFORE
  any provider call (REQUIREMENTS 32.10); a denial raises `AdmissionDenied`
  with zero provider calls.
- After the synchronous run reaches its durable terminal state the case /
  case-version / attempt / creator-credential rows are persisted in ONE
  transaction, and — only for PUBLISHED attempts — the frozen payload is
  stored by the atomic `PublicationService.publish_transactionally`.
- Quota rehydration: sessions persisted before a restart are re-registered
  into the in-memory admission controller (from the DB row) so they are
  still admitted with their persisted window/count; the durable
  `generations_count` is synced back after every admitted run.
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

from app.auth.tokens import (
    issue_anonymous_session_token,
    issue_creator_access_token,
    verifier as token_verifier,
)
from app.generation.admission import AdmissionDenied
from app.generation.controller import GenerationController
from app.generation.fake_provider import FakeProvider
from app.generation.ids import IdSource
from app.generation.live_provider import LiveHttpProvider
from app.generation.pipeline import STAGE_ORDER, normalize_prompt
from app.generation.prompt import PromptError
from app.generation.provider import GenerationStage, Provider
from app.generation.state_machine import GenerationState
from app.models.cases import Case, CaseVersion
from app.models.credentials import CreatorCredential
from app.models.generation import GenerationAttempt
from app.persistence.store import DuplicatePublication, Store
from app.persistence.timebase import EpochClock
from app.services.admission import DurableAdmissionController
from app.services.publication import (
    PublicationService,
    derive_title_from_prompt,
    public_case_dict_from_payload,
)

# Terminal/sanitized progress snapshots (REQUIREMENTS 40.3 progress).
_STATE_PROGRESS = {
    "DRAFT": 5,
    "GENERATING": 25,
    "REPAIRING": 60,
    "VALIDATING": 80,
    "PUBLISHED": 100,
    "FAILED": 100,
}
_STAGE_PROGRESS = {
    GenerationStage.CASE_TRUTH: 25,
    GenerationStage.PUBLIC_WORLD: 45,
    GenerationStage.EVIDENCE: 65,
    GenerationStage.WORLD_GRAPH: 85,
}
# A PUBLISHED attempt is placed in the transaction as VALIDATING and flipped to
# PUBLISHED only when the frozen payload is durably stored (never a
# "PUBLISHED without payload" state, Phase5 G).
_PRE_PUBLISH_STATE = GenerationState.VALIDATING.value
_PRE_PUBLISH_STATUS = GenerationState.VALIDATING.value
_PRE_PUBLISH_PROGRESS = _STATE_PROGRESS[GenerationState.VALIDATING.value]
_PRE_PUBLISH_STAGE = "validating"


class GenerationServiceError(Exception):
    """Base class for service-level domain errors."""


class ProviderConfigError(GenerationServiceError):
    """Provider selection/configuration error (fail-fast at construction)."""


class IdentifierConflict(GenerationServiceError):
    """A duplicate/colliding identifier could not be persisted (-> 409)."""


class AdmissionDeniedError(GenerationServiceError):
    """Admission rejected BEFORE any provider call (-> 429 ADMISSION_DENIED).

    Raised by the API-facing layer instead of the Phase 4
    ``app.generation.admission.AdmissionDenied`` so ``app.api`` never imports
    generation material (boundary contract in test_boundaries.py).
    """


class PromptValidationError(GenerationServiceError):
    """The prompt violates the generation input bounds (-> 422 PROMPT_ERROR)."""


class UnknownCaseError(GenerationServiceError):
    """start_case_version on a case that does not exist (-> 404)."""


@dataclass(frozen=True)
class CreatedAnonymousSession:
    """Outcome of ``POST /sessions/anonymous``."""

    anonymous_session_token: str
    anonymous_quota_session_id: str
    quota_window_end: float


@dataclass(frozen=True)
class CaseStarted:
    """Outcome of ``POST /cases`` (creator token appears ONLY here)."""

    case_id: str
    generation_id: str
    generation_attempt_id: str
    creator_access_token: str
    status: str


class _LockedIdSource:
    """Thread-safe facade over a shared ``IdSource`` (unique across requests)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._lock = threading.Lock()

    def generation_attempt_id(self) -> str:
        with self._lock:
            return self._inner.generation_attempt_id()

    def case_id(self) -> str:
        with self._lock:
            return self._inner.case_id()

    def session_id(self) -> str:
        with self._lock:
            return self._inner.session_id()


class _CasePinnedIdSource:
    """Ids facade that pins ``case_id`` to an existing case's id.

    A fresh Phase 4 controller allocates its OWN case id per attempt; when a
    generation targets an EXISTING case (phase-5 ``start_case_version``), the
    controller must keep using the real case id so the frozen payload, the
    case-version rows and the publication all address the SAME case.
    Attempt/session ids still come from the shared (locked) service source.
    """

    def __init__(self, inner: Any, case_id: str) -> None:
        self._inner = inner
        self._case_id = case_id

    def case_id(self) -> str:
        return self._case_id

    def generation_attempt_id(self) -> str:
        return self._inner.generation_attempt_id()

    def session_id(self) -> str:
        return self._inner.session_id()


class OpaqueIdSource:
    """Restart-safe server-owned identity source (Phase5 ids).

    Case/attempt/session ids are collision-safe by randomness (>= 72 bits per
    id family) instead of per-process counters, so a fresh service over an
    existing database NEVER collides with rows persisted by an earlier
    process. The database primary keys remain the real guard (the store
    translates an IntegrityError into a clean 409); the random ids are opaque
    and unguessable, which also hardens IDOR probing (knowing an id never
    grants access — authorization is token-based).

    The per-case VERSION numbers remain monotonic via ``cases.next_version``
    (atomic UPDATE ... RETURNING), which is what REQUIREMENTS 7.1/Phase5 B
    requires; this class only names the opaque case/attempt/session ids.
    """

    def generation_attempt_id(self) -> str:
        return f"GA-{secrets.token_urlsafe(9)}"

    def case_id(self) -> str:
        return f"CASE-{secrets.token_urlsafe(9)}"

    def session_id(self) -> str:
        return f"QUOTA-{secrets.token_urlsafe(9)}"


def _sanitized_progress(record: Any) -> tuple[str, int]:
    """Sanitized (stage, progress) snapshot for a finished attempt."""
    state = record.state
    if state is GenerationState.PUBLISHED:
        return "published", 100
    if state is GenerationState.FAILED:
        return "failed", 100
    last_stage: GenerationStage | None = None
    for stage in STAGE_ORDER:
        if stage in record.stage_done:
            last_stage = stage
    if last_stage is not None:
        return last_stage.value, _STAGE_PROGRESS.get(last_stage, 25)
    return state.value.lower(), _STATE_PROGRESS.get(state.value, 0)


def _pre_publish_snapshot(record: Any) -> tuple[str, str, str, int, str | None]:
    """(state, status, stage, progress, reason) durable snapshot for placement.

    A PUBLISHED attempt is placed as VALIDATING and flipped to PUBLISHED only
    when the frozen payload is durably stored (never a "PUBLISHED without
    payload" state — Phase5 G). FAILED attempts are terminal.
    """
    if record.state is GenerationState.PUBLISHED:
        return (
            _PRE_PUBLISH_STATE,
            _PRE_PUBLISH_STATUS,
            _PRE_PUBLISH_STAGE,
            _PRE_PUBLISH_PROGRESS,
            None,
        )
    pre_stage, pre_progress = _sanitized_progress(record)
    return (
        record.state.value,
        record.state.value,
        pre_stage,
        pre_progress,
        record.reason if record.state is GenerationState.FAILED else None,
    )


class GenerationService:
    """Durable case generation + session/quota orchestration."""

    def __init__(
        self,
        *,
        settings: Any,
        store: Store,
        clock: Any = None,
        admission: DurableAdmissionController | None = None,
        provider_factory: Callable[[], Provider] | None = None,
        ids: IdSource | None = None,
        publication: PublicationService | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._clock = clock if clock is not None else EpochClock()
        self._ids = _LockedIdSource(ids if ids is not None else OpaqueIdSource())
        if admission is None:
            admission = DurableAdmissionController(
                clock=self._clock,
                ids=OpaqueIdSource(),  # separate session-id namespace
                max_concurrent_generations=settings.max_concurrent_generations,
                max_concurrent_generations_global=(
                    settings.max_concurrent_generations_global
                ),
                max_generations_per_session_per_window=(
                    settings.max_generations_per_session_per_window
                ),
                max_generations_global_per_window=(
                    settings.max_generations_global_per_window
                ),
                anonymous_quota_session_ttl_seconds=(
                    settings.anonymous_quota_session_ttl_seconds
                ),
            )
        self._admission = admission
        self._provider_factory = (
            provider_factory if provider_factory is not None else self._build_default_provider_factory()
        )
        self._publication = (
            publication if publication is not None else PublicationService(store)
        )
        # NOTE: no startup database scan. Rehydration is LAZY and happens per
        # token use in ``_ensure_admission_ready`` (Phase5 E step 6: "the API
        # layer rehydrates/syncs the in-memory AdmissionController from DB on
        # token use"). A startup scan would open a connection and create the
        # database file on import, which must never happen (health/readiness
        # apps must boot without touching the DB).

    # ------------------------------------------------------------------ #
    # sessions
    # ------------------------------------------------------------------ #

    def create_anonymous_quota_session(self) -> CreatedAnonymousSession:
        """Create one durable quota session (DB row + admission registration)."""
        session = self._admission.create_anonymous_quota_session()
        token = issue_anonymous_session_token()
        self._store.create_session(
            session_id=session.session_id,
            token_verifier=token_verifier(token),
            quota_window_end=session.quota_window_end,
            created_at=session.created_at,
            generations_count=0,
        )
        return CreatedAnonymousSession(
            anonymous_session_token=token,
            anonymous_quota_session_id=session.session_id,
            quota_window_end=session.quota_window_end,
        )

    # ------------------------------------------------------------------ #
    # generation
    # ------------------------------------------------------------------ #

    def start_case_generation(
        self,
        prompt_text: str,
        *,
        anonymous_quota_session_id: str,
        creator_token: str | None = None,
        difficulty: str | None = None,
    ) -> CaseStarted:
        """Run one private case generation durably (version 1).

        - admission is checked inside the fresh controller BEFORE any provider
          call (``AdmissionDenied`` propagates; zero provider calls),
        - the case / case-version / attempt / creator-credential rows are
          persisted atomically AFTER the synchronous run,
        - a PUBLISHED result is persisted by the atomic publication
          transaction (frozen payload + state flips),
        - the durable session generation counter is synced back.
        """
        settings = self._settings
        # Local prompt validation: zero reservations, zero provider calls.
        try:
            normalize_prompt(prompt_text, max_chars=settings.max_prompt_chars)
        except PromptError:
            raise PromptValidationError(
                "prompt is invalid or exceeds the configured limit"
            ) from None
        session_row = self._store.get_session(anonymous_quota_session_id)
        if session_row is None:
            raise AdmissionDeniedError("unknown anonymous quota session")
        self._ensure_admission_ready(session_row)

        handle, record, now = self._run_generation(
            prompt_text,
            anonymous_quota_session_id=anonymous_quota_session_id,
            creator_token=creator_token,
        )
        case_id = handle.case_id
        attempt_id = handle.attempt_id
        version = 1
        generation_id = f"GEN-{version}"
        creator_token_value = issue_creator_access_token()
        title = derive_title_from_prompt(record.prompt or prompt_text)
        pre_state, pre_status, pre_stage, pre_progress, reason = _pre_publish_snapshot(
            record
        )

        self._persist_creation(
            case_id=case_id,
            quota_session_id=anonymous_quota_session_id,
            version=version,
            title=title,
            difficulty=difficulty,
            state=pre_state,
            state_reason=reason,
            generation_id=generation_id,
            attempt_id=attempt_id,
            attempt_status=pre_status,
            attempt_stage=pre_stage,
            attempt_progress=pre_progress,
            creator_verifier=token_verifier(creator_token_value),
            expires_at=now + settings.creator_token_ttl_seconds,
            created_at=now,
        )
        status = self._publish_if_ready(record, title, settings, now, case_id, version) or record.state.value
        self._sync_generations(anonymous_quota_session_id)
        return CaseStarted(
            case_id=case_id,
            generation_id=generation_id,
            generation_attempt_id=attempt_id,
            creator_access_token=creator_token_value,
            status=status,
        )

    def start_case_version(
        self,
        case_id: str,
        prompt_text: str,
        *,
        anonymous_quota_session_id: str,
        creator_token: str | None = None,
        difficulty: str | None = None,
    ) -> CaseStarted:
        """Generate the NEXT CaseVersion of an existing case (Phase5 B).

        ``cases.next_version`` is advanced atomically
        (``UPDATE ... RETURNING``); generation_id is the per-case monotonic
        public label (GEN-2, GEN-3, ...). An existing PUBLISHED version is
        never touched: the new version is a NEW case_version + NEW published
        row (Phase5 G — publishing v2 never alters v1 playthroughs).
        """
        settings = self._settings
        try:
            normalize_prompt(prompt_text, max_chars=settings.max_prompt_chars)
        except PromptError:
            raise PromptValidationError(
                "prompt is invalid or exceeds the configured limit"
            ) from None
        if self._store.get_case(case_id) is None:
            raise UnknownCaseError(f"case {case_id!r} does not exist")
        session_row = self._store.get_session(anonymous_quota_session_id)
        if session_row is None:
            raise AdmissionDeniedError("unknown anonymous quota session")
        self._ensure_admission_ready(session_row)

        version = self._store.allocate_version(case_id)
        generation_id = f"GEN-{version}"
        handle, record, now = self._run_generation(
            prompt_text,
            anonymous_quota_session_id=anonymous_quota_session_id,
            creator_token=creator_token,
            ids=_CasePinnedIdSource(self._ids, case_id),
        )
        attempt_id = handle.attempt_id
        creator_token_value = issue_creator_access_token()
        title = derive_title_from_prompt(record.prompt or prompt_text)
        pre_state, pre_status, pre_stage, pre_progress, reason = _pre_publish_snapshot(
            record
        )
        self._persist_creation(
            case_id=case_id,
            quota_session_id=anonymous_quota_session_id,
            version=version,
            title=title,
            difficulty=difficulty,
            state=pre_state,
            state_reason=reason,
            generation_id=generation_id,
            attempt_id=attempt_id,
            attempt_status=pre_status,
            attempt_stage=pre_stage,
            attempt_progress=pre_progress,
            creator_verifier=token_verifier(creator_token_value),
            expires_at=now + settings.creator_token_ttl_seconds,
            created_at=now,
            create_case_row=False,
        )
        status = self._publish_if_ready(record, title, settings, now, case_id, version) or record.state.value
        self._sync_generations(anonymous_quota_session_id)
        return CaseStarted(
            case_id=case_id,
            generation_id=generation_id,
            generation_attempt_id=attempt_id,
            creator_access_token=creator_token_value,
            status=status,
        )

    # -- shared run/persist machinery ------------------------------------ #

    def _run_generation(
        self,
        prompt_text: str,
        *,
        anonymous_quota_session_id: str,
        creator_token: str | None,
        ids: Any | None = None,
    ) -> tuple[Any, Any, float]:
        """Fresh per-request controller; admission inside; synchronous run."""
        settings = self._settings
        controller = GenerationController(
            provider=self._provider_factory(),
            admission=self._admission,
            clock=self._clock,
            ids=ids if ids is not None else self._ids,
            deadline_seconds=settings.generation_deadline_seconds,
            max_llm_calls_per_generation=settings.max_llm_calls_per_generation,
            max_repair_passes=settings.max_repair_passes,
            max_full_regenerations=settings.max_full_regenerations,
            max_prompt_chars=settings.max_prompt_chars,
            seed=None,  # per-controller auto seed (deterministic per controller)
        )
        try:
            handle = controller.start_generation(
                prompt_text,
                anonymous_quota_session_id=anonymous_quota_session_id,
                creator_token=creator_token,
            )
        except AdmissionDenied:
            # Translate the Phase 4 admission denial into the API-facing
            # service error (the API layer must never import generation).
            raise AdmissionDeniedError("generation admission denied") from None
        record = controller.attempt(handle.attempt_id)
        if record is None:  # pragma: no cover - defensive
            raise GenerationServiceError("generation attempt record unavailable")
        return handle, record, float(self._clock.now())

    def _publish_if_ready(
        self,
        record: Any,
        title: str,
        settings: Any,
        now: float,
        case_id: str,
        version: int,
    ) -> str | None:
        """Atomic publication when the run reached PUBLISHED.

        Returns the final status when published (PUBLISHED); None otherwise.
        """
        if record.state is not GenerationState.PUBLISHED:
            return None
        published = record.published
        if published is None:  # pragma: no cover - controller invariant
            raise GenerationServiceError("published aggregate unavailable")
        if published.case_version != int(version):
            # Phase 4 builds the frozen aggregate with case_version = 1 (the
            # MVP constant). Phase 5 publishes PER-CASE VERSIONS; the frozen
            # top-level version is patched so the immutable published_versions
            # row (case_id, version) and the DTO serialization agree. The
            # hidden truth/public sections keep the Phase 4 v1 constant (no
            # truth is served in Phase 5; phase 6 reveal reads the truth row
            # directly). Documented deviation — no Phase 3/4 file changes.
            try:
                object.__setattr__(published, "case_version", int(version))
            except (AttributeError, TypeError) as exc:
                raise GenerationServiceError(
                    "cannot align the published aggregate version"
                ) from None
        self._publication.publish_transactionally(
            published,
            seed=record.seed,
            prompt=record.prompt,
            model=(
                settings.llm_model
                if settings.generation_provider == "live"
                else None
            ),
            title=title,
        )
        return GenerationState.PUBLISHED.value

    def _persist_creation(
        self,
        *,
        case_id: str,
        quota_session_id: str,
        version: int,
        title: str,
        difficulty: str | None,
        state: str,
        state_reason: str | None,
        generation_id: str,
        attempt_id: str,
        attempt_status: str,
        attempt_stage: str,
        attempt_progress: int,
        creator_verifier: str,
        expires_at: float,
        created_at: float,
        create_case_row: bool = True,
    ) -> None:
        """One atomic transaction: case? + version + attempt + credential."""
        try:
            with self._store.transaction() as session:
                if create_case_row:
                    session.add(
                        Case(
                            case_id=case_id,
                            quota_session_id=quota_session_id,
                            title=title,
                            difficulty=difficulty,
                            next_version=version + 1,
                            created_at=created_at,
                        )
                    )
                session.add(
                    CaseVersion(
                        case_id=case_id,
                        version=version,
                        state=state,
                        state_reason=state_reason,
                        generation_id=generation_id,
                        created_at=created_at,
                    )
                )
                session.add(
                    GenerationAttempt(
                        attempt_id=attempt_id,
                        case_id=case_id,
                        case_version=version,
                        status=attempt_status,
                        stage=attempt_stage,
                        progress=int(attempt_progress),
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )
                session.add(
                    CreatorCredential(
                        case_id=case_id,
                        token_verifier=creator_verifier,
                        created_at=created_at,
                        expires_at=expires_at,
                    )
                )
        except IntegrityError:
            # Duplicate/colliding identifier persisted: roll back happened
            # inside the transaction helper — no partial state remains.
            raise IdentifierConflict(
                f"duplicate identifier while persisting case {case_id!r}"
            ) from None

    # ------------------------------------------------------------------ #
    # durable reads (survive restart)
    # ------------------------------------------------------------------ #

    def get_generation_progress(
        self, generation_id: str, *, case_id: str | None = None
    ) -> dict[str, Any] | None:
        """Sanitized durable progress: {caseId, generationId, status,
        progress, stage}. NEVER includes truth/proof/prompt/diagnostics."""
        row = self._store.get_generation_attempt_by_generation_id(
            generation_id, case_id=case_id
        )
        if row is None:
            return None
        return {
            "caseId": row.case_id,
            "generationId": generation_id,
            "status": row.status,
            "progress": row.progress,
            "stage": row.stage,
        }

    def get_public_case(self, case_id: str, case_version: int) -> dict[str, Any] | None:
        """Exact published-version public DTO dict (from the frozen payload)."""
        row = self._store.get_published(case_id, case_version)
        if row is None:
            return None
        try:
            payload = json.loads(row.payload_json)
        except (ValueError, TypeError):
            return None
        return public_case_dict_from_payload(payload)

    def get_latest_public_case(self, case_id: str) -> dict[str, Any] | None:
        """Latest published-version public DTO dict (GET /cases default only)."""
        row = self._store.get_latest_published(case_id)
        if row is None:
            return None
        try:
            payload = json.loads(row.payload_json)
        except (ValueError, TypeError):
            return None
        return public_case_dict_from_payload(payload)

    # ------------------------------------------------------------------ #
    # admission helpers
    # ------------------------------------------------------------------ #

    def _ensure_admission_ready(self, session_row: Any) -> None:
        """Register a persisted session into the in-memory admission when the
        current process has not seen it (restart / multi-service safety)."""
        if not self._admission.is_known(session_row.session_id):
            self._admission.rehydrate_session(
                session_row.session_id,
                session_row.created_at,
                session_row.quota_window_end,
                session_row.generations_count,
            )

    def _sync_generations(self, session_id: str) -> None:
        """Mirror the in-memory generation counter into the durable row."""
        count = self._admission.session_generations(session_id)
        self._store.update_session_generations(session_id, count)

    # ------------------------------------------------------------------ #
    # provider factory
    # ------------------------------------------------------------------ #

    def _build_default_provider_factory(self) -> Callable[[], Provider]:
        settings = self._settings
        if settings.generation_provider == "live":
            url = settings.live_provider_url
            key = settings.llm_api_key
            model = settings.llm_model
            if not (url and key and model):
                raise ProviderConfigError(
                    "generation_provider=live requires LIVE_PROVIDER_URL, "
                    "LLM_API_KEY and LLM_MODEL"
                )

            def _live() -> Provider:
                return LiveHttpProvider(endpoint_url=url, api_key=key, model=model)

            return _live
        script = self._load_fake_script()
        if not script:
            raise ProviderConfigError("fake provider script is empty")

        def _fake() -> Provider:
            return FakeProvider(script=script)

        return _fake

    def _load_fake_script(self) -> dict[GenerationStage, list[str]]:
        """Deterministic fake script: FAKE_PROVIDER_SCRIPT JSON when configured
        (stage-name -> list of directives/strings), else the builtin dev-mode
        case shipped with the package (never imports backend/tests)."""
        configured = self._settings.fake_provider_script
        if configured is not None:
            path = Path(str(configured))
            if not path.is_absolute():
                path = Path.cwd() / path
            if not path.exists():
                raise ProviderConfigError(f"FAKE_PROVIDER_SCRIPT not found: {path}")
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ProviderConfigError(
                    f"FAKE_PROVIDER_SCRIPT is not valid JSON: {exc}"
                ) from None
        else:
            builtin = Path(__file__).resolve().parent / "dev_mode_case.json"
            try:
                raw = json.loads(builtin.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:  # pragma: no cover - shipped file
                raise ProviderConfigError(
                    f"builtin dev-mode case is unreadable: {exc}"
                ) from None
        script: dict[GenerationStage, list[str]] = {}
        for stage_name, entries in raw.items():
            try:
                stage = GenerationStage(str(stage_name))
            except ValueError:
                raise ProviderConfigError(
                    f"unknown stage {stage_name!r} in fake provider script"
                ) from None
            if not isinstance(entries, list):
                raise ProviderConfigError(
                    f"fake provider script stage {stage_name!r} must map to a list"
                )
            script[stage] = [str(entry) for entry in entries]
        return script


__all__ = [
    "AdmissionDenied",
    "AdmissionDeniedError",
    "CaseStarted",
    "CreatedAnonymousSession",
    "GenerationService",
    "GenerationServiceError",
    "IdentifierConflict",
    "PromptError",
    "PromptValidationError",
    "ProviderConfigError",
    "UnknownCaseError",
]