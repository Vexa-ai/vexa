"""A routine's run is a turn in the routine's own chat thread (ADR-0046 decision 12, ADR-0048).

The worker names its conversation after `VEXA_CHAT_SESSION`; without it, it falls back to the
person's `main` chat — so a routine run that was not stamped resumed `main`'s conversation (another
harness's id, which the CLI refused) and nothing reached the routine's chat. The routine's
`scheduled` dispatch that names a session is stamped exactly as a chat turn is."""
from __future__ import annotations

from control_plane.dispatch import build_unit_env
from shared.config import load_settings

BASE = {"identity": {"subject": "u_1", "launcher": "user:u_1"}, "runner": "openai-agent",
        "workspaces": [{"id": "u_1", "mode": "rw"}], "start": {"entrypoint": {"inline": "hi"}}}


def _env(tmp_path, inv):
    return build_unit_env(load_settings(workspaces_dir=str(tmp_path)), inv, unit_id="u", token="t")


def test_a_routine_run_names_the_routine_thread(tmp_path):
    inv = {**BASE, "trigger": "scheduled",
           "context": {"kind": "generic", "session": "flow-0123456789abcdef"}}
    assert _env(tmp_path, inv)["VEXA_CHAT_SESSION"] == "flow-0123456789abcdef"


def test_a_chat_turn_is_unchanged(tmp_path):
    inv = {**BASE, "trigger": "message", "context": {"kind": "generic", "session": "pchat-x"}}
    assert _env(tmp_path, inv)["VEXA_CHAT_SESSION"] == "pchat-x"


def test_a_scheduled_dispatch_with_no_thread_names_none(tmp_path):
    inv = {**BASE, "trigger": "scheduled", "context": {"kind": "generic"}}
    assert "VEXA_CHAT_SESSION" not in _env(tmp_path, inv)


def test_the_turn_is_told_its_own_thread_id():
    from worker.engine import thread_preamble
    text = thread_preamble("pchat-mv6j378b")
    assert "`pchat-mv6j378b`" in text and "params.thread" in text and "flows_submit" in text
    assert thread_preamble("") == ""


def test_a_routine_card_names_the_chat_it_runs_in():
    from control_plane import routine_flows as rf
    f = {"name": "airport", "version": 1, "scope": "user:2", "status": "active",
         "trigger": {"kind": "schedule", "cron": "*/30 * * * *"},
         "params": {"instruction": "x", "thread": "pchat-mv6j378b"}}
    assert rf.card_from_flow("2", f)["thread"] == "pchat-mv6j378b"
    f["params"] = {"instruction": "x"}
    assert rf.card_from_flow("2", f)["thread"] == rf.thread_session("user:2", "airport")
