"""A SIGNAL TURN MAY END SILENTLY (friction 6, 2026-10-16).

A turn the meeting (or a room's sensor) woke used to force a visible chat message — the agent wrote
"Nothing to add" because nothing told it silence was an answer, and that line landed in the person's
chat as noise. Now the wake tells the agent it may answer with nothing, or with exactly
``marks.QUIET_REPLY``; such a reply records no visible message (history, room stream). A turn a
PERSON started is untouched: whatever the agent said there is shown as before.
"""
from __future__ import annotations

import json

from control_plane import events
from control_plane.workspace_reader import parse_history
from shared import marks, units

from tests.test_r1_rooms import _room, rp, world  # noqa: F401 — `world` is a fixture
from tests.test_transcript_signals import _signal_event


def _wake_prompt() -> str:
    return events.event_to_invocation(_signal_event())["start"]["entrypoint"]["inline"]


def _history(*lines) -> str:
    return "\n".join(json.dumps(x) for x in lines)


def _say(*blocks):
    return {"type": "assistant", "message": {"content": list(blocks)}}


def _text(t):
    return {"type": "text", "text": t}


def test_the_wake_tells_the_agent_silence_is_an_answer():
    for data in (_signal_event()["data"], {"session": "room-1", "buffer": [], "score": 1.0}):
        prompt = events.signal_prompt("plan", data)
        assert marks.QUIET_REPLY in prompt
        assert "Nothing to add" not in prompt


def test_a_quiet_signal_turn_leaves_no_visible_message():
    for reply in (marks.QUIET_REPLY, f"  {marks.QUIET_REPLY}\n", ""):
        raw = _history({"type": "user", "message": {"content": _wake_prompt()}}, _say(_text(reply)))
        turns = parse_history(raw, [])
        assert [t for t in turns if t["role"] == "agent"] == [], reply


def test_a_quiet_signal_turn_that_updated_the_page_shows_the_work_but_not_the_sentinel():
    raw = _history({"type": "user", "message": {"content": _wake_prompt()}},
                   _say(_text("Updating the page."), {"type": "tool_use", "name": "Edit", "input": {}}),
                   {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
                   _say(_text(marks.QUIET_REPLY)))
    (agent,) = [t for t in parse_history(raw, []) if t["role"] == "agent"]
    assert marks.QUIET_REPLY not in agent["text"] and agent["ops"]


def test_a_signal_turn_that_speaks_is_shown():
    raw = _history({"type": "user", "message": {"content": _wake_prompt()}}, _say(_text("Noted the decision.")))
    (agent,) = [t for t in parse_history(raw, []) if t["role"] == "agent"]
    assert agent["text"] == "Noted the decision."


def test_a_person_turn_is_unchanged_whatever_the_agent_said():
    raw = _history({"type": "user", "message": {"content": "anything new?"}}, _say(_text(marks.QUIET_REPLY)),
                   {"type": "user", "message": {"content": "and now?"}}, _say(_text("Yes: the proxy.")))
    agents = [t for t in parse_history(raw, []) if t["role"] == "agent"]
    assert [a["text"] for a in agents] == [marks.QUIET_REPLY, "Yes: the proxy."]


def test_the_quiet_window_closes_on_the_next_person_turn():
    raw = _history({"type": "user", "message": {"content": _wake_prompt()}}, _say(_text(marks.QUIET_REPLY)),
                   {"type": "user", "message": {"content": "hello?"}}, _say(_text(marks.QUIET_REPLY)))
    agents = [t for t in parse_history(raw, []) if t["role"] == "agent"]
    assert [a["text"] for a in agents] == [marks.QUIET_REPLY]


def test_a_quiet_room_signal_turn_posts_nothing_to_the_room(world):  # noqa: F811
    import fakeredis
    from control_plane import room_echo, rooms
    client, store, *_ = world
    sid = _room(client)
    r = fakeredis.FakeRedis(decode_responses=True)
    sig = rp.room_unit_id("team", sid) + units.SIGNAL_UNIT_SUFFIX
    for ev in ({"type": "message-delta", "text": marks.QUIET_REPLY}, {"type": "turn-complete"}):
        r.xadd(units.output_topic(sig), {"event": json.dumps(ev)})
    assert room_echo.echo_unit(r, store, sig) == 0
    assert r.xrange(rooms.stream_key(sid)) == []
    # a room turn a person started still echoes what it said, verbatim
    plain = rp.room_unit_id("team", sid)
    for ev in ({"type": "message-delta", "text": marks.QUIET_REPLY}, {"type": "turn-complete"}):
        r.xadd(units.output_topic(plain), {"event": json.dumps(ev)})
    assert room_echo.echo_unit(r, store, plain) == 1
