"""Production-boundary tests for Minutes retention adapters."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from meeting_api.recordings.adapters import SqlAlchemyRecordingRepo
from meeting_api.recordings.ports import RecordingWriteRefused
from meeting_api.retention.adapters import (
    S3RetentionStorage,
    SqlAlchemyRetentionRepo,
    recording_prefixes_for_meeting,
)
from meeting_api.retention.fakes import InMemoryRetentionStorage
from meeting_api.retention.service import ErasureFailed, erase_meeting


class FakeS3Client:
    def __init__(self, keys: list[str]):
        self.keys = set(keys)
        self.list_calls = 0
        self.delete_batch_sizes: list[int] = []
        self.delete_quiet: list[bool | None] = []

    def get_bucket_versioning(self, *, Bucket):
        return {}

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys=1000, ContinuationToken=None):
        self.list_calls += 1
        matching = sorted(key for key in self.keys if key.startswith(Prefix))
        offset = int(ContinuationToken or 0)
        page = matching[offset : offset + MaxKeys]
        next_offset = offset + len(page)
        response = {
            "Contents": [{"Key": key} for key in page],
            "IsTruncated": next_offset < len(matching),
        }
        if response["IsTruncated"]:
            response["NextContinuationToken"] = str(next_offset)
        return response

    def delete_objects(self, *, Bucket, Delete):
        objects = Delete["Objects"]
        self.delete_batch_sizes.append(len(objects))
        self.delete_quiet.append(Delete.get("Quiet"))
        for obj in objects:
            self.keys.discard(obj["Key"])
        return {"Deleted": objects, "Errors": []}


async def test_s3_retention_storage_censuses_and_deletes_every_page():
    prefix = "recordings/user-a/recording-a/session-a/"
    client = FakeS3Client(
        [f"{prefix}audio/{index:06d}.wav" for index in range(2505)]
        + ["recordings/user-b/recording-b/session-b/audio/master.wav"]
    )
    storage = S3RetentionStorage(bucket="minutes-test", client=client)

    assert await storage.count_prefix(prefix) == 2505
    assert await storage.delete_prefix(prefix) == 2505
    assert await storage.count_prefix(prefix) == 0
    assert client.keys == {"recordings/user-b/recording-b/session-b/audio/master.wav"}
    assert client.delete_batch_sizes == [1000, 1000, 505]
    assert client.delete_quiet == [True, True, True]


class FakeVersionedS3Client:
    def __init__(self, objects: list[tuple[str, str, str]]):
        # (kind, key, version_id), where kind is "version" or "marker".
        self.objects = set(objects)
        self.delete_batch_sizes: list[int] = []
        self.delete_quiet: list[bool | None] = []

    def get_bucket_versioning(self, *, Bucket):
        return {"Status": "Enabled"}

    def list_object_versions(
        self, *, Bucket, Prefix, MaxKeys=1000, KeyMarker=None, VersionIdMarker=None
    ):
        matching = sorted(item for item in self.objects if item[1].startswith(Prefix))
        offset = int(KeyMarker or 0)
        page = matching[offset : offset + MaxKeys]
        next_offset = offset + len(page)
        response = {
            "Versions": [
                {"Key": key, "VersionId": version_id}
                for kind, key, version_id in page
                if kind == "version"
            ],
            "DeleteMarkers": [
                {"Key": key, "VersionId": version_id}
                for kind, key, version_id in page
                if kind == "marker"
            ],
            "IsTruncated": next_offset < len(matching),
        }
        if response["IsTruncated"]:
            response["NextKeyMarker"] = str(next_offset)
            response["NextVersionIdMarker"] = "cursor"
        return response

    def delete_objects(self, *, Bucket, Delete):
        objects = Delete["Objects"]
        self.delete_batch_sizes.append(len(objects))
        self.delete_quiet.append(Delete.get("Quiet"))
        for obj in objects:
            target = (obj["Key"], obj["VersionId"])
            self.objects = {
                item for item in self.objects if (item[1], item[2]) != target
            }
        return {"Deleted": objects, "Errors": []}


async def test_s3_retention_storage_deletes_versions_and_delete_markers():
    prefix = "recordings/user-a/recording-a/session-a/"
    client = FakeVersionedS3Client(
        [
            ("version", f"{prefix}audio/chunk.wav", "v1"),
            ("version", f"{prefix}audio/chunk.wav", "v2"),
            ("marker", f"{prefix}audio/chunk.wav", "d1"),
            ("version", f"{prefix}audio/master.wav", "v3"),
            ("version", "recordings/user-b/other/session/audio/master.wav", "v4"),
        ]
    )
    storage = S3RetentionStorage(bucket="minutes-test", client=client)

    assert await storage.count_prefix(prefix) == 4
    assert await storage.delete_prefix(prefix) == 4
    assert await storage.count_prefix(prefix) == 0
    assert client.objects == {
        ("version", "recordings/user-b/other/session/audio/master.wav", "v4")
    }
    assert client.delete_batch_sizes == [4]
    assert client.delete_quiet == [True]


async def test_s3_retention_storage_paginates_every_version_and_marker():
    prefix = "recordings/user-a/recording-a/session-a/"
    client = FakeVersionedS3Client(
        [
            (
                "marker" if index % 3 == 0 else "version",
                f"{prefix}audio/{index:06d}.wav",
                f"v{index}",
            )
            for index in range(2505)
        ]
    )
    storage = S3RetentionStorage(bucket="minutes-test", client=client)

    assert await storage.count_prefix(prefix) == 2505
    assert await storage.delete_prefix(prefix) == 2505
    assert await storage.count_prefix(prefix) == 0
    assert client.delete_batch_sizes == [1000, 1000, 505]


def test_recording_prefixes_are_derived_from_owned_recording_identity():
    data = {
        "recordings": [
            {
                "id": 41,
                "session_uid": "session-a",
                "media_files": [
                    {"storage_path": "recordings/7/41/session-a/audio/master.wav"},
                    {"storage_path": "recordings/7/41/session-a/video/000003.webm"},
                ],
            },
            {
                "id": 42,
                "session_uid": "session-b",
                "media_files": [
                    {"storage_path": "recordings/7/42/session-b/audio/000000.wav"},
                ],
            },
        ]
    }

    assert recording_prefixes_for_meeting(7, data) == (
        "recordings/7/41/session-a/",
        "recordings/7/42/session-b/",
    )


def test_recording_prefix_derivation_rejects_mismatched_storage_identity():
    data = {
        "recordings": [
            {
                "id": 41,
                "session_uid": "session-a",
                "media_files": [
                    {"storage_path": "recordings/other-user/41/session-a/audio/master.wav"},
                ],
            }
        ]
    }

    with pytest.raises(ValueError, match="recording storage identity mismatch"):
        recording_prefixes_for_meeting(7, data)


def test_recording_prefix_derivation_ignores_metadata_without_object_paths():
    assert recording_prefixes_for_meeting(
        7,
        {"recordings": [{"status": "pending", "media_files": []}]},
    ) == ()


def test_recording_prefix_derivation_includes_durable_preupload_intents():
    prefix = "recordings/7/41/session-a/"
    assert recording_prefixes_for_meeting(
        7,
        {"zaki_recording_prefixes": [prefix], "recordings": []},
    ) == (prefix,)


class FakeDbResult:
    def __init__(self, *, row=None, scalar=None, rowcount=0):
        self._row = row
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._row

    def scalar_one(self):
        return self._scalar


class FakeDbSession:
    def __init__(self, state):
        self.state = state
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        self.commits += 1

    async def execute(self, statement, params=None):
        params = params or {}
        sql = " ".join(statement.split())
        if "pg_advisory_xact_lock" in sql:
            return FakeDbResult()
        if sql.startswith("SELECT id, user_id, data FROM meetings"):
            meeting = self.state.get("meeting")
            return FakeDbResult(row=dict(meeting) if meeting else None)
        if sql.startswith("SELECT count(*) FROM transcriptions"):
            return FakeDbResult(scalar=self.state["transcript_rows"])
        if sql.startswith("UPDATE meetings SET data"):
            self.state["meeting"]["data"]["zaki_retention"] = json.loads(params["retention"])
            return FakeDbResult(rowcount=1)
        if sql.startswith("DELETE FROM transcriptions"):
            deleted = self.state["transcript_rows"]
            self.state["transcript_rows"] = 0
            return FakeDbResult(rowcount=deleted)
        if sql.startswith("DELETE FROM meeting_sessions"):
            deleted = self.state["session_rows"]
            self.state["session_rows"] = 0
            return FakeDbResult(rowcount=deleted)
        if sql.startswith("DELETE FROM meetings"):
            existed = int(self.state.get("meeting") is not None)
            self.state["meeting"] = None
            return FakeDbResult(rowcount=existed)
        raise AssertionError(f"unexpected SQL: {sql}")


async def test_postgres_adapter_persists_erasing_census_then_deletes_owned_rows():
    state = {
        "meeting": {
            "id": 1,
            "user_id": 7,
            "data": {
                "summary": {"text": "private"},
                "recordings": [
                    {
                        "id": 41,
                        "session_uid": "session-a",
                        "media_files": [
                            {"storage_path": "recordings/7/41/session-a/audio/master.wav"}
                        ],
                    }
                ],
            },
        },
        "transcript_rows": 2,
        "session_rows": 1,
    }
    session = FakeDbSession(state)
    repo = SqlAlchemyRetentionRepo(lambda: session, statement_factory=lambda sql: sql)

    plan = await repo.begin_erasure("7", "1")
    assert plan is not None
    assert plan.recording_prefixes == ("recordings/7/41/session-a/",)
    assert plan.recording_objects is None
    assert state["meeting"]["data"]["zaki_retention"]["state"] == "erasing"

    plan = await repo.record_object_census(plan, 3)
    assert plan.recording_objects == 3
    assert state["meeting"]["data"]["zaki_retention"]["recording_objects"] == 3

    deleted = await repo.commit_erasure(plan)
    assert deleted == {"meeting_rows": 1, "transcript_rows": 2, "summary_documents": 1}
    assert state["meeting"] is None


async def test_persisted_erasure_retry_prefix_cannot_cross_the_meeting_owner():
    foreign_prefix = "recordings/8/99/session-b/"
    state = {
        "meeting": {
            "id": 1,
            "user_id": 7,
            "data": {
                "zaki_retention": {
                    "state": "erasing",
                    "recording_prefixes": [foreign_prefix],
                    "recording_objects": 1,
                    "transcript_rows": 0,
                    "summary_documents": 0,
                }
            },
        },
        "transcript_rows": 0,
        "session_rows": 0,
    }
    repo = SqlAlchemyRetentionRepo(
        lambda: FakeDbSession(state), statement_factory=lambda sql: sql
    )
    storage = InMemoryRetentionStorage()
    foreign_key = f"{foreign_prefix}audio/master.wav"
    storage.seed(foreign_key, b"other tenant")

    with pytest.raises(ErasureFailed, match="planning requires retry"):
        await erase_meeting(
            repo,
            storage,
            user_id="7",
            meeting_id="1",
            erased_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
            policy_version="minutes-retention-v1",
        )

    assert storage.snapshot(foreign_prefix) == {foreign_key: b"other tenant"}
    assert state["meeting"] is not None


class FakeGateSession:
    def __init__(self, data):
        self.data = data
        self.events: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement, params=None):
        sql = " ".join(statement.split())
        if "pg_advisory_lock_shared" in sql:
            self.events.append("lock")
            return FakeDbResult()
        if sql.startswith("SELECT data FROM meetings"):
            self.events.append("state")
            return FakeDbResult(row={"data": self.data} if self.data is not None else None)
        if "pg_advisory_unlock_shared" in sql:
            self.events.append("unlock")
            return FakeDbResult()
        raise AssertionError(f"unexpected SQL: {sql}")


async def test_recording_adapter_uses_shared_lock_and_refuses_erasing_meeting():
    open_session = FakeGateSession({})
    repo = SqlAlchemyRecordingRepo(
        lambda: open_session,
        statement_factory=lambda sql: sql,
    )

    async with repo.recording_write(1):
        open_session.events.append("body")
    assert open_session.events == ["lock", "state", "body", "unlock"]

    erasing_session = FakeGateSession({"zaki_retention": {"state": "erasing"}})
    repo = SqlAlchemyRecordingRepo(
        lambda: erasing_session,
        statement_factory=lambda sql: sql,
    )
    with pytest.raises(RecordingWriteRefused, match="not writable"):
        async with repo.recording_write(1):
            raise AssertionError("erasing meeting entered the write body")
    assert erasing_session.events == ["lock", "state", "unlock"]


async def test_recording_adapter_refuses_audio_expired_meeting():
    session = FakeGateSession(
        {"zaki_retention": {"expired_scopes": ["audio"]}}
    )
    repo = SqlAlchemyRecordingRepo(
        lambda: session,
        statement_factory=lambda sql: sql,
    )

    with pytest.raises(RecordingWriteRefused, match="not writable"):
        async with repo.recording_write(1):
            raise AssertionError("audio-expired meeting entered the write body")

    assert session.events == ["lock", "state", "unlock"]
