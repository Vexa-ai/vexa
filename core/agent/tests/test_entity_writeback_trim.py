"""The write-back phase's TRIM — the mechanical pre-pass and the hard budget.

The phase was right and slow: 118-136s on Haiku against a 31-47s answer, so the worker stayed busy
three times as long and every message the person sent meanwhile queued behind it (F45/F46). These
prove the two halves of the fix — that the commonest turn never reaches a model at all, and that
the budget is enforced rather than requested.
"""
from __future__ import annotations

import json
import time

import pytest

from shared import entities as E
from worker import engine
from worker.worker import serve


class FakeStream:
    def __init__(self, inbox=None):
        self.out = []
        self._inbox = list(inbox or [])

    def xadd(self, name, fields):
        self.out.append((name, fields))
        return str(len(self.out))

    def xread(self, streams, count=1, block=None):
        in_topic = next(iter(streams))
        if not self._inbox:
            return []
        eid, fields = self._inbox.pop(0)
        return [(in_topic, [(eid, fields)])]


def events(stream):
    return [json.loads(f["event"]) for _t, f in stream.out]


# ── the pre-pass ─────────────────────────────────────────────────────────────────────────────────

def test_names_come_out_of_prose_mechanically():
    assert E.candidate_names("Cottalango Leon chairs it for Sony Pictures Imageworks.") == \
        ["Cottalango Leon", "Sony Pictures Imageworks"]


def test_a_wikilinked_name_is_still_a_candidate_for_the_pre_pass():
    """The brackets are not the question — the index is. A `[[Name]]` with no page renders as an
    inert 'not found' chip, which is the same failure wearing brackets."""
    assert E.candidate_names("[[Olga Avramenko]] chairs it.", mask_linked=False) == \
        ["Olga Avramenko"]
    assert E.candidate_names("[[Olga Avramenko]] chairs it.", mask_linked=True) == []


def test_missing_names_subtracts_what_the_desk_already_holds(tmp_path):
    E.upsert_entity(tmp_path, "person", "Olga Avramenko", ["a fact"], "a source")
    got = E.missing_names([tmp_path], ["Olga Avramenko met Cottalango Leon."])
    assert got == ["Cottalango Leon"]


def test_missing_names_is_capped_because_the_list_is_the_budget(tmp_path):
    # alphabetic on purpose: the extractor is a PROPER-NAME regex and does not match digits
    names = [f"Alpha Beta{chr(97 + i)}{chr(97 + i)}" for i in range(20)]
    text = " ".join(f"{n} spoke." for n in names)
    assert len(E.missing_names([tmp_path], [text], limit=5)) == 5


def test_a_turn_whose_names_all_have_pages_never_reaches_a_model(tmp_path):
    E.upsert_entity(tmp_path, "person", "Olga Avramenko", ["a fact"], "a source")
    mounts = [{"slug": "d", "path": str(tmp_path), "write": True}]
    assert engine.writeback_candidates(["Olga Avramenko chairs it."], mounts) == []


def test_a_read_only_mount_is_not_searched_for_candidates(tmp_path):
    assert engine.writeback_candidates(["Olga Avramenko chairs it."],
                                       [{"slug": "_global", "path": str(tmp_path),
                                         "write": False}]) == []


# ── the four gates ───────────────────────────────────────────────────────────────────────────────

def test_no_candidates_means_no_phase(monkeypatch):
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)
    assert engine.should_write_back("a long message " * 20, tool_calls=3, candidates=[]) is False
    assert engine.should_write_back("a long message " * 20, tool_calls=3,
                                    candidates=["Olga Avramenko"]) is True


def test_a_turn_that_already_upserted_gets_no_phase(monkeypatch):
    """The note step calls `entity_upsert` itself. A phase after it is a second model call to find
    out that the work is done — which is the phase on exactly the turns that need it least."""
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)
    assert engine.should_write_back("anything", tool_calls=4, upserts=2,
                                    candidates=["Olga"]) is False


