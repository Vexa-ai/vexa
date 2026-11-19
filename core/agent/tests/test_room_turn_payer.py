"""Who a room's turn bills (founder I20, `RoomPrincipal.payer`): the spend cap admits, and the usage
meter counts, the payer — the workspace owner, and in a room of two whoever started the turn.

L1 over fakeredis: the dispatcher's admission and its meter watch, and the meter handing a unit from
one payer to the next without losing the earlier payer's usage."""
from __future__ import annotations

import json

import fakeredis
import pytest

from control_plane import llm_budget as lb
from control_plane import room_principal as rp
from control_plane.dispatch import Dispatcher
from shared.config import load_settings


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


class _Meter:
    def __init__(self):
        self.watched = []

    def watch(self, unit_id, subject, from_id="0-0"):
        self.watched.append((unit_id, subject))


@pytest.fixture
def disp(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    (root / "_global" / "POLICIES.md").write_text("---\nllm_budget_user_daily_usd: 1\n---\n# Policies\n")
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    runtime = _Runtime()
    d = Dispatcher(settings, runtime, _Identity())
    budget = lb.Budget(fakeredis.FakeRedis(decode_responses=True),
                       lambda: lb.read_global_policy(settings), env={})
    meter = _Meter()
    d.set_llm_budget(budget, meter)
    return d, budget, meter, runtime


def _turn(speaker: str) -> dict:
    return {"identity": {"subject": speaker, "launcher": f"user:{speaker}"}, "runner": "claude-code",
            "workspaces": [{"id": speaker, "mode": "rw"}], "trigger": "message",
            "context": {"kind": "none"}, "start": {"entrypoint": {"inline": "hi"}}}


def _pair(**kw) -> rp.RoomPrincipal:
    return rp.RoomPrincipal(workspace="pair-ab", owner="alice", session="s-1",
                            roster=("alice:owner", "bob:owner"), pair=True, **kw)


def test_deny_a_room_of_two_refuses_the_turn_when_its_starter_is_out_of_budget(disp):
    d, budget, _meter, runtime = disp
    budget.record("bob", {"cost": 1.0})                 # bob at his $1 day cap; alice untouched
    with pytest.raises(lb.BudgetExhausted):
        d.dispatch(_turn("bob"), room_principal=_pair())
    assert runtime.spawned == []


def test_a_room_of_two_does_not_charge_the_other_owners_cap_for_the_starters_turn(disp):
    d, budget, meter, runtime = disp
    budget.record("alice", {"cost": 1.0})               # alice at her cap; bob starts the turn
    d.dispatch(_turn("bob"), room_principal=_pair())
    assert len(runtime.spawned) == 1
    assert [s for _u, s in meter.watched] == ["bob"]    # his turn's usage is counted to him


def test_an_ordinary_room_bills_its_owner_whoever_speaks(disp):
    d, budget, meter, runtime = disp
    team = rp.RoomPrincipal(workspace="team", owner="alice", session="s-2",
                            roster=("alice:owner", "bob:contributor"))
    budget.record("alice", {"cost": 1.0})
    with pytest.raises(lb.BudgetExhausted):
        d.dispatch(_turn("bob"), room_principal=team)
    assert runtime.spawned == []


def _xadd(r, topic, ev):
    return r.xadd(topic, {"event": json.dumps(ev)})


def test_the_meter_counts_the_earlier_payers_turn_when_the_unit_changes_hands():
    r = fakeredis.FakeRedis(decode_responses=True)
    b = lb.Budget(r, lambda: {}, env={})
    topic = lambda uid: f"unit:{uid}:out"  # noqa: E731
    m = lb.UsageMeter(b, r, output_topic=topic)
    m.watch("u1", "alice", from_id="0-0")
    _xadd(r, topic("u1"), {"type": "llm-usage", "cost": 0.5})       # alice's turn, not yet polled
    _xadd(r, topic("u1"), {"type": "turn-complete"})
    tail = r.xrevrange(topic("u1"), count=1)[0][0]
    m.watch("u1", "bob", from_id=tail)                                # bob starts the next turn
    _xadd(r, topic("u1"), {"type": "llm-usage", "cost": 0.25})
    _xadd(r, topic("u1"), {"type": "turn-complete"})
    m.poll_once()
    assert b.spent("alice")[("user", "day")][0] == pytest.approx(0.5)
    assert b.spent("bob")[("user", "day")][0] == pytest.approx(0.25)
    assert m.watching() == []
