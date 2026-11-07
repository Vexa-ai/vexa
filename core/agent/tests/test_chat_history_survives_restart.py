"""A chat keeps its whole conversation when the worker starts a fresh harness session (#1783).

Live 2026-10-15: a five-turn chat lost everything before its last message. The Claude CLI writes
its own per-session bookkeeping into the transcript (``attachment`` records: a prompt snapshot,
the instructions, the skill listing — about 600 KB before the first word), so the resume budget
(``VEXA_CHAT_RESUME_MAX_BYTES``, 1 MB) was spent after two to four turns and the worker started a
new harness session. Two things then went wrong, and these tests hold both:

* the budget counts the conversation, not the CLI's bookkeeping — such a chat is resumed;
* when a thread does restart (a real over-budget history, another harness), the history the chat
  shows still holds every earlier turn: every transcript the conversation carries is read, oldest
  first, not only the one the pointer names.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import worker.engine as engine
from control_plane import session_sync
from control_plane.session_store import SessionStore
from llm.claude_code import ClaudeCodeHarness
from tests._session_fakes import MemBlobs

BOOKKEEPING = 900_000


def _line(obj: dict) -> str:
    return json.dumps(obj)


def _exchange(prompt: str, reply: str, at: str, *, snapshot: int = 0) -> list[str]:
    out = []
    if snapshot:
        out.append(_line({"type": "attachment", "timestamp": at,
                          "attachment": {"type": "prompt_snapshot", "content": "s" * snapshot}}))
    out += [
        _line({"type": "user", "timestamp": at, "message": {"content": prompt}}),
        _line({"type": "assistant", "timestamp": at,
               "message": {"content": [{"type": "text", "text": reply}]}}),
    ]
    return out


def _key(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


# ── the budget counts the conversation ──────────────────────────────────────────────────────────

def test_cli_bookkeeping_does_not_count_against_the_resume_budget(tmp_path: Path):
    proj = tmp_path / ".claude" / "projects" / "-w"
    proj.mkdir(parents=True)
    lines = _exchange("first", "one", "2026-10-15T08:46:00Z", snapshot=BOOKKEEPING)
    (proj / "sid-1.jsonl").write_text("\n".join(lines) + "\n")
    got = ClaudeCodeHarness().transcript_bytes(tmp_path, "sid-1")
    assert got < 10_000, got


def test_a_chat_over_the_budget_only_by_bookkeeping_is_resumed(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VEXA_CHAT_RESUME_MAX_BYTES", raising=False)
    chat_root = tmp_path / "_system"
    proj = chat_root / ".claude" / "projects" / "-w"
    proj.mkdir(parents=True)
    lines = (_exchange("a", "1", "2026-10-15T08:46:00Z", snapshot=BOOKKEEPING)
             + _exchange("b", "2", "2026-10-15T08:52:00Z", snapshot=BOOKKEEPING))
    (proj / "sid-1.jsonl").write_text("\n".join(lines) + "\n")
    sessions = chat_root / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "main.session").write_text("sid-1\n")
    assert engine._resume_id(chat_root, sessions / "main.session", ClaudeCodeHarness()) == "sid-1"


def test_a_real_history_over_the_budget_still_restarts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VEXA_CHAT_RESUME_MAX_BYTES", "1000")
    chat_root = tmp_path / "_system"
    proj = chat_root / ".claude" / "projects" / "-w"
    proj.mkdir(parents=True)
    (proj / "sid-1.jsonl").write_text("\n".join(_exchange("a" * 2000, "1", "2026-10-15T08:46:00Z")) + "\n")
    sessions = chat_root / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "main.session").write_text("sid-1\n")
    assert engine._resume_id(chat_root, sessions / "main.session", ClaudeCodeHarness()) is None


# ── the history shows every turn across a restart ───────────────────────────────────────────────

@pytest.fixture()
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    yield s
    s.close()


def _restarted_bundle() -> dict:
    """Four turns on harness session ``old``; the fifth on ``new``, seeded with the summary."""
    said = ["prepare the call", "find the logos", "put it on one page", "bring the proposal",
            "send the bot"]
    old, recs = [], []
    for i, words in enumerate(said[:4]):
        prompt = f"[preamble]\n{words}"
        old += _exchange(prompt, f"reply {i}", f"2026-10-15T08:4{6 + i}:00Z", snapshot=1000 if i == 0 else 0)
        recs.append({"key": _key(prompt), "user_text": words, "reply": f"reply {i}"})
    seeded = "## This conversation so far\n\nPerson: prepare the call\n\n---\n\n[preamble]\nsend the bot"
    new = _exchange(seeded, "reply 4", "2026-10-15T09:00:00Z", snapshot=1000)
    recs.append({"key": _key(seeded), "user_text": "send the bot", "reply": "reply 4"})
    return {
        ".claude/sessions/conversation.session": b"a-new\n",
        ".claude/sessions/conversation.turns.jsonl": ("\n".join(json.dumps(r) for r in recs) + "\n").encode(),
        # the newer transcript sorts FIRST by name: order must come from the transcripts themselves
        ".claude/projects/-w/a-new.jsonl": ("\n".join(new) + "\n").encode(),
        ".claude/projects/-w/z-old.jsonl": ("\n".join(old) + "\n").encode(),
    }


def test_history_keeps_every_turn_after_the_harness_session_restarted():
    turns = session_sync.history_from_files(_restarted_bundle())
    people = [t.get("user_text") for t in turns if t["role"] == "user"]
    assert people == ["prepare the call", "find the logos", "put it on one page",
                      "bring the proposal", "send the bot"]
    assert [t["text"] for t in turns if t["role"] == "agent"] == [f"reply {i}" for i in range(5)]


def test_the_stored_chat_view_survives_a_restart(store):
    sid = store.create("alice", name="pchat-1")["id"]
    store.save_bundle(sid, 0, _restarted_bundle())
    turns = session_sync.history(store, store.get(sid))
    assert sum(1 for t in turns if t["role"] == "user") == 5


def test_a_transcript_of_another_conversation_is_not_read():
    files = _restarted_bundle()
        # a side transcript (a machinery turn, a subagent) that no turn record names is not this chat
    files[".claude/projects/-w/side.jsonl"] = ("\n".join(_exchange("intruder", "no", "2026-10-15T07:00:00Z")) + "\n").encode()
    files[".claude/projects/-w/x/deep.jsonl"] = ("\n".join(_exchange("intruder", "no", "2026-10-15T07:00:00Z")) + "\n").encode()
    people = [t.get("user_text") or t["text"] for t in session_sync.history_from_files(files) if t["role"] == "user"]
    assert "intruder" not in " ".join(people)
