"""A meeting bot connects to Redis as a user of its own session — never with the service connection.

The user may append to ``transcription_segments`` and use its own meeting's live-update and command
channels, with the six commands the bot sends; the spawn hands the bot that user's URL; a user that
cannot be defined fails the spawn; the user goes when the session reaches a terminal state; users
Redis lost on a restart are defined again with the same (derived) password. The exact rules were
checked against Valkey 8 with the bot image's own client.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.workload_redis import (
    INDEX_KEY,
    BotRedisError,
    BotRedisUsers,
    acl_rules,
    user_for,
)

SECRET = "test-admin-token"
SERVICE_URL = "redis://:service-password@redis:6379/0"


class FakeAsyncRedis:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.users: dict[str, list[str]] = {}
        self.index: dict[str, str] = {}

    async def execute_command(self, *args):
        if self.fail:
            raise ConnectionError("redis down")
        if args[:2] == ("ACL", "SETUSER"):
            self.users[args[2]] = list(args[3:])
            return "OK"
        if args[:2] == ("ACL", "DELUSER"):
            return 1 if self.users.pop(args[2], None) is not None else 0
        if args[:2] == ("ACL", "GETUSER"):
            return self.users.get(args[2])
        raise AssertionError(f"unexpected command {args}")

    async def hset(self, key, field, value):
        assert key == INDEX_KEY
        self.index[field] = value

    async def hgetall(self, key):
        return dict(self.index)

    async def hdel(self, key, field):
        self.index.pop(field, None)


def _users(r, now=lambda: 1000.0):
    return BotRedisUsers(r, SERVICE_URL, secret=SECRET, max_age_sec=3600, now=now)


def test_the_rules_name_the_stream_and_the_meetings_two_channels_only():
    rules = acl_rules(42, "pw")
    assert rules[:3] == ["reset", "on", ">pw"]
    assert [r for r in rules if r.startswith("~")] == ["~transcription_segments"]
    assert [r for r in rules if r.startswith("&")] == ["&tc:meeting:42:mutable", "&bot_commands:meeting:42"]
    assert sorted(r[1:] for r in rules if r.startswith("+")) == sorted(
        ["xadd", "publish", "subscribe", "unsubscribe", "ping", "quit"])


async def test_grant_defines_the_user_and_hands_back_its_url():
    r = FakeAsyncRedis()
    url = await _users(r).grant("conn-a", 42)
    parts = urlsplit(url)
    assert parts.username == user_for("conn-a") and parts.hostname == "redis" and parts.path == "/0"
    assert "service-password" not in url
    assert r.users[user_for("conn-a")] == acl_rules(42, parts.password)
    assert r.index[user_for("conn-a")] == "conn-a|42|1000"


async def test_a_user_that_cannot_be_defined_is_an_error():
    with pytest.raises(BotRedisError):
        await _users(FakeAsyncRedis(fail=True)).grant("conn-a", 42)


async def test_revoke_and_age_expiry():
    r = FakeAsyncRedis()
    users = _users(r, now=lambda: 0.0)
    await users.grant("conn-old", 1)
    await users.grant("conn-a", 2)
    await users.revoke("conn-a")
    assert user_for("conn-a") not in r.users and user_for("conn-a") not in r.index
    # the next spawn, past the max age, removes what no terminal callback did
    await _users(r, now=lambda: 4000.0).grant("conn-new", 3)
    assert set(r.users) == {user_for("conn-new")}


async def test_restore_defines_again_what_redis_lost_with_the_same_password():
    r = FakeAsyncRedis()
    url = await _users(r).grant("conn-a", 42)
    r.users.clear()
    assert await _users(r).restore() == 1
    assert r.users[user_for("conn-a")] == acl_rules(42, urlsplit(url).password)
    assert await _users(r).restore() == 0


# ── the spawn and the session's end ─────────────────────────────────────────────────────────────

def _app(r, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", SECRET)
    runtime = FakeRuntimeClient()
    client = TestClient(create_app(meeting_repo=InMemoryMeetingRepo(), runtime=runtime,
                                   token_secret=SECRET, bot_redis=_users(r)))
    return client, runtime


def test_the_bot_is_handed_its_sessions_user_and_loses_it_at_the_end(monkeypatch):
    r = FakeAsyncRedis()
    client, runtime = _app(r, monkeypatch)
    resp = client.post("/bots", headers={"x-user-id": "7"},
                       json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert resp.status_code == 201, resp.text
    inv = json.loads(runtime.specs[-1]["env"]["VEXA_BOT_CONFIG"])
    assert urlsplit(inv["redisUrl"]).username == user_for(inv["connectionId"])
    assert "service-password" not in json.dumps(runtime.specs[-1])
    auth = {"Authorization": f"Bearer {inv['token']}"}
    for status in ("joining", "active"):
        assert client.post("/bots/internal/callback/lifecycle", headers=auth,
                           json={"connection_id": inv["connectionId"], "status": status}).status_code == 200
    assert user_for(inv["connectionId"]) in r.users
    assert client.post("/bots/internal/callback/lifecycle", headers=auth,
                       json={"connection_id": inv["connectionId"], "status": "completed",
                             "exit_code": 0, "completion_reason": "stopped"}).status_code == 200
    assert user_for(inv["connectionId"]) not in r.users


def test_a_spawn_whose_redis_user_cannot_be_defined_fails_without_a_workload(monkeypatch):
    client, runtime = _app(FakeAsyncRedis(fail=True), monkeypatch)
    resp = client.post("/bots", headers={"x-user-id": "7"},
                       json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    assert resp.status_code == 502
    assert runtime.specs == []
