"""The caller's tab joins the call `call_person` started (call-person-autojoin, 2026-10-18).

meeting-api ends a call nobody joins within its first-join window (30 s). The founder typed
"Call tester-b", the agent started the call and answered with the room — and if he did not open it
fast enough the call was gone. The answer now carries `ui_action: "join_call"`, and both runners turn
that into a `call` event the terminal acts on (it opens the room and joins as the caller, after
re-reading the call and checking it is the viewer's own).

What is pinned here:
  * the event is derived only from a SUCCESSFUL `call_person` answer, on both runners, through the
    one helper;
  * it carries ids of a checked shape and the other person's display name — never a subject id,
    the share token or anything the model wrote;
  * an answer without the marker, or with a malformed id, earns nothing.
"""
from __future__ import annotations

import json

from llm.claude_code import parse_stream_json
from llm.openai_agent import _panel_events
from llm.tool_events import _OPEN_TOOLS, _open_event

STARTED = {
    "session_id": "s-0123abcd", "room_ref": "[[room:s-0123abcd]]", "room_created": True,
    "with": {"subject": "tester-b", "name": "Tester B"}, "ui_action": "join_call", "already_live": False,
    "call": {"room": "call-abc123", "meeting_id": 7, "transcribed": True, "started": 1.0},
    "rang": ["tester-b"], "ring_window_s": 30, "say": "Calling Tester B.",
}


def _use(tool, cid="c1"):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": tool, "input": {"person": "tester-b"}, "id": cid}]}})


def _result(payload, cid="c1", err=False):
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": cid, "is_error": err, "content": json.dumps(payload)}]}})


def test_call_person_is_a_panel_tool_on_both_names():
    assert "mcp__vexa__call_person" in _OPEN_TOOLS and "call_person" in _OPEN_TOOLS


def test_a_started_call_earns_a_call_event_with_ids_and_a_name_only():
    ev = _open_event(json.dumps(STARTED))
    assert ev == {"type": "call", "chat": "s-0123abcd", "room": "call-abc123",
                  "with": "Tester B", "already_live": False}
    # never the other person's subject id, the share token or the model-facing `say`
    assert "tester-b" not in json.dumps(ev) and "say" not in ev


def test_a_call_already_live_is_still_joined():
    ev = _open_event(json.dumps({**STARTED, "already_live": True}))
    assert ev is not None and ev["already_live"] is True


def test_no_marker_or_a_bad_id_earns_nothing():
    assert _open_event(json.dumps({k: v for k, v in STARTED.items() if k != "ui_action"})) is None
    assert _open_event(json.dumps({**STARTED, "session_id": "../etc"})) is None
    assert _open_event(json.dumps({**STARTED, "call": {"room": "a b"}})) is None
    assert _open_event(json.dumps({**STARTED, "call": None})) is None


def test_claude_code_emits_the_call_event_only_on_success():
    ok = list(parse_stream_json(iter([_use("mcp__vexa__call_person"), _result(STARTED)])))
    assert [e for e in ok if e.get("type") == "call"] == [
        {"type": "call", "chat": "s-0123abcd", "room": "call-abc123", "with": "Tester B", "already_live": False}]
    failed = list(parse_stream_json(iter([_use("mcp__vexa__call_person"), _result(STARTED, err=True)])))
    assert not [e for e in failed if e.get("type") == "call"]


def test_the_openai_runner_derives_the_same_event():
    call = {"name": "call_person", "arguments": "{}"}
    assert _panel_events(call, True, json.dumps(STARTED)) == [_open_event(json.dumps(STARTED))]
    assert _panel_events(call, False, json.dumps(STARTED)) == []
