"""Phase 25/30 — Frontier BYOK OpenAI-compatible provider adapter.

``FrontierProvider`` implements the EXACT existing ``Provider`` protocol
(``Provider.generate(GenerateRequest) -> ProviderResult``) and reuses the
``LiveHttpProvider`` transport pattern (one synchronous HTTP POST of an
OpenAI-compatible ``{model, messages:[{role, content}]}`` body + Bearer auth),
with the SAME Phase 20 hardening:

- PD-SEC-06: provider-derived error content is NEVER echoed. A non-2xx body
  is reduced to the sanitized status only — never ``response.text``, never an
  exception ``str()`` (``httpx.RequestError`` strings embed the request URL).
- PD-SEC-09: 2xx responses are bounded (``MAX_FRONTIER_RESPONSE_BYTES``,
  256 KiB — the same cap LiveHttpProvider and OllamaProvider enforce); an
  over-cap body degrades to the clean size-cap error instead of being
  buffered unboundedly.
- The API key is an attempt-scoped TRANSIENT secret: it lives in this
  adapter's constructor (from the immutable per-attempt ``GenerationSelection``
  — Phase 30 browser BYOK), is never logged, never embedded in an exception
  and never serialized into any DTO / capability response / case material. It
  travels ONLY as the ``Authorization: Bearer <key>`` header of the outbound
  POST.

Phase 30 — typed provider failures (``FrontierHttpError``): every non-2xx
status, timeout and network/size-cap failure raises a ``ProviderError``
subclass carrying a canonical ``GenerationFailureCode`` so the generation
controller classifies the attempt WITHOUT ever parsing provider internals:

    HTTP 401/403  -> FRONTIER_AUTH_FAILED
    HTTP 404      -> FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND
    HTTP 429      -> FRONTIER_RATE_LIMITED
    request timeout -> FRONTIER_TIMEOUT
    HTTP 5xx (and any other non-2xx status) -> FRONTIER_PROVIDER_ERROR
    network request failure / oversize 2xx  -> FRONTIER_PROVIDER_ERROR

The STATUS BAND is therefore available to the controller for the §23
normalized code mapping, while the browser still never sees raw bodies,
headers or credentials.

Why a separate adapter instead of overloading ``LiveHttpProvider``:
``LiveHttpProvider`` is the legacy ``live`` provider pair (``LIVE_PROVIDER_URL``
/ ``LLM_API_KEY`` / ``LLM_MODEL``) with its own configured model bound at
construction. Phase 25/30 need a SECOND hosted provider (``frontier``) whose
endpoint comes from the trusted server-owned registry and whose key + model
are the user's per-attempt BYOK values.

``sink`` is accepted for interface compatibility; this sync adapter never
produces ``pending`` results (the call blocks until completion or timeout).

Phase30-fix (DEF-A) — REAL wall-clock timeout enforcement. The httpx
``timeout=`` argument is a PER-OPERATION (connect/write/read/pool) timeout,
NOT a total wall-clock deadline: a slow/dribbling response-body read, DNS
resolution, connection-pool wait or upstream stream can legally outlive it
(the live production defect: a call advertised as 180000 ms effective ran
~579348 ms and still logged success). This adapter therefore bounds the ENTIRE
outbound operation — connect, request write, upstream processing, response
headers, response-body read and decode — with a real monotonic deadline equal
to ``request.timeout_seconds`` (already the controller's
``min(configured provider timeout, remaining generation deadline - margin)``).

Phase30-fix DEF-020 — TRUE cancellation at the wall-clock bound. The
outbound call runs in a short-lived supervised worker over a DEDICATED,
per-call ``httpx.Client`` owned by this adapter. When the supervising caller
thread observes the deadline while the call is still in flight it CLOSES that
client from the supervisor side — closing the connection interrupts the
blocked request/body-read port — instead of merely discarding a still-running
call that keeps holding the user's key. The worker therefore terminates at/
before the bound (bounded re-join), and repeated timeouts cannot accumulate
live outbound threads. A timed-out call is NEVER allowed to produce a
``ProviderResult`` afterwards: the outcome box is private to this adapter and
discarded on timeout, so a late worker completion can never surface as a
successful stale mutation to the controller.

Phase30-fix DEF-018 — NO unhandled thread exception may escape the supervised
worker. Every transport/body/streaming failure (``httpx.ReadTimeout``,
``httpx.RemoteProtocolError``, ``httpx.ConnectError``, ``OSError``, ...) is
reduced to the typed canonical path: ``FRONTIER_TIMEOUT`` when the wall-clock
deadline has been reached, otherwise ``FRONTIER_PROVIDER_ERROR`` with a
sanitized message (never the exception ``str()`` which may embed the URL).
``generate()`` ALWAYS returns a ``ProviderResult`` or raises a
``FrontierHttpError`` — never None.

The module-level ``httpx`` name is a SMALL TRANSPORT SEAM
(``_FrontierTransportSeam``): its ``post`` attribute is the injectable
outbound dispatch (deterministic mocks replace it in tests), and its default
implementation issues the POST through the dedicated per-call client,
registering that client in the call's outcome box so the supervisor can abort
it. The seam exposes the real httpx exception/class vocabulary unchanged.

Phase30-fix (DEF-B) — canonical trusted stage schemas reach the wire. The
controller attaches the server-owned per-stage output JSON Schema (from
``prompts.json_schema_for_stage_output`` — a parser-shaped schema, see
DEF-019) to the ``GenerateRequest``; when the registry entry declared the
native ``openai_json_schema`` capability, the adapter sends the
OpenAI-compatible ``response_format={"type": "json_schema", "json_schema":
{...}}`` representation of THAT server-owned schema only. The browser can
never supply or replace schema/response_format/endpoint/protocol — the
``FrontierBlock`` accepts exactly ``provider/apiKey/model`` and everything
else is registry + stage derived. Providers/entries without the capability
(mode ``None``) keep the existing bounded prompt-embedded fallback and report
``structuredOutput=false`` truthfully via ``last_structured_output``.

Phase31A — two PROVEN compatibility fixes (see ADVERSARIAL_REVIEW.md
"Phase 31A — Live Dev-Box root-cause session"):

- Track A (Cohere evidence 400): a server-owned protocol-adaptation layer
  (``app.generation.schema_adapters``) rewrites ONLY the TRANSPORT
  ``response_format`` schema — never the canonical schema/fingerprint/parser —
  so the EVIDENCE_v1 open ``structured`` object satisfies Cohere's "every
  object must declare >= 1 required property" JSON-Schema rule while keeping
  the open-object intent (``additionalProperties``). The adapter is selected
  by a closed server-side function of the validated model + registry
  capability; every non-Cohere provider keeps the byte-identical canonical
  wire.
- Track B (DeepSeek REPAIR_BUDGET_EXHAUSTED from envelope leakage): a bounded,
  shape-gated content-EXTRACTION step in the 2xx path
  (``extract_openai_chat_completions_content``) unwraps an OpenAI-compatible
  Chat Completions envelope (``choices[i].message.content`` / top-level
  ``content``) into the bare stage-document string BEFORE the strict stage
  parser sees it. Non-envelope bodies (bare golden-mock stage JSON, code-
  fenced docs, malformed fixtures) pass through UNCHANGED. Strict parsers and
  validators are untouched.
"""

