"""REMOVING AND MOVING A PAGE (Vexa-ai/vexa#1621) — the verbs "remove from personal" needed.

Friction `fr_a373e9448d2909a6`, founder session 176, 13:36Z: *"remove from personal"*. The agent was
moving a seven-page customer dossier off the desk into that customer's own workspace, and found that
`workspace_write` creates or overwrites and everything else is read-only. So it OVERWROTE each page
with a one-line pointer and had to tell the founder that "removed" meant "collapsed" — the seven
files were still on the desk.

What has to be true, and what each section below pins:

* **a removal is a COMMIT** — the page leaves the working tree and stays in the history, which is
  the whole reason an agent may do this without asking twice;
* **a move inside one workspace leaves a POINTER** at the old path, so a `[[wikilink]]` written
  before the move still lands somewhere that says where the page went;
* **a move ACROSS workspaces is a write in the target and a delete in the source** — two
  repositories, two commits, and NO pointer (the containment rule: a note naming a path in another
  workspace is a reference its readers cannot follow);
* **either end read-only refuses the whole move** — `_system` always, `_global` unless the caller is
  an org admin — and it refuses BEFORE anything is written, because half a move is the one outcome
  worse than no move;
* **the write-back phase does not put back what the turn just took away** — the phase asks "which
  names has no desk got a page for", and a page deleted a second ago is exactly such a name.

L2: a real FastAPI app over fakes and real git repositories in `tmp_path` — no redis, no runtime, no
model. The membership scaffolding is `test_lane_a_shared_mounts`'s, reused rather than re-invented
for the reason that file's own neighbours give: the refusals must hold against the same
authoritative `policy/members.json` Lane A itself reads.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from workspaces.shared import entities as entities_mod

from tests.test_api import _FakeIdentity, _FakeRuntime

JANE = "u_jane"
ADMIN = "u_admin"


# ── the harness ──────────────────────────────────────────────────────────────────────────────────

def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _init_ws(root: Path, slug: str) -> Path:
    ws = root / slug
    ws.mkdir(parents=True, exist_ok=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _page(ws: Path, rel: str, text: str = "# OeNB\n\nThe dossier.\n") -> Path:
    f = ws / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    _git(ws, "add", "--", rel)
    _git(ws, "commit", "-q", "-m", f"add {rel}", "--", rel)
    return f


def _settings(root: Path, *, admins: str = ""):
    return load_settings(workspaces_dir=str(root),
                         global_system_workspace_path=str(root / "_global"),
                         global_admin_subjects=admins,
                         internal_api_secret="s", ui_url="https://app.example.test", redis_url="")


def _client(root: Path, *, admins: str = "", index=None) -> TestClient:
    settings = _settings(root, admins=admins)
    return TestClient(create_app(
        Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=index or m.InMemoryMembershipIndex(),
    ))


def _h(subject: str) -> dict:
    return {"X-User-Id": subject}


def _delete(c: TestClient, subject: str, path: str, slug: str | None = None):
    body: dict = {"path": path}
    if slug is not None:
        body["slug"] = slug
    return c.post("/api/workspace/remove", json=body, headers=_h(subject))


def _move(c: TestClient, subject: str, **body):
    return c.post("/api/workspace/move", json=body, headers=_h(subject))


# ── 1. a removal is a commit, never a loss ───────────────────────────────────────────────────────

def test_a_deleted_page_leaves_the_desk_and_stays_in_the_history(tmp_path):
    ws = _init_ws(tmp_path, JANE)
    _page(ws, "kg/entities/company/oenb.md")
    r = _delete(_client(tmp_path), JANE, "kg/entities/company/oenb.md")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True and body["path"] == "kg/entities/company/oenb.md"
    # gone from the working tree — the founder's whole ask
    assert not (ws / "kg/entities/company/oenb.md").exists()
    # and recoverable: the commit is a removal, and the bytes are one `git show` behind it
    assert body["commit"] and body["commit"] == _git(ws, "rev-parse", "HEAD")
    assert "D\tkg/entities/company/oenb.md" in _git(ws, "show", "--name-status", "--format=", "HEAD")
    assert "The dossier." in _git(ws, "show", "HEAD~1:kg/entities/company/oenb.md")


def test_the_commit_subject_says_which_page_went(tmp_path):
    """`git log --oneline` is what a person reads when they are working out what happened — the
    same F31 subject shape every other workspace writer uses."""
    ws = _init_ws(tmp_path, JANE)
    _page(ws, "notes/plan.md")
    _delete(_client(tmp_path), JANE, "notes/plan.md")
    assert _git(ws, "log", "-1", "--format=%s") == f"{JANE}: notes/plan.md — removed"


def test_removing_an_entity_page_refreshes_the_index_the_next_turn_reads(tmp_path):
    """`kg/INDEX.md` rides in EVERY dispatch (`worker/engine.entity_index_preamble`). A page removed
    without it is a page the next turn is still told the workspace holds."""
    ws = _init_ws(tmp_path, JANE)
    _page(ws, "kg/entities/company/oenb.md")
    entities_mod.write_index(ws, JANE)
    assert "oenb" in (ws / entities_mod.INDEX_PATH).read_text()
    _delete(_client(tmp_path), JANE, "kg/entities/company/oenb.md")
    assert "oenb" not in (ws / entities_mod.INDEX_PATH).read_text()


def test_deleting_a_draft_does_not_mint_an_entity_index(tmp_path):
    """The index is CREATED by `write_index`, so refreshing it on every removal would grow a
    `kg/INDEX.md` in a workspace that has never held an entity."""
    ws = _init_ws(tmp_path, JANE)
    _page(ws, "notes/draft.md")
    _delete(_client(tmp_path), JANE, "notes/draft.md")
    assert not (ws / entities_mod.INDEX_PATH).exists()


def test_a_page_that_is_not_there_is_a_404_and_a_folder_is_a_400(tmp_path):
    """Both are ANSWERS the agent is meant to say out loud, not states to retry through."""
    ws = _init_ws(tmp_path, JANE)
    _page(ws, "notes/plan.md")
    c = _client(tmp_path)
    assert _delete(c, JANE, "notes/gone.md").status_code == 404
    assert _delete(c, JANE, "notes").status_code == 400


def test_a_path_that_leaves_the_workspace_is_refused(tmp_path):
    """The same guard the read and write routes run — a removal that could be talked out of the
    workspace root is `rm` with a friendly name."""
    _init_ws(tmp_path, JANE)
    c = _client(tmp_path)
    for bad in ("../u_bob/README.md", "/etc/passwd", ".git/config"):
        assert _delete(c, JANE, bad).status_code in (400, 403), bad


def test_removing_a_page_cannot_be_confused_with_destroying_the_workspace(tmp_path):
    """THE SHAPE THIS ROUTE WAS WRITTEN AS FIRST, and why it is not that.

    `DELETE /api/workspace/file` reads perfectly and collides with `DELETE /api/workspace/{slug}` —
    which destroys a whole workspace irreversibly, and whose `{slug}` matches the literal segment
    `file`. Two routes matching one URL means registration order decides the answer, and of every
    pair in this app to leave to registration order that is the worst one. Caught by
    `test_route_table.test_no_two_routes_can_match_the_same_url`; pinned here so the reason travels
    with the verb rather than only with the gate."""
    ws = _init_ws(tmp_path, JANE)
    _page(ws, "notes/plan.md")
    r = _client(tmp_path).request("DELETE", "/api/workspace/file",
                                  params={"path": "notes/plan.md"}, headers=_h(JANE))
    assert r.status_code in (400, 404, 405)
    assert (ws / "notes/plan.md").is_file()


def test_a_page_the_turn_wrote_but_never_committed_still_leaves(tmp_path):
    """`git add -- a b` refuses the WHOLE call on one unmatched pathspec, and a path git has never
    heard of is exactly that. The file must still go, and the other paths must still commit."""
    ws = _init_ws(tmp_path, JANE)
    (ws / "notes").mkdir()
    (ws / "notes/scratch.md").write_text("uncommitted\n")     # never `git add`ed
    r = _delete(_client(tmp_path), JANE, "notes/scratch.md")
    assert r.status_code == 200 and not (ws / "notes/scratch.md").exists()


# ── 2. a move inside one workspace ───────────────────────────────────────────────────────────────

def test_a_move_inside_one_workspace_leaves_a_pointer_at_the_old_path(tmp_path):
    ws = _init_ws(tmp_path, JANE)
    _page(ws, "kg/entities/company/oenb.md")
    r = _move(_client(tmp_path), JANE,
              **{"from": "kg/entities/company/oenb.md", "to": "customers/oenb.md"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["moved"] is True and body["pointer"] == "kg/entities/company/oenb.md"
    assert "The dossier." in (ws / "customers/oenb.md").read_text()
    stub = (ws / "kg/entities/company/oenb.md").read_text()
    assert "moved_to: customers/oenb.md" in stub and "customers/oenb.md" in stub
    # ONE commit, naming both paths — the move is one act
    assert body["commit"] == body["source_commit"] == _git(ws, "rev-parse", "HEAD")
    names = _git(ws, "show", "--name-only", "--format=", "HEAD").split()
    assert "customers/oenb.md" in names and "kg/entities/company/oenb.md" in names


def test_a_moved_asset_gets_no_markdown_pointer(tmp_path):
    """A stub is a PAGE. A markdown pointer written over `assets/logo.png` is a broken picture
    wearing a helpful sentence."""
    ws = _init_ws(tmp_path, JANE)
    (ws / "assets").mkdir()
    (ws / "assets/logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "logo")
    r = _move(_client(tmp_path), JANE, **{"from": "assets/logo.png", "to": "assets/oenb-logo.png"})
    assert r.status_code == 200 and r.json()["pointer"] is None
    assert not (ws / "assets/logo.png").exists()
    assert (ws / "assets/oenb-logo.png").read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_moving_a_page_onto_itself_is_refused(tmp_path):
    ws = _init_ws(tmp_path, JANE)
    _page(ws, "notes/plan.md")
    r = _move(_client(tmp_path), JANE, **{"from": "notes/plan.md", "to": "notes/plan.md"})
    assert r.status_code == 400


def test_a_move_with_no_to_slug_stays_in_the_workspace_it_started_in(tmp_path):
    """Defaulting `to_slug` to the caller's desk would turn every rename inside a shared workspace
    into a silent extraction of a page out of it."""
    root = tmp_path
    _init_ws(root, JANE)
    idx = m.InMemoryMembershipIndex()
    ws = _init_ws(root, "oenb-c1")
    m.ensure_owner(root, "oenb-c1", JANE, index=idx)
    _page(ws, "notes/plan.md")
    r = _move(_client(root, index=idx), JANE,
              **{"from": "notes/plan.md", "to": "notes/2026-plan.md", "slug": "oenb-c1"})
    assert r.status_code == 200, r.text
    assert r.json()["to_workspace"] == "oenb-c1"
    assert (ws / "notes/2026-plan.md").is_file()
    assert not (root / JANE / "notes/2026-plan.md").exists()


# ── 3. across workspaces: a write in the target, a delete in the source ──────────────────────────

def test_a_cross_workspace_move_writes_the_target_and_deletes_the_source(tmp_path):
    """The founder's actual ask: seven pages off the desk and into the customer's own workspace."""
    root = tmp_path
    desk = _init_ws(root, JANE)
    _page(desk, "kg/entities/company/oenb.md")
    idx = m.InMemoryMembershipIndex()
    target = _init_ws(root, "oenb-c1")
    m.ensure_owner(root, "oenb-c1", JANE, index=idx)
    before_target = _git(target, "rev-parse", "HEAD")

    r = _move(_client(root, index=idx), JANE,
              **{"from": "kg/entities/company/oenb.md", "to": "kg/entities/company/oenb.md",
                 "to_slug": "oenb-c1"})
    assert r.status_code == 200, r.text
    body = r.json()
    # the page is THERE
    assert "The dossier." in (target / "kg/entities/company/oenb.md").read_text()
    # …and GONE from the desk, with no pointer stub left behind (the containment rule)
    assert not (desk / "kg/entities/company/oenb.md").exists()
    assert body["pointer"] is None
    # two repositories, two commits
    assert body["commit"] == _git(target, "rev-parse", "HEAD") != before_target
    assert body["source_commit"] == _git(desk, "rev-parse", "HEAD")
    assert body["commit"] != body["source_commit"]
    assert "D\tkg/entities/company/oenb.md" in _git(desk, "show", "--name-status", "--format=", "HEAD")


def test_a_cross_workspace_move_into_a_workspace_the_caller_only_reads_is_refused(tmp_path):
    """A viewer may READ a shared workspace. Writing a page into it — which is what the target half
    of a move is — is the contributor's act, and the refusal must land before the source is touched."""
    root = tmp_path
    desk = _init_ws(root, JANE)
    _page(desk, "notes/plan.md")
    idx = m.InMemoryMembershipIndex()
    _init_ws(root, "wsA")
    m.ensure_owner(root, "wsA", "owner1", index=idx)
    m.grant_membership(root, "wsA", JANE, "viewer", added_by="owner1", index=idx)

    r = _move(_client(root, index=idx), JANE,
              **{"from": "notes/plan.md", "to": "notes/plan.md", "to_slug": "wsA"})
    assert r.status_code == 403
    assert (desk / "notes/plan.md").is_file(), "the source was touched by a refused move"
    assert not (root / "wsA/notes/plan.md").exists()


