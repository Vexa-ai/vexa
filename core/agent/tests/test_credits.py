"""The credits ledger (`control_plane/credits.py`, PLAN-8H 6.2, architecture decision G1).

Credits are dollars at our price: a call's market cost x the markup (1.3 by default). Each payer has a
5-hour and a weekly window, reported as a percentage of the plan's allowance. Extra usage is a switch
the person turns on (data only, no payment). All of it is behind the `usage` feature, off by default:
off, nothing is debited, nothing is refused, and `/api/usage` answers `feature_disabled`."""
from __future__ import annotations

import datetime as dt

import fakeredis
import pytest

from control_plane import credits as C
from control_plane import llm_budget as lb
from control_plane import model_prices as mp

NOON = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc).timestamp()
H5 = 5 * 3600
M = 1_000_000


def _r():
    return fakeredis.FakeRedis(decode_responses=True)


# ── debiting ──────────────────────────────────────────────────────────────────────────────────
def test_credits_are_market_cost_times_the_markup():
    r = _r()
    got = C.debit(r, "alice", cost_usd=2.0, now=NOON, policy={}, subject="alice", kind="chat", model="m")
    assert got == pytest.approx(2.6)
    assert C.debit(r, "alice", cost_usd=1.0, now=NOON, policy={"credit_markup": "1.0"}) == pytest.approx(1.0)


def test_an_unreadable_or_below_cost_markup_keeps_the_default():
    assert C.markup({"credit_markup": "lots"}) == 1.3
    assert C.markup({"credit_markup": "0.5"}) == 1.3          # never sell below cost by a typo


def test_each_call_is_journalled_with_its_price():
    r = _r()
    C.debit(r, "alice", cost_usd=0.5, now=NOON, policy={}, subject="alice", kind="routines", model="qwen3.8-27b")
    (_, row), = r.xrange(C.JOURNAL)
    assert row["payer"] == "alice" and row["kind"] == "routines" and row["model"] == "qwen3.8-27b"
    assert float(row["cost_usd"]) == pytest.approx(0.5) and float(row["credits"]) == pytest.approx(0.65)


# ── windows ───────────────────────────────────────────────────────────────────────────────────
def test_a_window_opens_at_the_first_call_and_resets_after_its_length():
    r = _r()
    pol = {"credits_5h": "10", "credits_week": "100"}
    assert C.state(r, "alice", pol, now=NOON)["five_h"] == {"pct": 0.0, "used": 0.0, "allowance": 10.0, "resets_at": None}
    C.debit(r, "alice", cost_usd=1.0, now=NOON, policy=pol)                  # 1.3 credits
    C.debit(r, "alice", cost_usd=1.0, now=NOON + 3600, policy=pol)
    s = C.state(r, "alice", pol, now=NOON + 3600)
    assert s["five_h"]["used"] == pytest.approx(2.6) and s["five_h"]["pct"] == pytest.approx(26.0)
    assert s["five_h"]["resets_at"] == NOON + H5
    assert s["week"]["resets_at"] == NOON + 7 * 86400 and s["week"]["pct"] == pytest.approx(2.6)
    # past the five hours: a new window; the week keeps counting
    C.debit(r, "alice", cost_usd=1.0, now=NOON + H5 + 1, policy=pol)
    s = C.state(r, "alice", pol, now=NOON + H5 + 1)
    assert s["five_h"]["used"] == pytest.approx(1.3) and s["five_h"]["resets_at"] == NOON + 2 * H5 + 1
    assert s["week"]["used"] == pytest.approx(3.9)


def test_payers_are_separate():
    r = _r()
    C.debit(r, "alice", cost_usd=1.0, now=NOON, policy={})
    assert C.state(r, "bob", {}, now=NOON)["five_h"]["used"] == 0


def test_no_allowance_means_no_window_limit():
    r = _r()
    C.debit(r, "alice", cost_usd=1000.0, now=NOON, policy={})
    s = C.state(r, "alice", {}, now=NOON)
    assert s["five_h"]["allowance"] is None and s["five_h"]["pct"] == 0.0
    C.admit(r, "alice", {}, now=NOON)                                        # never refused


# ── the limit and extra usage ─────────────────────────────────────────────────────────────────
def test_a_full_window_refuses_with_when_it_is_back():
    r = _r()
    pol = {"credits_5h": "1"}
    C.debit(r, "alice", cost_usd=1.0, now=NOON, policy=pol)                  # 1.3 of 1
    with pytest.raises(lb.BudgetExhausted) as e:
        C.admit(r, "alice", pol, now=NOON + 60)
    assert e.value.kind == "usage_window" and e.value.period == "5h"
    assert "5-hour" in e.value.sentence() and "back at" in e.value.sentence()
    # the time reads in a sentence: 12:00 + 5h = 17:00 UTC (the chat's limit card shows it as is)
    assert e.value.sentence().startswith("You've reached this 5-hour limit — back at 17:00 UTC.")
    assert e.value.http_status == 402
    C.admit(r, "alice", pol, now=NOON + H5 + 1)                              # the next window is open


def test_extra_usage_carries_on_past_the_window_and_counts_what_it_used():
    r = _r()
    pol = {"credits_5h": "1"}
    C.debit(r, "alice", cost_usd=1.0, now=NOON, policy=pol)
    C.set_extra(r, "alice", True, now=NOON, topup_credits=20)
    C.admit(r, "alice", pol, now=NOON + 60)
    C.debit(r, "alice", cost_usd=1.0, now=NOON + 60, policy=pol)
    s = C.state(r, "alice", pol, now=NOON + 60)
    assert s["extra"] is True and s["topup_credits"] == pytest.approx(20.0)
    assert s["extra_used"] == pytest.approx(1.3)            # the call made while extra usage was on
    C.set_extra(r, "alice", False, now=NOON + 61)
    with pytest.raises(lb.BudgetExhausted):
        C.admit(r, "alice", pol, now=NOON + 62)


