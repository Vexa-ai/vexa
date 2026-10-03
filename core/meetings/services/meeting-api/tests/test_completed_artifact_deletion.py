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


# ── captured-signal tapes (#116) ────────────────────────────────────────────────────────────────
# The tape is NOT in ``meeting.data['recordings']`` by design, so every test above passes with the
# raw per-channel PCM of the "erased" meeting still sitting in the bucket. These are the ones that
# would have caught that.

SESSION = "sess-41"
TAPE = f"signal/{OWNER}/{MEETING_ID}/{SESSION}/"
OTHER_SESSION_TAPE = f"signal/{OWNER}/{MEETING_ID}/sess-41-retry/"
OTHER_MEETING_TAPE = f"signal/{OWNER}/{MEETING_ID + 1}/sess-99/"
OTHER_OWNER_TAPE = f"signal/{OTHER}/{MEETING_ID}/sess-41/"


def _seed_tapes(storage) -> None:
    storage.blobs[f"{TAPE}captured-signal.jsonl"] = b'{"type":"captured_signal_header"}'
    storage.blobs[f"{TAPE}botlog.txt"] = b"bot said things"
    storage.blobs[f"{OTHER_SESSION_TAPE}captured-signal.jsonl"] = b"retry session"
    storage.blobs[f"{OTHER_MEETING_TAPE}captured-signal.jsonl"] = b"a different meeting"
    storage.blobs[f"{OTHER_OWNER_TAPE}captured-signal.jsonl"] = b"a different owner"


def _survivors(storage) -> list[str]:
    return sorted(k for k in storage.blobs if k.startswith("signal/"))


def test_deleting_a_completed_meeting_erases_every_captured_signal_tape_it_left():
    store, storage, client = _fixture()
    _seed_tapes(storage)

    assert client.delete(
        f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OWNER)}
    ).status_code == 204

    # Both of THIS meeting's sessions go — a retry leaves a second tape and an owner asking for the
    # meeting to be erased means all of them.
    assert _survivors(storage) == [
        f"{OTHER_MEETING_TAPE}captured-signal.jsonl",
        f"{OTHER_OWNER_TAPE}captured-signal.jsonl",
    ], "erasure is bounded to this owner's this meeting — and reaches every session inside it"


def test_a_promoted_tape_is_erased_by_an_owner_request_though_the_janitor_spares_it():
    """Promotion outranks the storage budget. It does not outrank the person on the tape."""
    from meeting_api.recordings.signal_janitor import sweep_signal_tapes

    store, storage, client = _fixture()
    _seed_tapes(storage)
    storage.blobs[f"{TAPE}PROMOTED"] = b""

    # Control: the budget janitor, at a budget of one byte, still refuses to touch it.
    import asyncio
    asyncio.run(sweep_signal_tapes(storage, budget_bytes=1, min_age_s=0.0, now=1e12))
    assert f"{TAPE}captured-signal.jsonl" in storage.blobs, "janitor spares a promoted tape"

    assert client.delete(
        f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OWNER)}
    ).status_code == 204
    assert not [k for k in storage.blobs if k.startswith(TAPE)], (
        "the erasure request takes the promoted tape and its marker with it"
    )


def test_native_key_delete_reports_the_signal_objects_it_erased():
    store, storage, client = _fixture()
    _seed_tapes(storage)

    response = client.delete(
        "/meetings/google_meet/private-room", headers={"x-user-id": str(OWNER)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] == "completed_meeting_artifacts"
    # 2 parts of sess-41 + 1 of the retry session. The recordings count is reported separately:
    # what a user could play back and what we kept for ourselves are different questions.
    assert body["signal_objects_deleted"] == 3
    assert body["objects_deleted"] == 2


def test_non_owner_cannot_erase_a_tape():
    store, storage, client = _fixture()
    _seed_tapes(storage)

    assert client.delete(
        f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OTHER)}
    ).status_code == 404
    assert f"{TAPE}captured-signal.jsonl" in storage.blobs


def test_a_tape_delete_failure_leaves_the_meeting_retryable_and_the_retry_converges():
    class FailsOnTapeStorage(InMemoryStorage):
        def __init__(self):
            super().__init__()
            self.fail = True

        async def delete(self, key: str) -> None:
            if self.fail and key.startswith("signal/"):
                self.fail = False
                raise RuntimeError("injected object-store failure on the tape")
            await super().delete(key)

    store, storage, client = _fixture(storage_cls=FailsOnTapeStorage)
    _seed_tapes(storage)
    headers = {"x-user-id": str(OWNER)}

    first = client.delete(f"/meetings/{MEETING_ID}", headers=headers)
    assert first.status_code == 500
    # The DB is untouched, so the transcript is still addressable and the same request can retry.
    assert store._meetings[MEETING_ID]["data"]["artifact_deletion"]["state"] == "pending"
    assert client.get(
        "/transcripts/google_meet/private-room", headers=headers
    ).status_code == 200

    # The retry re-lists the prefix rather than replaying a remembered key set, which is what makes
    # it converge after a partial first pass.
    assert client.delete(f"/meetings/{MEETING_ID}", headers=headers).status_code == 204
    assert not [k for k in storage.blobs if k.startswith(TAPE)]
    assert store._meetings[MEETING_ID]["data"]["artifact_deletion"]["state"] == "completed"
    assert store._meetings[MEETING_ID]["data"]["artifact_deletion"]["scope"] == (
        "primary_transcript_recording_and_signal_storage"
    )


def test_a_tape_left_by_a_meeting_that_was_never_recorded_is_still_erased():
    """The case the recordings sweep can never reach: a tape with no recording to hang it off."""
    store, storage, client = _fixture()
    storage.blobs.clear()
    store._meetings[MEETING_ID]["data"].pop("recordings", None)
    storage.blobs[f"{TAPE}captured-signal.jsonl"] = b"frames"

    assert client.delete(
        f"/meetings/{MEETING_ID}", headers={"x-user-id": str(OWNER)}
    ).status_code == 204
    assert storage.blobs == {}
