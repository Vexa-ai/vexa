"""Production I/O for calendar sync — the ICS fetch (SSRF-pinned) + the config discovery hop.

``fetch_ics`` dereferences a USER-SUPPLIED URL server-side, so it MUST ride the same pinned
transport the webhook sender uses (``webhooks/ssrf.build_pinned_transport``): the host is
resolved + validated at connect time and the socket dials the validated IP — a DNS-rebinding
flip can never turn the poller into an internal-network probe. Size-capped: a feed larger than
``MAX_ICS_BYTES`` is refused, not parsed.

``fetch_configs`` asks admin-api's internal edge (X-Internal-Secret) which users have a feed
connected — the secret URL crosses only this internal hop.
"""
from __future__ import annotations

from typing import Optional

MAX_ICS_BYTES = 2 * 1024 * 1024  # 2 MB — a personal calendar feed is KBs; refuse anything huge


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
                        *, timeout_s: float = 10.0) -> Optional[list[dict]]:
    """``[{user_id, ics_url, auto_join}]`` from admin-api's internal calendar-configs edge, or
    ``None`` when identity is unreachable (the sweep skips the tick — fail-closed, not fail-silent)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(
                f"{admin_api_url.rstrip('/')}/internal/calendar-configs",
                headers={"X-Internal-Secret": internal_secret},
            )
        if resp.status_code != 200:
            return None
        body = resp.json()
        configs = body.get("configs") if isinstance(body, dict) else None
        return configs if isinstance(configs, list) else None
    except Exception:
        return None
