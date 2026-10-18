"""``POST /voice-notes/recorder`` — the MCP tool ``record_voice_note`` (#1783).

An agent has no microphone, so asking it to record a voice note can only hand the act to the
person: the answer carries ``ui_action: open_voice_note``, and the person's open Minutes tab opens
the recorder on it (the same shape ``connection_request`` answers with for the Connections panel).
Nothing is created here: the meeting row is made when the recording starts in the browser
(``POST /bots {"platform": "voice_note"}``), so a request nobody follows up leaves nothing behind.

A worker with nobody in the loop (a routine, an event, a meeting run) is refused with the shared
``human_session_required`` answer: there is nobody to speak.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from ..regime import require_person

UI_ACTION = "open_voice_note"

INSTRUCTION = (
    "The recorder was asked to open in the person's Minutes tab; you recorded nothing and cannot "
    "hear the note. Do not say it is recording until the person confirms. If they are not in "
    "Minutes, tell them to open a new chat there and choose 'Record a voice note'. When they press "
    "Stop the note is a meeting like any other: find it with list_meetings (platform voice_note, "
    "newest first) and read it with get_meeting_transcript."
)


def build_recorder_router() -> APIRouter:
    router = APIRouter()

    @router.post("/voice-notes/recorder", dependencies=[Depends(require_person)])
    async def record_voice_note(x_user_id: Optional[str] = Header(default=None)):
        """Open the voice-note recorder in the person's Minutes tab, so they can dictate a note
        that is transcribed like a meeting. You cannot record or hear audio yourself: this only
        asks the person's open Minutes tab to start the recorder, and they speak and press Stop.
        Call it when the person asks to record, dictate or take a voice note. It is refused in a
        run with nobody present (a routine or background flow). Afterwards the note is a meeting
        with platform voice_note: find it with list_meetings and read it with
        get_meeting_transcript."""
        if not x_user_id:
            raise HTTPException(status_code=401, detail="Missing user identity")
        return {"status": "awaiting_user", "ui_action": UI_ACTION, "instruction": INSTRUCTION}

    return router
