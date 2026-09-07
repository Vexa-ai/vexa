"""worker harness — the redis serve() loop over a fake stream + injected turn (no docker, no claude).

Proves: the entrypoint turn runs first, each turn's UnitEvents XADD to the output Stream tagged with a
turn id + a turn-complete marker, interactive messages on the input Stream run in order, a `stop`
message exits, and an idle read reaps the harness (returns).
"""
from __future__ import annotations

import json
import pathlib

from llm.claude_code import ClaudeCodeHarness, _link_skills_into_workspace
from worker.worker import serve


class FakeStream:
    def __init__(self, inbox=None):
        self.out = []  # (topic, fields)
        self._inbox = list(inbox or [])

    def xadd(self, name, fields):
        self.out.append((name, fields))
        return str(len(self.out))

    def xread(self, streams, count=1, block=None):
        in_topic = next(iter(streams))
        if not self._inbox:
            return []  # idle → serve() returns
        eid, fields = self._inbox.pop(0)
        return [(in_topic, [(eid, fields)])]

    def events(self):
        return [json.loads(f["event"]) for _t, f in self.out]


def _turn(prompt):
    yield {"type": "message-delta", "text": f"re:{prompt}"}
    yield {"type": "commit", "sha": "abc"}


def _msg(eid, prompt):
    return (eid, {"turn": json.dumps({"prompt": prompt})})


def test_entrypoint_then_interactive_then_idle():
    s = FakeStream(inbox=[_msg("1-0", "again")])
    serve(s, out_topic="unit:u:out", in_topic="unit:u:in", turn=_turn,
          start={"entrypoint": {"inline": "hello"}}, idle_ms=10)
    evs = s.events()
    # t0 (entrypoint "hello"): accepted, delta, commit, turn-complete
    assert evs[0] == {"type": "turn-accepted", "turn_id": "t0"}
    assert evs[1] == {"type": "message-delta", "text": "re:hello", "turn_id": "t0"}
    assert evs[2]["type"] == "commit" and evs[2]["turn_id"] == "t0"
    # …carrying the turn's own step count (Vexa-ai/vexa#1622): a turn that called no tool says 0
    # rather than saying nothing, because "no steps" and "nobody counted" are different facts.
    assert evs[3] == {"type": "turn-complete", "turn_id": "t0", "steps": 0}
    # t1 (interactive "again")
    assert evs[4] == {"type": "turn-accepted", "turn_id": "t1"}
    assert evs[5] == {"type": "message-delta", "text": "re:again", "turn_id": "t1"}
    assert evs[7] == {"type": "turn-complete", "turn_id": "t1", "steps": 0}
    assert all(t == "unit:u:out" for t, _ in s.out)


def test_session_start_serves_inbox_without_entrypoint_turn():
    s = FakeStream(inbox=[_msg("1-0", "hi")])
    serve(s, out_topic="o", in_topic="i", turn=_turn,
          start={"session": {"ref": ".claude/.session"}}, idle_ms=10)
    evs = s.events()
    # no t0 — the first event is the interactive turn t1's liveness ack
    assert evs[0]["turn_id"] == "t1" and evs[0]["type"] == "turn-accepted"


def test_stop_message_exits_immediately():
    s = FakeStream(inbox=[("1-0", {"turn": json.dumps({"type": "stop"})}), _msg("2-0", "never")])
    serve(s, out_topic="o", in_topic="i", turn=_turn, start={}, idle_ms=10)
    assert s.out == []  # stop before any turn ran


def test_interactive_turn_ack_echoes_the_delivery_nonce():
    s = FakeStream(inbox=[("1-0", {"turn": json.dumps({"prompt": "warm", "nonce": "n-42"})})])
    serve(s, out_topic="o", in_topic="i", turn=_turn, start={}, idle_ms=10)
    evs = s.events()
    assert evs[0] == {"type": "turn-accepted", "turn_id": "t1", "nonce": "n-42"}


