import logging
from fastapi import Depends, HTTPException, status, Request

from ..auth import validate_request, UserProxy

logger = logging.getLogger(__name__)


async def get_current_user(request: Request) -> UserProxy:
    """Dependency to verify request auth and return a UserProxy.

    Uses the same gateway-header / standalone-key dual-mode auth as the
    main meeting-api auth module.  No admin_models dependency required.
    """
    info = await validate_request(request)
    return UserProxy(info["user_id"], info["max_concurrent"], info["scopes"])
