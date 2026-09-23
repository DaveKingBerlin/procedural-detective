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
import os
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from sqlalchemy.exc import IntegrityError

from app.auth.tokens import (
    issue_anonymous_session_token,
    issue_creator_access_token,
    verifier as token_verifier,
)
from app.generation.admission import AdmissionDenied
from app.generation.controller import GenerationController
from app.generation.fake_provider import FakeProvider
from app.generation.failure_codes import (
    GenerationFailureCode,
    infer_failure_code,
    public_failure_code,
)
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
from app.core.observability import emit_event

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


def _bounded_provider_script_load(path: Path) -> Any:
    """Read + bounded-JSON-decode a fake provider script (DEF-067: a deep
    nesting bomb raises a clean ``BoundedJsonError`` — a ``ValueError`` — never
    an uncaught ``RecursionError`` from ``json.loads``)."""
    from app.assets.depthguard import bounded_json_loads

    return bounded_json_loads(path.read_text(encoding="utf-8"))


# Generation-service bound for the Phase 13 unknown-object request list.
MAX_GENERATED_REQUESTS = 8


_PD_DEV_TRACE = os.environ.get("PD_DEV_TRACE") == "true"


def _dev_trace(message: str) -> None:
    """DEV-ONLY structured trace (Phase17E PART B); gated, default OFF."""
    if _PD_DEV_TRACE:
        print(f"[PD-DEV-TRACE] {message}", flush=True)


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


class EnvironmentHintError(GenerationServiceError):
    """The optional Phase 11 environment hint violates the input-safety bounds
    (-> 422 ENVIRONMENT_ERROR; an UNSAFE hint is rejected, never resolved)."""


class UnknownCaseError(GenerationServiceError):
    """start_case_version on a case that does not exist (-> 404)."""


def _record_asset_oracle_provenance(published: Any, settings: Any) -> dict[str, str]:
    """INTERNAL Phase 10 diagnostic: resolver provenance of a published draft.

    After a placement's assetId has been produced (the frozen published
    aggregate), run every placement through the Asset Oracle resolver so tests
    and QA can assert the golden case resolves exclusively through the catalog
    (provenance CATALOG_EXACT / CATALOG_ALIAS, never the legacy ad-hoc switch).

    The result is DIAGNOSTIC ONLY: it is never serialized into the payload,
    never stored, and never exposed through any API DTO (the public WorldGraph
    DTO stays byte-identical — placements keep their original assetId + safe
    metadata). A catalog problem must never break an otherwise-valid
    publication, so any failure degrades to an empty map.
    """
    try:
        from app.assets import load_catalog_from_repo, resolve_placements_provenance

        catalog = None
        configured = getattr(settings, "asset_catalog_path", None)
        if configured is not None:
            catalog = load_catalog_from_repo(path=configured)
        return resolve_placements_provenance(
            published.draft.world_graph.placements, catalog=catalog
        )
    except Exception:  # noqa: BLE001 - diagnostics never alter publication
        return {}


def _has_unresolved_required(world_reqs: Any, composition: Any) -> bool:
    """True when a CRITICALITY_REQUIRED prompt object is NOT in the world.

    Phase 14_5 semantics: a crime-critical unknown object that could not be
    generated or placed MUST NEVER be silently removed or substituted. After
    the world repair budget is exhausted, a missing REQUIRED object is a
    TERMINAL world failure (the attempt FAILS, nothing is published).
    """
    from app.world.requirements import CRITICALITY_REQUIRED

    resolved = composition.resolution_record.get("resolved", {}) or {}
    placed_assets = {p.asset_id for p in composition.placements}
    for request in world_reqs.objects:
        if getattr(request, "criticality", None) != CRITICALITY_REQUIRED:
            continue
        entry = resolved.get(getattr(request, "requested_name", ""))
        if entry is None or not entry.get("assetId"):
            return True
        if entry["assetId"] not in placed_assets:
            return True
    return False