from __future__ import annotations

import math
import threading
from typing import Any

import httpx as _httpx

from app.assets.depthguard import (
    BoundedJsonError,
    bounded_json_loads,
)
from app.generation.clock import Clock, RealClock
from app.generation.failure_codes import GenerationFailureCode
from app.generation.frontier_registry import (
    STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA,
)
from app.generation.prompts import canonical_schema_bytes, schema_fingerprint
from app.generation.provider import (
    GenerateRequest,
    ProviderError,
    ProviderResult,
)
from app.generation.schema_adapters import (
    adapt_schema_for_transport,
    schema_adapter_id_for_frontier_call,
)

# Bounded 2xx response cap (PD-SEC-09 parity with the live/ollama adapters).
MAX_FRONTIER_RESPONSE_BYTES = 256 * 1024  # 256 KiB

# Documented default timeout when the caller does not supply one (the service
# always passes the operator-configured FRONTIER_TIMEOUT_SECONDS).
DEFAULT_FRONTIER_TIMEOUT_SECONDS = 60.0

# The caller-thread poll quantum: how long the supervising thread may wait on
# the worker before re-reading its monotonic clock. Small enough that a
# fake/accelerated clock (tests) is re-read promptly; large enough not to
# spin the CPU on every fast successful call.
_WALL_CLOCK_POLL_QUANTUM_SECONDS = 0.02

# The native OpenAI-compatible JSON Schema structured-output mode name (the
# single supported member of ``FRONTIER_STRUCTURED_OUTPUT_MODES``). Sent ONLY
# when the trusted registry entry declared it AND the stage carries a
# server-owned schema.
_FRONTIER_RESPONSE_FORMAT_JSON_SCHEMA = "openai_json_schema"


