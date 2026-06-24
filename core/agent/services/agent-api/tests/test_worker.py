"""worker harness — the redis serve() loop over a fake stream + injected turn (no docker, no claude).

Proves: the entrypoint turn runs first, each turn's UnitEvents XADD to the output Stream tagged with a
turn id + a turn-complete marker, interactive messages on the input Stream run in order, a `stop`
message exits, and an idle read reaps the harness (returns).
"""
from __future__ import annotations

import json

from agent_api.worker import serve


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
    # t0 (entrypoint "hello"): delta, commit, turn-complete
    assert evs[0] == {"type": "message-delta", "text": "re:hello", "turn_id": "t0"}
    assert evs[1]["type"] == "commit" and evs[1]["turn_id"] == "t0"
    assert evs[2] == {"type": "turn-complete", "turn_id": "t0"}
    # t1 (interactive "again")
    assert evs[3] == {"type": "message-delta", "text": "re:again", "turn_id": "t1"}
    assert evs[5] == {"type": "turn-complete", "turn_id": "t1"}
    assert all(t == "unit:u:out" for t, _ in s.out)


def test_session_start_serves_inbox_without_entrypoint_turn():
    s = FakeStream(inbox=[_msg("1-0", "hi")])
    serve(s, out_topic="o", in_topic="i", turn=_turn,
          start={"session": {"ref": ".claude/.session"}}, idle_ms=10)
    evs = s.events()
    # no t0 — the first event is the interactive turn t1
    assert evs[0]["turn_id"] == "t1"


def test_stop_message_exits_immediately():
    s = FakeStream(inbox=[("1-0", {"turn": json.dumps({"type": "stop"})}), _msg("2-0", "never")])
    serve(s, out_topic="o", in_topic="i", turn=_turn, start={}, idle_ms=10)
    assert s.out == []  # stop before any turn ran


# ── meeting mode: consume transcript Stream → gate → emit cards ───────────────────────────────────

from agent_api.worker import parse_cards, serve_meeting  # noqa: E402


class MeetingStream:
    def __init__(self, inbox):
        self.out = []
        self._inbox = list(inbox)

    def xadd(self, name, fields):
        self.out.append((name, fields))
        return str(len(self.out))

    def xread(self, streams, count=1, block=None):
        tname = next(iter(streams))
        if not self._inbox:
            return []
        batch, self._inbox = self._inbox, []
        return [(tname, list(batch))]

    def events(self):
        return [json.loads(f["event"]) for _t, f in self.out]


def _seg(speaker, text, completed=True):
    return {"speaker": speaker, "text": text, "completed": completed}


def _transcript(eid, *segs):
    return (eid, {"payload": json.dumps({"type": "transcription", "segments": list(segs)})})


def _card_turn(segments):
    yield {"type": "message-delta", "text": f"saw {len(segments)} lines"}
    yield {"type": "card", "card": {"kind": "person", "title": "Priya", "body": "new attendee"}}


def test_serve_meeting_emits_card_on_new_speaker_gate():
    s = MeetingStream(inbox=[_transcript("1-0", _seg("Jane", "hi"), _seg("Raj", "hello"))])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="unit:u:out", card_turn=_card_turn, idle_ms=10)
    evs = s.events()
    assert any(e["type"] == "card" and e["card"]["title"] == "Priya" for e in evs)
    assert any(e["type"] == "turn-complete" for e in evs)
    assert all(t == "unit:u:out" for t, _ in s.out)


def test_serve_meeting_reaps_on_session_end():
    s = MeetingStream(inbox=[
        _transcript("1-0", _seg("Jane", "a"), _seg("Jane", "b")),
        ("2-0", {"payload": json.dumps({"type": "session_end"})}),
    ])
    serve_meeting(s, transcript_stream="tc:m1", out_topic="o", card_turn=_card_turn, idle_ms=10)
    # the session_end flush emitted the buffered beat, then returned
    assert any(e["type"] == "card" for e in s.events())


def test_parse_cards_tolerant():
    assert parse_cards('[{"kind":"person","title":"Priya","body":"x"}]')[0]["title"] == "Priya"
    assert parse_cards('Here are the cards:\n[{"kind":"action","title":"Send quote","body":"by Fri"}] done')[0]["kind"] == "action"
    assert parse_cards("nothing salient") == []
    assert parse_cards("[]") == []
    assert parse_cards(None) == []
