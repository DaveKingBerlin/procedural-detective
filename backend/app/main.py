"""Application factory (REQUIREMENTS section 46 layout).

``create_app(settings=None)`` builds a FastAPI application with CORS, the
versioned API router and the structured-error exception handlers. Settings are
injectable so tests can point the app at a temporary DATABASE_URL.
"""

from __future__ import annotations

import logging
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

    app.add_middleware(
        EnvelopeCORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    register_exception_handlers(app)
    return app


# Module-level instance so the documented run command works as-is:
#   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Tests inject their own settings and never rely on this instance.
app = create_app()