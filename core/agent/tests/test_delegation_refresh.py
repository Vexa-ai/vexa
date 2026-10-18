"""A live unit's delegation token is replaced before it expires; an ended unit's never is.

agent-api re-mints a live unit's token once half of its life has passed — same person, regime,
ceiling and target, new ``jti`` — records it for revocation, and publishes it at the unit's delegation
key. The worker reads that key before each turn and rewrites its MCP attachment, so a unit alive past
one token's life keeps its tools. The replaced token is NOT revoked while its unit runs: a turn that
started with it keeps it until its own ``exp``. A unit the runtime no longer runs is never refreshed,
and all its tokens are revoked: identity refuses them. A turn that outlives its token anyway ends
with a typed fault (``worker.tool_access``).

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
from worker import tool_access

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


def test_a_token_is_not_replaced_before_half_of_its_life(tmp_path):
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    early = claims["iat"] + TTL * 0.45
    dr.sweep(store, lambda: [uid], now=early, refresher=_refresher())
    assert store.get(delegation_key(uid)) == token


def test_a_unit_alive_past_the_ttl_keeps_its_tools(tmp_path):
    """THE CASE THIS EXISTS FOR. The unit is still running when half of its token's life has
    passed: a new token is published, and past the first token's expiry the unit's published token
    is still good."""
    store = _store()
    uid, first, claims = _dispatch(tmp_path, store)
    t_refresh = claims["iat"] + TTL * 0.5
    dr.sweep(store, lambda: [uid], now=t_refresh, refresher=_refresher())
    second = store.get(delegation_key(uid))
    assert second and second != first
    fresh = delegation.verify_delegation(SECRET, second, now=int(t_refresh))
    assert fresh["jti"] != claims["jti"]
    # same person, regime, ceiling and target
    assert fresh["sub"] == claims["sub"] and fresh["scope"] == claims["scope"]
    assert fresh.get("target") == claims.get("target")
    assert fresh["exp"] == int(t_refresh) + TTL
    # past the FIRST token's expiry, the unit's token is still accepted
    past_ttl = claims["exp"] + 60
    dr.sweep(store, lambda: [uid], now=past_ttl, refresher=_refresher())
    current = store.get(delegation_key(uid))
    assert not _identity_refuses(store, current, past_ttl)


def test_a_replaced_token_is_not_revoked_while_its_unit_runs(tmp_path):
    """A turn that started with the old token keeps it: half a lifetime of headroom (900 s at the
    default), until its own exp. Nothing revokes it early while the unit is live."""
    store = _store()
    uid, first, claims = _dispatch(tmp_path, store)
    t_refresh = claims["iat"] + TTL * 0.5
    dr.sweep(store, lambda: [uid], now=t_refresh, refresher=_refresher())
    for t in (t_refresh + 60, t_refresh + 600, claims["exp"] - 1):
        dr.sweep(store, lambda: [uid], now=t, refresher=_refresher())
        assert not _identity_refuses(store, first, t), t
    assert claims["exp"] - t_refresh == TTL // 2 == 900
    # it ends at its own exp
    assert _identity_refuses(store, first, claims["exp"])


def test_a_replaced_token_is_revoked_when_its_unit_ends(tmp_path):
    store = _store()
    uid, first, claims = _dispatch(tmp_path, store)
    t = claims["iat"] + TTL * 0.5
    dr.sweep(store, lambda: [uid], now=t, refresher=_refresher())
    second = store.get(delegation_key(uid))
    dr.sweep(store, lambda: [], now=t + 1, refresher=_refresher())
    assert _identity_refuses(store, first, t + 2)
    assert _identity_refuses(store, second, t + 2)


def test_a_unit_refreshed_again_and_again_never_loses_its_token(tmp_path):
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    t = claims["iat"]
    seen = {token}
    for _ in range(6):  # three hours of a warm unit
        t += TTL * 0.5
        dr.sweep(store, lambda: [uid], now=t, refresher=_refresher())
        current = store.get(delegation_key(uid))
        assert not _identity_refuses(store, current, t + 1)
        seen.add(current)
    assert len(seen) == 7


def test_a_stopped_unit_is_never_refreshed_and_its_token_is_refused(tmp_path):
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    due = claims["iat"] + TTL * 0.5
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
    t = claims["iat"] + TTL * 0.5
    dr.sweep(store, lambda: [uid], now=t, refresher=_refresher())
    second = store.get(delegation_key(uid))
    dr.sweep(store, lambda: [], now=t + 1, refresher=_refresher())
    assert _identity_refuses(store, second, t + 2)


def test_the_refresh_reads_agent_api_s_record_never_the_worker_s_copy(tmp_path):
    """The worker's copy is a key the worker can read (and, in the shared-Redis mode, write); what is
    re-minted is agent-api's own record, verified with its own key."""
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    wider = delegation.mint_delegation(SECRET, subject="someone_else", regime="human", workspaces="*",
                                       ttl_sec=TTL, now=claims["iat"])
    store.set(delegation_key(uid), wider)
    t = claims["iat"] + TTL * 0.5
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
    dr.sweep(store, lambda: [uid], now=claims["iat"] + TTL * 0.5, refresher=_refresher())
    assert refresh() is True                      # the next turn starts
    attached = _attachment(path)
    assert not _identity_refuses(store, attached, claims["exp"] + 60)
    assert _identity_refuses(store, first, claims["exp"])


