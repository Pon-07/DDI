"""Unified Authentication router re-export for engine.auth.routers."""

from app.routers.auth import *
from app.routers.auth import router

__all__ = ["router", "auth_service"]