# ── 4. the read-only tiers ───────────────────────────────────────────────────────────────────────

def test_the_private_system_tier_is_never_a_removal_target(tmp_path):
    """`_system` is read-WRITE in the mount stack — chat continuity, sessions, settings and
    `identity.md` live there and the platform writes them. That is exactly why an agent verb must
    not delete out of it, at either end of a move."""
    root = tmp_path
    _init_ws(root, JANE)
    c = _client(root)
    assert _delete(c, JANE, "identity.md", slug="_system").status_code == 403
    assert _move(c, JANE, **{"from": "identity.md", "to": "x.md", "slug": "_system"}).status_code == 403
    assert _move(c, JANE, **{"from": "notes/plan.md", "to": "x.md",
                             "to_slug": "_system"}).status_code == 403


def test_the_company_tier_is_removable_only_by_an_org_admin(tmp_path):
    """`_global` is the organisation's, mounted read-only into every worker. The write route already
    asks this question; the removal route asks the same one rather than a weaker second spelling."""
    root = tmp_path
    _init_ws(root, JANE)
    _init_ws(root, ADMIN)
    g = _init_ws(root, "_global")
    _page(g, "kg/entities/company/vexa.md")

    refused = _delete(_client(root, admins=ADMIN), JANE,
                      "kg/entities/company/vexa.md", slug="_global")
    assert refused.status_code == 403
    assert (g / "kg/entities/company/vexa.md").is_file()

    allowed = _delete(_client(root, admins=ADMIN), ADMIN,
                      "kg/entities/company/vexa.md", slug="_global")
    assert allowed.status_code == 200, allowed.text
    assert not (g / "kg/entities/company/vexa.md").exists()


