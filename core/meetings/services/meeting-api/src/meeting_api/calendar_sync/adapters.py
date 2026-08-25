"""Production I/O for calendar sync — the ICS fetch (SSRF-pinned, size-budgeted) + the config
discovery hop.

``fetch_ics`` dereferences a USER-SUPPLIED URL server-side, so it MUST ride the same pinned
transport the webhook sender uses (``webhooks/ssrf.build_pinned_transport``): the host is
resolved + validated at connect time and the socket dials the validated IP — a DNS-rebinding
flip can never turn the poller into an internal-network probe.

Size-budgeted, and the budget is an OPERATOR DIAL (#1182). Two things were wrong with the old
module constant. It was **unappealable** — three references, all in this file, no env/compose/helm
surface — so an oversize feed had no workaround to offer anyone. And its premise ("a personal
calendar feed is KBs") does not describe the feeds users actually connect: a Google/Outlook secret
iCal address exports the WHOLE calendar, history included, and accepts no time-range parameter, so
a work calendar with years behind it fails ALWAYS rather than sometimes. The cap is now
``CALENDAR_MAX_ICS_BYTES`` over a default sized for a real work calendar, and the body STREAMS —
the running total is checked per chunk, so the cap bounds peak memory instead of merely describing
a body that is already fully resident.

``fetch_configs`` asks admin-api's internal edge (X-Internal-Secret) which users have a feed
connected — the secret URL crosses only this internal hop.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

# The DEFAULT feed cap — deliberately NOT "what a personal calendar weighs" (#1182). 10 MB is on the
# order of 15-25k VEVENTs, comfortably past a decade of a dense work calendar, while still refusing a
# runaway feed. The sweep syncs users one at a time (``__main__._calendar_sync_loop`` awaits each
# ``run_user_sync`` in turn), so this bounds the service's peak, not a peak per connected user.
DEFAULT_MAX_ICS_BYTES = 10 * 1024 * 1024

# Bytes buffered before the not-a-feed sniff runs. Comfortably over the 200-CHARACTER head the sniff
# matches on, so leading whitespace can never starve it into a false "no BEGIN:VCALENDAR".
_SNIFF_AFTER_BYTES = 4096
_SNIFF_HEAD_CHARS = 200


def max_ics_bytes() -> int:
    """The effective feed cap in bytes: ``CALENDAR_MAX_ICS_BYTES`` when an operator set a usable
    value, else ``DEFAULT_MAX_ICS_BYTES``.

    Read at CALL time, not at import — config.v1's rule (no boot snapshot), and it keeps the dial
    honest for the sync-now edge as well as the sweep. A garbage or non-positive value warns and
    falls back rather than raising: this runs inside a per-user sweep that must never take the loop
    down over an operator's typo, and a silent wrong-limit would be the very defect this closes.
    """
    raw = (os.getenv("CALENDAR_MAX_ICS_BYTES") or "").strip()
    if not raw:
        return DEFAULT_MAX_ICS_BYTES
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        log.warning("CALENDAR_MAX_ICS_BYTES=%r is not a positive integer — falling back to the "
                    "%d-byte default", raw, DEFAULT_MAX_ICS_BYTES)
        return DEFAULT_MAX_ICS_BYTES
    return value


def _human_bytes(n: int) -> str:
    """The cap as an operator would write it — whole MB when it divides evenly, else raw bytes."""
    mb = 1024 * 1024
    return f"{n // mb} MB" if n >= mb and n % mb == 0 else f"{n} bytes"


def _not_a_feed(head_bytes: bytes) -> Optional[str]:
    """The teachable reason when the body is not an ICS feed, or ``None`` when it looks like one.

    Runs on the FIRST bytes so it beats the size budget. Before #1182 the size test came first, so
    an HTML login/error page bigger than the cap was reported as "too large" and the message that
    actually helps — copy the *Secret address in iCal format* — could never be reached.
    """
    head = head_bytes.decode("utf-8", errors="replace").lstrip()[:_SNIFF_HEAD_CHARS].lower()
    if head.startswith("<") or "<html" in head:
        return ("the URL returns a web page, not a calendar feed — in Google Calendar use "
                "Settings → Integrate calendar → 'Secret address in iCal format' (ends in .ics)")
    if "begin:vcalendar" not in head:
        return "the URL doesn't return an ICS calendar (no BEGIN:VCALENDAR)"
    return None


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


async def _read_streamed(resp, cap: int) -> tuple[Optional[str], Optional[str]]:
    """Consume an open streaming response under the byte budget → ``(feed_text, None)`` or
    ``(None, human_reason)``. Split out so the borrowed-client and owned-client paths run the
    IDENTICAL budget and sniff — a second copy is how the two drift."""
    if resp.status_code in (301, 302, 303, 307, 308):
        return None, "the URL redirects — paste the final feed URL (Google: the 'Secret address in iCal format')"
    if resp.status_code != 200:
        return None, f"the URL answered HTTP {resp.status_code}"
    body = bytearray()
    sniffed = False
    async for chunk in resp.aiter_bytes():
        body.extend(chunk)
        if not sniffed and len(body) >= _SNIFF_AFTER_BYTES:
            sniffed = True
            reason = _not_a_feed(bytes(body[:_SNIFF_AFTER_BYTES]))
            if reason is not None:
                return None, reason
        if len(body) > cap:
            return None, (f"the feed is too large (over {_human_bytes(cap)}) — an "
                          f"operator can raise CALENDAR_MAX_ICS_BYTES")
    if not sniffed:  # the whole feed is shorter than the sniff window
        reason = _not_a_feed(bytes(body))
        if reason is not None:
            return None, reason
    encoding = resp.charset_encoding or "utf-8"
    try:
        return bytes(body).decode(encoding, errors="replace"), None
    except LookupError:  # a charset the feed declared but Python cannot name
        return bytes(body).decode("utf-8", errors="replace"), None


async def fetch_ics(url: str, *, timeout_s: float = 15.0,
                    client=None) -> tuple[Optional[str], Optional[str]]:
    """GET the ICS feed over the SSRF-pinned transport → ``(feed_text, None)`` on success or
    ``(None, human_reason)`` on any failure. The reason is USER-FACING (it becomes the feed's
    ``last_error`` and is shown in the terminal's calendar panel), so it names the actual
    problem — an HTML page instead of a feed, a bad status, oversize — never a stack trace. The
    oversize reason names the CONFIGURED limit and the key that changes it, because on a self-host
    the person reading the panel is the operator who can raise it.

    The body STREAMS: the not-a-feed sniff runs on the first bytes and the size budget is applied
    per chunk, so either refusal abandons the transfer instead of buffering a body we will not use.
    A ``Content-Length`` pre-check would add nothing on top of that — the per-chunk budget already
    stops the download, and under ``Content-Encoding`` the declared length is not the decompressed
    size the cap is measured against anyway.

    ``client`` (optional) is a caller-owned pinned client — pass one to share a connection pool
    across a sweep; the caller owns its lifetime. It streams under the same budget: abandoning an
    oversize transfer matters MORE on a shared client, which outlives the single fetch.
    """
    cap = max_ics_bytes()
    try:
        if client is not None:
            async with client.stream("GET", url) as resp:
                return await _read_streamed(resp, cap)
        async with build_ics_client(timeout_s=timeout_s) as owned:
            async with owned.stream("GET", url) as resp:
                return await _read_streamed(resp, cap)
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
