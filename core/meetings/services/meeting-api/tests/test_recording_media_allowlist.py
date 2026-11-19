"""A chunk upload's ``media_type`` and ``media_format`` are allow-listed before they reach an object key.

Both are path segments of a chunk's key (``recordings/<user>/<rec>/<session>/<media_type>/<seq>.<fmt>``)
and both come from the upload's form. Only the media a bot records reach a key: audio or video, as
WAV, WebM, Matroska or MP4. Anything else is refused (422) before the body is read and nothing is
stored; ``chunk_storage_key`` refuses it too. The recordings list has one writer, ``mutate_recordings``:
the port's unused ``put_recordings`` is gone from it and from both adapters.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import mint_meeting_token
from meeting_api.recordings import adapters, build_router
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage
from meeting_api.recordings.jsonb import RECORDING_MEDIA_FORMATS, RECORDING_MEDIA_TYPES, chunk_storage_key
from meeting_api.recordings.ports import RecordingRepo

SECRET = "recording-media-allowlist-key-0123456789abcdef"
USER = 7
SESSION = "conn-media"


def _client():
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=1, user_id=USER, session_uid=SESSION)
    storage = InMemoryStorage()
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    return TestClient(app), storage


def _upload(client, *, media_type=None, media_format=None, flat=False):
    token = mint_meeting_token(1, USER, "google_meet", "abc", secret=SECRET, session_uid=SESSION)
    meta = {"session_uid": SESSION, "chunk_seq": 0, "is_final": False}
    data = {}
    if flat:
        data.update({k: v for k, v in (("media_type", media_type), ("media_format", media_format)) if v})
    else:
        meta.update({k: v for k, v in (("media_type", media_type), ("format", media_format)) if v})
    data["metadata"] = json.dumps(meta)
    return client.post("/internal/recordings/upload", headers={"Authorization": f"Bearer {token}"},
                       data=data, files={"file": ("chunk.bin", b"RIFF....WAVE", "application/octet-stream")})


def test_the_allow_lists_are_the_media_a_bot_records():
    assert RECORDING_MEDIA_TYPES == ("audio", "video")
    assert RECORDING_MEDIA_FORMATS == ("wav", "webm", "mkv", "mp4")


@pytest.mark.parametrize("media_type,media_format", [("audio", "wav"), ("audio", "webm"), ("video", "webm"),
                                                     ("video", "mkv"), ("video", "mp4")])
def test_the_media_a_bot_records_is_stored(media_type, media_format):
    client, storage = _client()
    r = _upload(client, media_type=media_type, media_format=media_format)
    assert r.status_code == 200, r.text
    (key,) = storage.blobs
    assert key.endswith(f"/{SESSION}/{media_type}/000000.{media_format}")


@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("media_type", ["../../signal", "audio/../../x", "image", "AUDIO", "audio ", "a\\b"])
def test_an_unknown_media_type_is_refused_and_nothing_is_stored(media_type, flat):
    client, storage = _client()
    r = _upload(client, media_type=media_type, media_format="wav", flat=flat)
    assert r.status_code == 422, r.text
    assert storage.blobs == {}


@pytest.mark.parametrize("flat", [False, True])
@pytest.mark.parametrize("media_format", ["wav/../../../x", "../x", "exe", "WAV", "wav.", "json", "jsonl"])
def test_an_unknown_media_format_is_refused_and_nothing_is_stored(media_format, flat):
    client, storage = _client()
    r = _upload(client, media_type="audio", media_format=media_format, flat=flat)
    assert r.status_code == 422, r.text
    assert storage.blobs == {}


@pytest.mark.parametrize("field,value", [("media_type", "../x"), ("media_type", "signal"),
                                         ("media_format", "wav/../x"), ("media_format", "txt")])
def test_the_key_builder_refuses_what_the_route_refuses(field, value):
    args = {"user_id": USER, "recording_id": 1, "session_uid": SESSION, "media_type": "audio",
            "media_format": "wav", "chunk_seq": 0, field: value}
    with pytest.raises(ValueError):
        chunk_storage_key(**args)


def test_the_recordings_list_has_one_writer():
    assert not hasattr(RecordingRepo, "put_recordings")
    assert not hasattr(InMemoryRecordingRepo, "put_recordings")
    assert not any(hasattr(cls, "put_recordings") for cls in vars(adapters).values() if isinstance(cls, type))
    assert hasattr(RecordingRepo, "mutate_recordings") and hasattr(InMemoryRecordingRepo, "mutate_recordings")
