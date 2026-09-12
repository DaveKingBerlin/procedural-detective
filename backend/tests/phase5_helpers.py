"""Shared Phase 5 test helpers.

- ``assert_no_hidden_leaks`` — recursive key-path scanner (Phase5 J): walks
  EVERY nesting level of a response payload and fails when a hidden/secret
  field or value appears. Contextual rule: ``motiveId`` is allowed ONLY as a
  key of the public ``motives`` collection; everything else on the forbidden
  list is banned anywhere (murdererId / victimId / weaponId / canonical crime
  time / truth / proof / diagnostics / prompt / provider output / verifier /
  tokens / internal db ids / ...).
- ``assert_sanitized_error`` — asserts the 4xx/5xx body never leaks stack
  traces, SQL, table/column names or file paths.
- API babysitter helpers: create an anonymous session, mint a case, create a
  playthrough, all through the public API.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

# Truth-level / hidden / internal keys banned ANYWHERE in a public response.
_FORBIDDEN_ANYWHERE = frozenset(
    {
        # canonical hidden truth / crime
        "murdererId",
        "victimId",
        "weaponId",
        "crimeTime",
        "canonicalCrimeTime",
        "canonical",
        "truthfulness",
        "crime",
        "timeline",
        "facts",
        "relationships",
        # solution / validation internals
        "solutionProof",
        "acceptedScoring",
        "solverProof",
        "proof",
        "truth",
        "universe",
        "universes",
        "validation",
        "report",
        "winners",
        "remainingCandidateIds",
        "remainingMotiveIds",
        "remainingWeaponIds",
        # generation internals / provider material
        "prompt",
        "providerOutput",
        "diagnostics",
        "seed",
        "model",
        "locked",
        "promptNote",
        "stageOutputs",
        "worldGraphSpec",  # platform-internal name space (DTO uses worldGraph)
        # evidence deduction material that Phase 5 never exposes
        "sourceRef",
        "propositions",
        "observedAt",
        "uncertaintySeconds",
        "presentedData",
        # tokens / secrets / internal identities
        "verifier",
        "tokenVerifier",
        "token",  # the three explicit creation fields use distinct names
        "sessionId",
        "anonymousQuotaSessionId",
        "quotaSessionId",
        "attemptId",
        "generationAttemptId",  # public ONLY in the explicit creation DTO
    }
)

_TOKEN_KEYS = frozenset({"anonymousSessionToken", "creatorAccessToken", "playthroughAccessToken"})

_CANONICAL_CRIME_TIME = "2026-09-11T22:17:00+02:00"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# Paths where ``motiveId`` is the PUBLIC universe member id (legitimate).
_MOTIVE_PATH_RE = re.compile(r"^motives\.\d+\.motiveId$")

# Raw-text markers that must never appear in an error response.
_FORBIDDEN_TEXT_MARKERS = (
    "Traceback",
    " File \"",
    "line ",
    "sqlite",
    "SQLite",
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE FROM",
    "CREATE TABLE",
    "table ",
    "column ",
    "C:",
    "\\backend\\",
    "backend\\",
    "operational error",
    "IntegrityError",
    "OperationalError",
    "no such table",
)


def _iter_key_paths(node: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    """Yield (dotted-key-path, value) for every node of a JSON-ish tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield (path, value)
            yield from _iter_key_paths(value, path)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            path = f"{prefix}.{index}"
            yield (path, value)
            yield from _iter_key_paths(value, path)


def assert_no_hidden_leaks(
    payload: Any,
    *,
    allow_token_keys: frozenset[str] = frozenset(),
    known_tokens: set[str] | None = None,
    allow_none: bool = True,
) -> list[str]:
    """Assert a response payload contains no hidden/secret material.

    Walks EVERY nesting level (deep-nesting regression, Phase5 J/M26). Returns
    the list of violations (empty == clean); also asserts directly.
    """
    violations: list[str] = []
    for path, value in _iter_key_paths(payload):
        leaf_key = path.rsplit(".", 1)[-1]
        if leaf_key in _FORBIDDEN_ANYWHERE:
            if leaf_key == "generationAttemptId":
                continue  # public creation-time label (POST /cases contract)
            violations.append(f"forbidden key {path!r}")
        if leaf_key == "motiveId" and not _MOTIVE_PATH_RE.match(path):
            violations.append(f"motiveId outside the public motives collection at {path!r}")
        if leaf_key in _TOKEN_KEYS and leaf_key not in allow_token_keys:
            violations.append(f"token key exposed outside creation at {path!r}")
        creation_field = leaf_key in _TOKEN_KEYS and leaf_key in allow_token_keys
        if isinstance(value, str):
            if value == _CANONICAL_CRIME_TIME or _CANONICAL_CRIME_TIME in value:
                violations.append(f"canonical crime time leaked at {path!r}")
            if _HEX64_RE.match(value):
                violations.append(f"64-hex verifier-like value leaked at {path!r}")
            # The creation-time token fields legitimately carry the token value
            # exactly once; ALWAYS forbid it anywhere else.
            if known_tokens and value in known_tokens and not creation_field:
                violations.append(f"known token value leaked at {path!r}")
    assert not violations, (
        "hidden/secret material found in public response: " + "; ".join(violations)
    )
    return violations


