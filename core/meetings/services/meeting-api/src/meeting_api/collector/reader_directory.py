"""reader_directory.py — a subject's verified address, from identity, for the owner's access list.

A reader who redeemed a share before meeting-api kept a roster (``data.share_viewers``) is known
only by id. When the OWNER opens the access list, meeting-api asks identity for those readers'
addresses over the internal tier it already uses (``ADMIN_API_URL`` + ``INTERNAL_API_SECRET``,
both declared in config.v1) and backfills the roster once. A lookup that fails answers ``None``:
the reader stays listed by id — still removable — and the next owner view tries again. It never
fails the owner's request.
"""
from __future__ import annotations

import os
from typing import Awaitable, Callable, Optional

TIMEOUT_S = 2.0
#: At most this many lookups per owner view, so a meeting with a long legacy roster is named over
#: a few views rather than holding one request open.
MAX_LOOKUPS = 25

EmailOf = Callable[[int], Awaitable[Optional[str]]]


def from_env() -> "Optional[EmailOf]":
    """The identity lookup, or ``None`` when this deployment wires no identity door."""
    base = (os.getenv("ADMIN_API_URL") or "").rstrip("/")
    secret = os.getenv("INTERNAL_API_SECRET") or ""
    if not base or not secret:
        return None

    async def email_of(user_id: int) -> Optional[str]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.get(f"{base}/internal/users/{int(user_id)}/email",
                                     headers={"X-Internal-Secret": secret})
            if r.status_code != 200:
                return None
            email = (r.json() or {}).get("email")
            return str(email).strip().lower() or None if email else None
        except Exception:  # noqa: BLE001 — a missing name is a degraded list, never a failed one
            return None

    return email_of
