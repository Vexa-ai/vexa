import hmac
import logging
import os

from fastapi import Depends, HTTPException, Request, status

from ..auth import validate_request, UserProxy

logger = logging.getLogger(__name__)

INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET", "")
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"


async def get_current_user(request: Request) -> UserProxy:
    """Dependency to verify request auth and return a UserProxy.

    Uses the same gateway-header / standalone-key dual-mode auth as the
    main meeting-api auth module.  No admin_models dependency required.
    """
    info = await validate_request(request)
    return UserProxy(info["user_id"], info["max_concurrent"], info["scopes"])


async def require_internal_secret(request: Request) -> None:
    """Guard service-to-service internal routes with the shared INTERNAL_API_SECRET.

    Mirrors admin-api's /internal/validate contract:
    - INTERNAL_API_SECRET unset + DEV_MODE=false  → 503 (fail-closed)
    - INTERNAL_API_SECRET set, X-Internal-Secret absent or mismatched → 403
    - INTERNAL_API_SECRET unset + DEV_MODE=true → allow (local development)

    Closes CVE-2026-25058 / GHSA-w73r-2449-qwgh by rejecting unauthenticated
    callers on previously open routes such as /internal/transcripts/{id}.
    """
    if not DEV_MODE and not INTERNAL_API_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INTERNAL_API_SECRET not configured",
        )
    if INTERNAL_API_SECRET:
        provided = request.headers.get("X-Internal-Secret", "")
        if not hmac.compare_digest(provided, INTERNAL_API_SECRET):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid internal secret",
            )
