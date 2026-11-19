"""The paused-routine card: ONE queue item for a routine switched off after repeated refusals.

agent-api pauses a scheduled routine whose runs are refused the same way three times in a row and
publishes `routine.paused` once (`core/agent/control_plane/routine_refusals.py`). `routine_paused`
is its only consumer: one step that re-reads the routine file and blocks while it stays off. That
blocked reaction is what `whats_waiting` shows, in `behavior/queue/routine_paused.human.md`'s words.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agent_half  # noqa: E402
import flows_defs.production as production  # noqa: E402
from flows import Block, Done, Reaction, Registry, StepCtx, StepError  # noqa: E402

from test_link_loop import _StubDB  # noqa: E402

pytestmark = agent_half.required

REFS = {"uid": "7", "routine": "inbox", "reason": "human_session_required"}
PAUSED = "---\nenabled: false\ncron: '*/10 * * * *'\nprompt: Check my email.\npaused_reason: \"paused\"\n---\n"


def _ctx(refs: dict) -> StepCtx:
    r = Reaction("rid", "sid", "e", refs, "f", 1, "step", "running", 1, 0.0, None, None, None)
    return StepCtx(reaction=r, effect_key="rid:step", prior={}, clock_now=1_700_000_000.0, scratch={})


def _reg(monkeypatch, files: dict):
    monkeypatch.setattr(production, "ws_file", lambda uid, path, slug=None: files.get((uid, path)))
    reg = Registry()
    production.build(reg, _StubDB())
    return reg


def test_the_flow_is_the_only_consumer_of_routine_paused(monkeypatch):
    reg = _reg(monkeypatch, {})
    flows = reg.by_event[production.ROUTINE_PAUSED.name]
    assert [f.name for f in flows] == ["routine_paused"]
    assert flows[0].steps == ("await_routine",)
    assert reg.needs("await_routine") == frozenset({"agent"})


def test_a_paused_routine_blocks_which_is_the_one_queue_item(monkeypatch):
    reg = _reg(monkeypatch, {("7", "routines/inbox.md"): PAUSED})
    out = reg.steps["await_routine"](_ctx(dict(REFS)))
    assert isinstance(out, Block) and "inbox" in out.reason


@pytest.mark.parametrize("text", [None, "---\nenabled: true\ncron: '0 9 * * *'\nprompt: x\n---\n"])
def test_a_routine_switched_back_on_or_deleted_is_not_asked_about_again(monkeypatch, text):
    files = {("7", "routines/inbox.md"): text} if text else {}
    out = _reg(monkeypatch, files).steps["await_routine"](_ctx(dict(REFS)))
    assert isinstance(out, Done) and out.result["outcome"] == "no_longer_paused"


@pytest.mark.parametrize("refs", [{"uid": "7"}, {"routine": "inbox"}, {"uid": "7", "routine": "../x"}])
def test_refs_without_a_person_and_a_routine_name_are_refused_terminally(monkeypatch, refs):
    with pytest.raises(StepError) as e:
        _reg(monkeypatch, {}).steps["await_routine"](_ctx(refs))
    assert e.value.retryable is False


def test_the_queue_has_words_for_it():
    say = Path(__file__).resolve().parents[3] / "behavior" / "queue" / "routine_paused.human.md"
    assert "nothing is wrong with their connection" in say.read_text()