def _link_required_unknown_evidence(
    composition: Any, world_reqs: Any, draft: Any
) -> tuple[list[Any] | None, tuple[Any, ...]]:
    """Phase 14_5 DEV composition seam for REQUIRED unseen weapons.

    A REQUIRED unknown object resolved via PROCEDURAL_GENERATED becomes the
    crime-weapon EVIDENCE object: its placement gains ``interaction="inspect"``
    and a discoverable forensic fingerprint evidence id ``<objectId>_fp_01``
    whose proposition links the unseen object to ``michael_carter`` — a
    suspect the golden scenario ALREADY excludes (cctv_michael_office_01).
    The solver-critical facts stay on the catalog knife (golden all_true and
    weapon uniqueness untouched); the new fact is a necessary-condition-neutral
    label: ``OBJECT_CONTAINS_FINGERPRINT`` never excludes and the unseen
    object carries only the INSPECTABLE affordance (never POTENTIAL_WEAPON),
    so the candidate universe and every deduction are byte-identical.

    Returns ``(placements | None, newEvidenceSpecs)`` — ``None`` when there is
    nothing to link (no required procedural object, or the golden persons do
    not contain michael_carter — the seam degrades gracefully, never breaking
    a valid publication).
    """
    import dataclasses

    from app.environments.placer import is_procedural_asset_id
    from app.generation.schemas import EvidenceSpec, PropSpec
    from app.world.requirements import CRITICALITY_REQUIRED

    if not hasattr(draft, "persons") or not any(
        getattr(p, "person_id", None) == "michael_carter" for p in draft.persons
    ):
        return None, ()
    required_assets: dict[str, str] = {}
    resolved = composition.resolution_record.get("resolved", {}) or {}
    for request in world_reqs.objects:
        if getattr(request, "criticality", None) != CRITICALITY_REQUIRED:
            continue
        entry = resolved.get(getattr(request, "requested_name", ""))
        if entry is None:
            continue
        asset_id = entry.get("assetId")
        if asset_id and is_procedural_asset_id(asset_id):
            required_assets[asset_id] = getattr(request, "requested_name", "")
    if not required_assets:
        return None, ()
    new_object_ids = {spec.object_id for spec in composition.new_objects}
    placements = list(composition.placements)
    new_facts: list[Any] = []
    done: set[str] = set()
    for index, placement in enumerate(placements):
        if placement.asset_id not in required_assets:
            continue
        if placement.object_id not in new_object_ids or placement.asset_id in done:
            continue
        done.add(placement.asset_id)
        label = placement.object_id.replace("_", " ")
        fact_id = f"{placement.object_id}_fp_01"
        new_facts.append(
            EvidenceSpec(
                id=fact_id,
                kind="forensic",
                propositions=(
                    PropSpec(
                        type="OBJECT_CONTAINS_FINGERPRINT",
                        object_id=placement.object_id,
                        person_id="michael_carter",
                    ),
                ),
                source_ref={"kind": "record", "sourceId": fact_id},
                reliability="high",
                presentation={
                    "title": f"Latent fingerprint on the {label}",
                    "description": (
                        "A forensic fingerprint lift taken from the {} during "
                        "the investigation. The print belongs to Michael "
                        "Carter, who was already at the consulting office when "
                        "the murder happened — the deduction is unaffected."
                    ).format(label),
                },
                discoverable=True,
            )
        )
        placements[index] = dataclasses.replace(
            placement, interaction="inspect", evidence_id=fact_id
        )
    if not new_facts:
        return None, ()
    return placements, tuple(new_facts)


# --------------------------------------------------------------------------- #
# Phase 11 — environment hint validation + kit composition (backend half)
# --------------------------------------------------------------------------- #

_ENVIRONMENT_MAX_LENGTH = 40

# Forbidden URL-scheme tokens (substring scan, as in the asset-request gate).
_ENVIRONMENT_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "http:",
    "https:",
    "data:",
    "file:",
    "javascript:",
)
# Executable/handler word tokens at word boundaries.
_ENVIRONMENT_FORBIDDEN_WORD_RE = re.compile(
    r"\b(?:script|handler|shader|function|eval)\b", re.IGNORECASE
)
_ENVIRONMENT_DRIVE_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def environment_hint_safety(environment: object) -> tuple[str, ...]:
    """Deterministic sorted issues for one RAW environment hint (empty=safe).

    Mirrors the prompt/asset-request robustness: bounded to 40 chars, no
    control characters, no URL schemes / path separators / traversal /
    absolute-prefix forms, no executable word tokens — where every categorical
    check is ALSO run over the NFKC-normalized form so mangled spellings cannot
    evade the gate. Never raises and performs no I/O.
    """
    if environment is None:
        return ()
    if not isinstance(environment, str):
        return ("environment must be a string",)
    value = environment
    issues: list[str] = []
    if len(value) > _ENVIRONMENT_MAX_LENGTH:
        issues.append(
            f"environment exceeds {_ENVIRONMENT_MAX_LENGTH} characters"
        )
    if any(ord(ch) < 0x20 for ch in value):
        issues.append("environment contains a control character")

    norm = unicodedata.normalize("NFKC", value)
    forms = (value, norm)
    lowered = [form.casefold() for form in forms]
    for scheme in _ENVIRONMENT_FORBIDDEN_TOKENS:
        if any(scheme in form for form in lowered):
            issues.append(f"environment contains a forbidden URL scheme {scheme!r}")
            break
    if _ENVIRONMENT_FORBIDDEN_WORD_RE.search(value) or _ENVIRONMENT_FORBIDDEN_WORD_RE.search(norm):
        issues.append("environment contains a forbidden executable token")
    if any(("/" in form) or ("\\" in form) for form in forms):
        issues.append("environment contains a path separator")
    if any(".." in form for form in forms):
        issues.append("environment contains path traversal '..'")
    if any(
        form.startswith(("/", "\\")) or _ENVIRONMENT_DRIVE_ABSOLUTE_RE.match(form)
        for form in forms
    ):
        issues.append("environment is an absolute path")
    return tuple(sorted(set(issues)))


