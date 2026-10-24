"""calendar_tools.py — the calendar that makes the bot come by itself, as three MCP tools.

Vexa's auto-join runs on the person's connected calendars (identity's `/user/calendars`: up to ten
secret ICS feeds, each with its own auto-join policy). meeting-api's calendar sync turns each feed's
events into PLANNED meetings (status `scheduled`, `data.auto_join`), and its auto-join sweep sends the
bot when one starts. Every route below already exists on the public API; these tools only compose
them, with the caller's own key, through the gateway — which enforces the scopes (`/user/calendars`
needs `bot`, the meeting rows need `tx`).

  calendar_status  GET /user/calendars + GET /user/calendar/sync + GET /meetings?status=scheduled
                   → what is connected, when it last synced, and which upcoming meetings the bot
                     will join by itself.
  calendar_connect no hop → the page on this deployment's site where the PERSON connects a
                     calendar. A feed address is a credential: it is typed there, never here.
  set_auto_join    PATCH /meetings/{id} {auto_join}  (one meeting, pinned as the person's choice), or
                   PATCH /user/calendars/{id} {auto_join} + POST /user/calendars/{id}/sync (a whole
                   calendar's policy, applied at once to its meetings nobody pinned).

Registered from `app.create_app` with one call (`install`), before the MCP surface is derived; their
annotations are in `ANNOTATIONS`, applied once the tools exist (`annotate`).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .paths import path_segment

#: Where this deployment's site lets a person connect a calendar (operator setting, config.v1).
CONNECT_URL_ENV = "VEXA_CALENDAR_CONNECT_URL"

#: name -> {title, readOnlyHint, destructiveHint, idempotentHint, openWorldHint} — the convention of
#: Vexa-ai/vexa#1852 (`annotations.REGISTRY`): `openWorldHint` only where the effect leaves Vexa.
ANNOTATIONS: Dict[str, Dict[str, Any]] = {
    "calendar_status": {"title": "Calendar status", "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": False},
    # Reads nothing and changes nothing: it hands back a link the person opens.
    "calendar_connect": {"title": "Connect a calendar", "readOnlyHint": True, "destructiveHint": False,
                         "idempotentHint": True, "openWorldHint": False},
    # Turning auto-join on sends a bot into a third-party meeting later; off keeps one out. The same
    # call twice is one state, and either direction is undone by the opposite call.
    "set_auto_join": {"title": "Set auto-join", "readOnlyHint": False, "destructiveHint": False,
                      "idempotentHint": True, "openWorldHint": True},
}

#: An upcoming meeting is one whose start is at most this far in the past (the sweep's grace window).
_GRACE = timedelta(minutes=15)

MakeRequest = Callable[..., Awaitable[Any]]


class SetAutoJoin(BaseModel):
    enabled: bool = Field(..., description="true: the bot joins by itself when the meeting starts. false: it does not.")
    meeting_db_id: Optional[int] = Field(
        None, description="One planned meeting (its `meeting_db_id` from calendar_status). Pinned as the "
                          "person's own choice: calendar sync never overrides it.")
    calendar_id: Optional[str] = Field(
        None, description="One connected calendar (its `id` from calendar_status): its policy for every "
                          "meeting it brings in, applied now to the ones nobody pinned.")
    all_calendars: bool = Field(
        False, description="Every connected calendar at once — the person's 'always' or 'never'.")


def _parse_when(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def upcoming_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One planned meeting as calendar_status shows it, with whether the bot will come and why not."""
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    auto_join = data.get("auto_join") is not False          # absent = on (meeting-api's rule)
    native = row.get("native_meeting_id")
    has_link = bool(native) and str(row.get("platform") or "unknown") != "unknown"
    reason = None
    if not auto_join:
        reason = "auto-join is off for this meeting"
    elif not has_link:
        reason = "no meeting link (Meet, Teams, Zoom or Jitsi) on the event"
    sources = data.get("calendar_sources") if isinstance(data.get("calendar_sources"), list) else []
    return {
        "meeting_db_id": row.get("id"),
        "title": data.get("title"),
        "scheduled_at": data.get("scheduled_at"),
        "platform": row.get("platform"),
        "native_meeting_id": native,
        "meeting_url": data.get("constructed_meeting_url"),
        "calendar_id": data.get("calendar_connection_id")
                       or next((s.get("id") for s in sources if isinstance(s, dict) and s.get("id")), None),
        "calendar_name": data.get("calendar_name"),
        "auto_join": auto_join,
        "pinned": bool(data.get("auto_join_user_set")),
        "will_auto_join": auto_join and has_link,
        "why_not": reason,
        "auto_join_error": data.get("auto_join_error"),
    }


