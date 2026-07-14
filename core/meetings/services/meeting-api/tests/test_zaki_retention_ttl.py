"""Clock-controlled policy proof for independent Minutes carrier expiry."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from meeting_api.retention import (
    DueScope,
    ScopeExpiries,
    TtlBatchFailed,
    materialize_scope_expiries,
    run_ttl_batch,
)


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


class InMemoryTtlStore:
    def __init__(self):
        self.meetings: dict[tuple[str, str], dict] = {}
        self.failed: set[tuple[str, str, str]] = set()
        self.requested_limits: list[int] = []

    def seed(
        self,
        *,
        user_id: str,
        meeting_id: str,
        expiries: ScopeExpiries,
        audio: bytes = b"audio",
        transcript: str = "private transcript",
        summary: str = "private summary",
    ) -> None:
        self.meetings[(user_id, meeting_id)] = {
            "expiries": expiries,
            "audio": audio,
            "transcript": transcript,
            "summary": summary,
        }

    async def list_due_scopes(self, *, now: datetime, limit: int):
        self.requested_limits.append(limit)
        due: list[DueScope] = []
        for (user_id, meeting_id), meeting in sorted(self.meetings.items()):
            for scope in ("audio", "transcript", "summary"):
                expires_at = getattr(meeting["expiries"], scope)
                if meeting[scope] is not None and expires_at <= now:
                    due.append(
                        DueScope(
                            user_id=user_id,
                            meeting_id=meeting_id,
                            scope=scope,
                            expires_at=expires_at,
                        )
                    )
        return tuple(due[:limit])

    async def expire_scope(self, item: DueScope) -> int:
        key = (item.user_id, item.meeting_id, item.scope)
        if key in self.failed:
            raise RuntimeError("private carrier failure")
        meeting = self.meetings[key[:2]]
        if meeting[item.scope] is None:
            return 0
        meeting[item.scope] = None
        return 1


def _expiries(*, audio_days=1, transcript_days=2, summary_days=3):
    return ScopeExpiries(
        audio=NOW + timedelta(days=audio_days),
        transcript=NOW + timedelta(days=transcript_days),
        summary=NOW + timedelta(days=summary_days),
    )


def test_scope_expiries_are_independent_utc_instants_and_never_extend():
    initial = _expiries()
    proposed_extension = ScopeExpiries(
        audio=initial.audio + timedelta(days=10),
        transcript=initial.transcript + timedelta(days=10),
        summary=initial.summary + timedelta(days=10),
    )

    assert materialize_scope_expiries(proposed_extension, existing=initial) == initial

    shortened = ScopeExpiries(
        audio=initial.audio - timedelta(hours=1),
        transcript=initial.transcript,
        summary=initial.summary - timedelta(hours=2),
    )
    assert materialize_scope_expiries(shortened, existing=initial) == shortened


def test_scope_expiries_reject_naive_or_missing_values():
    with pytest.raises(ValueError, match="UTC"):
        materialize_scope_expiries(
            ScopeExpiries(
                audio=NOW.replace(tzinfo=None),
                transcript=NOW,
                summary=NOW,
            )
        )


async def test_ttl_batch_expires_each_scope_only_at_its_boundary():
    store = InMemoryTtlStore()
    store.seed(user_id="user-a", meeting_id="meeting-a", expiries=_expiries())

    before = await run_ttl_batch(store, now=NOW + timedelta(days=1) - timedelta(microseconds=1))
    assert before.expired == {"audio": 0, "transcript": 0, "summary": 0}

    audio = await run_ttl_batch(store, now=NOW + timedelta(days=1))
    assert audio.expired == {"audio": 1, "transcript": 0, "summary": 0}
    assert store.meetings[("user-a", "meeting-a")]["transcript"] is not None
    assert store.meetings[("user-a", "meeting-a")]["summary"] is not None

    transcript = await run_ttl_batch(store, now=NOW + timedelta(days=2))
    assert transcript.expired == {"audio": 0, "transcript": 1, "summary": 0}

    summary = await run_ttl_batch(store, now=NOW + timedelta(days=3))
    assert summary.expired == {"audio": 0, "transcript": 0, "summary": 1}


async def test_ttl_batch_is_bounded_and_reports_content_free_counts():
    store = InMemoryTtlStore()
    for index in range(5):
        store.seed(
            user_id="user-a",
            meeting_id=f"meeting-{index}",
            expiries=_expiries(audio_days=0, transcript_days=10, summary_days=10),
        )

    receipt = await run_ttl_batch(store, now=NOW, limit=2)

    assert receipt.attempted == 2
    assert receipt.expired == {"audio": 2, "transcript": 0, "summary": 0}
    assert receipt.failed == 0
    assert store.requested_limits == [2]
    assert "meeting" not in repr(receipt)
    assert "private" not in repr(receipt)
    mutated_view = receipt.expired
    mutated_view["audio"] = 999
    assert receipt.expired["audio"] == 2


async def test_failed_scope_remains_retryable_without_blocking_other_due_scopes():
    store = InMemoryTtlStore()
    expiry = _expiries(audio_days=0, transcript_days=0, summary_days=10)
    store.seed(user_id="user-a", meeting_id="meeting-a", expiries=expiry)
    store.failed.add(("user-a", "meeting-a", "audio"))

    first = await run_ttl_batch(store, now=NOW)
    assert first.expired == {"audio": 0, "transcript": 1, "summary": 0}
    assert first.failed == 1
    assert store.meetings[("user-a", "meeting-a")]["audio"] == b"audio"

    store.failed.clear()
    retry = await run_ttl_batch(store, now=NOW)
    assert retry.expired == {"audio": 1, "transcript": 0, "summary": 0}
    assert retry.failed == 0


async def test_ttl_batch_leaves_a_second_tenant_byte_identically_unchanged():
    store = InMemoryTtlStore()
    store.seed(
        user_id="user-a",
        meeting_id="meeting-a",
        expiries=_expiries(audio_days=0, transcript_days=0, summary_days=0),
    )
    store.seed(
        user_id="user-b",
        meeting_id="meeting-b",
        expiries=_expiries(audio_days=10, transcript_days=10, summary_days=10),
        audio=b"control audio",
        transcript="control transcript",
        summary="control summary",
    )
    control = deepcopy(store.meetings[("user-b", "meeting-b")])

    receipt = await run_ttl_batch(store, now=NOW)

    assert receipt.expired == {"audio": 1, "transcript": 1, "summary": 1}
    assert store.meetings[("user-b", "meeting-b")] == control


@pytest.mark.parametrize("limit", [0, -1, 501, True])
async def test_ttl_batch_rejects_invalid_limits_before_store_access(limit):
    store = InMemoryTtlStore()

    with pytest.raises(ValueError, match="limit"):
        await run_ttl_batch(store, now=NOW, limit=limit)

    assert store.requested_limits == []


async def test_ttl_batch_fails_closed_on_future_or_duplicate_store_candidates():
    class InvalidStore(InMemoryTtlStore):
        async def list_due_scopes(self, *, now, limit):
            item = DueScope(
                user_id="user-a",
                meeting_id="meeting-a",
                scope="audio",
                expires_at=now + timedelta(seconds=1),
            )
            return (item, item)

    store = InvalidStore()

    with pytest.raises(TtlBatchFailed, match="invalid") as exc:
        await run_ttl_batch(store, now=NOW)

    assert exc.value.__cause__ is None
    assert store.meetings == {}
