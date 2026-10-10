"""#116 — completed meeting transcript + recording-object erasure."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.recordings.fakes import InMemoryStorage


OWNER = 7
OTHER = 8
MEETING_ID = 41
RECORDING_ID = 9001
PREFIX = f"recordings/{OWNER}/{RECORDING_ID}/sess-41/audio/"


def _recording() -> dict:
    return {
        "id": RECORDING_ID,
        "meeting_id": MEETING_ID,
        "user_id": OWNER,
        "session_uid": "sess-41",
        "status": "completed",
        "media_files": [{
            "id": 22,
            "type": "audio",
            "format": "wav",
            "storage_path": f"{PREFIX}master.wav",
        }],
    }


def _fixture(*, status: str = "completed", storage_cls=InMemoryStorage):
    store = InMemoryTranscriptStore()
    store.seed_meeting(
        meeting_id=MEETING_ID,
        user_id=OWNER,
        platform="google_meet",
        native_meeting_id="private-room",
        status=status,
        data={
            "recordings": [_recording()],
            "processed": {"views": [{"doc": {"notes": ["derived"]}}]},
            "notes": "derived summary",
            "share_grants": [{"id": "share"}],
            "transcript_viewers": [OTHER],
        },
        segments=[{
            "segment_id": "s1", "start": 0, "end": 1,
            "text": "confidential", "language": "en",
        }],
    )
    storage = storage_cls()
    storage.blobs[f"{PREFIX}000000.wav"] = b"chunk"
    storage.blobs[f"{PREFIX}master.wav"] = b"master"
    return store, storage, TestClient(
        create_app(transcript_store=store, storage=storage),
        raise_server_exceptions=False,
    )


def test_owner_deletes_completed_artifacts_but_terminal_meeting_row_survives():
    store, storage, client = _fixture()

    response = client.delete(
        f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OWNER)}
    )
    assert response.status_code == 204
    assert storage.blobs == {}
    assert client.get(
        "/transcripts/google_meet/private-room", headers={"x-user-id": str(OWNER)}
    ).status_code == 404

    meeting = store._meetings[MEETING_ID]
    assert meeting["status"] == "completed", "terminal lifecycle evidence is retained"
    assert meeting["segments"] == {}
    assert "recordings" not in meeting["data"]
    assert "processed" not in meeting["data"]
    assert "notes" not in meeting["data"]
    assert meeting["data"]["artifact_deletion"]["backup_residuals"] == (
        "expire_under_deployment_retention_policy"
    )


def test_non_owner_gets_indistinguishable_404_and_cannot_delete_any_artifact():
    store, storage, client = _fixture()

    response = client.delete(
        f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OTHER)}
    )
    assert response.status_code == 404
    assert sorted(storage.blobs) == [f"{PREFIX}000000.wav", f"{PREFIX}master.wav"]
    assert store._meetings[MEETING_ID]["segments"]["s1"]["text"] == "confidential"


def test_storage_failure_preserves_paths_and_transcript_for_retry():
    class FailsOnceStorage(InMemoryStorage):
        def __init__(self):
            super().__init__()
            self.fail = True

        async def delete(self, key: str) -> None:
            if self.fail:
                self.fail = False
                raise RuntimeError("injected object-store failure")
            await super().delete(key)

    store, storage, client = _fixture(storage_cls=FailsOnceStorage)

    first = client.delete(f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OWNER)})
    assert first.status_code == 500
    assert store._meetings[MEETING_ID]["data"]["recordings"][0]["id"] == RECORDING_ID
    assert store._meetings[MEETING_ID]["data"]["artifact_deletion"]["state"] == "pending"
    assert client.get(
        "/transcripts/google_meet/private-room", headers={"x-user-id": str(OWNER)}
    ).status_code == 200

    retry = client.delete(f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OWNER)})
    assert retry.status_code == 204
    assert storage.blobs == {}
    assert "recordings" not in store._meetings[MEETING_ID]["data"]
    assert store._meetings[MEETING_ID]["data"]["artifact_deletion"]["state"] == "completed"


def test_completed_artifact_delete_is_idempotent_and_active_lifecycle_is_not_deleted():
    store, storage, client = _fixture()
    headers = {"x-user-id": str(OWNER)}
    assert client.delete(f"/meetings/{MEETING_ID}", headers=headers).status_code == 204
    assert client.delete(f"/meetings/{MEETING_ID}", headers=headers).status_code == 204

    active_store, active_storage, active_client = _fixture(status="active")
    response = active_client.delete(f"/meetings/{MEETING_ID}", headers=headers)
    assert response.status_code == 409
    assert active_store._meetings[MEETING_ID]["status"] == "active"
    assert active_storage.blobs


def test_deletes_every_fixture_session_and_promotion_without_touching_other_meetings():
    store, storage, client = _fixture()
    prefix = f"signal/{OWNER}/{MEETING_ID}/"
    for name in ("session-a/captured-signal.jsonl", "session-a/PROMOTED", "session-b/botlog.txt"):
        storage.blobs[prefix + name] = b"private fixture"
    other = f"signal/{OWNER}/{MEETING_ID + 1}/session-a/captured-signal.jsonl"
    storage.blobs[other] = b"other meeting"
    assert client.delete(f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OWNER)}).status_code == 204
    assert storage.blobs == {other: b"other meeting"}


def test_fixtures_without_recordings_are_deleted_and_storage_failure_can_retry():
    class FailsFixtureOnce(InMemoryStorage):
        fail = True
        async def delete(self, key):
            if key.startswith("signal/") and self.fail:
                self.fail = False
                raise RuntimeError("fixture storage unavailable")
            await super().delete(key)
    store, storage, client = _fixture(storage_cls=FailsFixtureOnce)
    store._meetings[MEETING_ID]["data"]["recordings"] = []
    storage.blobs.clear()
    key = f"signal/{OWNER}/{MEETING_ID}/session-a/transcript.jsonl"
    storage.blobs[key] = b"fixture"
    headers = {"x-user-id": str(OWNER)}
    assert client.delete(f"/meetings/{MEETING_ID}", headers=headers).status_code == 500
    assert store._meetings[MEETING_ID]["data"]["artifact_deletion"]["state"] == "pending"
    assert client.delete(f"/meetings/{MEETING_ID}", headers=headers).status_code == 204
    assert storage.blobs == {}


# ── the transcript held in Redis goes with the rest ──────────────────────────────────────────────

def _cache_keys(meeting_id: int) -> list[str]:
    return [f"meeting:{meeting_id}:segments", f"proc:meeting:{meeting_id}", f"tc:meeting:{meeting_id}"]


def _seed_cache(r, meeting_id: int) -> None:
    r.hset(f"meeting:{meeting_id}:segments", "s1", json.dumps({"text": "confidential"}))
    r.xadd(f"proc:meeting:{meeting_id}", {"payload": json.dumps({"notes": "confidential"})})
    r.xadd(f"tc:meeting:{meeting_id}", {"payload": json.dumps(
        {"type": "transcription", "segments": [{"segment_id": "s1", "text": "confidential"}]})})


def test_delete_erases_every_transcript_key_in_redis_and_no_other_meetings():
    import fakeredis

    server = fakeredis.FakeServer()
    sync = fakeredis.FakeRedis(server=server, decode_responses=True)
    store = InMemoryTranscriptStore(redis_client=fakeredis.aioredis.FakeRedis(server=server))
    store.seed_meeting(meeting_id=MEETING_ID, user_id=OWNER, platform="google_meet",
                       native_meeting_id="private-room", status="completed", data={},
                       segments=[{"segment_id": "s1", "start": 0, "end": 1, "text": "confidential"}])
    _seed_cache(sync, MEETING_ID)
    _seed_cache(sync, MEETING_ID + 1)
    client = TestClient(create_app(transcript_store=store, storage=InMemoryStorage()),
                        raise_server_exceptions=False)

    assert client.delete(f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OWNER)}).status_code == 204
    assert [k for k in _cache_keys(MEETING_ID) if sync.exists(k)] == []
    assert all(sync.exists(k) for k in _cache_keys(MEETING_ID + 1)), "another meeting's keys were touched"


def test_the_sql_store_erases_the_same_keys():
    """The production store's finalize needs a database to run, which this suite does not start, so
    this pins that it deletes the same tuple the fake does (the fake is exercised end to end above)."""
    import inspect

    from meeting_api.collector.adapters import SqlAlchemyTranscriptStore

    src = inspect.getsource(SqlAlchemyTranscriptStore.finalize_completed_artifact_deletion)
    assert "self._redis.delete(*erased_meeting_cache_keys(meeting_id))" in src


def test_the_erased_keys_are_the_ones_the_transcript_writers_use():
    from meeting_api.collector.db_writer import segments_hash_key
    from meeting_api.collector.ingest import _transcript_stream
    from meeting_api.collector.ports import erased_meeting_cache_keys

    keys = set(erased_meeting_cache_keys(MEETING_ID))
    assert segments_hash_key(MEETING_ID) in keys
    assert _transcript_stream(MEETING_ID) in keys
    assert f"proc:meeting:{MEETING_ID}" in keys
