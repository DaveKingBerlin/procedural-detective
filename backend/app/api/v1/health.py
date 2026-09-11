"""Health and readiness endpoints (shared Phase 2 contract)."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.engine import Engine

from app.db.session import readiness_status
from app.schemas.errors import ErrorResponse
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])

NOT_READY_MESSAGE = {
    "database": "database unavailable",
    "migrations": "migrations not applied",
}


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Pure liveness check. Never touches the database.",
)
def health() -> HealthResponse:
    """Return the fixed liveness contract body."""
    return HealthResponse()


@router.get(
    "/readiness",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Reports ready only when the configured database accepts a real "
        "connection (SELECT 1) and Alembic is at the head revision."
    ),
    responses={
        503: {
            "model": ErrorResponse,
            "description": "Database unreachable or migrations not at head revision.",
        }
    },
)
def readiness(request: Request) -> ReadinessResponse:
    """Probe the database and the migration state."""
    engine: Engine = request.app.state.engine
    status = readiness_status(engine)
    ready = status == {"database": "ok", "migrations": "ok"}
    if ready:
        return ReadinessResponse(status="ready", database="ok", migrations="ok")
    message = NOT_READY_MESSAGE.get(
        "database" if status.get("database") != "ok" else "migrations",
        "service not ready",
    )
    raise HTTPException(
        status_code=503,
        detail={"code": "NOT_READY", "message": message, "details": status},
    )