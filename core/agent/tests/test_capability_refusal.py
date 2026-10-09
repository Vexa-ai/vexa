"""F70 — a turn that refuses a tool it is holding (2026-09-02), and F73's panel move.

The founder asked for a bot and was told "I don't have a bot-dispatch tool in this session".
`bot_send` was in the list, the CLI logged `hasTools: true`, and the model never attempted a call.
Asked afterwards to enumerate its tools it listed them all and admitted it had been "guessing at my
own capabilities instead of checking them"."""
from __future__ import annotations

import json

from llm.claude_code import _bot_artifact
from worker.friction import disbelieved_capability

TOOLS = ["Read", "Write", "mcp__vexa__request_meeting_bot", "mcp__vexa__stop_bot",
         "mcp__vexa__get_meeting_transcript"]
REFUSAL = ("I don't have a bot-dispatch tool in this session — I can read and write your "
           "workspaces, but sending the Vexa bot into a call is a platform action I can't take.")


def test_the_refusal_that_actually_happened_is_caught():
    assert disbelieved_capability("send the bot to https://meet.google.com/ios-xdtp-vmt",
                                  REFUSAL, TOOLS) == "request_meeting_bot"


def test_it_does_not_fire_when_the_tool_is_genuinely_absent():
    """The condition that makes acting on this safe. With the send out of the list the refusal is
    TRUE, and correcting a true statement would be the same error pointed the other way."""
    assert disbelieved_capability("send the bot in", REFUSAL,
                                  ["Read", "Write", "mcp__vexa__get_meeting_transcript"]) is None


def test_a_reported_failure_is_not_a_refusal():
    """"I cannot reach it right now" is the honest answer the machinery note ASKS for. Firing here
    would re-run turns that did the right thing, and a re-run is not free."""
    for honest in ("I called request_meeting_bot and it returned 502 — I cannot get the bot in right now.",
                   "I can't reach the meeting service at the moment; the error was a timeout.",
                   "That meeting has ended, so there is nothing to join."):
        assert disbelieved_capability("send the bot in", honest, TOOLS) is None


def test_the_verb_has_to_match_the_tool():
    """A refusal about something else is not evidence about the send."""
    assert disbelieved_capability("what did they decide about pricing?", REFUSAL, TOOLS) is None


def test_junk_never_raises():
    for p, r, t in (("", REFUSAL, TOOLS), ("send", "", TOOLS), ("send", REFUSAL, []),
                    ("send", REFUSAL, None), (None, None, None)):
        assert disbelieved_capability(p, r, t) is None


# ── F73 — the send opens the transcript beside the chat ──────────────────────────────────────────

def _result(payload: dict) -> list:
    return [{"type": "text", "text": json.dumps(payload)}]


def test_a_successful_send_moves_the_panel_by_ROW():
    ev = _bot_artifact(_result({"id": 118, "platform": "google_meet",
                                "native_meeting_id": "ios-xdtp-vmt", "status": "requested"}))
    # `native` rides along since Vexa-ai/vexa#1597 — the event is also the fact agent-api binds the
    # meeting to this chat's session from, and a binding answers to both addresses. The PANEL move
    # is untouched: path by row, pin and focus.
    assert ev == {"type": "artifact", "path": "meeting:118", "pin": True, "focus": True,
                  "native": "ios-xdtp-vmt"}


def test_the_native_id_is_never_the_path():
    """A personal room's native id spans every meeting ever held in it — it names a series, and the
    resolver would pick whichever occurrence is newest. No row, no event."""
    assert _bot_artifact(_result({"native_meeting_id": "ios-xdtp-vmt", "status": "requested"})) is None
    assert _bot_artifact(_result({"id": "", "status": "requested"})) is None


def test_a_bot_already_in_the_meeting_opens_that_meeting():
    """`request_meeting_bot` answers a second send for a live meeting with the meeting it already
    has; that is the room this chat asked for, so it opens and binds the same way."""
    ev = _bot_artifact(_result({"status": "already_exists",
                                "meeting": {"id": 118, "native_meeting_id": "ios-xdtp-vmt"}}))
    assert ev == {"type": "artifact", "path": "meeting:118", "pin": True, "focus": True,
                  "native": "ios-xdtp-vmt"}


def test_a_failed_send_opens_nothing():
    assert _bot_artifact(_result({"detail": "the bot could not be dispatched"})) is None
    assert _bot_artifact(_result({"id": 118, "status": "failed"})) is None
    assert _bot_artifact(_result({"status": "already_exists", "detail": "conflict"})) is None


def test_a_result_that_is_not_json_is_not_a_crash():
    for junk in (None, [], [{"type": "text", "text": "the bot is in"}]):
        assert _bot_artifact(junk) is None


def test_the_worker_friction_path_scrubs_before_it_is_durable(tmp_path, monkeypatch):
    """#1416 redacts on the way INTO the server's copy. `worker/friction.report` writes two other
    places — a fallback log that outlives the turn, and the request body — and neither was scrubbed.

    The F70 detector is what makes this load-bearing rather than theoretical: it deliberately puts
    the person's prompt and the agent's reply into a record, so the turn where somebody pastes a
    token and the agent then refuses a capability writes that token to disk."""
    import json
    from worker import friction

    log = tmp_path / "friction.jsonl"
    monkeypatch.setattr(friction, "FALLBACK_LOG", log)
    monkeypatch.setattr(friction, "_edge", lambda: ("http://127.0.0.1:1", "vxd_t.t.t"))  # unreachable: log-only path

    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    friction.report({"kind": "capability-hallucination", "tool": "bot_send",
                     "prompt": f"attach the repo with {secret} please",
                     "reply": "I don't have a bot-dispatch tool in this session"},
                    subject="126")

    written = log.read_text()
    assert secret not in written, "a pasted token reached the durable fallback log"
    rec = json.loads(written.splitlines()[-1])
    assert rec["tool"] == "bot_send"          # non-secret fields survive intact
    assert "bot-dispatch tool" in rec["reply"]

def test_email_refusal_with_transcript_in_context_does_not_replay():
    assert disbelieved_capability('Context: transcript. Send a test email', "I don't have a send tool in this session. Here is your email draft.", TOOLS) is None
    assert disbelieved_capability('read the transcript', "I cannot access Gmail tools in this session", TOOLS) is None
