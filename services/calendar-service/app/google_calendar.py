"""Google Calendar API client — auth, event listing, meeting URL extraction."""

import re
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger("calendar-service.google")

TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


async def refresh_access_token(
    client_id: str, client_secret: str, refresh_token: str
) -> tuple[str, int]:
    """Exchange a refresh token for a fresh access token. Returns (access_token, expires_in)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["access_token"], int(data.get("expires_in", 3600))


async def list_events(
    access_token: str,
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
    sync_token: Optional[str] = None,
    max_results: int = 50,
) -> dict:
    """Fetch events from Google Calendar API. Returns raw API response dict."""
    params: dict[str, str] = {
        "maxResults": str(max_results),
        "singleEvents": "true",
        "orderBy": "startTime",
    }

    if sync_token:
        params["syncToken"] = sync_token
    else:
        if time_min:
            params["timeMin"] = time_min.isoformat()
        if time_max:
            params["timeMax"] = time_max.isoformat()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            EVENTS_URL,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 410:
            # Sync token expired — caller should do a full sync
            return {"items": [], "nextSyncToken": None, "fullSyncRequired": True}
        resp.raise_for_status()
        return resp.json()


# --- Meeting URL extraction ---

MEETING_URL_PATTERNS = [
    re.compile(r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}"),
    re.compile(r"https://[\w.-]*zoom\.us/j/\d+(\?pwd=\w+)?"),
    re.compile(r"https://teams\.microsoft\.com/l/meetup-join/[^\s\"<>]+"),
]


def extract_meeting_url(event: dict) -> Optional[str]:
    """Extract a meeting URL from a Google Calendar event object."""
    # 1. conferenceData.entryPoints (most reliable)
    conference_data = event.get("conferenceData", {})
    for ep in conference_data.get("entryPoints", []):
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            return ep["uri"]

    # 2. hangoutLink
    hangout = event.get("hangoutLink")
    if hangout:
        return hangout

    # 3. Scan location and description for known patterns
    for field in ["location", "description"]:
        text = event.get(field, "") or ""
        for pattern in MEETING_URL_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(0)

    return None


def detect_platform(url: str) -> Optional[str]:
    """Detect the meeting platform from a URL."""
    if "meet.google.com" in url:
        return "google_meet"
    if "zoom.us" in url:
        return "zoom"
    if "teams.microsoft.com" in url:
        return "teams"
    return None


def parse_event_time(event: dict, key: str) -> Optional[datetime]:
    """Parse start or end time from a Google Calendar event."""
    time_info = event.get(key, {})
    dt_str = time_info.get("dateTime")
    if dt_str:
        return datetime.fromisoformat(dt_str)
    # All-day events only have 'date', skip them (no meeting time)
    return None
