"""ZAKI Minutes retention — owner-scoped, cross-carrier meeting erasure."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from meeting_api.retention import ErasureFailed, erase_meeting
from meeting_api.retention.fakes import InMemoryRetentionRepo, InMemoryRetentionStorage


async def test_owner_erasure_removes_raw_data_and_preserves_other_tenant():
    repo = InMemoryRetentionRepo()
    storage = InMemoryRetentionStorage()
    repo.seed_meeting(
        user_id="user_a",
        meeting_id="meeting_a",
        transcript_rows=["private transcript one", "private transcript two"],
        summaries=["private summary"],
        recording_keys=["recordings/user_a/meeting_a/audio-1", "recordings/user_a/meeting_a/audio-2"],
    )
    repo.seed_meeting(
        user_id="user_b",
        meeting_id="meeting_b",
        transcript_rows=["other tenant transcript"],
        summaries=["other tenant summary"],
        recording_keys=["recordings/user_b/meeting_b/audio-1"],
    )
    for key in repo.recording_keys():
        storage.seed(key, f"bytes:{key}".encode())
    other_before = (repo.snapshot("meeting_b"), storage.snapshot("recordings/user_b/meeting_b/"))

    receipt = await erase_meeting(
        repo,
        storage,
        user_id="user_a",
        meeting_id="meeting_a",
        erased_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
        policy_version="minutes-retention-v1",
    )

    assert receipt is not None
    assert receipt.as_dict() == {
        "user_id": "user_a",
        "meeting_id": "meeting_a",
        "erased_at": "2026-07-14T12:30:00+00:00",
        "policy_version": "minutes-retention-v1",
        "deleted": {
            "meeting_rows": 1,
            "transcript_rows": 2,
            "summary_documents": 1,
            "recording_objects": 2,
        },
    }
    assert repo.snapshot("meeting_a") is None
    assert storage.snapshot("recordings/user_a/meeting_a/") == {}
    assert (repo.snapshot("meeting_b"), storage.snapshot("recordings/user_b/meeting_b/")) == other_before
    serialized = json.dumps(receipt.as_dict(), sort_keys=True)
    assert "private transcript" not in serialized
    assert "private summary" not in serialized
    assert "recordings/" not in serialized

    foreign_receipt = await erase_meeting(
        repo,
        storage,
        user_id="user_a",
        meeting_id="meeting_b",
        erased_at=datetime(2026, 7, 14, 12, 31, tzinfo=timezone.utc),
        policy_version="minutes-retention-v1",
    )
    assert foreign_receipt is None
    assert (repo.snapshot("meeting_b"), storage.snapshot("recordings/user_b/meeting_b/")) == other_before


async def test_storage_failure_keeps_database_authoritative_and_hides_object_key():
    repo = InMemoryRetentionRepo()
    repo.seed_meeting(
        user_id="user_a",
        meeting_id="meeting_a",
        transcript_rows=["private transcript"],
        summaries=[],
        recording_keys=["recordings/user_a/meeting_a/private-audio"],
    )

    class FailingStorage(InMemoryRetentionStorage):
        async def delete(self, key: str) -> bool:
            raise RuntimeError(f"storage refused {key}")

    with pytest.raises(ErasureFailed, match="meeting erasure failed before database commit") as exc:
        await erase_meeting(
            repo,
            FailingStorage(),
            user_id="user_a",
            meeting_id="meeting_a",
            erased_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
            policy_version="minutes-retention-v1",
        )

    assert exc.value.__cause__ is None
    assert "private-audio" not in str(exc.value)
    assert repo.snapshot("meeting_a") is not None


async def test_database_failure_after_object_delete_returns_safe_retry_signal():
    class FailingRepo(InMemoryRetentionRepo):
        async def commit_erasure(self, plan):
            raise RuntimeError("database refused private transcript")

    repo = FailingRepo()
    storage = InMemoryRetentionStorage()
    repo.seed_meeting(
        user_id="user_a",
        meeting_id="meeting_a",
        transcript_rows=["private transcript"],
        summaries=[],
        recording_keys=["recordings/user_a/meeting_a/private-audio"],
    )
    storage.seed("recordings/user_a/meeting_a/private-audio", b"private audio bytes")

    with pytest.raises(ErasureFailed, match="meeting erasure requires retry") as exc:
        await erase_meeting(
            repo,
            storage,
            user_id="user_a",
            meeting_id="meeting_a",
            erased_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
            policy_version="minutes-retention-v1",
        )

    assert exc.value.__cause__ is None
    assert "private transcript" not in str(exc.value)
    assert repo.snapshot("meeting_a") is not None
    assert storage.snapshot("recordings/user_a/meeting_a/") == {}


async def test_retry_receipt_counts_every_planned_object_when_one_is_already_absent():
    repo = InMemoryRetentionRepo()
    storage = InMemoryRetentionStorage()
    repo.seed_meeting(
        user_id="user_a",
        meeting_id="meeting_a",
        transcript_rows=[],
        summaries=[],
        recording_keys=["recordings/user_a/meeting_a/audio-1", "recordings/user_a/meeting_a/audio-2"],
    )
    storage.seed("recordings/user_a/meeting_a/audio-2", b"remaining bytes")

    receipt = await erase_meeting(
        repo,
        storage,
        user_id="user_a",
        meeting_id="meeting_a",
        erased_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
        policy_version="minutes-retention-v1",
    )

    assert receipt is not None
    assert receipt.recording_objects == 2
