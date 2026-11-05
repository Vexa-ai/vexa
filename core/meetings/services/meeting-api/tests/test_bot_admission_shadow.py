"""Bot limits, shadow only: the prediction arithmetic, and proof a spawn is never touched.

OFFLINE, over the in-memory fakes, except the page-statement checks, which compile the real SQL.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.admission import Interval, Prediction, admission_mode, build_observer, decide, predict
from meeting_api.admission.predict import peak_by_hour, raised_floor, with_headroom
from meeting_api.admission.router import build_reading_router
from meeting_api.admission.shadow import COMPUTE_KEY, COUNTS_KEY, OUTSIDE_KEY, PREDICTION_KEY, FailOnce, ShadowObserver
from meeting_api.admission.sweep import compute, daily_tick, history_page_stmt, interval_of
from meeting_api.bot_spawn import MaxBotsExceeded, request_bot
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

KW = dict(redis_url="r", token_secret="s", meeting_api_url="http://meeting-api:8080")
USER = 7
MON = datetime(2026, 11, 2)  # a Monday: hour of the week 0


def at(day=0, hour=0, minute=0):
    return MON + timedelta(days=day, hours=hour, minutes=minute)


def booked(start, end):
    return Interval(start=start, end=end, booked=True)


def adhoc(start, end):
    return Interval(start=start, end=end, booked=False)


class FakeRedis:
    def __init__(self):
        self.kv, self.hashes, self.ttl = {}, {}, {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        self.ttl[key] = ex
        return True

    async def delete(self, key):
        self.kv.pop(key, None)

    async def hincrby(self, key, field, n):
        self.hashes.setdefault(key, {})
        self.hashes[key][field] = self.hashes[key].get(field, 0) + n

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def expire(self, key, seconds):
        self.ttl[key] = seconds


class Untouchable:
    """Any attribute read fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"touched .{name} while off")


class RedisDown:
    def __getattr__(self, name):
        async def _fail(*a, **k):
            raise ConnectionError("redis is down")
        return _fail


class TouchlessRepo(InMemoryMeetingRepo):
    async def count_active_bots_by_kind(self, **_):
        raise AssertionError("the shadow query ran while off")


class SlowRepo(InMemoryMeetingRepo):
    async def count_active_bots_by_kind(self, **_):
        await asyncio.sleep(30)


class FailingRepo(InMemoryMeetingRepo):
    async def count_active_bots_by_kind(self, **_):
        raise RuntimeError("database is down")


# ── predict ───────────────────────────────────────────────────────────────────────────────────────

def test_empty_history_gives_two_and_two():
    p = predict([])
    assert (p.scheduled.limit, p.adhoc.limit) == (2, 2)
    assert p.scheduled.at(5) == 2


def test_peak_is_per_hour_of_week_and_across_weeks():
    # Two bots on Monday 10:00 of week one, three on Monday 10:00 of week two: same hour of the week.
    week_two = timedelta(days=7)
    intervals = [adhoc(at(hour=10), at(hour=10, minute=30)) for _ in range(2)]
    intervals += [adhoc(at(hour=10) + week_two, at(hour=10, minute=30) + week_two) for _ in range(3)]
    intervals += [adhoc(at(day=1, hour=9), at(day=1, hour=9, minute=30))]  # Tuesday 09:00, alone
    assert peak_by_hour(intervals) == {10: 3, 33: 1}
    p = predict(intervals)
    assert p.adhoc.limit == 5  # 3 + 50%, up
    assert p.adhoc.at(10) == 5 and p.adhoc.at(33) == 2 and p.adhoc.at(100) == 2


@pytest.mark.parametrize("peak,limit", [(0, 2), (1, 2), (2, 3), (3, 5), (4, 6), (5, 8), (7, 11)])
def test_fifty_percent_rounds_up(peak, limit):
    assert with_headroom(peak) == limit


