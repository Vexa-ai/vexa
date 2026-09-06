"""`GET /api/workspaces/{slug}/git/history` — the workspace README's history section, and its scope.

Founder, 2026-09-06, looking at a customer workspace's `README.md` in the preview
(Vexa-ai/vexa#1623): *"if it's a workspace readme we want to have data — shared with whom, controls
like github sync, git history lookup, etc."*

The whole risk in a history route is the scope, so that is what these tests are about. The claim
being held is narrow and testable: **this route can show no page a subject could not already read**,
because it does not decide who may read anything — `_read_target` does, the same call the file read
makes. So the tests below are written as a COMPARISON against `GET /api/workspace/file` wherever
there is an answer to compare: the two routes agree on a member, on a stranger, and on `_global`.

Two deliberate narrowings get their own tests, because a narrowing that is not tested is a narrowing
that gets removed by the next person who finds it surprising:

  * `_system` is refused, though `_read_target` would resolve it — it is chats/sessions/settings, the
    one tier `_global/POLICIES.md` calls genuinely private, and it has no README to put this on;
  * `personal` is the desk, so the terminal's slug-less desk tab has a name to put in a path segment.

Offline L2 over real git repositories — no docker, no network. The scaffolding is
`test_lane_a_shared_mounts`'s, inlined so the module stands alone.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_git_sync import detach_home, home_remote
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


def _commit(ws: Path, path: str, text: str, message: str, *, who=("t", "t@t")) -> None:
    f = ws / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    _git(ws, "add", "-A")
    _git(ws, "-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}", "commit", "-q", "-m", message)


def _init_ws(root: Path, slug: str) -> Path:
    """A real git workspace with three commits: the seed, one README edit, one page elsewhere."""
    ws = root / slug
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    _commit(ws, "README.md", "# hello\n", "seed")
    _commit(ws, "kg/entities/acme.md", "# Acme\n", "entity acme")
    _commit(ws, "README.md", "# hello\n\nlinks\n", "readme: link the entity")
    return ws


def _client(root: Path, index=None) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=index or m.InMemoryMembershipIndex(),
    ))


def _h(subject: str) -> dict:
    return {"X-User-Id": subject}


#: The three commits `_init_ws` makes, newest-first. Anything else in a history read is the app's own
#: identity plumbing (`<slug>: workspace identity`, authored `Vexa`), which is a real commit in a real
#: workspace and is deliberately NOT filtered out of the route — so the tests read past it instead.
SEEDED = ["readme: link the entity", "entity acme", "seed"]


def _ours(body: dict) -> list[str]:
    return [c["msg"] for c in body["commits"] if c["msg"] in SEEDED]


def _shared(root: Path, slug: str, owner: str, subject: str | None = None, role="contributor"):
    idx = m.InMemoryMembershipIndex()
    _init_ws(root, slug)
    m.ensure_owner(root, slug, owner, index=idx)
    if subject:
        m.grant_membership(root, slug, subject, role, added_by=owner, index=idx)
    return idx


# ── the ordinary read ────────────────────────────────────────────────────────────────────────────
def test_a_member_reads_the_workspace_history_newest_first(tmp_path):
    idx = _shared(tmp_path, "wsA", owner="owner1", subject="reader1", role="viewer")

    r = _client(tmp_path, idx).get("/api/workspaces/wsA/git/history", headers=_h("reader1"))

    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "wsA"
    assert _ours(body) == SEEDED                      # newest first
    # every commit carries who and what — the four columns the panel renders
    top = next(c for c in body["commits"] if c["msg"] == "readme: link the entity")
    assert top["author"] and top["when"] and top["files"] == ["README.md"]


def test_the_path_filter_narrows_to_one_page(tmp_path):
    idx = _shared(tmp_path, "wsA", owner="owner1", subject="reader1", role="viewer")
    c = _client(tmp_path, idx)

    all_of_it = c.get("/api/workspaces/wsA/git/history", headers=_h("reader1")).json()
    one_page = c.get("/api/workspaces/wsA/git/history?path=kg/entities/acme.md",
                     headers=_h("reader1")).json()

    assert _ours(all_of_it) == SEEDED
    assert [c["msg"] for c in one_page["commits"]] == ["entity acme"]
    assert one_page["path"] == "kg/entities/acme.md"


def test_two_commits_two_pages_and_each_filter_returns_only_its_own(tmp_path):
    """*This page only* must MEAN this page only (Vexa-ai/vexa#1628 point 2).

    The founder's reading of the screenshot was *"either the route ignores `path=` or the client
    never sends it"*, and this is the route's half of the answer, stated the narrow way: two commits
    that touch two different pages, then each filter asked for in turn. Excluding the wrong commit is
    the claim — the test above only ever checked that the right one survives, and a route that
    ignored `path` entirely would pass it whenever the pathspec happened to name the newest file.

    The third commit is the one that matters most: it touches BOTH pages, so it must appear under
    both filters, and its `files` must come back narrowed to the pathspec that matched it. That is
    what lets the panel show the reader what the filter actually matched instead of a commit message
    that names some other file."""
    ws = _init_ws(tmp_path, "42")
    _commit(ws, "asks/prep.md", "# Prep\n", "asks/prep.md — added")
    f = ws / "kg/entities/acme.md"
    f.write_text("# Acme\n\nmore\n")
    (ws / "README.md").write_text("# hello\n\nlinks\n\nand more\n")
    _git(ws, "add", "-A")
    _git(ws, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "both pages at once")
    c = _client(tmp_path)

    readme = c.get("/api/workspaces/personal/git/history?path=README.md", headers=_h("42")).json()
    entity = c.get("/api/workspaces/personal/git/history?path=kg/entities/acme.md", headers=_h("42")).json()
    asks = c.get("/api/workspaces/personal/git/history?path=asks/prep.md", headers=_h("42")).json()

    # what each page's history IS…
    assert [x["msg"] for x in readme["commits"]] == ["both pages at once", "readme: link the entity", "seed"]
    assert [x["msg"] for x in entity["commits"]] == ["both pages at once", "entity acme"]
    assert [x["msg"] for x in asks["commits"]] == ["asks/prep.md — added"]
    # …and, the half that was never asserted, what it is NOT
    assert "asks/prep.md — added" not in [x["msg"] for x in readme["commits"]]
    assert "entity acme" not in [x["msg"] for x in readme["commits"]]
    # the shared commit comes back under both, with its file list narrowed to what matched
    both_r = next(x for x in readme["commits"] if x["msg"] == "both pages at once")
    both_e = next(x for x in entity["commits"] if x["msg"] == "both pages at once")
    assert both_r["sha"] == both_e["sha"]
    assert both_r["files"] == ["README.md"] and both_e["files"] == ["kg/entities/acme.md"]
    # and the whole workspace still shows all four
    every = c.get("/api/workspaces/personal/git/history", headers=_h("42")).json()
    assert {"both pages at once", "asks/prep.md — added", *SEEDED} <= {x["msg"] for x in every["commits"]}


def test_limit_is_honoured_and_bounded(tmp_path):
    idx = _shared(tmp_path, "wsA", owner="owner1", subject="reader1", role="viewer")
    c = _client(tmp_path, idx)

    assert len(c.get("/api/workspaces/wsA/git/history?limit=1", headers=_h("reader1")).json()["commits"]) == 1
    # a caller cannot ask this to walk an unbounded history: the ceiling is the server's
    assert c.get("/api/workspaces/wsA/git/history?limit=100000", headers=_h("reader1")).json()["limit"] == 200


def test_a_workspace_with_no_commits_yet_is_an_empty_list_not_an_error(tmp_path):
    """A seeded-but-never-committed workspace is an ordinary state. The panel renders "no history
    yet"; it must not render an error, and it must not be handed one."""
    (tmp_path / "42").mkdir(parents=True)

    r = _client(tmp_path).get("/api/workspaces/personal/git/history", headers=_h("42"))

    assert r.status_code == 200
    assert r.json()["commits"] == []


# ── the scope: the same answers the file read gives ──────────────────────────────────────────────
def test_a_stranger_is_refused_exactly_as_the_file_read_refuses_them(tmp_path):
    """The claim of the whole route in one test: history and text are scoped by the SAME call, so
    they cannot disagree about who may see this workspace."""
    idx = _shared(tmp_path, "wsA", owner="owner1")
    c = _client(tmp_path, idx)

    file_read = c.get("/api/workspace/file?path=README.md&slug=wsA", headers=_h("stranger"))
    history = c.get("/api/workspaces/wsA/git/history", headers=_h("stranger"))

    assert file_read.status_code == 403
    assert history.status_code == file_read.status_code


def test_personal_is_the_callers_own_desk(tmp_path):
    """The terminal's desk tab carries no slug — its file reads pass none — but a path segment
    cannot be empty. `personal` is the name the rest of the system already uses for that."""
    _init_ws(tmp_path, "42")

    r = _client(tmp_path).get("/api/workspaces/personal/git/history", headers=_h("42"))

    assert r.status_code == 200
    assert _ours(r.json()) == SEEDED


def test_a_path_that_climbs_out_of_the_workspace_is_refused(tmp_path):
    """`path` is a pathspec handed to `git log`. `git log -- ../../etc` reads history from outside
    the workspace, so it goes through the same guard every other caller-supplied path does."""
    _init_ws(tmp_path, "42")

    r = _client(tmp_path).get("/api/workspaces/personal/git/history?path=../../etc/passwd",
                              headers=_h("42"))

    assert r.status_code == 400


# ── the two narrowings ───────────────────────────────────────────────────────────────────────────
def test_system_is_refused_even_though_a_file_read_of_it_is_not(tmp_path):
    """`_system` is the caller's OWN private tier, so `_read_target` resolves it and the file route
    serves it. This route refuses it anyway: sessions and settings are not a workspace's history,
    there is no `_system` README, and `POLICIES.md` calls it the one genuinely private tier."""
    r = _client(tmp_path).get("/api/workspaces/_system/git/history", headers=_h("42"))

    assert r.status_code == 403
    assert "_system" in r.json()["detail"]


def test_global_is_readable_by_every_subject(tmp_path):
    """The org tier is mounted read-only into every worker and every chat, so its history answers
    everyone — the same answer `_read_target` already gives the file read."""
    _init_ws(tmp_path, "_global")
    c = _client(tmp_path)

    for subject in ("42", "7", "someone-else"):
        r = c.get("/api/workspaces/_global/git/history", headers=_h(subject))
        assert r.status_code == 200, subject
        assert _ours(r.json()) == SEEDED, subject


# ── detach: the inverse of attach, and nothing more ──────────────────────────────────────────────
def test_detaching_removes_the_remote_and_touches_no_file(tmp_path):
    """The fourth sync control. The risk it is written against is implementing "detach" as "swap
    back to the parked tree", which loses the tree the person is reading."""
    ws = _init_ws(tmp_path, "42")
    _git(ws, "remote", "add", "origin", "https://github.com/acme/kg.git")
    before = _git(ws, "rev-parse", "HEAD")

    gone = detach_home(ws)

    # `_display_url` is what the sync section already shows a person — token-free, `.git` trimmed
    assert gone == ("origin", "https://github.com/acme/kg")
    assert home_remote(ws) is None
    assert (ws / "README.md").read_text() == "# hello\n\nlinks\n"     # the tree is untouched
    assert _git(ws, "rev-parse", "HEAD") == before                    # so is the history
    assert _git(ws, "status", "--porcelain") == ""


def test_detaching_a_workspace_with_no_home_is_a_no_op_not_an_error(tmp_path):
    ws = _init_ws(tmp_path, "42")

    assert detach_home(ws) is None

    r = _client(tmp_path).post("/api/workspace/git-remote-detach", json={}, headers=_h("42"))
    assert r.status_code == 200
    assert r.json()["detached"] is False
