"""recordings — chunk upload + finalize → master in meeting.data JSONB (recording.v1).

Drives the SHIPPED ``upload_chunk`` / ``finalize_master`` / ``build_router`` over the in-memory
fakes, OFFLINE (no MinIO, no DB): chunks fold into the recording's JSONB payload, the master is
built by the golden-locked codec and the media-file stamped finalized, and the upload-token auth +
session-resolution seams behave.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import mint_meeting_token
from meeting_api.recordings import (
    InvalidRecordingMetadata,
    build_router,
    finalize_master,
    upload_chunk,
)
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage
from meeting_api.recordings.jsonb import chunk_storage_key
from meeting_api.recordings.ports import RecordingWriteRefused

SECRET = "test-admin-token"
USER = 7
MEETING_ID = 1
SESSION_UID = "conn-abc"

# A minimal valid wav file (44-byte RIFF header + 4 bytes of PCM) so the wav master codec runs.
def _wav(n_data: int = 4) -> bytes:
    import struct

    data = b"\x00" * n_data
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    chunk = struct.pack("<4sI", b"data", len(data)) + data
    riff_len = 4 + len(fmt) + len(chunk)
    return struct.pack("<4sI4s", b"RIFF", riff_len, b"WAVE") + fmt + chunk


def _seeded():
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    return repo, InMemoryStorage()


async def test_chunk_upload_cannot_bypass_the_meeting_write_gate():
    class WriteRefused(RuntimeError):
        pass

    class RefusingRepo(InMemoryRecordingRepo):
        def __init__(self):
            super().__init__()
            self.gate_calls = 0

        @asynccontextmanager
        async def recording_write(self, meeting_id):
            self.gate_calls += 1
            raise WriteRefused("meeting erasure has started")
            yield  # pragma: no cover

    repo = RefusingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    storage = InMemoryStorage()

    with pytest.raises(WriteRefused, match="erasure has started"):
        await upload_chunk(
            repo,
            storage,
            token_meeting_id=MEETING_ID,
            session_uid=SESSION_UID,
            data=_wav(),
            media_format="wav",
        )

    assert repo.gate_calls == 1
    assert storage.blobs == {}


async def test_master_finalization_cannot_bypass_the_meeting_write_gate():
    class WriteRefused(RuntimeError):
        pass

    class RefusingRepo(InMemoryRecordingRepo):
        @asynccontextmanager
        async def recording_write(self, meeting_id):
            raise WriteRefused("meeting erasure has started")
            yield  # pragma: no cover

    repo = RefusingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)

    with pytest.raises(WriteRefused, match="erasure has started"):
        await finalize_master(
            repo,
            InMemoryStorage(),
            meeting_id=MEETING_ID,
            recording_id=123,
        )


# ── flow: upload folds chunks into JSONB; finalize builds the master ─────────────────────────────

async def test_upload_chunk_writes_recording_jsonb():
    repo, storage = _seeded()
    receipt = await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
        data=_wav(), media_type="audio", media_format="wav", chunk_seq=0, is_final=False,
    )
    assert receipt["status"] == "in_progress"
    recs = await repo.get_recordings(MEETING_ID)
    assert len(recs) == 1
    mf = recs[0]["media_files"][0]
    assert mf["type"] == "audio"
    assert mf["chunk_count"] == 1
    # The chunk landed in storage under the parent key scheme.
    assert mf["storage_path"] in storage.blobs


async def test_final_chunk_completes_recording():
    repo, storage = _seeded()
    await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                       data=_wav(), media_format="wav", chunk_seq=0, is_final=False)
    receipt = await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                                 data=_wav(), media_format="wav", chunk_seq=1, is_final=True)
    assert receipt["status"] == "completed"


async def test_retried_chunk_sequence_does_not_inflate_recording_metadata():
    repo, storage = _seeded()

    first = await upload_chunk(
        repo,
        storage,
        token_meeting_id=MEETING_ID,
        session_uid=SESSION_UID,
        data=_wav(),
        media_format="wav",
        chunk_seq=0,
        is_final=False,
    )
    retry = await upload_chunk(
        repo,
        storage,
        token_meeting_id=MEETING_ID,
        session_uid=SESSION_UID,
        data=_wav(),
        media_format="wav",
        chunk_seq=0,
        is_final=False,
    )

    media = (await repo.get_recordings(MEETING_ID))[0]["media_files"][0]
    assert retry["storage_path"] == first["storage_path"]
    assert len(storage.blobs) == 1
    assert media["chunk_count"] == 1
    assert media["file_size_bytes"] == len(_wav())


async def test_retried_final_chunk_preserves_completion_and_media_creation_times():
    import asyncio

    repo, storage = _seeded()
    await upload_chunk(
        repo,
        storage,
        token_meeting_id=MEETING_ID,
        session_uid=SESSION_UID,
        data=_wav(),
        media_format="wav",
        chunk_seq=0,
        is_final=True,
    )
    original = (await repo.get_recordings(MEETING_ID))[0]
    original_completed_at = original["completed_at"]
    original_media_created_at = original["media_files"][0]["created_at"]

    await asyncio.sleep(0.002)
    await upload_chunk(
        repo,
        storage,
        token_meeting_id=MEETING_ID,
        session_uid=SESSION_UID,
        data=_wav(),
        media_format="wav",
        chunk_seq=0,
        is_final=True,
    )

    retried = (await repo.get_recordings(MEETING_ID))[0]
    assert retried["completed_at"] == original_completed_at
    assert retried["media_files"][0]["created_at"] == original_media_created_at


async def test_failed_retry_fold_preserves_the_existing_chunk_object():
    class FailingRetryRepo(InMemoryRecordingRepo):
        fail_fold = False

        async def mutate_recordings(self, meeting_id, mutator):
            if self.fail_fold:
                raise RuntimeError("database retry fold failed")
            return await super().mutate_recordings(meeting_id, mutator)

    repo = FailingRetryRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    storage = InMemoryStorage()
    first = await upload_chunk(
        repo,
        storage,
        token_meeting_id=MEETING_ID,
        session_uid=SESSION_UID,
        data=_wav(),
        media_format="wav",
        chunk_seq=0,
        is_final=False,
    )
    original = storage.blobs[first["storage_path"]]
    repo.fail_fold = True

    with pytest.raises(RuntimeError, match="database retry fold failed"):
        await upload_chunk(
            repo,
            storage,
            token_meeting_id=MEETING_ID,
            session_uid=SESSION_UID,
            data=_wav(),
            media_format="wav",
            chunk_seq=0,
            is_final=False,
        )

    assert storage.blobs[first["storage_path"]] == original


async def test_failed_same_key_race_cannot_delete_a_concurrently_committed_chunk():
    import asyncio
    import contextvars

    class SameKeyRepo(InMemoryRecordingRepo):
        def __init__(self):
            super().__init__()
            self._chunk_lock = asyncio.Lock()
            self.in_chunk_lock = contextvars.ContextVar("in_chunk_lock", default=False)
            self.success_committed = asyncio.Event()

        @asynccontextmanager
        async def chunk_write(self, key):
            async with self._chunk_lock:
                token = self.in_chunk_lock.set(True)
                try:
                    yield
                finally:
                    self.in_chunk_lock.reset(token)

        async def mutate_recordings(self, meeting_id, mutator):
            if asyncio.current_task().get_name() == "failing-upload":
                await self.success_committed.wait()
                raise RuntimeError("forced fold failure")
            result = await super().mutate_recordings(meeting_id, mutator)
            self.success_committed.set()
            return result

    class SameKeyStorage(InMemoryStorage):
        def __init__(self, repo):
            super().__init__()
            self.repo = repo
            self.unlocked_checks = 0
            self.both_unlocked_checks = asyncio.Event()

        async def exists(self, key):
            if self.repo.in_chunk_lock.get():
                return await super().exists(key)
            self.unlocked_checks += 1
            if self.unlocked_checks == 2:
                self.both_unlocked_checks.set()
            await self.both_unlocked_checks.wait()
            return False

        async def upload(self, key, data, *, content_type):
            await asyncio.sleep(0)
            await super().upload(key, data, content_type=content_type)

    repo = SameKeyRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    storage = SameKeyStorage(repo)

    successful = asyncio.create_task(
        upload_chunk(
            repo,
            storage,
            token_meeting_id=MEETING_ID,
            session_uid=SESSION_UID,
            data=_wav(8),
            media_format="wav",
            chunk_seq=0,
            is_final=False,
        ),
        name="successful-upload",
    )
    failing = asyncio.create_task(
        upload_chunk(
            repo,
            storage,
            token_meeting_id=MEETING_ID,
            session_uid=SESSION_UID,
            data=_wav(4),
            media_format="wav",
            chunk_seq=0,
            is_final=False,
        ),
        name="failing-upload",
    )
    success_result, failure_result = await asyncio.gather(
        successful,
        failing,
        return_exceptions=True,
    )

    assert isinstance(success_result, dict)
    assert isinstance(failure_result, RuntimeError)
    media = (await repo.get_recordings(MEETING_ID))[0]["media_files"][0]
    assert media["storage_path"] in storage.blobs


async def test_failed_first_chunk_database_fold_removes_the_orphan_object():
    class FailingRepo(InMemoryRecordingRepo):
        async def mutate_recordings(self, meeting_id, mutator):
            raise RuntimeError("database write failed")

    repo = FailingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    storage = InMemoryStorage()

    with pytest.raises(RuntimeError, match="database write failed"):
        await upload_chunk(
            repo,
            storage,
            token_meeting_id=MEETING_ID,
            session_uid=SESSION_UID,
            data=_wav(),
            media_format="wav",
            chunk_seq=0,
            is_final=False,
        )

    assert storage.blobs == {}


async def test_failed_compensation_still_leaves_a_durable_prefix_for_erasure():
    class FailingRepo(InMemoryRecordingRepo):
        def __init__(self):
            super().__init__()
            self.registered_prefixes: list[str] = []

        async def register_recording_prefix(self, meeting_id, prefix):
            self.registered_prefixes.append(prefix)

        async def mutate_recordings(self, meeting_id, mutator):
            raise RuntimeError("database fold failed")

    class CleanupFailStorage(InMemoryStorage):
        async def delete(self, key):
            raise RuntimeError("object cleanup failed")

    repo = FailingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)

    with pytest.raises(RuntimeError, match="compensation requires retry"):
        await upload_chunk(
            repo,
            CleanupFailStorage(),
            token_meeting_id=MEETING_ID,
            session_uid=SESSION_UID,
            data=_wav(),
            media_format="wav",
            chunk_seq=0,
            is_final=False,
        )

    assert len(repo.registered_prefixes) == 1
    prefix = repo.registered_prefixes[0]
    assert prefix.startswith(f"recordings/{USER}/")
    assert prefix.endswith(f"/{SESSION_UID}/")


async def test_finalize_master_builds_and_stamps():
    repo, storage = _seeded()
    rid = None
    for seq in range(3):
        receipt = await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
            data=_wav(), media_format="wav", chunk_seq=seq, is_final=False,
        )
        rid = receipt["recording_id"]
    master_key = await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)
    assert master_key.endswith("/audio/master.wav")
    assert master_key in storage.blobs  # the codec-built master was uploaded
    recs = await repo.get_recordings(MEETING_ID)
    mf = recs[0]["media_files"][0]
    assert mf["is_final"] is True
    assert mf["finalized_by"] == "recording_finalizer.master"
    assert mf["storage_path"] == master_key


async def test_upload_before_session_is_pending():
    repo, storage = _seeded()
    receipt = await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid="unknown-session",
        data=_wav(), media_format="wav", chunk_seq=0, is_final=False,
    )
    assert receipt == {"status": "pending"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_uid", "../session"),
        ("session_uid", "."),
        ("media_type", "../audio"),
        ("media_type", "screen"),
        ("media_format", "../wav"),
        ("media_format", "exe"),
        ("chunk_seq", -1),
    ],
)
async def test_upload_rejects_unsafe_storage_key_metadata_before_mutation(field, value):
    repo, storage = _seeded()
    kwargs = {
        "token_meeting_id": MEETING_ID,
        "session_uid": SESSION_UID,
        "data": _wav(),
        "media_type": "audio",
        "media_format": "wav",
        "chunk_seq": 0,
        field: value,
    }

    with pytest.raises(InvalidRecordingMetadata):
        await upload_chunk(repo, storage, **kwargs)

    assert storage.blobs == {}
    assert repo._meetings[MEETING_ID]["recording_prefixes"] == []
    assert await repo.get_recordings(MEETING_ID) == []


async def test_upload_refuses_a_meeting_without_a_resolved_owner_before_mutation():
    class OwnerlessRepo(InMemoryRecordingRepo):
        async def owner_of(self, meeting_id):
            return None

    repo = OwnerlessRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    storage = InMemoryStorage()

    with pytest.raises(RecordingWriteRefused, match="not writable"):
        await upload_chunk(
            repo,
            storage,
            token_meeting_id=MEETING_ID,
            session_uid=SESSION_UID,
            data=_wav(),
        )

    assert storage.blobs == {}
    assert repo._meetings[MEETING_ID]["recording_prefixes"] == []
    assert await repo.get_recordings(MEETING_ID) == []


# ── route: the upload endpoint authenticates the MeetingToken ────────────────────────────────────

def _client():
    from fastapi import FastAPI

    repo, storage = _seeded()
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    return TestClient(app)


def _resign_meeting_token(*, header_update=None, claim_update=None, claim_remove=()):
    import base64
    import hashlib
    import hmac
    import json

    token = mint_meeting_token(MEETING_ID, USER, "google_meet", "abc", secret=SECRET)
    header_b64, payload_b64, _ = token.split(".")
    header = json.loads(base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4)))
    claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    header.update(header_update or {})
    claims.update(claim_update or {})
    for claim in claim_remove:
        claims.pop(claim, None)

    def encode(value):
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    signing_input = f"{encode(header)}.{encode(claims)}"
    signature = hmac.new(SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def test_upload_route_requires_token():
    client = _client()
    r = client.post(
        "/internal/recordings/upload",
        data={"session_uid": SESSION_UID, "media_format": "wav", "chunk_seq": 0, "is_final": "true"},
        files={"file": ("c.wav", _wav(), "audio/wav")},
    )
    assert r.status_code == 401  # missing Authorization


def test_upload_route_accepts_valid_token():
    client = _client()
    token = mint_meeting_token(MEETING_ID, USER, "google_meet", "abc", secret=SECRET)
    r = client.post(
        "/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"session_uid": SESSION_UID, "media_format": "wav", "chunk_seq": 0, "is_final": "true"},
        files={"file": ("c.wav", _wav(), "audio/wav")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"


def test_upload_route_rejects_a_divergent_same_sequence_replay_without_mutation():
    from copy import deepcopy

    from fastapi import FastAPI

    repo, storage = _seeded()
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    client = TestClient(app)
    token = mint_meeting_token(MEETING_ID, USER, "google_meet", "abc", secret=SECRET)
    request = {
        "headers": {"Authorization": f"Bearer {token}"},
        "data": {
            "session_uid": SESSION_UID,
            "media_format": "wav",
            "chunk_seq": 0,
            "is_final": "false",
        },
    }
    first = client.post(
        "/internal/recordings/upload",
        **request,
        files={"file": ("c.wav", _wav(4), "audio/wav")},
    )
    assert first.status_code == 200
    before_recordings = deepcopy(repo._meetings[MEETING_ID]["recordings"])
    before_blobs = dict(storage.blobs)

    replay = client.post(
        "/internal/recordings/upload",
        **request,
        files={"file": ("c.wav", _wav(8), "audio/wav")},
    )

    assert replay.status_code == 409
    assert replay.json() == {"detail": "Recording chunk conflicts with its existing sequence"}
    assert repo._meetings[MEETING_ID]["recordings"] == before_recordings
    assert storage.blobs == before_blobs


@pytest.mark.parametrize(
    ("header_update", "claim_update"),
    [
        ({"alg": "HS512"}, None),
        ({"typ": "JWE"}, None),
        (None, {"iss": "another-service"}),
        (None, {"aud": "another-audience"}),
        (None, {"scope": "transcribe:read"}),
    ],
)
def test_upload_route_rejects_tokens_outside_the_recording_write_profile(
    header_update,
    claim_update,
):
    client = _client()
    token = _resign_meeting_token(
        header_update=header_update,
        claim_update=claim_update,
    )

    response = client.post(
        "/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"session_uid": SESSION_UID, "media_format": "wav", "chunk_seq": 0},
        files={"file": ("c.wav", _wav(), "audio/wav")},
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("claim_update", "claim_remove"),
    [
        (None, ("meeting_id",)),
        ({"meeting_id": "not-an-integer"}, ()),
        ({"meeting_id": 0}, ()),
    ],
)
def test_upload_route_rejects_tokens_without_a_valid_meeting_identity(
    claim_update,
    claim_remove,
):
    client = _client()
    token = _resign_meeting_token(
        claim_update=claim_update,
        claim_remove=claim_remove,
    )

    response = client.post(
        "/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"session_uid": SESSION_UID, "media_format": "wav", "chunk_seq": 0},
        files={"file": ("c.wav", _wav(), "audio/wav")},
    )

    assert response.status_code == 401


def test_upload_route_reports_invalid_storage_metadata_without_echoing_it():
    client = _client()
    token = mint_meeting_token(MEETING_ID, USER, "google_meet", "abc", secret=SECRET)
    response = client.post(
        "/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "session_uid": SESSION_UID,
            "media_type": "../private-audio",
            "media_format": "wav",
            "chunk_seq": 0,
            "is_final": "true",
        },
        files={"file": ("c.wav", _wav(), "audio/wav")},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid recording metadata"}
    assert "private-audio" not in response.text


def test_upload_route_rejects_an_oversized_chunk_before_storage_or_database_mutation(
    monkeypatch,
):
    from fastapi import FastAPI

    monkeypatch.setenv("RECORDING_CHUNK_MAX_BYTES", str(len(_wav(4))))
    repo, storage = _seeded()
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    client = TestClient(app)
    token = mint_meeting_token(MEETING_ID, USER, "google_meet", "abc", secret=SECRET)

    response = client.post(
        "/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"session_uid": SESSION_UID, "media_format": "wav", "chunk_seq": 0},
        files={"file": ("c.wav", _wav(8), "audio/wav")},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Recording chunk exceeds the upload limit"}
    assert storage.blobs == {}
    assert repo._meetings[MEETING_ID]["recording_prefixes"] == []
    assert repo._meetings[MEETING_ID]["recordings"] == []


def test_upload_route_reports_conflict_after_erasure_starts():
    from fastapi import FastAPI

    class RefusingRepo(InMemoryRecordingRepo):
        @asynccontextmanager
        async def recording_write(self, meeting_id):
            raise RecordingWriteRefused("private state detail")
            yield  # pragma: no cover

    repo = RefusingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    app = FastAPI()
    app.include_router(build_router(repo, InMemoryStorage(), token_secret=SECRET))
    client = TestClient(app, raise_server_exceptions=False)
    token = mint_meeting_token(MEETING_ID, USER, "google_meet", "abc", secret=SECRET)

    response = client.post(
        "/internal/recordings/upload",
        data={"session_uid": SESSION_UID, "media_format": "wav", "chunk_seq": 0, "is_final": "true"},
        files={"file": ("c.wav", _wav(), "audio/wav")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Meeting is no longer writable"}
    assert "private state detail" not in response.text


def test_master_route_reports_conflict_after_erasure_starts():
    from fastapi import FastAPI

    class RefusingRepo(InMemoryRecordingRepo):
        @asynccontextmanager
        async def recording_write(self, meeting_id):
            raise RecordingWriteRefused("private state detail")
            yield  # pragma: no cover

        async def list_meeting_recordings(self, user_id):
            return [{"id": 41, "meeting_id": MEETING_ID, "media_files": []}]

    app = FastAPI()
    app.include_router(build_router(RefusingRepo(), InMemoryStorage(), token_secret=SECRET))
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/recordings/41/master", headers={"x-user-id": str(USER)})

    assert response.status_code == 409
    assert response.json() == {"detail": "Meeting is no longer writable"}
    assert "private state detail" not in response.text


# ── G4: object-storage I/O must not block the event loop ─────────────────────────────────────────


async def test_s3_recording_list_reads_every_paginated_chunk_page():
    from meeting_api.recordings.adapters import S3Storage

    class PaginatedClient:
        def __init__(self):
            self.keys = [f"recordings/7/41/session/audio/{index:06d}.wav" for index in range(1002)]

        def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
            offset = int(ContinuationToken or 0)
            page = self.keys[offset : offset + 1000]
            next_offset = offset + len(page)
            return {
                "Contents": [{"Key": key} for key in page],
                "IsTruncated": next_offset < len(self.keys),
                **(
                    {"NextContinuationToken": str(next_offset)}
                    if next_offset < len(self.keys)
                    else {}
                ),
            }

    storage = S3Storage(bucket="recordings")
    storage._client = PaginatedClient()

    keys = await storage.list("recordings/7/41/session/audio/")

    assert len(keys) == 1002
    assert keys[0].endswith("000000.wav")
    assert keys[-1].endswith("001001.wav")


class _BlockingS3Client:
    """A stub boto3 client whose put_object BLOCKS (sync) — stands in for a slow S3 round-trip."""

    def __init__(self, block_s: float):
        self._block_s = block_s
        self.calls = 0

    def put_object(self, **kw):
        import time

        time.sleep(self._block_s)  # a real, blocking, synchronous call (what boto3 does)
        self.calls += 1
        return {}


async def test_s3_storage_does_not_block_the_event_loop():
    """G4: a blocking boto3 call must run OFF the loop (asyncio.to_thread), so the control plane keeps
    serving lifecycle/webhook/ws traffic during a slow/large S3 op. We run a ~0.3s blocking upload
    concurrently with a 5ms heartbeat — a non-blocking loop ticks many times; a blocked loop ~never."""
    import asyncio

    from meeting_api.recordings.adapters import S3Storage

    class _StubS3(S3Storage):
        def __init__(self, client):
            super().__init__(bucket="b")
            self._stub = client

        def _c(self):
            return self._stub

        # NB: _run is INHERITED (asyncio.to_thread) — that's exactly what's under test.

    storage = _StubS3(_BlockingS3Client(block_s=0.3))
    ticks = {"n": 0}
    stop = {"v": False}

    async def heartbeat():
        while not stop["v"]:
            ticks["n"] += 1
            await asyncio.sleep(0.005)

    hb = asyncio.create_task(heartbeat())
    try:
        await storage.upload("k", b"x" * 1024, content_type="audio/wav")
    finally:
        stop["v"] = True
        await hb

    assert storage._stub.calls == 1
    assert ticks["n"] >= 20, (
        f"event loop appears BLOCKED during the S3 upload (only {ticks['n']} heartbeats in ~0.3s) — "
        "the boto3 call is not being offloaded to a thread"
    )


async def test_cancelled_s3_upload_finishes_the_thread_before_releasing_its_caller():
    """A cancelled asyncio.to_thread waiter must not return while boto3 can still create an object.

    Recording upload holds its database write lease around this call. Delaying cancellation until the
    sync call finishes guarantees erasure cannot release the lease, sweep, and then receive a ghost
    object from the still-running worker thread.
    """
    import asyncio
    import threading

    from meeting_api.recordings.adapters import S3Storage

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class BlockingClient:
        def put_object(self, **kwargs):
            started.set()
            release.wait(timeout=2)
            finished.set()
            return {}

    storage = S3Storage(bucket="b")
    storage._client = BlockingClient()
    upload = asyncio.create_task(
        storage.upload("key", b"bytes", content_type="audio/wav")
    )
    while not started.is_set():
        await asyncio.sleep(0)

    upload.cancel()
    await asyncio.sleep(0.01)
    assert not upload.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await upload
    assert finished.is_set()


# ── G3: concurrent chunk folds must not lose updates (atomic read→modify→write) ──────────────────


class _YieldingStorage(InMemoryStorage):
    """An InMemoryStorage whose upload YIELDS the event loop, so two concurrent uploads genuinely
    interleave (forcing the read→modify→write race the atomic mutate must serialize)."""

    async def upload(self, key, data, *, content_type):
        import asyncio

        await asyncio.sleep(0)
        await super().upload(key, data, content_type=content_type)


async def test_concurrent_first_chunks_share_one_recording_prefix():
    """The first two requests for a session must not allocate competing object prefixes."""
    import asyncio

    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    storage = _YieldingStorage()

    receipts = await asyncio.gather(
        upload_chunk(
            repo,
            storage,
            token_meeting_id=MEETING_ID,
            session_uid=SESSION_UID,
            data=_wav(),
            media_format="wav",
            chunk_seq=0,
            is_final=False,
        ),
        upload_chunk(
            repo,
            storage,
            token_meeting_id=MEETING_ID,
            session_uid=SESSION_UID,
            data=_wav(),
            media_format="wav",
            chunk_seq=1,
            is_final=False,
        ),
    )

    prefixes = {receipt["storage_path"].rsplit("/", 2)[0] for receipt in receipts}
    assert len(prefixes) == 1
    assert len(repo._meetings[MEETING_ID]["recording_prefixes"]) == 1
    assert all(key.startswith(f"{next(iter(prefixes))}/") for key in storage.blobs)


async def test_concurrent_chunk_uploads_do_not_lose_updates():
    """G3: two chunk uploads racing on the SAME recording must BOTH be folded. The old
    get_recordings → apply → put_recordings ran in SEPARATE transactions, so the second put clobbered
    the first (lost update → chunk_count stuck at 2). The atomic mutate_recordings re-reads the LIVE
    list under one lock and folds cumulatively → chunk_count 3."""
    import asyncio

    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    storage = _YieldingStorage()

    # chunk 0 (sequential) establishes the recording.
    await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                       data=_wav(), media_format="wav", chunk_seq=0, is_final=False)
    # chunks 1 + 2 race.
    await asyncio.gather(
        upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                     data=_wav(), media_format="wav", chunk_seq=1, is_final=False),
        upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                     data=_wav(), media_format="wav", chunk_seq=2, is_final=False),
    )

    recs = await repo.get_recordings(MEETING_ID)
    bot_recs = [r for r in recs if r.get("source") == "bot"]
    assert len(bot_recs) == 1, f"exactly one recording for the session, got {len(bot_recs)}"
    mf = next(m for m in bot_recs[0]["media_files"] if m["type"] == "audio")
    assert mf["chunk_count"] == 3, f"all 3 chunks must be folded (no lost update), got {mf['chunk_count']}"
