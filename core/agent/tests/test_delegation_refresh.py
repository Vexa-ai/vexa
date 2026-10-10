"""A live unit's delegation token is replaced before it expires; an ended unit's never is.

agent-api re-mints a live unit's token once two thirds of its life has passed — same person, regime,
ceiling and target, new ``jti`` — records it for revocation, and publishes it at the unit's delegation
key. The worker reads that key before each turn and rewrites its MCP attachment, so a unit alive past
one token's life keeps its tools. The replaced token is revoked after a short overlap. A unit the
runtime no longer runs is never refreshed, and its token is revoked: identity refuses it.

The store is fakeredis (Redis's own TTLs); time is passed explicitly as ``now``.
"""
from __future__ import annotations

import json
import os
import time

import fakeredis
import pytest

from control_plane import delegation_refresh as drf
from control_plane import delegation_revocation as dr
from control_plane import dispatch
from shared import delegation
from shared.config import load_settings
from shared.units import delegation_key
from worker import engine
from worker import friction

SECRET = "test-delegation-secret"
TTL = 1800

INV = {
    "identity": {"subject": "u_jane", "launcher": "user:u_jane"},
    "runner": "claude-code",
    "workspaces": [{"id": "u_jane", "mode": "rw"}, {"id": "ws_team", "mode": "ro"}],
    "trigger": "scheduled",
    "context": {"kind": "none"},
    "start": {"entrypoint": {"inline": "hi"}},
}


class _Runtime:
    def __init__(self):
        self.spawned: list[dict] = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append(env)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "running"


class _Identity:
    def mint(self, *a):
        return "dispatch-token"


def _store():
    return fakeredis.FakeRedis(decode_responses=True)


def _dispatch(tmp_path, store):
    settings = load_settings(mcp_url="http://gateway:8000/mcp", mcp_delegation_secret=SECRET,
                             workspaces_dir=str(tmp_path), mcp_delegation_ttl_sec=TTL)
    rt = _Runtime()
    uid = dispatch.Dispatcher(settings, rt, _Identity(), delegation_store=store).dispatch(INV)
    token = rt.spawned[-1]["VEXA_MCP_DELEGATION_TOKEN"]
    return uid, token, delegation.verify_delegation(SECRET, token)


def _refresher():
    return drf.Refresher(SECRET, lambda: TTL)


def _identity_refuses(store, token, now) -> bool:
    """What identity's /internal/validate does with a vxd_ bearer: verify, then refuse a jti with a
    revocation key (admin_api/app/delegation_revocation.py)."""
    try:
        claims = delegation.verify_delegation(SECRET, token, now=int(now))
    except delegation.DelegationError:
        return True
    return bool(store.exists(dr.revoked_key(claims["jti"])))


# ── agent-api: the refresh ──────────────────────────────────────────────────────────────────────

def test_a_dispatch_publishes_its_token_for_the_unit_and_for_agent_api(tmp_path):
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    assert store.get(delegation_key(uid)) == token
    assert store.get(dr.CURRENT_PREFIX + uid) == token
    assert 0 < store.ttl(delegation_key(uid)) <= TTL


def test_a_token_is_not_replaced_before_two_thirds_of_its_life(tmp_path):
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    early = claims["iat"] + TTL * 0.6
    dr.sweep(store, lambda: [uid], now=early, refresher=_refresher())
    assert store.get(delegation_key(uid)) == token


def test_a_unit_alive_past_the_ttl_keeps_its_tools(tmp_path):
    """THE CASE THIS EXISTS FOR. The unit is still running when two thirds of its token's life has
    passed: a new token is published, the old one is revoked after the overlap, and past the first
    token's expiry the unit's published token is still good."""
    store = _store()
    uid, first, claims = _dispatch(tmp_path, store)
    t_refresh = claims["iat"] + TTL * 0.7
    dr.sweep(store, lambda: [uid], now=t_refresh, refresher=_refresher())
    second = store.get(delegation_key(uid))
    assert second and second != first
    fresh = delegation.verify_delegation(SECRET, second, now=int(t_refresh))
    assert fresh["jti"] != claims["jti"]
    # same person, regime, ceiling and target
    assert fresh["sub"] == claims["sub"] and fresh["scope"] == claims["scope"]
    assert fresh.get("target") == claims.get("target")
    assert fresh["exp"] == int(t_refresh) + TTL
    # within the overlap the old token still stands (a turn already holding it finishes)
    assert not _identity_refuses(store, first, t_refresh + 60)
    # after it, the sweep revokes the replaced token
    after_overlap = t_refresh + drf.REFRESH_OVERLAP_SEC + 1
    dr.sweep(store, lambda: [uid], now=after_overlap, refresher=_refresher())
    assert _identity_refuses(store, first, after_overlap)
    # past the FIRST token's expiry, the unit's token is still accepted
    past_ttl = claims["exp"] + 60
    current = store.get(delegation_key(uid))
    assert current == second
    assert not _identity_refuses(store, current, past_ttl)


def test_a_unit_refreshed_again_and_again_never_loses_its_token(tmp_path):
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    t = claims["iat"]
    seen = {token}
    for _ in range(6):  # three hours of a warm unit, swept every half hour
        t += TTL * 0.7
        dr.sweep(store, lambda: [uid], now=t, refresher=_refresher())
        current = store.get(delegation_key(uid))
        assert not _identity_refuses(store, current, t + 1)
        seen.add(current)
    assert len(seen) == 7