# --------------------------------------------------------------------------- #
# Phase31A (Track B) — OpenAI-compatible 2xx envelope content extraction.
#
# PROVEN ROOT CAUSE (``ADVERSARIAL_REVIEW.md`` — "Phase 31A — Live Dev-Box
# root-cause session", Track B): deepseek/deepseek-v4.1-flash exhausts the
# repair budget with providerCallCount=6 / repairCount=2 and validator codes
# [STRUCTURED_OUTPUT_INVALID] unchanged across every pass. The free-text
# issue root is ``case_truth: unknown key 'choices'/'created'/'id'/'model'/...
# missing required 'crime'`` — the STRICT stage parser was receiving the RAW
# OpenRouter OpenAI-compatible Chat Completions ENVELOPE
# ``{"choices":[{"message":{"content":"<stage JSON as a string>"}}], "id":...,
# "model":..., "usage":...}`` instead of the stage document. Repairs cannot
# fix a transport/extraction defect, hence UNCHANGED -> REPAIR_BUDGET_EXHAUSTED.
#
# The fix is a SERVER-OWNED, SHAPE-GATED content-EXTRACTION step in the 2xx
# path (NOT a validator change, NOT a model/prompt change): if the bounded
# body is exactly an OpenAI-compatible envelope, ONLY the first usable
# ``choices[i].message.content`` string (or a top-level ``content`` string)
# becomes the stage content; otherwise the body passes through UNCHANGED, so
# the Phase 30 golden-mock wires (bare stage JSON documents / code-fenced
# docs / "malformed" fixtures) stay byte-identical. The strict parser and the
# full validation suite are untouched.
# --------------------------------------------------------------------------- #
#
# Bounded + NaN-rejecting decode: the body is already transport-bounded
# (MAX_FRONTIER_RESPONSE_BYTES); ``bounded_json_loads`` (the repo-wide depth
# preflight parser) additionally rejects a nesting-bomb BEFORE ``json.loads``,
# and ``_reject_non_finite_anywhere`` rejects the non-standard JSON numbers
# (``NaN`` / ``Infinity`` / ``-Infinity``) that ``json.loads`` would accept.
# Any decode/hazard failure means "not the envelope shape" -> pass-through.


def _reject_non_finite_anywhere(node: Any) -> None:
    """Iterative (never recursive) non-finite-number presence check.

    ``json.loads`` accepts the non-standard ``NaN``/``Infinity``/
    ``-Infinity`` constants by default; a non-finite number anywhere makes a
    body ineligible for envelope extraction (pass-through to the strict
    parser, which then reports its own deterministic issue). Never recurses,
    so a hostile deep tree cannot stack-blow here (the depth preflight has
    already bounded the document).
    """
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("non-finite JSON number")


