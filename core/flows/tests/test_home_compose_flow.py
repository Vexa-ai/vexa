"""The new-chat home's clock (#1783): two system flows, one step, agent-api does the composing.

`home.stale` (agent-api, when a page read finds the home old) and `meeting.completed` each run
`compose_home`, which asks agent-api to compose for that person. Both are INSTANCE flows — no owner —
so they never count against the person's routine quota."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent_half  # noqa: E402
import flows_defs.production as production  # noqa: E402
from flows import Done, Reaction, Registry, StepCtx, StepError  # noqa: E402

from test_link_loop import _StubDB  # noqa: E402

pytestmark = agent_half.required


def _ctx(refs: dict, flow=None) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior={}, clock_now=1_700_000_000.0, scratch={}, flow=flow)


def _reg():
    reg = Registry()
    production.build(reg, _StubDB())
    return reg


def test_home_stale_composes_and_the_flow_has_no_owner():
    reg = _reg()
    stale = reg.by_event[production.HOME_STALE.name]
    assert [f.name for f in stale] == ["home_compose"]
    assert not [f for f in reg.by_event[production.COMPLETED.name] if f.name.startswith("home")]
    for f in stale:
        assert f.steps == ("compose_home",)
        assert not f.owner                       # system flow: the quota reads owner, and there is none
    assert reg.needs("compose_home") == frozenset({"agent"})


def test_the_step_asks_agent_api_as_the_person(monkeypatch):
    calls = []
    monkeypatch.setattr(production.ag, "compose_home",
                        lambda uid, trigger: calls.append((uid, trigger)) or {"id": "hc-1", "stage": "new", "cards": 3, "model": ""})
    reg = _reg()
    flow = reg.by_event[production.HOME_STALE.name][0]
    out = reg.steps["compose_home"](_ctx({"uid": "7"}, flow))
    assert isinstance(out, Done) and out.result["id"] == "hc-1"
    assert calls == [("7", "home.stale")]


def test_a_fact_without_a_person_is_refused_terminally():
    with pytest.raises(StepError) as e:
        _reg().steps["compose_home"](_ctx({}))
    assert e.value.retryable is False


def test_an_agent_api_that_does_not_answer_never_fails_the_reaction(monkeypatch):
    def down(uid, trigger):
        raise StepError("agent-api did not compose the home (HTTP 0)", retryable=True)
    monkeypatch.setattr(production.ag, "compose_home", down)
    out = _reg().steps["compose_home"](_ctx({"uid": "7"}))
    assert isinstance(out, Done) and out.result["skipped"]
