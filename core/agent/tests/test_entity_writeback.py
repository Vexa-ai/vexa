"""The write-back phase and the entity index in context — PRD decision 24, items 2 and 3.

Offline: a fake stream, an injected turn and an injected phase. Nothing here needs docker, a model
or the MCP; what is being proved is the CONTRACT — when the phase runs, what of it the person sees,
and that it can never cost them their answer.
"""
from __future__ import annotations

import json

import pytest

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


# ── when the phase runs (the cheap-turn skip) ────────────────────────────────────────────────────

def test_a_turn_that_called_a_tool_always_writes_back(monkeypatch):
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)
    assert engine.should_write_back("ok", tool_calls=1) is True


def test_a_short_turn_with_no_tool_call_is_skipped(monkeypatch):
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)
    assert engine.should_write_back("thanks", tool_calls=0) is False


def test_a_long_turn_with_no_tool_call_still_writes_back(monkeypatch):
    """Either signal alone is a turn that can have learned something: a long message carries facts
    with no tool call at all. The floor is on the PERSON's words, never on the agent's reply."""
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)
    assert engine.should_write_back(" ".join(["word"] * 60), tool_calls=0) is True


def test_the_floor_is_configurable(monkeypatch):
    monkeypatch.setenv("VEXA_WRITEBACK_MIN_TOKENS", "3")
    assert engine.should_write_back("one two three", tool_calls=0) is True
    monkeypatch.setenv("VEXA_WRITEBACK_MIN_TOKENS", "99")
    assert engine.should_write_back("one two three", tool_calls=0) is False


def test_the_phase_can_be_switched_off_entirely(monkeypatch):
    monkeypatch.setenv("VEXA_WRITEBACK", "0")
    assert engine.should_write_back("anything at all", tool_calls=5) is False


# ── what the person sees of it ───────────────────────────────────────────────────────────────────

def test_step_lines_pass_through_prose_and_the_tab_do_not():
    raw = [
        {"type": "message-delta", "text": "I have recorded three entities"},
        {"type": "tool-call", "tool": "mcp__vexa__entity_upsert", "args": {}, "callId": "c1"},
        {"type": "tool-result", "callId": "c1", "ok": True, "summary": "{...}"},
        {"type": "artifact", "workspace": "d", "path": "kg/entities/person/x.md", "focus": True},
        {"type": "commit", "sha": "abc"},
        {"type": "done", "reply": "nothing new", "sessionId": "s"},
    ]
    out = list(engine.writeback_events(iter(raw)))
    assert [e["type"] for e in out] == ["tool-call", "tool-result", "commit"]
    assert all(e["phase"] == "writeback" for e in out)


# ── the loop ─────────────────────────────────────────────────────────────────────────────────────

def _serve(stream, turn, writeback):
    serve(stream, out_topic="out", in_topic="in", turn=turn,
          start={"entrypoint": {"inline": "who is Olga Avramenko?"}}, idle_ms=1,
          writeback=writeback)


def test_the_phase_runs_after_the_answer_and_before_turn_complete(monkeypatch):
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)

    def turn(_p):
        yield {"type": "tool-call", "tool": "Read", "args": {}, "callId": "a"}
        yield {"type": "message-delta", "text": "She chairs the DNA TSC."}
        yield {"type": "done", "reply": "She chairs the DNA TSC.", "sessionId": "s1"}

    def writeback(_p):
        yield {"type": "tool-call", "tool": "mcp__vexa__entity_upsert", "args": {}, "callId": "b"}
        yield {"type": "message-delta", "text": "recorded 1 entity"}
        yield {"type": "done", "reply": "done", "sessionId": "s1"}

    s = FakeStream()
    _serve(s, turn, writeback)
    kinds = [(e["type"], e.get("phase")) for e in events(s)]
    answer = kinds.index(("message-delta", None))
    phase = kinds.index(("tool-call", "writeback"))
    complete = kinds.index(("turn-complete", None))
    assert answer < phase < complete
    # the phase's own prose never reaches the person
    assert ("message-delta", "writeback") not in kinds


