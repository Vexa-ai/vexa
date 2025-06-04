import logging
from fastapi import Depends, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Relative import for API_KEY_NAME from the service's config.py
from config import API_KEY_NAME
# Imports from shared libraries
from shared_models.database import get_db
from shared_models.models import APIToken, User

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_current_user(api_key: str = Security(api_key_header),
                           db: AsyncSession = Depends(get_db)) -> User:
    """Dependency to verify X-API-Key and return the associated User."""
    if not api_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing API token")

    # Find the token in the database
    result = await db.execute(
        select(APIToken, User)
        .join(User, APIToken.user_id == User.id)
        .where(APIToken.token == api_key)
    )
    token_user = result.first()

    if not token_user:
        logger.warning(f"Invalid API token provided: {api_key[:10]}...")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API token"
        )

    _token_obj, user_obj = token_user
    return user_obj 