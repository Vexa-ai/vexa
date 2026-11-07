"""Every model is priced from a maintained market table (`control_plane/model_prices.py`), never at a
flat top-tier fallback; and the two usage ledgers have one writer (`llm_budget.Budget.record`).

Before: a call that reported tokens but no cost (the self-hosted Qwen) was counted at $15/$75 per
1M tokens — about 60x its market price."""
from __future__ import annotations

import datetime as dt
import json

import fakeredis
import pytest

from control_plane import llm_budget as lb
from control_plane import model_prices as mp
from shared import usage as usage_mod

NOON = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc).timestamp()
M = 1_000_000

OR_BODY = {"data": [
    {"id": "qwen/qwen3.8-27b", "pricing": {"prompt": "0.00000024", "completion": "0.0000022",
                                            "input_cache_read": "0.00000005"}},
    {"id": "anthropic/claude-haiku-5.5", "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}},
    {"id": "anthropic/claude-haiku-5.5:batch", "pricing": {"prompt": "0.00000005", "completion": "0.00000025"}},
    {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
    {"id": "acme/new-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
]}


def _table(r=None, fetch=None, env=None, clock=lambda: NOON):
    return mp.PriceTable(r, env=env if env is not None else {}, fetch=fetch or (lambda url: OR_BODY),
                         clock=clock, background=False)


def _budget(policy=None, prices=None, r=None):
    r = r or fakeredis.FakeRedis(decode_responses=True)
    return lb.Budget(r, lambda: dict(policy or {}), clock=lambda: NOON, env={},
                     prices=prices or _table(r)), r


# ── the red case: Qwen is priced at market, not at $15/$75 ─────────────────────────────────────
def test_qwen_without_a_reported_cost_is_priced_at_market_not_the_flat_fallback():
    b, _ = _budget()
    usd = b.record("alice", {"model": "qwen3.8-27b", "prompt_tokens": M, "completion_tokens": M})
    assert usd == pytest.approx(0.24 + 2.20)
    assert usd < 15 + 75


def test_the_snapshot_prices_qwen_at_market_when_the_live_table_cannot_be_read():
    def down(url):
        raise OSError("offline")
    t = _table(fetch=down)
    p = t.price("qwen3.8-27b")
    assert p is not None and p.source == "snapshot"
    assert (p.prompt, p.completion) == (0.24, 2.20)


def test_no_snapshot_price_is_above_the_market_read_it_was_taken_from():
    doc = json.loads(mp.SNAPSHOT.read_text())
    assert doc["source"].startswith("https://openrouter.ai/")
    for mid, row in doc["models"].items():
        assert 0 <= row["prompt"] <= 15 and 0 <= row["completion"] <= 75, mid
    assert mp.norm(doc["unmatched"]) in {mp.norm(k) for k in doc["models"]}


# ── matching ───────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("served,expect", [
    ("qwen3.8-27b", (0.24, 2.20)), ("Qwen/Qwen3.8-27B", (0.24, 2.20)), ("qwen/qwen3.8-27b", (0.24, 2.20)),
    ("claude-haiku-5-5", (0.10, 0.50)), ("anthropic/claude-haiku-5.5", (0.10, 0.50)),
])
def test_catalog_and_served_ids_find_the_market_id(served, expect):
    p = _table().price(served)
    assert p is not None and (round(p.prompt, 6), round(p.completion, 6)) == expect


def test_a_variant_or_a_varying_router_price_is_never_taken_for_the_base_model():
    t = _table()
    assert t.price("anthropic/claude-haiku-5.5:batch") is None
    assert t.price("openrouter/auto") is None


def test_cached_prompt_tokens_are_counted_at_the_cache_read_price():
    p = _table().price("qwen3.8-27b")
    assert p.cost({"prompt_tokens": M, "cache_read_tokens": M // 2, "completion_tokens": 0}) == \
        pytest.approx(0.5 * 0.24 + 0.5 * 0.05)


# ── a model no table names ─────────────────────────────────────────────────────────────────────
def test_an_unmatched_model_is_never_counted_above_market_by_default():
    b, _ = _budget()
    usd = b.record("alice", {"model": "my-lab/finetune-7b", "prompt_tokens": M, "completion_tokens": M})
    assert 0 < usd <= 0.24 + 2.20


def test_an_operator_rate_prices_an_unmatched_model_but_never_overrides_the_market_table():
    b, _ = _budget(policy={"llm_price_input_usd_per_mtok": "1", "llm_price_output_usd_per_mtok": "2"})
    assert b.record("a", {"model": "my-lab/finetune-7b", "prompt_tokens": M, "completion_tokens": M}) == \
        pytest.approx(3.0)
    assert b.record("a", {"model": "qwen3.8-27b", "prompt_tokens": M, "completion_tokens": M}) == \
        pytest.approx(2.44)


def test_the_providers_own_cost_still_wins():
    b, _ = _budget()
    assert b.record("a", {"model": "qwen3.8-27b", "cost": 0.01, "prompt_tokens": M}) == pytest.approx(0.01)


def test_the_default_policy_carries_no_flat_price():
    caps = lb.Caps.from_policy({})
    assert caps.price_in is None and caps.price_out is None


# ── the table: read daily, cached, shared ──────────────────────────────────────────────────────
def test_the_live_table_is_read_once_a_day_and_shared_through_redis():
    r = fakeredis.FakeRedis(decode_responses=True)
    calls = []
    now = [NOON]

    def fetch(url):
        calls.append(url)
        return OR_BODY
    t = _table(r, fetch=fetch, clock=lambda: now[0])
    assert t.price("acme/new-model").source == "live"
    assert calls == [mp.DEFAULT_URL]
    t.price("qwen3.8-27b")
    assert len(calls) == 1                                    # not again within the day
    # another replica reads redis, not the network
    other = _table(r, fetch=lambda url: pytest.fail("fetched again"), clock=lambda: now[0])
    assert other.price("acme/new-model").prompt == pytest.approx(1.0)
    now[0] += mp.REFRESH_S + 1
    t.price("qwen3.8-27b")
    assert len(calls) == 2                                    # stale after a day: read again


def test_a_failed_refresh_keeps_the_last_live_table():
    now = [NOON]
    state = {"up": True}

    def fetch(url):
        if not state["up"]:
            raise OSError("down")
        return OR_BODY
    t = _table(fetch=fetch, clock=lambda: now[0])
    assert t.price("acme/new-model") is not None
    state["up"] = False
    now[0] += mp.REFRESH_S + 1
    assert t.price("acme/new-model").source == "live"


def test_off_never_fetches_and_prices_from_the_snapshot():
    t = _table(fetch=lambda url: pytest.fail("fetched"), env={mp.URL_ENV: "off"})
    assert t.price("claude-opus-5-5").prompt == 4.0
    assert t.price("acme/new-model") is None


# ── one ledger: the USD ledger is the record, the token view is written by the same call ───────
def test_one_record_writes_the_spend_and_the_rolling_token_view():
    b, r = _budget()
    b.record("alice", {"model": "qwen3.8-27b", "prompt_tokens": 1000, "completion_tokens": 200},
             kind="routines")
    used = usage_mod.rolling(r, "alice", now=NOON)
    assert used["total"] == 1200 and used["routines"] == 1200
    assert b.spent("alice")[("user", "day")][0] > 0


def test_the_meter_records_a_turn_under_its_kind():
    r = fakeredis.FakeRedis(decode_responses=True)
    b, _ = _budget(r=r)
    meter = lb.UsageMeter(b, r, output_topic=lambda u: f"out:{u}")
    uid = "agent-alice-chat-main-auto"
    r.xadd(f"out:{uid}", {"event": json.dumps({"type": "done", "usage": {
        "model": "qwen3.8-27b", "prompt_tokens": 500, "completion_tokens": 50}})})
    r.xadd(f"out:{uid}", {"event": json.dumps({"type": "turn-complete"})})
    meter.watch(uid, "alice")
    meter.poll_once()
    assert usage_mod.rolling(r, "alice", now=NOON)["routines"] == 550


def test_the_worker_no_longer_writes_the_token_ledger():
    from worker.engine import count_usage
    r = fakeredis.FakeRedis(decode_responses=True)
    evs = [{"type": "done", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}]
    assert list(count_usage(iter(evs), r, "agent-u2-chat-main")) == evs
    assert usage_mod.rolling(r, "u2")["total"] == 0


def test_agent_apis_own_calls_go_through_the_one_ledger():
    b, r = _budget()
    lb.set_ledger(b)
    try:
        lb.record_local("bob", "home", {"model": "claude-haiku-5-5", "prompt_tokens": M, "completion_tokens": 0},
                        fallback_client=None)
    finally:
        lb.set_ledger(None)
    assert b.spent("bob")[("user", "day")][0] == pytest.approx(0.10)
    assert usage_mod.rolling(r, "bob", now=NOON)["home"] == M


def test_without_a_ledger_the_token_view_is_still_counted():
    r = fakeredis.FakeRedis(decode_responses=True)
    lb.set_ledger(None)
    lb.record_local("bob", "home", {"prompt_tokens": 7, "completion_tokens": 3}, fallback_client=r)
    assert usage_mod.rolling(r, "bob")["home"] == 10
