"""A recordings write that lands after the meeting's recordings were deleted is refused.

``upload_chunk`` resolves its session (which already refuses an erased meeting), then stores the
chunk, then folds it into ``meeting.data['recordings']``. A typed delete of the meeting's transcript
and recordings can stamp the row between the first step and the last. ``mutate_recordings`` decides
the stamp again under the row lock the fold holds, so the fold is refused, and the upload removes
the object it just stored: no recording entry comes back, no object is left behind, and the caller
gets the answer it would have got had the delete landed first. Finalize and a per-recording delete
take the same refusal.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from meeting_api.recordings import (
    MeetingErased,
    SessionNotFound,
    finalize_master,
    upload_chunk,
)
from meeting_api.recordings.deletion import delete_owned_recording
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage
from meeting_api.recordings.jsonb import chunk_storage_key

USER = 7
MEETING_ID = 1
SESSION_UID = "conn-abc"
GOLDEN = Path(__file__).resolve().parents[5] / "core" / "gateway" / "contracts" / "api.v1" / "golden"


def _wav(n_data: int = 4) -> bytes:
    import struct

    data = b"\x00" * n_data
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    chunk = struct.pack("<4sI", b"data", len(data)) + data
    return struct.pack("<4sI4s", b"RIFF", 4 + len(fmt) + len(chunk), b"WAVE") + fmt + chunk


class _EraseDuringUpload(InMemoryStorage):
    """Storage whose next upload of a matching key lets the meeting delete land while the object is
    being stored — after the session lookup, before the fold takes the row lock."""

    def __init__(self, repo: InMemoryRecordingRepo, state: str):
        super().__init__()
        self._repo, self._state, self.armed = repo, state, False

    async def upload(self, key, data, *, content_type):
        await super().upload(key, data, content_type=content_type)
        if self.armed:
            self.armed = False
            self._repo.erase(MEETING_ID, state=self._state)


def _seeded(state: str = "completed"):
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    return repo, _EraseDuringUpload(repo, state)


async def _chunk(repo, storage, seq: int, *, is_final: bool = False):
    return await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
        data=_wav(), media_format="wav", chunk_seq=seq, is_final=is_final,
    )


# ── the race: the delete lands between the chunk's upload and its fold ───────────────────────────

@pytest.mark.parametrize("state", ["pending", "completed"])
async def test_a_chunk_in_flight_when_the_meeting_is_erased_is_not_folded_and_its_object_is_removed(state):
    repo, storage = _seeded(state)
    first = await _chunk(repo, storage, 0)
    assert first["chunk_seq"] == 0

    storage.armed = True
    receipt = await _chunk(repo, storage, 1)

    # The answer a chunk gets when the delete landed first (find_session sees the stamp).
    assert receipt == {"status": "pending"}
    assert await _chunk(repo, storage, 2) == {"status": "pending"}

    recs = await repo.get_recordings(MEETING_ID)
    if state == "completed":
        assert recs == [], "the erased meeting must not regain a recording entry"
    else:
        # The delete is still running; the entry it is about to remove is untouched by the late fold.
        (mf,) = recs[0]["media_files"]
        assert mf["chunk_count"] == 1
    late_key = chunk_storage_key(
        user_id=USER, recording_id=first["recording_id"], session_uid=SESSION_UID,
        media_type="audio", media_format="wav", chunk_seq=1,
    )
    assert storage.deleted == [late_key]
    assert late_key not in storage.blobs, "the late chunk's object must not outlive the delete"


async def test_a_final_chunk_in_flight_when_the_meeting_is_erased_is_a_404_and_leaves_nothing():
    repo, storage = _seeded("completed")
    storage.armed = True
    with pytest.raises(SessionNotFound):
        await _chunk(repo, storage, 0, is_final=True)
    assert await repo.get_recordings(MEETING_ID) == []
    assert storage.blobs == {}
    assert len(storage.deleted) == 1
    # A retry of the same final chunk gets the same answer from the session lookup.
    with pytest.raises(SessionNotFound):
        await _chunk(repo, storage, 0, is_final=True)


async def test_a_master_built_while_the_meeting_is_erased_is_not_stamped_and_is_removed():
    repo, storage = _seeded("pending")
    receipt = await _chunk(repo, storage, 0, is_final=True)

    storage.armed = True  # the next upload is the master
    assert await finalize_master(
        repo, storage, meeting_id=MEETING_ID, recording_id=receipt["recording_id"],
    ) is None
    master = storage.deleted[-1]
    assert master.rsplit("/", 1)[-1].startswith("master.")
    assert master not in storage.blobs
    (mf,) = (await repo.get_recordings(MEETING_ID))[0]["media_files"]
    assert mf.get("finalized_by") is None


async def test_a_recording_delete_racing_the_meeting_erase_still_completes():
    repo, storage = _seeded()
    repo._meetings[MEETING_ID]["status"] = "completed"
    receipt = await _chunk(repo, storage, 0, is_final=True)
    repo.erase(MEETING_ID, state="pending")

    out = await delete_owned_recording(
        repo, storage, user_id=USER, recording_id=receipt["recording_id"],
    )
    assert out["status"] == "deleted"
    assert storage.blobs == {}


# ── the port: mutate_recordings refuses an erased meeting before the mutator runs ─────────────────

@pytest.mark.parametrize("state", ["pending", "completed"])
async def test_mutate_recordings_refuses_an_erased_meeting(state):
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    repo._meetings[MEETING_ID]["recordings"] = [{"id": 5, "media_files": []}]
    repo.erase(MEETING_ID, state=state)
    before = await repo.get_recordings(MEETING_ID)
    calls = []

    def mutator(recs):
        calls.append(recs)
        return recs + [{"id": 6}], "folded"

    with pytest.raises(MeetingErased):
        await repo.mutate_recordings(MEETING_ID, mutator)
    assert calls == []
    assert await repo.get_recordings(MEETING_ID) == before
    assert await repo.find_session(SESSION_UID) is None


def _row(data):
    return SimpleNamespace(id=MEETING_ID, data=data)


def test_the_production_rule_refuses_every_erased_or_missing_meeting():
    from meeting_api.recordings.adapters import writable_meeting_data

    live = {"recordings": [{"id": 5}]}
    copy = writable_meeting_data(_row(live))
    assert copy == live and copy is not live
    assert writable_meeting_data(_row(None)) == {}
    with pytest.raises(MeetingErased):
        writable_meeting_data(None)
    for name in ("MeetingResponse.deleted.json", "MeetingResponse.deleting.json"):
        with pytest.raises(MeetingErased):
            writable_meeting_data(_row(json.loads((GOLDEN / name).read_text())["data"]))


class _Rows:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def first(self):
        return self._row


class _Db:
    def __init__(self, row):
        self._row, self.commits = row, 0

    async def execute(self, _stmt):
        return _Rows(self._row)

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def test_the_production_repo_refuses_under_its_row_lock():
    """Through SqlAlchemyRecordingRepo.mutate_recordings over a scripted session (needs SQLAlchemy,
    which the service image carries and this lane may not)."""
    pytest.importorskip("sqlalchemy")
    from meeting_api.recordings.adapters import SqlAlchemyRecordingRepo

    db = _Db(_row(json.loads((GOLDEN / "MeetingResponse.deleted.json").read_text())["data"]))
    calls = []
    with pytest.raises(MeetingErased):
        await SqlAlchemyRecordingRepo(lambda: db).mutate_recordings(
            MEETING_ID, lambda recs: (calls.append(recs), (recs, None))[1])
    assert calls == [] and db.commits == 0
