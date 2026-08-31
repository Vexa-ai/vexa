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

import os

# A REAL work calendar exports its whole history — years of it — because an ICS address takes no
# time-range parameter. The parser next door reads a 14-day window and discards the rest, so the
# old 2 MB constant refused feeds over bytes it was never going to keep, and no env var, Helm
# value or compose value could move it (Vexa-ai/vexa#1182). Tunable now, with a ceiling that fits
# an actual work calendar.
MAX_ICS_BYTES = int(os.environ.get("CALENDAR_ICS_MAX_BYTES", str(25 * 1024 * 1024)))


def build_ics_client(*, timeout_s: float = 15.0):
    """A pinned httpx client for ICS fetches — the ONE place the transport is configured.

    A sweep that reuses a single client across a tick's feeds keeps connection pooling and TLS
    handshakes amortized; ``fetch_ics`` builds a per-call one when no client is passed.
    """
    import httpx

    from ..webhooks.ssrf import build_pinned_transport

    return httpx.AsyncClient(
        timeout=timeout_s, transport=build_pinned_transport(), follow_redirects=False,
    )


async def fetch_ics(url: str, *, timeout_s: float = 15.0, client=None,
                    etag: Optional[str] = None,
                    last_modified: Optional[str] = None,
                    ) -> tuple[Optional[str], Optional[str], dict]:
    """GET the ICS feed over the SSRF-pinned transport → ``(feed_text, None, validators)`` on
    success, ``(None, human_reason, {})`` on failure, or ``(None, None, {"not_modified": True})``
    when the feed says it has not changed.

    CONDITIONAL BY DEFAULT. Pass the ``etag``/``last_modified`` kept from the last poll and the
    server answers 304 with NO BODY when nothing has changed — which is most polls. Without this
    every sweep downloads every feed in full: at ten thousand feeds on a five-minute tick that is
    120k full transfers an hour, and the provider throttles us long before we notice.

    The failure reason is USER-FACING (it becomes the feed's ``last_error`` in the calendar
    panel), so it names the actual problem — an HTML page instead of a feed, a bad status,
    oversize — never a stack trace.

    ``client`` (optional) is a caller-owned pinned client — pass one to share a connection pool
    across a sweep; the caller owns its lifetime."""
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        if client is not None:
            resp = await client.get(url, headers=headers or None)
        else:
            async with build_ics_client(timeout_s=timeout_s) as owned:
                resp = await owned.get(url, headers=headers or None)
        if resp.status_code == 304:
            # nothing was transferred; keep the validators we already had
            return None, None, {"not_modified": True, "etag": etag,
                                "last_modified": last_modified}
        if resp.status_code in (301, 302, 303, 307, 308):
            return None, "the URL redirects — paste the final feed URL (Google: the 'Secret address in iCal format')", {}
        if resp.status_code != 200:
            return None, f"the URL answered HTTP {resp.status_code}", {}
        if len(resp.content) > MAX_ICS_BYTES:
            mb = MAX_ICS_BYTES // (1024 * 1024)
            return None, (f"the feed is larger than {mb} MB — an operator can raise "
                          f"CALENDAR_ICS_MAX_BYTES"), {}
        text = resp.text
        head = text.lstrip()[:200].lower()
        if head.startswith("<") or "<html" in head:
            return None, ("the URL returns a web page, not a calendar feed — in Google Calendar use "
                          "Settings → Integrate calendar → 'Secret address in iCal format' (ends in .ics)"), {}
        if "begin:vcalendar" not in head:
            return None, "the URL doesn't return an ICS calendar (no BEGIN:VCALENDAR)", {}
        return text, None, {"etag": resp.headers.get("etag"),
                            "last_modified": resp.headers.get("last-modified")}
    except Exception:
        return None, ("couldn't reach the URL (unreachable, timed out, or a blocked/internal "
                      "address)"), {}


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
