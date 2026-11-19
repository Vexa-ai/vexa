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


def test_a_person_turn_shows_what_the_agent_said_but_never_the_marker():
    # 2026-10-18: internal markers never render — not even on a turn a person started.
    raw = _history({"type": "user", "message": {"content": "anything new?"}}, _say(_text("Nothing yet. " + marks.QUIET_REPLY)),
                   {"type": "user", "message": {"content": "and now?"}}, _say(_text("Yes: the proxy.")))
    agents = [t for t in parse_history(raw, []) if t["role"] == "agent"]
    assert [a["text"] for a in agents] == ["Nothing yet.", "Yes: the proxy."]


def test_the_quiet_window_closes_on_the_next_person_turn():
    raw = _history({"type": "user", "message": {"content": _wake_prompt()}}, _say(_text(marks.QUIET_REPLY)),
                   {"type": "user", "message": {"content": "hello?"}}, _say(_text("Hello.")))
    agents = [t for t in parse_history(raw, []) if t["role"] == "agent"]
    assert [a["text"] for a in agents] == ["Hello."]
    users = [t for t in parse_history(raw, []) if t["role"] == "user"]
    assert users[0].get("trigger") == "signal" and "trigger" not in users[1]


def _composed_like_the_worker(wake: str) -> str:
    """The worker composes a chat turn as the thread preamble and the instruction block FIRST and the
    wake (mark included) after them — the shape of the live app.dev turn of 2026-10-18 (mark ~21 KB in)."""
    return ("This chat's thread id is `s-0123456789abcdef0123456789abcdef`. A routine you create here runs "
            "in this chat.\n\n## How you speak\nRead, search and open whatever you need SILENTLY.\n"
            + ("Instruction block line.\n" * 400) + "\n" + wake)


def test_a_signal_wake_behind_the_preamble_is_still_a_signal_turn_and_its_quiet_reply_never_shows():
    # LIVE BUG app.dev 2026-10-18: "From the call" over a literal "[vexa-quiet]". `is_signal_turn`
    # tested `startswith`, so a composed prompt (preamble first) was never a signal turn.
    prompt = _composed_like_the_worker(_wake_prompt())
    assert not prompt.startswith(marks.flow_mark(marks.SIGNAL_FLOW, marks.SIGNAL_STEP))
    assert marks.is_signal_turn(prompt)
    raw = _history({"type": "user", "message": {"content": prompt}}, _say(_text(marks.QUIET_REPLY)))
    turns = parse_history(raw, [])
    assert [t for t in turns if t["role"] == "agent"] == []
    assert [t.get("trigger") for t in turns if t["role"] == "user"] == ["signal"]
    # the wake prompt itself (which TELLS the agent about the marker) stays the user turn's raw text,
    # hidden by the terminal off `trigger`; nothing the agent said carries it
    assert marks.QUIET_REPLY not in json.dumps([t for t in turns if t["role"] != "user"])


def test_a_person_who_merely_mentions_the_mark_mid_sentence_did_not_start_a_signal_turn():
    assert not marks.is_signal_turn("what does " + marks.flow_mark(marks.SIGNAL_FLOW, marks.SIGNAL_STEP) + "mean?")


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
