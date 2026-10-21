"""The leak rule at the turn's commit (`llm.private_writes`, `llm.ports.run_harness_turn`).

A turn that read the person's mail and then wrote, with its own file tools, into a workspace another
member reads: that change is not committed, the workspace is put back, the turn says what it held
and the code that releases it, and the held content goes to agent-api by reference — never onto a
desk (H7). The person's `confirm <code>` has agent-api write it once. A turn that read nothing
private, or that wrote only to the person's own desk, commits as before.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from llm.ports import run_harness_turn
from llm.private_writes import Guard, private_source

OWNER = "7"
AUTHOR = ("Ada", "ada@example.com")


def _repo(p: Path, members=None) -> Path:
    p.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(p)], check=True)
    (p / "README.md").write_text("# ws\n")
    if members:
        (p / "policy").mkdir()
        (p / "policy" / "members.json").write_text(json.dumps(members))
    for a in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"]):
        subprocess.run(["git", "-C", str(p), *a], check=True)
    return p


class _Harness:
    """Reads the mail (a tool call), then writes a page into every mount it was given."""

    def __init__(self, tool, writes):
        self.tool, self.writes = tool, writes

    def run_turn(self, work, prompt, **_):
        if self.tool:
            yield {"type": "tool-call", "tool": self.tool, "callId": "c1", "args": {}}
            yield {"type": "tool-result", "callId": "c1", "ok": True}
        for path, text in self.writes:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text)
        yield {"type": "done", "ok": True, "reply": "ok"}


def _log(p: Path) -> int:
    return int(subprocess.run(["git", "-C", str(p), "rev-list", "--count", "HEAD"], capture_output=True,
                              text=True, check=True).stdout)


def _setup(tmp_path):
    desk = _repo(tmp_path / OWNER)
    shared = _repo(tmp_path / "meetup", members=[{"subject": OWNER, "role": "owner"},
                                                 {"subject": "8", "role": "contributor",
                                                  "email": "val@example.com"}])
    handed: dict = {}

    def handback(code, payload):
        handed[code] = payload
        return f"unit:u1:handback:held-{code}"
    guard = Guard(OWNER, unit="", handback=handback)
    guard.handed = handed
    return desk, shared, guard


def _turn(guard, desk, shared, tool, text="facts", confirm=""):
    h = _Harness(tool, [(shared / "kg/entities/project/meetup.md", text)])
    return list(run_harness_turn(desk, "go", h, extra_mounts=[shared], private_guard=guard,
                                 confirm_text=confirm, author=AUTHOR))


def test_private_source_names():
    assert private_source("mcp__vexa__gmail_search") == "mail"
    assert private_source("mcp__vexa__calendar_events") == "calendar"
    assert private_source("mcp__mercury__list_transactions").startswith("connected service")
    assert private_source("mcp__vexa__workspace_write") is None
    assert private_source("Read") is None


def test_a_turn_that_read_nothing_private_commits_the_shared_page(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    events = _turn(guard, desk, shared, None)
    assert not any(e["type"] == "write-withheld" for e in events)
    assert _log(shared) == 2


def test_a_turn_that_read_mail_is_held_from_the_shared_workspace(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    events = _turn(guard, desk, shared, "mcp__vexa__gmail_search", text="invoice unpaid")
    held = [e for e in events if e["type"] == "write-withheld"]
    assert len(held) == 1 and held[0]["paths"] == ["kg/entities/project/meetup.md"]
    assert held[0]["readers"] == ["val@example.com"] and held[0]["sources"] == ["mail"]
    said = "".join(e.get("text", "") for e in events if e["type"] == "message-delta")
    assert "Withheld from meetup" in said and f"confirm {held[0]['code']}" in said
    assert _log(shared) == 1
    assert not (shared / "kg/entities/project/meetup.md").exists()
    assert subprocess.run(["git", "-C", str(shared), "status", "--porcelain"], capture_output=True,
                          text=True).stdout == ""
    # NOT on any desk (H7): handed to agent-api by reference, which keeps it encrypted
    assert not (desk / ".vexa").exists()
    assert held[0]["ref"].endswith(held[0]["code"])
    assert list(guard.handed[held[0]["code"]]["files"]) == ["kg/entities/project/meetup.md"]
    assert _log(desk) == 1


def test_the_persons_own_desk_still_commits(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    h = _Harness("mcp__vexa__gmail_search", [(desk / "notes/mail.md", "invoice")])
    events = list(run_harness_turn(desk, "go", h, extra_mounts=[shared], private_guard=guard,
                                   author=AUTHOR))
    assert not any(e["type"] == "write-withheld" for e in events)
    assert _log(desk) == 2


def test_the_mark_outlives_the_turn(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    _turn(guard, desk, shared, "mcp__vexa__gmail_search")
    events = _turn(guard, desk, shared, None, text="later")
    assert any(e["type"] == "write-withheld" for e in events)
    assert _log(shared) == 1


def test_the_commit_body_of_a_marked_session_is_never_the_reply(tmp_path):
    """D6: a commit body is a multi-reader surface. A marked session committing to a shared mount
    (here: the confirmed release already in HEAD, a later unheld file is impossible) carries a fixed
    body; on the person's own desk the reply stays."""
    from llm.private_writes import NEUTRAL_BODY
    desk, shared, guard = _setup(tmp_path)
    guard.observe({"type": "tool-call", "tool": "mcp__vexa__crm_read"})
    assert guard.commit_message(shared, "the invoice is unpaid") == NEUTRAL_BODY
    assert guard.commit_message(desk, "the invoice is unpaid") == "the invoice is unpaid"


