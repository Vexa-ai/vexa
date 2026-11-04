"""The per-account bot-start bucket (founder, 4 Nov 2026: abuse protections for ad-hoc bots).

``POST /bots`` on the public edge spends one token of the account's bucket; an empty bucket is a 429
naming the reason, with the time to the next token. Nothing else spends it: every other route, and
every other account, is unaffected. Scheduled starts never reach this edge (meeting-api's auto-join
spawns in process; Flows calls the service door, its own listener)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import VALID_KEY, FakeAuthorizer, FakeDownstream, FakeRedis
from gateway.app import create_app
from gateway.ratelimit import PerUserRateLimiter, bot_start_from_env, is_bot_start

AUTH = {"x-api-key": VALID_KEY}
BOT = {"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}


def _client(limiter, authorizer=None):
    app = create_app(authorizer or FakeAuthorizer(),
                     FakeDownstream(status_code=201, body={"id": 1}), FakeRedis(),
                     bot_start_limiter=limiter)
    return TestClient(app)


def test_starts_past_the_bucket_are_429_with_the_reason_and_the_wait():
    now = {"t": 0.0}
    limiter = PerUserRateLimiter(capacity=2, refill_per_sec=0.5 / 60, clock=lambda: now["t"])
    client = _client(limiter)

    assert client.post("/bots", headers=AUTH, json=BOT).status_code == 201
    assert client.post("/bots", headers=AUTH, json=BOT).status_code == 201
    r = client.post("/bots", headers=AUTH, json=BOT)

    assert r.status_code == 429
    assert r.json() == {"detail": "Bot start rate exceeded", "reason": "bot_start_rate"}
    assert r.headers["retry-after"] == "120"
    now["t"] = 120.0
    assert client.post("/bots", headers=AUTH, json=BOT).status_code == 201, "one token back"


def test_only_the_bot_start_spends_the_bucket():
    limiter = PerUserRateLimiter(capacity=1, refill_per_sec=0, clock=lambda: 0.0)
    client = _client(limiter)
    for _ in range(5):
        assert client.get("/bots/status", headers=AUTH).status_code != 429
    assert client.post("/bots", headers=AUTH, json=BOT).status_code == 201
    assert client.post("/bots", headers=AUTH, json=BOT).status_code == 429


def test_one_accounts_starts_never_throttle_another():
    limiter = PerUserRateLimiter(capacity=1, refill_per_sec=0, clock=lambda: 0.0)
    first = _client(limiter)
    other = _client(limiter, authorizer=FakeAuthorizer(user={
        "user_id": 8, "scopes": ["bot", "tx"], "max_concurrent": 3, "email": "o@example.com"}))
    assert first.post("/bots", headers=AUTH, json=BOT).status_code == 201
    assert first.post("/bots", headers=AUTH, json=BOT).status_code == 429
    assert other.post("/bots", headers=AUTH, json=BOT).status_code == 201


def test_the_production_bucket_defaults_and_its_off_switch():
    limiter = bot_start_from_env(lambda k, d: {}.get(k, d))
    assert limiter is not None and limiter.refill_per_sec == 0.5 / 60
    assert [limiter.allow("u") for _ in range(11)].count(True) == 10
    assert bot_start_from_env(lambda k, d: {"GATEWAY_BOT_START_LIMIT_DISABLED": "1"}.get(k, d)) is None


def test_what_counts_as_a_bot_start():
    assert is_bot_start("POST", "/bots") and is_bot_start("post", "/bots/")
    assert not is_bot_start("GET", "/bots")
    assert not is_bot_start("POST", "/bots/google_meet/abc/chat")
    assert not is_bot_start("DELETE", "/bots/google_meet/abc")
