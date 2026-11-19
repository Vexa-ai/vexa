"""THE ROOM SHOWS AT ONCE THAT VEXA IS ON IT (founder, app.dev 2026-10-16): the moment a sensor's wake
is accepted for a room, its turn-status says "Vexa heard you · thinking…"; the reply replaces it; a
turn that ends with nothing to say leaves "Vexa: nothing to add" briefly, then nothing."""
import fakeredis

from control_plane import rooms

ROOM = "s-room1"


def test_a_wake_is_heard_at_once_and_the_reply_clears_it():
    r = fakeredis.FakeRedis(decode_responses=True)
    assert rooms.turn_status(r, ROOM, busy=False, now=100.0) is None
    rooms.note_heard(r, ROOM, "addressed", now=100.0)
    assert rooms.turn_status(r, ROOM, busy=False, now=101.0) == "thinking"     # before the turn starts
    assert rooms.turn_status(r, ROOM, busy=True, now=160.0) == "thinking"      # while it runs
    row = {"id": ROOM, "owner": "31"}
    rooms.publish(r, row, {"seq": 3, "author": "vexa", "text": "We decided Tuesday.", "created": 161.0},
                  author_name="Vexa", author_kind="agent")
    assert rooms.turn_status(r, ROOM, busy=False, now=162.0) is None           # the reply replaced it


def test_a_turn_that_says_nothing_leaves_nothing_to_add_then_nothing():
    r = fakeredis.FakeRedis(decode_responses=True)
    rooms.note_heard(r, ROOM, "threshold", now=100.0)
    assert rooms.turn_status(r, ROOM, busy=True, now=130.0) == "thinking"
    assert rooms.turn_status(r, ROOM, busy=False, now=140.0) == "nothing"      # ended, no reply
    assert rooms.turn_status(r, ROOM, busy=False, now=141.0) == "nothing"
    assert rooms.turn_status(r, ROOM, busy=False, now=150.0) is None           # shown briefly, then gone


def test_a_person_s_message_does_not_clear_it():
    r = fakeredis.FakeRedis(decode_responses=True)
    rooms.note_heard(r, ROOM, "addressed", now=100.0)
    rooms.publish(r, {"id": ROOM, "owner": "31"}, {"seq": 4, "author": "32", "text": "and also", "created": 101.0})
    assert rooms.turn_status(r, ROOM, busy=False, now=102.0) == "thinking"



def test_the_step_line_is_the_running_turns_latest_step():
    import json
    from shared import units
    r = fakeredis.FakeRedis(decode_responses=True)
    assert rooms.current_step(r, ROOM) is None
    unit = "agent-ws-team-room-" + ROOM + "-signal"
    r.set(f"session:{ROOM}:lease", unit)
    topic = units.output_topic(unit)
    r.xadd(topic, {"event": json.dumps({"type": "tool-call", "tool": "mcp__vexa__workspace_read",
                                        "args": {"file_path": "kg/notes.md", "secret": "x"}})})
    r.xadd(topic, {"event": json.dumps({"type": "tool-call", "tool": "Write",
                                        "args": {"file_path": "kg/entities/meeting/m.md"}})})
    assert rooms.current_step(r, ROOM) == {"tool": "Write", "file_path": "kg/entities/meeting/m.md"}
    r.xadd(topic, {"event": json.dumps({"type": "turn-complete"})})
    assert rooms.current_step(r, ROOM) is None                          # the turn is over
