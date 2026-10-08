"""A meeting whose transcript its owner deleted is not replayed by the live view or folded into chat.

meeting-api keeps the meeting row after a typed delete and stamps `data.artifact_deletion` on it, so
the access check alone still passes for the owner and the bound workspace's members. Both transcript
readers in agent-api — the live SSE and the chat grounding — refuse a row carrying that stamp,
whatever the transcript stream in redis may still hold.

Offline: fakes + tmp dirs + fakeredis; no meeting-api, no runtime.
"""
from __future__ import annotations

import json

import fakeredis
import pytest
from fastapi.testclient import TestClient

from control_plane.api import ChatBody, ChatContextBody, _context_grounding, create_app
from control_plane.api_shared import transcript_erased
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

OWNER, STRANGER, ROW = "501", "502", "8001"
LINE = "the budget is final"
DELETED = {"state": "completed", "scope": "primary_transcript_recording_and_fixture_storage"}


def _row(deleted: bool):
    data = {"title": "Budget"}
    if deleted:
        data["artifact_deletion"] = DELETED
    return {"id": int(ROW), "status": "completed", "platform": "google_meet",
            "native_meeting_id": "abc-defg-hij", "user_id": OWNER, "data": data}


@pytest.fixture
def fake_redis(monkeypatch):
    import redis
    r = fakeredis.FakeRedis(decode_responses=True)
    r.xadd(f"tc:meeting:{ROW}", {"payload": json.dumps({"type": "transcription", "segments": [
        {"segment_id": "s1", "speaker": "Ana", "text": LINE}]})})
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: r)
    return r


def test_the_deletion_stamp_is_what_marks_a_transcript_erased():
    assert transcript_erased(_row(deleted=True))
    assert transcript_erased({"data": {"artifact_deletion": {"state": "pending"}}})
    assert not transcript_erased(_row(deleted=False))
    assert not transcript_erased(None) and not transcript_erased({"data": None})


def test_chat_does_not_fold_a_deleted_meetings_transcript(fake_redis):
    focus = {"kind": "meeting", "native_id": "abc-defg-hij", "meeting_id": ROW,
             "platform": "google_meet", "status": "completed"}
    body = ChatBody(prompt="what was decided?", context=ChatContextBody(focus=focus))

    def ground(row):
        return _context_grounding(body, "s1", "redis://fake", schedule_rows=lambda: [],
                                  workspace_mounts=lambda: [], meeting_access=lambda mid: row)[2]

    assert LINE in ground(_row(deleted=False))
    assert LINE not in ground(_row(deleted=True))


def test_the_live_view_refuses_a_deleted_meeting(tmp_path):
    def lookup(user_id, meeting_id, workspaces=None):
        return _row(deleted=True) if str(user_id) == OWNER and str(meeting_id) == ROW else None

    (tmp_path / OWNER).mkdir()
    (tmp_path / STRANGER).mkdir()
    client = TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(tmp_path)), object(), object()),
        reader=WorkspaceReader(str(tmp_path)), meeting_owner_lookup=lookup,
        redis_url="redis://127.0.0.1:6379/0"))
    url = f"/api/meeting/stream?meeting_id={ROW}&session_uid={ROW}"
    gone = client.get(url, headers={"X-User-Id": OWNER})
    assert gone.status_code == 410
    # …and a stranger is still refused on access, before deletion is even considered.
    assert client.get(url, headers={"X-User-Id": STRANGER}).status_code == 403
