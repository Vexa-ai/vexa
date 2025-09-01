from __future__ import annotations

from typing import Awaitable, Callable
import logging
from fastapi import HTTPException
from app.database.service import TranscriptionService
from shared_models.database import async_session_local
from shared_models.models import Meeting

logger = logging.getLogger("bot_manager.orchestrators.common")


async def enforce_user_concurrency_limit(
    user_id: int,
    count_running_bots_for_user: Callable[[], Awaitable[int]],
) -> None:
    """Ensure the user has not exceeded max_concurrent_bots.

    This helper centralizes the shared concurrency enforcement logic.
    The concrete orchestrator supplies an async function that returns the
    number of currently running (and/or pending) bots for the given user.
    """
    user = await TranscriptionService.get_or_create_user(user_id)
    if not user:
        logger.error(f"User with ID {user_id} not found during limit check.")
        raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

    try:
        current_bot_count = await count_running_bots_for_user()
    except Exception as e:  # noqa: BLE001
        logger.error(
            f"Failed to count running bots for user {user_id}: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to verify current bot count.")

    user_limit = getattr(user, "max_concurrent_bots", None)
    logger.info(
        f"[Limit Check] User {user_id}: running/pending bots={current_bot_count}, limit={user_limit}"
    )

    if user_limit is None:
        logger.error(f"User {user_id} missing 'max_concurrent_bots' attribute.")
        raise HTTPException(status_code=500, detail="User configuration error: Bot limit not set.")

    if current_bot_count >= int(user_limit):
        logger.warning(
            f"User {user_id} reached bot limit ({user_limit}). Rejecting new launch."
        )
        raise HTTPException(
            status_code=403,
            detail=f"User has reached the maximum concurrent bot limit ({user_limit}).",
        )

    logger.info(
        f"[Limit Check] User {user_id} under limit ({current_bot_count}/{user_limit})."
    )


async def count_user_active_bots(user_id: int) -> int:
    """Return count of user's meetings that should consume seats.

    Simple and robust: count meetings in statuses that represent an active seat
    and explicitly EXCLUDE 'stopping'. This makes seat freeing immediate on Stop.
    """
    try:
        async with async_session_local() as db:
            result = await db.execute(
                Meeting.__table__.count().where(
                    (Meeting.user_id == user_id) & (Meeting.status.in_(['requested', 'active']))
                )
            )
            count = result.scalar_one() if hasattr(result, 'scalar_one') else result.scalar() or 0
            logger.info(f"[Seat Count] User {user_id}: active/requested meetings (excluding 'stopping') = {count}")
            return int(count)
    except Exception as e:
        logger.warning(f"[Seat Count] Fallback due to DB error for user {user_id}: {e}")
        # Fallback conservatively to 0 so we don't block users due to a read error
        return 0


