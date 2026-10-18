"""agent-api's half of a routine pausing in a shared chat (Vexa-ai/vexa#1783).

flows pauses the routine when a run was refused a private source because its chat is shared
(`core/flows/tests/test_routine_shared_pause.py`). It needs two things from agent-api:

  * the turn's outcome names WHO the chat is shared with, so the line in the chat can say it;
  * the routine's card carries the reason flows recorded, so the strip shows it paused and why.
"""
from __future__ import annotations

from tests._session_fakes import MemBlobs
from control_plane import routine_flows, session_access
from control_plane.session_store import SessionStore


def _store(tmp_path):
    return SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())


def test_the_outcome_names_the_other_people_in_the_chat(tmp_path):
    store = _store(tmp_path)
    row = store.create("7", name="pchat-abc")
    store.add_participant(row["id"], "7", "9")
    store.add_participant(row["id"], "7", "11")
    names = {"9": "Bob", "11": ""}
    got = session_access.shared_with(store, "7", "pchat-abc", lambda who: names.get(who, ""))
    assert got == ["Bob", "someone"]


def test_a_private_chat_names_nobody(tmp_path):
    store = _store(tmp_path)
    store.create("7", name="pchat-abc")
    assert session_access.shared_with(store, "7", "pchat-abc", lambda who: "x") == []
    assert session_access.shared_with(None, "7", "pchat-abc", lambda who: "x") == []
    assert session_access.shared_with(store, "7", "never-made", lambda who: "x") == []


def test_the_card_carries_the_reason_flows_recorded():
    f = {"name": "morning-brief", "version": 1, "scope": "user:7", "status": "retired",
         "trigger": {"kind": "schedule", "cron": "0 7 * * *"}, "steps": ["agent_run"],
         "paused_reason": "this chat is now shared with Bob, so your mail can't be read here.",
         "params": {"instruction": "x", "thread": "pchat-abc"}}
    card = routine_flows.card_from_flow("7", f)
    assert card["enabled"] is False
    assert card["paused_reason"].startswith("this chat is now shared with Bob")
    running = routine_flows.card_from_flow("7", {**f, "status": "active"})
    assert "paused_reason" not in running
