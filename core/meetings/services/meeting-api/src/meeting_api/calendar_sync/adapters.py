"""Production I/O for calendar sync — the ICS fetch (SSRF-pinned) + the config discovery hop.

``fetch_ics`` dereferences a USER-SUPPLIED URL server-side, so it MUST ride the same pinned
transport the webhook sender uses (``webhooks/ssrf.build_pinned_transport``): the host is
resolved + validated at connect time and the socket dials the validated IP — a DNS-rebinding
flip can never turn the poller into an internal-network probe. Size-capped: a feed larger than
``MAX_ICS_BYTES`` is refused, not parsed.

``fetch_configs`` asks admin-api's internal edge (X-Internal-Secret) which users have a feed
connected — the secret URL crosses only this internal hop. A validated list (including ``[]``)
is authoritative. Transport/auth/upstream/shape faults raise a typed, sanitized exception so a
caller can never mistake an unavailable identity service for "no feed connected".
"""
from __future__ import annotations

from enum import StrEnum
from typing import Optional, TypedDict

MAX_ICS_BYTES = 2 * 1024 * 1024  # 2 MB — a personal calendar feed is KBs; refuse anything huge
CALENDAR_CONFIG_UNAVAILABLE_DETAIL = "calendar configuration is temporarily unavailable"


class CalendarConfig(TypedDict):
    user_id: int
    ics_url: str
    auto_join: bool


class CalendarConfigDiscoveryKind(StrEnum):
    CONFIGURATION = "configuration"
    TRANSPORT = "transport"
    CONNECTIVITY = "connectivity"
    AUTHENTICATION = "authentication"
    UPSTREAM_STATUS = "upstream_status"
    RESPONSE_SHAPE = "response_shape"


class CalendarConfigDiscoveryError(RuntimeError):
    """Sanitized failure of admin-api's internal calendar-config discovery edge.

    ``kind`` is safe for system telemetry. The exception deliberately carries no URL, credential,
    response body, user/account id, or underlying exception text.
    """

    source = "admin_api.calendar_configs"

    def __init__(self, kind: CalendarConfigDiscoveryKind):
        if not isinstance(kind, CalendarConfigDiscoveryKind):
            raise TypeError("kind must be a CalendarConfigDiscoveryKind")
        self.kind = kind
        super().__init__(f"calendar config discovery failed ({kind.value})")


async def fetch_ics(url: str, *, timeout_s: float = 15.0) -> tuple[Optional[str], Optional[str]]:
    """GET the ICS feed over the SSRF-pinned transport → ``(feed_text, None)`` on success or
    ``(None, human_reason)`` on any failure. The reason is USER-FACING (it becomes the feed's
    ``last_error`` and is shown in the terminal's calendar panel), so it names the actual
    problem — an HTML page instead of a feed, a bad status, oversize — never a stack trace."""
    import httpx

    from ..webhooks.ssrf import build_pinned_transport

    try:
        async with httpx.AsyncClient(
            timeout=timeout_s, transport=build_pinned_transport(), follow_redirects=False,
        ) as client:
            resp = await client.get(url)
        if resp.status_code in (301, 302, 303, 307, 308):
            return None, "the URL redirects — paste the final feed URL (Google: the 'Secret address in iCal format')"
        if resp.status_code != 200:
            return None, f"the URL answered HTTP {resp.status_code}"
        if len(resp.content) > MAX_ICS_BYTES:
            return None, "the feed is too large (over 2 MB)"
        text = resp.text
        head = text.lstrip()[:200].lower()
        if head.startswith("<") or "<html" in head:
            return None, ("the URL returns a web page, not a calendar feed — in Google Calendar use "
                          "Settings → Integrate calendar → 'Secret address in iCal format' (ends in .ics)")
        if "begin:vcalendar" not in head:
            return None, "the URL doesn't return an ICS calendar (no BEGIN:VCALENDAR)"
        return text, None
    except Exception:
        return None, "couldn't reach the URL (unreachable, timed out, or a blocked/internal address)"


async def fetch_configs(admin_api_url: str, internal_secret: str,
                        *, timeout_s: float = 10.0) -> list[CalendarConfig]:
    """Return admin-api's authoritative ``[{user_id, ics_url, auto_join}]`` list.

    A successful empty list means no user has a feed. Every non-authoritative outcome raises
    ``CalendarConfigDiscoveryError``; callers may therefore reserve absence/404 for a completed,
    validated discovery response only.
    """
    import httpx

    if not admin_api_url or not internal_secret:
        raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.CONFIGURATION)
    try:
        parsed_admin_api_url = httpx.URL(admin_api_url)
    except (TypeError, httpx.InvalidURL):
        raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.CONFIGURATION) from None
    if parsed_admin_api_url.scheme not in ("http", "https") or not parsed_admin_api_url.host:
        raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.CONFIGURATION)
    endpoint = f"{str(parsed_admin_api_url).rstrip('/')}/internal/calendar-configs"

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(
                endpoint,
                headers={"X-Internal-Secret": internal_secret},
            )
    except (httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.LocalProtocolError):
        raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.CONFIGURATION) from None
    except httpx.TimeoutException:
        raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.TRANSPORT) from None
    except httpx.RequestError:
        raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.CONNECTIVITY) from None

    if resp.status_code in (401, 403):
        raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.AUTHENTICATION)
    if resp.status_code != 200:
        raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.UPSTREAM_STATUS)

    try:
        body = resp.json()
    except ValueError:
        raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.RESPONSE_SHAPE) from None

    configs = body.get("configs") if isinstance(body, dict) else None
    if not isinstance(configs, list):
        raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.RESPONSE_SHAPE)
    validated: list[CalendarConfig] = []
    seen_user_ids: set[int] = set()
    for cfg in configs:
        if not isinstance(cfg, dict):
            raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.RESPONSE_SHAPE)
        user_id = cfg.get("user_id")
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id in seen_user_ids:
            raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.RESPONSE_SHAPE)
        seen_user_ids.add(user_id)
        if not isinstance(cfg.get("ics_url"), str) or not cfg["ics_url"]:
            raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.RESPONSE_SHAPE)
        if not isinstance(cfg.get("auto_join"), bool):
            raise CalendarConfigDiscoveryError(CalendarConfigDiscoveryKind.RESPONSE_SHAPE)
        validated.append({
            "user_id": user_id,
            "ics_url": cfg["ics_url"],
            "auto_join": cfg["auto_join"],
        })
    return validated


async def fetch_user_config(admin_api_url: str, internal_secret: str, user_id: int,
                            *, timeout_s: float = 10.0) -> Optional[CalendarConfig]:
    """Return the gateway-bound user's config, or ``None`` after authoritative absence only."""
    configs = await fetch_configs(admin_api_url, internal_secret, timeout_s=timeout_s)
    return next((cfg for cfg in configs if cfg["user_id"] == user_id), None)
