"""A worker's delegation token ends with its unit: recorded at dispatch, revoked when the unit stops.

Identity verifies a ``vxd_`` token statelessly, so without this a token handed to a worker stayed good
until its ``exp`` whatever became of the worker. agent-api records each token's ``jti`` against the
unit it was minted for and, once the runtime no longer runs that unit, writes
``vexa:delegation:revoked:<jti>`` with the token's remaining lifetime — the key identity checks.

The store is a real Redis protocol implementation in memory (fakeredis), so TTLs are Redis's own.
"""
from __future__ import annotations

import time

import fakeredis
import pytest

from control_plane import delegation_revocation as dr
from control_plane import dispatch
from shared import delegation
from shared.config import DELEGATION_TTL_MARGIN_SEC, load_settings

SECRET = "test-delegation-secret"

INV = {
    "identity": {"subject": "u_jane", "launcher": "user:u_jane"},
    "runner": "claude-code",
    "workspaces": [{"id": "u_jane", "mode": "rw"}],
    "trigger": "scheduled",
    "context": {"kind": "none"},
    "start": {"entrypoint": {"inline": "hi"}},
}


class _Runtime:
    """Spawns into a list; ``state`` is what the runtime reports for any workload."""

    def __init__(self, state: str = "running"):
        self.spawned: list[dict] = []
        self.state = state

    def spawn(self, workload_id, profile, env):
        self.spawned.append(env)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return self.state


class _Identity:
    def mint(self, *a):
        return "dispatch-token"


class _BrokenStore:
    def __getattr__(self, name):
        def fail(*a, **k):
            raise ConnectionError("redis down")
        return fail


def _settings(tmp_path, **over):
    return load_settings(**{"mcp_url": "http://gateway:8000/mcp", "mcp_delegation_secret": SECRET,
                            "workspaces_dir": str(tmp_path), **over})


def _store():
    return fakeredis.FakeRedis(decode_responses=True)


def _claims(env):
    return delegation.verify_delegation(SECRET, env["VEXA_MCP_DELEGATION_TOKEN"])


def _dispatch(tmp_path, store, runtime=None):
    rt = runtime or _Runtime()
    uid = dispatch.Dispatcher(_settings(tmp_path), rt, _Identity(),
                              delegation_store=store).dispatch(INV)
    return uid, rt.spawned[-1]


# ── agent-api records the token it hands a unit ─────────────────────────────────────────────────

def test_a_dispatch_records_its_token_against_its_unit(tmp_path):
    store = _store()
    uid, env = _dispatch(tmp_path, store)
    claims = _claims(env)
    exp, minted = store.hget(dr.unit_key(uid), claims["jti"]).split("|")
    assert int(exp) == claims["exp"]
    assert abs(float(minted) - time.time()) < 60
    assert uid in store.smembers(dr.UNITS_KEY)
    # the record lives no longer than the token in it
    assert 0 < store.ttl(dr.unit_key(uid)) <= claims["exp"] - claims["iat"]


def test_a_shorter_lived_token_never_cuts_an_earlier_tokens_record_short():
    store = _store()
    t = time.time()
    dr.record(store, unit_id="u1", jti="long", exp=int(t) + 3000, now=t)
    dr.record(store, unit_id="u1", jti="short", exp=int(t) + 120, now=t)
    assert store.ttl(dr.unit_key("u1")) > 2900


def test_a_token_that_cannot_be_recorded_is_never_handed_out(tmp_path, caplog):
    """An unrecorded token could never be revoked: the worker runs without the vexa MCP instead (it
    attaches the MCP only with a token, worker.engine.mcp_delegation_config)."""
    uid, env = _dispatch(tmp_path, _BrokenStore())
    assert "VEXA_MCP_DELEGATION_TOKEN" not in env
    assert not any(v.startswith(delegation.PREFIX) for v in env.values())
    assert "could not be recorded for revocation" in caplog.text


# ── identity admits a token only while agent-api holds it live ──────────────────────────────────

def test_a_dispatch_holds_its_token_live_for_the_tokens_life(tmp_path):
    store = _store()
    _, env = _dispatch(tmp_path, store)
    claims = _claims(env)
    key = dr.live_key(claims["jti"])
    assert key == "vexa:delegation:live:" + claims["jti"]
    assert store.get(key) == "1"
    assert 0 < store.ttl(key) <= claims["exp"] - claims["iat"]


def test_ending_the_unit_takes_the_tokens_live_record_with_it(tmp_path):
    store = _store()
    _, env = _dispatch(tmp_path, store)
    jti = _claims(env)["jti"]
    assert dr.sweep(store, lambda: [], now=time.time() + dr.GRACE_SEC + 1) == 1
    assert not store.exists(dr.live_key(jti))
    assert store.exists(dr.revoked_key(jti))


# ── THE UNIT-END PATH: mint → the unit stops → the jti is in the store, for the token's remaining life

