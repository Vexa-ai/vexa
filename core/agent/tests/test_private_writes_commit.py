"""The leak rule at the turn's commit (`llm.private_writes`, `llm.ports.run_harness_turn`).

A turn that read the person's mail and then wrote, with its own file tools, into a workspace another
member reads: that change is not committed, the workspace is put back, the turn says what it held
and the code that releases it, and the person's `confirm <code>` writes it once. A turn that read
nothing private, or that wrote only to the person's own desk, commits as before.
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
    return desk, shared, Guard(OWNER, desk)


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
    # waiting on the person's own desk, out of its history
    assert (desk / ".vexa/held-writes" / f"{held[0]['code']}.json").exists()
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


def test_the_person_confirms_and_it_is_written_once(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    code = next(e for e in _turn(guard, desk, shared, "mcp__vexa__gmail_search", text="invoice unpaid")
                if e["type"] == "write-withheld")["code"]
    h = _Harness(None, [])
    events = list(run_harness_turn(desk, "go", h, extra_mounts=[shared], private_guard=guard,
                                   confirm_text=f"yes confirm {code}", author=AUTHOR))
    assert any(e["type"] == "write-released" for e in events)
    assert not any(e["type"] == "write-withheld" for e in events)
    assert (shared / "kg/entities/project/meetup.md").read_text() == "invoice unpaid"
    assert _log(shared) == 2
    assert not (desk / ".vexa/held-writes" / f"{code}.json").exists()
