"""Application factory (REQUIREMENTS section 46 layout).

``create_app(settings=None)`` builds a FastAPI application with CORS, the
versioned API router and the structured-error exception handlers. Settings are
injectable so tests can point the app at a temporary DATABASE_URL.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.api.v1 import api_router
from app.core.config import SERVICE_NAME, SERVICE_VERSION, Settings
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


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Build the FastAPI application.

    ``settings`` is injected by tests; when None the canonical ``Settings()``
    singleton (env vars + optional .env) is used as the one config source.
    """
    settings = settings if settings is not None else Settings()

    app = FastAPI(
        title="Procedural Detective API",
        description="Backend API for the Procedural Detective hackathon application.",
        version=SERVICE_VERSION,
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

    app.include_router(api_router)
    register_exception_handlers(app)
    return app


# Module-level instance so the documented run command works as-is:
#   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Tests inject their own settings and never rely on this instance.
app = create_app()