class CursorStream:
    """A fake honoring in-topic CURSOR semantics (ids compare as redis stream ids), so the boot
    tail-capture behavior is provable: entries already in the stream at boot are skipped; entries
    appended later (even during the entrypoint turn) are consumed."""

    def __init__(self, preloaded=None):
        self.out = []
        self.entries = list(preloaded or [])  # [(id, fields)] id-ordered

    @staticmethod
    def _key(eid):
        ms, _, seq = eid.partition("-")
        return (int(ms), int(seq or 0))

    def xadd(self, name, fields):
        self.out.append((name, fields))
        return str(len(self.out))

    def xrevrange(self, name, count=1):
        return list(reversed(self.entries))[:count]

    def xread(self, streams, count=1, block=None):
        topic, last = next(iter(streams.items()))
        if last == "$":
            return []  # nothing arrives "later" in a fake
        pending = [e for e in self.entries if self._key(e[0]) > self._key(last)]
        if not pending:
            return []
        return [(topic, [pending[0]])]

    def events(self):
        return [json.loads(f["event"]) for _t, f in self.out]


def test_boot_tail_capture_skips_the_predelivered_copy():
    # The dispatcher XADDs the message to unit:in BEFORE spawning (warm delivery). On a COLD spawn
    # the same prompt arrives as the entrypoint — the pre-delivered copy must be SKIPPED, not
    # replayed as a second turn.
    s = CursorStream(preloaded=[("5-0", {"turn": json.dumps({"prompt": "hello", "nonce": "n1"})})])
    serve(s, out_topic="o", in_topic="i", turn=_turn, start={"entrypoint": {"inline": "hello"}}, idle_ms=10)
    evs = s.events()
    assert [e["turn_id"] for e in evs] == ["t0", "t0", "t0", "t0"]  # exactly ONE turn ran
    assert evs[1]["text"] == "re:hello"


def test_message_landing_during_the_entrypoint_turn_is_consumed_not_lost():
    # Before the tail-capture fix serve() read from "$" AFTER the entrypoint turn — a message that
    # arrived while t0 ran was invisible forever (the lost-turn hang).
    s = CursorStream(preloaded=[("5-0", {"turn": json.dumps({"prompt": "hello"})})])

    def turn_with_midturn_arrival(prompt):
        if prompt == "hello":  # t0: a follow-up lands while this turn is still running
            s.entries.append(("6-0", {"turn": json.dumps({"prompt": "follow-up", "nonce": "n2"})}))
        yield {"type": "message-delta", "text": f"re:{prompt}"}

    serve(s, out_topic="o", in_topic="i", turn=turn_with_midturn_arrival,
          start={"entrypoint": {"inline": "hello"}}, idle_ms=10)
    evs = s.events()
    texts = [e.get("text") for e in evs if e["type"] == "message-delta"]
    assert texts == ["re:hello", "re:follow-up"]
    accepted = [e for e in evs if e["type"] == "turn-accepted"]
    assert accepted[1]["nonce"] == "n2"


def test_midturn_message_uses_the_active_harness_steering_seam():
    s = CursorStream(preloaded=[("5-0", {"turn": json.dumps({"prompt": "hello"})})])

    class SteeringHarness:
        def __init__(self):
            self.injected = []

        def midturn_enabled(self):
            return True

        def inject_user_message(self, text):
            self.injected.append(text)
            return True

    harness = SteeringHarness()

    def active_turn(prompt):
        s.entries.append(("6-0", {"turn": json.dumps({"prompt": "steer me", "nonce": "n2"})}))
        yield {"type": "message-delta", "text": "working"}
        yield {"type": "done", "reply": "done", "sessionId": "s1", "ok": True}

    serve(s, out_topic="o", in_topic="i", turn=active_turn,
          start={"entrypoint": {"inline": "hello"}}, idle_ms=10, harness=harness)
    assert harness.injected == ["steer me"]
    assert any(event["type"] == "turn-accepted" and event.get("injected") is True
               and event.get("nonce") == "n2" for event in s.events())
    assert any(event["type"] == "user-injected" and event["text"] == "steer me"
               for event in s.events())