def test_a_roster_edited_by_the_turn_does_not_shrink_its_audience(tmp_path):
    """The readers come from HEAD's roster joined with the work tree's (SF-8)."""
    from llm.private_writes import other_readers
    _desk, shared, _guard = _setup(tmp_path)
    (shared / "policy" / "members.json").write_text(json.dumps([{"subject": OWNER, "role": "owner"}]))
    assert other_readers(shared, OWNER) == ["val@example.com"]


def test_an_unreadable_mark_holds(tmp_path):
    """Fail closed: a worker whose Redis cannot be read treats the session as having read private data."""
    class _Down:
        def read(self, unit):
            raise RuntimeError("redis down")

        def mark(self, *a, **k):
            raise RuntimeError("redis down")
    desk, shared, guard = _setup(tmp_path)
    guard.store, guard.unit = _Down(), "agent-7-chat-main"
    events = _turn(guard, desk, shared, None, text="anything")
    held = [e for e in events if e["type"] == "write-withheld"]
    assert held and held[0]["sources"] == ["unverifiable"]
    assert _log(shared) == 1


def test_the_mark_survives_a_worker_idle_exit(tmp_path):
    """The edge's mark lives in Redis: a NEW worker (fresh Guard, nothing observed) still holds."""
    import fakeredis
    from shared.taint import TaintStore
    client = fakeredis.FakeRedis(decode_responses=True)
    TaintStore(client).mark("agent-7-chat-main", ["crm"])          # written by the gateway edge
    desk, shared, guard = _setup(tmp_path)
    guard.store, guard.unit = TaintStore(client), "agent-7-chat-main-g2"   # a respawn, new mount gen
    events = _turn(guard, desk, shared, None)
    held = [e for e in events if e["type"] == "write-withheld"]
    assert held and held[0]["sources"] == ["crm"]
    # and the worker's own observation persists for the next worker of the conversation
    guard.observe({"type": "tool-call", "tool": "mcp__personal__list_transactions"})
    fresh = Guard(OWNER, unit="agent-7-chat-main-auto", store=TaintStore(client))
    assert "connected service (personal)" in fresh.sources()


def test_the_person_confirms_and_agent_api_writes_it_once(tmp_path, _privacy_store):
    """The release is agent-api's: the session saver took the hand-back into the encrypted store,
    and the person's `confirm <code>` writes the files and commits them before the turn runs."""
    from control_plane import held_store, private_taint
    from control_plane.session_sync import _take_held
    desk, shared, guard = _setup(tmp_path)
    ev = next(e for e in _turn(guard, desk, shared, "mcp__vexa__gmail_search", text="invoice unpaid")
              if e["type"] == "write-withheld")
    code = ev["code"]
    _privacy_store.set(ev["ref"].replace("u1", "u1"), json.dumps(guard.handed[code]))
    assert _take_held(_privacy_store, OWNER, "u1", ev)
    assert _privacy_store.get(ev["ref"]) is None                 # the plaintext hand-back is gone
    raw = _privacy_store.get(held_store.item_key(OWNER, code))
    assert raw and "invoice unpaid" not in raw and "meetup.md" not in raw   # encrypted at rest
    released = private_taint.release_confirmed_files(OWNER, f"yes confirm {code}", tmp_path)
    assert released and released[0]["paths"] == ["kg/entities/project/meetup.md"]
    assert (shared / "kg/entities/project/meetup.md").read_text() == "invoice unpaid"
    assert _log(shared) == 2
    assert private_taint.release_confirmed_files(OWNER, f"confirm {code}", tmp_path) == []   # once


def test_a_marked_turns_write_under_dot_vexa_is_held_too(tmp_path):
    """`.vexa/` is committed with the turn like any page, so it is held like any page: mail content
    written there in a workspace others read never lands in its history."""
    desk, shared, guard = _setup(tmp_path)
    h = _Harness("mcp__vexa__gmail_search", [(shared / ".vexa" / "notes.md", "invoice unpaid")])
    events = list(run_harness_turn(desk, "go", h, extra_mounts=[shared], private_guard=guard,
                                   author=AUTHOR))
    held = [e for e in events if e["type"] == "write-withheld"]
    assert held and held[0]["paths"] == [".vexa/notes.md"], events
    assert _log(shared) == 1
    assert not (shared / ".vexa" / "notes.md").exists()
    assert list(guard.handed[held[0]["code"]]["files"]) == [".vexa/notes.md"]


class _Marks:
    """The persisted mark store (shared/taint.py) as a worker that never saw the read finds it."""

    def __init__(self, sources):
        self.sources = set(sources)

    def read(self, unit):
        return set(self.sources)

    def mark(self, unit, sources, *, worker=False):
        self.sources |= set(sources)


def test_a_session_resumed_in_a_new_worker_is_still_held(tmp_path):
    """The mark lives in the store, not in the process: a fresh guard (a new worker, a resumed chat)
    that observed no private read itself still holds the shared write."""
    desk, shared, _guard = _setup(tmp_path)
    fresh = Guard(OWNER, unit="agent-7-chat-pchat-1", store=_Marks({"mail"}),
                  handback=lambda code, payload: f"ref-{code}")
    events = _turn(fresh, desk, shared, None, text="from the mail")
    assert any(e["type"] == "write-withheld" for e in events)
    assert _log(shared) == 1
