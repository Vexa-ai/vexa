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
