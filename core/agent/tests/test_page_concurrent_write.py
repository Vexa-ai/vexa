"""Two writers on one page never lose a line (#1783, collaboration rehearsal 2026-10-14).

Two agents in one shared workspace each read `notes.md`, added a bullet and wrote the page back
whole; the second write erased the first bullet. A write whose text derives from an earlier
version than the current one is merged three ways; same-line changes are refused with the page as
it is now; the single-writer case is untouched.
"""
from __future__ import annotations

import subprocess
import threading

import pytest

from control_plane import page_merge
from tests.test_global_root import _client, _repo, _settings

START = "# Meeting notes\n\n- (start)\n"
WITH_A = START + "- A: budget approved at 40k\n"
WITH_B = START + "- B: launch moved to November\n"


def _commit(repo, rel, text, msg="w"):
    (repo / rel).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "--", rel], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", msg], check=True)


@pytest.fixture
def repo(tmp_path):
    r = _repo(tmp_path / "ws")
    _commit(r, "notes.md", START, "seed")
    return r


def test_a_write_started_from_an_older_version_keeps_the_other_writers_line(repo):
    _commit(repo, "notes.md", WITH_A)
    out, merged = page_merge.reconcile(repo, "notes.md", WITH_B)
    assert merged
    assert "- A: budget approved at 40k" in out and "- B: launch moved to November" in out
    assert out.index("A: budget") < out.index("B: launch")


def test_a_write_from_the_current_version_is_taken_as_written(repo):
    _commit(repo, "notes.md", WITH_A)
    want = WITH_A + "- C: next step\n"
    assert page_merge.reconcile(repo, "notes.md", want) == (want, False)


def test_a_rewrite_from_the_current_version_is_taken_as_written(repo):
    _commit(repo, "notes.md", WITH_A)
    want = "# Notes\n\nall new\n"
    assert page_merge.reconcile(repo, "notes.md", want) == (want, False)


def test_an_undo_to_an_earlier_version_is_taken_as_written(repo):
    _commit(repo, "notes.md", WITH_A)
    assert page_merge.reconcile(repo, "notes.md", START) == (START, False)


BASE6 = "# Plan\n\n- goals\n- owner: tbd\n- risks\n- dates\n"


def test_both_changing_the_same_line_is_refused_with_the_page_as_it_is(repo):
    _commit(repo, "notes.md", BASE6)
    _commit(repo, "notes.md", BASE6.replace("tbd", "A") + "- A extra\n")
    with pytest.raises(page_merge.PageConflict) as e:
        page_merge.reconcile(repo, "notes.md", BASE6.replace("tbd", "B"))
    assert "owner: A" in e.value.current


def test_disjoint_edits_merge_cleanly(repo):
    base = "# T\n\nfirst\n\nmiddle\n\nlast\n"
    _commit(repo, "notes.md", base)
    _commit(repo, "notes.md", base.replace("first", "first (A)"))
    out, merged = page_merge.reconcile(repo, "notes.md", base.replace("last", "last (B)"))
    assert merged and "first (A)" in out and "last (B)" in out


# ── the route: what the agent's `workspace_write` calls ─────────────────────────────────────────

U = {"X-User-Id": "u_writer"}


def _desk(tmp_path):
    store = tmp_path / "workspaces"
    store.mkdir()
    _repo(store / "u_writer")  # a person's workspace is a git repository
    c = _client(store, _settings(store, ""))
    r = c.put("/api/workspace/file", headers=U, json={"path": "notes.md", "content": START})
    assert r.status_code == 200, r.text
    return c


def test_the_route_merges_and_says_so(tmp_path):
    c = _desk(tmp_path)
    assert c.put("/api/workspace/file", headers=U, json={"path": "notes.md", "content": WITH_A}).status_code == 200
    r = c.put("/api/workspace/file", headers=U, json={"path": "notes.md", "content": WITH_B})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["merged"] is True and "merged" in body["said"]
    page = c.get("/api/workspace/file", headers=U, params={"path": "notes.md"}).json()["content"]
    assert "A: budget" in page and "B: launch" in page


def test_the_route_refuses_a_same_line_change_and_writes_nothing(tmp_path):
    c = _desk(tmp_path)
    c.put("/api/workspace/file", headers=U, json={"path": "notes.md", "content": BASE6})
    c.put("/api/workspace/file", headers=U, json={"path": "notes.md",
                                                   "content": BASE6.replace("tbd", "A") + "- A extra\n"})
    r = c.put("/api/workspace/file", headers=U, json={"path": "notes.md", "content": BASE6.replace("tbd", "B")})
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["refused"] == "page changed while you were editing it" and "owner: A" in d["current"]
    page = c.get("/api/workspace/file", headers=U, params={"path": "notes.md"}).json()["content"]
    assert "owner: A" in page and "owner: B" not in page


def test_simultaneous_writes_keep_both_lines(tmp_path):
    c = _desk(tmp_path)
    res = []

    def w(text):
        res.append(c.put("/api/workspace/file", headers=U, json={"path": "notes.md", "content": text}).status_code)

    ts = [threading.Thread(target=w, args=(t,)) for t in (WITH_A, WITH_B)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res == [200, 200]
    page = c.get("/api/workspace/file", headers=U, params={"path": "notes.md"}).json()["content"]
    assert "A: budget" in page and "B: launch" in page


# ── the merge itself, in memory (no file is opened by name: test_worktree_io_single_path) ─────────

def test_merge_keeps_the_same_change_once():
    base = "a\nb\nc\n"
    assert page_merge._merge3("a\nB\nc\n", base, "a\nB\nc\n") == "a\nB\nc\n"


def test_merge_applies_edits_in_different_places_and_both_insertions():
    base = "a\nb\nc\nd\n"
    out = page_merge._merge3("a\nb\nc\nd\nours\n", base, "A\nb\nc\nd\ntheirs\n")
    assert out == "A\nb\nc\nd\ntheirs\nours\n"


def test_merge_refuses_two_different_edits_of_one_line():
    with pytest.raises(page_merge.PageConflict):
        page_merge._merge3("a\nX\nc\n", "a\nb\nc\n", "a\nY\nc\n")
