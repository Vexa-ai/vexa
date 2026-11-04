"""The one per-account limits model (meeting_api/limits.py): keys, defaults, resolution, the header.

Self-contained like the module, so the scheduled-bots train ports both unchanged."""
from __future__ import annotations

import json

from meeting_api.limits import (
    DEFAULT_BOOKING_RATE,
    DEFAULT_BOT_START_RATE,
    AccountLimits,
    TokenRate,
    from_header,
    resolve,
)

PLANS = {
    "default": {"seat_cooldown_s": 60},
    "plans": {
        "payg": {"max_scheduled_bots": 10, "bot_start_rate": {"burst": 20, "per_minute": 1}},
        "welcome": {"max_scheduled_bots": 2},
    },
}


def test_every_limit_has_one_key_and_one_default():
    limits = AccountLimits()
    assert limits.max_concurrent_bots is None
    assert limits.max_scheduled_bots == 5
    assert limits.bot_start_rate == TokenRate(burst=10, per_minute=0.5)
    assert limits.seat_cooldown_s == 60
    assert limits.queue_wait_s == 1800, "the founder's 30-minute queue wait"
    assert limits.booking_rate == TokenRate(burst=200, per_minute=10 / 60)


def test_the_account_column_is_the_ad_hoc_cap_and_the_plan_then_the_override_set_the_rest():
    limits = resolve(max_concurrent_bots=5, plan="payg", plan_settings=PLANS,
                     override={"max_scheduled_bots": 12})
    assert limits.max_concurrent_bots == 5
    assert limits.max_scheduled_bots == 12, "the account's override beats its plan"
    assert limits.bot_start_rate == TokenRate(burst=20, per_minute=1.0), "the plan beats the default"
    assert limits.queue_wait_s == 1800


def test_an_unknown_plan_or_no_settings_gives_the_defaults():
    assert resolve(max_concurrent_bots=2, plan="unknown", plan_settings=PLANS).max_scheduled_bots == 5
    assert resolve(max_concurrent_bots=2) == AccountLimits(max_concurrent_bots=2)


def test_an_invalid_value_never_lifts_a_limit():
    for bad in ({"max_scheduled_bots": 2.5}, {"max_scheduled_bots": "lots"},
                {"max_scheduled_bots": float("nan")}, {"seat_cooldown_s": float("inf")},
                {"bot_start_rate": {"burst": 5, "per_minute": float("nan")}},
                {"max_scheduled_bots": True}, {"bot_start_rate": {"burst": 0, "per_minute": 1}},
                {"bot_start_rate": {"burst": 5}}, {"seat_cooldown_s": -5}, "not an object"):
        limits = resolve(max_concurrent_bots=3, plan="welcome", plan_settings=PLANS, override=bad)
        assert limits.max_scheduled_bots == 2, bad
        assert limits.bot_start_rate == DEFAULT_BOT_START_RATE, bad
        assert limits.seat_cooldown_s == 60, bad


def test_the_header_as_the_gateway_sends_it_today_is_the_ad_hoc_cap():
    assert from_header("3") == AccountLimits(max_concurrent_bots=3)
    assert from_header("0").max_concurrent_bots == 0, "0 is a real cap: no bot may start"
    assert from_header(None) == AccountLimits()
    assert from_header("garbage") == AccountLimits()


def test_the_resolved_set_round_trips_through_the_header():
    limits = resolve(max_concurrent_bots=5, plan="payg", plan_settings=PLANS)
    assert from_header(limits.to_json()) == limits
    assert from_header(json.dumps({"max_concurrent": 4})).max_concurrent_bots == 4


def test_retry_after_is_the_time_to_one_token():
    assert DEFAULT_BOT_START_RATE.retry_after_s() == 120
    assert DEFAULT_BOOKING_RATE.retry_after_s() == 360
    assert TokenRate(burst=1, per_minute=0).retry_after_s() == 3600


def test_a_negative_limit_allows_nothing_and_never_means_no_limit():
    assert from_header("-1").max_concurrent_bots == 0
    assert from_header(json.dumps({"max_concurrent_bots": -3})).max_concurrent_bots == 0
    assert resolve(max_concurrent_bots=3, override={"max_scheduled_bots": -1}).max_scheduled_bots == 0


def test_numbers_that_are_not_limits_are_refused_and_fractions_are_explicit():
    assert from_header('{"max_concurrent_bots": NaN}') == AccountLimits()
    assert from_header('{"max_concurrent_bots": Infinity}') == AccountLimits()
    assert resolve(max_concurrent_bots=3.0).max_concurrent_bots == 3, "a whole float is a count"
    assert resolve(max_concurrent_bots=2.5).max_concurrent_bots is None, "a fraction is not a count"
    assert resolve(max_concurrent_bots=1, override={"seat_cooldown_s": 59.2}).seat_cooldown_s == 60, \
        "a duration rounds up, never shortening the limit"
