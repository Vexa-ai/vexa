"""The boot anchor skips the entrypoint's own copy — and nothing else.

⚠ 2026-09-02, measured on a scratch session. Two rapid sends to a COLD session: both POSTs
returned 200, both messages landed in the worker's in-topic, and only ONE turn ran. The worker
anchored its in-topic cursor at the boot-time TAIL, which skips everything already in the stream
— correct when the only thing there is the entrypoint's duplicate, silently wrong the moment
there are two.

The dispatcher now stamps the delivery nonce on BOTH copies of the entrypoint (the XADD and the
spawn spec), so the duplicate is identifiable rather than merely recent.
"""
from __future__ import annotations

import json


from worker import engine
from tests.conftest import signed_turn


class FakeStream:
    """Enough redis-stream surface for the boot path: xrange/xrevrange/xread/xadd."""

    def __init__(self, entries):
        self.entries = list(entries)          # [(id, {"turn": json})]
        self.out = []

    def xrange(self, topic, *a, **kw):
        return list(self.entries)

    def xrevrange(self, topic, count=1):
        return list(reversed(self.entries))[:count]

    def xread(self, streams, count=1, block=None):
        return None                            # nothing new → the serve loop exits

    def xadd(self, topic, fields):
        self.out.append(fields)


def _entry(i, prompt, nonce):
    return (f"{i}-0", signed_turn({"type": "message", "prompt": prompt, "nonce": nonce}))


def _turns(stream):
    """The prompts actually RUN, in order, read off the out-stream's turn-accepted events."""
    ids = []
    for f in stream.out:
        ev = json.loads(f["event"])
        if ev.get("type") == "turn-accepted":
            ids.append(ev.get("turn_id"))
    return ids


def _run(monkeypatch, stream, start):
    ran = []

    def fake_turn(prompt):
        ran.append(prompt)
        return iter(())

    engine.serve(stream, in_topic="in", out_topic="out", start=start, turn=fake_turn, idle_ms=1)
    return ran


def test_two_messages_before_boot_produce_two_turns_in_order(monkeypatch):
    nonce = "u1:111"
    stream = FakeStream([_entry(1, "first", nonce), _entry(2, "second", "u1:222")])
    start = {"entrypoint": {"inline": "first", "nonce": nonce}}
    ran = _run(monkeypatch, stream, start)
    assert ran == ["first", "second"], "the queued second message must run, after the entrypoint"
    assert _turns(stream) == ["t0", "t1"]


def test_the_entrypoint_copy_is_not_run_twice(monkeypatch):
    nonce = "u1:111"
    stream = FakeStream([_entry(1, "only", nonce)])
    start = {"entrypoint": {"inline": "only", "nonce": nonce}}
    ran = _run(monkeypatch, stream, start)
    assert ran == ["only"], "the pre-delivered duplicate must be skipped, not re-run"
    assert _turns(stream) == ["t0"]


def test_three_waiting_messages_keep_arrival_order(monkeypatch):
    nonce = "u1:111"
    stream = FakeStream([_entry(1, "a", nonce), _entry(2, "b", "n2"), _entry(3, "c", "n3")])
    ran = _run(monkeypatch, stream, {"entrypoint": {"inline": "a", "nonce": nonce}})
    assert ran == ["a", "b", "c"]


def test_without_a_nonce_the_old_tail_anchor_still_applies(monkeypatch):
    # An older dispatcher, or a session-only start: fall back to the previous behaviour rather than
    # inventing a new one. Everything present at boot is skipped, as before.
    stream = FakeStream([_entry(1, "x", "n1"), _entry(2, "y", "n2")])
    ran = _run(monkeypatch, stream, {"entrypoint": {"inline": "x"}})
    assert ran == ["x"]


class CursorStream(FakeStream):
    """…plus the published inbox cursor (`shared/units.inbox_cursor_key`): how far the previous
    worker of this unit read."""

    def __init__(self, entries, cursor=None):
        super().__init__(entries)
        self.kv = {"cur": cursor} if cursor else {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value):
        self.kv[key] = value


def test_a_cold_boot_never_reruns_what_a_previous_worker_already_took():
    """A unit's in-topic outlives its worker. A worker that boots (idle exit, a restart, a drain)
    must start after the cursor the previous worker published, or every message the person sent
    in that chat runs again — measured on app.dev, 2026-10-13: a restarted worker re-ran 33 old turns
    in one chat, ran its transcript copy ahead of the store and got its replies refused."""
    nonce = "u1:333"
    stream = CursorStream([_entry(1, "old one", "u1:111"), _entry(2, "old two", "u1:222"),
                           _entry(3, "new", nonce), _entry(4, "queued after", "u1:444")], cursor="2-0")
    start = {"entrypoint": {"inline": "new", "nonce": nonce}}
    ran = []
    engine.serve(stream, in_topic="in", out_topic="out", start=start, inbox_cursor="cur",
                 turn=lambda p: ran.append(p) or iter(()), idle_ms=1)
    assert ran == ["new", "queued after"]
    assert stream.kv["cur"] == "4-0"


def test_with_no_published_cursor_every_waiting_message_still_runs():
    nonce = "u1:111"
    stream = CursorStream([_entry(1, "first", nonce), _entry(2, "second", "u1:222")])
    ran = []
    engine.serve(stream, in_topic="in", out_topic="out", start={"entrypoint": {"inline": "first", "nonce": nonce}},
                 inbox_cursor="cur", turn=lambda p: ran.append(p) or iter(()), idle_ms=1)
    assert ran == ["first", "second"]
