"""The LLM spend cap (`control_plane/llm_budget.py`): counted from each call's reported usage,
enforced before every turn is dispatched, announced at 80% and 100%, and switchable off entirely.

L1 over fakeredis for the ledger; L2 through `/api/chat/submit` for the refusal a person sees, with
a runtime that records every spawn so "nothing reached the model" is an assertion, not a hope."""
from __future__ import annotations

import datetime as dt
import json

import fakeredis
import pytest
from fastapi.testclient import TestClient

from control_plane import llm_budget as lb
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import load_settings

# 2026-10-15 12:00 UTC: the day resets at 2026-10-16 00:00, the month at 2026-11-01 00:00.
NOON = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc).timestamp()


def _budget(policy=None, env=None, notify=None, r=None):
    r = r or fakeredis.FakeRedis(decode_responses=True)
    return lb.Budget(r, lambda: dict(policy or {}), clock=lambda: NOON, env=env or {}, notify=notify), r


# ── the policy ─────────────────────────────────────────────────────────────────────────────────
def test_defaults_are_conservative_and_every_cap_is_on():
    caps = lb.Caps.from_policy({})
    assert caps.caps == {("org", "day"): 25.0, ("org", "month"): 250.0,
                         ("user", "day"): 5.0, ("user", "month"): 50.0}
    # no flat price: a call without a reported cost is priced at its model's market price
    assert caps.price_in is None and caps.price_out is None


def test_policy_values_override_and_off_removes_one_cap():
    caps = lb.Caps.from_policy({"llm_budget_user_daily_usd": "$2.5", "llm_budget_org_monthly_usd": "off",
                                "llm_budget_notify": "admin-1, ops-2"})
    assert caps.caps[("user", "day")] == 2.5 and caps.caps[("org", "month")] is None
    assert caps.notify == ("admin-1", "ops-2")


def test_an_unreadable_value_keeps_the_default_never_lifts_the_cap():
    caps = lb.Caps.from_policy({"llm_budget_user_daily_usd": "lots", "llm_budget_org_daily_usd": "-1"})
    assert caps.caps[("user", "day")] == 5.0 and caps.caps[("org", "day")] == 25.0
    assert set(caps.problems) == {"llm_budget_user_daily_usd", "llm_budget_org_daily_usd"}


# ── counting ───────────────────────────────────────────────────────────────────────────────────
def test_the_providers_own_cost_is_counted_when_it_reports_one():
    caps = lb.Caps.from_policy({})
    assert lb.cost_usd({"cost": 0.0123, "prompt_tokens": 10**6}, caps) == pytest.approx(0.0123)


def test_tokens_are_priced_at_the_policy_rates_when_no_cost_is_reported():
    caps = lb.Caps.from_policy({"llm_price_input_usd_per_mtok": "3", "llm_price_output_usd_per_mtok": "15"})
    assert lb.cost_usd({"prompt_tokens": 2_000_000, "completion_tokens": 100_000}, caps) == pytest.approx(7.5)


def test_record_adds_to_the_persons_and_the_deployments_day_and_month():
    b, r = _budget()
    b.record("alice", {"cost": 1.25})
    b.record("alice", {"cost": 0.75})
    b.record("bob", {"cost": 1.0})
    s = b.spent("alice")
    assert s[("user", "day")][0] == pytest.approx(2.0) and s[("user", "month")][0] == pytest.approx(2.0)
    assert s[("org", "day")][0] == pytest.approx(3.0) and s[("org", "month")][0] == pytest.approx(3.0)
    assert b.spent("bob")[("user", "day")][0] == pytest.approx(1.0)
    # kept across restarts: a fresh Budget over the same redis reads the same counters
    assert lb.Budget(r, dict, clock=lambda: NOON, env={}).spent("alice")[("org", "day")][0] == pytest.approx(3.0)


