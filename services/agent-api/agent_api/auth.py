"""Simple API key authentication.

Validates X-API-Key header against API_KEY env var.
If API_KEY is empty, authentication is disabled (open access).
"""

import hmac
import logging

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from agent_api import config

logger = logging.getLogger("agent_api.auth")

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(api_key: str = Security(API_KEY_HEADER)):
    """FastAPI dependency that rejects requests without a valid API key.

    If API_KEY is not configured, all requests are allowed (dev mode).
    """
    if not config.API_KEY:
        return  # No auth configured — open access
    if not api_key or not hmac.compare_digest(api_key, config.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )
