"""A MESSAGE SENT WHILE THE CHAT IS STILL ANSWERING IS ALWAYS ANSWERED — and the answer is watched.

The founder, 2026-10-10, on the dogfood stack: *"sometimes chat does not answer if asked while it's
not yet answered."* The message showed in the chat; the worker even answered it; nobody was
reading the Stream it answered on. Three gaps, each one alone enough to lose the answer:

  1. **The view closed on the wrong turn's end.** The worker finishes a turn's write-back AFTER
     its `done`, on a thread, and takes the next message meanwhile — so on one output Stream turn
     t2 begins, and may finish, before t1's `turn-complete`. The relay closed every view on the
     first `turn-complete` it met, cutting off the answer the person had just asked for.
  2. **"Is anything queued?" was the only question an idle chat asked.** A message submitted
     mid-turn leaves the inbox the instant the worker takes it, which is the instant the turn in
     front ends — before a chat whose view just closed can ask. `GET /api/chat/pending?after=`
     now also says how many turns STARTED after the last event that chat read (`taken`).
  3. **A submission could land on a different unit.** A mid-turn model change or new workspace
     raises the stale-mounts flag; the submission stepped the generation and ran on a second
     worker whose Stream the open view never reads. A submission now reads the generation.
"""
from __future__ import annotations

import json
import sys

from control_plane import api_shared
from shared import units

from .test_inbox import _HEADERS, _UNIT, _submit, client, fake_redis  # noqa: F401 — fixtures


# ── 1. the relay follows every turn that began on the view ────────────────────────────────────

def _read_frames(frames: list[dict]) -> list[dict]:
    import shared.adapters as adapters

    class _FakeRedis:
        def __init__(self):
            self.i = 0

        def xread(self, streams, count=50, block=None):
            if self.i >= len(frames):
                return []
            out = [("t", [(f"{self.i + 1}-0", {"event": json.dumps(frames[self.i])})])]
            self.i += 1
            return out

    class _FakeModule:
        @staticmethod
        def from_url(_url, decode_responses=True):
            return _FakeRedis()

    saved = sys.modules.get("redis")
    sys.modules["redis"] = _FakeModule
    try:
        return [ev for ev, _id in adapters.RedisStreamReader("redis://x", idle_giveup_ms=1).read("u")
                if ev is not None]
    finally:
        if saved is None:
            sys.modules.pop("redis", None)
        else:
            sys.modules["redis"] = saved


def test_a_view_stays_open_for_the_turn_that_began_before_the_one_in_front_completed():
    """The founder's session, as the Stream recorded it: B was taken while A wrote its write-back."""
    frames = [
        {"type": "turn-accepted", "turn_id": "t1"},
        {"type": "message-delta", "text": "A", "turn_id": "t1"},
        {"type": "done", "ok": True, "turn_id": "t1"},
        {"type": "turn-accepted", "turn_id": "t2"},          # B, submitted mid-turn, taken now
        {"type": "tool-call", "tool": "Write", "turn_id": "t1"},   # A's write-back, still running
        {"type": "turn-complete", "turn_id": "t1"},          # the old relay closed the view HERE
        {"type": "message-delta", "text": "B", "turn_id": "t2"},
        {"type": "done", "ok": True, "turn_id": "t2"},
        {"type": "turn-complete", "turn_id": "t2"},
        {"type": "message-delta", "text": "never read", "turn_id": "t3"},
    ]
    got = _read_frames(frames)
    assert [(e["type"], e.get("turn_id")) for e in got][-3:] == [
        ("message-delta", "t2"), ("done", "t2"), ("turn-complete", "t2")]
    assert all(e.get("turn_id") != "t3" for e in got)


def test_a_view_resumed_mid_turn_still_closes_on_that_turns_completion():
    """A reconnect never saw its turn begin; its `turn-complete` is still the end, as before."""
    frames = [
        {"type": "message-delta", "text": "rest", "turn_id": "t1"},
        {"type": "turn-complete", "turn_id": "t1"},
        {"type": "message-delta", "text": "never read", "turn_id": "t2"},
    ]
    assert [e["type"] for e in _read_frames(frames)] == ["message-delta", "turn-complete"]


def test_an_injected_ack_opens_no_turn():
    """Mid-turn steering acknowledges with a nonce and starts nothing — it must not hold a view."""
    frames = [
        {"type": "turn-accepted", "turn_id": "t1"},
        {"type": "turn-accepted", "nonce": "n-1", "injected": True},
        {"type": "turn-complete", "turn_id": "t1"},
        {"type": "message-delta", "text": "never read", "turn_id": "t2"},
    ]
    assert [e["type"] for e in _read_frames(frames)][-1] == "turn-complete"


# ── 2. an idle chat learns that a turn started after what it read ─────────────────────────────

def _out(r, ev: dict) -> str:
    return r.xadd(units.output_topic(_UNIT), {"event": json.dumps(ev)})


def test_pending_says_a_turn_was_taken_after_the_cursor_the_chat_read_to(client, fake_redis):
    _out(fake_redis, {"type": "turn-accepted", "turn_id": "t1"})
    _out(fake_redis, {"type": "done", "ok": True, "turn_id": "t1"})
    cursor = _out(fake_redis, {"type": "turn-complete", "turn_id": "t1"})   # where the view ended
    _out(fake_redis, {"type": "turn-accepted", "turn_id": "t2"})            # B, already taken
    _out(fake_redis, {"type": "turn-accepted", "nonce": "n", "injected": True})  # not a turn

    seen = client.get("/api/chat/pending", headers=_HEADERS,
                      params={"session": "main", "after": cursor}).json()
    assert seen["pending"] == []          # nothing is QUEUED — which is why this was missed
    assert seen["taken"] == 1             # …but something STARTED after what the chat read

    # Without a cursor the answer is what it always was: no `taken` to misread.
    assert "taken" not in client.get("/api/chat/pending", headers=_HEADERS,
                                     params={"session": "main"}).json()


def test_pending_refuses_a_cursor_that_is_not_a_stream_id(client):
    r = client.get("/api/chat/pending", headers=_HEADERS, params={"session": "main", "after": "$"})
    assert r.status_code == 422


def test_turns_taken_after_is_best_effort_without_redis():
    assert api_shared.turns_taken_after(None, _UNIT, "1-0") == 0
    assert api_shared.turns_taken_after("redis://x", _UNIT, "") == 0


# ── 3. a submission queues on the unit that is running, never a new one ───────────────────────

def test_a_submission_does_not_step_the_mount_generation(client, fake_redis):
    sess = client.app.state.sessions
    _submit(client, prompt="first", turn_id="c-0")
    # Mid-turn, the agent made a workspace (or the person changed the model): mounts are stale.
    assert sess.add_workspace("u1", "main", "fresh-ws") is True

    body = _submit(client, prompt="and this, while you are at it", turn_id="c-1").json()
    assert body["unit"] == _UNIT                       # the running unit, not a new one
    assert sess.mount_gen("u1", "main") == 0

    # The next turn started on an idle chat takes the new generation, as before.
    r = client.post("/api/chat", headers=_HEADERS, json={"session": "main", "prompt": "next"})
    assert r.headers["x-unit-id"] == units.chat_unit_id("u1", "main", 1)
