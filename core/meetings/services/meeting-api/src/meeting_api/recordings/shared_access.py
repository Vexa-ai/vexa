"""shared_access.py — may a NON-owner read this meeting's recording?

Two facts must both hold, and both are the owner's decisions recorded on ``meeting.data`` by the
meetings collector (the single writer of those keys):

1. the caller can read the meeting at all — a transcript-share recipient (``transcript_viewers``)
   or a member of the shared workspace the meeting is bound to (``workspace_id``) — the same access
   union the transcript reads apply; and
2. the owner allowed the people they share with the recording (``share_settings.recording``).

Absent either, the answer is no (default-deny, P20). The owner's own path is unchanged and never
comes here.
"""
from __future__ import annotations

from ..collector.share_access import recording_shared


def may_read_shared_recording(data: dict, user_id: int, member_workspaces=None) -> bool:
    if not isinstance(data, dict) or not recording_shared(data):
        return False
    if user_id in (data.get("transcript_viewers") or []):
        return True
    ws = data.get("workspace_id")
    return bool(ws and member_workspaces and ws in member_workspaces)
