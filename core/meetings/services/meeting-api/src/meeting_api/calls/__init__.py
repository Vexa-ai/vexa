"""calls — a Vexa call is a meeting on the deployment's own LiveKit server (#1783, ADR-0048 d6).

``POST /bots {"platform": "vexa_call"}`` (bot_spawn) creates the room with its transcription
notice and spawns the listener — a bot that joins the room hidden and transcribes each
participant's track as its own channel. Participants get their room tokens from agent-api, which
owns the session the call belongs to; this module mints only the listener's and the room
service's tokens.

Front door (P6): import from here, never a deep module path.
"""
from __future__ import annotations

from .livekit import LiveKitConfig, RoomService, admin_token, listener_token, participant_token, presenter_token
from .spawn import (
    PLATFORM,
    CallLimits,
    Calls,
    CallsNotDeployed,
    InMemoryCallRooms,
    PreparedCall,
    discard_call,
    prepare_call,
    record_meeting,
)

__all__ = [
    "PLATFORM",
    "CallLimits",
    "Calls",
    "CallsNotDeployed",
    "InMemoryCallRooms",
    "LiveKitConfig",
    "PreparedCall",
    "RoomService",
    "admin_token",
    "discard_call",
    "listener_token",
    "participant_token",
    "presenter_token",
    "prepare_call",
    "record_meeting",
]
