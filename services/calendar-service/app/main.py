"""Calendar Service — Google Calendar sync + bot scheduling."""

import os
import asyncio
import logging

import uvicorn
from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from meeting_api.database import get_db, init_db
from meeting_api.models import CalendarEvent
from admin_models.models import User
from app.sync import sync_user_calendar, schedule_upcoming_bots

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", "300"))

logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("calendar-service")

app = FastAPI(title="Calendar Service", description="Google Calendar sync and auto-join scheduling")


@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(sync_loop())


async def sync_loop():
    """Background loop: sync all connected calendars and schedule bots."""
    while True:
        try:
            from meeting_api.database import async_session_local
            async with async_session_local() as db:
                # Find all users with google_calendar oauth configured
                result = await db.execute(select(User))
                users = result.scalars().all()
                for user in users:
                    gc = (user.data or {}).get("google_calendar", {})
                    if gc.get("oauth", {}).get("refresh_token"):
                        try:
                            await sync_user_calendar(user.id, db)
                        except Exception as e:
                            logger.error(f"Sync failed for user {user.id}: {e}")

                # Schedule bots for upcoming events
                await schedule_upcoming_bots(db)
        except Exception as e:
            logger.error(f"Sync loop error: {e}")

        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "calendar-service"}


@app.post("/calendar/connect")
async def connect_calendar(user_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    """Trigger initial sync after OAuth connection."""
    count = await sync_user_calendar(user_id, db)
    return {"status": "connected", "events_synced": count}


@app.get("/calendar/status")
async def calendar_status(user_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    """Check if user has calendar connected."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    gc = (user.data or {}).get("google_calendar", {})
    connected = bool(gc.get("oauth", {}).get("refresh_token"))

    event_count = 0
    if connected:
        count_result = await db.execute(
            select(CalendarEvent).where(CalendarEvent.user_id == user_id)
        )
        event_count = len(count_result.scalars().all())

    return {
        "connected": connected,
        "event_count": event_count,
    }


@app.delete("/calendar/disconnect")
async def disconnect_calendar(user_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    """Remove OAuth tokens and stop syncing."""
    from sqlalchemy import update
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user_data = dict(user.data or {})
    user_data.pop("google_calendar", None)
    await db.execute(
        update(User).where(User.id == user_id).values(data=user_data)
    )
    await db.commit()
    return {"status": "disconnected"}


@app.get("/calendar/events")
async def list_events(user_id: int = Query(...), db: AsyncSession = Depends(get_db)):
    """List upcoming calendar events for a user."""
    result = await db.execute(
        select(CalendarEvent)
        .where(CalendarEvent.user_id == user_id)
        .order_by(CalendarEvent.start_time)
    )
    events = result.scalars().all()
    return [
        {
            "id": e.id,
            "title": e.title,
            "start_time": e.start_time.isoformat() if e.start_time else None,
            "end_time": e.end_time.isoformat() if e.end_time else None,
            "meeting_url": e.meeting_url,
            "platform": e.platform,
            "status": e.status,
        }
        for e in events
    ]


@app.put("/calendar/preferences")
async def update_preferences(
    user_id: int = Query(...),
    auto_join: bool = True,
    lead_time_minutes: int = 2,
    db: AsyncSession = Depends(get_db),
):
    """Set auto-join and lead time preferences."""
    from sqlalchemy import update
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user_data = dict(user.data or {})
    gc = user_data.get("google_calendar", {})
    gc["preferences"] = {
        "auto_join": auto_join,
        "lead_time_minutes": lead_time_minutes,
    }
    user_data["google_calendar"] = gc
    await db.execute(
        update(User).where(User.id == user_id).values(data=user_data)
    )
    await db.commit()
    return {"status": "updated", "preferences": gc["preferences"]}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8050, reload=True)
