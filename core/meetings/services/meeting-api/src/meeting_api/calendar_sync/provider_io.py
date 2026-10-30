"""Production I/O for the calendar-API readers — fetch a window of events, refresh a token.

The counterpart to ``adapters.fetch_ics``: that one dereferences a user-supplied URL and so must
ride the SSRF-pinned transport; these talk to two FIXED, known hosts, so the risk is inverted. What
must never happen here is a user-controlled value (a calendar id) escaping into the URL and
redirecting the request somewhere else — so ids are path-escaped and every request is built against
a constant base, never string-concatenated from stored input.

Error contract copied deliberately from ``fetch_ics``: every call returns ``(value, human_reason)``
and never raises. The reason is USER-FACING — it becomes the connection's ``last_error`` and is
shown in the calendar panel — so it names the actual problem in words the person who connected the
calendar can act on ("reconnect", not "401").

**Tokens are arguments, never state.** Nothing here reads a database or a secret store; the caller
hands over an access token and gets events back. That keeps this module offline-testable and keeps
the decrypt→use path in one place upstream, where it can be audited.

Pagination is exhaustive but bounded: a calendar with an implausible number of events in a 14-day
window is a bug or an attack, not a user, so the page loop stops at ``MAX_PAGES`` and says so rather
than looping until the sweep's deadline.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GRAPH_CALENDAR_VIEW_URL = "https://graph.microsoft.com/v1.0/me/calendarView"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

# One sweep of one calendar. 250 events/page × 20 pages = 5000 events in a 14-day window; past that
# we are not reading a person's calendar any more.
PAGE_SIZE = 250
MAX_PAGES = 20

# The narrowest scopes that serve the product: read the events, and list which calendars exist.
# Anything wider is a bigger consent prompt, a slower Google review, and more to lose if a token
# leaks. We never write to a calendar, so no write scope is requested.
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
)
MICROSOFT_SCOPES = ("offline_access", "Calendars.Read")


def build_client(*, timeout_s: float = 20.0):
    """A plain httpx client for provider APIs — fixed hosts, so no SSRF pinning is needed here.

    Redirects stay OFF: neither API redirects in normal operation, and following one would let a
    compromised or misconfigured endpoint move an Authorization header to another host.
    """
    import httpx

    return httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)


def _reason_for_status(status: int, *, provider: str) -> str:
    """A status code → something the person who connected the calendar can act on."""
    if status in (401, 403):
        return (f"{provider} refused the stored authorization — reconnect the calendar to grant "
                "access again")
    if status == 404:
        return "that calendar no longer exists, or the connected account can no longer see it"
    if status == 429:
        return f"{provider} is rate-limiting us — the next sync will retry"
    if 500 <= status < 600:
        return f"{provider} is having trouble ({status}) — the next sync will retry"
    return f"{provider} answered HTTP {status}"


async def _get_json(client, url: str, *, token: str, params: dict,
                    provider: str, headers: Optional[dict] = None):
    request_headers = {"Authorization": f"Bearer {token}"}
    request_headers.update(headers or {})
    try:
        resp = await client.get(url, params=params, headers=request_headers)
    except Exception:
        return None, f"couldn't reach {provider} (unreachable or timed out)"
    if resp.status_code != 200:
        return None, _reason_for_status(resp.status_code, provider=provider)
    try:
        return resp.json(), None
    except Exception:
        return None, f"{provider} returned a response we couldn't read"


async def fetch_google_events(client, *, access_token: str, calendar_id: str,
                              window_start: datetime, window_end: datetime):
    """Google Calendar events in the window → ``(items, None)`` or ``(None, human_reason)``.

    ``singleEvents=true`` is not optional — it is what makes the provider expand recurrences, which
    ``events_from_google`` relies on. ``showDeleted=true`` likewise: without it a cancelled instance
    is merely absent, and absence and cancellation retire a row for different reasons.
    """
    url = GOOGLE_EVENTS_URL.format(calendar_id=quote(calendar_id, safe=""))
    params: dict[str, Any] = {
        "singleEvents": "true",
        "showDeleted": "true",
        "orderBy": "startTime",
        "maxResults": PAGE_SIZE,
        "timeMin": window_start.isoformat(),
        "timeMax": window_end.isoformat(),
    }
    items: list = []
    for _ in range(MAX_PAGES):
        body, reason = await _get_json(client, url, token=access_token, params=params,
                                       provider="Google Calendar")
        if reason:
            return None, reason
        items.extend(body.get("items") or [])
        token_next = body.get("nextPageToken")
        if not token_next:
            return items, None
        params["pageToken"] = token_next
    return None, "that calendar has more events than we can read in one sync"


async def fetch_microsoft_events(client, *, access_token: str,
                                 window_start: datetime, window_end: datetime):
    """Graph calendarView in the window → ``(items, None)`` or ``(None, human_reason)``.

    ``Prefer: outlook.timezone="UTC"`` matters: without it Graph answers in the mailbox's own zone
    using WINDOWS zone ids ("Pacific Standard Time"), which are not IANA and do not load. The reader
    falls back to UTC in that case, so the header is what keeps the fallback from being load-bearing.
    """
    params: dict[str, Any] = {
        "startDateTime": window_start.isoformat(),
        "endDateTime": window_end.isoformat(),
        "$top": PAGE_SIZE,
        "$orderby": "start/dateTime",
    }
    url = GRAPH_CALENDAR_VIEW_URL
    items: list = []
    for _ in range(MAX_PAGES):
        body, reason = await _get_json(client, url, token=access_token, params=params,
                                       provider="Outlook", headers={"Prefer": 'outlook.timezone="UTC"'})
        if reason:
            return None, reason
        items.extend(body.get("value") or [])
        next_link = body.get("@odata.nextLink")
        if not next_link:
            return items, None
        # Graph's nextLink is a fully-formed absolute URL that already carries the query; passing
        # our params alongside it would duplicate them.
        if not next_link.startswith("https://graph.microsoft.com/"):
            return None, "Outlook returned a paging link we don't trust"
        url, params = next_link, {}
    return None, "that calendar has more events than we can read in one sync"


async def refresh_access_token(client, *, provider: str, refresh_token: str,
                               client_id: str, client_secret: str):
    """A refresh token → ``({access_token, expires_in}, None)`` or ``(None, human_reason)``.

    A refresh that comes back ``invalid_grant`` is terminal, not transient: the user revoked access,
    changed their password, or the token expired through disuse. The caller must surface that as
    "reconnect", never retry it on a loop — retrying a dead grant is how an integration ends up
    hammering an identity provider with a credential that will never work again.
    """
    url = GOOGLE_TOKEN_URL if provider == "google" else MICROSOFT_TOKEN_URL
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if provider == "microsoft":
        form["scope"] = " ".join(MICROSOFT_SCOPES)
    try:
        resp = await client.post(url, data=form)
    except Exception:
        return None, "couldn't reach the sign-in service to refresh the calendar connection"
    if resp.status_code != 200:
        detail = ""
        try:
            detail = str((resp.json() or {}).get("error") or "")
        except Exception:
            pass
        if detail == "invalid_grant" or resp.status_code in (400, 401):
            return None, "the calendar connection was revoked or expired — reconnect it"
        return None, f"couldn't refresh the calendar connection (HTTP {resp.status_code})"
    try:
        body = resp.json()
    except Exception:
        return None, "the sign-in service returned a response we couldn't read"
    access = body.get("access_token")
    if not access:
        return None, "the sign-in service returned no access token"
    return {"access_token": access, "expires_in": body.get("expires_in")}, None
