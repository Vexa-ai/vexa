"""#116 — completed meeting transcript + recording-object erasure."""
from __future__ import annotations

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
