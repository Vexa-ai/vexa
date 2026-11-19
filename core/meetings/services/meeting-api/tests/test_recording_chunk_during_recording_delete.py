"""A chunk in flight while its one recording is deleted is not folded back.

A per-recording delete (``DELETE /recordings/{id}``) marks the entry ``deletion_pending``, erases the
recording's objects, then removes the entry. A chunk that looked the recording up before that and
folds during or after it must not bring the entry back, and must not leave its own object behind.
"""
from __future__ import annotations

import struct

import pytest

from meeting_api.recordings import SessionNotFound, upload_chunk
from meeting_api.recordings.deletion import delete_owned_recording
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage

USER, MEETING_ID, SESSION_UID = 7, 1, "conn-abc"


def _wav() -> bytes:
    data = b"\x00" * 4
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    chunk = struct.pack("<4sI", b"data", len(data)) + data
    return struct.pack("<4sI4s", b"RIFF", 4 + len(fmt) + len(chunk), b"WAVE") + fmt + chunk


class _DeleteDuringUpload(InMemoryStorage):
    """Lets the recording's delete run (fully, or only its first step) while a chunk is stored."""

    def __init__(self, repo):
        super().__init__()
        self.repo, self.mode, self.recording_id = repo, None, None

    async def upload(self, key, data, *, content_type):
        await super().upload(key, data, content_type=content_type)
        mode, self.mode = self.mode, None
        if mode == "complete":
            await delete_owned_recording(self.repo, self, user_id=USER, recording_id=self.recording_id)
        elif mode == "pending":
            await self.repo.prepare_recording_deletion(USER, self.recording_id)


async def _chunk(repo, storage, seq, *, is_final=False):
    return await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                              data=_wav(), media_format="wav", chunk_seq=seq, is_final=is_final)


@pytest.mark.parametrize("mode", ["complete", "pending"])
@pytest.mark.parametrize("is_final", [False, True])
async def test_a_chunk_racing_its_recordings_delete_is_not_folded_and_leaves_no_object(mode, is_final):
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID, status="completed")
    storage = _DeleteDuringUpload(repo)
    first = await _chunk(repo, storage, 0)
    before_objects = set(storage.blobs)
    before = await repo.get_recordings(MEETING_ID)
    storage.recording_id, storage.mode = first["recording_id"], mode
    if is_final:
        with pytest.raises(SessionNotFound):
            await _chunk(repo, storage, 1, is_final=True)
    else:
        assert await _chunk(repo, storage, 1) == {"status": "pending"}
    recs = await repo.get_recordings(MEETING_ID)
    if mode == "complete":
        assert recs == [] and storage.blobs == {}
    else:
        assert set(storage.blobs) == before_objects          # the in-flight chunk's object is gone
        assert [r["media_files"] for r in recs] == [r["media_files"] for r in before]
        assert recs[0]["deletion_pending"] is True
