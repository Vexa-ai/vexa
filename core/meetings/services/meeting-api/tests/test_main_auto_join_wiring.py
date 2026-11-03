"""F154, carried over to the start route (ADR-0052 s2d) — `publish_status` must be a real callable.

The auto-join sweep's `_tick` once passed `publish_status=publish_status` while the function it named
had been deleted (commit 1699aa3ac), so every sweep raised NameError and calendar auto-join was
silently dead. The sweep is gone (s2d); the same callable now reaches the planned-row start route
through `__main__._planned_start_deps`, which Flows' booked_bot calls for every booked meeting. This
drives that REAL builder — not a reimplementation — and proves its `publish_status` publishes a
`meeting.status` frame on the owner's channel, best-effort, exactly as the sweep's closure did.

It also pins that the sweep is gone: no `_auto_join_loop`, no `auto_join` module, so nothing but the
route can start a booked meeting's bot.
"""
from __future__ import annotations

import importlib.util
import json

import meeting_api.__main__ as main_mod


class _FakeRedis:
    def __init__(self, fail: bool = False):
        self.published: list[tuple[str, str]] = []
        self.fail = fail

    async def publish(self, channel, message):
        if self.fail:
            raise RuntimeError("redis down")
        self.published.append((channel, message))


async def test_the_start_routes_publish_status_is_wired_and_publishes():
    redis_client = _FakeRedis()
    deps = main_mod._planned_start_deps(redis_client, None)
    assert callable(deps.publish_status)

    await deps.publish_status(
        user_id=42, meeting_id="m-1", native_id="n-1", status="joining", when="2026-09-03T08:52:00Z",
    )

    assert len(redis_client.published) == 1
    channel, message = redis_client.published[0]
    assert channel == "u:42:meetings"
    assert json.loads(message) == {
        "type": "meeting.status",
        "meeting_id": "m-1",
        "native": "n-1",
        "status": "joining",
        "when": "2026-09-03T08:52:00Z",
    }
    # best-effort: a failed publish never fails the start
    await main_mod._planned_start_deps(_FakeRedis(fail=True), None).publish_status(
        user_id=1, meeting_id=1, native_id="x", status="scheduled", when=None)


def test_there_is_no_auto_join_sweep_left():
    """ADR-0052 §3: one clock. Flows' booked_bot is the only thing that starts a booked bot."""
    assert importlib.util.find_spec("meeting_api.bot_spawn.auto_join") is None
    source = open(main_mod.__file__).read()
    assert "_auto_join_loop" not in source and "auto_join_tick" not in source