# ── meeting mode — REMOVED (PRD decision 34) ──────────────────────────────────────────────────
#
# ~950 lines lived here: serve_meeting over the transcript stream, the card/note beat, the proc
# stream and its cursor, the view_end marker, the per-meeting workspace transcript FILE, the
# durable envelope, and the agents/meeting.md config knobs. All of it exercised the in-product
# inference pipeline, which is gone: the product runs no model calls of its own beside the
# agent, and a meeting reaches it over the MCP.


# ── workspace skills: governed skills/ symlinked into .claude/skills ──────────────────────────────

def test_link_skills_creates_dir_and_symlink(tmp_path):
    """Creates skills/ and points .claude/skills at it."""
    _link_skills_into_workspace(tmp_path)
    skills = tmp_path / "skills"
    link = tmp_path / ".claude" / "skills"
    assert skills.is_dir()
    assert link.is_symlink()
    assert pathlib.Path(link.readlink()) == skills


def test_link_skills_idempotent(tmp_path):
    """Running twice leaves a single correct symlink; an existing skill file survives."""
    (tmp_path / "skills" / "demo").mkdir(parents=True)
    (tmp_path / "skills" / "demo" / "SKILL.md").write_text("x")
    _link_skills_into_workspace(tmp_path)
    _link_skills_into_workspace(tmp_path)
    link = tmp_path / ".claude" / "skills"
    assert link.is_symlink()
    assert (link / "demo" / "SKILL.md").read_text() == "x"


def test_link_skills_does_not_clobber_real_skills_dir(tmp_path):
    """A pre-existing real skills/ dir + its files are preserved, not replaced."""
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "keep.md").write_text("keep")
    _link_skills_into_workspace(tmp_path)
    assert (tmp_path / "skills" / "keep.md").read_text() == "keep"


def test_link_skills_corrects_wrong_existing_symlink(tmp_path):
    """A stale .claude/skills symlink pointing elsewhere is repointed at skills/."""
    wrong = tmp_path / "elsewhere"
    wrong.mkdir()
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "skills").symlink_to(wrong, target_is_directory=True)
    _link_skills_into_workspace(tmp_path)
    link = claude / "skills"
    assert link.is_symlink()
    assert pathlib.Path(link.readlink()) == tmp_path / "skills"


def test_link_skills_keeps_real_claude_skills_dir(tmp_path):
    """If .claude/skills is a real dir (not a symlink), leave it untouched."""
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "x.md").write_text("real")
    _link_skills_into_workspace(tmp_path)
    link = tmp_path / ".claude" / "skills"
    assert not link.is_symlink() and link.is_dir()
    assert (link / "x.md").read_text() == "real"


def test_seed_claude_md_defers_copilot_steering_to_meeting_md():
    """Guard: the workspace-seed CLAUDE.md must not itself carry copilot behavior, and must name
    agents/meeting.md as the ONLY steering source when a workspace chooses to override the
    deployment default (the seed no longer ships agents/ — absent means defaults). CLAUDE.md is
    auto-loaded as project memory on every turn, so copilot steering here would be a second,
    conflicting source."""
    seed = pathlib.Path(__file__).resolve().parents[3] / "behavior" / "workspaces" / "default" / "CLAUDE.md"
    text = seed.read_text()
    lower = text.lower()
    # Names meeting.md as the governing source, with an exclusivity word ("exclusive"/"only source").
    assert "agents/meeting.md" in text
    assert "exclusiv" in lower or "only source" in lower
    # No copilot watch/ignore steering smuggled into CLAUDE.md (only the guard *mentions* the words).
    assert "surface only new entities" not in lower
    assert "real-time meeting behavior" not in lower

