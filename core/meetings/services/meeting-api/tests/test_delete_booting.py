"""DELETE /meetings on a meeting whose bot is still booting.

The meeting delete stops the bot through the same `stop_meeting` the stop route uses. With a runtime
wired, that stop also tears down a booting bot's workload — and the delete has no request
BackgroundTasks of its own. That path must answer (2xx), never 500, and the workload is still torn down.
It is the `VEXA_MEETING_DELETE_LIVE=stop` path; the default (`refuse`) answers 409 as 0.12 did.
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher
from meeting_api.recordings.fakes import InMemoryStorage

OWNER = 7
NATIVE = "booting-room"


def test_delete_meetings_on_a_booting_bot_answers_and_tears_the_workload_down(monkeypatch):
    monkeypatch.setenv("VEXA_MEETING_DELETE_LIVE", "stop")
    repo, pub, runtime = InMemoryMeetingRepo(), InMemoryCommandPublisher(), FakeRuntimeClient()
    m = asyncio.run(repo.create_meeting(user_id=OWNER, platform="google_meet", native_meeting_id=NATIVE, data={}))
    sid = f"sess-{m['id']}"
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=sid))
    asyncio.run(repo.update_meeting_status(session_uid=sid, status="awaiting_admission"))
    asyncio.run(repo.set_bot_container(meeting_id=m["id"], bot_container_id="wl-booting"))
    store = InMemoryTranscriptStore()
    store.seed_meeting(meeting_id=m["id"], user_id=OWNER, platform="google_meet", native_meeting_id=NATIVE,
                       status="awaiting_admission", data={}, segments=[])
    app = create_app(open_callbacks=True, meeting_repo=repo, command_publisher=pub, runtime=runtime,
                     transcript_store=store, storage=InMemoryStorage())
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.delete(f"/meetings/google_meet/{NATIVE}", headers={"x-user-id": str(OWNER)})
    assert 200 <= r.status_code < 300, r.text
    assert repo._meetings[m["id"]]["data"].get("stop_requested") is True, "the bot was asked to leave"
    assert runtime.deleted == ["wl-booting"], "the booting workload is still torn down"