# ── through the spend ledger, only with the feature on ────────────────────────────────────────
def _budget(r, on: bool, policy=None):
    prices = mp.PriceTable(r, env={mp.URL_ENV: "off"}, background=False)
    return lb.Budget(r, lambda: dict(policy or {}), clock=lambda: NOON, env={}, prices=prices,
                     features=lambda: {"usage": on})


def test_the_spend_ledger_debits_credits_only_with_usage_on():
    r = _r()
    _budget(r, False).record("alice", {"model": "qwen3.8-27b", "prompt_tokens": M, "completion_tokens": M})
    assert C.state(r, "alice", {}, now=NOON)["week"]["used"] == 0
    assert r.xlen(C.JOURNAL) == 0
    _budget(r, True).record("alice", {"model": "qwen3.8-27b", "prompt_tokens": M, "completion_tokens": M})
    assert C.state(r, "alice", {}, now=NOON)["week"]["used"] == pytest.approx(2.44 * 1.3)


def test_the_window_refuses_a_turn_only_with_usage_on():
    r = _r()
    pol = {"credits_5h": "1"}
    C.debit(r, "alice", cost_usd=5.0, now=NOON, policy=pol)
    _budget(r, False, pol).admit("alice")                                   # off: nothing refused
    with pytest.raises(lb.BudgetExhausted):
        _budget(r, True, pol).admit("alice")


# ── L2: the usage meter API and the refusal a person sees ─────────────────────────────────────
from fastapi.testclient import TestClient  # noqa: E402

from control_plane.api import create_app  # noqa: E402
from control_plane.dispatch import Dispatcher  # noqa: E402
from shared.config import load_settings  # noqa: E402


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


def _stack(tmp_path, monkeypatch, features: str):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    monkeypatch.setenv("VEXA_FEATURES", features)
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    (root / "_global" / "POLICIES.md").write_text("---\ncredits_5h: 1\ncredits_week: 10\n---\n# Policies\n")
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    runtime = _Runtime()
    dispatcher = Dispatcher(settings, runtime, _Identity())
    r = _r()
    from control_plane import features as F
    budget = lb.Budget(r, lambda: lb.read_global_policy(settings), env={},
                       prices=mp.PriceTable(r, env={mp.URL_ENV: "off"}, background=False),
                       features=lambda: F.effective(root / "_global"))
    dispatcher.set_llm_budget(budget)
    return TestClient(create_app(dispatcher, stream_reader=_Reader())), runtime, budget, r


def test_the_meter_api_is_refused_with_usage_off(tmp_path, monkeypatch):
    client, *_ = _stack(tmp_path, monkeypatch, "")
    got = client.get("/api/usage", headers={"X-User-Id": "alice"})
    assert got.status_code == 403 and got.json()["detail"]["reason"] == "feature_disabled"


def test_the_meter_shows_windows_and_extra_usage_turns_a_full_window_back_on(tmp_path, monkeypatch):
    client, runtime, budget, r = _stack(tmp_path, monkeypatch, "usage")
    h = {"X-User-Id": "alice"}
    s = client.get("/api/usage", headers=h).json()
    assert s["five_h"]["pct"] == 0 and s["five_h"]["allowance"] == 1.0 and s["extra"] is False
    budget.record("alice", {"cost": 1.0})                          # 1.3 credits of a 1-credit window
    s = client.get("/api/usage", headers=h).json()
    assert s["five_h"]["pct"] == pytest.approx(130.0) and s["week"]["pct"] == pytest.approx(13.0)
    assert client.get("/api/usage", headers={"X-User-Id": "bob"}).json()["five_h"]["used"] == 0
    refused = client.post("/api/chat/submit", headers=h, json={"prompt": "hi"})
    assert refused.status_code == 402 and refused.json()["reason"] == "usage_window"
    assert "5-hour limit" in refused.json()["detail"]
    spawned = len(runtime.spawned)
    on = client.post("/api/usage", headers=h, json={"extra": True, "topup_credits": 10})
    assert on.status_code == 200 and on.json()["extra"] is True and on.json()["topup_credits"] == 10
    assert client.post("/api/chat/submit", headers=h, json={"prompt": "hi"}).status_code == 200
    assert len(runtime.spawned) > spawned
    assert client.post("/api/usage", headers=h, json={"extra": True, "card": "4242"}).status_code == 422


def test_usage_is_not_in_all_so_app_dev_keeps_it_off():
    from control_plane import features as F
    assert F.install_defaults({F.ENV: "all"})["usage"] is False
    assert F.install_defaults({F.ENV: "all,usage"})["usage"] is True


def test_a_markup_below_cost_is_refused_and_the_default_applies_through_the_ledger():
    # the POLICIES prose and the code agree: default 1.3; below 1.0 refused (1.3 applies); 1.0 allowed
    r = _r()
    assert C.debit(r, "a", cost_usd=1.0, now=NOON, policy={"credit_markup": "0.9"}) == pytest.approx(1.3)
    assert C.debit(r, "a", cost_usd=1.0, now=NOON, policy={"credit_markup": "-2"}) == pytest.approx(1.3)
    assert C.debit(r, "a", cost_usd=1.0, now=NOON, policy={"credit_markup": "1.0"}) == pytest.approx(1.0)
    assert C.debit(r, "a", cost_usd=1.0, now=NOON, policy={}) == pytest.approx(1.3)
    seed = (C.__file__.rsplit("/core/", 1)[0] + "/behavior/global/POLICIES.md")
    text = open(seed).read()
    assert "credit_markup: 1.3" in text and "below 1.0" in text and "refused" in text