@pytest.mark.parametrize("ttl", [60, 900, 1800, 3600])
def test_the_refresh_point_is_half_of_the_life(ttl):
    iat = int(time.time())
    assert not drf.due({"iat": iat, "exp": iat + ttl}, iat + ttl * 0.49)
    assert drf.due({"iat": iat, "exp": iat + ttl}, iat + ttl * 0.5)


# ── a turn that outlives its token fails loud (P18) ────────────────────────────────────────────

def _turn(tool_ok: bool, done: dict | None = None):
    return [
        {"type": "turn-accepted"},
        {"type": "tool-call", "tool": "mcp__vexa__list_meetings", "args": {}, "callId": "c1"},
        {"type": "tool-result", "callId": "c1", "ok": tool_ok, "summary": "401"},
        {"type": "message-delta", "text": "I could not reach your meetings."},
        done or {"type": "done", "reply": "I could not reach your meetings.", "ok": True},
    ]


def test_a_vexa_call_refused_after_the_token_expired_ends_the_turn_with_a_typed_fault():
    exp = 1_800_000_000
    events = list(tool_access.watch(_turn(tool_ok=False), exp, now=lambda: exp + 5))
    done = events[-1]
    assert done["type"] == "done" and done["ok"] is False
    f = done["fault"]
    assert f["source"] == "vexa-tools" and f["kind"] == "access_expired" and f["status"] == 401
    assert "tool access" in f["detail"] and "expired" in f["detail"]
    assert f["remedy"]
    # the turn's own words are kept beside the fault
    assert done["reply"] == "I could not reach your meetings."
    assert events[:-1] == _turn(tool_ok=False)[:-1]


def test_a_refused_call_before_the_token_expired_is_not_blamed_on_it():
    exp = 1_800_000_000
    done = list(tool_access.watch(_turn(tool_ok=False), exp, now=lambda: exp - 5))[-1]
    assert done["ok"] is True and "fault" not in done


def test_a_successful_call_after_expiry_raises_nothing_and_another_tool_is_not_counted():
    exp = 1_800_000_000
    assert "fault" not in list(tool_access.watch(_turn(tool_ok=True), exp, now=lambda: exp + 5))[-1]
    other = [{"type": "tool-call", "tool": "WebFetch", "callId": "w"},
             {"type": "tool-result", "callId": "w", "ok": False},
             {"type": "done", "reply": "", "ok": True}]
    assert "fault" not in list(tool_access.watch(other, exp, now=lambda: exp + 5))[-1]


def test_a_done_that_already_names_its_fault_keeps_it():
    exp = 1_800_000_000
    provider = {"source": "model-provider", "kind": "unpaid", "status": 402}
    done = list(tool_access.watch(
        _turn(False, {"type": "done", "reply": "", "ok": False, "fault": provider}), exp,
        now=lambda: exp + 5))[-1]
    assert done["fault"] == provider


def test_the_turn_attaches_with_a_token_whose_exp_the_worker_reads():
    token = delegation.mint_delegation(SECRET, subject="u_jane", regime="human", workspaces="*",
                                       ttl_sec=TTL, now=1_800_000_000)
    assert tool_access.token_exp(token) == 1_800_000_000 + TTL
    assert tool_access.token_exp("vxd_garbage") is None and tool_access.token_exp("") is None


def test_end_to_end_a_turn_that_outlives_its_token_fails_loud(tmp_path):
    """A turn starts on the unit's current token; it runs past that token's exp; its vexa call is
    refused (identity refuses the expired token); the turn's done carries the typed fault."""
    store = _store()
    uid, token, claims = _dispatch(tmp_path, store)
    exp = tool_access.token_exp(token)
    assert exp == claims["exp"]
    late = claims["exp"] + 30
    assert _identity_refuses(store, token, late)          # what the gateway hears from identity
    done = list(tool_access.watch(_turn(tool_ok=False), exp, now=lambda: late))[-1]
    assert done["ok"] is False and done["fault"]["kind"] == "access_expired"
