"""Application factory (REQUIREMENTS section 46 layout).

``create_app(settings=None)`` builds a FastAPI application with CORS, the
versioned API router and the structured-error exception handlers. Settings are
injectable so tests can point the app at a temporary DATABASE_URL.

Phase 8 additions (deployment hardening, Phase8 H/I/J):

- ``CacheControlMiddleware`` — every private/authenticated ``/api/v1``
  response (sessions, cases, generations, playthroughs, investigation,
  accusation and reveal) carries ``Cache-Control: no-store`` (+ ``Pragma:
  no-cache``). Bearer credentials, private case data and the canonical truth
  must never be stored by a browser or an intermediary cache. The public
  probes (``/api/v1/health``, ``/api/v1/readiness``), ``/openapi.json`` and
  ``/docs`` stay outside that rule.
- Static/SPA serving — when ``STATIC_DIR`` is set, the app serves the built
  frontend at ``/`` with an index.html fallback for SPA routes
  (``/scene``, ``/accuse``, ``/reveal``) and immutable-cached ``/assets``.
  With ``STATIC_DIR`` unset the API-only surface is byte-for-byte unchanged
  (local dev keeps Vite as the frontend server).
- Lifespan shutdown — the SQLite engines (readiness + store) are disposed on
  graceful shutdown so the container releases the database file cleanly.

Phase 18A addition (submission trust / release hygiene):

- ``SecurityHeadersMiddleware`` — security headers on EVERY HTTP
  response. ``Content-Security-Policy`` is the Phase 20 (PD-SEC-08) baseline:
  ``default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;
  font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self';
  frame-ancestors 'none'; form-action 'self'; script-src 'self'`` — WITHOUT
  ``'unsafe-eval'`` (the production Vite/Babylon build was verified to contain
  no ``eval()``/``new Function``/``WebAssembly`` usage, so strict script-src is
  safe; the Babylon texture-loading chunks that construct blob workers are
  NOT imported by the app and stay lazy-unloaded, so no blob:/worker-src
  whitelist is required today — adapt only if the browser suite proves a
  texture path needs it) plus ``X-Frame-Options: DENY``,
  ``X-Content-Type-Options: nosniff``, ``Referrer-Policy:
  strict-origin-when-cross-origin`` and a deny-all ``Permissions-Policy``.

Phase 20 additions (PD-SEC-02 / PD-SEC-06):

- ``anon_session_limiter`` + ``generation_ip_limiter`` on ``app.state`` — the
  bounded public admission policies for ``POST /sessions/anonymous``
  (per-IP + global ceiling) and ``POST /cases`` (per-IP hourly budget),
  keyed on the ``TRUST_PROXY``-aware resolved client IP. Deployment mode
  (§8.1): single-process hackathon hosting; in-memory rate state is accepted
  and NO multi-replica claim is made.
- ``enforce_production_trace_policy`` — production startup REJECTS
  ``PD_DEV_TRACE=true`` / ``PD_GENERATION_DEBUG_LOGS=true`` (PD-SEC-06).
"""

from __future__ import annotations

import json
import logging
import mimetypes
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.api.v1 import api_router
from app.core.config import SERVICE_NAME, SERVICE_VERSION, Settings
from app.core.observability import (
    configure_logging,
    enforce_production_trace_policy,
)
from app.core.ratelimit import AnonymousSessionLimiter, SlidingWindowRateLimiter
from app.db.session import create_db_engine
from app.persistence.store import Store
from app.persistence.timebase import EpochClock
from app.services.generation import GenerationService
from app.services.publication import PublicationService

logger = logging.getLogger(SERVICE_NAME)

_STATUS_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    429: "TOO_MANY_REQUESTS",
}


