"""Budgets and model policy in `_global/POLICIES.md` (#1783): read live, env only as the install
default, a person may only narrow; tokens counted per person per rolling day from the harness's
usage reports; background work pauses with a visible reason, a person is warned at 80% and stopped
at 100%; the admin sees one line per person; the catalog allows what the policy allows."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import budgets
from control_plane.model_providers import catalog as catalog_mod
from control_plane.routers import budgets as budgets_router
from control_plane.session_store import SessionStore
from shared import usage
from tests._session_fakes import MemBlobs

SEED = (Path(__file__).resolve().parents[3] / "behavior" / "global" / "POLICIES.md").read_text()


class FakeRedis:
    def __init__(self):
        self.h, self.kv = {}, {}

    def pipeline(self):
        return _Pipe(self)

    def hincrby(self, k, f, n):
        self.h.setdefault(k, {})
        self.h[k][f] = self.h[k].get(f, 0) + int(n)

    def hgetall(self, k):
        return dict(self.h.get(k, {}))

    def expire(self, k, s):
        return True

    def incr(self, k):
        self.kv[k] = self.kv.get(k, 0) + 1
        return self.kv[k]


class _Pipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def __getattr__(self, name):
        def op(*a):
            self.ops.append((name, a))
            return self
        return op

    def execute(self):
        return [getattr(self.r, n)(*a) for n, a in self.ops]


class Bell:
    def __init__(self):
        self.rows = []

    def emit(self, recipients, **kw):
        self.rows.append((list(recipients), kw))
        return []


def test_the_seed_declares_every_key_and_each_is_documented_in_one_line():
    fm = SEED.split("---", 2)[1]
    for k in budgets.KEYS:
        assert f"\n{k}:" in fm, k
        assert f"- `{k}` — " in SEED, k


def test_read_live_with_the_env_as_install_default_and_a_typo_never_unlimited(tmp_path):
    g = tmp_path / "_global"
    g.mkdir()
    env = {"VEXA_BUDGET_TOKENS_PER_USER_DAY": "900000"}
    assert budgets.resolve("", env)["budget_tokens_per_user_day"] == 900000            # install default
    (g / "POLICIES.md").write_text("---\nbudget_tokens_per_user_day: 1,500,000\n---\n")
    assert budgets.read(g, env)["budget_tokens_per_user_day"] == 1_500_000             # the admin's, live
    (g / "POLICIES.md").write_text("---\nbudget_tokens_per_user_day: lots\nroutine_runs_per_day: -1\n---\n")
    p = budgets.read(g, env)
    assert p["budget_tokens_per_user_day"] == 900000 and p["routine_runs_per_day"] == 48


def test_a_person_may_only_narrow():
    admin = budgets.resolve("---\nbudget_tokens_per_user_day: 1000\nmodels_allowed: a, b\nevent_agent_max_trust: draft\n---\n")
    mine = budgets.narrow(admin, {"budget_tokens_per_user_day": 5000, "models_allowed": "b,z", "event_agent_max_trust": "act"})
    assert mine["budget_tokens_per_user_day"] == 1000 and mine["models_allowed"] == "b" and mine["event_agent_max_trust"] == "draft"
    assert budgets.narrow(admin, {"budget_tokens_per_user_day": 10})["budget_tokens_per_user_day"] == 10


def test_usage_is_a_rolling_day_from_the_harness_reports():
    r = FakeRedis()
    now = 1_800_000_000.0
    tok = usage.tokens_of({"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10, "junk": 9})
    assert tok == 160
    usage.record(r, "u1", usage.kind_of_unit("agent-u1-chat-main-auto"), tok, now=now - 23 * 3600)
    usage.record(r, "u1", usage.kind_of_unit("agent-u1-chat-main"), 40, now=now)
    usage.record(r, "u1", "chat", 999, now=now - 25 * 3600)                      # older than a day
    got = usage.rolling(r, "u1", now=now)
    assert got["total"] == 200 and got["routines"] == 160 and got["chat"] == 40
    assert usage.subject_of_unit("agent-u_ann-chat-main-g2") == "u_ann"
    assert usage.subject_of_unit("agent-77-event-0123456789") == "77"
    assert usage.kind_of_unit("agent-77-event-0123456789") == "events"


def test_the_worker_passes_usage_on_and_agent_api_is_the_one_writer():
    # R0 (2026-10-16): the worker no longer writes the token view; agent-api's llm_budget does,
    # from the same `done` (test_model_prices.py), so a turn is counted once.
    from worker.engine import count_usage
    r = FakeRedis()
    evs = [{"type": "message-delta", "text": "x"}, {"type": "done", "ok": True, "usage": {"input_tokens": 70, "output_tokens": 30}}]
    assert list(count_usage(iter(evs), r, "agent-u2-chat-main")) == evs
    assert usage.rolling(r, "u2")["chat"] == 0


def test_interactive_warns_at_80_and_stops_at_100_background_pauses_at_its_share():
    pol = budgets.resolve("---\nbudget_tokens_per_user_day: 1000\nbudget_tokens_routines_share: 30\n---\n")
    bell, notes = Bell(), []
    budgets._WARNED.clear()
    assert budgets.gate("u", background=False, policy=pol, used={"total": 500}, notifier=bell) is None and not bell.rows
    assert budgets.gate("u", background=False, policy=pol, used={"total": 850}, notifier=bell) is None
    assert "85%" in bell.rows[-1][1]["object_name"]
    stop = budgets.gate("u", background=False, policy=pol, used={"total": 1000}, notifier=bell)
    assert stop["refused"] == "budget_exhausted" and "ask your admin" in stop["note"]
    paused = budgets.gate("u", background=True, policy=pol, used={"total": 400, "routines": 300}, notifier=bell,
                          note=notes.append)
    assert paused["refused"] == "budget_paused" and "30%" in paused["note"] and notes and notes[0] == paused["note"]
    assert budgets.gate("v", background=True, policy=pol, used={"total": 100, "routines": 50}) is None


def test_the_admin_sees_one_line_per_person_and_nobody_else_does(tmp_path, monkeypatch):
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    store.create("u_ann", name="a", title="A")
    store.create("u_bob", name="b", title="B")
    r = FakeRedis()
    usage.record(r, "u_ann", "chat", 1_000_000)
    usage.record(r, "u_bob", "routines", 10)
    monkeypatch.setattr(budgets, "usage_client", lambda url: r)

    class D:
        catalog = None

        def is_admin(self, s):
            return s == "u_admin"

    app = FastAPI()
    app.include_router(budgets_router.build(subject_of=lambda req: req.headers["x-user-id"], dispatcher=D(),
                                            session_store=store, wsr=None, _global_root=lambda: None, redis_url="redis://x"))
    c = TestClient(app)
    assert c.get("/api/budgets/usage", headers={"x-user-id": "u_ann"}).status_code == 403
    body = c.get("/api/budgets/usage", headers={"x-user-id": "u_admin"}).json()
    assert body["people"][0]["line"] == "u_ann: 1,000,000 tokens (50% of budget; 0 background)"
    mine = c.get("/api/budgets", headers={"x-user-id": "u_bob"}).json()
    assert mine["used"]["routines"] == 10 and mine["limits"]["budget_tokens_per_user_day"] == 2_000_000
    store.close()


def test_the_catalog_allows_what_the_policy_allows_and_a_missing_model_is_unavailable():
    decl = {"providers": {"anth": {"adapter": "anthropic"}},
            "models": [{"id": "big-opus", "provider": "anth", "model": "claude-opus-x", "display_name": "Big", "default": True},
                       {"id": "small-haiku", "provider": "anth", "model": "claude-haiku-x", "display_name": "Small"}]}
    cat = catalog_mod.parse(json.dumps(decl))
    from control_plane.dispatch import route_context
    ctx = route_context({})
    assert [m["id"] for m in cat.visible(ctx, admin=False)] == ["big-opus", "small-haiku"]
    cat.policy = lambda: budgets.resolve("---\nmodels_allowed: small-haiku, gone-model\nmodel_chat_default: small-haiku\n---\n")
    assert [m["id"] for m in cat.visible(ctx, admin=False)] == ["small-haiku"]
    assert cat.default_for(ctx, admin=False) == "small-haiku"
    status = budgets.model_status(cat.policy(), cat.ids)
    assert {"model": "gone-model", "status": "unavailable"} in status
    cat.policy = lambda: budgets.resolve("---\nmodels_for: small-haiku=@company.example\n---\n")
    assert [m["id"] for m in cat.visible(ctx, admin=False, person=lambda: ["u1", "pat@company.example"])] == ["big-opus", "small-haiku"]
    assert [m["id"] for m in cat.visible(ctx, admin=False, person=lambda: ["u2", "x@else.example"])] == ["big-opus"]
    from control_plane.dispatch import background_model
    cat.policy = lambda: budgets.resolve("")
    assert background_model(cat, "agent-u-chat-main-auto", "message") == "small-haiku"
    assert background_model(cat, "agent-u-chat-main", "message") == ""
