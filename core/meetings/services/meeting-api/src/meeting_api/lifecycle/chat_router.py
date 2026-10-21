"""The chat-send HTTP route — ``POST /bots/{platform}/{native_meeting_id}/chat`` (api.v1).

Sugar over the ``acts.v1`` ``chat_send`` act (ADR-0035): the route resolves the caller's meeting the
way the user stop does and publishes ``{"action":"chat_send","text":…}`` on that meeting's
``bot_commands:meeting:{id}`` channel, through the same ``CommandPublisher`` port. The bot types the
line into the meeting chat; whatever the meeting's chat reader then captures comes back through
``GET …/chat`` and the ``va:meeting:{id}:chat`` live frame like any other message.

Who may send: the meeting's OWNER only — the caller's own non-terminal rows for the room, the
identity ``IdentityGuard`` rebuilt from the gateway's signed token. A member of the workspace the
meeting is bound to may read it, but a line typed into the meeting speaks for the bot's owner, so
membership does not reach this route. The edge's ``bot`` scope is checked again here
(``route_scopes``), and a shared turn is refused (``regime.refuse_shared``; the row says
``shared: refuse``).

Refusals, in the order they are checked:

  * ``401`` — no signed caller identity.
  * ``422`` — the platform is not in the sealed ``Platform`` enum, its bot has no chat sender (only
    ``google_meet`` and ``jitsi`` do), or the body is not an object carrying a non-blank string
    ``text`` of at most ``MAX_CHAT_CHARS`` characters (the sealed ``ChatSendRequest``).
  * ``404`` — the caller has no live meeting row for ``(platform, native_meeting_id)``.
  * ``409`` — there is a row, but its bot is not in the meeting (still booting or waiting in the
    lobby, or already asked to stop), or no bot was listening when the act went out: nothing on the
    other end could type the line.
  * ``429`` — the meeting already took ``CHAT_POSTS_PER_WINDOW`` posts in the current
    ``CHAT_WINDOW_SEC``-second window (:class:`ChatRateLimit`, counted per meeting across every
    caller and every replica); ``Retry-After`` says when the window turns. Nothing was sent.
  * ``503`` — the command bus (redis) is unreachable; nothing was sent, retry.

``200`` means a listening bot RECEIVED the act (``status: "queued"``), not that the line appeared in
the meeting: the bot does not acknowledge a ``chat_send``.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Request

from ..bot_spawn.ports import MeetingRepo
from .stop import leave_command_channel
from .stop_router import _SUPPORTED_PLATFORMS, CommandPublisher, _active_rows, _resolve_user_id

#: The platforms whose bot carries a chat sender (``services/bot`` ``createChatSender``). The others
#: are in the ``Platform`` enum but nothing in their meeting would type the line.
CHAT_PLATFORMS = frozenset({"google_meet", "jitsi"})

#: The longest line this route hands a bot. Meet and Jitsi chat lines are short; a bound here keeps
#: a megabyte of text from being typed into somebody's meeting by one call.
MAX_CHAT_CHARS = 2000

#: A meeting takes at most this many chat posts in each ``CHAT_WINDOW_SEC``-second window. A person
#: typing, or an agent answering in turn, stays far below it; a loop posting into the room does not.
CHAT_POSTS_PER_WINDOW = 10
CHAT_WINDOW_SEC = 10

# The one status in which the bot is IN the meeting and listening on its command channel. A bot in
# `needs_help` is stuck before admission, and a `stopping` one is on its way out.
_IN_MEETING_STATUSES = frozenset({"active"})


def chat_send_payload(text: str) -> dict:
    """The ``acts.v1`` ``ChatSend`` body — exactly its two declared fields."""
    return {"action": "chat_send", "text": text}


class ChatRateLimit:
    """Counts a meeting's chat posts in fixed ``window``-second windows and refuses past ``limit``.

    In Redis when the store is a Redis client (one ``INCR`` per post on the window's own key, which
    expires with it, so every meeting-api replica draws on the same count), else in this process.
    """

    PREFIX = "chat:rate:meeting:"

    def __init__(self, store: Any = None, *, limit: int = CHAT_POSTS_PER_WINDOW,
                 window: int = CHAT_WINDOW_SEC, clock: Optional[Callable[[], float]] = None) -> None:
        if limit <= 0 or window <= 0:
            raise ValueError("limit and window must be > 0")
        self._redis = store if store is not None and hasattr(store, "pipeline") else None
        self.limit = limit
        self.window = window
        self._clock = clock or time.time
        self._counts: dict[str, tuple[int, int]] = {}

    async def take(self, meeting_id: Any) -> Optional[int]:
        """Count one post for the meeting. ``None`` when it is within the limit, else the whole
        seconds until the window turns (the ``Retry-After``)."""
        now = self._clock()
        slot = int(now // self.window)
        key = str(meeting_id)
        if self._redis is not None:
            redis_key = f"{self.PREFIX}{key}:{slot}"
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, self.window + 1)
                count, _ = await pipe.execute()
        else:
            self._counts = {k: v for k, v in self._counts.items() if v[0] == slot}
            count = self._counts.get(key, (slot, 0))[1] + 1
            self._counts[key] = (slot, count)
        if int(count) <= self.limit:
            return None
        return max(1, math.ceil((slot + 1) * self.window - now))


async def _text_from(request: Request) -> str:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="body must be JSON: {\"text\": \"…\"}")
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=422, detail="'text' must be a non-empty string")
    if len(text) > MAX_CHAT_CHARS:
        raise HTTPException(status_code=422,
                            detail=f"'text' is longer than {MAX_CHAT_CHARS} characters")
    return text


def build_chat_router(repo: MeetingRepo, publisher: CommandPublisher,
                      rate_limit: Optional[ChatRateLimit] = None) -> APIRouter:
    """The chat-send route over the injected ``MeetingRepo`` + ``CommandPublisher`` ports. The
    per-meeting limit counts in the command bus's Redis unless a ``rate_limit`` is given."""
    router = APIRouter()
    limit = rate_limit or ChatRateLimit(publisher)

    @router.post("/bots/{platform}/{native_meeting_id}/chat")
    async def send_chat(
        platform: str,
        native_meeting_id: str,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        if platform not in _SUPPORTED_PLATFORMS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unsupported platform '{platform}' — "
                    f"must be one of: {', '.join(sorted(_SUPPORTED_PLATFORMS))}"
                ),
            )
        if platform not in CHAT_PLATFORMS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"in-meeting chat cannot be sent on '{platform}' — "
                    f"supported: {', '.join(sorted(CHAT_PLATFORMS))}"
                ),
            )
        text = await _text_from(request)

        rows = await _active_rows(repo, user_id, platform, native_meeting_id)
        if not rows:
            raise HTTPException(status_code=404, detail="No active meeting for this bot")
        # Newest first, as `_active_rows` returns them: the room's current bot is the one to speak.
        meeting = next(
            (row for row in rows
             if row.get("status") in _IN_MEETING_STATUSES
             and not (row.get("data") or {}).get("stop_requested")),
            None,
        )
        if meeting is None:
            raise HTTPException(
                status_code=409,
                detail=f"the bot is not in the meeting (status '{rows[0].get('status')}') — "
                       "chat can be sent once it has been admitted",
            )

        try:
            retry_after = await limit.take(meeting["id"])
        except Exception as e:  # noqa: BLE001 — the count lives on the command bus: same failure
            raise HTTPException(
                status_code=503,
                detail="bot command bus (redis) unavailable; the message was not sent — retry",
            ) from e
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail=f"this meeting takes at most {limit.limit} chat messages every "
                       f"{limit.window} seconds; the message was not sent — retry after "
                       f"{retry_after} s",
                headers={"Retry-After": str(retry_after)},
            )

        try:
            receivers = await publisher.publish(
                leave_command_channel(meeting["id"]), json.dumps(chat_send_payload(text))
            )
        except Exception as e:  # noqa: BLE001 — Redis unreachable → narrow, retryable failure
            raise HTTPException(
                status_code=503,
                detail="bot command bus (redis) unavailable; the message was not sent — retry",
            ) from e
        # Redis answers with how many subscribers received the act. None did: the bot has not
        # subscribed yet, or it has gone — the line went nowhere, and the caller is told so.
        if isinstance(receivers, int) and receivers <= 0:
            raise HTTPException(
                status_code=409,
                detail="no bot was listening in the meeting; the message was not sent — retry "
                       "once the bot is in the meeting",
            )

        _log_chat_queued(user_id, meeting["id"], len(text))
        return {
            "status": "queued",
            "meeting_id": meeting["id"],
            "native_meeting_id": native_meeting_id,
        }

    return router


def _log_chat_queued(user_id, meeting_id, chars: int) -> None:
    # The length, never the text: a chat line is the user's words, and an operator log is not
    # where they belong.
    try:
        from ..obs import log_event

        log_event(
            "chat_send_queued", audience="user", span="bots.chat",
            user_id=user_id, meeting_id=str(meeting_id), fields={"chars": chars},
        )
    except Exception:  # noqa: BLE001 — logging never fails a send
        pass
