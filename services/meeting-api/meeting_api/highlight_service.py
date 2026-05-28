"""Highlight service - CRUD + clip generation for Phase 2 MVP."""

import logging
import secrets

from sqlalchemy import select

from .models import Highlight

logger = logging.getLogger("meeting_api.highlights")


async def create_highlight(
    db,
    meeting_id: int,
    start_time: float,
    end_time: float,
    title: str = None,
    summary: str = None,
    highlight_type: str = "custom",
    speaker: str = None,
    source: str = "manual",
):
    h = Highlight(
        meeting_id=meeting_id,
        start_time=start_time,
        end_time=end_time,
        title=title,
        summary=summary,
        type=highlight_type,
        speaker=speaker,
        source=source,
    )
    db.add(h)
    await db.commit()
    await db.refresh(h)
    logger.info(f"Created highlight {h.id} for meeting {meeting_id}")
    return h


async def get_highlights_for_meeting(meeting_id: int, db):
    result = await db.execute(
        select(Highlight).where(Highlight.meeting_id == meeting_id).order_by(Highlight.start_time)
    )
    return result.scalars().all()


async def get_highlight_by_id(highlight_id: int, db):
    return await db.get(Highlight, highlight_id)


async def get_highlight_by_token(clip_token: str, db):
    result = await db.execute(
        select(Highlight).where(Highlight.clip_token == clip_token)
    )
    return result.scalar_one_or_none()


async def update_highlight(db, highlight_id: int, updates: dict):
    h = await db.get(Highlight, highlight_id)
    if not h:
        return None
    for key, value in updates.items():
        if hasattr(h, key):
            setattr(h, key, value)
    await db.commit()
    await db.refresh(h)
    return h


async def delete_highlight(db, highlight_id: int) -> bool:
    h = await db.get(Highlight, highlight_id)
    if not h:
        return False
    await db.delete(h)
    await db.commit()
    return True


async def generate_clip(highlight: Highlight, db) -> str:
    """Generate a secure share token for the highlight clip."""
    token = secrets.token_urlsafe(32)
    highlight.clip_token = token
    await db.commit()
    logger.info(f"Generated clip token {token[:12]}... for highlight {highlight.id}")
    return token
