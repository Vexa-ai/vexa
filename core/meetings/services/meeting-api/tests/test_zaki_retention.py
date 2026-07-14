"""ZAKI Minutes retention — owner-scoped, cross-carrier meeting erasure."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
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
        recording_prefixes=["recordings/user_a/recording_a/session_a/"],
        recording_objects=3,
    )
    repo.seed_meeting(
        user_id="user_b",
        meeting_id="meeting_b",
        transcript_rows=["other tenant transcript"],
        summaries=["other tenant summary"],
        recording_prefixes=["recordings/user_b/recording_b/session_b/"],
        recording_objects=1,
    )
    for key in [
        "recordings/user_a/recording_a/session_a/audio/000000.wav",
        "recordings/user_a/recording_a/session_a/audio/000001.wav",
        "recordings/user_a/recording_a/session_a/audio/master.wav",
        "recordings/user_b/recording_b/session_b/audio/master.wav",
    ]:
        storage.seed(key, f"bytes:{key}".encode())
    other_before = (repo.snapshot("meeting_b"), storage.snapshot("recordings/user_b/recording_b/"))

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
            "recording_objects": 3,
        },
    }
    assert repo.snapshot("meeting_a") is None
    assert storage.snapshot("recordings/user_a/recording_a/") == {}
    assert (repo.snapshot("meeting_b"), storage.snapshot("recordings/user_b/recording_b/")) == other_before
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
    assert (repo.snapshot("meeting_b"), storage.snapshot("recordings/user_b/recording_b/")) == other_before


async def test_storage_failure_keeps_database_authoritative_and_hides_object_key():
    repo = InMemoryRetentionRepo()
    repo.seed_meeting(
        user_id="user_a",
        meeting_id="meeting_a",
        transcript_rows=["private transcript"],
        summaries=[],
        recording_prefixes=["recordings/user_a/recording_a/session_a/"],
        recording_objects=1,
    )

    class FailingStorage(InMemoryRetentionStorage):
        async def delete_prefix(self, prefix: str) -> int:
            raise RuntimeError(f"storage refused {prefix}")

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


async def test_planning_failure_hides_database_content_and_requires_retry():
    class FailingPlanRepo(InMemoryRetentionRepo):
        async def begin_erasure(self, user_id, meeting_id):
            raise RuntimeError("database exposed private transcript")

    with pytest.raises(ErasureFailed, match="planning requires retry") as exc:
        await erase_meeting(
            FailingPlanRepo(),
            InMemoryRetentionStorage(),
            user_id="user_a",
            meeting_id="meeting_a",
            erased_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
            policy_version="minutes-retention-v1",
        )

    assert exc.value.__cause__ is None
    assert "private transcript" not in str(exc.value)


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
        recording_prefixes=["recordings/user_a/recording_a/session_a/"],
        recording_objects=1,
    )
    storage.seed("recordings/user_a/recording_a/session_a/audio/private-audio", b"private audio bytes")

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
    assert storage.snapshot("recordings/user_a/recording_a/") == {}


async def test_retry_receipt_counts_every_planned_object_when_one_is_already_absent():
    repo = InMemoryRetentionRepo()
    storage = InMemoryRetentionStorage()
    repo.seed_meeting(
        user_id="user_a",
        meeting_id="meeting_a",
        transcript_rows=[],
        summaries=[],
        recording_prefixes=["recordings/user_a/recording_a/session_a/"],
        recording_objects=2,
    )
    storage.seed("recordings/user_a/recording_a/session_a/audio/audio-2", b"remaining bytes")

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


async def test_uncensused_plan_counts_and_persists_objects_before_deletion():
    class UncensusedRepo(InMemoryRetentionRepo):
        def __init__(self):
            super().__init__()
            self.census_calls = 0

        async def begin_erasure(self, user_id, meeting_id):
            plan = await super().begin_erasure(user_id, meeting_id)
            return replace(plan, recording_objects=None) if plan else None

        async def record_object_census(self, plan, recording_objects):
            self.census_calls += 1
            return replace(plan, recording_objects=recording_objects)

    repo = UncensusedRepo()
    storage = InMemoryRetentionStorage()
    prefix = "recordings/user_a/recording_a/session_a/"
    repo.seed_meeting(
        user_id="user_a",
        meeting_id="meeting_a",
        transcript_rows=[],
        summaries=[],
        recording_prefixes=[prefix],
        recording_objects=99,
    )
    storage.seed(f"{prefix}audio/000000.wav", b"chunk")
    storage.seed(f"{prefix}audio/master.wav", b"master")

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
    assert repo.census_calls == 1
    assert storage.snapshot(prefix) == {}


async def test_erasure_quiesces_inflight_upload_and_sweeps_its_late_object():
    repo = InMemoryRetentionRepo()
    storage = InMemoryRetentionStorage()
    prefix = "recordings/user_a/recording_a/session_a/"
    repo.seed_meeting(
        user_id="user_a",
        meeting_id="meeting_a",
        transcript_rows=["private transcript"],
        summaries=[],
        recording_prefixes=[prefix],
        recording_objects=2,
    )
    storage.seed(f"{prefix}audio/000000.wav", b"first chunk")

    assert await repo.begin_recording_write("meeting_a") is True
    erasure = asyncio.create_task(erase_meeting(
        repo,
        storage,
        user_id="user_a",
        meeting_id="meeting_a",
        erased_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
        policy_version="minutes-retention-v1",
    ))

    for _ in range(10):
        if repo.snapshot("meeting_a")["state"] == "erasing":
            break
        await asyncio.sleep(0)
    assert repo.snapshot("meeting_a")["state"] == "erasing"
    assert await repo.begin_recording_write("meeting_a") is False

    storage.seed(f"{prefix}audio/000001.wav", b"late in-flight chunk")
    await repo.end_recording_write("meeting_a")
    receipt = await erasure

    assert receipt is not None
    assert receipt.recording_objects == 2
    assert storage.snapshot(prefix) == {}
    assert repo.snapshot("meeting_a") is None


async def test_erasure_rejects_a_broad_recording_prefix_before_storage_deletion():
    repo = InMemoryRetentionRepo()
    storage = InMemoryRetentionStorage()
    repo.seed_meeting(
        user_id="user_a",
        meeting_id="meeting_a",
        transcript_rows=["private transcript"],
        summaries=[],
        recording_prefixes=["recordings/user_a/"],
        recording_objects=1,
    )
    victim_key = "recordings/user_a/another_recording/session/audio/master.wav"
    storage.seed(victim_key, b"must survive")

    with pytest.raises(ErasureFailed, match="meeting erasure plan is invalid") as exc:
        await erase_meeting(
            repo,
            storage,
            user_id="user_a",
            meeting_id="meeting_a",
            erased_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
            policy_version="minutes-retention-v1",
        )

    assert "recordings/user_a" not in str(exc.value)
    assert storage.snapshot("recordings/user_a/") == {victim_key: b"must survive"}
    assert repo.snapshot("meeting_a") is not None


async def test_transcript_only_meeting_erases_without_a_recording_prefix():
    repo = InMemoryRetentionRepo()
    repo.seed_meeting(
        user_id="user_a",
        meeting_id="meeting_a",
        transcript_rows=["private transcript"],
        summaries=[],
        recording_prefixes=[],
        recording_objects=0,
    )

    receipt = await erase_meeting(
        repo,
        InMemoryRetentionStorage(),
        user_id="user_a",
        meeting_id="meeting_a",
        erased_at=datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc),
        policy_version="minutes-retention-v1",
    )

    assert receipt is not None
    assert receipt.recording_objects == 0
    assert repo.snapshot("meeting_a") is None
