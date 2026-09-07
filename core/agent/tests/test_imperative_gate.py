"""F162 — the imperative gate (ledger 2026-09-02 14:17Z-14:30Z, entries F161/F162/F166/F169).

In a live-meeting copilot chat the founder wrote "send bot" FOUR TIMES. The turn answered with a
workspace `propose` call (Objective/membership questions), a WebSearch and a WebFetch — never
`bot_send`. Root cause (worker-side half; the `bots_running` state-truth half is fixed in the rig,
not here): the composed prompt put the person's own words LAST, after six preambles' worth of
onboarding/propose framing ("ASK the user their name early", "answer from this context before
asking..."), so an explicit operational imperative lost to that framing every time.

`imperative_preamble()` is a pure function of the person's raw message — no model call, no I/O —
so its detection and its position in the composed prompt are provable without a live harness,
exactly like `timeline_preamble` in `test_timeline_preamble.py`.
"""
from __future__ import annotations

from worker import engine
from worker.engine import imperative_preamble


# ── detection: the phrases the ledger names, and only those firing noise ──────────────────────────

def test_send_bot_is_detected():
    text = imperative_preamble("please send bot now")
    assert "operational imperative" in text
    assert "call `bot_send` first" in text


def test_all_four_repeats_still_name_bot_send_once():
    """The founder wrote it four times in the ledger incident — the gate must not repeat itself
    four times back, and must not miss it on any single repeat either."""
    for prompt in ["send bot", "Send bot", "send the bot", "SEND BOT please"]:
        text = imperative_preamble(prompt)
        assert text.count("bot_send") == 1, prompt


def test_join_the_meeting_maps_to_bot_send():
    assert "call `bot_send` first" in imperative_preamble("can you join the meeting now")


def test_stop_recording_and_stop_bot_map_to_bot_stop():
    assert "call `bot_stop` first" in imperative_preamble("stop recording please")
    assert "call `bot_stop` first" in imperative_preamble("stop the bot")


def test_schedule_bot_maps_to_bot_schedule():
    assert "call `bot_schedule` first" in imperative_preamble("schedule the bot for 3pm")


def test_multiple_imperatives_in_one_message_all_listed():
    text = imperative_preamble("send bot, and stop recording when it's done")
    assert "call `bot_send` first" in text
    assert "call `bot_stop` first" in text


def test_ordinary_chat_gets_no_imperative_framing():
    """No false positives: a turn that never named an operational imperative gets nothing extra —
    exactly the ordinary-turn case `timeline_preamble`'s own tests protect."""
    assert imperative_preamble("what did we discuss about pricing last week?") == ""
    assert imperative_preamble("") == ""
    assert imperative_preamble("who is attending this meeting?") == ""


# ── it reaches the turn, and it reaches it FIRST ───────────────────────────────────────────────────

def test_imperative_preamble_ships_on_the_turn_prompt_before_everything_else(tmp_path, monkeypatch):
    """The whole point of F162: not merely present, but FIRST — ahead of the mounts preamble's own
    onboarding nudge ("ASK the user their name early") and ahead of the MCP-status note, so the
    model reads the imperative before any competing concern."""
    seen = {}

    def fake_run(work, prompt, harness, **kw):
        seen["prompt"] = prompt
        yield {"type": "done", "reply": "ok", "sessionId": "s"}

    monkeypatch.setattr(engine, "run_harness_turn", fake_run)
    # Two mounts so mounts_preamble (which carries the onboarding nudge) actually renders — a
    # single-mount turn short-circuits it to "" and the ordering claim would be untestable.
    monkeypatch.setattr(engine, "active_mounts", lambda: [
        {"slug": "personal", "path": str(tmp_path), "role": "private", "write": True,
         "primary": True, "purpose": ""},
        {"slug": "acme-deal", "path": str(tmp_path), "role": "shared", "write": True,
         "primary": False, "purpose": "The ACME deal room."},
    ])
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "timeline_preamble", lambda: "")

    class H:
        def prepare(self, work, chat_root=None):
            pass

        def transcript_bytes(self, work, sid):
            return 0

    list(engine.run_turn_over_workspace(tmp_path, "send bot", harness=H(), commit=False))
    prompt = seen["prompt"]
    assert "operational imperative" in prompt
    assert "call `bot_send` first" in prompt
    assert "ASK the user their name early" in prompt  # the onboarding nudge is still there...
    # ...but the imperative gate comes BEFORE it, and before the person's own "send bot" too.
    assert prompt.index("operational imperative") < prompt.index("ASK the user their name early")
    assert prompt.index("operational imperative") < prompt.rindex("send bot")


def test_a_turn_with_no_imperative_carries_no_extra_framing(tmp_path, monkeypatch):
    seen = {}

    def fake_run(work, prompt, harness, **kw):
        seen["prompt"] = prompt
        yield {"type": "done", "reply": "ok", "sessionId": "s"}

    monkeypatch.setattr(engine, "run_harness_turn", fake_run)
    monkeypatch.setattr(engine, "active_mounts", lambda: [
        {"slug": "personal", "path": str(tmp_path), "role": "private", "write": True, "primary": True}])
    monkeypatch.setattr(engine, "_ensure_repo", lambda w: None)
    monkeypatch.setattr(engine, "timeline_preamble", lambda: "")

    class H:
        def prepare(self, work, chat_root=None):
            pass

        def transcript_bytes(self, work, sid):
            return 0

    list(engine.run_turn_over_workspace(tmp_path, "what's on the agenda today?", harness=H(), commit=False))
    assert "operational imperative" not in seen["prompt"]
