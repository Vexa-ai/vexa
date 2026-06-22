"""Regression test for #448: storage I/O must not block the event loop.

``internal_upload_recording`` performs a synchronous boto3 ``put_object`` on
every chunk. If that call runs directly on the asyncio event loop, the blocking
S3 round-trip stalls the loop and trips the (otherwise trivial) liveness /
readiness probes, causing avoidable pod restarts under load. The fix offloads
the call via ``asyncio.to_thread``; this test asserts the upload executes on a
worker thread, not the main (event-loop) thread.

With the pre-fix code (direct call on the loop) this test fails; with the
offload it passes.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from meeting_api import recordings as recordings_module

from .conftest import make_meeting, make_session
from .test_recordings_concurrent_chunks import _StatefulMockDB, _make_upload_call


@pytest.mark.asyncio
async def test_chunk_upload_runs_off_event_loop_thread():
    meeting = make_meeting(data={})
    session = make_session()
    mock_db = _StatefulMockDB(session=session, meeting=meeting)

    main_thread = threading.current_thread()
    recorded: dict[str, object] = {}

    def _capture_upload(*args, **kwargs):
        # Synchronous boto3 stand-in: record where it executed.
        recorded["thread"] = threading.current_thread()
        try:
            asyncio.get_running_loop()
            recorded["had_running_loop"] = True
        except RuntimeError:
            recorded["had_running_loop"] = False
        return None

    fake_storage = MagicMock()
    fake_storage.upload_file = MagicMock(side_effect=_capture_upload)

    with patch.object(recordings_module, "get_storage_client", return_value=fake_storage), \
         patch.object(recordings_module.attributes, "flag_modified", new=MagicMock()):
        await recordings_module.internal_upload_recording(
            db=mock_db,
            **_make_upload_call("audio", "wav"),
        )

    assert fake_storage.upload_file.called, "storage.upload_file was never invoked"
    assert recorded.get("thread") is not None
    assert recorded["thread"] is not main_thread, (
        "storage.upload_file ran on the event-loop (main) thread — the blocking "
        "boto3 call must be offloaded via asyncio.to_thread (see #448)."
    )
    assert recorded["had_running_loop"] is False, (
        "storage.upload_file ran with a live event loop in its thread — it must "
        "execute in a worker thread, not on the loop."
    )
