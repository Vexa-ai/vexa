"""Calendar sync loop — polls Google Calendar, upserts events, schedules bots."""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from meeting_api.models import CalendarEvent
from admin_models.models import User
from app.google_calendar import (
    refresh_access_token,
    list_events,
    extract_meeting_url,
    detect_platform,
    parse_event_time,
)

logger = logging.getLogger("calendar-service.sync")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
MEETING_API_URL = os.getenv("MEETING_API_URL", "http://meeting-api:8080")
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")
DEFAULT_LEAD_TIME_MINUTES = int(os.getenv("DEFAULT_LEAD_TIME_MINUTES", "2"))


async def sync_user_calendar(user_id: int, db: AsyncSession) -> int:
    """Sync a single user's Google Calendar events. Returns count of upserted events."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"User {user_id} not found")
        return 0

    user_data = user.data or {}
    gc_data = user_data.get("google_calendar", {})
    oauth = gc_data.get("oauth", {})
    refresh_token = oauth.get("refresh_token")
    if not refresh_token:
        logger.info(f"User {user_id} has no Google Calendar refresh token")
        return 0

    # Refresh access token
    access_token, expires_in = await refresh_access_token(
        GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, refresh_token
    )

    # Get existing sync token for incremental sync
    existing_sync_token = gc_data.get("sync_token")

    time_min = datetime.now(timezone.utc)
    time_max = time_min + timedelta(days=7)

    api_response = await list_events(
        access_token,
        time_min=time_min,
        time_max=time_max,
        sync_token=existing_sync_token,
    )

    if api_response.get("fullSyncRequired"):
        logger.info(f"Full sync required for user {user_id}, clearing sync token")
        api_response = await list_events(
            access_token, time_min=time_min, time_max=time_max
        )

    events = api_response.get("items", [])
    next_sync_token = api_response.get("nextSyncToken")
    upserted = 0

    for event in events:
        event_id = event.get("id")
        if not event_id:
            continue

        # Skip cancelled events
        if event.get("status") == "cancelled":
            await db.execute(
                update(CalendarEvent)
                .where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.external_event_id == event_id,
                )
                .values(status="cancelled")
            )
            continue

        start_time = parse_event_time(event, "start")
        if not start_time:
            continue  # All-day event, skip

        end_time = parse_event_time(event, "end")
        meeting_url = extract_meeting_url(event)
        platform = detect_platform(meeting_url) if meeting_url else None

        stmt = pg_insert(CalendarEvent).values(
            user_id=user_id,
            external_event_id=event_id,
            title=event.get("summary", ""),
            start_time=start_time,
            end_time=end_time,
            meeting_url=meeting_url,
            platform=platform,
            status="pending",
        ).on_conflict_do_update(
            constraint="uq_calendar_event_user_ext_id",
            set_={
                "title": event.get("summary", ""),
                "start_time": start_time,
                "end_time": end_time,
                "meeting_url": meeting_url,
                "platform": platform,
            },
        )
        await db.execute(stmt)
        upserted += 1

    # Save new sync token
    if next_sync_token:
        gc_data["sync_token"] = next_sync_token
        user_data["google_calendar"] = gc_data
        await db.execute(
            update(User).where(User.id == user_id).values(data=user_data)
        )

    await db.commit()
    logger.info(f"Synced {upserted} events for user {user_id}")
    return upserted


async def schedule_upcoming_bots(db: AsyncSession) -> int:
    """Check for pending events within lead time and schedule bots. Returns count scheduled."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(minutes=DEFAULT_LEAD_TIME_MINUTES)

    result = await db.execute(
        select(CalendarEvent).where(
            CalendarEvent.status == "pending",
            CalendarEvent.start_time <= cutoff,
            CalendarEvent.start_time >= now - timedelta(minutes=5),
            CalendarEvent.meeting_url.isnot(None),
            CalendarEvent.platform.isnot(None),
        )
    )
    events = result.scalars().all()
    scheduled = 0

    for event in events:
        # Get user's API key for meeting-api auth
        user_result = await db.execute(select(User).where(User.id == event.user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            continue

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{MEETING_API_URL}/bots",
                    json={
                        "platform": event.platform,
                        "native_meeting_id": _extract_native_id(event.meeting_url, event.platform),
                        "bot_name": f"Vexa - {event.title or 'Calendar'}",
                    },
                    headers={"X-API-Key": BOT_API_TOKEN},
                    timeout=30,
                )

            if resp.status_code in (200, 201):
                resp_data = resp.json()
                await db.execute(
                    update(CalendarEvent)
                    .where(CalendarEvent.id == event.id)
                    .values(
                        status="scheduled",
                        meeting_id=resp_data.get("id"),
                    )
                )
                scheduled += 1
                logger.info(f"Scheduled bot for event {event.id}: {event.title}")
            else:
                logger.error(f"Bot request failed for event {event.id}: {resp.status_code} {resp.text}")
                await db.execute(
                    update(CalendarEvent)
                    .where(CalendarEvent.id == event.id)
                    .values(status="failed")
                )
        except Exception as e:
            logger.error(f"Failed to schedule bot for event {event.id}: {e}")

    await db.commit()
    return scheduled


def _extract_native_id(url: str, platform: str) -> str:
    """Extract the native meeting ID from a URL for meeting-api."""
    if platform == "google_meet":
        # https://meet.google.com/abc-defg-hij -> abc-defg-hij
        return url.rsplit("/", 1)[-1].split("?")[0]
    if platform == "zoom":
        # https://zoom.us/j/123456?pwd=xxx -> 123456
        import re
        match = re.search(r"/j/(\d+)", url)
        return match.group(1) if match else url
    if platform == "teams":
        return url
    return url
