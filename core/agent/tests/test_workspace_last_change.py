"""`GET /api/workspaces/{slug}/git/last-change` — the front page's second sentence (Vexa-ai/vexa#1634).

Founder, 2026-09-06, on the strip #1628 had just sized down: *"what about this one? never spoke
about how to make it right, helpful and nice."* The line under the title was a git log read out
loud — a commit subject, an author id, a file count — where a person needs *Changed 14 minutes ago
by Jane Smith: the policies wizard ask*.

Neither half of that sentence exists in a git log, and that is the whole reason this route exists.
So the tests below hold exactly those two claims, plus the scope:

  * **THE THING, BY ITS TITLE.** `title:` → the first `# ` heading → the file's own name, and
    several pages become a count. The floor matters as much as the first two: `asks/policies-
    wizard.md` has neither a title nor a heading — it opens with the prompt it is — and *the
    policies wizard ask* is the founder's own words for it, which is its file name read aloud.
  * **THE PERSON, BY THEIR NAME, NEVER AN ADDRESS.** Their own `self: true` page first, then the
    company directory, then nothing. `None` is an answer the panel renders as *someone*; an
    address in a name's place is the defect this route was opened to remove.
  * **THE SCOPE IS `_read_target`'S**, the same call the file read makes — so this route can
    describe no commit whose page a subject could not open. Held here as a COMPARISON against
    `GET /api/workspace/file`, exactly as `test_workspace_history.py` holds it for the history.

Offline L2 over real git repositories — no docker, no network. The scaffolding is
`test_workspace_history.py`'s, inlined so the module stands alone.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane import front_page
from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _commit(ws: Path, files: dict[str, str], message: str, *, who=("owner1", "owner1@vexa.local")) -> None:
    for path, text in files.items():
        f = ws / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    _git(ws, "add", "-A")
    _git(ws, "-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}", "commit", "-q", "-m", message)


def _init_ws(root: Path, slug: str) -> Path:
    ws = root / slug
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "owner1@vexa.local")
    _git(ws, "config", "user.name", "owner1")
    _commit(ws, {"README.md": "# hello\n"}, "seed")
    return ws


def _client(root: Path, index=None) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=index or m.InMemoryMembershipIndex(),
    ))


def _h(subject: str) -> dict:
    return {"X-User-Id": subject}


def _shared(root: Path, slug: str, owner: str = "owner1", subject: str | None = None, role="contributor"):
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(root, slug, owner, index=idx)
    if subject:
        m.grant_membership(root, slug, subject, role, added_by=owner, index=idx)
    return idx


def _desk_person(root: Path, subject: str, page: str) -> None:
    """That subject's own desk, with one person page on it."""
    d = root / subject / front_page.PERSON_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "me.md").write_text(page)


SELF_PAGE = "---\nself: true\nname: Jane Smith\n---\n\n# Jane Smith\n\nShe works here.\n"


