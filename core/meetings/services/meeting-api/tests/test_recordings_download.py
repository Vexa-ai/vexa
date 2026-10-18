"""recordings — saving a meeting's audio (`?download=1` on the raw master byte route).

The terminal's "Download audio" reads the same owner-scoped route the player streams, asking for
the bytes as a file. Drives the SHIPPED ``build_router`` over the in-memory fakes, offline.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.recordings import build_router
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage

OWNER = 7
OTHER = 8
MEETING_ID = 31
RECORDING_ID = 100
MEDIA_FILE_ID = 11
STORAGE_PATH = "recordings/7/100/conn-abc/audio/master.webm"
MASTER = b"\x1aE\xdf\xa3" + bytes(range(200))


def _client(fmt: str = "webm", path: str = STORAGE_PATH):
    repo = InMemoryRecordingRepo()
    storage = InMemoryStorage()
    storage.blobs[path] = MASTER
    repo.seed(meeting_id=MEETING_ID, user_id=OWNER, session_uid="conn-abc", status="completed")
    repo.seed(meeting_id=MEETING_ID + 1, user_id=OTHER, session_uid="conn-b", status="completed")
    repo._meetings[MEETING_ID]["recordings"] = [{
        "id": RECORDING_ID, "session_uid": "conn-abc", "source": "bot", "status": "completed",
        "created_at": "2026-10-09T14:03:11Z",
        "media_files": [{"id": MEDIA_FILE_ID, "type": "audio", "format": fmt, "is_final": True,
                         # Already made seekable: finalize serves the stored master as-is.
                         "seekable_version": 1, "storage_path": path}],
    }]
    app = FastAPI()
    app.include_router(build_router(repo, storage))
    return TestClient(app)


_URL = f"/recordings/{RECORDING_ID}/media/{MEDIA_FILE_ID}/raw?type=audio"


def test_download_marks_the_owner_bytes_as_an_attachment_with_the_real_extension():
    r = _client().get(_URL + "&download=1", headers={"x-user-id": str(OWNER)})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/webm"
    assert r.headers["content-disposition"] == (
        f'attachment; filename="meeting-{MEETING_ID}-2026-10-09-audio.webm"')
    assert r.content == MASTER


def test_playback_read_stays_inline():
    r = _client().get(_URL, headers={"x-user-id": str(OWNER)})
    assert r.status_code == 200
    assert "content-disposition" not in r.headers


def test_a_ranged_download_keeps_the_attachment_header():
    r = _client().get(_URL + "&download=1", headers={"x-user-id": str(OWNER), "Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.headers["content-disposition"].startswith("attachment;")


def test_wav_recording_downloads_as_wav():
    path = "recordings/7/100/conn-abc/audio/master.wav"
    r = _client("wav", path).get(_URL + "&download=1", headers={"x-user-id": str(OWNER)})
    assert r.headers["content-type"] == "audio/wav"
    assert r.headers["content-disposition"].endswith('-audio.wav"')


def test_another_user_cannot_download_the_owners_audio():
    r = _client().get(_URL + "&download=1", headers={"x-user-id": str(OTHER)})
    assert r.status_code == 404
    assert MASTER not in r.content
    assert "content-disposition" not in r.headers
