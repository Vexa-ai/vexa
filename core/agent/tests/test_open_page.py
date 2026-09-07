"""Vexa-ai/vexa#1586 — the agent can OPEN something, and says truthfully whether it did.

The founder typed "open meeting transcript" in a meeting chat. The agent called
`meeting_transcript`, summarised 677 segments in prose and offered to re-verify facts. He answered
*"it did not open the transcript"*.

It could not. Every panel move on this surface is a SIDE EFFECT of doing something else — a write
opens its own file (`artifact`), a `bot_send` opens the room's transcript — so an agent asked to
SHOW a thing had describing it as its only move. `open_page` is the move, `open` is the event, and
what these tests pin is the pair of properties the reply depends on:

  * the event is emitted ONLY when the tool answered yes, so a turn cannot say it opened something
    the panel was never told to show;
  * both runners derive it from the same result through the same function, so the convention cannot
    be right on one runner and absent on the other — which is the whole reason `_open_event` lives
    in `claude_code` and is imported by `openai_agent`.
"""
from __future__ import annotations

import json

from llm.claude_code import _OPEN_TOOLS, _open_event, parse_stream_json
from llm.openai_agent import _panel_events


def _use(tool, args=None, cid="c1"):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": tool, "input": args or {}, "id": cid}]}})


def _result(payload, cid="c1", err=False):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": cid, "is_error": err, "content": body}]}})


def _events(lines):
    return list(parse_stream_json(iter(lines)))


OPENED_DOC = {"opened": True, "target": "kg/entities/person/ada.md", "workspace": "",
              "path": "kg/entities/person/ada.md"}
OPENED_TRANSCRIPT = {"opened": True, "target": "meeting:transcript", "workspace": "",
                     "path": "meeting:147"}
NO_TRANSCRIPT = {"opened": False, "target": "meeting:transcript",
                 "reason": "no transcript for this meeting"}


# ── the event's own shape ────────────────────────────────────────────────────────────────────────

def test_an_opened_page_becomes_an_open_event_carrying_the_resolved_slot():
    assert _open_event(json.dumps(OPENED_DOC)) == {
        "type": "open", "target": "kg/entities/person/ada.md",
        "workspace": "", "path": "kg/entities/person/ada.md"}


def test_the_transcript_target_opens_the_canvas_by_ROW_and_never_a_file():
    ev = _open_event(json.dumps(OPENED_TRANSCRIPT))
    # `meeting:<row>` is the same vocabulary `_bot_artifact` emits and `pageForArtifact` resolves —
    # a transcript is not a file (founder ruling 2026-09-01), so nothing here may become a path.
    assert ev == {"type": "open", "target": "meeting:transcript", "workspace": "",
                  "path": "meeting:147"}
    assert not ev["path"].endswith(".md")


def test_a_refusal_paints_NOTHING():
    # "no transcript for this meeting" is a successful CALL whose answer is no. The model is told,
    # in words, and its one-line reply is that reason; the panel is not moved at all.
    assert _open_event(json.dumps(NO_TRANSCRIPT)) is None
    assert _open_event(json.dumps({"opened": False, "reason": "no such page"})) is None
    # and an answer we cannot read is not an answer
    assert _open_event("not json") is None
    assert _open_event(json.dumps({"opened": True})) is None      # yes, but to WHAT?


def test_an_open_event_carries_no_focus_flag():
    # An `artifact` weighs `focus` because it is the turn's suggestion. An `open` is the person's
    # own ask coming back, so it always fronts and there is nothing to weigh — the day this grows a
    # flag is the day a reader asks for a page and does not get it.
    assert "focus" not in _open_event(json.dumps(OPENED_DOC))


# ── claude-code: derived off the stream, success-only, per call id ───────────────────────────────

