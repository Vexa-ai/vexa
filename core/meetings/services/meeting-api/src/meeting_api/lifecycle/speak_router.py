"""Speak in a live meeting — ``POST``/``DELETE /bots/{platform}/{native_meeting_id}/speak`` (api.v1).

Sugar over the ``acts.v1`` ``speak`` / ``speak_stop`` acts (ADR-0035), the way ``chat_router`` is over
``chat_send``: the route resolves ONE bot and publishes ``{"action":"speak","text":…,"id":<request_id>}``
(plus ``voice`` when one was named) on that bot's ``bot_commands:meeting:{id}`` channel; the bot's
``SpeakController`` turns it into audio in the call.

Who may speak (consent, #1866): the meeting's OWNER — the caller's own non-terminal rows for the room —
or, when the caller owns none, an ``owner``/``contributor`` of the workspace the meeting is bound to
(``reconfigure_router._editable_member_rows``, the same rule as ``PUT …/config``). A ``reader`` is 403,
an unconfirmable role 503. A bot speaks only if it was spawned with ``voice_agent_enabled: true``
(``data.voice_agent_enabled``); a row from before that flag existed has none and is refused, never
assumed on.

``POST`` refusals, in the order they are checked:

  * ``401`` — no signed caller identity.
  * ``403`` — a turn with nobody in the loop (``regime.LIVE_BOT_VERBS``), as for chat.
  * ``422`` — a platform with no meeting to speak into (``SPEAK_PLATFORMS``).
  * ``413`` — a body over ``MAX_SPEAK_BODY_BYTES``.
  * ``422`` — the body is not a JSON object; ``text`` missing, blank or over ``MAX_SPEAK_CHARS``;
    ``voice`` malformed; or audio input (``audio_base64``/``audio_url``, #514).
  * ``404`` — nothing the caller may speak through in this room (one answer for "no such meeting" and
    "not yours").
  * ``403`` / ``503`` — a workspace member who is not an editor / identity cannot say.
  * ``409`` — the chosen bot is not ``active`` or has ``stop_requested``; or it was started without
    ``voice_agent_enabled: true``.
  * ``429`` — the meeting already took ``SPEAK_POSTS_PER_WINDOW`` requests in this
    ``SPEAK_WINDOW_SEC``-second window (counted per meeting across callers and replicas, on its own key
    — chat's count is untouched); ``Retry-After`` says when the window turns.
  * ``503`` — the command bus (redis) is unreachable.
  * ``409`` — no bot received the act.

``200`` (``status: "queued"``) means a listening bot RECEIVED the act, not that the audio has played;
there is no result channel yet. ``request_id`` is the act's ``id`` and every bot log line about the
utterance carries it.

``DELETE`` resolves bots the same way but needs neither ``active`` nor the voice flag and is never
rate-limited — stopping must always work. It publishes ``speak_stop`` to every bot it resolves.

Audit: ``bot_speak_queued`` / ``bot_speak_refused`` / ``bot_speak_stop_sent`` carry the caller, the
meeting, the request id, the caller's role and the length — never the spoken text, which is meeting
content.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ..obs import log_event
from ..regime import live_bot_guard
from .chat_router import ChatRateLimit
from .reconfigure_router import WorkspaceRoles, _editable_member_rows
from .stop import leave_command_channel
from .stop_router import CommandPublisher, _active_rows, _resolve_user_id

#: The platforms with a meeting a bot can be heard in. ``browser_session``, ``voice_note`` and
#: ``vexa_call`` have no call to speak into.
SPEAK_PLATFORMS = frozenset({"google_meet", "teams", "zoom", "jitsi"})

#: About thirty seconds of speech — one utterance, not a monologue.
MAX_SPEAK_CHARS = 500
MAX_SPEAK_BODY_BYTES = 16384

SPEAK_POSTS_PER_WINDOW = 6
SPEAK_WINDOW_SEC = 60
SPEAK_RATE_PREFIX = "speak:rate:meeting:"

#: A TTS preset id, passed through to the bot. ``auto`` (or none) means the bot's ``TTS_VOICE``.
_VOICE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

_IN_MEETING_STATUSES = frozenset({"active"})

_SPEAK_ACTION = "speak in this meeting"

_NOT_VOICE_ENABLED = ("this bot was started without voice_agent_enabled: true, so it cannot speak — "
                      "start a bot with that flag")


def speak_payload(text: str, request_id: str, voice: Optional[str]) -> dict:
    """The ``acts.v1`` ``Speak`` body — exactly its declared fields; ``voice`` only when named."""
    act = {"action": "speak", "text": text, "id": request_id}
    if voice:
        act["voice"] = voice
    return act


class _Refused(HTTPException):
    """A refusal that is also an audit line (``bot_speak_refused`` with ``reason``)."""

    def __init__(self, status_code: int, reason: str, detail: str,
                 headers: Optional[dict] = None) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.reason = reason


def _parse(raw: bytes) -> tuple[str, Optional[str]]:
    """The ``text`` and the voice to pass on (``None`` = the bot's default), or a 413/422."""
    if len(raw) > MAX_SPEAK_BODY_BYTES:
        raise _Refused(413, "body_too_large",
                       f"the request body is larger than {MAX_SPEAK_BODY_BYTES} bytes — send only "
                       "the text to speak")
    try:
        body = json.loads(raw or b"null")
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise _Refused(422, "bad_body", "the body must be a JSON object: {\"text\": \"…\"}")
    if body.get("audio_base64") is not None or body.get("audio_url") is not None:
        raise _Refused(422, "audio_input", "audio input is not served by this route yet — see #514")
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise _Refused(422, "bad_text", "'text' must be a non-empty string — send the sentence to say")
    if len(text) > MAX_SPEAK_CHARS:
        raise _Refused(422, "text_too_long",
                       f"'text' is longer than {MAX_SPEAK_CHARS} characters — split it into "
                       "shorter requests")
    voice = body.get("voice")
    if voice is not None and (not isinstance(voice, str) or not _VOICE.match(voice)):
        raise _Refused(422, "bad_voice",
                       "'voice' must be a TTS preset id: letters, digits, '_', '.' or '-', at most "
                       "64 characters — or leave it out to use the server's default voice")
    return text, (None if voice in (None, "auto") else voice)


def _check_platform(platform: str) -> None:
    if platform not in SPEAK_PLATFORMS:
        raise _Refused(422, "unsupported_platform",
                       f"a bot cannot speak on '{platform}' — supported: "
                       f"{', '.join(sorted(SPEAK_PLATFORMS))}")


async def _resolve(repo, roles: WorkspaceRoles, user_id: int, platform: str, native_meeting_id: str,
                   x_user_workspaces: Optional[str]) -> tuple[list, str]:
    """The rows the caller may speak through, newest first, and the role that grants it
    (``owner`` for their own rows, else ``editor``). 404 when there are none; 403/503 from the
    workspace rule."""
    rows = await _active_rows(repo, user_id, platform, native_meeting_id)
    if rows:
        return rows, "owner"
    try:
        rows = await _editable_member_rows(repo, roles, user_id, platform, native_meeting_id,
                                           x_user_workspaces, action=_SPEAK_ACTION)
    except HTTPException as e:
        reason = "role_unconfirmed" if e.status_code == 503 else "role_not_editor"
        raise _Refused(e.status_code, reason, e.detail) from e
    if not rows:
        raise _Refused(404, "no_meeting",
                       "no running bot in this meeting that you can speak through — start one "
                       "with voice_agent_enabled: true")
    return rows, "editor"


def build_speak_router(repo, publisher: CommandPublisher, roles: WorkspaceRoles,
                       rate_limit: Optional[ChatRateLimit] = None) -> APIRouter:
    """The speak and speak-stop routes over the injected ``MeetingRepo`` + ``CommandPublisher`` +
    ``WorkspaceRoles`` ports. The per-meeting count lives in the command bus's Redis unless a
    ``rate_limit`` is given."""
    router = APIRouter()
    limit = rate_limit or ChatRateLimit(publisher, limit=SPEAK_POSTS_PER_WINDOW,
                                        window=SPEAK_WINDOW_SEC, prefix=SPEAK_RATE_PREFIX)

    @router.post("/bots/{platform}/{native_meeting_id}/speak",
                 dependencies=[Depends(live_bot_guard("POST", "/bots/{platform}/{native_meeting_id}/speak"))])
    async def speak(
        platform: str,
        native_meeting_id: str,
        request: Request,
        x_user_id: Optional[str] = Header(default=None),
        x_user_workspaces: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        meeting_id: Any = None
        role: Optional[str] = None
        try:
            _check_platform(platform)
            # One read: the size check needs the raw bytes, and `request.json()` after `body()` would
            # read the stream twice.
            text, voice = _parse(await request.body())
            rows, role = await _resolve(repo, roles, user_id, platform, native_meeting_id,
                                        x_user_workspaces)
            meeting = next(
                (row for row in rows
                 if row.get("status") in _IN_MEETING_STATUSES
                 and not (row.get("data") or {}).get("stop_requested")),
                None,
            )
            if meeting is None:
                raise _Refused(409, "not_in_meeting",
                               f"the bot is not in the meeting (status '{rows[0].get('status')}') — "
                               "it can speak once it has been admitted")
            meeting_id = meeting["id"]
            if (meeting.get("data") or {}).get("voice_agent_enabled") is not True:
                raise _Refused(409, "voice_not_enabled", _NOT_VOICE_ENABLED)

            try:
                retry_after = await limit.take(meeting_id)
            except Exception as e:  # noqa: BLE001 — the count lives on the command bus: same failure
                raise _Refused(503, "bus_unavailable",
                               "bot command bus (redis) unavailable; nothing was said — retry") from e
            if retry_after is not None:
                raise _Refused(429, "rate_limited",
                               f"this meeting takes at most {limit.limit} speak requests every "
                               f"{limit.window} seconds; nothing was said — retry after "
                               f"{retry_after} s",
                               headers={"Retry-After": str(retry_after)})

            request_id = uuid.uuid4().hex
            try:
                receivers = await publisher.publish(
                    leave_command_channel(meeting_id),
                    json.dumps(speak_payload(text, request_id, voice)))
            except Exception as e:  # noqa: BLE001 — Redis unreachable → narrow, retryable failure
                raise _Refused(503, "bus_unavailable",
                               "bot command bus (redis) unavailable; nothing was said — retry") from e
            # Redis answers with how many subscribers received the act. None did: the bot has not
            # subscribed yet, or it has gone.
            if isinstance(receivers, int) and receivers <= 0:
                raise _Refused(409, "no_listener",
                               "no bot was listening in the meeting; nothing was said — retry once "
                               "the bot is in the meeting")
        except _Refused as refused:
            _log("bot_speak_refused", user_id, meeting_id,
                 {"reason": refused.reason, "status": refused.status_code, "role": role})
            raise

        _log("bot_speak_queued", user_id, meeting_id,
             {"request_id": request_id, "chars": len(text), "role": role})
        return {
            "status": "queued",
            "request_id": request_id,
            "meeting_id": meeting_id,
            "platform": platform,
            "native_meeting_id": native_meeting_id,
            "chars": len(text),
        }

    @router.delete("/bots/{platform}/{native_meeting_id}/speak",
                   dependencies=[Depends(live_bot_guard("DELETE", "/bots/{platform}/{native_meeting_id}/speak"))])
    async def stop_speaking(
        platform: str,
        native_meeting_id: str,
        x_user_id: Optional[str] = Header(default=None),
        x_user_workspaces: Optional[str] = Header(default=None),
    ):
        user_id = _resolve_user_id(x_user_id)
        role: Optional[str] = None
        try:
            _check_platform(platform)
            rows, role = await _resolve(repo, roles, user_id, platform, native_meeting_id,
                                        x_user_workspaces)
            act = json.dumps({"action": "speak_stop"})
            received = 0
            try:
                for row in rows:
                    receivers = await publisher.publish(leave_command_channel(row["id"]), act)
                    if isinstance(receivers, int) and receivers > 0:
                        received += 1
            except Exception as e:  # noqa: BLE001 — the command bus is down: narrow, retryable
                raise _Refused(503, "bus_unavailable",
                               "bot command bus (redis) unavailable; the stop was not sent — "
                               "retry") from e
            if not received:
                raise _Refused(409, "no_listener",
                               "no bot was listening in the meeting; there is nothing to stop")
        except _Refused as refused:
            _log("bot_speak_refused", user_id, None,
                 {"reason": refused.reason, "status": refused.status_code, "role": role,
                  "stop": True})
            raise

        _log("bot_speak_stop_sent", user_id, rows[0]["id"], {"bots": received, "role": role})
        return {"status": "stop_sent", "bots": received}

    return router


def _log(event: str, user_id, meeting_id, fields: dict) -> None:
    # Who, which meeting, the request id and the length — never the words: a spoken line is
    # meeting content, and an operator log is not where it belongs.
    try:
        log_event(event, audience="user", span="bots.speak", user_id=user_id,
                  meeting_id=str(meeting_id) if meeting_id is not None else None,
                  fields={k: v for k, v in fields.items() if v is not None})
    except Exception:  # noqa: BLE001 — logging never fails a request
        pass
