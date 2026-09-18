"""Version 1 API namespace. All public routes live under ``/api/v1``."""

from fastapi import APIRouter

from app.api.v1.cases import router as cases_router
from app.api.v1.generation_capabilities import router as generation_capabilities_router
from app.api.v1.generations import router as generations_router
from app.api.v1.health import router as health_router
from app.api.v1.investigation import router as investigation_router
from app.api.v1.playthroughs import router as playthroughs_router
from app.api.v1.sessions import router as sessions_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(sessions_router)
api_router.include_router(cases_router)
api_router.include_router(generations_router)
api_router.include_router(playthroughs_router)
api_router.include_router(investigation_router)
api_router.include_router(generation_capabilities_router)