"""THE EXPRESS LANE (2026-10-13, app.dev): a chat busy with a long turn (workspace_write, 18+ steps);
the founder pasted a Zoom link, then "send bot". Both sat queued behind the turn and the bot never
went. A meeting link or an explicit bot command is acted on at once, beside the running turn, with
one line in the chat; the running turn is told; everything else still queues."""
from __future__ import annotations

import json

from worker import engine, express
from tests.conftest import signed_turn

ZOOM = "https://us05web.zoom.us/j/92446951537?pwd=abc"


class Stream:
    def __init__(self, entries):
        self.entries = list(entries)
        self.out = []
        self.kv = {}

    def xrange(self, topic, *a, **kw):
        return list(self.entries)

    def xrevrange(self, topic, count=1):
        return list(reversed(self.entries))[:count]

    def xread(self, streams, count=1, block=None):
        cur = list(streams.values())[0]
        n = lambda e: tuple(int(x) for x in e.split("-"))
        after = [e for e in self.entries if n(e[0]) > n(cur)][:count]
        return [("in", after)] if after else None

    def xadd(self, topic, fields):
        self.out.append(json.loads(fields["event"]))

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v


def _entry(i, prompt):
    return (f"{i}-0", signed_turn({"type": "message", "prompt": prompt, "nonce": f"n{i}"}))


class FakeSend:
    def __init__(self):
        self.sent = []

    def __call__(self, link):
        self.sent.append(link)
        outcome = "already_there" if len(self.sent) > 1 else "requested"
        ev = {"type": "meeting-link", "outcome": outcome, "url": link, "platform": "zoom",
              "native_meeting_id": "92446951537", "status": "awaiting_admission"}
        return [ev], ev


def test_a_link_and_send_bot_queued_behind_a_long_turn_are_acted_on_before_it_ends():
    send = FakeSend()
    lane = express.Express(send=send)
    stream = Stream([_entry(1, "rewrite the plan page")])
    later = [_entry(2, ZOOM), _entry(3, "send bot"), _entry(4, "and then summarise it")]
    order = []

    def turn(prompt):
        order.append(prompt)
        for i in range(20):                     # a long turn: 20 events, the queue arrives meanwhile
            if i == 3 and later:
                stream.entries.extend(later)
                later.clear()
            yield {"type": "tool-call", "tool": "workspace_write", "callId": str(i)}
        yield {"type": "done", "ok": True, "reply": "done"}

    engine.serve(stream, in_topic="in", out_topic="out", start={"entrypoint": {"inline": "rewrite the plan page", "nonce": "n1"}},
                 turn=turn, idle_ms=1, express=lane, inbox_cursor="cur")
    assert send.sent == [ZOOM, ZOOM], "the link is sent, and 'send bot' re-asks for the same meeting"
    lines = [e.get("text") for e in stream.out if e.get("type") == "message-delta" and e.get("express")]
    assert lines[0] == "Bot sent to Zoom 92446951537 — waiting for admission"
    assert lines[1] == "A bot is already in Zoom 92446951537"
    # both express answers land before the long turn completes
    first_complete = next(i for i, e in enumerate(stream.out) if e.get("type") == "turn-complete" and e.get("turn_id") == "t0")
    express_done = [i for i, e in enumerate(stream.out) if e.get("type") == "turn-complete" and e.get("express")]
    assert len(express_done) == 2 and max(express_done) < first_complete
    # the ordinary message still queues, and runs after — told what happened meanwhile
    assert order[0] == "rewrite the plan page" and order[-1].endswith("and then summarise it")
    assert "Bot sent to Zoom 92446951537" in order[-1]


def test_an_ordinary_message_is_never_express():
    lane = express.Express(send=FakeSend())
    assert not lane.claims("can you send me the notes from the bot meeting yesterday and a summary")
    assert not lane.claims("send bot")          # no meeting known yet: nothing to send to
    assert lane.claims(ZOOM) and lane.claims(f"{ZOOM} send bot") and lane.claims(f"join {ZOOM}")


def test_send_now_stops_the_running_turn_and_runs_that_message_next_once():
    stream = Stream([_entry(1, "rewrite the plan page")])
    later = [_entry(2, "first queued"), _entry(3, "this one now")]
    order = []

    def turn(prompt):
        order.append(prompt)
        for i in range(30):
            if i == 2 and later:
                stream.entries.extend(later)
                later.clear()
                stream.kv["now"] = "3-0"        # the person pressed "send now" on the second row
            yield {"type": "tool-call", "tool": "workspace_write", "callId": str(i)}
        yield {"type": "done", "ok": True, "reply": "done"}

    engine.serve(stream, in_topic="in", out_topic="out", start={"entrypoint": {"inline": "rewrite the plan page", "nonce": "n1"}},
                 turn=turn, idle_ms=1, inbox_cursor="cur", send_now_key="now")
    assert order == ["rewrite the plan page", "this one now", "first queued"]
    assert any(e.get("type") == "turn-truncated" and e.get("turn_id") == "t0" for e in stream.out)