def test_a_cheap_turn_runs_no_phase(monkeypatch):
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)
    monkeypatch.setenv("VEXA_WRITEBACK_MIN_TOKENS", "40")
    calls = []

    def turn(_p):
        yield {"type": "done", "reply": "you're welcome", "sessionId": "s"}

    def writeback(_p):
        calls.append(1)
        yield {"type": "done", "reply": "", "sessionId": "s"}

    s = FakeStream()
    serve(s, out_topic="out", in_topic="in", turn=turn,
          start={"entrypoint": {"inline": "thanks"}}, idle_ms=1, writeback=writeback)
    assert calls == []


def test_a_phase_that_raises_never_costs_the_person_their_turn(monkeypatch):
    """Bookkeeping is not worth a dead session. The answer has already streamed by the time this
    runs, so the only thing a failure may do is not happen — and say so in the log."""
    monkeypatch.delenv("VEXA_WRITEBACK", raising=False)

    def turn(_p):
        yield {"type": "tool-call", "tool": "Read", "args": {}, "callId": "a"}
        yield {"type": "done", "reply": "an answer", "sessionId": "s"}

    def writeback(_p):
        raise RuntimeError("the entity endpoint is down")
        yield  # pragma: no cover

    s = FakeStream()
    _serve(s, turn, writeback)
    assert [e["type"] for e in events(s)][-1] == "turn-complete"


# ── the index in context ─────────────────────────────────────────────────────────────────────────

def test_every_dispatch_carries_the_rule_and_the_index(tmp_path):
    from shared import entities as E
    desk = tmp_path / "desk-1"
    desk.mkdir()
    E.upsert_entity(desk, "person", "Olga Avramenko", ["Chairs the TSC."], "the call",
                    today="2026-09-02")
    E.write_index(desk, "desk-1")
    txt = engine.entity_index_preamble([{"slug": "desk-1", "path": str(desk), "write": True}])
    assert "A name without a page gets one NOW" in txt
    assert "Facts carry a source" in txt
    assert "kg/MISSING.md`, never invented" in txt
    assert "Olga Avramenko" in txt and "kg/entities/person/olga-avramenko.md" in txt


def test_the_index_renders_live_when_the_file_has_never_been_written(tmp_path):
    """A first dispatch into a workspace nobody has upserted must still see what it holds — being
    told 'nothing exists' when three pages do is how a duplicate page gets created."""
    from shared import entities as E
    desk = tmp_path / "desk-2"
    E.upsert_entity(desk, "company", "Vexa", ["Ships a meeting bot."], "the README")
    assert not (desk / "kg" / "INDEX.md").exists()
    txt = engine.entity_index_preamble([{"slug": "desk-2", "path": str(desk), "write": True}])
    assert "Vexa" in txt


def test_a_read_only_mount_is_not_offered_as_a_place_to_write_entities(tmp_path):
    g = tmp_path / "_global"
    (g / "kg" / "entities" / "company").mkdir(parents=True)
    (g / "kg" / "entities" / "company" / "acme.md").write_text("---\ntype: company\ntitle: Acme\n---\n")
    assert engine.entity_index_preamble(
        [{"slug": "_global", "path": str(g), "write": False, "role": "global"}]) == ""


def test_the_preamble_ships_on_the_turn_prompt(tmp_path, monkeypatch):
    """The rule reaches EVERY turn, for the reason kg_links does: a rule that reaches only composed
    openings is a rule about nothing, and a chat the person opened themselves is where names land."""
    seen = {}

    def fake_run(work, prompt, harness, **kw):
        seen["prompt"] = prompt
        yield {"type": "done", "reply": "ok", "sessionId": "s"}

    monkeypatch.setattr(engine, "run_harness_turn", fake_run)
    monkeypatch.setattr(engine, "active_mounts",
                        lambda: [{"slug": "desk-1", "path": str(tmp_path), "write": True,
                                  "primary": True}])
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)

    class H:
        def prepare(self, work, chat_root=None):
            pass

        def transcript_bytes(self, work, sid):
            return 0

    list(engine.run_turn_over_workspace(tmp_path, "hello", harness=H(), commit=False))
    assert "A name without a page gets one NOW" in seen["prompt"]
