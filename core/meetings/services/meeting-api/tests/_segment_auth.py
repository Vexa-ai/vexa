"""A bot's signed ``transcription_segments`` entry, for tests that drive the collector's stream paths.

The collector admits an entry only when it carries ``auth`` (the session MeetingToken's
header.payload) and ``sig`` (HMAC-SHA256 of the ``payload`` keyed with the token) for the meeting
the payload names — what the bot's sink writes (``transcript-redis.ts`` ``entryAuth``). Tests set
``ADMIN_TOKEN`` to :data:`SECRET` (the ``admin_token`` fixture) and enqueue through
:func:`signed_xadd`.
"""
from __future__ import annotations

import json
from typing import Optional

import pytest

from meeting_api.bot_spawn.invocation import mint_meeting_token
from meeting_api.collector import signed_entry

SECRET = "segment-entry-secret-for-tests-0123456789abcdef"
STREAM = "transcription_segments"


@pytest.fixture
def admin_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    return SECRET


def token_for(meeting_id: int, *, secret: str = SECRET, ttl_seconds: int = 3600) -> str:
    return mint_meeting_token(int(meeting_id), 1, "google_meet", "abc-defg-hij", ttl_seconds=ttl_seconds,
                              secret=secret, session_uid=f"conn-{meeting_id}")


def signed_fields(data: dict, *, token: Optional[str] = None) -> dict:
    """The stream fields a bot writes for ``data`` (the payload dict)."""
    return signed_entry(token or token_for(int(data["meeting_id"])), json.dumps(data))


async def signed_xadd(bus, data: dict, *, token: Optional[str] = None, stream: str = STREAM) -> str:
    """Enqueue ``data`` as its session's bot would (through the bus's own redis client)."""
    return await bus._client.xadd(stream, signed_fields(data, token=token))