def test_a_move_out_of_the_company_tier_is_refused_for_everyone_but_the_admin(tmp_path):
    root = tmp_path
    desk = _init_ws(root, JANE)
    g = _init_ws(root, "_global")
    _page(g, "README.md", "# the company\n")
    r = _move(_client(root, admins=ADMIN), JANE,
              **{"from": "README.md", "to": "stolen.md", "slug": "_global"})
    assert r.status_code == 403
    assert (g / "README.md").is_file() and not (desk / "stolen.md").exists()


def test_kg_templates_are_shapes_and_not_records_here_either(tmp_path):
    """The write route refuses `kg/templates/` because a save from that tab rewrites the shape every
    future entity is copied from. A removal would do it permanently."""
    ws = _init_ws(tmp_path, JANE)
    _page(ws, "kg/templates/company.md", "# {{name}}\n")
    r = _delete(_client(tmp_path), JANE, "kg/templates/company.md")
    assert r.status_code == 403 and (ws / "kg/templates/company.md").is_file()


# ── 5. the write-back phase does not put back what the turn took away ────────────────────────────

def test_a_deleted_page_is_not_re_surfaced_by_the_write_back_phase(tmp_path):
    """THE DEFECT THIS CLOSES, in one line: the phase asks "which names has no mounted desk got a
    page for", and a page deleted a second ago is exactly such a name. Without the exclusion the
    founder says *"remove from personal"*, the agent removes it, and the bookkeeping writes it
    straight back with a fresh dated entry."""
    from worker import engine

    ws = _init_ws(tmp_path, JANE)
    (ws / "kg/entities/person").mkdir(parents=True)
    mounts = [{"slug": JANE, "path": str(ws), "role": "private", "write": True, "primary": True}]
    said = ["Removed the Olga Avramenko page from your desk."]

    # with the page gone and nothing said about it, the phase would propose writing it again
    assert engine.writeback_candidates(said, mounts) == ["Olga Avramenko"]
    # …and does not, once the turn's own removal is known
    assert engine.writeback_candidates(said, mounts, removed={"olga-avramenko"}) == []