def test_mint_then_end_the_unit_and_its_token_is_revoked_for_its_remaining_life(tmp_path):
    store = _store()
    uid, env = _dispatch(tmp_path, store)
    claims = _claims(env)
    later = time.time() + dr.GRACE_SEC + 1
    # the runtime no longer reports the unit: it completed, idled out, was stopped or failed
    assert dr.sweep(store, lambda: [], now=later) == 1
    key = dr.revoked_key(claims["jti"])
    assert store.get(key) == "1"
    assert 0 < store.ttl(key) <= claims["exp"] - later
    assert not store.exists(dr.unit_key(uid)) and uid not in store.smembers(dr.UNITS_KEY)


def test_a_unit_still_running_keeps_its_token(tmp_path):
    store = _store()
    uid, env = _dispatch(tmp_path, store)
    assert dr.sweep(store, lambda: [uid], now=time.time() + dr.GRACE_SEC + 1) == 0
    assert not store.exists(dr.revoked_key(_claims(env)["jti"]))
    assert store.hexists(dr.unit_key(uid), _claims(env)["jti"])


def test_a_token_younger_than_the_grace_is_spared(tmp_path):
    """Its spawn may not have reached the runtime yet, so "not live" says nothing about it."""
    store = _store()
    uid, env = _dispatch(tmp_path, store)
    assert dr.sweep(store, lambda: []) == 0
    assert not store.exists(dr.revoked_key(_claims(env)["jti"]))


def test_an_expired_token_is_forgotten_not_written(tmp_path):
    store = _store()
    t = time.time()
    dr.record(store, unit_id="u1", jti="old", exp=int(t) - 5, now=t - 600)
    assert dr.sweep(store, lambda: [], now=t) == 0
    assert not store.exists(dr.revoked_key("old"))
    assert not store.smembers(dr.UNITS_KEY)


def test_a_sweep_that_cannot_read_the_runtime_revokes_nothing():
    """A runtime blip must not cut a live worker's toolbelt; the records wait for the next sweep."""
    store = _store()
    t = time.time()
    dr.record(store, unit_id="u1", jti="j1", exp=int(t) + 900, now=t - 600)

    def down():
        raise ConnectionError("runtime down")

    with pytest.raises(ConnectionError):
        dr.sweep(store, down, now=t)
    assert not store.exists(dr.revoked_key("j1"))
    assert store.hexists(dr.unit_key("u1"), "j1")


def test_a_reused_unit_id_revokes_the_ended_incarnation_at_the_next_dispatch(tmp_path):
    """A chat thread keeps its unit id across warm windows. Once the next container runs, the sweep
    sees the id as live, so the dispatch that starts it revokes the previous container's token."""
    store = _store()
    uid = dispatch.dispatch_id(INV)
    t = time.time()
    dr.record(store, unit_id=uid, jti="ended-incarnation", exp=int(t) + 900, now=t - 600)
    _, env = _dispatch(tmp_path, store, _Runtime(state="stopped"))
    assert store.get(dr.revoked_key("ended-incarnation")) == "1"
    fresh = _claims(env)["jti"]
    assert not store.exists(dr.revoked_key(fresh))          # the new container's token stands
    assert store.hexists(dr.unit_key(uid), fresh)


def test_a_dispatch_to_a_running_unit_revokes_nothing(tmp_path):
    """A warm unit's container still holds its token: a touch must not cut it."""
    store = _store()
    uid = dispatch.dispatch_id(INV)
    t = time.time()
    dr.record(store, unit_id=uid, jti="warm", exp=int(t) + 900, now=t - 600)
    _dispatch(tmp_path, store, _Runtime(state="running"))
    assert not store.exists(dr.revoked_key("warm"))


def test_the_key_is_the_one_identity_reads():
    """Held equal with admin-api's reader by gate:fact-parity (delegation-revocation-key)."""
    assert dr.revoked_key("abc") == "vexa:delegation:revoked:abc"


# ── the lifetime: the chat warm window plus one turn, unless configured ──────────────────────────

def test_the_default_lifetime_is_the_chat_warm_window_plus_one_turn():
    s = load_settings()
    assert s.delegation_ttl_sec() == s.chat_idle_timeout_sec + DELEGATION_TTL_MARGIN_SEC == 1800
    assert load_settings(chat_idle_timeout_sec=1200).delegation_ttl_sec() == 2100


def test_a_configured_lifetime_wins_and_is_minted(tmp_path):
    store = _store()
    rt = _Runtime()
    dispatch.Dispatcher(_settings(tmp_path, mcp_delegation_ttl_sec=600), rt, _Identity(),
                        delegation_store=store).dispatch(INV)
    claims = _claims(rt.spawned[-1])
    assert claims["exp"] - claims["iat"] == 600


def test_a_lifetime_under_a_minute_is_refused():
    with pytest.raises(Exception):
        load_settings(mcp_delegation_ttl_sec=30)