def test_raised_floor_holds_only_above_the_plan_default():
    many = [adhoc(at(hour=h), at(hour=h, minute=5)) for h in range(3)]
    assert predict(many, cap=20).adhoc.limit == 20
    assert predict(many, cap=20).scheduled.limit == 20
    assert predict(many, cap=5).adhoc.limit == 2
    assert raised_floor(None) == 0 and raised_floor(5) == 0 and raised_floor(6) == 6


def test_floor_does_not_cap_a_higher_peak():
    crowd = [adhoc(at(hour=1), at(hour=2)) for _ in range(10)]
    assert predict(crowd, cap=6).adhoc.limit == 15


def test_overlap_counts_in_every_hour_it_crosses():
    # A runs 10:30-12:30, B 11:45-12:15. They overlap in 11:00 and 12:00, not in 10:00.
    long = adhoc(at(hour=10, minute=30), at(hour=12, minute=30))
    short = adhoc(at(hour=11, minute=45), at(hour=12, minute=15))
    assert peak_by_hour([long, short]) == {10: 1, 11: 2, 12: 2}


def test_back_to_back_is_not_overlap_and_zero_length_still_counts():
    first = adhoc(at(hour=10), at(hour=11))
    second = adhoc(at(hour=11), at(hour=12))
    assert peak_by_hour([first, second]) == {10: 1, 11: 1}
    assert peak_by_hour([adhoc(at(hour=3), at(hour=3))]) == {3: 1}


def test_week_wraps_from_sunday_to_monday():
    assert peak_by_hour([adhoc(at(day=6, hour=23, minute=30), at(day=7, hour=0, minute=30))]) == {167: 1, 0: 1}


def test_booked_and_adhoc_are_separate():
    p = predict(
        [booked(at(hour=9), at(hour=10)) for _ in range(4)] + [adhoc(at(hour=9), at(hour=10))],
    )
    assert p.scheduled.limit == 6 and p.adhoc.limit == 2


def test_prediction_survives_json():
    p = predict([booked(at(hour=9), at(hour=10)) for _ in range(4)])
    assert Prediction.from_json(json.loads(json.dumps(p.to_json()))) == p


# ── decide ────────────────────────────────────────────────────────────────────────────────────────

def test_decide_allows_under_the_limit_and_queues_at_it():
    p = predict([adhoc(at(hour=9), at(hour=10)) for _ in range(2)])  # limit 3
    assert decide(p, kind="adhoc", active=2, cap=None, at=at(hour=9)).would == "allow"
    over = decide(p, kind="adhoc", active=3, cap=None, at=at(hour=9))
    assert (over.limit, over.would) == (3, "queue_over_limit")


def test_within_prediction_is_about_this_hour_of_the_week():
    p = predict([adhoc(at(hour=9), at(hour=10)) for _ in range(4)])  # 6 at Monday 09:00, 2 elsewhere
    usual = decide(p, kind="adhoc", active=3, cap=None, at=at(hour=9))
    unusual = decide(p, kind="adhoc", active=3, cap=None, at=at(hour=15))
    assert usual.within_prediction and usual.would == "allow"
    assert not unusual.within_prediction and unusual.would == "allow"


def test_decide_applies_the_identity_cap_as_a_floor():
    decision = decide(Prediction(), kind="adhoc", active=7, cap=10, at=at())
    assert (decision.limit, decision.would, decision.within_prediction) == (10, "allow", True)


# ── the flag ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "", "off", "enforce", "SHADOW", "on", "1"])
def test_anything_but_shadow_is_off(value):
    env = {} if value is None else {"BOT_ADMISSION_MODE": value}
    assert admission_mode(env) == "off"
    assert build_observer(Untouchable(), Untouchable(), env=env) is None


def test_shadow_builds_an_observer():
    assert isinstance(build_observer(object(), object(), env={"BOT_ADMISSION_MODE": "shadow"}), ShadowObserver)


# ── the spawn is untouched ────────────────────────────────────────────────────────────────────────

