"""Frictions 1 and 2 on the files a turn writes itself (`llm.private_writes`, app.dev 2026-10-16).

1. THE AGENT SAID "SAVED" WHEN NOTHING WAS. A native write into a workspace other people read, from
   a session that read the person's mail, was put back after the turn — and the line saying so came
   after the turn's `done`, where nobody showed it, and the next turn never heard of it. Now: the
   withheld line is part of the turn (before `done`, on its `reply` and `withheld`), the next turn's
   prompt says what was not saved, and every turn of a marked session is told the rule up front and
   pointed at `workspace_write`, which refuses at once with the approval card.
2. THE TAINT WAS THE WHOLE SESSION. What a private read GAVE the agent is fingerprinted (digests,
   never the text) and only a file whose added text carries it is held. A read whose answer the
   worker did not see still holds every shared write.
"""
from __future__ import annotations

import json

from llm.claude_code import parse_stream_json
from llm.ports import run_harness_turn
from llm.private_writes import Guard
from shared.private_fingerprint import digests_of
from tests.test_private_writes_commit import AUTHOR, OWNER, _log, _repo

MAIL = ("From: Lena Fischer <lena.fischer@venue.example>. The venue invoice for the October "
        "meetup is still unpaid: €615 due Friday.")


class _Harness:
    """Reads the mail (a tool call whose result carries the read's digests), then writes pages."""

    def __init__(self, writes, digests=True, read=True):
        self.writes, self.digests, self.read = writes, digests, read
        self.prompts: list[str] = []

    def run_turn(self, work, prompt, **_):
        self.prompts.append(prompt)
        if self.read:
            yield {"type": "tool-call", "tool": "mcp__vexa__gmail_search", "callId": "c1", "args": {}}
            res = {"type": "tool-result", "callId": "c1", "ok": True, "summary": "1 message"}
            if self.digests:
                res["private_digests"] = digests_of(MAIL)
            yield res
        for path, text in self.writes:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        yield {"type": "message-delta", "text": "Saved."}
        yield {"type": "done", "ok": True, "reply": "Saved."}


def _setup(tmp_path):
    desk = _repo(tmp_path / OWNER)
    shared = _repo(tmp_path / "meetup", members=[{"subject": OWNER, "role": "owner"},
                                                 {"subject": "8", "role": "contributor",
                                                  "email": "val@example.com"}])
    return desk, shared, Guard(OWNER, desk)


def _run(guard, desk, shared, h, prompt="go"):
    return list(run_harness_turn(desk, prompt, h, extra_mounts=[shared], private_guard=guard,
                                 confirm_text=prompt, author=AUTHOR))


# ── 1: explicit, inside the turn ────────────────────────────────────────────────────────────────
def test_the_withheld_line_is_part_of_the_turn_not_after_it(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    events = _run(guard, desk, shared, _Harness([(shared / "notes/venue.md", "Venue: €615 unpaid.\n")]))
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "done"
    held = next(e for e in events if e["type"] == "write-withheld")
    line = next(i for i, e in enumerate(events)
                if e["type"] == "message-delta" and "Withheld from meetup" in e.get("text", ""))
    assert line < kinds.index("done")
    done = events[-1]
    assert "Withheld from meetup" in done["reply"] and "not saved" in done["reply"].lower()
    assert done["withheld"][0]["code"] == held["code"]


def test_the_next_turn_is_told_what_was_not_saved(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    _run(guard, desk, shared, _Harness([(shared / "notes/venue.md", "Venue: €615 unpaid.\n")]))
    h = _Harness([], read=False)
    _run(guard, desk, shared, h)
    told = h.prompts[0]
    assert "notes/venue.md" in told and "NOT saved" in told and "meetup" in told


def test_a_marked_session_is_told_the_rule_before_it_writes(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    _run(guard, desk, shared, _Harness([]))
    h = _Harness([], read=False)
    _run(guard, desk, shared, h)
    told = h.prompts[0]
    assert "workspace_write" in told and "meetup" in told and "mail" in told


def test_an_unmarked_session_is_told_nothing(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    h = _Harness([], read=False)
    _run(guard, desk, shared, h, prompt="hello")
    assert h.prompts == ["hello"]


def test_the_digests_never_leave_the_turn(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    events = _run(guard, desk, shared, _Harness([]))
    assert not any("private_digests" in e for e in events)


# ── 2: held for what it carries ─────────────────────────────────────────────────────────────────
def test_a_shared_page_with_nothing_from_the_mail_is_committed(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    events = _run(guard, desk, shared, _Harness(
        [(shared / "kg/entities/company/circleci.md", "# CircleCI\n\nCI/CD service we evaluate.\n")]))
    assert not any(e["type"] == "write-withheld" for e in events)
    assert _log(shared) == 2


def test_only_the_page_that_carries_the_mail_is_held(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    events = _run(guard, desk, shared, _Harness([
        (shared / "kg/entities/company/circleci.md", "# CircleCI\n\nCI/CD service.\n"),
        (shared / "notes/venue.md", "Ask lena.fischer@venue.example about the room.\n")]))
    held = next(e for e in events if e["type"] == "write-withheld")
    assert held["paths"] == ["notes/venue.md"]
    assert _log(shared) == 2
    assert (shared / "kg/entities/company/circleci.md").exists()
    assert not (shared / "notes/venue.md").exists()


def test_a_read_the_worker_did_not_see_holds_every_shared_write(tmp_path):
    desk, shared, guard = _setup(tmp_path)
    events = _run(guard, desk, shared, _Harness(
        [(shared / "kg/entities/company/circleci.md", "# CircleCI\n")], digests=False))
    assert any(e["type"] == "write-withheld" for e in events)
    assert _log(shared) == 1


def test_claude_code_hands_the_guard_digests_of_a_private_read_only():
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__vexa__gmail_search", "input": {"query": "x"}},
            {"type": "tool_use", "id": "t2", "name": "mcp__vexa__workspace_write", "input": {}}]}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": MAIL}]},
            {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]}}),
        json.dumps({"type": "result", "subtype": "success", "result": "ok", "session_id": "s"}),
    ]
    res = {e["callId"]: e for e in parse_stream_json(lines) if e["type"] == "tool-result"}
    assert set(res["t1"]["private_digests"]) == set(digests_of(MAIL))
    assert MAIL not in json.dumps(res["t1"])
    assert "private_digests" not in res["t2"]