def test_a_stopped_unit_is_never_refreshed_and_its_token_is_refused(tmp_path):
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    due = claims["iat"] + TTL * 0.7
    # the runtime no longer runs the unit
    dr.sweep(store, lambda: [], now=due, refresher=_refresher())
    assert store.get(delegation_key(uid)) is None
    assert store.get(dr.CURRENT_PREFIX + uid) is None
    assert _identity_refuses(store, token, due + 1)
    # and nothing for it was minted
    assert not store.hgetall(dr.unit_key(uid))


def test_a_refreshed_token_is_revoked_on_the_first_sweep_after_its_unit_ends(tmp_path):
    """A refreshed token is not waiting on a spawn, so the spawn grace does not protect it."""
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    t = claims["iat"] + TTL * 0.7
    dr.sweep(store, lambda: [uid], now=t, refresher=_refresher())
    second = store.get(delegation_key(uid))
    dr.sweep(store, lambda: [], now=t + 1, refresher=_refresher())
    assert _identity_refuses(store, second, t + 2)
    assert _identity_refuses(store, token, t + 2)


def test_the_refresh_reads_agent_api_s_record_never_the_worker_s_copy(tmp_path):
    """The worker's copy is a key the worker can read (and, in the shared-Redis mode, write); what is
    re-minted is agent-api's own record, verified with its own key."""
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    wider = delegation.mint_delegation(SECRET, subject="someone_else", regime="human", workspaces="*",
                                       ttl_sec=TTL, now=claims["iat"])
    store.set(delegation_key(uid), wider)
    t = claims["iat"] + TTL * 0.7
    dr.sweep(store, lambda: [uid], now=t, refresher=_refresher())
    fresh = delegation.verify_delegation(SECRET, store.get(delegation_key(uid)), now=int(t))
    assert fresh["sub"] == "u_jane" and fresh["scope"] == claims["scope"]


def test_an_expired_record_is_not_refreshed(tmp_path):
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    assert _refresher()(store, uid, claims["exp"] + 5) is None


# ── the worker: the attachment follows the published token ─────────────────────────────────────

def _attachment(path):
    cfg = json.loads(path.read_text())
    return cfg["mcpServers"][engine.VEXA_MCP_SERVER]["headers"]["Authorization"].removeprefix("Bearer ")


def test_the_worker_rewrites_its_attachment_when_a_newer_token_is_published(tmp_path):
    store = _store()
    path = tmp_path / "mcp.json"
    engine.write_mcp_config(path, "http://gateway:8000/mcp", "vxd_first")
    refresh = engine.DelegationRefresh(store, delegation_key("u1"), path=str(path),
                                       url="http://gateway:8000/mcp", token="vxd_first")
    assert refresh() is False                     # nothing published yet: the attachment stands
    store.set(delegation_key("u1"), "vxd_second")
    assert refresh() is True
    assert _attachment(path) == "vxd_second" and refresh.current() == "vxd_second"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert refresh() is False                     # the same token is not rewritten


def test_a_store_the_worker_cannot_read_keeps_the_attachment(tmp_path):
    class _Down:
        def get(self, key):
            raise ConnectionError("redis down")

    path = tmp_path / "mcp.json"
    engine.write_mcp_config(path, "http://gateway:8000/mcp", "vxd_first")
    refresh = engine.DelegationRefresh(_Down(), "k", path=str(path), url="http://gateway:8000/mcp",
                                       token="vxd_first")
    assert refresh() is False
    assert _attachment(path) == "vxd_first"


def test_the_token_leaves_the_environment_and_friction_reads_the_fresh_one(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_MCP_DELEGATION_TOKEN", "vxd_boot")
    monkeypatch.setenv("VEXA_MCP_URL", "http://gateway:8000/mcp")
    token = engine.DelegationRefresh.take()
    assert token == "vxd_boot" and "VEXA_MCP_DELEGATION_TOKEN" not in os.environ
    store = _store()
    refresh = engine.DelegationRefresh(store, delegation_key("u1"), path=None,
                                       url="http://gateway:8000/mcp", token=token)
    friction.use_token_source(refresh.current)
    try:
        store.set(delegation_key("u1"), "vxd_next")
        refresh()
        assert friction._edge() == ("http://gateway:8000", "vxd_next")
    finally:
        friction.use_token_source(None)


def test_end_to_end_a_warm_worker_attaches_with_a_token_good_past_the_first_ttl(tmp_path):
    """agent-api refreshes; the worker, before its next turn, rewrites the attachment it hands the
    harness; the token in the attachment is accepted after the first token's expiry."""
    store = _store()
    uid, first, claims = _dispatch(tmp_path, store)
    path = tmp_path / "mcp.json"
    engine.write_mcp_config(path, "http://gateway:8000/mcp", first)
    refresh = engine.DelegationRefresh(store, delegation_key(uid), path=str(path),
                                       url="http://gateway:8000/mcp", token=first)
    t = claims["iat"] + TTL * 0.7
    dr.sweep(store, lambda: [uid], now=t, refresher=_refresher())
    dr.sweep(store, lambda: [uid], now=t + drf.REFRESH_OVERLAP_SEC + 1, refresher=_refresher())
    assert refresh() is True                      # the next turn starts
    attached = _attachment(path)
    assert not _identity_refuses(store, attached, claims["exp"] + 60)
    assert _identity_refuses(store, first, claims["exp"] - 1)


@pytest.mark.parametrize("ttl", [60, 900, 1800, 3600])
def test_the_refresh_point_is_two_thirds_of_the_life(ttl):
    iat = int(time.time())
    assert not drf.due({"iat": iat, "exp": iat + ttl}, iat + ttl * 0.66)
    assert drf.due({"iat": iat, "exp": iat + ttl}, iat + ttl * 0.67)