async def _spawn(repo, runtime, *, observer=None, cap=None, nid="m-1", **extra):
    return await request_bot(
        repo, runtime, user_id=USER, platform="google_meet", native_meeting_id=nid,
        max_concurrent=cap, admission_observer=observer, **extra, **KW,
    )


def _comparable(meeting, runtime):
    """The answer, less what differs between any two spawns (run ids, tokens, timestamps)."""
    per_run = {"created_at", "updated_at", "bot_container_id", "data"}
    data_per_run = {"service_authority", "meet_respawn", "sessions"}
    data = {k: (k if k in data_per_run else v) for k, v in meeting["data"].items()}
    return {k: v for k, v in meeting.items() if k not in per_run}, data, len(runtime.specs)


async def test_off_makes_no_redis_call_and_no_query():
    repo, runtime = TouchlessRepo(), FakeRuntimeClient()
    observer = build_observer(repo, Untouchable(), env={"BOT_ADMISSION_MODE": "off"})
    meeting = await _spawn(repo, runtime, observer=observer)
    assert meeting["status"] == "requested" and len(runtime.specs) == 1


async def test_default_call_has_no_observer():
    repo, runtime = TouchlessRepo(), FakeRuntimeClient()
    await request_bot(repo, runtime, user_id=USER, platform="google_meet", native_meeting_id="m-1", **KW)
    assert len(runtime.specs) == 1


@pytest.mark.parametrize("make_repo,redis", [
    (InMemoryMeetingRepo, RedisDown()),
    (SlowRepo, FakeRedis()),
    (FailingRepo, FakeRedis()),
])
async def test_shadow_with_a_failing_dependency_returns_the_same_spawn(make_repo, redis):
    baseline_runtime = FakeRuntimeClient()
    baseline = await _spawn(InMemoryMeetingRepo(), baseline_runtime)

    repo, runtime = make_repo(), FakeRuntimeClient()
    observer = ShadowObserver(repo, redis, timeout_s=0.02)
    meeting = await asyncio.wait_for(_spawn(repo, runtime, observer=observer), 5)

    assert _comparable(meeting, runtime) == _comparable(baseline, baseline_runtime)


async def test_shadow_does_not_change_the_cap_guard():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    observer = ShadowObserver(repo, FakeRedis())
    first = await _spawn(repo, runtime, observer=observer, cap=1)
    repo.set_status(first["id"], "active")
    with pytest.raises(MaxBotsExceeded):
        await _spawn(repo, runtime, observer=observer, cap=1, nid="m-2")


async def test_shadow_logs_the_event_and_counts(capsys):
    repo, runtime, redis = InMemoryMeetingRepo(), FakeRuntimeClient(), FakeRedis()
    observer = ShadowObserver(repo, redis)
    first = await _spawn(repo, runtime, observer=observer, nid="m-1")
    repo.set_status(first["id"], "active")
    second = await _spawn(repo, runtime, observer=observer, nid="m-2")
    repo.set_status(second["id"], "active")
    await _spawn(repo, runtime, observer=observer, nid="m-3")

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if "bot_admission_shadow" in line]
    fields = [e["fields"] for e in events]
    assert [f["active"] for f in fields] == [0, 1, 2]
    assert fields[-1] == {
        "user_id": USER, "kind": "adhoc", "active": 2, "limit": 2,
        "would": "queue_over_limit", "within_prediction": False,
    }
    assert redis.hashes[COUNTS_KEY] == {"would_allow": 2, "would_queue": 1}


async def test_auto_join_kind_is_logged_as_scheduled(capsys):
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    observer = ShadowObserver(repo, FakeRedis())
    await _spawn(repo, runtime, observer=observer, admission_kind="scheduled")
    out = capsys.readouterr().out
    event = next(json.loads(line) for line in out.splitlines() if "bot_admission_shadow" in line)
    assert event["fields"]["kind"] == "scheduled"


