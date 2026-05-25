"""Tests for the v0.10.6.1 internal outbound-event ledger.

#330 — several independent paths can schedule ``run_all_tasks(meeting_id)`` for
the same meeting. Internal POST_MEETING_HOOKS feed billing/usage, so they must
be duplicate-safe and retryable. The hotfix stores a tiny outbox in
``meeting.data["outbound_events"]`` instead of adding a DB column.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api import post_meeting as post_meeting_module
from meeting_api.models import Meeting
from meeting_api.webhook_delivery import DeliveryResult

from .conftest import MockResult, TEST_MEETING_ID, TEST_USER_EMAIL, make_meeting


def test_guard_claims_outbound_event_before_delivery():
    """Static guard: row-lock ledger claim must happen before HTTP delivery."""
    src = inspect.getsource(post_meeting_module.fire_post_meeting_hooks)
    tree = ast.parse(src)

    first_claim: int | None = None
    first_deliver: int | None = None
    first_mark: int | None = None

    for node in ast.walk(tree):
        if (
            first_claim is None
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "claim_outbound_event"
        ):
            first_claim = node.lineno
        if (
            first_deliver is None
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "deliver_with_result"
        ):
            first_deliver = node.lineno
        if (
            first_mark is None
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "mark_outbound_event"
        ):
            first_mark = node.lineno

    assert first_claim is not None, "fire_post_meeting_hooks must claim an outbound event"
    assert first_deliver is not None, "fire_post_meeting_hooks must deliver the claimed event"
    assert first_mark is not None, "fire_post_meeting_hooks must record the delivery outcome"
    assert first_claim < first_deliver < first_mark


class _StatefulMockDB:
    """AsyncSession-shaped mock with a simulated SELECT FOR UPDATE row lock."""

    def __init__(self, meeting):
        self.shared_meeting = meeting
        self._row_lock = asyncio.Lock()
        self._locked_by: object | None = None
        self.with_for_update_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0

        self.execute = AsyncMock(side_effect=self._execute)
        self.commit = AsyncMock(side_effect=self._commit)
        self.rollback = AsyncMock(side_effect=self._rollback)
        self.flush = AsyncMock()
        self.add = MagicMock()
        self.refresh = AsyncMock()
        self.close = AsyncMock()

    async def _execute(self, stmt):
        stmt_str = str(stmt)
        if "FOR UPDATE" in stmt_str.upper():
            await self._row_lock.acquire()
            self._locked_by = asyncio.current_task()
            self.with_for_update_calls += 1
            return MockResult([self.shared_meeting])

        fake_user = MagicMock()
        fake_user.email = TEST_USER_EMAIL
        return MockResult([fake_user])

    async def _commit(self):
        self.commit_calls += 1
        if self._locked_by is asyncio.current_task() and self._row_lock.locked():
            self._locked_by = None
            self._row_lock.release()

    async def _rollback(self):
        self.rollback_calls += 1
        if self._locked_by is asyncio.current_task() and self._row_lock.locked():
            self._locked_by = None
            self._row_lock.release()


def _make_meeting_for_hook(**overrides):
    now = datetime.utcnow()
    defaults = dict(start_time=now, end_time=now, data={})
    defaults.update(overrides)
    return make_meeting(**defaults)


@pytest.mark.asyncio
async def test_single_fire_records_outbound_event_and_delivers():
    meeting = _make_meeting_for_hook()
    mock_db = _StatefulMockDB(meeting)

    with patch.object(post_meeting_module, "POST_MEETING_HOOKS", ["http://hook.local/billing"]), \
         patch.object(
             post_meeting_module,
             "deliver_with_result",
             new=AsyncMock(return_value=DeliveryResult(status="delivered")),
         ) as mock_deliver:
        await post_meeting_module.fire_post_meeting_hooks(meeting, mock_db)

    events = meeting.data.get("outbound_events") or {}
    assert len(events) == 1
    event = next(iter(events.values()))
    assert event["channel"] == "post_meeting_hooks"
    assert event["event_type"] == "meeting.completed"
    assert event["status"] == "delivered"
    assert event["attempts"] == 1
    assert mock_deliver.call_count == 1


@pytest.mark.asyncio
async def test_sequential_second_call_short_circuits_on_ledger():
    meeting = _make_meeting_for_hook()
    mock_db = _StatefulMockDB(meeting)

    with patch.object(post_meeting_module, "POST_MEETING_HOOKS", ["http://hook.local/billing"]), \
         patch.object(
             post_meeting_module,
             "deliver_with_result",
             new=AsyncMock(return_value=DeliveryResult(status="delivered")),
         ) as mock_deliver:
        await post_meeting_module.fire_post_meeting_hooks(meeting, mock_db)
        await post_meeting_module.fire_post_meeting_hooks(meeting, mock_db)

    assert mock_deliver.call_count == 1
    assert len(meeting.data.get("outbound_events") or {}) == 1
    assert mock_db.rollback_calls == 1


@pytest.mark.asyncio
async def test_concurrent_callers_exactly_one_delivers():
    meeting = _make_meeting_for_hook()
    mock_db = _StatefulMockDB(meeting)

    async def slow_success(**_kwargs):
        await asyncio.sleep(0.05)
        return DeliveryResult(status="delivered")

    with patch.object(post_meeting_module, "POST_MEETING_HOOKS", ["http://hook.local/billing"]), \
         patch.object(
             post_meeting_module,
             "deliver_with_result",
             new=AsyncMock(side_effect=slow_success),
         ) as mock_deliver:
        await asyncio.gather(*[
            post_meeting_module.fire_post_meeting_hooks(meeting, mock_db)
            for _ in range(4)
        ])

    assert mock_deliver.call_count == 1
    events = meeting.data.get("outbound_events") or {}
    assert len(events) == 1
    assert next(iter(events.values()))["status"] == "delivered"


@pytest.mark.asyncio
async def test_no_hooks_configured_is_noop():
    meeting = _make_meeting_for_hook()
    mock_db = _StatefulMockDB(meeting)

    with patch.object(post_meeting_module, "POST_MEETING_HOOKS", []), \
         patch.object(post_meeting_module, "deliver_with_result", new_callable=AsyncMock) as mock_deliver:
        await post_meeting_module.fire_post_meeting_hooks(meeting, mock_db)

    assert mock_deliver.call_count == 0
    assert meeting.data == {}
    assert mock_db.with_for_update_calls == 0


@pytest.mark.asyncio
async def test_meeting_without_timestamps_is_noop():
    meeting = _make_meeting_for_hook(start_time=None, end_time=None)
    mock_db = _StatefulMockDB(meeting)

    with patch.object(post_meeting_module, "POST_MEETING_HOOKS", ["http://hook.local/billing"]), \
         patch.object(post_meeting_module, "deliver_with_result", new_callable=AsyncMock) as mock_deliver:
        await post_meeting_module.fire_post_meeting_hooks(meeting, mock_db)

    assert mock_deliver.call_count == 0
    assert meeting.data == {}
    assert mock_db.with_for_update_calls == 0
