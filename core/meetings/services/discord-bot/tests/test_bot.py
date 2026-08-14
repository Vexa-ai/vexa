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


# ---- _non_bot_member_count: the left_alone roster source ------------------------------------
# Live-witness regression (2026-08-14): channel.members reads the guild member cache, which is
# empty without the privileged members intent, so the count was 0 with two humans mid-call and
# left_alone fired. The count must come from channel.voice_states (intent-independent).


class _FakeGuild:
    def __init__(self, cached_members):
        self._cached = cached_members

    def get_member(self, uid):
        return self._cached.get(uid)


class _FakeMember:
    def __init__(self, bot):
        self.bot = bot


class _FakeChannel:
    """The exact shape of the live failure: member cache empty, voice states populated."""

    def __init__(self, voice_state_ids, cached_members=None):
        self.members = []  # empty member cache: reading this is the bug
        self.voice_states = {uid: object() for uid in voice_state_ids}
        self.guild = _FakeGuild(cached_members or {})


def test_count_uses_voice_states_not_the_empty_member_cache():
    from discord_bot.bot import _non_bot_member_count

    # bot id 99 plus two humans in voice, member cache empty (no members intent)
    channel = _FakeChannel(voice_state_ids=[99, 1, 2])
    assert _non_bot_member_count(channel, own_id=99) == 2


def test_count_excludes_only_ourselves_when_cache_is_empty():
    from discord_bot.bot import _non_bot_member_count

    channel = _FakeChannel(voice_state_ids=[99])
    assert _non_bot_member_count(channel, own_id=99) == 0


def test_count_excludes_a_cached_bot_member():
    from discord_bot.bot import _non_bot_member_count

    # another bot (uid 50) IS in the member cache and flagged bot: excluded. The uncached
    # human (uid 1) still counts.
    channel = _FakeChannel(voice_state_ids=[99, 50, 1], cached_members={50: _FakeMember(bot=True)})
    assert _non_bot_member_count(channel, own_id=99) == 1


def test_uncached_occupant_counts_as_human():
    from discord_bot.bot import _non_bot_member_count

    # safe default: an occupant we cannot classify keeps the bot in the call
    channel = _FakeChannel(voice_state_ids=[99, 7])
    assert _non_bot_member_count(channel, own_id=99) == 1


# ---- _teardown ordering (the redis-race and lost-audio-window fixes) --------------------------


class _Recorder:
    """Records the order of every teardown-relevant call."""

    def __init__(self):
        self.calls = []

    def make(self, name, result=None):
        async def _f(*a, **k):
            self.calls.append(name)
            return result

        return _f


async def test_teardown_order_is_stop_capture_then_drain_then_close():
    from discord_bot.bot import _teardown

    rec = _Recorder()

    class _Dave:
        stop = rec.make("dave.stop")

    class _Session:
        stop_reason = "left_alone"
        flush_all = rec.make("flush_all")

        async def emit_completed(self, reason):
            rec.calls.append(f"emit_completed:{reason}")

    class _Voice:
        async def disconnect(self, force=False):
            rec.calls.append("voice.disconnect")

    class _Client:
        close = rec.make("client.close")

    class _Redis:
        aclose = rec.make("redis.aclose")

    async def _sleeper():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            rec.calls.append("task.cancelled")
            raise

    tasks = [asyncio.create_task(_sleeper())]
    await asyncio.sleep(0)  # let the sleeper start

    await _teardown(
        session=_Session(), dave_client=_Dave(), voice_protocol=_Voice(), client=_Client(),
        connect_task=None, background_tasks=tasks, redis_client=_Redis(),
    )

    # capture stops BEFORE the final drain (audio arriving mid-teardown is flushed, not lost)
    assert rec.calls.index("dave.stop") < rec.calls.index("flush_all")
    # last lifecycle event postdates the last segment flush
    assert rec.calls.index("flush_all") < rec.calls.index("emit_completed:left_alone")
    # poll loops are dead BEFORE redis closes (the live-witness ConnectionError race)
    assert rec.calls.index("task.cancelled") < rec.calls.index("redis.aclose")
    assert rec.calls[-1] == "redis.aclose"


async def test_teardown_tolerates_absent_components():
    from discord_bot.bot import _teardown

    rec = _Recorder()

    class _Client:
        close = rec.make("client.close")

    class _Redis:
        aclose = rec.make("redis.aclose")

    await _teardown(
        session=None, dave_client=None, voice_protocol=None, client=_Client(),
        connect_task=None, background_tasks=[], redis_client=_Redis(),
    )
    assert rec.calls == ["client.close", "redis.aclose"]