async def test_active_is_counted_per_kind():
    repo = InMemoryMeetingRepo()
    runtime = FakeRuntimeClient()
    plain = await _spawn(repo, runtime, nid="m-1")
    repo.set_status(plain["id"], "active")
    planned = await _spawn(repo, runtime, nid="m-2")
    await repo.merge_meeting_data(planned["id"], {"scheduled_at": "2026-11-05T10:00:00Z"})
    repo.set_status(planned["id"], "active")
    done = await _spawn(repo, runtime, nid="m-3")
    repo.set_status(done["id"], "completed")

    assert await repo.count_active_bots_by_kind(user_id=USER) == {"scheduled": 1, "adhoc": 1}
    assert await repo.count_active_bots_by_kind(user_id=USER, exclude_meeting_id=plain["id"]) == {
        "scheduled": 1, "adhoc": 0,
    }


# ── once per 7 days ───────────────────────────────────────────────────────────────────────────────

async def test_outside_prediction_is_logged_once_per_seven_days(capsys):
    repo, redis = InMemoryMeetingRepo(), FakeRedis()
    observer = ShadowObserver(repo, redis)
    repo.count_active_bots_by_kind = _active(5)
    for _ in range(3):
        await observer.observe(user_id=USER, kind="adhoc", cap=None)

    out = capsys.readouterr().out
    assert sum("bot_admission_outside_prediction" in line for line in out.splitlines()) == 1
    assert sum('"event":"bot_admission_shadow"' in line for line in out.splitlines()) == 3
    assert redis.ttl[OUTSIDE_KEY.format(USER)] == 7 * 24 * 3600

    # Seven days later (the key expired), it may say so again. Another account is on its own clock.
    del redis.kv[OUTSIDE_KEY.format(USER)]
    await observer.observe(user_id=USER, kind="adhoc", cap=None)
    await observer.observe(user_id=USER + 1, kind="adhoc", cap=None)
    out = capsys.readouterr().out
    assert sum("bot_admission_outside_prediction" in line for line in out.splitlines()) == 2


async def test_inside_prediction_sets_no_marker():
    repo, redis = InMemoryMeetingRepo(), FakeRedis()
    await ShadowObserver(repo, redis).observe(user_id=USER, kind="adhoc", cap=None)
    assert OUTSIDE_KEY.format(USER) not in redis.kv


def _active(n):
    async def count(**_):
        return {"scheduled": 0, "adhoc": n}
    return count


async def test_a_failure_logs_once_until_it_recovers(caplog):
    observer = ShadowObserver(FailingRepo(), RedisDown())
    with caplog.at_level("WARNING"):
        for _ in range(3):
            await observer.observe(user_id=USER, kind="adhoc", cap=None)
    assert sum("bot admission" in r.message for r in caplog.records) == 1
    fail = FailOnce()
    fail.failed("x", ValueError())
    fail.worked()
    assert fail._down is False


# ── the daily job ─────────────────────────────────────────────────────────────────────────────────

class FakeHistory:
    def __init__(self, pages=(), error=None):
        self._pages, self._error = pages, error
        self.runs = 0

    async def pages(self, now):
        self.runs += 1
        if self._error:
            raise self._error
        for page in self._pages:
            yield page


async def test_compute_stores_each_account_with_a_ttl_and_the_reading_reports_it():
    page = [(USER, adhoc(at(hour=9), at(hour=10))) for _ in range(4)] + [(8, booked(at(hour=1), at(hour=2)))]
    redis = FakeRedis()
    summary = await compute(FakeHistory([page]), redis, now=at(day=3))

    assert summary["accounts"] == 2 and summary["rows"] == 5
    stored = Prediction.from_json(json.loads(redis.kv[PREDICTION_KEY.format(USER)]))
    assert stored.adhoc.limit == 6
    assert redis.ttl[PREDICTION_KEY.format(USER)] == 3 * 24 * 3600

    observer = ShadowObserver(InMemoryMeetingRepo(), redis)
    observer_reading = await observer.reading()
    assert observer_reading["accounts_predicted"] == 2
    assert observer_reading["last_compute_at"] == at(day=3).isoformat() + "Z"


