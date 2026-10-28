"""segment_entry.py — how a session signs one ``transcription_segments`` entry (transcript.v1 StreamEntry).

Every bot appends to the one ``transcription_segments`` stream, so the stream cannot say which meeting
an entry may speak for. Each entry therefore carries, beside its ``payload``:

  * ``auth`` — the ``header.payload`` part of the session's MeetingToken (its claims name the meeting);
  * ``sig``  — HMAC-SHA256 of the ``payload`` string, keyed with the whole token, as 64 hex digits.

The collector rebuilds the token from ``auth`` with the secret that minted it, so the bearer never
enters Redis, and admits the entry only when the signature holds, the token is valid and the payload's
``meeting_id`` is the token's.

THIS FILE IS VENDORED byte for byte into meeting-api (``collector/segment_entry.py``) and the compose
stack test (``deploy/compose/tests/_segment_entry.py``); ``gate:fact-parity`` (fact
``segment-entry-signer``) compares the copies. Edit the copy in ``core/meetings/contracts/transcript.v1/``
and copy it out. The bot's TypeScript twin (``bot/src/adapters/transcript-redis.ts`` ``entryAuth``) is
held to it by the contract's ``SignedEntryVector`` golden. Standard library only.
"""
from __future__ import annotations

import hashlib
import hmac


def signature(token: str, payload: str) -> str:
    """``sig``: hex HMAC-SHA256 of ``payload`` keyed with the whole MeetingToken."""
    return hmac.new(token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def signed_entry(token: str, payload: str) -> dict:
    """The stream fields a session writes for ``payload``. A value that is not a three-part token signs
    nothing: the entry carries only its payload, and the collector drops it like any unsigned one."""
    parts = (token or "").split(".")
    if len(parts) != 3 or not all(parts):
        return {"payload": payload}
    return {"payload": payload, "auth": f"{parts[0]}.{parts[1]}", "sig": signature(token, payload)}