def assert_sanitized_error(response_text: str) -> None:
    """Assert an error body contains no traceback/SQL/path internals."""
    lower = response_text.lower()
    for marker in _FORBIDDEN_TEXT_MARKERS:
        assert marker not in response_text, (
            f"error response leaks {marker!r}"
        )
    for marker in ("traceback", "stack trace"):
        assert marker not in lower, f"error response leaks {marker!r}"


# --------------------------------------------------------------------------- #
# API babysitters (test-only helpers over a TestClient)
# --------------------------------------------------------------------------- #


def create_session(client: Any) -> tuple[str, dict[str, Any]]:
    """POST /api/v1/sessions/anonymous -> (token, body)."""
    res = client.post("/api/v1/sessions/anonymous")
    body = res.json()
    assert res.status_code == 201, body
    return body["anonymousSessionToken"], body


def create_case(
    client: Any,
    session_token: str,
    prompt: str = "Victim: sarah_miller\nMurderer: thomas_reed\n",
    difficulty: str | None = "medium",
) -> dict[str, Any]:
    """POST /api/v1/cases with an anonymous session token -> creation body."""
    payload: dict[str, Any] = {"prompt": prompt}
    if difficulty is not None:
        payload["difficulty"] = difficulty
    res = client.post(
        "/api/v1/cases",
        json=payload,
        headers={"Authorization": f"Bearer {session_token}"},
    )
    body = res.json()
    assert res.status_code == 201, body
    return body


def create_playthrough(
    client: Any,
    creator_token: str,
    case_id: str,
    case_version: int = 1,
) -> tuple[int, dict[str, Any]]:
    res = client.post(
        f"/api/v1/cases/{case_id}/versions/{case_version}/playthroughs",
        headers={"Authorization": f"Bearer {creator_token}"},
    )
    return res.status_code, res.json()


def auth(headers_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {headers_token}"}


# --------------------------------------------------------------------------- #
# publication pipeline helpers (shared by publication_tx + concurrency tests)
# --------------------------------------------------------------------------- #

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)


def _phase5_settings_for(url: str):
    from app.core.config import Settings

    return Settings(
        database_url=url,
        max_generations_per_session_per_window=8,
        max_concurrent_generations=4,
    )


def golden_script(store, database_url: str) -> dict:
    """The production builtin dev-mode fake script (no test fixtures)."""
    from app.services.generation import GenerationService

    return GenerationService(
        settings=_phase5_settings_for(database_url), store=store
    )._load_fake_script()


def held_published(database_url: str, script: dict, seed: int = 11):
    """Run the golden pipeline with hold_before_publish=True and return
    (published, attempt_record, session_id, clock)."""
    from app.generation.admission import AdmissionController
    from app.generation.clock import ManualClock
    from app.generation.controller import GenerationController
    from app.generation.fake_provider import FakeProvider
    from app.generation.ids import IdSource

    clock = ManualClock(start_time=1000.0)
    ids = IdSource()
    admission = AdmissionController(
        clock=clock,
        ids=ids,
        max_concurrent_generations=4,
        max_concurrent_generations_global=8,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        anonymous_quota_session_ttl_seconds=86400,
        global_window_end=1000.0 + 86400,
    )
    session = admission.create_anonymous_quota_session()
    controller = GenerationController(
        provider=FakeProvider(script=script),
        admission=admission,
        clock=clock,
        ids=ids,
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=seed,
        hold_before_publish=True,
    )
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    result = controller.publish(handle.attempt_id, hold_ok=True)
    assert result.success, result.reason
    assert result.published is not None
    return result.published, record, session.session_id, clock


def seed_session(store, session_id, clock):
    """Create one durable session row (FK parent for every other row)."""
    from app.auth.tokens import issue_token, verifier as _v

    now = float(clock.now())
    store.create_session(
        session_id=session_id,
        token_verifier=_v(issue_token()),
        quota_window_end=now + 3600,
        created_at=now,
    )
    return now


def seed_pending_version(store, published, session_id, clock, status="VALIDATING"):
    """Persist pre-publication rows: session + case + version + attempt."""
    from app.persistence.store import Store  # noqa: F401 (type clarity)

    now = seed_session(store, session_id, clock)
    store.create_case(
        case_id=published.case_id,
        quota_session_id=session_id,
        title="Atomic",
        difficulty=None,
        created_at=now,
    )
    store.create_case_version(
        case_id=published.case_id,
        version=published.case_version,
        state="VALIDATING",
        generation_id=f"GEN-{published.case_version}",
        created_at=now,
    )
    try:
        store.upsert_generation_attempt(
            attempt_id=published.generation_attempt_id,
            case_id=published.case_id,
            case_version=published.case_version,
            status=status,
            stage="validating",
            progress=80,
            created_at=now,
            updated_at=now,
        )
    except Exception:  # noqa: BLE001 - an attempt may already exist
        pass
    return now