# ── the cap ────────────────────────────────────────────────────────────────────────────────────
def test_deny_at_the_persons_daily_cap():
    b, _ = _budget({"llm_budget_user_daily_usd": "1"})
    b.admit("alice")
    b.record("alice", {"cost": 1.0})
    with pytest.raises(lb.BudgetExhausted) as exc:
        b.admit("alice")
    e = exc.value
    assert (e.kind, e.scope, e.period, e.http_status) == ("llm_budget_exhausted", "user", "day", 402)
    assert e.resets_at == "2026-10-16 00:00 UTC"
    assert e.sentence() == "You have reached your AI usage limit for today. It resets at 2026-10-16 00:00 UTC."
    b.admit("bob")              # one person's cap is theirs alone


def test_the_deployments_cap_refuses_everybody():
    b, _ = _budget({"llm_budget_org_monthly_usd": "3", "llm_budget_user_daily_usd": "off",
                    "llm_budget_user_monthly_usd": "off", "llm_budget_org_daily_usd": "off"})
    b.record("alice", {"cost": 2.0})
    b.record("bob", {"cost": 1.0})
    for who in ("alice", "bob", "carol"):
        with pytest.raises(lb.BudgetExhausted) as exc:
            b.admit(who)
        assert (exc.value.scope, exc.value.period) == ("org", "month")
        assert exc.value.resets_at == "2026-11-01 00:00 UTC"
        assert "administrator has been notified" in exc.value.sentence()


def test_the_kill_switch_refuses_every_turn_whatever_the_ledger_says():
    b, _ = _budget(env={lb.KILL_ENV: "1"})
    with pytest.raises(lb.BudgetExhausted) as exc:
        b.admit("alice")
    assert exc.value.kind == "llm_disabled" and exc.value.http_status == 403
    assert "switched off" in exc.value.sentence()
    assert exc.value.fault()["kind"] == "refused"


def test_a_ledger_that_cannot_be_read_refuses():
    class Down:
        def mget(self, keys):
            raise ConnectionError("down")
    b = lb.Budget(Down(), dict, clock=lambda: NOON, env={})
    with pytest.raises(lb.BudgetExhausted):
        b.admit("alice")


# ── the notices ────────────────────────────────────────────────────────────────────────────────
def test_the_admin_is_told_once_at_80_and_once_at_100_percent():
    sent = []
    b, _ = _budget({"llm_budget_org_daily_usd": "10", "llm_budget_org_monthly_usd": "off",
                    "llm_budget_user_daily_usd": "off", "llm_budget_user_monthly_usd": "off",
                    "llm_budget_notify": "admin-1"},
                   notify=lambda who, notice: sent.append((who, notice)))
    b.record("alice", {"cost": 7.0})
    assert sent == []
    b.record("alice", {"cost": 1.5})                 # 85%
    b.record("bob", {"cost": 0.1})                   # still 80% band: no second notice
    assert [(w, n["percent"], n["scope"]) for w, n in sent] == [(("admin-1",), 80, "org")]
    b.record("bob", {"cost": 2.0})                   # 106%
    b.record("bob", {"cost": 2.0})
    assert [n["percent"] for _, n in sent] == [80, 100]
    assert [n["percent"] for n in b.notices()] == [100, 80]


def test_a_person_crossing_their_own_cap_is_announced_with_who_they_are():
    sent = []
    b, _ = _budget({"llm_budget_user_daily_usd": "1", "llm_budget_notify": "admin-1"},
                   notify=lambda who, notice: sent.append(notice))
    b.record("alice", {"cost": 1.0})
    mine = [n for n in sent if n["scope"] == "user"]
    assert {n["percent"] for n in mine} == {80, 100} and {n["subject"] for n in mine} == {"alice"}


# ── the meter: usage events on a unit's output stream ──────────────────────────────────────────
def _xadd(r, topic, ev):
    return r.xadd(topic, {"event": json.dumps(ev)})


