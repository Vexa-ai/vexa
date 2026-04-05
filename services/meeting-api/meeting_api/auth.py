"""Dual-mode auth: gateway headers (Vexa deployment) or standalone API keys."""

import os
import logging
from fastapi import HTTPException, Request, Depends
from fastapi.security import APIKeyHeader

logger = logging.getLogger("meeting_api.auth")

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
API_KEYS = [k.strip() for k in os.getenv("API_KEYS", "").split(",") if k.strip()]


class UserProxy:
    """Minimal user-like object for backward compat with existing endpoint code."""
    def __init__(self, user_id, max_concurrent, scopes):
        self.id = user_id
        self.max_concurrent_bots = max_concurrent
        self.data = {}
        self.email = f"user-{user_id}"
        self.scopes = scopes


async def validate_request(request: Request) -> dict:
    """Returns {user_id, scopes, max_concurrent} or raises 401/403."""
    # 1. Gateway mode: trusted headers (set by api-gateway after token validation)
    user_id = request.headers.get("X-User-ID")
    if user_id:
        return {
            "user_id": int(user_id),
            "scopes": request.headers.get("X-User-Scopes", "").split(","),
            "max_concurrent": int(request.headers.get("X-User-Limits", "1")),
        }

    # 2. Standalone mode: API key check
    api_key = request.headers.get("X-API-Key", "")
    if API_KEYS:
        if not api_key or api_key not in API_KEYS:
            raise HTTPException(status_code=403, detail="Invalid or missing API key")
        return {"user_id": 0, "scopes": ["*"], "max_concurrent": 999}

    # 3. No auth configured (dev mode)
    if not API_KEYS:
        return {"user_id": 0, "scopes": ["*"], "max_concurrent": 999}

    raise HTTPException(status_code=401, detail="Authentication required")


async def get_user_and_token(request: Request) -> tuple:
    """Backward-compatible wrapper: returns (api_key, UserProxy) for existing code."""
    info = await validate_request(request)
    api_key = request.headers.get("X-API-Key", "")
    user = UserProxy(info["user_id"], info["max_concurrent"], info["scopes"])
    return (api_key, user)