def _error_body(code: str, message: str, details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


class RequestBodySizeMiddleware:
    """ASGI middleware enforcing ``Settings.max_request_body_size`` (DEF-048).

    ``MAX_REQUEST_BODY_SIZE`` was configured but never enforced: oversized
    bodies were fully buffered and only later rejected by validation. This
    middleware closes that gap:

    - When the request declares a ``Content-Length`` greater than the bound,
      it responds immediately with the 413 ``REQUEST_TOO_LARGE`` error
      envelope and the route is NEVER invoked (no receive reads happen).
    - When no Content-Length is declared (chunked/unknown bodies), the
      middleware pre-reads at most ``max_body_bytes + 1`` bytes: if the body
      crosses the bound it answers 413 WITHOUT ever invoking the application
      (draining only when a mid-stream chunk proves more body is coming;
      a terminal over-bound message answers 413 immediately — awaiting a
      receive() after the request is complete would hang uvicorn); otherwise
      the consumed messages are replayed to the application unchanged
      (bounded buffering <= bound + one chunk, documented).

    Every other request path (GET, small bodies, websockets, ...) is
    unaffected. The response uses the shared error envelope so every non-2xx
    stays ``{"error": {...}}``.
    """

    def __init__(self, app: Any, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = int(max_body_bytes)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or self.max_body_bytes <= 0:
            await self.app(scope, receive, send)
            return
        content_length = self._declared_content_length(scope)
        if content_length is not None:
            if content_length > self.max_body_bytes:
                await self._send_413(send)
                return
            await self.app(scope, receive, send)
            return
        await self._streaming_capped_call(scope, receive, send)

    @staticmethod
    def _declared_content_length(scope) -> int | None:
        for name, value in scope.get("headers") or ():
            if name.lower() == b"content-length":
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

    async def _streaming_capped_call(self, scope, receive, send) -> None:
        """Bounded pre-read for chunked/unknown-length bodies.

        The cap is checked on EVERY ``http.request`` message — INCLUDING the
        terminal one (``more_body=False``). uvicorn can coalesce an entire
        one-write chunked body into a single terminal message, and a body that
        crosses the bound exactly on its last chunk was previously forwarded
        to the application (DEF-048 bypass).

        Drain discipline (DEF-048 hang): ``await receive()`` after a TERMINAL
        ``more_body=False`` message would block forever in uvicorn (the ASGI
        flow-control waits for the next http.request that never comes), so:

        - over-bound message with ``more_body=True``  -> the stream really is
          incomplete: drain the remaining body (bounded receive loop), then 413;
        - over-bound TERMINAL message (``more_body=False``) -> NEVER call
          ``receive()`` again: send the 413 envelope immediately and return.

        In both shapes the application is never invoked.
        """
        messages: deque = deque()
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                messages.append(message)
                if message["type"] == "http.disconnect":
                    break
                continue
            messages.append(message)
            body = message.get("body", b"") or b""
            total += len(body)
            if total > self.max_body_bytes:
                if message.get("more_body", False):
                    # Genuinely more body is coming; consume it (bounded).
                    await self._drain_body(receive)
                # Terminal over-bound: send 413 immediately; NEVER receive
                # again after the request is complete.
                await self._send_413(send)
                return
            if not message.get("more_body", False):
                break
        async def replayed_receive():
            return messages.popleft() if messages else {
                "type": "http.request", "body": b"", "more_body": False
            }

        await self.app(scope, replayed_receive, send)

    @staticmethod
    async def _drain_body(receive) -> None:
        """Consume the rest of an oversized request body (never buffered)."""
        while True:
            drain = await receive()
            if drain["type"] == "http.request" and not drain.get("more_body", False):
                return
            if drain["type"] == "http.disconnect":
                return

    @staticmethod
    async def _send_413(send) -> None:
        body = json.dumps(
            _error_body("REQUEST_TOO_LARGE", "Request body too large", None),
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


class EnvelopeCORSMiddleware(CORSMiddleware):
    """CORSMiddleware that rejects disallowed-origin preflights with the shared
    error envelope instead of Starlette's plain-text 400 (DEF-014).

    A disallowed preflight now answers ``400`` with
    ``{"error":{"code":"CORS_ORIGIN_NOT_ALLOWED", ...}}`` and no ACAO header.
    Every other behavior is inherited unchanged: allowed preflights still get
    the ACAO headers, and simple requests are untouched (a disallowed simple
    request still just omits ACAO so the browser blocks it).
    """

    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code == 400 and not self.is_allowed_origin(request_headers["origin"]):
            return JSONResponse(
                status_code=400,
                content=_error_body(
                    "CORS_ORIGIN_NOT_ALLOWED",
                    "Origin %s is not allowed" % request_headers["origin"],
                    None,
                ),
            )
        return response


# --------------------------------------------------------------------------- #
# Phase 8 H1 — cache safety for every private/authenticated response
# --------------------------------------------------------------------------- #

# Every /api/v1 endpoint EXCEPT the public probes can
# answer with bearer credentials, private case material, evidence or the
# canonical truth. None of it may ever be stored by a browser or a shared
# intermediary cache. The generation-capabilities endpoint is public
# (auth-free) capability metadata like health/readiness.
_PRIVATE_API_PREFIX = "/api/v1/"
_PUBLIC_PROBE_PATHS = frozenset(
    {
        "/api/v1/health",
        "/api/v1/readiness",
        "/api/v1/generation-capabilities",
    }
)

_NO_STORE_HEADERS = (("cache-control", "no-store"), ("pragma", "no-cache"))


def is_private_api_path(path: str) -> bool:
    """True for private/authenticated API paths that must never be cached.

    Every path under ``/api/v1/`` other than the public probes (health /
    readiness / generation-capabilities) carries bearer-accessed or
    token-bearing content: sessions, cases, generations, playthroughs,
    investigation, accusation and reveal. ``/openapi.json``, ``/docs`` and
    ``/redoc`` are outside ``/api/v1`` and are intentionally NOT treated as
    private.
    """
    if not isinstance(path, str) or not path.startswith(_PRIVATE_API_PREFIX):
        return False
    return path.rstrip("/") not in _PUBLIC_PROBE_PATHS


class CacheControlMiddleware:
    """ASGI middleware: ``Cache-Control: no-store`` on private responses.

    Applied to the response ``send`` call of every request whose path is a
    private API path, so it works for EVERY status (200/4xx/5xx) without
    touching route handlers. Header values are appended, never overwriting an
    explicit more-specific cache-control a route may have set.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not is_private_api_path(scope.get("path") or ""):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _NO_STORE_HEADERS:
                    headers.append(name, value)
            await send(message)

        await self.app(scope, receive, send_no_store)


# --------------------------------------------------------------------------- #
# Phase 18A + Phase 20 (PD-SEC-08) — security headers + full baseline CSP
# --------------------------------------------------------------------------- #
#
# The app is served SAME-ORIGIN (single container: FastAPI + built SPA) and
# never needs to be embedded in a third-party frame, so framing is denied.
#
# PD-SEC-08: a FULL source-restricting CSP baseline is now sent (defense in
# depth; Phase 20 §19):
#
#   default-src 'self'                       — everything else falls back to self
#   script-src 'self'                        — NO 'unsafe-eval': the production
#                                              Vite/Babylon build was scanned and
#                                              contains NO eval()/new Function/
#                                              WebAssembly usage; index.html loads
#                                              the app via an external module
#                                              script only (no inline scripts).
#                                              The Babylon KTX2/basis texture
#                                              loader chunks (which construct
#                                              blob workers) are NOT imported by
#                                              the app and remain lazy-unloaded,
#                                              so no blob:/worker-src whitelist
#                                              is required; adapt the whitelist
#                                              ONLY if the browser suite proves a
#                                              texture path needs it (§19).
#   style-src 'self' 'unsafe-inline'         — Vite ships an external stylesheet;
#                                              Babylon/React inject inline style
#                                              attributes (style-src-attr).
#   img-src 'self' data:                     — Babylon procedural/dynamic
#                                              textures use data: URLs.
#   font-src 'self'                          — bundled fonts only.
#   connect-src 'self'                       — same-origin API (PD-SEC-04).
#   object-src 'none'                        — no plugins/embeds.
#   base-uri 'self'                          — no base-tag hijack.
#   frame-ancestors 'none'                   — framing denied (level-3).
#   form-action 'self'                       — forms only submit same-origin.
#
# The remaining headers are advisory/non-restrictive and cannot break
# rendering:
#  - X-Frame-Options: DENY — older-browser framing equivalent;
#  - X-Content-Type-Options: nosniff — don't MIME-sniff API/SPA responses;
#  - Referrer-Policy: strict-origin-when-cross-origin — never leak query
#    strings cross-origin (the SPA uses query params only on its own origin);
#  - Permissions-Policy — deny camera/microphone/geolocation (unused).

_CSP_BASELINE = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

_SECURITY_HEADERS = (
    ("content-security-policy", _CSP_BASELINE),
    ("x-frame-options", "DENY"),
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "strict-origin-when-cross-origin"),
    ("permissions-policy", "camera=(), microphone=(), geolocation=()"),
)


class SecurityHeadersMiddleware:
    """ASGI middleware: minimal security headers on every HTTP response.

    Applied at the TRANSPORT BOUNDARY — wrapped around the entire built
    middleware stack (``ServerErrorMiddleware`` included) — so the headers are
    present on every response: API success + error envelopes (the unhandled
    ``/boom``-class 500 envelopes included), static/SPA files, CORS preflights
    and Starlette's structurally-outermost ``ServerErrorMiddleware`` responses
    alike (ADV-204). Header values are appended only when absent (an explicit
    route-level header is never overwritten).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS:
                    if name not in headers:
                        headers.append(name, value)
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class SecurityHeadersFastAPI(FastAPI):
    """FastAPI variant that applies the security headers at the transport edge.

    ADV-204: an UNHANDLED route exception is caught by Starlette's
    ``ServerErrorMiddleware``, which ALWAYS sits above every ``add_middleware``
    registration in ``build_middleware_stack`` and emits its sanitized 500
    envelope directly to the transport — the response never flows back through
    middleware registered on the app. Wrapping the ENTIRE built stack (the
    ``ServerErrorMiddleware`` included) with ``SecurityHeadersMiddleware``
    guarantees EVERY response — the pure-exception 500 envelopes included —
    carries the same minimal security headers as every other path, while the
    sanitized ``INTERNAL_ERROR`` envelope behavior (DEF-053) is untouched (the
    wrapper only adds headers, never touches the body).
    """

    def build_middleware_stack(self):
        stack = super().build_middleware_stack()
        return SecurityHeadersMiddleware(stack)


# --------------------------------------------------------------------------- #
# Phase 8 J2 — static/SPA serving (only when STATIC_DIR is configured)
# --------------------------------------------------------------------------- #

_SPA_FALLBACK_EXCLUDED_PREFIXES = ("/api/",)
_SPA_FALLBACK_EXCLUDED_PATHS = frozenset({"/openapi.json", "/docs", "/redoc"})


def _safe_static_path(root: Path, relative: str) -> Path | None:
    """Resolve ``relative`` inside ``root`` or return None (path traversal)."""
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _index_html_response(root: Path) -> Response:
    """index.html with Cache-Control: no-store (Phase8 H1/H2: the SPA shell is
    token/state-free but its own JS refetches everything from the private API;
    the shell itself must still never be cached by a shared proxy)."""
    index = root / "index.html"
    try:
        body = index.read_bytes()
    except OSError:
        return JSONResponse(
            status_code=404,
            content=_error_body("NOT_FOUND", "Not found", None),
        )
    return Response(
        content=body,
        media_type="text/html",
        headers={
            "cache-control": "no-store",
            "pragma": "no-cache",
        },
    )


def _asset_response(root: Path, relative: str) -> Response:
    """One built asset under ``/assets`` — content-hashed by Vite, so the
    requested name is immutable: ``Cache-Control: public, max-age=31536000,
    immutable`` (Phase8 J2)."""
    path = _safe_static_path(root / "assets", relative)
    if path is None:
        return JSONResponse(
            status_code=404,
            content=_error_body("NOT_FOUND", "Not found", None),
        )
    content_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(
        path,
        media_type=content_type or "application/octet-stream",
        headers={"cache-control": "public, max-age=31536000, immutable"},
    )


def _configure_static_serving(app: FastAPI, settings: Settings) -> None:
    """Register the production SPA routes when a build directory is configured.

    Only active when ``settings.static_dir`` is set (container/production).
    Local dev (no STATIC_DIR) keeps Vite as the frontend server and the API
    surface exactly as before — every pre-existing test uses that mode.

    Routes are registered AFTER ``api_router`` so ``/api/v1/*`` is always
    resolved by the real API first; the ``{path:path}`` fallback explicitly
    refuses to shadow unknown API/doc paths (an unknown /api path keeps the
    404 error envelope instead of returning index.html).
    """
    if settings.static_dir is None:
        return
    root = Path(settings.static_dir).resolve()
    app.state.static_dir = root

    @app.get("/", include_in_schema=False)
    def serve_index() -> Response:  # noqa: ANN201
        return _index_html_response(root)

    @app.get("/assets/{asset_path:path}", include_in_schema=False)
    def serve_asset(asset_path: str) -> Response:  # noqa: ANN201
        return _asset_response(root, asset_path)

    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str) -> Response:  # noqa: ANN201
        # ``path`` carries NO leading slash (Starlette path param), so rebuild
        # the full path before the exclusion tests.
        full_path = "/" + path if path else "/"
        if full_path.startswith(_SPA_FALLBACK_EXCLUDED_PREFIXES):
            raise StarletteHTTPException(
                status_code=404, detail={"code": "NOT_FOUND", "message": "Not found", "details": None}
            )
        if full_path in _SPA_FALLBACK_EXCLUDED_PATHS:
            raise StarletteHTTPException(
                status_code=404, detail={"code": "NOT_FOUND", "message": "Not found", "details": None}
            )
        return _index_html_response(root)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Register every HTTPException under the shared error envelope.

    Registered for ``StarletteHTTPException`` so it also overrides routing-level
    errors (404 Not Found, 405 Method Not Allowed), which Starlette raises as
    ``starlette.exceptions.HTTPException``. ``fastapi.HTTPException`` is a
    subclass of it, so endpoint-raised HTTPExceptions use the same handler.

    A dict ``detail`` may carry ``{"code", "message", "details"}`` for
    endpoints that need full control (e.g. readiness NOT_READY).
    """
    code = _STATUS_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
    message = "Request failed"
    details = None
    if isinstance(exc.detail, dict):
        code = exc.detail.get("code", code)
        message = exc.detail.get("message", message)
        details = exc.detail.get("details")
    else:
        message = str(exc.detail) if exc.detail else message
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code, message, details),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422s (query/body/path validation) use the same envelope.

    The ``input`` value of each error is stripped so client-supplied values
    (potentially sensitive) are never echoed back to the response.
    """
    errors = [
        {key: value for key, value in err.items() if key != "input"}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=_error_body("VALIDATION_ERROR", "Request validation failed", {"errors": errors}),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Unhandled exceptions become 500 INTERNAL_ERROR with no leak of the
    traceback or the original message. The exception is logged server-side."""
    logger.exception("Unhandled application error: %s", exc)
    return JSONResponse(
        status_code=500,
        content=_error_body("INTERNAL_ERROR", "Internal server error", None),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the structured-error handlers (exported for reuse in tests)."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Lifespan: dispose the SQLite engines on graceful shutdown (Phase8 I2).

    uvicorn runs the lifespan shutdown on SIGINT/SIGTERM, releasing the
    SQLite file handle so an external watchdog can restart or back up the
    volume cleanly. ``dispose()`` is idempotent, so tests that dispose
    explicitly after the TestClient context remain correct.
    """
    yield
    engine = getattr(app.state, "engine", None)
    if engine is not None:
        try:
            engine.dispose()
        except Exception:  # noqa: BLE001 - shutdown must never mask a failure
            logger.exception("Error disposing the readiness engine during shutdown")
    store = getattr(app.state, "store", None)
    if store is not None:
        try:
            store.dispose()
        except Exception:  # noqa: BLE001 - shutdown must never mask a failure
            logger.exception("Error disposing the store engine during shutdown")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Build the FastAPI application.

    ``settings`` is injected by tests; when None the canonical ``Settings()``
    singleton (env vars + optional .env) is used as the one config source.
    """
    settings = settings if settings is not None else Settings()
    # PD-SEC-06: production must never run with development trace logging. The
    # phase-preferred policy is to REJECT startup with a clear sanitized error
    # (see ``enforce_production_trace_policy`` in app/core/observability.py).
    enforce_production_trace_policy(settings)
    configure_logging(settings)

    app = SecurityHeadersFastAPI(
        title="Procedural Detective API",
        description="Backend API for the Procedural Detective hackathon application.",
        version=SERVICE_VERSION,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.engine = create_db_engine(settings)

    # -- Phase 5 wiring: store + services (INVARIANT 7: SQLite is the only
    # authoritative state; process memory is never authoritative) ----------
    app.state.clock = EpochClock()
    app.state.store = Store(settings.database_url)
    app.state.publication_service = PublicationService(app.state.store)
    app.state.generation_service = GenerationService(
        settings=settings,
        store=app.state.store,
        clock=app.state.clock,
        publication=app.state.publication_service,
    )
    # PD-SEC-02 — bounded public admission policies (single-process in-memory
    # rate state, documented deployment mode §8.1). Keyed on the
    # TRUST_PROXY-aware resolved client IP; atomic under each limiter's lock.
    app.state.anon_session_limiter = AnonymousSessionLimiter(
        clock=app.state.clock,
        per_ip_limit=settings.anon_session_limit_per_ip_per_10_min,
        per_ip_window_seconds=10 * 60,
        global_limit=settings.anon_session_global_limit_per_min,
        global_window_seconds=60,
    )
    app.state.generation_ip_limiter = SlidingWindowRateLimiter(
        clock=app.state.clock,
        limit=settings.generation_limit_per_ip_per_hour,
        window_seconds=60 * 60,
    )
    # Phase 21 F-02 — bounded playthrough creation budgets (in-memory rolling
    # 1-hour windows; the SAME single-process deployment mode as the Phase 20
    # rate state, and the SAME F-05 bounded identity map). The per-creator
    # identity is the stored SHA-256 token verifier (never the raw token, and
    # never the creator identity exposed anywhere). The DURABLE per-case caps
    # (MAX_ACTIVE/MAX_RETAINED) are enforced atomically in the store's create
    # transaction instead — the in-memory budgets only throttle minting rate.
    app.state.playthrough_creator_limiter = SlidingWindowRateLimiter(
        clock=app.state.clock,
        limit=settings.playthrough_create_limit_per_creator,
        window_seconds=60 * 60,
    )
    app.state.playthrough_ip_limiter = SlidingWindowRateLimiter(
        clock=app.state.clock,
        limit=settings.playthrough_create_limit_per_ip,
        window_seconds=60 * 60,
    )

    app.add_middleware(
        EnvelopeCORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # DEF-048: MAX_REQUEST_BODY_SIZE (REQUIREMENTS 45) is now enforced as an
    # early 413 envelope — the route is never invoked and no oversized body is
    # buffered.
    app.add_middleware(
        RequestBodySizeMiddleware,
        max_body_bytes=settings.max_request_body_size,
    )
    # Phase 8 H1: no-store on every private/authenticated response (so it also
    # covers error envelopes and never blocks CORS header flow).
    app.add_middleware(CacheControlMiddleware)
    # Phase 18A: minimal security headers are applied at the TRANSPORT BOUNDARY
    # via ``SecurityHeadersFastAPI.build_middleware_stack`` (the wrapper sits
    # OUTSIDE Starlette's ``ServerErrorMiddleware``, so even the sanitized
    # unhandled-exception 500 envelopes carry them — ADV-204). Nothing is
    # registered here: ``add_middleware`` would land INSIDE
    # ``ServerErrorMiddleware`` and miss exactly those responses.

    app.include_router(api_router)
    _configure_static_serving(app, settings)
    register_exception_handlers(app)
    return app


# Module-level instance so the documented run command works as-is:
#   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
# The --no-proxy-headers flag is load-bearing (DEF-094): uvicorn's platform
# default --proxy-headers trusts loopback and rewrites request.client from a
# hostile X-Forwarded-For BEFORE the app's TRUST_PROXY=false identity gate.
# Tests inject their own settings and never rely on this instance.
app = create_app()
