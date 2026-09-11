"""Response DTOs (schemas). Persistence/domain objects are never serialized."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """GET /api/v1/health — pure liveness; never touches the database."""

    status: Literal["ok"] = "ok"
    service: Literal["procedural-detective"] = "procedural-detective"
    version: str = "0.1.0"


class ReadinessResponse(BaseModel):
    """GET /api/v1/readiness — database reachable and migrations at head."""

    status: Literal["ready"] = "ready"
    database: Literal["ok"] = "ok"
    migrations: Literal["ok"] = "ok"