def _change(root: Path, slug: str, subject: str, idx=None, **params) -> dict:
    r = _client(root, idx).get(f"/api/workspaces/{slug}/git/last-change",
                               headers=_h(subject), params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ── the thing, by its title ─────────────────────────────────────────────────────────────────────
def test_the_changed_page_comes_back_by_its_frontmatter_title(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _desk_person(tmp_path, "owner1", SELF_PAGE)
    _commit(ws, {"kg/board.md": "---\ntitle: the governing board\n---\n\n# Board\n\nrows\n"},
            "wsA: kg/board.md — added")

    body = _change(tmp_path, "wsA", "owner1", idx)

    change = body["change"]
    assert change["count"] == 1
    assert [p["title"] for p in change["pages"]] == ["the governing board"]
    # …and the sentence's other half, which no git log carries either
    assert change["author"] == "Jane Smith"
    assert change["when"]                       # git's own relative string, read rather than retyped


def test_the_first_heading_when_there_is_no_title(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {"kg/board.md": "# the governing board\n\nrows\n"}, "wsA: kg/board.md — added")

    assert [p["title"] for p in _change(tmp_path, "wsA", "owner1", idx)["change"]["pages"]] \
        == ["the governing board"]


def test_the_files_own_name_when_it_carries_neither(tmp_path):
    """`asks/policies-wizard.md` has no title and no heading — it opens with the prompt it IS. The
    floor is its own name read aloud, which is what the founder called it in the issue."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {"asks/policies-wizard.md": "---\nlabel: policies\n---\n[policies-wizard] You are…\n"},
            "wsA: asks/policies-wizard.md — added")

    assert [p["title"] for p in _change(tmp_path, "wsA", "owner1", idx)["change"]["pages"]] \
        == ["policies wizard"]


def test_several_pages_are_a_count_and_machinery_is_not_one_of_them(tmp_path):
    """A turn-commit touches several files. The pages are counted; `policy/` and `CLAUDE.md` are not
    pages a person opens, and counting them would say "seven pages" about a change to five."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {**{f"kg/p{i}.md": f"# page {i}\n" for i in range(5)},
                 "policy/notes.md": "# a policy note\n", "CLAUDE.md": "# conventions\n"},
            "wsA: MISSING.md, OBJECTIVES.md +13 — 7 files changed")

    change = _change(tmp_path, "wsA", "owner1", idx)["change"]
    assert change["count"] == 5
    assert sorted(p["path"] for p in change["pages"]) == [f"kg/p{i}.md" for i in range(5)]
    assert all("policy/" not in p["path"] for p in change["pages"])


def test_a_path_narrows_it_to_when_that_page_last_changed(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {"kg/board.md": "# the governing board\n"}, "wsA: kg/board.md — added")
    _commit(ws, {"kg/other.md": "# something else\n"}, "wsA: kg/other.md — added")

    whole = _change(tmp_path, "wsA", "owner1", idx)["change"]
    scoped = _change(tmp_path, "wsA", "owner1", idx, path="kg/board.md")["change"]

    assert [p["title"] for p in whole["pages"]] == ["something else"]
    assert [p["title"] for p in scoped["pages"]] == ["the governing board"]
    assert scoped["sha"] != whole["sha"]


def test_a_workspace_nothing_has_been_committed_to_answers_with_no_change(tmp_path):
    """An ordinary state of a new workspace, not a failure — a 404 would render as an error."""
    ws = tmp_path / "wsA"
    (ws / "kg").mkdir(parents=True)
    idx = _shared(tmp_path, "wsA")

    body = _change(tmp_path, "wsA", "owner1", idx)
    assert body["change"] is None