def test_claude_code_emits_the_open_after_its_tool_result():
    evs = _events([_use("mcp__vexa__open_page", {"target": "meeting:transcript"}),
                   _result(OPENED_TRANSCRIPT)])
    opens = [e for e in evs if e["type"] == "open"]
    assert opens == [{"type": "open", "target": "meeting:transcript", "workspace": "",
                      "path": "meeting:147"}]
    types = [e["type"] for e in evs]
    assert types.index("tool-result") < types.index("open")


def test_claude_code_opens_nothing_when_the_call_itself_failed():
    evs = _events([_use("mcp__vexa__open_page", {"target": "x.md"}),
                   _result(OPENED_DOC, err=True)])
    assert not [e for e in evs if e["type"] == "open"]


def test_claude_code_opens_nothing_when_the_tool_refused():
    evs = _events([_use("mcp__vexa__open_page", {"target": "meeting:transcript"}),
                   _result(NO_TRANSCRIPT)])
    assert not [e for e in evs if e["type"] == "open"]


def test_two_opens_in_one_turn_are_matched_by_call_id():
    # the transcript, then the note — a turn may legitimately open twice, and the second answer must
    # not be attributed to the first ask
    evs = _events([_use("mcp__vexa__open_page", {"target": "meeting:transcript"}, cid="a"),
                   _use("mcp__vexa__open_page", {"target": "meeting:note"}, cid="b"),
                   _result(OPENED_TRANSCRIPT, cid="a"),
                   _result({"opened": True, "target": "meeting:note", "workspace": "",
                            "path": "kg/entities/meeting/2026-03-02-0000-dna-tsc.md"}, cid="b")])
    assert [e["path"] for e in evs if e["type"] == "open"] == [
        "meeting:147", "kg/entities/meeting/2026-03-02-0000-dna-tsc.md"]


def test_a_result_with_no_matching_call_opens_nothing():
    evs = _events([_use("mcp__vexa__open_page", {"target": "x.md"}, cid="c1"),
                   _result(OPENED_DOC, cid="c1", err=True),
                   _result(OPENED_DOC, cid="c1")])
    assert not [e for e in evs if e["type"] == "open"]


def test_reading_a_transcript_is_not_opening_it():
    # the exact shape of the founder's turn: the agent read the meeting and said words about it.
    # Nothing about that may move the panel — which is why the vocabulary is a closed set.
    evs = _events([_use("mcp__vexa__meeting_transcript", {"meeting_id": "147"}),
                   _result({"read_ok": True, "total_segments": 677})])
    assert not [e for e in evs if e["type"] == "open"]
    assert "mcp__vexa__meeting_transcript" not in _OPEN_TOOLS


# ── openai-agent: the same result, through the same function ────────────────────────────────────

def test_openai_agent_derives_the_same_event_from_the_same_result():
    call = {"name": "mcp__vexa__open_page", "args": {"target": "meeting:transcript"}}
    assert _panel_events(call, True, json.dumps(OPENED_TRANSCRIPT)) == [
        {"type": "open", "target": "meeting:transcript", "workspace": "", "path": "meeting:147"}]
    # both runners agree, byte for byte, because it is one function and not two conventions
    assert _panel_events(call, True, json.dumps(OPENED_TRANSCRIPT))[0] == \
        _open_event(json.dumps(OPENED_TRANSCRIPT))


def test_openai_agent_opens_nothing_on_a_failed_call_or_a_refusal():
    call = {"name": "open_page", "args": {"target": "meeting:transcript"}}
    assert _panel_events(call, False, json.dumps(OPENED_TRANSCRIPT)) == []
    assert _panel_events(call, True, json.dumps(NO_TRANSCRIPT)) == []


def test_the_worker_is_allowed_to_call_it():
    # A tool the worker's allow-set omits is a tool the model is never offered, and the whole fix
    # would be inert with everything else in place.
    import json as _json
    from pathlib import Path
    manifest = _json.loads(
        (Path(__file__).resolve().parents[1] / "worker" / "mcp_tools.v1.json").read_text())
    assert "open_page" in manifest["tools"]
