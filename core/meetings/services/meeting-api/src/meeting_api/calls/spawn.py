"""Starting a call: the room, its notice, and the listener's invocation block (#1783).

``POST /bots {"platform": "vexa_call", "session_id": "s-…"}`` makes a call. The caller becomes the
meeting's owner. Before the row and the listener exist, the room is created on the LiveKit server
with its metadata — the transcription notice every participant's client shows, and the owner and
session the room belongs to (agent-api reads them back to bind the call to the session and to mint
participant tokens: ADR-0048 decision 6). The listener's invocation block carries a hidden,
subscribe-only token for that room.
"""
from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .livekit import LiveKitConfig, RoomService, listener_token

PLATFORM = "vexa_call"
SESSION_ID_RE = re.compile(r"^s-[0-9a-f]{8,64}$")
DEFAULT_MAX_DURATION_SEC = 4 * 3600
DEFAULT_EMPTY_GRACE_SEC = 30
#: A call started and then cancelled before anyone joined ends this soon: its listener never waits
#: alone in an empty room for minutes (founder 2026-10-15, "never lingers").
DEFAULT_FIRST_JOIN_TIMEOUT_SEC = 30
DEFAULT_MAX_PARTICIPANTS = 50
#: The listener's token outlives the call's cap by this much (joining late, reconnecting).
LISTENER_TOKEN_MARGIN_SEC = 600


class CallsNotDeployed(Exception):
    """This deployment runs no LiveKit server (no LIVEKIT_URL)."""


class CallRooms(Protocol):
    async def create_room(self, room: str, *, metadata: dict, empty_timeout_sec: int,
                          departure_timeout_sec: int, max_participants: int) -> dict: ...

    async def update_metadata(self, room: str, metadata: dict) -> dict: ...

    async def delete_room(self, room: str) -> None: ...


@dataclass
class InMemoryCallRooms:
    """The offline room service (tests, the in-process app)."""

    rooms: dict = field(default_factory=dict)
    deleted: list = field(default_factory=list)

    async def create_room(self, room: str, *, metadata: dict, empty_timeout_sec: int,
                          departure_timeout_sec: int, max_participants: int) -> dict:
        self.rooms[room] = {"metadata": dict(metadata), "empty_timeout": empty_timeout_sec,
                            "departure_timeout": departure_timeout_sec,
                            "max_participants": max_participants}
        return {"name": room}

    async def update_metadata(self, room: str, metadata: dict) -> dict:
        self.rooms[room]["metadata"] = dict(metadata)
        return {"name": room}

    async def delete_room(self, room: str) -> None:
        self.rooms.pop(room, None)
        self.deleted.append(room)

    async def list_participants(self, room: str) -> list[dict]:
        return list((self.rooms.get(room) or {}).get("participants") or [])


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    return value