def install(app: FastAPI, *, base_url: str, make_request: MakeRequest, get_api_key: Callable[..., Any],
            env: Optional[Mapping[str, str]] = None) -> None:
    """Register the three calendar tools on ``app`` (call before ``FastApiMCP`` derives the surface)."""
    environ = env if env is not None else os.environ

    async def _soft(method: str, url: str, api_key: str, **kw: Any) -> tuple[Any, Optional[str]]:
        """A hop whose refusal is part of the answer rather than the end of it: (body, None) or
        (None, why) — a key without `tx` still learns what is connected."""
        try:
            return await make_request(method, url, api_key, **kw), None
        except HTTPException as exc:
            return None, f"{exc.status_code}: {exc.detail}"

    @app.get("/calendar-status", operation_id="calendar_status")
    async def calendar_status(
        days: int = Query(7, ge=1, le=60, description="How far ahead to list meetings (default 7 days)."),
        api_key: str = Depends(get_api_key),
    ) -> Dict[str, Any]:
        """
        The person's calendar auto-join at a glance: the calendars connected to Vexa, when they last
        synced, and the upcoming meetings they brought in — each with `will_auto_join` (the bot joins
        by itself when it starts) and, when not, `why_not`.

        Nothing connected (`calendars` empty)? calendar_connect gives the link where the person
        connects one. Change who joins with set_auto_join (one meeting, or a whole calendar).
        """
        cal = await make_request("GET", f"{base_url}/user/calendars", api_key)
        calendars = [c for c in (cal or {}).get("calendars") or [] if isinstance(c, dict)]
        sync, _ = await _soft("GET", f"{base_url}/user/calendar/sync", api_key)
        rows, refused = await _soft("GET", f"{base_url}/meetings", api_key,
                                    params={"status": "scheduled", "limit": 100})
        out: Dict[str, Any] = {
            "calendars": [{k: c.get(k) for k in ("id", "name", "enabled", "auto_join", "bot_name",
                                                 "ics_url_set", "ics_url_masked")} for c in calendars],
            "sync": sync if isinstance(sync, dict) else None,
            "window_days": days,
        }
        if refused is not None:
            out["upcoming"] = None
            out["upcoming_unavailable"] = f"the meeting list was refused ({refused}); it needs the `tx` scope"
            return out
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=days)
        upcoming: List[Dict[str, Any]] = []
        for row in (rows or {}).get("meetings") or []:
            if not isinstance(row, dict):
                continue
            when = _parse_when(((row.get("data") or {}) if isinstance(row.get("data"), dict) else {}).get("scheduled_at"))
            if when is None or not (now - _GRACE <= when <= horizon):
                continue
            upcoming.append(upcoming_row(row))
        upcoming.sort(key=lambda m: str(m.get("scheduled_at") or ""))
        out["upcoming"] = upcoming
        out["will_auto_join"] = sum(1 for m in upcoming if m["will_auto_join"])
        return out

    @app.get("/calendar-connect", operation_id="calendar_connect")
    async def calendar_connect(api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
        """
        The link where the PERSON connects a calendar to Vexa, so the bot joins their meetings by
        itself. Give them the link to open; they paste their calendar's secret iCal address there.

        Never ask for that address in chat and never pass it to any tool: it is a credential, and
        this tool takes none. After they connect, calendar_status shows the calendar and its meetings.
        """
        url = (environ.get(CONNECT_URL_ENV) or "").strip() or None
        steps = [
            "Open the link and sign in to Vexa.",
            "Google Calendar: Settings → your calendar → 'Secret address in iCal format'; Outlook: "
            "Settings → Calendar → Shared calendars → Publish a calendar → the ICS link.",
            "Paste that address into Vexa's calendar settings and save; auto-join is on by default.",
        ]
        return {
            "url": url,
            "steps": steps,
            "note": ("Hand the person this link; the calendar address is entered there by them, never here."
                     if url else
                     f"This deployment has not named its calendar page ({CONNECT_URL_ENV}); the person "
                     "connects a calendar in Vexa's settings → Calendar."),
        }

    @app.post("/auto-join", operation_id="set_auto_join")
    async def set_auto_join(data: SetAutoJoin, api_key: str = Depends(get_api_key)) -> Dict[str, Any]:
        """
        Turn the bot's auto-join on or off — for ONE planned meeting (`meeting_db_id`), for one
        connected calendar (`calendar_id`), or for all of them (`all_calendars=true`). Name exactly one.

        A meeting-level choice is the person's and sticks: calendar sync never overrides it. A
        calendar-level choice is that calendar's policy for every meeting it brings in, applied now to
        its meetings nobody pinned. Only planned meetings can change; one the bot already joined is
        refused (409). Do this when the person asks, not on your own initiative.
        """
        named = [n for n, v in (("meeting_db_id", data.meeting_db_id), ("calendar_id", data.calendar_id),
                                ("all_calendars", data.all_calendars or None)) if v is not None]
        if len(named) != 1:
            raise HTTPException(status_code=422, detail=(
                "set_auto_join: name exactly one of meeting_db_id, calendar_id or all_calendars=true"
                + (f" (got {', '.join(named)})" if named else "")))
        if data.meeting_db_id is not None:
            row = await make_request("PATCH", f"{base_url}/meetings/{path_segment(data.meeting_db_id)}",
                                     api_key, {"auto_join": data.enabled})
            return {"scope": "meeting", "meeting": upcoming_row(row if isinstance(row, dict) else {})}
        if data.calendar_id is not None:
            ids = [data.calendar_id]
        else:
            cal = await make_request("GET", f"{base_url}/user/calendars", api_key)
            ids = [c["id"] for c in (cal or {}).get("calendars") or [] if isinstance(c, dict) and c.get("id")]
            if not ids:
                raise HTTPException(status_code=409, detail=(
                    "set_auto_join: no calendar is connected — calendar_connect gives the person the link"))
        changed: List[Dict[str, Any]] = []
        for cid in ids:
            seg = path_segment(cid)
            updated = await make_request("PATCH", f"{base_url}/user/calendars/{seg}", api_key,
                                         {"auto_join": data.enabled})
            # Sync is not automatic after a policy write (the terminal runs it too): apply it now, so
            # the answer and calendar_status agree. A sync that fails leaves the policy saved.
            synced = True
            try:
                await make_request("POST", f"{base_url}/user/calendars/{seg}/sync", api_key)
            except HTTPException:
                synced = False
            changed.append({"id": cid, "name": (updated or {}).get("name") if isinstance(updated, dict) else None,
                            "auto_join": data.enabled, "synced": synced})
        return {"scope": "calendar" if data.calendar_id is not None else "all_calendars", "calendars": changed}


def annotate(tools: Any, registry: Mapping[str, Mapping[str, Any]]) -> List[str]:
    """Set ``title`` + ``annotations`` IN PLACE on the tools ``registry`` names; returns those set.

    The shape of #1852's ``annotations.apply`` for the tools this edge registers here, so when that
    lands these entries move into its REGISTRY unchanged."""
    import mcp.types as mcp_types  # noqa: PLC0415

    done: List[str] = []
    for tool in tools:
        spec = registry.get(getattr(tool, "name", ""))
        if not spec:
            continue
        tool.title = spec["title"]
        tool.annotations = mcp_types.ToolAnnotations(**dict(spec))
        done.append(tool.name)
    return done
