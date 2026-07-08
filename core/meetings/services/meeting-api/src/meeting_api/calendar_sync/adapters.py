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


async def fetch_ics(url: str, *, timeout_s: float = 15.0) -> Optional[str]:
    """GET the ICS feed over the SSRF-pinned transport → the feed text, or ``None`` on any
    failure (bad status, oversize, network error, blocked target). Failures are the caller's
    ``last_error`` — never an exception out of the sweep."""
    import httpx

    from ..webhooks.ssrf import build_pinned_transport

    try:
        async with httpx.AsyncClient(
            timeout=timeout_s, transport=build_pinned_transport(), follow_redirects=False,
        ) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        if len(resp.content) > MAX_ICS_BYTES:
            return None
        return resp.text
    except Exception:
        return None


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
