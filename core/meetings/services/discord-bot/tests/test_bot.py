"""bot.py's two seams that are plain asyncio/dict logic with no discord.py dependency (the rest of
bot.py needs a live Discord gateway, per its own module docstring, and is not covered here):

  * _on_connect_task_done: without this, a dead/raising gateway task leaves run() waiting on
    stop_event forever (the bot can never be torn down).
  * failed_event(): the shape bot.py emits directly when client.login() fails before a
    MeetingSession exists, so a login failure still reports a lifecycle.v1 'failed' event instead
    of propagating with no report at all.
"""

import asyncio

from discord_bot.bot import _on_connect_task_done
from discord_bot.contracts import conforms_lifecycle_event
from discord_bot.session import failed_event


async def test_on_connect_task_done_sets_stop_event_on_exception():
    async def boom():
        raise RuntimeError("gateway dropped")

    task = asyncio.create_task(boom())
    stop_event = asyncio.Event()
    logs = []
    await asyncio.sleep(0)  # let the task run to completion
    _on_connect_task_done(task, stop_event=stop_event, log=logs.append)

    assert stop_event.is_set()
    assert any("gateway dropped" in m for m in logs)


async def test_on_connect_task_done_sets_stop_event_on_clean_return():
    async def finish():
        return None

    task = asyncio.create_task(finish())
    stop_event = asyncio.Event()
    logs = []
    await asyncio.sleep(0)
    _on_connect_task_done(task, stop_event=stop_event, log=logs.append)

    assert stop_event.is_set()
    assert logs == []  # no exception, nothing to log


async def test_on_connect_task_done_is_a_noop_on_cancellation():
    async def hang():
        await asyncio.sleep(10)

    task = asyncio.create_task(hang())
    stop_event = asyncio.Event()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _on_connect_task_done(task, stop_event=stop_event, log=lambda m: None)

    assert not stop_event.is_set()  # a deliberate shutdown-path cancel, not a dropped gateway


def test_failed_event_conforms_and_carries_login_failure_shape():
    event = failed_event("sess-uid-1", failure_stage="requested", completion_reason="join_failure", reason="401 Unauthorized")
    assert event["connection_id"] == "sess-uid-1"
    assert event["status"] == "failed"
    assert event["failure_stage"] == "requested"
    assert event["completion_reason"] == "join_failure"
    assert event["reason"] == "401 Unauthorized"
    conforms_lifecycle_event(event)
