"""F154 — the auto-join sweep's `_tick` closure passed `publish_status=publish_status` into
`auto_join_tick(...)` while the `publish_status` function it referenced had been deleted by
commit 1699aa3ac ("bot_name: no fourth store") — the commit correctly folded the two
`fetch_bot_context` builders into one `_bot_context_fetcher()` helper but dropped
`publish_status` entirely even though `_tick()` still names it. On the running dogfood stack
every `_auto_join_loop` sweep raised `NameError: name 'publish_status' is not defined` inside
`_tick()`, so calendar auto-join has been silently dead since the commit landed.

This test drives the REAL `_attach_background_loops` wiring in `meeting_api.__main__` — not a
reimplementation of it — so it fails with the NameError on the buggy tip and passes once
`publish_status` is restored. It stubs `auto_join_tick` to capture the kwargs `_tick()` builds
(proving `publish_status` is a real callable, not merely present) and then invokes the captured
`publish_status` to prove it publishes a `meeting.status` frame to redis exactly as main's
pre-regression closure did.

Every OTHER background loop `_attach_background_loops` starts (segment-consumer, db-writer,
webhook-drain, stop-reconcile, service-authority, calendar-sync, signal-tape-janitor) is left
running for real against bare fakes — cheapest way to reach the auto-join tick is through the
actual `lifespan` context manager, and a patched `asyncio.sleep` that raises after being awaited
once ends every loop (including auto-join) after exactly one tick, whether that tick fails inside
its own try/except or returns cleanly.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import meeting_api.__main__ as main_mod
import meeting_api.bot_spawn.auto_join as auto_join_mod


class _StopLoop(Exception):
    """Sentinel raised by the patched asyncio.sleep so every background loop's `while True` ends
    after exactly one tick, instead of running forever."""


class _FakeMeetingRepo:
    # Only needs to exist + carry the attribute `_auto_join_loop` gates on; auto_join_tick itself
    # is stubbed below, so no real repo behaviour is exercised.
    def list_scheduled_meetings(self):  # pragma: no cover - never actually called (tick is stubbed)
        return []


class _FakeRuntime:
    pass


class _FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel, message):
        self.published.append((channel, message))


def _fake_app():
    app = types.SimpleNamespace()
    app.state = types.SimpleNamespace()
    app.router = types.SimpleNamespace()
    return app


async def test_auto_join_tick_publish_status_is_wired_and_publishes(monkeypatch):
    captured_kwargs: dict = {}
    logged_exceptions: list[BaseException] = []

    async def _stub_auto_join_tick(*args, **kwargs):
        # Evaluating this call's keyword arguments (in the REAL `_tick()` closure) is exactly
        # where the regression raised NameError on `publish_status=publish_status` — reaching
        # this stub body at all is already proof the closure built cleanly.
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(auto_join_mod, "auto_join_tick", _stub_auto_join_tick)

    # `_auto_join_loop`'s while-loop swallows any tick exception via a bare
    # `except Exception: log.exception(...)`. Capture what `log.exception` was called with
    # directly (rather than fighting pytest's log-capture plugin across the asyncio.create_task
    # boundary) so a red run names the real defect instead of just its symptom.
    def _record_exception(msg, *args, **kwargs):
        exc = sys.exc_info()[1]
        if exc is not None:
            logged_exceptions.append(exc)

    monkeypatch.setattr(main_mod.log, "exception", _record_exception)

    real_sleep = asyncio.sleep  # captured before the patch below — used to yield the event loop

    async def _sleep_once_then_stop(delay, *a, **kw):
        raise _StopLoop()

    monkeypatch.setattr(asyncio, "sleep", _sleep_once_then_stop)

    app = _fake_app()
    redis_client = _FakeRedis()

    main_mod._attach_background_loops(
        app,
        transcript_store=types.SimpleNamespace(),
        segment_bus=types.SimpleNamespace(),
        redis_client=redis_client,
        meeting_repo=_FakeMeetingRepo(),
        runtime=_FakeRuntime(),
        service_authority=None,
        system_webhook_sink=None,
        session_factory=None,  # _guarded degrades to run-the-tick unconditionally
        storage=None,
    )

    async with app.router.lifespan_context(app):
        # `asyncio.create_task` only SCHEDULES the loops — they don't run a single line until we
        # yield control. `real_sleep` (captured before the patch, called directly so it bypasses
        # the patched `asyncio.sleep` module attribute) gives every loop's first tick + its
        # patched-sleep-raises-_StopLoop teardown time to actually run before we exit the
        # context. `lifespan`'s own `finally` then cancels + gathers with return_exceptions=True,
        # so those per-loop failures never surface here.
        await real_sleep(0.05)

    if "publish_status" not in captured_kwargs:
        details = "\n".join(repr(e) for e in logged_exceptions) or (
            "(nothing logged either — _tick() may not have run at all)"
        )
        raise AssertionError(
            "auto_join_tick was never called with publish_status — _tick() raised before "
            f"reaching it:\n{details}"
        )
    publish_status = captured_kwargs["publish_status"]
    assert callable(publish_status)

    # Prove it behaves exactly like main's pre-regression closure: publishes a meeting.status
    # frame on the user's channel, best-effort (never raises even if redis_client.publish does).
    await publish_status(
        user_id=42, meeting_id="m-1", native_id="n-1", status="joining", when="2026-09-03T08:52:00Z",
    )

    assert len(redis_client.published) == 1
    channel, message = redis_client.published[0]
    assert channel == "u:42:meetings"
    frame = json.loads(message)
    assert frame == {
        "type": "meeting.status",
        "meeting_id": "m-1",
        "native": "n-1",
        "status": "joining",
        "when": "2026-09-03T08:52:00Z",
    }
