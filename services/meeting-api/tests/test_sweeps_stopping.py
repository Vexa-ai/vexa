"""Regression tests for stale stopping lifecycle sweep."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from meeting_api.schemas import MeetingCompletionReason, MeetingStatus
from meeting_api.sweeps import _sweep_stale_stopping

from .conftest import MockResult, make_meeting


class _DbContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_stale_stopping_uses_status_progress_not_updated_at(mock_db):
    """Webhook retry writes can refresh updated_at without real lifecycle progress."""
    now = datetime.utcnow()
    stale_transition_at = now - timedelta(minutes=20)
    meeting = make_meeting(
        status=MeetingStatus.STOPPING.value,
        created_at=stale_transition_at,
        updated_at=now,
        data={
            "status_transition": [
                {
                    "from": MeetingStatus.ACTIVE.value,
                    "to": MeetingStatus.STOPPING.value,
                    "timestamp": stale_transition_at.isoformat(),
                }
            ]
        },
    )
    mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

    with patch(
        "meeting_api.callbacks._classify_stopped_exit",
        new_callable=AsyncMock,
        return_value=(MeetingStatus.COMPLETED, MeetingCompletionReason.STOPPED),
    ), patch(
        "meeting_api.meetings.update_meeting_status",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_update, patch("meeting_api.meetings.get_redis", return_value=None):
        swept = await _sweep_stale_stopping(lambda: _DbContext(mock_db))

    assert swept == 1
    mock_update.assert_awaited_once()
    kwargs = mock_update.await_args.kwargs
    assert kwargs["transition_reason"] == "stale_stopping_sweep"
    assert kwargs["transition_metadata"]["stuck_for_seconds"] >= 300