async def test_a_failed_read_writes_nothing_and_logs_once(caplog):
    redis = FakeRedis()
    fail = FailOnce()
    history = FakeHistory(error=TimeoutError())
    with caplog.at_level("WARNING"):
        assert await daily_tick(history, redis, fail) is None
        assert await daily_tick(history, redis, fail) is None
    assert sum("bot admission" in r.message for r in caplog.records) == 1
    assert history.runs == 2  # the failed run gave its claim back
    assert not [k for k in redis.kv if "prediction" in k or k == COMPUTE_KEY]


async def test_redis_down_does_not_escape_the_tick():
    history = FakeHistory()
    assert await daily_tick(history, RedisDown(), FailOnce()) is None
    assert history.runs == 0


async def test_one_run_a_day():
    redis, history = FakeRedis(), FakeHistory()
    fail = FailOnce()
    assert await daily_tick(history, redis, fail) is not None
    assert await daily_tick(history, redis, fail) is None
    assert history.runs == 1


def _sql(stmt):
    from sqlalchemy.dialects import postgresql
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_the_page_reads_only_timestamps_user_and_booked():
    sql = _sql(history_page_stmt(
        created_from=at(day=-28), created_until=at(), started_from=at(day=-14),
        after=(at(day=-20), 12), limit=1000,
    ))
    selected = sql.split("FROM", 1)[0]
    assert "meetings.data ->> 'scheduled_at'" in selected or "(meetings.data ->> 'scheduled_at')" in selected
    assert selected.count("meetings.data") == 1
    for column in ("platform_specific_id", "bot_container_id", "meetings.status"):
        assert column not in selected
    assert "meetings.created_at >=" in sql and "ORDER BY meetings.created_at, meetings.id" in sql
    assert "LIMIT 1000" in sql


def test_interval_of_a_row_with_no_end():
    row = SimpleNamespace(start_time=at(hour=1), end_time=None, updated_at=at(day=5), booked=False)
    assert interval_of(row, at(day=9)).end == at(hour=7)  # a later edit doesn't stretch it past 6 h
    row = SimpleNamespace(start_time=at(hour=1), end_time=None, updated_at=at(hour=2), booked=True)
    got = interval_of(row, at(day=9))
    assert got.end == at(hour=2) and got.booked is True
    row = SimpleNamespace(start_time=at(hour=1), end_time=at(hour=3), updated_at=at(hour=2), booked=None)
    assert interval_of(row, at(day=9)) == Interval(at(hour=1), at(hour=3), False)


# ── the reading ───────────────────────────────────────────────────────────────────────────────────

def test_reading_is_internal_only():
    redis = FakeRedis()
    redis.hashes[COUNTS_KEY] = {"would_allow": "3", "would_queue": "1"}
    redis.kv[COMPUTE_KEY] = json.dumps({"accounts": 12, "rows": 40, "at": "2026-11-05T00:00:00Z", "duration_s": 1.5})
    app = FastAPI()
    app.include_router(build_reading_router(ShadowObserver(object(), redis), internal_secret="sekret"))
    client = TestClient(app)

    assert client.get("/internal/bot-admission/reading").status_code == 403
    assert client.get("/internal/bot-admission/reading", headers={"x-internal-secret": "no"}).status_code == 403
    response = client.get("/internal/bot-admission/reading", headers={"x-internal-secret": "sekret"})
    assert response.status_code == 200
    assert response.json() == {
        "mode": "shadow", "would_allow": 3, "would_queue": 1, "accounts_predicted": 12,
        "last_compute_at": "2026-11-05T00:00:00Z", "last_compute_duration_s": 1.5,
    }


def test_reading_refuses_with_no_secret_configured():
    app = FastAPI()
    app.include_router(build_reading_router(ShadowObserver(object(), FakeRedis()), internal_secret=""))
    response = TestClient(app).get("/internal/bot-admission/reading", headers={"x-internal-secret": "x"})
    assert response.status_code == 403