def test_the_legacy_call_shape_still_gates_on_cheapness(monkeypatch):
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)
    assert engine.should_write_back("thanks", tool_calls=0) is False
    assert engine.should_write_back("ok", tool_calls=1) is True


# ── the budget ───────────────────────────────────────────────────────────────────────────────────

def test_the_tool_call_budget_stops_the_phase_and_says_so():
    def many():
        for i in range(50):
            yield {"type": "tool-call", "tool": "mcp__vexa__entity_upsert", "callId": str(i)}

    out = list(engine.bounded(many(), max_tool_calls=3, max_seconds=99))
    assert sum(1 for e in out if e["type"] == "tool-call") == 3
    assert out[-1]["type"] == "writeback-truncated" and out[-1]["reason"] == "tool-call budget"


def test_the_time_budget_stops_the_phase():
    def slow():
        for i in range(50):
            time.sleep(0.01)
            yield {"type": "message-delta", "text": str(i)}

    out = list(engine.bounded(slow(), max_tool_calls=99, max_seconds=0.03))
    assert out[-1]["type"] == "writeback-truncated" and out[-1]["reason"] == "time budget"
    assert len(out) < 50


def test_the_budget_CLOSES_the_generator_so_the_subprocess_dies():
    """A budget that only stops reading leaves the CLI running and the worker busy — the exact
    stall the budget exists to prevent, one layer down."""
    closed = []

    def gen():
        try:
            while True:
                yield {"type": "tool-call", "tool": "x", "callId": "1"}
        except GeneratorExit:
            closed.append(True)
            raise

    list(engine.bounded(gen(), max_tool_calls=1, max_seconds=99))
    assert closed == [True]


def test_the_budget_is_configurable(monkeypatch):
    monkeypatch.setenv("VEXA_WRITEBACK_MAX_TOOL_CALLS", "3")
    monkeypatch.setenv("VEXA_WRITEBACK_MAX_SECONDS", "7")
    assert engine.writeback_budget() == (3, 7.0)


def test_the_truncation_marker_is_not_shown_as_prose():
    out = list(engine.writeback_events(iter([
        {"type": "writeback-truncated", "reason": "time budget"},
        {"type": "message-delta", "text": "recorded three"},
    ])))
    assert [e["type"] for e in out] == ["writeback-truncated"]


# ── the loop, end to end ─────────────────────────────────────────────────────────────────────────

