"""WS-6 — per-user token-bucket rate limiter (the gateway DoS guard), pure-logic module tests."""
from gateway.ratelimit import PerUserRateLimiter, from_env


def test_token_bucket_allows_burst_then_blocks():
    now = {"t": 0.0}
    rl = PerUserRateLimiter(capacity=3, refill_per_sec=0, clock=lambda: now["t"])
    assert [rl.allow("u") for _ in range(3)] == [True, True, True]
    assert rl.allow("u") is False  # bucket empty, no refill


def test_token_bucket_refills_over_time():
    now = {"t": 0.0}
    rl = PerUserRateLimiter(capacity=2, refill_per_sec=1.0, clock=lambda: now["t"])
    assert rl.allow("u") and rl.allow("u")   # drain the 2
    assert rl.allow("u") is False            # empty
    now["t"] = 1.0                           # +1s → +1 token
    assert rl.allow("u") is True
    assert rl.allow("u") is False
    now["t"] = 10.0                          # long gap refills only up to capacity (2), not more
    assert rl.allow("u") and rl.allow("u")
    assert rl.allow("u") is False


def test_buckets_are_per_user():
    now = {"t": 0.0}
    rl = PerUserRateLimiter(capacity=1, refill_per_sec=0, clock=lambda: now["t"])
    assert rl.allow("a") is True
    assert rl.allow("a") is False
    assert rl.allow("b") is True  # b has its own bucket — one user's exhaustion never throttles another


def test_invalid_config_raises():
    import pytest

    with pytest.raises(ValueError):
        PerUserRateLimiter(capacity=0, refill_per_sec=1)


def test_from_env_disabled_returns_none():
    env = {"GATEWAY_RATE_LIMIT_DISABLED": "1"}
    assert from_env(lambda k, d="": env.get(k, d)) is None


def test_from_env_builds_a_generous_limiter_by_default():
    rl = from_env(lambda k, d="": d)  # all defaults
    assert rl is not None
    assert rl.allow("u") is True


def test_stress_burst_allow_deny_split_is_exact():
    """SHAKE: capacity 100, no refill, 5000 calls from ONE user → EXACTLY 100 allowed, 4900 denied.
    The bucket never over-admits under a heavy burst (the DoS guard holds at scale)."""
    now = {"t": 0.0}
    rl = PerUserRateLimiter(capacity=100, refill_per_sec=0, clock=lambda: now["t"])
    verdicts = [rl.allow("flooder") for _ in range(5000)]
    assert verdicts.count(True) == 100, f"over-admitted: {verdicts.count(True)} allowed"
    assert verdicts.count(False) == 4900


def test_stress_many_users_isolated_under_load():
    """SHAKE: 500 distinct users each fire 10 against a capacity-5 bucket → every user gets exactly 5
    (no cross-user bleed, no shared-state corruption under load)."""
    now = {"t": 0.0}
    rl = PerUserRateLimiter(capacity=5, refill_per_sec=0, clock=lambda: now["t"])
    for u in range(500):
        allowed = sum(1 for _ in range(10) if rl.allow(f"u{u}"))
        assert allowed == 5, f"user u{u} got {allowed} (expected 5)"