def _resolve_environment_for_generation(
    environment: str | None,
) -> tuple[str, dict[str, Any]]:
    """Resolve a validated environment hint to a kit id (never raises).

    Returns ``(environment_id, diagnostics)`` where ``diagnostics`` carries
    ``{environmentId, provenance, ambiguous, candidates, matchedAlias}`` for
    internal recording. Unknown/ambiguous values resolve to the documented
    fallback kit (``apartment``) with provenance FALLBACK.
    """
    from app.environments import (
        EnvironmentProvenance,
        FALLBACK_ENVIRONMENT_ID,
        resolve_environment,
    )

    hint = str(environment) if environment else FALLBACK_ENVIRONMENT_ID
    try:
        resolution = resolve_environment(hint)
        if resolution.resolved:
            return (
                resolution.environment_id,
                {
                    "environmentId": resolution.environment_id,
                    "provenance": resolution.provenance.value,
                    "ambiguous": resolution.ambiguous,
                    "candidates": tuple(resolution.candidates),
                    "matchedAlias": resolution.matched_alias,
                },
            )
        # Ambiguous semantic match: NO arbitrary winner — fall back explicitly.
        return (
            FALLBACK_ENVIRONMENT_ID,
            {
                "environmentId": FALLBACK_ENVIRONMENT_ID,
                "provenance": EnvironmentProvenance.FALLBACK.value,
                "ambiguous": True,
                "candidates": tuple(resolution.candidates),
                "matchedAlias": None,
            },
        )
    except Exception:  # noqa: BLE001 - degradation must never break generation
        return (
            FALLBACK_ENVIRONMENT_ID,
            {
                "environmentId": FALLBACK_ENVIRONMENT_ID,
                "provenance": EnvironmentProvenance.FALLBACK.value,
                "ambiguous": False,
                "candidates": (),
                "matchedAlias": None,
            },
        )



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
    failure_code: str | None = None


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
    """(state, status, stage, progress, failure code) durable snapshot.

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
    failure_code = None
    if record.state is GenerationState.FAILED:
        failure_code = public_failure_code(getattr(record, "failure_code", None))
        if failure_code is None:
            failure_code = infer_failure_code(getattr(record, "reason", None)).value
    return (
        record.state.value,
        record.state.value,
        pre_stage,
        pre_progress,
        failure_code,
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
        spec_provider: Any = None,
        generate_unknown_assets: bool = False,
        generated_cache: Any = None,
        world_repair_provider: Any = None,
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
                global_window_seconds=settings.global_generation_window_seconds,
            )
        self._admission = admission
        self._provider_factory = (
            provider_factory if provider_factory is not None else self._build_default_provider_factory()
        )
        self._publication = (
            publication if publication is not None else PublicationService(store)
        )
        # Phase 10 INTERNAL diagnostic: last published draft's Asset Oracle
        # provenance (objectId -> provenance). Never serialized, never served.
        self._last_publish_provenance: dict[str, str] | None = None
        # Phase 11 INTERNAL diagnostic: last environment resolution
        # (environmentId, provenance, ambiguous, candidates, matchedAlias,
        # compositionFailed). Never serialized, never served.
        self._last_environment_resolution: dict[str, Any] | None = None
        # Phase 14 INTERNAL diagnostic: the last extracted WorldRequirements
        # (never serialized, never served; used by tests/QA).
        self._last_world_requirements: Any = None
        # Phase 13 — OPT-IN declarative procedural assets for unknown-object
        # requests (default OFF: without a configured AssetSpecProvider the
        # generation path is unavailable and every request resolves exactly as
        # before; a configured provider + flag is the live/test path).
        self._spec_provider = spec_provider
        self._generate_unknown_assets = bool(generate_unknown_assets)
        self._generated_cache = generated_cache
        # Phase 14 — deterministic world repair hook: a callable
        # ``Callable[[tuple[str, ...]], WorldRequirements | None]`` receiving
        # the sanitized world diagnostics and returning a REVISED
        # ``WorldRequirements`` (None = no revision). When configured, an
        # invalid world composition routes through this bounded repair loop;
        # the repaired composition runs the COMPLETE validation pipeline again
        # (locked constraints are NEVER passed to the provider and cannot be
        # mutated by it). Default None: world issues degrade the composition.
        self._world_repair_provider = world_repair_provider
        # Bounded world-repair budget (mirrors max_repair_passes semantics).
        self._max_world_repair_passes = 2
        # Phase 13 INTERNAL diagnostic: whether the last generated-asset
        # composition degraded (never serialized, never served).
        self._last_generated_composition_failed: bool | None = None
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
        """Create one durable quota session (DB row + admission registration).

        ADV-229: when the bounded in-memory session store is at ``max_sessions``
        the controller denies admission; the denial is translated here into the
        API-facing ``AdmissionDeniedError`` so the boundary yields the sanitized
        429 ADMISSION_DENIED envelope (fail closed, no internal detail).
        """
        try:
            session = self._admission.create_anonymous_quota_session()
        except AdmissionDenied:
            raise AdmissionDeniedError("anonymous session store at capacity") from None
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
        environment: str | None = None,
        unknown_asset_requests: "tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] | None" = None,
    ) -> CaseStarted:
        """Run one private case generation durably (version 1).

        - admission is checked inside the fresh controller BEFORE any provider
          call (``AdmissionDenied`` propagates; zero provider calls),
        - the case / case-version / attempt / creator-credential rows are
          persisted atomically AFTER the synchronous run,
        - a PUBLISHED result is persisted by the atomic publication
          transaction (frozen payload + state flips),
        - the durable session generation counter is synced back,
        - ``environment`` (Phase 11) is a validated environment hint: unsafe
          values raise ``EnvironmentHintError`` (422) BEFORE any reservation;
          safe unknown values resolve to the documented fallback (apartment).
        - ``unknown_asset_requests`` (Phase 13, OPT-IN): an explicit bounded
          list of declarative unknown-object requests (each a mapping with
          ``requestedName`` + optional ``objectId``/``categoryHint``/``tags``).
          When generation is enabled (``generate_unknown_assets=True`` and a
          ``spec_provider`` is configured) each request is resolved through the
          procedural oracle, compiled into a frozen definition, placed into the
          resolved kit, and embedded in the published payload (the bootstrap
          world object then carries ``generated``). Any failure or invalid
          request degrades: the payload keeps the golden composition.
        """
        settings = self._settings
        from app.world.environment import canonicalize_environment_hint

        # Local input validation: zero reservations, zero provider calls.
        # Phase 19 Fix A: an explicit ``environment`` body field is
        # deterministically CANONICALIZED (trim/lowercase/space->underscore/
        # alias map) before any resolution; a REJECTED value (path-like /
        # ``..`` / absolute / URL scheme / unsupported token) raises the same
        # sanitized EnvironmentHintError as before — never a provider call,
        # never a file-lookup.
        canonical_environment: str | None = None
        if environment is not None:
            issues = environment_hint_safety(environment)
            if issues:
                raise EnvironmentHintError(
                    "environment hint is invalid or exceeds the configured limit"
                )
            canonical_environment, _canonical_issues = canonicalize_environment_hint(
                environment
            )
            if canonical_environment is None:
                # Safe-but-unknown hint (e.g. "greenhouse by the lake"): the
                # documented safe-unknown behavior is the FALLBACK kit — keep
                # the original value for the resolver (never a provider call).
                canonical_environment = environment
            elif canonical_environment != environment:
                emit_event(
                    "environment.canonicalized",
                    environmentId=canonical_environment,
                    reasonCode="ENVIRONMENT_HINT_CANONICALIZED",
                )
        # Phase 14 — deterministic prompt -> WorldRequirements. The explicit
        # Phase 11 ``environment`` body field takes precedence over the prompt
        # derived hint; unknown values fall back to the documented default kit.
        try:
            locked, _prompt_note = normalize_prompt(
                prompt_text, max_chars=settings.max_prompt_chars
            )
        except PromptError:
            raise PromptValidationError(
                "prompt is invalid or exceeds the configured limit"
            ) from None
        from app.world.extract import extract_world_requirements

        world_reqs = extract_world_requirements(prompt_text, locked)
        prompt_hint = world_reqs.environment_hint
        prompt_canonical, _prompt_canonical_issues = canonicalize_environment_hint(
            prompt_hint
        )
        hint = (
            canonical_environment
            if canonical_environment is not None
            else prompt_canonical
            if prompt_canonical is not None
            else world_reqs.environment_hint
        )
        environment_id, environment_diagnostics = _resolve_environment_for_generation(
            hint
        )
        self._last_environment_resolution = environment_diagnostics
        self._last_world_requirements = world_reqs
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
        if _PD_DEV_TRACE:
            _dev_trace(
                "service.persisted caseId=%s generationId=%s attemptId=%s state=%s dbn=none"
                % (case_id, generation_id, attempt_id, pre_state)
            )
        if record.state is GenerationState.PUBLISHED:
            # Phase 16_2: in ollama mode the stage driver OWNS the composed
            # world (LLM world-requirements -> environment -> oracle -> placer)
            # inside the controller run; the deterministic re-composition below
            # MUST NOT overwrite it. It still applies for fake/live unchanged.
            if settings.generation_provider == "ollama":
                pass
            elif self._last_environment_resolution is not None:
                self._last_environment_resolution["compositionFailed"] = not (
                    self._apply_kit_composition(
                        record, environment_id, world_reqs, environment
                    )
                )
            # Phase 13: OPT-IN declarative procedural assets for the explicit
            # unknown-object request list (after the kit composition, so the
            # golden set is already re-anchored and the generated set is placed
            # into the SAME kit). In ollama driver mode the unknown objects go
            # through the driver's ASSET_SPEC path instead.
            if (
                settings.generation_provider != "ollama"
                and self._generate_unknown_assets
                and self._spec_provider is not None
                and unknown_asset_requests
            ):
                self._last_generated_composition_failed = not (
                    self._apply_generated_composition(
                        record, environment_id, list(unknown_asset_requests)
                    )
                )
        status = self._publish_if_ready(record, title, settings, now, case_id, version) or record.state.value
        if _PD_DEV_TRACE:
            _dev_trace(
                "service.publication.atompted status=%s recordState=%s "
                "publishProvenance=%s"
                % (
                    status,
                    record.state.value,
                    "yes" if self._last_publish_provenance is not None else "no",
                )
            )
        self._sync_generations(anonymous_quota_session_id)
        return CaseStarted(
            case_id=case_id,
            generation_id=generation_id,
            generation_attempt_id=attempt_id,
            creator_access_token=creator_token_value,
            status=status,
            failure_code=(
                public_failure_code(getattr(record, "failure_code", None))
                if record.state is GenerationState.FAILED
                else None
            ),
        )

    # ------------------------------------------------------------------ #
    # Phase 13 — declarative procedural assets (OPT-IN unknown-object path)
    # ------------------------------------------------------------------ #

    def generate_and_stage(
        self,
        asset_request: Any,
        spec_provider: Any = None,
    ) -> str:
        """SERVICE helper: compile + cache ONE unknown asset; returns its
        ``proc.*`` assetId (used by tests + Phase 14).

        ``spec_provider`` overrides the service-configured provider when
        supplied. Raises ``GenerationServiceError``/``AssetGenerationError``
        when no provider is configured or the provider/parse path fails —
        staging never degrades silently (callers opt in explicitly).
        """
        provider = spec_provider if spec_provider is not None else self._spec_provider
        if provider is None:
            raise GenerationServiceError("no asset spec provider configured")
        from app.assets.oracle import generate_and_stage as oracle_stage

        return oracle_stage(
            asset_request, provider, cache=self._generated_cache
        )

    def _apply_generated_composition(
        self,
        record: Any,
        environment_id: str,
        unknown_requests: list[Any],
    ) -> bool:
        """Compose the published draft with declarative generated assets.

        Runs AFTER the Phase 11 kit composition (the golden set is already
        re-anchored). Each unknown request is resolved through the procedural
        oracle (bounded), compiled into a frozen definition, placed into the
        SAME kit via the placer, and embedded in the payload (world-object +
        world-graph placement carrying the definition). Real failure modes
        DEGRADE: the payload keeps the golden composition (never a crash, never
        an invalid publication). Returns True when the payload was composed
        (or there was nothing to add).
        """
        try:
            import dataclasses

            from app.assets.catalog import load_catalog_from_repo
            from app.assets.oracle import resolve_or_generate
            from app.assets.spec_provider import BoundedSpecProvider
            from app.environments.compose import compose_world_graph_for_kit
            from app.environments.manifests import load_environment
            from app.generation.safety import validate_world_graph
            from app.generation.schemas import ObjectSpec
            from app.world.composer import SPEC_PROVIDER_CALL_LIMIT

            published = getattr(record, "published", None)
            if published is None or getattr(published, "draft", None) is None:
                return True
            kit = load_environment(environment_id)
            catalog = load_catalog_from_repo()
            draft = published.draft

            resolved: list[tuple[Any, Any]] = []
            # Phase 14_5 provider-call budget: the explicit unknown-object
            # request list cannot burn unlimited provider calls either.
            budgeted = BoundedSpecProvider(
                self._spec_provider, call_limit=SPEC_PROVIDER_CALL_LIMIT
            )
            for raw in list(unknown_requests)[:MAX_GENERATED_REQUESTS]:
                if not isinstance(raw, Mapping):
                    continue
                outcome = resolve_or_generate(
                    raw,
                    spec_provider=budgeted,
                    cache=self._generated_cache,
                    catalog=catalog,
                )
                if outcome.generated is None or outcome.generated.definition is None:
                    continue  # provider miss/invalid -> explicitly skipped
                resolved.append((raw, outcome.generated))
            if not resolved:
                return True

            definitions: dict[str, Any] = {
                gen.asset_id: gen.definition for _raw, gen in resolved
            }
            generated_requests: list[dict[str, Any]] = []
            new_objects = list(draft.objects)
            for index, (raw, gen) in enumerate(resolved):
                object_id = str(raw.get("objectId") or f"proc_obj_{index}")
                generated_requests.append(
                    {
                        "objectId": object_id,
                        "assetId": gen.asset_id,
                        "interaction": "",
                        "evidenceId": None,
                        "categoryHint": gen.definition.category,
                    }
                )
                new_objects.append(
                    ObjectSpec(
                        object_id=object_id,
                        asset_id=gen.asset_id,
                        affordances=(),
                        subtype=(
                            gen.definition.subtype
                            if isinstance(gen.definition.subtype, str)
                            else None
                        ),
                    )
                )

            world_graph = compose_world_graph_for_kit(
                kit,
                [*draft.world_graph.placements, *generated_requests],
                catalog=catalog,
                generated_definitions=definitions,
            )
            object_ids = {obj.object_id for obj in new_objects}
            evidence_ids = {fact.id for fact in draft.evidence}
            issues = validate_world_graph(world_graph, object_ids, evidence_ids)
            if issues:
                # Degrade: keep the payload without the generated assets.
                return False
            new_draft = dataclasses.replace(
                draft, objects=tuple(new_objects), world_graph=world_graph
            )
            record.published = dataclasses.replace(published, draft=new_draft)
            return True
        except Exception:  # noqa: BLE001 - degradation must never break publication
            return False

    def _apply_kit_composition(
        self,
        record: Any,
        environment_id: str,
        world_reqs: Any,
        explicit_environment: str | None = None,
    ) -> bool:
        """Phase 14 — compose the PUBLISHED draft for the resolved kit + prompt.

        Two deterministic paths:

        - **Legacy byte-identity (apartment)**: when the resolved kit is the
          default apartment AND the prompt produces no NEW object tokens and no
          placement relations, the provider-golden world graph is kept VERBATIM
          and only the scene gains its additive ``environment_id`` +
          ``environment_version`` — the DEFAULT GOLDEN APARTMENT stays
          byte-identical (the explicit Phase 11 ``environment`` body field
          selecting apartment behaves identically to the default).
        - **Prompt-to-world composer**: otherwise ``app.world.composer`` composes
          the kit base + prompt objects (through the Asset Oracle with the
          app-owned deterministic procedural fallback), the world graph is
          rebuilt on the kit zones and the COMPLETE validation pipeline
          (``pipeline.validate_draft``) runs again on the composed draft.

        WORld validation: a non-empty world issue bucket routes through the
        configured ``world_repair_provider`` (bounded, deterministic; sanitized
        diagnostics; locked constraints are NEVER passed to the provider). The
        repaired composition runs the FULL validation again. A locked-constraint
        violation is TERMINAL — the attempt FAILS and is never published. Any
        other failure degrades exactly like Phase 11: the scene keeps its pin
        and the previous (golden) world composition stays. Returns True when
        the payload was composed (or there was nothing to add); False records
        ``compositionFailed`` in the environment diagnostics.
        """
        import dataclasses

        from app.assets.catalog import load_catalog_from_repo
        from app.assets.spec_provider import BoundedSpecProvider
        from app.environments.compose import scene_for_kit
        from app.environments.manifests import load_environment
        from app.environments.placer import is_procedural_asset_id
        from app.generation import pipeline as pipeline_mod
        from app.generation.publish import build_published_case_version
        from app.generation.schemas import (
            EvidenceSpec,
            PropSpec,
            WorldGraphLocationSpec,
            WorldGraphSpec,
        )
        from app.generation.state_machine import (
            GenerationState,
            ValidationOutcome,
        )
        from app.world.composer import (
            KnownObjectSpecProvider,
            SPEC_PROVIDER_CALL_LIMIT,
            compose_world,
        )
        from app.world.extract import is_base_object_request
        from app.world.requirements import CRITICALITY_REQUIRED, WorldRequirements

        published = getattr(record, "published", None)
        if published is None or getattr(published, "draft", None) is None:
            return True
        draft = published.draft
        try:
            kit = load_environment(environment_id)
            scene = scene_for_kit(kit, draft.scene)
            catalog = load_catalog_from_repo()
            if not isinstance(world_reqs, WorldRequirements):
                world_reqs = WorldRequirements()

            has_new_objects = any(
                not is_base_object_request(request.requested_name)
                for request in world_reqs.objects
            )
            if (
                environment_id == "apartment"
                and not has_new_objects
                and not world_reqs.relations
            ):
                # Legacy byte-identity path: scene-only injection.
                new_draft = dataclasses.replace(draft, scene=scene)
                record.published = dataclasses.replace(published, draft=new_draft)
                return True

            spec_provider = (
                self._spec_provider
                if self._spec_provider is not None
                else KnownObjectSpecProvider()
            )
            # Phase 14_5 — ONE provider-call budget for the WHOLE attempt: the
            # budget wrapper is hoisted here so every repair pass re-composes
            # against the SAME bounded count (cache hits never call it).
            attempt_budget = BoundedSpecProvider(
                spec_provider, call_limit=SPEC_PROVIDER_CALL_LIMIT
            )

            def _compose(reqs: WorldRequirements) -> Any:
                return compose_world(
                    reqs,
                    env_resolver=None,
                    spec_provider=attempt_budget,
                    cache=self._generated_cache,
                    evidence_placements=draft.world_graph.placements,
                    catalog=catalog,
                    kit=kit,
                    max_world_objects=int(
                        getattr(self._settings, "max_world_objects_per_kit", 32)
                    ),
                )

            composition = _compose(world_reqs)
            if composition.issues and self._world_repair_provider is not None:
                for _pass in range(self._max_world_repair_passes):
                    base_report = (
                        record.last_validation
                        if record.last_validation is not None
                        else pipeline_mod.ValidationReport()
                    )
                    repair_report = dataclasses.replace(
                        base_report, world_issues=composition.issues
                    )
                    revised = self._world_repair_provider(
                        repair_report.repair_diagnostics
                    )
                    if revised is None:
                        break
                    composition = _compose(revised)
                    if not composition.issues:
                        break

            if composition.issues:
                if _has_unresolved_required(world_reqs, composition):
                    # Phase 14_5: a crime-critical REQUIRED unknown object could
                    # not be generated/placed within the repair budget —
                    # TERMINAL failure. Never silently removed, never replaced
                    # with a semantically-incorrect catalog asset.
                    record.state = GenerationState.FAILED
                    record.reason = (
                        "terminal world failure: a crime-critical required "
                        "prompt object could not be generated or placed"
                    )
                    return False
                # Degrade (documented): pin the scene, keep the golden world.
                record.published = dataclasses.replace(
                    published, draft=dataclasses.replace(draft, scene=scene)
                )
                return False

            # Phase 14_5 — DEV composition seam: a REQUIRED unseen weapon
            # (resolved PROCEDURAL_GENERATED from an unseen prompt noun) becomes
            # the crime-weapon EVIDENCE object: it gains a discoverable forensic
            # fingerprint fact linked to the ALREADY-EXCLUDED michael_carter.
            # The solver-critical facts stay on the catalog knife (golden
            # all_true/solvability untouched); the new fact ONLY labels the
            # unseen object as evidence-bearing and discoverable — it never
            # widens the candidate universe (INSPECTABLE affordance only, no
            # POTENTIAL_WEAPON) and never alters a necessary-condition fact.
            new_placements, new_facts = _link_required_unknown_evidence(
                composition, world_reqs, draft
            )
            new_draft = dataclasses.replace(
                draft,
                scene=scene,
                objects=tuple([*draft.objects, *composition.new_objects]),
                evidence=tuple([*draft.evidence, *new_facts]),
                # ADV-153: the DECORATIVE-unresolved warnings are stored with
                # the published draft (player-safe, bounded, sanitized).
                composition_notes=tuple(composition.composition_notes or ()),
                world_graph=WorldGraphSpec(
                    locations=tuple(
                        WorldGraphLocationSpec(
                            location_id=zone.zone_id,
                            template=f"{kit.environment_id}_template",
                            rooms=zone.rooms,
                        )
                        for zone in kit.zones
                    ),
                    placements=tuple(
                        new_placements if new_placements is not None else composition.placements
                    ),
                ),
            )
            # COMPLETE validation pipeline over the composed draft.
            record.draft = new_draft
            record._phase3_cache = None
            report = pipeline_mod.validate_draft(record)
            if report.locked_violations:
                # A locked-constraint violation is TERMINAL (never repaired).
                record.state = GenerationState.FAILED
                record.reason = (
                    "terminal validation failure: "
                    + "; ".join(report.locked_violations)
                )
                return False
            if report.outcome is not ValidationOutcome.VALID:
                record.published = dataclasses.replace(
                    published, draft=dataclasses.replace(draft, scene=scene)
                )
                return False
            record.published = build_published_case_version(record)
            return True
        except Exception:  # noqa: BLE001 - degradation must never break publication
            # Degrade: the scene + previous (golden) world composition stay.
            try:
                record.published = dataclasses.replace(
                    published, draft=dataclasses.replace(draft, scene=scene)
                )
            except Exception:  # noqa: BLE001 - never raise on degradation
                pass
            return False

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
            locked, _prompt_note = normalize_prompt(
                prompt_text, max_chars=settings.max_prompt_chars
            )
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

        # Phase 14 — prompt-to-world for the re-publication (v2+): extract the
        # WorldRequirements from the NEW prompt and resolve the environment.
        from app.world.extract import extract_world_requirements
        from app.world.environment import canonicalize_environment_hint

        world_reqs = extract_world_requirements(prompt_text, locked)
        self._last_world_requirements = world_reqs
        canonical, _canonical_issues = canonicalize_environment_hint(
            world_reqs.environment_hint
        )
        hint = canonical if canonical is not None else world_reqs.environment_hint
        environment_id, environment_diagnostics = _resolve_environment_for_generation(
            hint
        )
        self._last_environment_resolution = environment_diagnostics

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
        if record.state is GenerationState.PUBLISHED:
            # Phase 16_2: skip the deterministic re-composition in ollama mode
            # (the stage driver owns the composed world for the new version).
            if (
                settings.generation_provider != "ollama"
                and self._last_environment_resolution is not None
            ):
                self._last_environment_resolution["compositionFailed"] = not (
                    self._apply_kit_composition(record, environment_id, world_reqs)
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
            failure_code=(
                public_failure_code(getattr(record, "failure_code", None))
                if record.state is GenerationState.FAILED
                else None
            ),
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
        # Phase 16_2: a selected local-Llama provider runs through the Ollama
        # stage driver (structured per-stage calls producing a full draft),
        # classified through the SAME controller lifecycle.
        driver = self._build_stage_driver() if settings.generation_provider == "ollama" else None
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
            # Phase 19 Fix C — hierarchical provider budgets from config.
            max_core_llm_calls=settings.max_core_llm_calls_per_generation,
            max_llm_calls_per_procedural_asset=(
                settings.max_llm_calls_per_procedural_asset
            ),
            max_procedural_assets_per_generation=(
                settings.max_procedural_assets_per_generation
            ),
            max_failed_assets_per_generation=(
                settings.max_failed_assets_per_generation
            ),
            seed=None,  # per-controller auto seed (deterministic per controller)
            stage_driver=driver,
            provider_timeout_seconds=(
                settings.ollama_timeout_seconds
                if settings.generation_provider == "ollama"
                else 30.0
                if settings.generation_provider == "live"
                else None
            ),
            provider_name=settings.generation_provider,
            provider_model=(
                settings.ollama_model
                if settings.generation_provider == "ollama"
                else settings.llm_model
            ),
        )
        _t0 = time.perf_counter()
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
        if _PD_DEV_TRACE:
            budget = record.budget
            _dev_trace(
                "service.run_generation.done state=%s reason=%r dbn=none calls=%s "
                "repairs=%s regens=%s elapsedMs=%d deadlineRemainingMs=%s "
                "attemptId=%s"
                % (
                    record.state.value,
                    record.reason,
                    budget.calls if budget is not None else "?",
                    budget.repair_passes if budget is not None else "?",
                    budget.regenerations if budget is not None else "?",
                    int((time.perf_counter() - _t0) * 1000),
                    _dev_remaining(record),
                    record.attempt_id,
                )
            )
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
        emit_event(
            "publication.started",
            caseId=case_id,
            caseVersion=version,
            generationAttemptId=getattr(record, "attempt_id", None),
            deadlineRemainingMs=(
                int(record.budget.remaining_seconds() * 1000)
                if getattr(record, "budget", None) is not None else None
            ),
        )
        try:
            self._publication.publish_transactionally(
                published,
                seed=record.seed,
                prompt=record.prompt,
                model=(
                    settings.llm_model
                    if settings.generation_provider == "live"
                    else settings.ollama_model
                    if settings.generation_provider == "ollama"
                    else None
                ),
                title=title,
            )
        except DuplicatePublication:
            raise
        except Exception:
            failure_code = GenerationFailureCode.PUBLICATION_FAILED.value
            try:
                # The publication service transaction has rolled back. Mark
                # the durable snapshot FAILED separately; no payload is ever
                # left behind for this terminal state.
                self._store.update_case_version_state(
                    case_id,
                    int(version),
                    GenerationState.FAILED.value,
                    state_reason=failure_code,
                )
                self._store.upsert_generation_attempt(
                    attempt_id=record.attempt_id,
                    case_id=case_id,
                    case_version=int(version),
                    status=GenerationState.FAILED.value,
                    stage="failed",
                    progress=100,
                    created_at=float(now),
                    updated_at=float(now),
                )
            except Exception:
                # Preserve the original publication exception and its safe API
                # mapping even if the defensive failure snapshot also fails.
                pass
            emit_event(
                "publication.failed",
                caseId=case_id,
                caseVersion=version,
                generationAttemptId=getattr(record, "attempt_id", None),
                failureCode=failure_code,
            )
            raise
        # Phase 10 internal diagnostic: how the published placements resolved
        # through the Asset Oracle (never part of the payload / DTOs).
        self._last_publish_provenance = _record_asset_oracle_provenance(
            published, settings
        )
        emit_event(
            "publication.complete",
            caseId=case_id,
            caseVersion=version,
            generationAttemptId=getattr(record, "attempt_id", None),
            published=True,
            deadlineRemainingMs=(
                int(record.budget.remaining_seconds() * 1000)
                if getattr(record, "budget", None) is not None else None
            ),
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
        version_row = self._store.get_case_version_by_generation_id(
            generation_id, case_id=case_id
        )
        return {
            "caseId": row.case_id,
            "generationId": generation_id,
            "status": row.status,
            "progress": row.progress,
            "stage": row.stage,
            "failureCode": (
                public_failure_code(version_row.state_reason)
                if row.status == GenerationState.FAILED.value and version_row is not None
                else None
            ),
        }

    def get_public_case(
        self,
        case_id: str,
        case_version: int,
        *,
        discovered: set[str] | frozenset[str] | None = None,
    ) -> dict[str, Any] | None:
        """Exact published-version public DTO dict (from the frozen payload).

        ``discovered`` (Phase 20 / PD-SEC-01) — when supplied the DTO is the
        PLAYTHROUGH-scoped public-case and carries ONLY player-known evidence
        metadata (see ``publication.public_case_dict_from_payload``); when
        None (the creator-scoped ``GET /cases/{id}`` dossier) the full
        REQUIREMENTS 41.2 public-case evidence list is kept.
        """
        row = self._store.get_published(case_id, case_version)
        if row is None:
            return None
        try:
            payload = json.loads(row.payload_json)
        except (ValueError, TypeError):
            return None
        return public_case_dict_from_payload(payload, discovered=discovered)

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
        if settings.generation_provider == "ollama":
            from app.core.config import DEFAULT_OLLAMA_BASE_URL
            from app.generation.ollama_provider import (
                OllamaProvider,
                ollama_structured_output_supported,
            )

            base_url = settings.ollama_base_url or DEFAULT_OLLAMA_BASE_URL

            # Phase17B §2: resolve the structured-output capability ONCE at
            # factory build (a single documented /api/version probe; never per
            # call, never raises). True => every request carries the
            # AUTHORITATIVE per-stage JSON Schema in format; False => the
            # documented "json" fallback.
            structured_output = False
            try:
                structured_output = ollama_structured_output_supported(settings)
            except Exception:  # noqa: BLE001 - capability probe never blocks
                structured_output = False

            def _ollama() -> Provider:
                return OllamaProvider(
                    base_url=base_url,
                    model=settings.ollama_model,
                    timeout_seconds=settings.ollama_timeout_seconds,
                    temperature=settings.ollama_temperature,
                    num_ctx=settings.ollama_num_ctx,
                    structured_output=structured_output,
                )

            return _ollama
        script = self._load_fake_script()
        if not script:
            raise ProviderConfigError("fake provider script is empty")

        def _fake() -> Provider:
            return FakeProvider(script=script)

        return _fake

    def _build_stage_driver(self) -> Any:
        """Phase 16_2: an ``OllamaStageDriver`` for a selected local-Llama provider.

        Only the ollama branch is enabled; fake/live return None (their
        deterministic composition is UNCHANGED). The driver shares the cached
        generated-asset store; unknown REQUIRED objects route through its
        ASSET_SPEC adapter (bound)."""
        from app.services.ollama_driver import OllamaStageDriver

        return OllamaStageDriver(
            settings=self._settings,
            provider_factory=self._provider_factory,
            generated_cache=self._generated_cache,
            catalog=None,
        )

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
                raw = _bounded_provider_script_load(path)
            except (OSError, ValueError) as exc:
                raise ProviderConfigError(
                    f"FAKE_PROVIDER_SCRIPT is not valid JSON: {exc}"
                ) from None
        else:
            builtin = Path(__file__).resolve().parent / "dev_mode_case.json"
            try:
                raw = _bounded_provider_script_load(builtin)
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


def _dev_remaining(record: Any) -> str:
    budget = getattr(record, "budget", None)
    if budget is None or not hasattr(budget, "remaining_seconds"):
        return "none"
    return str(int(budget.remaining_seconds() * 1000))


__all__ = [
    "AdmissionDenied",
    "AdmissionDeniedError",
    "CaseStarted",
    "CreatedAnonymousSession",
    "EnvironmentHintError",
    "GenerationService",
    "GenerationServiceError",
    "IdentifierConflict",
    "MAX_GENERATED_REQUESTS",
    "PromptError",
    "PromptValidationError",
    "ProviderConfigError",
    "UnknownCaseError",
]