def test_the_meter_counts_each_usage_event_once_and_stops_at_turn_complete():
    b, r = _budget()
    topic = lambda uid: f"unit:{uid}:out"  # noqa: E731
    m = lb.UsageMeter(b, r, output_topic=topic)
    _xadd(r, topic("u1"), {"type": "llm-usage", "cost": 9.0})      # before the watch: not ours
    tail = r.xrevrange(topic("u1"), count=1)[0][0]
    m.watch("u1", "alice", from_id=tail)
    _xadd(r, topic("u1"), {"type": "message-delta", "text": "hi"})
    _xadd(r, topic("u1"), {"type": "llm-usage", "cost": 0.5})
    _xadd(r, topic("u1"), {"type": "llm-usage", "prompt_tokens": 1000, "completion_tokens": 100})
    _xadd(r, topic("u1"), {"type": "turn-complete"})
    m.poll_once()
    assert m.watching() == []
    # no model named: the price table's base open-weight market price (model_prices.py)
    expect = 0.5 + (1000 * 0.24 + 100 * 2.20) / 1e6
    assert b.spent("alice")[("user", "day")][0] == pytest.approx(expect)
    # a second replica watching the same unit from the same point counts nothing twice
    m2 = lb.UsageMeter(b, r, output_topic=topic)
    m2.watch("u1", "alice", from_id=tail)
    m2.poll_once()
    assert b.spent("alice")[("user", "day")][0] == pytest.approx(expect)


# ── L2: the refusal a person sees, and that nothing was spawned ────────────────────────────────
class _Runtime:
    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append(workload_id)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Reader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "turn-complete"}


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    (root / "_global" / "POLICIES.md").write_text("---\nllm_budget_user_daily_usd: 1\n---\n# Policies\n")
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    runtime = _Runtime()
    dispatcher = Dispatcher(settings, runtime, _Identity())
    r = fakeredis.FakeRedis(decode_responses=True)
    budget = lb.Budget(r, lambda: lb.read_global_policy(settings), env={})
    dispatcher.set_llm_budget(budget)
    client = TestClient(create_app(dispatcher, stream_reader=_Reader()))
    return client, runtime, budget, dispatcher


def test_deny_at_the_cap_over_the_chat_route_nothing_is_spawned(stack):
    client, runtime, budget, _ = stack
    ok = client.post("/api/chat/submit", headers={"X-User-Id": "alice"}, json={"prompt": "hi"})
    assert ok.status_code == 200, ok.text
    spawned = len(runtime.spawned)
    budget.record("alice", {"cost": 1.0})            # read from _global/POLICIES.md: $1 a day
    r = client.post("/api/chat/submit", headers={"X-User-Id": "alice"}, json={"prompt": "again"})
    assert r.status_code == 402, r.text
    body = r.json()
    assert body["detail"].startswith("You have reached your AI usage limit for today.")
    assert body["reason"] == "llm_budget_exhausted"
    # the typed fault the chat already renders: a refused model call, not "Chat request failed"
    assert (body["fault"]["source"], body["fault"]["kind"]) == ("model-provider", "unpaid")
    assert body["fault"]["detail"] == body["detail"]
    assert len(runtime.spawned) == spawned
    # a person watching the stream gets the sentence ON the stream, as an error event
    st = client.post("/api/chat", headers={"X-User-Id": "alice"}, json={"prompt": "again"})
    assert st.status_code == 200 and "text/event-stream" in st.headers["content-type"]
    frames = [json.loads(line[5:]) for line in st.text.splitlines() if line.startswith("data:")]
    assert frames[0]["type"] == "error" and frames[0]["message"].startswith("You have reached")
    assert frames[0]["fault"]["kind"] == "unpaid" and frames[-1]["type"] == "turn-complete"
    assert len(runtime.spawned) == spawned
    # another person is unaffected
    assert client.post("/api/chat/submit", headers={"X-User-Id": "bob"},
                       json={"prompt": "hi"}).status_code == 200


def test_the_dispatch_itself_refuses_so_every_trigger_is_covered(stack):
    """Routines, sensors and event agents reach the model only through `Dispatcher.dispatch`
    (flows posts their turns to `/api/chat`; the ingress sinks call it directly)."""
    client, runtime, budget, disp = stack
    budget.record("alice", {"cost": 5.0})
    inv = {"identity": {"subject": "alice", "launcher": "user:alice"}, "runner": "claude-code",
           "workspaces": [{"id": "alice", "mode": "rw"}], "trigger": "scheduled",
           "context": {"kind": "none"}, "start": {"entrypoint": {"inline": "hi"}}}
    spawned = len(runtime.spawned)
    with pytest.raises(lb.BudgetExhausted):
        disp.dispatch(inv)
    assert len(runtime.spawned) == spawned