def extract_openai_chat_completions_content(text: str) -> str | None:
    """Return only the inner stage-content string when ``text`` is an
    OpenAI-compatible Chat Completions 2xx ENVELOPE; ``None`` otherwise.

    Envelope shapes that are extracted (server-owned, deterministic):

      1. ``{"choices": [{"message": {"content": "<str>"}}, ...], ...}`` — the
         FIRST dict item carrying a ``message.content`` string wins (never a
         list of items, never raw output, never multiple-choices ambiguity);
      2. ``{"content": "<str>", ...}`` — the OpenAI-compatible top-level
         content form.

    Every other shape — a BARE stage JSON document, a code-fenced document,
    invalid JSON, a deep/bombed document, a non-finite-number body, a
    malformed envelope without a usable content string — returns ``None`` so
    the caller passes the original body through UNCHANGED (the strict parser
    then reports its deterministic issue, exactly as before the fix).

    The extracted string is further bounded to ``MAX_FRONTIER_RESPONSE_BYTES``
    (belt-and-braces: the transport read was already capped). The envelope
    keys/markup (id/model/usage/choices-array) are NEVER echoed — only the
    content string leaves this function.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        parsed = bounded_json_loads(text)
        _reject_non_finite_anywhere(parsed)
    except (BoundedJsonError, TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    top_content = parsed.get("content")
    if isinstance(top_content, str):
        if len(top_content.encode("utf-8")) > MAX_FRONTIER_RESPONSE_BYTES:
            return None
        return top_content
    choices = parsed.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                if len(content.encode("utf-8")) > MAX_FRONTIER_RESPONSE_BYTES:
                    return None
                return content
    return None


# --------------------------------------------------------------------------- #
# Phase31A — INTERNAL safe upstream status classification (diagnostics only)
# --------------------------------------------------------------------------- #
#
# The PUBLIC failure-code vocabulary (FRONTIER_*) is unchanged. These closed
# tokens are an ADDITIONAL internal diagnostic that distinguishes a schema-
# level 4xx rejection from a 5xx provider outage without ever reading the
# upstream body/text. Every token is a member of this closed set and is NEVER
# derived from raw provider content.

SAFE_ERROR_CLASS_SCHEMA_REJECTED = "SCHEMA_REJECTED"
SAFE_ERROR_CLASS_AUTH_FAILED = "AUTH_FAILED"
SAFE_ERROR_CLASS_NOT_FOUND = "NOT_FOUND"
SAFE_ERROR_CLASS_RATE_LIMITED = "RATE_LIMITED"
SAFE_ERROR_CLASS_UPSTREAM_ERROR = "UPSTREAM_ERROR"
SAFE_ERROR_CLASS_TIMEOUT = "TIMEOUT"
SAFE_ERROR_CLASS_NETWORK = "NETWORK"

CLOSED_SAFE_ERROR_CLASSES: frozenset[str] = frozenset(
    {
        SAFE_ERROR_CLASS_SCHEMA_REJECTED,
        SAFE_ERROR_CLASS_AUTH_FAILED,
        SAFE_ERROR_CLASS_NOT_FOUND,
        SAFE_ERROR_CLASS_RATE_LIMITED,
        SAFE_ERROR_CLASS_UPSTREAM_ERROR,
        SAFE_ERROR_CLASS_TIMEOUT,
        SAFE_ERROR_CLASS_NETWORK,
    }
)

# The status bands that most plausibly mean "the request/schema was rejected"
# (deterministic, documented; never read from the response body).
_SCHEMA_REJECTED_STATUSES: frozenset[int] = frozenset({400, 422})


def frontier_safe_error_class_for_status(
    status_code: int | None,
    *,
    timeout: bool = False,
    network: bool = False,
) -> str | None:
    """Phase31A — deterministic internal status-band -> safe error class.

    Closed mapping (see ``CLOSED_SAFE_ERROR_CLASSES``), sanitized and
    deterministic — never a raw provider message:

    - ``timeout=True``   -> ``"TIMEOUT"``
    - ``network=True``   -> ``"NETWORK"``
    - 400/422            -> ``"SCHEMA_REJECTED"``
    - 401/403            -> ``"AUTH_FAILED"``
    - 404                -> ``"NOT_FOUND"``
    - 429                -> ``"RATE_LIMITED"``
    - 5xx                -> ``"UPSTREAM_ERROR"``
    - anything else      -> ``None`` (default — no claim is made)

    This is an INTERNAL diagnostic only; the public ``failureCode`` stays the
    existing FRONTIER_* vocabulary. ``timeout``/``network`` win over a status
    so a transport-level failure never misreports a status band.
    """
    if timeout:
        return SAFE_ERROR_CLASS_TIMEOUT
    if network:
        return SAFE_ERROR_CLASS_NETWORK
    if status_code in _SCHEMA_REJECTED_STATUSES:
        return SAFE_ERROR_CLASS_SCHEMA_REJECTED
    if status_code in (401, 403):
        return SAFE_ERROR_CLASS_AUTH_FAILED
    if status_code == 404:
        return SAFE_ERROR_CLASS_NOT_FOUND
    if status_code == 429:
        return SAFE_ERROR_CLASS_RATE_LIMITED
    if isinstance(status_code, int) and 500 <= status_code < 600:
        return SAFE_ERROR_CLASS_UPSTREAM_ERROR
    return None


class _ActiveCallState:
    """Where the CURRENT supervised call lives.

    ``box`` holds the private outcome box of the call being executed by THIS
    thread (the worker). The real outbound seam reads it to register the
    dedicated abortable client so the supervisor can reach it; a test-driven
    mock reads it to register its own abort handle (same contract).
    Thread-local: concurrent attempts A/B never cross-talk.
    """

    def __init__(self) -> None:
        self.box: dict[str, Any] | None = None


_ACTIVE_CALL: "threading.local[_ActiveCallState]" = threading.local()


class _FrontierTransportSeam:
    """The module-level ``httpx`` seam of the Frontier adapter.

    ``post`` is the INJECTABLE outbound dispatch. Tests replace it with a
    deterministic ``httpx.post``-compatible mock
    (``fp_mod.httpx.post = mock``); the default implementation performs the
    POST through a dedicated per-call ``httpx.Client`` so the supervising
    caller thread can ABORT the in-flight outbound call at the wall-clock
    deadline (Phase30-fix DEF-020) by closing that client — closing the
    connection interrupts the blocked request/body read instead of merely
    discarding a still-running call.

    The dedicated client is registered in the CURRENT call's outcome box
    (``_ACTIVE_CALL`` thread-local, set by the worker) so the supervisor can
    find it. All other attributes expose the real httpx vocabulary
    (``TimeoutException`` / ``RequestError`` / ``Client`` / ``Response`` / ...)
    so the adapter's typed except clauses and tests behave byte-identically.
    """

    def __init__(self) -> None:
        self.post = self._default_outbound_post
        self.Client = _httpx.Client
        self.Response = _httpx.Response
        self.TimeoutException = _httpx.TimeoutException
        self.RequestError = _httpx.RequestError
        self.ReadTimeout = _httpx.ReadTimeout
        self.RemoteProtocolError = _httpx.RemoteProtocolError
        self.ConnectError = _httpx.ConnectError
        self.StreamClosed = _httpx.StreamClosed

    @staticmethod
    def _default_outbound_post(
        url: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ):
        client = _httpx.Client(timeout=timeout)
        state = getattr(_ACTIVE_CALL, "box", None)
        if state is not None:
            state["abort_client"] = client
        try:
            return client.post(url, json=json, headers=headers, timeout=timeout)
        finally:
            if state is not None and state.get("abort_client") is client:
                state["abort_client"] = None
            try:
                client.close()
            except Exception:  # noqa: BLE001 - the call was already aborted
                pass


# The module-level seam (tests patch ``fp_mod.httpx.post`` exactly as before).
httpx: _FrontierTransportSeam = _FrontierTransportSeam()


class FrontierHttpError(ProviderError):
    """A typed, sanitized Frontier provider failure (Phase 30 §23).

    Carries ``code`` — a canonical ``GenerationFailureCode`` from the closed
    Phase 30 vocabulary (FRONTIER_AUTH_FAILED / FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND /
    FRONTIER_RATE_LIMITED / FRONTIER_TIMEOUT / FRONTIER_PROVIDER_ERROR) — so
    the generation controller can classify the attempt without ever parsing
    provider internals. ``message`` is always sanitized: never a provider
    body, never a header, never a credential, never a request URL (the
    ``httpx`` exception class name would embed the URL and is omitted).

    Phase31A — internal safe diagnostic carriers: ``safe_error_class`` is an
    OPTIONAL closed-token internal classification (``SCHEMA_REJECTED`` /
    ``AUTH_FAILED`` / ``NOT_FOUND`` / ``RATE_LIMITED`` / ``UPSTREAM_ERROR`` /
    ``TIMEOUT`` / ``NETWORK`` — see ``frontier_safe_error_class_for_status``),
    and ``safe_upstream_status`` is the sanitized upstream status INTEGER (the
    only safe upstream signal ever read; the raw body/text is never opened).
    They are diagnostics only: the public ``code`` is never affected.
    """

    def __init__(
        self,
        message: str,
        *,
        code: GenerationFailureCode,
        safe_error_class: str | None = None,
        safe_upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        if safe_error_class is not None and safe_error_class not in CLOSED_SAFE_ERROR_CLASSES:
            safe_error_class = None
        self.safe_error_class = safe_error_class
        self.safe_upstream_status = (
            int(safe_upstream_status) if isinstance(safe_upstream_status, int) else None
        )


def frontier_failure_code_for_status(status_code: int) -> GenerationFailureCode:
    """Phase30 §23 — deterministic status-band -> canonical failure code.

    Closed mapping: 401/403 -> FRONTIER_AUTH_FAILED, 404 ->
    FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND, 429 -> FRONTIER_RATE_LIMITED, 5xx
    (and every OTHER non-2xx status the adapter refuses) ->
    FRONTIER_PROVIDER_ERROR. Timeouts are classified separately by the adapter
    (``httpx.TimeoutException`` -> FRONTIER_TIMEOUT).
    """
    if status_code in (401, 403):
        return GenerationFailureCode.FRONTIER_AUTH_FAILED
    if status_code == 404:
        return GenerationFailureCode.FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND
    if status_code == 429:
        return GenerationFailureCode.FRONTIER_RATE_LIMITED
    return GenerationFailureCode.FRONTIER_PROVIDER_ERROR


class FrontierProvider:
    """Synchronous, bounded OpenAI-compatible hosted-language adapter.

    ``endpoint_url`` is the trusted server-owned registry endpoint (never a
    browser value); ``api_key`` is the user's TRANSIENT per-attempt key and
    ``model`` the user's validated per-attempt model (Phase 30 BYOK).
    The key is never logged, never embedded in an exception and never
    serialized into any DTO / capability response / case material.

    ``clock`` is the monotonic wall-clock source for the Phase30-fix deadline
    (defaults to ``RealClock``; tests inject a manual clock).
    ``structured_output_mode`` is the SERVER-OWNED registry capability
    (``"openai_json_schema"`` or ``None``) — never a browser value.
    """

    def __init__(
        self,
        endpoint_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = DEFAULT_FRONTIER_TIMEOUT_SECONDS,
        sink: Any = None,  # CompletionSink | None (interface compatibility)
        clock: Clock | None = None,
        structured_output_mode: str | None = None,
    ) -> None:
        self._endpoint_url = str(endpoint_url)
        self._api_key = str(api_key)
        self._model = str(model)
        self._timeout_seconds = float(timeout_seconds)
        if (
            not math.isfinite(self._timeout_seconds)
            or self._timeout_seconds <= 0
        ):
            # DEF-022 — defense-in-depth: a non-finite/non-positive adapter
            # timeout would poison ``deadline``/``join`` (nan) or disable the
            # wall-clock bound (inf). Settings already bound 5..300; this keeps
            # the invariant at the adapter surface too.
            raise ValueError(
                "FrontierProvider timeout_seconds must be a finite positive "
                "number"
            )
        self._sink = sink
        self._clock: Clock = clock if clock is not None else RealClock()
        self._structured_output_mode = (
            str(structured_output_mode) if structured_output_mode is not None else None
        )
        # Truthful per-call telemetry: the structured-output mechanism that was
        # actually requested for the LAST call (``"openai_json_schema"`` when a
        # native ``response_format`` was attached, else ``None``). The
        # controller reads it to report ``structuredOutput`` truthfully.
        self.last_structured_output: str | None = None
        # Phase31A — SAFE per-call schema diagnostics, derived ONLY from the
        # server-owned request carrier (``request.json_schema`` / ``schema_id``),
        # never from a provider/browser value. Contents are never stored: only
        # the stable fingerprint, schema-id token, canonical byte length and
        # response-format type token.
        self.last_schema_fingerprint: str | None = None
        self.last_schema_id: str | None = None
        self.last_request_schema_byte_length: int | None = None
        self.last_response_format_type: str | None = None

    # -- Provider protocol ---------------------------------------------------

    def generate(self, request: GenerateRequest) -> ProviderResult:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": self._prompt_for(request)}],
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        timeout_seconds = float(
            request.timeout_seconds
            if request.timeout_seconds is not None
            else self._timeout_seconds
        )
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            # DEF-022 — the effective timeout is always finite and positive
            # (GenerateRequest rejects non-finite/<=0 values; this is the
            # belt-and-braces guard so nan/inf can never reach
            # ``deadline``/``join``). No meaningful call may start.
            raise FrontierHttpError(
                "provider request timed out",
                code=GenerationFailureCode.FRONTIER_TIMEOUT,
                safe_error_class=frontier_safe_error_class_for_status(
                    None, timeout=True
                ),
            )
        # Phase30-fix DEF-B — native structured output from the SERVER-OWNED
        # stage schema only. The schema travels on the request (attached by
        # ``pipeline.build_request`` from the canonical local contract — a
        # parser-shaped stage-output schema, DEF-019); it is sent only when the
        # trusted registry entry declared the matching adapter/protocol
        # capability. The browser never influences this body. A negative case
        # keeps the existing prompt-embedded fallback.
        native_structured = (
            self._structured_output_mode
            == STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA
            and isinstance(request.json_schema, dict)
        )
        if native_structured:
            # Phase31A (Track A) — server-owned protocol adaptation at the
            # TRANSPORT boundary only. ``schema_adapter_id_for_frontier_call``
            # derives the closed adapter family from the validated model +
            # registry capability (``None`` for every non-Cohere model: the
            # canonical schema goes on the wire byte-identical); when an
            # adapter applies, ONLY the deep-copied transport representation
            # changes (Cohere's ">= 1 required property per object" rule for
            # open objects). ``request.json_schema`` itself is never mutated,
            # and the canonical fingerprint/byte-length diagnostics below stay
            # canonical.
            adapter_id = schema_adapter_id_for_frontier_call(
                self._model, self._structured_output_mode
            )
            transport_schema = adapt_schema_for_transport(
                request.json_schema, adapter=adapter_id
            )
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_id or f"generation_{request.stage.value}",
                    "schema": transport_schema,
                },
            }
        self.last_structured_output = (
            _FRONTIER_RESPONSE_FORMAT_JSON_SCHEMA if native_structured else None
        )
        # Phase31A — safe per-call schema diagnostics. Derived ONLY from the
        # LOCAL trusted request carrier (never contents are stored): a stable
        # fingerprint + canonical byte length when the request carried a
        # server-owned schema, plus the schema-id / response-format type
        # tokens when native structured output is actually sent.
        if isinstance(request.json_schema, dict):
            self.last_schema_fingerprint = schema_fingerprint(request.json_schema)
            self.last_request_schema_byte_length = len(
                canonical_schema_bytes(request.json_schema)
            )
        else:
            self.last_schema_fingerprint = None
            self.last_request_schema_byte_length = None
        self.last_schema_id = request.schema_id if native_structured else None
        self.last_response_format_type = (
            _FRONTIER_RESPONSE_FORMAT_JSON_SCHEMA if native_structured else None
        )

        # Phase30-fix DEF-A — real wall-clock deadline around the ENTIRE
        # outbound operation (connect / write / upstream / headers / body /
        # decode). The supervising caller thread is what makes the TOTAL
        # bounded; the worker's own per-phase httpx timeout is only a floor.
        deadline = float(self._clock.now()) + timeout_seconds
        # Outcome box (PRIVATE to this call): ``error``/``result`` are set only
        # by the worker; ``done`` is the "every path completed" contract guard;
        # ``abort_client`` is the dedicated client the supervisor closes at the
        # deadline (DEF-020) or a test-registered abort handle.
        box: dict[str, Any] = {"done": False, "result": None, "error": None}

        def _run() -> None:
            # Associate THIS call with the outbound seam (thread-local), so a
            # real transport registers its dedicated client here and the
            # supervisor can abort it; a test mock uses the same slot.
            state = _ACTIVE_CALL
            state.box = box
            try:
                try:
                    response = httpx.post(
                        self._endpoint_url,
                        json=body,
                        headers=headers,
                        timeout=timeout_seconds,
                    )
                except httpx.TimeoutException:
                    box["error"] = FrontierHttpError(
                        f"provider request timed out after {timeout_seconds}s",
                        code=GenerationFailureCode.FRONTIER_TIMEOUT,
                        safe_error_class=frontier_safe_error_class_for_status(
                            None, timeout=True
                        ),
                    )
                    return
                except httpx.RequestError as exc:
                    # PD-SEC-06: str(exc) may embed the request URL — never
                    # surface it.
                    box["error"] = FrontierHttpError(
                        f"provider request failed: {type(exc).__name__}",
                        code=GenerationFailureCode.FRONTIER_PROVIDER_ERROR,
                        safe_error_class=frontier_safe_error_class_for_status(
                            None, network=True
                        ),
                    )
                    return
                except Exception as exc:  # noqa: BLE001 - DEF-018
                    box["error"] = self._classify_transport_failure(
                        exc, "provider request", deadline
                    )
                    return
                if not (200 <= response.status_code < 300):
                    # PD-SEC-06: the response body may carry provider/vendor
                    # error content — reduce it to the sanitized status band
                    # only (the safe integer + the internal closed-token class
                    # derived from the status; the raw body is never read).
                    box["error"] = FrontierHttpError(
                        f"provider failure: HTTP {response.status_code}",
                        code=frontier_failure_code_for_status(response.status_code),
                        safe_error_class=frontier_safe_error_class_for_status(
                            response.status_code
                        ),
                        safe_upstream_status=int(response.status_code),
                    )
                    return
                try:
                    text = self._read_bounded(response, deadline)
                except FrontierHttpError as exc:
                    box["error"] = exc
                    return
                except Exception as exc:  # noqa: BLE001 - DEF-018
                    # A mid-body/streaming transport failure
                    # (httpx.ReadTimeout / RemoteProtocolError / ConnectError /
                    # OSError ...) reduced to the canonical typed path.
                    box["error"] = self._classify_transport_failure(
                        exc, "provider response body read", deadline
                    )
                    return
                if text is None:
                    box["error"] = FrontierHttpError(
                        "provider response exceeded the size cap",
                        code=GenerationFailureCode.FRONTIER_PROVIDER_ERROR,
                    )
                    return
                # Phase31A (Track B) — envelope content extraction. When the
                # bounded 2xx body is an OpenAI-compatible Chat Completions
                # envelope, ONLY the inner stage-content string becomes the
                # ProviderResult content (the strict stage parser must never
                # see the envelope keys ``choices``/``id``/``model``/``usage``
                # ...); every other body shape passes through UNCHANGED so the
                # Phase 30 golden-mock wires (bare stage JSON / code-fenced
                # docs / malformed fixtures) stay byte-identical. The repair
                # path flows through the SAME extraction — a repair response
                # that arrives as an envelope becomes the bare full draft.
                extracted = extract_openai_chat_completions_content(text)
                box["result"] = ProviderResult(
                    content=text if extracted is None else extracted
                )
            except Exception as exc:  # noqa: BLE001 - absolute safety net
                # DEF-018: NO exception may escape the supervised worker with
                # the box incomplete; every conceivable failure lands as a
                # typed result.
                box["error"] = self._classify_transport_failure(
                    exc,
                    "provider request",
                    deadline,
                )
            finally:
                box["done"] = True
                state.box = None

        worker = threading.Thread(
            target=_run,
            name="frontier-provider-%s" % request.stage.value,
            daemon=True,
        )
        worker.start()
        while worker.is_alive():
            remaining = deadline - float(self._clock.now())
            if remaining <= 0:
                # DEF-020: the wall-clock deadline expired while the outbound
                # call was still in flight — ABORT the in-flight call (close
                # the dedicated client so the blocked request/stream is
                # interrupted and the worker terminates at/before the bound)
                # instead of merely discarding a still-running call, then
                # raise the canonical timeout.
                self._abort_in_flight(box)
                raise FrontierHttpError(
                    f"provider request timed out after {timeout_seconds}s",
                    code=GenerationFailureCode.FRONTIER_TIMEOUT,
                    safe_error_class=frontier_safe_error_class_for_status(
                        None, timeout=True
                    ),
                ) from None
            worker.join(timeout=min(remaining, _WALL_CLOCK_POLL_QUANTUM_SECONDS))
        # The worker finished. DEF-021: success is contractually returned only
        # STRICTLY BEFORE the wall-clock deadline — a completion observed at or
        # after the deadline (e.g. the worker died inside the final join window
        # that began while remaining > 0) is classified as the canonical
        # timeout, never a late success.
        if float(self._clock.now()) >= deadline:
            self._abort_in_flight(box)
            raise FrontierHttpError(
                f"provider request timed out after {timeout_seconds}s",
                code=GenerationFailureCode.FRONTIER_TIMEOUT,
                safe_error_class=frontier_safe_error_class_for_status(
                    None, timeout=True
                ),
            ) from None
        if box["error"] is not None:
            raise box["error"]
        if box["result"] is None:
            # DEF-018 — the Provider protocol NEVER yields None: a worker that
            # ended without a typed outcome (impossible after the safety net,
            # kept as a belt-and-braces invariant) fails closed instead of
            # returning None to the controller.
            raise FrontierHttpError(
                "provider request failed: empty result",
                code=GenerationFailureCode.FRONTIER_PROVIDER_ERROR,
            )
        return box["result"]

    def _classify_transport_failure(
        self, exc: Exception, phase: str, deadline: float
    ) -> FrontierHttpError:
        """DEF-018 — reduce an outbound/body/streaming failure to the canonical
        typed path: ``FRONTIER_TIMEOUT`` when the wall-clock deadline has been
        reached, otherwise ``FRONTIER_PROVIDER_ERROR`` with a sanitized message
        (the exception class name only — never ``str(exc)`` which may embed the
        request URL). The deadline check reads the SAME injected clock the
        supervisor uses, so manual/fake-clock tests stay deterministic.
        """
        if float(self._clock.now()) >= deadline:
            return FrontierHttpError(
                f"{phase} timed out",
                code=GenerationFailureCode.FRONTIER_TIMEOUT,
                safe_error_class=frontier_safe_error_class_for_status(
                    None, timeout=True
                ),
            )
        return FrontierHttpError(
            f"{phase} failed: {type(exc).__name__}",
            code=GenerationFailureCode.FRONTIER_PROVIDER_ERROR,
            safe_error_class=frontier_safe_error_class_for_status(
                None, network=True
            ),
        )

    @staticmethod
    def _abort_in_flight(box: dict[str, Any]) -> None:
        """DEF-020 — abort the in-flight outbound call (best effort).

        The real outbound seam registers the call's dedicated ``httpx.Client``
        in ``box["abort_client"]``; closing it from the supervising thread
        interrupts the blocked request/body read. Test mocks register their own
        abort handle in the same slot (same ``close()``-based contract). The
        call is aborted only after the deadline; its result was structurally
        discarded before this point.
        """
        client = box.get("abort_client")
        box["abort_client"] = None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - abort is best-effort
                pass

    def _read_bounded(self, response: httpx.Response, deadline: float) -> str | None:
        """Read at most ``MAX_FRONTIER_RESPONSE_BYTES`` bytes (PD-SEC-09) but
        never past the wall-clock ``deadline`` (Phase30-fix DEF-A).

        An over-cap body is NOT buffered: reading stops, and the size-cap error
        is returned (clean failure instead of unbounded memory use). When the
        deadline passes mid-read, the wall-clock timeout error is raised so the
        supervised worker exits promptly at the bound instead of dribbling on a
        slow socket long past it.

        DEF-018 — every mid-body/streaming transport exception (``httpx.ReadTimeout``,
        ``httpx.RemoteProtocolError``, ``httpx.ConnectError``, ``OSError``, ...)
        is reduced here to the canonical typed path: ``FRONTIER_TIMEOUT`` when
        the wall-clock deadline has been reached, otherwise
        ``FRONTIER_PROVIDER_ERROR`` with a sanitized message (the exception
        class name only, never ``str(exc)``). No unhandled exception may escape
        the supervised worker.
        """
        total = 0
        chunks: list[bytes] = []
        try:
            for chunk in response.iter_bytes(65536):
                if float(self._clock.now()) >= deadline:
                    raise FrontierHttpError(
                        "provider request timed out",
                        code=GenerationFailureCode.FRONTIER_TIMEOUT,
                        safe_error_class=frontier_safe_error_class_for_status(
                            None, timeout=True
                        ),
                    )
                total += len(chunk)
                if total > MAX_FRONTIER_RESPONSE_BYTES:
                    return None
                chunks.append(chunk)
        except FrontierHttpError:
            raise
        except Exception as exc:  # noqa: BLE001 - DEF-018
            raise self._classify_transport_failure(
                exc, "provider response body read", deadline
            ) from None
        return b"".join(chunks).decode("utf-8", errors="replace")

    # -- private --------------------------------------------------------------

    def _prompt_for(self, request: GenerateRequest) -> str:
        """Sanitized per-stage prompt (no provider-specific types leak)."""
        lines = [
            f"You are generating structured JSON for the "
            f"'{request.stage.value}' stage of a detective case.",
            "Return ONLY a single JSON document matching the documented schema.",
            "",
        ]
        lines.append(request.prompt_context or "(no context)")
        if request.locked is not None:
            lines.append("")
            lines.append("Locked user constraints (must be respected exactly):")
            for field, value in request.locked.locked_fields():
                if value is not None:
                    lines.append(f"- {field}: {value}")
        if request.diagnostics:
            lines.append("")
            lines.append("Repair diagnostics (sanitized):")
            for diagnostic in request.diagnostics:
                lines.append(f"- {diagnostic}")
        return "\n".join(lines)


__all__ = [
    "CLOSED_SAFE_ERROR_CLASSES",
    "DEFAULT_FRONTIER_TIMEOUT_SECONDS",
    "FrontierHttpError",
    "FrontierProvider",
    "MAX_FRONTIER_RESPONSE_BYTES",
    "SAFE_ERROR_CLASS_AUTH_FAILED",
    "SAFE_ERROR_CLASS_NETWORK",
    "SAFE_ERROR_CLASS_NOT_FOUND",
    "SAFE_ERROR_CLASS_RATE_LIMITED",
    "SAFE_ERROR_CLASS_SCHEMA_REJECTED",
    "SAFE_ERROR_CLASS_TIMEOUT",
    "SAFE_ERROR_CLASS_UPSTREAM_ERROR",
    "extract_openai_chat_completions_content",
    "frontier_failure_code_for_status",
    "frontier_safe_error_class_for_status",
]