def test_a_truncated_echo_of_a_removed_name_is_dropped_too(tmp_path):
    """The same prefix rule `missing_names` already makes against the slugs a desk holds: a name
    clipped out of prose slugifies to a prefix of the real page's slug."""
    from worker import engine

    ws = _init_ws(tmp_path, JANE)
    mounts = [{"slug": JANE, "path": str(ws), "role": "private", "write": True, "primary": True}]
    assert engine.writeback_candidates(["Zenith SI runs it."], mounts,
                                       removed={"zenith-sig"}) == []


@pytest.mark.parametrize("tool,args,expected", [
    ("mcp__vexa__workspace_delete", {"path": "kg/entities/company/oenb.md"}, {"oenb"}),
    ("workspace_delete", {"path": "notes/plan.md"}, {"plan"}),
    ("mcp__vexa__workspace_move", {"from": "kg/entities/person/ana.md", "to": "x/ana.md"}, {"ana"}),
    ("mcp__vexa__workspace_move", {"path": "kg/entities/person/ana.md", "to": "x/ana.md"}, {"ana"}),
    ("mcp__vexa__workspace_write", {"path": "kg/entities/company/oenb.md"}, set()),
    ("mcp__vexa__workspace_delete", {}, set()),
    ("mcp__vexa__workspace_delete", "not a dict", set()),
])
def test_the_removal_verbs_are_read_off_the_call_by_name(tool, args, expected):
    """Both spellings of the move's source argument: the HTTP body carries `from`, the rig's tool
    signature spells it `path` because `from` is a Python keyword. The same act reaching the turn
    loop under two names is how a rule comes to hold on one runner and not the other."""
    from worker import engine

    assert engine.removed_page_slugs(tool, args) == expected


# ── 6. the turn's prompt names the two verbs ─────────────────────────────────────────────────────

def test_the_turn_is_told_all_three_things_that_can_happen_to_a_page():
    """The tools arrive DEFERRED, so a verb the prompt does not mention is one the model has to go
    looking for before it can believe it exists — and a turn that does not believe a delete exists
    does not search for one, it improvises. This is the preamble that stops the improvisation."""
    from worker import engine

    p = engine.page_verbs_preamble()
    assert "`workspace_write(" in p and "`workspace_delete(" in p and "`workspace_move(" in p
    assert "commits" in p.lower() or "commit" in p.lower()
    # the exact failure it exists to prevent, in words the model can act on
    assert "overwriting a page with a note saying it moved" in p
