"""Version 1 API namespace. All public routes live under ``/api/v1``."""

from fastapi import APIRouter

from app.api.v1.health import router as health_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)