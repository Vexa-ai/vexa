"""lifecycle.v1 egress adapter — HTTP callback to meeting-api.

Mirrors ``services/bot/src/adapters/lifecycle-http.ts``: POSTs each lifecycle.v1 event verbatim to
``meetingApiCallbackUrl`` (headers: ``content-type: application/json`` + optional
``x-internal-secret``), with bounded retry/backoff. ``emit`` never raises — a dropped status report
must not crash the bot or strand a seated meeting; it is logged and swallowed (P14).

Injected with a minimal async ``post(url, headers, body) -> (ok, status)`` callable so this is
offline-testable with a fake — no live meeting-api required.
"""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, Optional

#: (url, headers, json-body-str) -> (ok, status). The real adapter wraps httpx.AsyncClient.post.
PostFn = Callable[[str, dict[str, str], str], Awaitable[tuple[bool, int]]]


class LifecycleSink:
    def __init__(
        self,
        callback_url: str,
        *,
        internal_secret: Optional[str] = None,
        post: PostFn,
        retries: int = 5,
        backoff_s: float = 0.5,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        log: Optional[Callable[[str], None]] = None,
    ):
        self._url = callback_url
        self._headers = {"content-type": "application/json"}
        if internal_secret:
            self._headers["x-internal-secret"] = internal_secret
        self._post = post
        self._retries = max(1, retries)
        self._backoff_s = backoff_s
        self._sleep = sleep or asyncio.sleep
        self._log = log or (lambda m: print(m, flush=True))

    async def emit(self, event: dict) -> None:
        body = json.dumps(event)
        last_err: Optional[str] = None
        for attempt in range(1, self._retries + 1):
            try:
                ok, status = await self._post(self._url, self._headers, body)
                if ok:
                    return
                last_err = f"HTTP {status}"
            except Exception as e:  # noqa: BLE001 — network error, never propagate out of emit
                last_err = str(e)
            if attempt < self._retries:
                await self._sleep(self._backoff_s * (2 ** (attempt - 1)))
        self._log(
            f"[discord-bot] lifecycle.v1 {event.get('status')} POST failed after "
            f"{self._retries} attempt(s): {last_err or 'unknown'} (giving up)"
        )
