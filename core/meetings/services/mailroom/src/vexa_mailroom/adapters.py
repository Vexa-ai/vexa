"""Production adapters — Mailpit as the inbound mailbox, the Vexa public API as the control plane.

Both are thin: everything interesting is in ``service.py``, and these only translate. The httpx
transport is injectable on both so the seam tests drive the SHIPPED adapter against a
``MockTransport`` — no Mailpit, no gateway, no network.

**MailpitSource is the dev source, not the design.** Mailpit is the dev inbound mailbox (a real
SMTP sink with an HTTP API); a hosted deployment replaces it with IMAP or an inbound-SMTP hook by
writing another ``MailSource``. That is the entire reason the port exists — see ``ports.py``.

**MeetingApiClient talks to the public REST surface**, with the workspace's API key in
``X-API-Key``, exactly as any other client would. It never imports meeting-api, never touches its
database, and never posts to an internal route: the mailroom is a CONSUMER of the meeting API.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence
from urllib.parse import quote

import httpx

from .ports import MailMessage

log = logging.getLogger("vexa_mailroom.adapters")

DEFAULT_TIMEOUT = 15.0


class MailpitSource:
    """``MailSource`` over Mailpit's HTTP API (``/api/v1/messages`` + ``/api/v1/message/{id}/raw``).

    Mailpit lists newest-first with an ISO ``Created`` stamp per message. ``fetch_new`` pages from
    the newest end until it passes the cursor, then returns the tail in ARRIVAL order — so a poll
    that finds three new invitations acts on them in the order they were sent, which is what makes
    an update that arrives right behind its invitation apply on top of it rather than under it.
    """

    def __init__(self, base_url: str, *, transport: Optional[httpx.BaseTransport] = None,
                 timeout: float = DEFAULT_TIMEOUT, page_size: int = 50) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout
        self._page_size = page_size

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout,
                                 transport=self._transport)

    async def fetch_new(self, *, since: Optional[str], limit: int) -> Sequence[MailMessage]:
        out: list[MailMessage] = []
        async with self._client() as client:
            start, scanned = 0, 0
            while len(out) < limit:
                r = await client.get("/api/v1/messages",
                                     params={"start": start, "limit": self._page_size})
                r.raise_for_status()
                body = r.json() or {}
                rows = body.get("messages") or []
                if not rows:
                    break
                stop = False
                for row in rows:
                    created = str(row.get("Created") or "")
                    if since is not None and created and created <= since:
                        stop = True
                        break
                    out.append(MailMessage(id=str(row.get("ID") or ""), created=created, raw=b""))
                    if len(out) >= limit:
                        break
                scanned += len(rows)
                if stop or scanned >= int(body.get("total") or 0):
                    break
                start += self._page_size

            hydrated: list[MailMessage] = []
            for m in reversed(out):                     # newest-first → arrival order
                if not m.id:
                    continue
                raw = await client.get(f"/api/v1/message/{quote(m.id)}/raw")
                if raw.status_code != 200:
                    log.warning("mailpit: raw fetch for %s → %s", m.id, raw.status_code)
                    continue
                hydrated.append(MailMessage(id=m.id, created=m.created, raw=raw.content))
        return hydrated


class MeetingApiClient:
    """``MeetingApi`` over the public REST surface (the gateway), authenticated with an API key.

    Every method answers with a dict and never raises on an HTTP status: a control plane that
    refuses is a NOTICE, not a crashed poll loop (the fail-safe rule). ``{"error": ...}`` is the
    refusal shape ``service.py`` checks.
    """

    def __init__(self, base_url: str, api_key: str, *,
                 transport: Optional[httpx.BaseTransport] = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout,
                                 transport=self._transport,
                                 headers={"X-API-Key": self._api_key})

    async def create_planned_meeting(self, *, workspace_id: Optional[str], meeting_url: str,
                                     title: Optional[str], scheduled_at: Optional[str],
                                     auto_join: bool = True) -> dict:
        payload: dict[str, Any] = {"meeting_url": meeting_url, "auto_join": auto_join}
        if title:
            payload["title"] = title
        if scheduled_at:
            payload["scheduled_at"] = scheduled_at
        if workspace_id:
            payload["workspace_id"] = workspace_id
        return await self._json("POST", "/meetings", payload)

    async def update_planned_meeting(self, meeting_id: int, **fields: Any) -> dict:
        return await self._json("PATCH", f"/meetings/{int(meeting_id)}", dict(fields))

    async def cancel_planned_meeting(self, meeting_id: int) -> bool:
        try:
            async with self._client() as client:
                r = await client.delete(f"/meetings/{int(meeting_id)}")
        except httpx.HTTPError as e:
            log.warning("meeting-api: DELETE /meetings/%s → %s", meeting_id, e)
            return False
        # 404 = already gone (the desired end state); 409 = the bot FSM owns it, which the caller
        # surfaces as a notice rather than silently claiming the bot was recalled.
        return r.status_code in (200, 204, 404)

    async def _json(self, method: str, path: str, payload: dict) -> dict:
        try:
            async with self._client() as client:
                r = await client.request(method, path, json=payload)
        except httpx.HTTPError as e:
            log.warning("meeting-api: %s %s → %s", method, path, e)
            return {"error": f"transport: {e}"}
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str((r.json() or {}).get("detail") or "")
            except ValueError:
                detail = r.text[:200]
            return {"error": f"{r.status_code}: {detail}"}
        try:
            return r.json() or {}
        except ValueError:                                   # pragma: no cover - defensive
            return {"error": "non-JSON response"}