def test_the_phase_is_handed_the_candidate_list_not_the_prompt(tmp_path, monkeypatch):
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)
    monkeypatch.setenv("VEXA_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.delenv("VEXA_MOUNTS", raising=False)
    got = {}

    def turn(_p):
        yield {"type": "tool-call", "tool": "Read", "args": {}, "callId": "a"}
        yield {"type": "message-delta", "text": "Cottalango Leon chairs the DNA TSC."}
        yield {"type": "done", "reply": "x", "sessionId": "s"}

    def writeback(candidates):
        got["candidates"] = candidates
        yield {"type": "tool-call", "tool": "mcp__vexa__entity_upsert", "callId": "b"}
        yield {"type": "done", "reply": "", "sessionId": "s"}

    s = FakeStream()
    serve(s, out_topic="out", in_topic="in", turn=turn,
          start={"entrypoint": {"inline": "what happened?"}}, idle_ms=1, writeback=writeback)
    assert "Cottalango Leon" in got["candidates"]
    assert engine.writeback_prompt(got["candidates"]).count("Cottalango Leon") == 1


def test_a_turn_that_names_nobody_new_runs_no_model_call(tmp_path, monkeypatch):
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)
    monkeypatch.setenv("VEXA_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.delenv("VEXA_MOUNTS", raising=False)
    calls = []

    def turn(_p):
        yield {"type": "tool-call", "tool": "Read", "args": {}, "callId": "a"}
        yield {"type": "message-delta", "text": "the build is green and the tests pass."}
        yield {"type": "done", "reply": "x", "sessionId": "s"}

    def writeback(_c):
        calls.append(1)
        yield {"type": "done", "reply": "", "sessionId": "s"}

    s = FakeStream()
    serve(s, out_topic="out", in_topic="in", turn=turn,
          start={"entrypoint": {"inline": "how is the build?"}}, idle_ms=1, writeback=writeback)
    assert calls == []
    assert [e["type"] for e in events(s)][-1] == "turn-complete"


# ── the budget over a REAL subprocess ────────────────────────────────────────────────────────────

def test_a_phase_over_budget_leaves_no_child_process(tmp_path, monkeypatch):
    """The budget must KILL the CLI, not merely stop reading it.

    This is the whole point of the trim and it is the one part that cannot be proved with a fake: a
    budget that stops consuming while the process keeps running leaves the worker exactly as busy as
    it was, which is the stall (F45/F46) the budget exists to remove. Worse, the first shape of
    `_exec_subprocess` would have HUNG here — `finally: proc.wait()` on a process nobody is reading
    blocks forever — so the budget would have produced a permanent stall instead of a temporary one.

    Runs the real chain two generator levels deep: `parse_stream_json(_exec_subprocess(...))` under
    `bounded()`. Closing the outer cascades GeneratorExit down to the subprocess's `finally`.

    ⚠ THE CHILD SLEEPS AFTER ITS LAST LINE, and that detail is the test. The first version looped
    forever writing, so closing the pipe killed it with SIGPIPE and the test passed with the kill
    path deleted — it was measuring the operating system. A CLI waiting on a model writes nothing
    for tens of seconds at a time, notices no closed pipe, and is exactly the case the kill exists
    for. With the kill removed this test HANGS on `proc.wait()`, which is the failure it names.
    """
    import os
    import subprocess as sp

    from llm import claude_code as cc

    seen = []

    class _Recorder:
        TimeoutExpired = sp.TimeoutExpired
        PIPE, STDOUT = sp.PIPE, sp.STDOUT

        @staticmethod
        def Popen(*a, **kw):
            proc = sp.Popen(*a, **kw)
            seen.append(proc)
            return proc

    monkeypatch.setattr(cc, "subprocess", _Recorder)

    monkeypatch.setenv("VEXA_HARNESS_REAP_GRACE_SEC", "0.3")
    # A CLI that emits its lines and then goes quiet, the way the real one does while it waits on a
    # model. It never exits on its own and it never notices a closed pipe.
    line = ('{"type":"assistant","message":{"content":[{"type":"tool_use",'
            '"name":"mcp__vexa__entity_upsert","id":"c","input":{}}]}}')
    argv = ["sh", "-c", f"echo '{line}'; echo '{line}'; echo '{line}'; sleep 300"]

    events = cc.parse_stream_json(cc._exec_subprocess(argv, str(tmp_path)))
    out = list(engine.bounded(events, max_tool_calls=2, max_seconds=10))

    assert sum(1 for e in out if e["type"] == "tool-call") == 2
    assert out[-1]["type"] == "writeback-truncated"
    assert len(seen) == 1
    proc = seen[0]
    assert proc.poll() is not None, "the CLI is still running after the budget stopped reading it"
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)


def test_a_phase_inside_its_budget_is_waited_for_not_killed(tmp_path, monkeypatch):
    """The reap must not truncate a normal turn: a CLI that finishes on its own is waited for, and
    every line it wrote arrives."""
    import subprocess as sp

    from llm import claude_code as cc

    line = ('{"type":"assistant","message":{"content":[{"type":"tool_use",'
            '"name":"Read","id":"c","input":{}}]}}')
    argv = ["sh", "-c", f"for i in 1 2 3; do echo '{line}'; done"]
    out = list(engine.bounded(cc.parse_stream_json(cc._exec_subprocess(argv, str(tmp_path))),
                              max_tool_calls=99, max_seconds=10))
    assert sum(1 for e in out if e["type"] == "tool-call") == 3
    assert not any(e["type"] == "writeback-truncated" for e in out)