# ── the person, by their name ───────────────────────────────────────────────────────────────────
def test_the_author_is_named_from_their_own_self_page(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _desk_person(tmp_path, "owner1", SELF_PAGE)
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added")

    assert _change(tmp_path, "wsA", "owner1", idx)["change"]["author"] == "Jane Smith"


def test_a_person_page_without_self_true_is_not_that_persons_name(tmp_path):
    """A desk holds pages about OTHER people too — `self: true` is what says which one is theirs."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _desk_person(tmp_path, "owner1", "---\nname: Somebody Else\n---\n\n# Somebody Else\n")
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added")

    assert _change(tmp_path, "wsA", "owner1", idx)["change"]["author"] is None


def test_the_company_directory_answers_when_the_desk_does_not(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    d = tmp_path / front_page.GLOBAL_SLUG / front_page.PERSON_DIR
    d.mkdir(parents=True)
    (d / "jane-smith.md").write_text("---\nsubject: owner1\nemail: jsmith@example.com\n---\n\n# Jane Smith\n")
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added")

    assert _change(tmp_path, "wsA", "owner1", idx)["change"]["author"] == "Jane Smith"


def test_it_never_answers_with_an_address(tmp_path):
    """Nothing written down anywhere: the answer is null, and the panel says *someone*. The one
    thing this must never do is reach for the address it can see — that is the line the founder
    read as repository facts rather than as a sentence about a place."""
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added",
            who=("jsmith@example.com", "jsmith@example.com"))

    change = _change(tmp_path, "wsA", "owner1", idx)["change"]
    assert change["author"] is None
    assert "@" not in str(change["author"])


def test_a_page_naming_itself_with_an_address_is_still_not_a_name(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    _desk_person(tmp_path, "owner1", "---\nself: true\nname: jsmith@example.com\n---\n")
    _commit(ws, {"kg/board.md": "# board\n"}, "wsA: kg/board.md — added")

    assert _change(tmp_path, "wsA", "owner1", idx)["change"]["author"] is None


def test_the_reader_asks_what_they_are_called(tmp_path):
    _desk_person(tmp_path, "owner1", SELF_PAGE)

    r = _client(tmp_path).get("/api/people/me", headers=_h("owner1"))

    assert r.status_code == 200
    assert r.json() == {"subject": "owner1", "name": "Jane Smith", "first_name": "Jane"}


def test_a_reader_nobody_has_written_down_is_named_null_not_addressed(tmp_path):
    r = _client(tmp_path).get("/api/people/me", headers=_h("owner1"))

    assert r.status_code == 200
    assert r.json() == {"subject": "owner1", "name": None, "first_name": None}


def test_the_roster_carries_names_beside_the_addresses(tmp_path):
    """The front page's FIRST sentence is people ("you, Jane Smith and 2 more"), and the roster is
    where they come from. Additive: a member nobody has written down has no `name` key at all."""
    _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA", subject="member2")
    _desk_person(tmp_path, "member2", SELF_PAGE)

    r = _client(tmp_path, idx).get("/api/workspace/members", params={"workspace_id": "wsA"},
                                   headers=_h("owner1"))

    assert r.status_code == 200
    rows = {row["subject"]: row for row in r.json()["members"]}
    assert rows["member2"]["name"] == "Jane Smith"
    assert "name" not in rows["owner1"]                    # nothing written down → nothing claimed


# ── the scope, held against the file read ───────────────────────────────────────────────────────
def test_a_stranger_is_refused_exactly_as_the_file_read_refuses_them(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")
    c = _client(tmp_path, idx)

    described = c.get("/api/workspaces/wsA/git/last-change", headers=_h("stranger"))
    read = c.get("/api/workspace/file", params={"path": "README.md", "slug": "wsA"},
                 headers=_h("stranger"))

    assert described.status_code == read.status_code == 403


def test_a_member_is_answered_exactly_as_the_file_read_answers_them(tmp_path):
    ws = _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA", subject="reader1", role="viewer")
    _commit(ws, {"kg/board.md": "# the governing board\n"}, "wsA: kg/board.md — added")
    c = _client(tmp_path, idx)

    described = c.get("/api/workspaces/wsA/git/last-change", headers=_h("reader1"))
    read = c.get("/api/workspace/file", params={"path": "README.md", "slug": "wsA"},
                 headers=_h("reader1"))

    assert described.status_code == read.status_code == 200
    assert [p["title"] for p in described.json()["change"]["pages"]] == ["the governing board"]


def test_system_is_refused_the_way_the_history_refuses_it(tmp_path):
    r = _client(tmp_path).get("/api/workspaces/_system/git/last-change", headers=_h("owner1"))

    assert r.status_code == 403
    assert "sessions and settings" in r.json()["detail"]


def test_personal_is_the_desk(tmp_path):
    """The terminal's desk tab carries no slug, so a path segment needs a word — the same one the
    history route takes."""
    desk = _init_ws(tmp_path, "owner1")
    _commit(desk, {"kg/note.md": "# a note to self\n"}, "personal: kg/note.md — added")

    body = _change(tmp_path, "personal", "owner1")

    assert [p["title"] for p in body["change"]["pages"]] == ["a note to self"]
    assert body["change"]["kind"] == "you"


def test_a_path_that_leaves_the_workspace_is_refused(tmp_path):
    _init_ws(tmp_path, "wsA")
    idx = _shared(tmp_path, "wsA")

    r = _client(tmp_path, idx).get("/api/workspaces/wsA/git/last-change",
                                   params={"path": "../../etc/passwd"}, headers=_h("owner1"))

    assert r.status_code == 400