def _env_bool(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CallLimits:
    max_duration_sec: int
    empty_grace_sec: int
    first_join_timeout_sec: int
    max_participants: int
    transcription_switchable: bool

    @classmethod
    def from_env(cls) -> "CallLimits":
        return cls(
            max_duration_sec=_env_int("CALL_MAX_DURATION_SEC", DEFAULT_MAX_DURATION_SEC),
            empty_grace_sec=_env_int("CALL_EMPTY_GRACE_SEC", DEFAULT_EMPTY_GRACE_SEC, minimum=0),
            first_join_timeout_sec=_env_int("CALL_FIRST_JOIN_TIMEOUT_SEC", DEFAULT_FIRST_JOIN_TIMEOUT_SEC),
            max_participants=_env_int("CALL_MAX_PARTICIPANTS", DEFAULT_MAX_PARTICIPANTS),
            transcription_switchable=_env_bool("CALL_TRANSCRIPTION_SWITCHABLE"),
        )


@dataclass
class Calls:
    """A deployment's calls: the LiveKit server and its room service."""

    config: LiveKitConfig
    rooms: CallRooms

    @classmethod
    def from_env(cls) -> Optional["Calls"]:
        cfg = LiveKitConfig.from_env()
        return cls(cfg, RoomService(cfg)) if cfg is not None else None


@dataclass(frozen=True)
class PreparedCall:
    native_meeting_id: str
    transcribed: bool
    #: invocation.v1 ``call`` — carries the listener's token: never stored, never logged.
    invocation: dict
    #: ``meeting.data.call`` — what a reader of the meeting may see.
    data: dict
    #: The room's metadata (the notice, its owner and session).
    notice: dict


async def record_meeting(calls: Calls, prepared: PreparedCall, meeting_id: int) -> None:
    """Name the meeting row in the room's metadata once it exists: agent-api binds a call to its
    session only when the row it is told matches this one."""
    await calls.rooms.update_metadata(prepared.native_meeting_id,
                                      {**prepared.notice, "meeting_id": int(meeting_id)})


def _hidden(p: dict) -> bool:
    return bool((p.get("permission") or {}).get("hidden"))


async def end_call_room(calls: Calls, meeting_row: dict) -> None:
    """THE CALL'S MEETING HAS ENDED — its listener is gone (live app.dev 2026-10-18).

    Without this the room outlived its meeting by the room's departure timeout (and forever once
    somebody rejoined it): agent-api read the room as the session's live call, so starting the call
    again in the same chat only took a token for that room — no new meeting, no listener, nothing
    transcribed, and the panel stayed on the ended meeting ("Ended · Bot left").

    The room's metadata is marked ``ended`` first (agent-api then treats the call as over and the next
    start makes a fresh meeting), then the room is deleted if no PERSON is in it. A person still in
    it (the transcriber was stopped mid-call) keeps their connection. Best effort, never raises."""
    if not isinstance(meeting_row, dict) or meeting_row.get("platform") != PLATFORM:
        return
    room = str(meeting_row.get("native_meeting_id") or meeting_row.get("platform_specific_id") or "")
    if not room:
        return
    data = meeting_row.get("data") if isinstance(meeting_row.get("data"), dict) else {}
    call = data.get("call") if isinstance(data.get("call"), dict) else {}
    notice = {
        "vexa_call": 1,
        "transcribed": bool(call.get("transcribed", True)),
        "transcription_switchable": bool(call.get("transcription_switchable", False)),
        "owner": str(meeting_row.get("user_id") or ""),
        "session_id": call.get("session_id"),
        "meeting_id": meeting_row.get("id"),
        "ended": True,
    }
    try:
        await calls.rooms.update_metadata(room, notice)
    except Exception:
        pass
    people = None
    lister = getattr(calls.rooms, "list_participants", None)
    if lister is not None:
        try:
            people = [p for p in (await lister(room)) if isinstance(p, dict) and not _hidden(p)]
        except Exception:
            people = None          # unknown: never disconnect someone on a failed read
    if people == []:
        try:
            await calls.rooms.delete_room(room)
        except Exception:
            pass


async def discard_call(calls: Calls, prepared: PreparedCall) -> None:
    """Remove the room of a call whose meeting never started (the bot limit, the service authority,
    a listener that could not spawn). Best effort: the room's own first-join timeout ends it anyway."""
    try:
        await calls.rooms.delete_room(prepared.native_meeting_id)
    except Exception:
        pass


def new_room_name() -> str:
    return f"call-{secrets.token_hex(6)}"


async def prepare_call(calls: Optional[Calls], *, user_id: int, session_id: Optional[str],
                       transcribe: Optional[bool], limits: Optional[CallLimits] = None) -> PreparedCall:
    """Create the room and the listener's block. Raises CallsNotDeployed, or ValueError with a
    message a 422 can carry."""
    if calls is None:
        raise CallsNotDeployed("calls are not deployed here: no LiveKit server is configured")
    limits = limits or CallLimits.from_env()
    if session_id is not None and (not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id)):
        raise ValueError("'session_id' must be a session id (s-…)")
    transcribed = True
    if transcribe is not None:
        if not isinstance(transcribe, bool):
            raise ValueError("'transcribe' must be true or false")
        if transcribe is False and not limits.transcription_switchable:
            raise ValueError("calls on this deployment are always transcribed "
                             "(CALL_TRANSCRIPTION_SWITCHABLE is off)")
        transcribed = transcribe
    room = new_room_name()
    notice = {
        "vexa_call": 1,
        "transcribed": transcribed,
        "transcription_switchable": limits.transcription_switchable,
        "owner": str(user_id),
        "session_id": session_id,
    }
    await calls.rooms.create_room(room, metadata=notice, empty_timeout_sec=limits.first_join_timeout_sec,
                                  departure_timeout_sec=limits.empty_grace_sec,
                                  max_participants=limits.max_participants)
    invocation = {
        "url": calls.config.internal_url,
        "room": room,
        "token": listener_token(calls.config, room=room,
                                ttl_sec=limits.max_duration_sec + LISTENER_TOKEN_MARGIN_SEC),
        "maxDurationSec": limits.max_duration_sec,
        "emptyGraceSec": limits.empty_grace_sec,
        "firstJoinTimeoutSec": limits.first_join_timeout_sec,
    }
    data = {"room": room, "url": calls.config.url, "session_id": session_id, "transcribed": transcribed,
            "transcription_switchable": limits.transcription_switchable,
            "max_duration_sec": limits.max_duration_sec}
    return PreparedCall(native_meeting_id=room, transcribed=transcribed, invocation=invocation, data=data,
                        notice=notice)
