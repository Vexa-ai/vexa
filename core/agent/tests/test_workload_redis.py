"""An agent worker connects to Redis as a user of its own unit — never with the service connection.

The user may touch exactly ``unit:<id>:in``, ``unit:<id>:out`` and ``unit:<id>:cursor`` with the six
commands the worker sends; the dispatch hands the worker that user's URL; a user that cannot be
defined refuses the dispatch; users of units no longer live are swept; users Redis lost on a restart
are defined again with the same (derived) password. The exact rules were checked against Valkey 8
with the worker image's own client.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from control_plane import dispatch, workload_redis as wr
from shared.config import load_settings

SECRET = "internal-secret-for-tests-0123456789abcdef"
SERVICE_URL = "redis://:service-password@redis:6379/0"


class FakeRedis:
    """Records ACL calls and keeps the index hash; ``users`` is what Redis currently defines."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.users: dict[str, list[str]] = {}
        self.index: dict[str, str] = {}
        self.calls: list[tuple] = []

    def execute_command(self, *args):
        self.calls.append(args)
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

    def hset(self, key, field, value):
        assert key == wr.INDEX_KEY
        self.index[field] = value

    def hgetall(self, key):
        return dict(self.index)

    def hdel(self, key, field):
        self.index.pop(field, None)


def test_the_rules_name_the_units_three_keys_and_nothing_else():
    rules = wr.acl_rules("agent-7-chat-a*b", "pw")
    assert rules[:3] == ["reset", "on", ">pw"]
    assert [r for r in rules if r.startswith("~")] == [
        "~unit:agent-7-chat-a\\*b:in", "~unit:agent-7-chat-a\\*b:out", "~unit:agent-7-chat-a\\*b:cursor"]
    assert "resetchannels" in rules and "-@all" in rules
    assert sorted(r[1:] for r in rules if r.startswith("+")) == sorted(wr.WORKER_COMMANDS)
    assert not any(r.startswith("&") or r in ("+@all", "~*", "allkeys", "allchannels") for r in rules)


def test_grant_defines_the_user_and_hands_back_its_url():
    r = FakeRedis()
    url = wr.grant(r, secret=SECRET, unit_id="agent-7-chat-s1", service_url=SERVICE_URL, now=lambda: 100)
    parts = urlsplit(url)
    user = wr.user_for("agent-7-chat-s1")
    assert parts.username == user and parts.hostname == "redis" and parts.port == 6379 and parts.path == "/0"
    assert "service-password" not in url
    assert r.users[user] == wr.acl_rules("agent-7-chat-s1", parts.password)
    assert r.index[user] == "agent-7-chat-s1|100"


def test_a_units_password_is_stable_and_its_own():
    a1 = wr.grant(FakeRedis(), secret=SECRET, unit_id="agent-7-chat-s1", service_url=SERVICE_URL)
    a2 = wr.grant(FakeRedis(), secret=SECRET, unit_id="agent-7-chat-s1", service_url=SERVICE_URL)
    b = wr.grant(FakeRedis(), secret=SECRET, unit_id="agent-8-chat-s1", service_url=SERVICE_URL)
    assert a1 == a2                                   # a respawn or a touch keeps a live worker's URL valid
    assert urlsplit(a1).password != urlsplit(b).password
    other = wr.grant(FakeRedis(), secret=SECRET + "x", unit_id="agent-7-chat-s1", service_url=SERVICE_URL)
    assert urlsplit(other).password != urlsplit(a1).password


def test_a_user_that_cannot_be_defined_is_an_error_not_a_fallback():
    with pytest.raises(wr.WorkloadRedisError):
        wr.grant(FakeRedis(fail=True), secret=SECRET, unit_id="u", service_url=SERVICE_URL)
    with pytest.raises(wr.WorkloadRedisError):
        wr.grant(FakeRedis(), secret="", unit_id="u", service_url=SERVICE_URL)


def test_sweep_removes_only_old_users_of_units_no_longer_live():
    r = FakeRedis()
    for uid, at in (("live", 0), ("gone", 0), ("young", 950)):
        wr.grant(r, secret=SECRET, unit_id=uid, service_url=SERVICE_URL, now=lambda at=at: at)
    removed = wr.sweep(r, ["live"], now=lambda: 1000, grace_sec=100)
    assert removed == 1
    assert set(r.index.values()) == {"live|0", "young|950"}
    assert wr.user_for("gone") not in r.users


def test_restore_defines_again_what_redis_lost_with_the_same_password():
    r = FakeRedis()
    url = wr.grant(r, secret=SECRET, unit_id="agent-7-chat-s1", service_url=SERVICE_URL)
    r.users.clear()                                   # a Redis restart keeps no ACL user
    assert wr.restore(r, secret=SECRET) == 1
    assert r.users[wr.user_for("agent-7-chat-s1")] == wr.acl_rules("agent-7-chat-s1", urlsplit(url).password)
    assert wr.restore(r, secret=SECRET) == 0


# ── the dispatch ────────────────────────────────────────────────────────────────────────────────

VALID_INV = {
    "identity": {"subject": "u_jane", "launcher": "user:u_jane"},
    "runner": "claude-code",
    "workspaces": [{"id": "u_jane", "mode": "rw"}],
    "trigger": "scheduled",
    "context": {"kind": "none"},
    "start": {"entrypoint": {"inline": "hi"}},
}


class _Runtime:
    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append(env)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, *a):
        return "tok"


def _settings(tmp_path):
    return load_settings(redis_url=SERVICE_URL, internal_api_secret=SECRET, workspaces_dir=str(tmp_path))


def test_the_worker_is_handed_its_units_user_not_the_service_url(tmp_path):
    rt, r = _Runtime(), FakeRedis()
    uid = dispatch.Dispatcher(_settings(tmp_path), rt, _Identity(), workload_redis=r).dispatch(VALID_INV)
    (env,) = rt.spawned
    assert urlsplit(env["REDIS_URL"]).username == wr.user_for(uid)
    assert "service-password" not in env["REDIS_URL"]
    assert not any("service-password" in v for v in env.values())


def test_a_dispatch_whose_redis_user_cannot_be_defined_does_not_spawn(tmp_path):
    rt = _Runtime()
    d = dispatch.Dispatcher(_settings(tmp_path), rt, _Identity(), workload_redis=FakeRedis(fail=True))
    with pytest.raises(wr.WorkloadRedisError):
        d.dispatch(VALID_INV)
    assert rt.spawned == []


def test_shared_mode_is_the_service_url(tmp_path):
    rt = _Runtime()
    dispatch.Dispatcher(_settings(tmp_path), rt, _Identity()).dispatch(VALID_INV)
    assert rt.spawned[0]["REDIS_URL"] == SERVICE_URL


def test_the_mode_setting_accepts_only_the_two_modes(monkeypatch):
    monkeypatch.setenv("REDIS_WORKLOAD_ACL", "shared")
    assert load_settings().redis_workload_acl == "shared"
    monkeypatch.setenv("REDIS_WORKLOAD_ACL", "off")
    with pytest.raises(Exception):
        load_settings()
