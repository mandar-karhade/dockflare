"""Aggregates all v1 API routers."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.containers import router as containers_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.health import router as health_router
from app.api.v1.tunnels import router as tunnels_router
from app.api.v1.zones import router as zones_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router, tags=["health"])
api_router.include_router(dashboard_router)
api_router.include_router(containers_router)
api_router.include_router(tunnels_router)
api_router.include_router(zones_router)
