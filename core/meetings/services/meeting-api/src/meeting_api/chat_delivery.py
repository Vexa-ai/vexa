"""The delivery record of one chat send — what ``GET /bots/{platform}/{native}/chat/{request_id}``
answers.

``POST /bots/{platform}/{native}/chat`` mints a ``request_id``, records it here as ``queued`` BEFORE
it publishes the ``acts.v1`` ``chat_send`` (the act carries the id), and removes the record again
when nothing received the act. The bot reports the outcome as a ``chat_send_result`` entry on its
signed ``transcription_segments`` stream; the collector settles the record from it (``settle``):
``posted``, or ``failed`` with the bot's ``ChatSendFailure`` reason.

The record is keyed by the request id alone (``chat:send:{request_id}``): an unguessable uuid4 hex,
unique across meetings. It names the meeting it was sent into and the user who sent it, so

  * a result settles it only when it comes from THAT meeting's bot (the entry's ``meeting_id`` is
    bound to the bot's signed MeetingToken before ``ingest`` sees it), and only while it is still
    ``queued`` — a bot cannot invent a send or overwrite an outcome;
  * only its sender reads it back, and only under the platform and native id it was sent to.

A record lives ``CHAT_SEND_TTL_SEC`` seconds from the send; settling keeps that TTL. A record still
``queued`` ``UNCONFIRMED_AFTER_SEC`` seconds after the send reads as ``unconfirmed``: the bot waits
at most a few seconds for the line to show, so no answer by then means none is coming (a bot that
predates the result message never answers at all).

In Redis when the store is a Redis client (every meeting-api replica and the collector read the same
records), else in this process — the app factory's in-memory command bus.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional

#: How long a send's record is kept, from the send.
CHAT_SEND_TTL_SEC = 3600

#: A send still ``queued`` this long after it went out reads as ``unconfirmed``.
UNCONFIRMED_AFTER_SEC = 60

#: The reasons a bot reports a line as not posted (``services/bot`` ``ChatSendFailure``). A failure
#: naming anything else is recorded as ``failed`` with reason ``unknown``.
CHAT_SEND_FAILURES = frozenset({
    "unsupported_platform", "no_composer", "not_landed", "no_chat_api", "page_error", "empty_text",
})

#: The one form a request id has on the wire (acts.v1 ``ChatSend.id``).
REQUEST_ID_FORM = re.compile(r"[A-Za-z0-9_-]{1,64}")

PREFIX = "chat:send:"


def chat_send_key(request_id: str) -> str:
    return f"{PREFIX}{request_id}"


class ChatDeliveryLedger:
    """The chat-send records: ``record`` → ``settle`` → ``read``, with ``discard`` for a send that
    never went out."""

    def __init__(self, store: Any = None, *, ttl: int = CHAT_SEND_TTL_SEC,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self._redis = (store if store is not None and hasattr(store, "get") and hasattr(store, "set")
                       else None)
        self.ttl = ttl
        self._clock = clock or time.time
        self._local: dict[str, tuple[float, dict]] = {}   # key → (expires_at, record)

    # ── storage ────────────────────────────────────────────────────────────────────────────────

    async def _get(self, key: str) -> Optional[dict]:
        if self._redis is not None:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            try:
                record = json.loads(raw)
            except (TypeError, ValueError):
                return None
            return record if isinstance(record, dict) else None
        held = self._local.get(key)
        if held is None:
            return None
        if held[0] <= self._clock():
            self._local.pop(key, None)
            return None
        return dict(held[1])

    # ── the ledger ─────────────────────────────────────────────────────────────────────────────

    async def record(self, request_id: str, *, user_id: int, meeting_id: int, platform: str,
                     native_meeting_id: str) -> None:
        """Record a send as ``queued``. Raises when the store is unreachable."""
        record = {"status": "queued", "user_id": int(user_id), "meeting_id": int(meeting_id),
                  "platform": platform, "native_meeting_id": native_meeting_id,
                  "created": self._clock()}
        key = chat_send_key(request_id)
        if self._redis is not None:
            await self._redis.set(key, json.dumps(record), ex=self.ttl, nx=True)
        else:
            self._local[key] = (self._clock() + self.ttl, record)

    async def discard(self, request_id: str) -> None:
        key = chat_send_key(request_id)
        if self._redis is not None:
            await self._redis.delete(key)
        else:
            self._local.pop(key, None)

    async def settle(self, request_id: str, *, meeting_id: int, posted: bool,
                     reason: Optional[str] = None) -> bool:
        """Apply the bot's result to a ``queued`` record of ``meeting_id``. Returns whether a record
        changed: an unknown, expired or already-settled id, or one sent into another meeting, is left
        as it is."""
        if not isinstance(request_id, str) or not REQUEST_ID_FORM.fullmatch(request_id):
            return False
        key = chat_send_key(request_id)
        record = await self._get(key)
        if record is None or record.get("status") != "queued":
            return False
        if record.get("meeting_id") != int(meeting_id):
            return False
        record["status"] = "posted" if posted else "failed"
        if not posted:
            record["reason"] = reason if reason in CHAT_SEND_FAILURES else "unknown"
        record["settled"] = self._clock()
        if self._redis is not None:
            # XX: never brings back a record that expired meanwhile. KEEPTTL: the send's own expiry.
            return bool(await self._redis.set(key, json.dumps(record), xx=True, keepttl=True))
        expires_at = self._local[key][0]
        self._local[key] = (expires_at, record)
        return True

    async def read(self, request_id: str) -> Optional[dict]:
        """The record, or ``None`` when the id is unknown or expired. A ``queued`` record older than
        ``UNCONFIRMED_AFTER_SEC`` reads as ``unconfirmed``."""
        if not isinstance(request_id, str) or not REQUEST_ID_FORM.fullmatch(request_id):
            return None
        record = await self._get(chat_send_key(request_id))
        if record is None:
            return None
        if (record.get("status") == "queued"
                and self._clock() - float(record.get("created") or 0) >= UNCONFIRMED_AFTER_SEC):
            record["status"] = "unconfirmed"
        return record
