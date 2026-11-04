"""The one per-account limits model (meeting_api/limits.py): keys, defaults, the one parser of the
bot-limits.v1 object (the one carrier of an account's limits, from identity's bot-context), and the
start-rate bucket. Plan defaults and per-account overrides are resolved by admin-api; this module
only reads what crosses that seam. The contract's ``BotLimits`` goldens are read here too."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from meeting_api.limits import (
    DEFAULT_BOOKING_RATE,
    DEFAULT_BOT_START_RATE,
    AccountLimits,
    LimitsUnavailable,
    TokenRate,
    resolve,
)


def _v1(cap=3, **limits):
    return {"version": 1, "max_concurrent_bots": cap, **limits}


def _goldens():
    for parent in Path(__file__).resolve().parents:
        d = parent / "identity" / "contracts" / "bot-limits.v1" / "golden"
        if d.is_dir():
            return sorted(d.glob("BotLimits.*.json"))
    raise AssertionError("bot-limits.v1 goldens not found")


def test_every_limit_has_one_key_and_one_default():
    limits = AccountLimits()
    assert limits.max_concurrent_bots is None
    assert limits.max_scheduled_bots == 5
    assert limits.bot_start_rate == TokenRate(burst=10, per_minute=0.5)
    assert limits.seat_cooldown_s == 60
    assert limits.queue_wait_s == 1800, "the founder's 30-minute queue wait"
    assert limits.booking_rate == TokenRate(burst=200, per_minute=10 / 60)


@pytest.mark.parametrize("path", _goldens(), ids=lambda p: p.name)
def test_every_contract_golden_is_read(path):
    body = json.loads(path.read_text())
    limits = resolve(body)
    assert limits.max_concurrent_bots == body["max_concurrent_bots"]
    if "seat_cooldown_s" in body:
        assert limits.seat_cooldown_s == body["seat_cooldown_s"]


def test_the_resolved_object_sets_each_limit():
    limits = resolve(_v1(4, max_scheduled_bots=12, bot_start_rate={"burst": 20, "per_minute": 1},
                         seat_cooldown_s=300, queue_wait_s=600,
                         booking_rate={"burst": 50, "per_minute": 1}))
    assert limits == AccountLimits(max_concurrent_bots=4, max_scheduled_bots=12,
                                   bot_start_rate=TokenRate(20, 1.0), seat_cooldown_s=300,
                                   queue_wait_s=600, booking_rate=TokenRate(50, 1.0))


def test_absent_keys_are_the_defaults_and_unknown_keys_are_carried_not_read():
    assert resolve(_v1(3)) == AccountLimits(max_concurrent_bots=3)
    assert resolve(_v1(3, booked_max_per_account=500)) == AccountLimits(max_concurrent_bots=3)


@pytest.mark.parametrize("answer", [None, {}, "x", {"max_concurrent_bots": 3},
                                    {"version": 2, "max_concurrent_bots": 3},
                                    {"version": 1}, {"version": 1, "max_concurrent_bots": 2.5},
                                    {"version": 1, "max_concurrent_bots": True}])
def test_no_readable_version_1_object_is_unavailable_never_unlimited(answer):
    with pytest.raises(LimitsUnavailable):
        resolve(answer)


def test_an_invalid_value_never_lifts_a_limit():
    for bad in ({"max_scheduled_bots": 2.5}, {"max_scheduled_bots": "lots"},
                {"max_scheduled_bots": float("nan")}, {"seat_cooldown_s": float("inf")},
                {"bot_start_rate": {"burst": 5, "per_minute": float("nan")}},
                {"max_scheduled_bots": True}, {"bot_start_rate": {"burst": 0, "per_minute": 1}},
                {"bot_start_rate": {"burst": 5}}, {"seat_cooldown_s": -5}):
        limits = resolve(_v1(3, **bad))
        assert limits.max_scheduled_bots == 5, bad
        assert limits.bot_start_rate == DEFAULT_BOT_START_RATE, bad
        assert limits.seat_cooldown_s == 60, bad


def test_a_negative_cap_allows_nothing_and_a_duration_rounds_up():
    assert resolve(_v1(-2)).max_concurrent_bots == 0
    assert resolve(_v1(0)).max_concurrent_bots == 0, "0 is a real cap: no bot may start"
    assert resolve(_v1(3.0)).max_concurrent_bots == 3, "a whole float is a count"
    assert resolve(_v1(1, seat_cooldown_s=59.2)).seat_cooldown_s == 60


def test_retry_after_is_the_time_to_one_token():
    assert DEFAULT_BOT_START_RATE.retry_after_s() == 120
    assert DEFAULT_BOOKING_RATE.retry_after_s() == 360
    assert TokenRate(burst=1, per_minute=0).retry_after_s() == 3600


def test_the_bucket_replays_the_recorded_starts():
    rate = TokenRate(burst=3, per_minute=0.5)          # one token back every 120 s
    assert rate.allows([])
    assert rate.allows([10, 5]), "two of three spent"
    assert not rate.allows([10, 5, 1]), "the burst is spent"
    assert rate.allows([130, 125, 121]), "one token refilled since"
    assert not rate.allows([130, 125, 121, 1])
    assert rate.allows([rate.horizon_s() + 1] * 50), "starts past the horizon are not counted"


def test_the_bucket_holds_its_long_run_rate():
    """A start every time the bucket allows one, for two hours: the long-run count stays within the
    rate plus the burst (plus the documented horizon slack)."""
    rate = TokenRate(burst=10, per_minute=0.5)
    starts: list[float] = []
    for t in range(0, 7200, 5):
        if rate.allows([t - s for s in starts]):
            starts.append(t)
    allowed = 10 + 0.5 * 120
    assert len(starts) <= allowed * 1.25, len(starts)
    assert len(starts) >= allowed * 0.9, len(starts)
