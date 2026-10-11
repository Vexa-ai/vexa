"""A PERSON DELETES A PAGE, AND CAN PUT IT BACK (founder 2026-10-11: *"we probably need a delete for a
note"*).

The removal itself is `POST /api/workspace/remove` (Vexa-ai/vexa#1621), pinned in
`test_workspace_delete_move`. What this file adds is the half that makes offering it to a person
safe — `POST /api/workspace/restore`, behind the page view's **Undo** and History's **Restore** —
and the refusals a person meets at both doors, one test per refusal:

* a removal and a restore are BOTH commits, and the restore puts back the exact bytes;
* a restore never overwrites a page that is at the path now;
* a restore must name a commit that removed THAT page;
* the people who may not remove a page may not restore it either: a reader of a shared workspace,
  a non-member, anyone but the admin on `_global`, anyone on `_system`, and nobody on
  `kg/templates/`;
* a contributor and an owner of a shared workspace may do both.

L2: a real FastAPI app over fakes and real git repositories in `tmp_path`, the harness of
`test_workspace_delete_move`, reused rather than re-invented.
"""
from __future__ import annotations

from tests.test_workspace_delete_move import ADMIN, JANE, _client, _git, _h, _init_ws, _page
from control_plane import workspace_membership as m

PAGE = "kg/entities/project/so-vexam.md"
TEXT = "# So Vexam\n\nNot a distinct project.\n"


def _remove(c, subject, path, slug=None):
    body = {"path": path, **({"slug": slug} if slug is not None else {})}
    return c.post("/api/workspace/remove", json=body, headers=_h(subject))


def _restore(c, subject, path, slug=None, commit=None):
    body = {"path": path, **({"slug": slug} if slug is not None else {}),
            **({"commit": commit} if commit is not None else {})}
    return c.post("/api/workspace/restore", json=body, headers=_h(subject))


# ── 1. delete, then undo ─────────────────────────────────────────────────────────────────────────

def test_undo_puts_back_exactly_what_the_delete_took_as_a_commit(tmp_path):
    ws = _init_ws(tmp_path, JANE)
    _page(ws, PAGE, TEXT)
    c = _client(tmp_path)
    gone = _remove(c, JANE, PAGE)
    assert gone.status_code == 200, gone.text
    assert not (ws / PAGE).exists()

    back = _restore(c, JANE, PAGE, commit=gone.json()["commit"])
    assert back.status_code == 200, back.text
    body = back.json()
    assert body["restored"] is True and body["path"] == PAGE
    assert body["from_commit"] == gone.json()["commit"]
    assert (ws / PAGE).read_text() == TEXT
    # history, not a rewrite of it: the removal is still there, and the restore is a new commit
    assert body["commit"] == _git(ws, "rev-parse", "HEAD") != gone.json()["commit"]
    assert _git(ws, "log", "-1", "--format=%s") == f"{JANE}: {PAGE} — restored"
    assert f"A\t{PAGE}" in _git(ws, "show", "--name-status", "--format=", "HEAD")


def test_restore_without_a_commit_takes_the_newest_removal_of_that_page(tmp_path):
    """History's Restore and a later "bring it back" have the path, not always the sha."""
    ws = _init_ws(tmp_path, JANE)
    _page(ws, PAGE, "first\n")
    c = _client(tmp_path)
    _remove(c, JANE, PAGE)
    _page(ws, PAGE, "second\n")
    _remove(c, JANE, PAGE)
    assert _restore(c, JANE, PAGE).status_code == 200
    assert (ws / PAGE).read_text() == "second\n"


def test_restoring_an_entity_page_puts_it_back_in_the_index(tmp_path):
    """`kg/INDEX.md` rides in every dispatch — a page restored without it is one the next turn is
    told does not exist, and the turn after that writes a duplicate."""
    from workspaces.shared import entities as entities_mod

    ws = _init_ws(tmp_path, JANE)
    _page(ws, PAGE, TEXT)
    entities_mod.write_index(ws, JANE)
    c = _client(tmp_path)
    _remove(c, JANE, PAGE)
    assert "so-vexam" not in (ws / entities_mod.INDEX_PATH).read_text()
    _restore(c, JANE, PAGE)
    assert "so-vexam" in (ws / entities_mod.INDEX_PATH).read_text()


def test_a_binary_file_comes_back_byte_for_byte(tmp_path):
    ws = _init_ws(tmp_path, JANE)
    raw = b"\x89PNG\r\n\x1a\n\x00\xff\x10"
    (ws / "assets").mkdir()
    (ws / "assets/logo.png").write_bytes(raw)
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "logo")
    c = _client(tmp_path)
    _remove(c, JANE, "assets/logo.png")
    assert _restore(c, JANE, "assets/logo.png").status_code == 200
    assert (ws / "assets/logo.png").read_bytes() == raw


# ── 2. what a restore refuses ────────────────────────────────────────────────────────────────────

def test_a_restore_never_overwrites_a_page_that_is_there_now(tmp_path):
    ws = _init_ws(tmp_path, JANE)
    _page(ws, PAGE, TEXT)
    c = _client(tmp_path)
    sha = _remove(c, JANE, PAGE).json()["commit"]
    _page(ws, PAGE, "a newer page\n")
    r = _restore(c, JANE, PAGE, commit=sha)
    assert r.status_code == 409
    assert (ws / PAGE).read_text() == "a newer page\n"


def test_a_commit_that_did_not_remove_the_page_is_refused(tmp_path):
    ws = _init_ws(tmp_path, JANE)
    _page(ws, PAGE, TEXT)
    _page(ws, "notes/other.md", "x\n")
    c = _client(tmp_path)
    other = _remove(c, JANE, "notes/other.md").json()["commit"]
    _remove(c, JANE, PAGE)
    assert _restore(c, JANE, PAGE, commit=other).status_code == 404
    assert _restore(c, JANE, PAGE, commit="not-a-sha").status_code == 400
    assert not (ws / PAGE).exists()


def test_a_page_that_was_never_removed_has_nothing_to_restore(tmp_path):
    _init_ws(tmp_path, JANE)
    assert _restore(_client(tmp_path), JANE, "notes/never.md").status_code == 404


def test_a_restore_path_that_leaves_the_workspace_is_refused(tmp_path):
    _init_ws(tmp_path, JANE)
    c = _client(tmp_path)
    for bad in ("../u_bob/README.md", "/etc/passwd", ".git/config"):
        assert _restore(c, JANE, bad).status_code in (400, 403), bad


# ── 3. who may: the edit rule, at both doors ─────────────────────────────────────────────────────

def _shared(root, role: str | None):
    """`team` owned by someone else, with JANE in it as ``role`` (None = not a member)."""
    _init_ws(root, JANE)
    idx = m.InMemoryMembershipIndex()
    ws = _init_ws(root, "team")
    m.ensure_owner(root, "team", "owner1", index=idx)
    if role:
        m.grant_membership(root, "team", JANE, role, added_by="owner1", index=idx)
    _page(ws, PAGE, TEXT)
    return ws, idx


def test_a_reader_of_a_shared_workspace_can_neither_delete_nor_restore(tmp_path):
    ws, idx = _shared(tmp_path, "viewer")
    c = _client(tmp_path, index=idx)
    assert _remove(c, JANE, PAGE, slug="team").status_code == 403
    assert (ws / PAGE).is_file()
    # the owner removes it; the reader still cannot bring it back
    owner = _remove(c, "owner1", PAGE, slug="team")
    assert owner.status_code == 200, owner.text
    assert _restore(c, JANE, PAGE, slug="team").status_code == 403
    assert not (ws / PAGE).exists()


def test_someone_outside_a_shared_workspace_can_neither_delete_nor_restore(tmp_path):
    ws, idx = _shared(tmp_path, None)
    c = _client(tmp_path, index=idx)
    assert _remove(c, JANE, PAGE, slug="team").status_code == 403
    assert (ws / PAGE).is_file()
    _remove(c, "owner1", PAGE, slug="team")
    assert _restore(c, JANE, PAGE, slug="team").status_code == 403


def test_a_contributor_of_a_shared_workspace_may_delete_and_restore(tmp_path):
    ws, idx = _shared(tmp_path, "contributor")
    c = _client(tmp_path, index=idx)
    assert _remove(c, JANE, PAGE, slug="team").status_code == 200
    assert not (ws / PAGE).exists()
    assert _restore(c, JANE, PAGE, slug="team").status_code == 200
    assert (ws / PAGE).read_text() == TEXT


def test_the_company_tier_is_restored_only_by_its_admin(tmp_path):
    root = tmp_path
    _init_ws(root, JANE)
    _init_ws(root, ADMIN)
    g = _init_ws(root, "_global")
    _page(g, PAGE, TEXT)
    c = _client(root, admins=ADMIN)
    assert _remove(c, ADMIN, PAGE, slug="_global").status_code == 200
    assert _restore(c, JANE, PAGE, slug="_global").status_code == 403
    assert not (g / PAGE).exists()
    assert _restore(c, ADMIN, PAGE, slug="_global").status_code == 200
    assert (g / PAGE).read_text() == TEXT


def test_the_private_system_tier_is_never_a_restore_target(tmp_path):
    _init_ws(tmp_path, JANE)
    assert _restore(_client(tmp_path), JANE, "identity.md", slug="_system").status_code == 403


def test_kg_templates_are_not_restored_either(tmp_path):
    ws = _init_ws(tmp_path, JANE)
    _page(ws, "kg/templates/company.md", "# {{name}}\n")
    _git(ws, "rm", "-q", "kg/templates/company.md")
    _git(ws, "commit", "-q", "-m", "drop template")
    r = _restore(_client(tmp_path), JANE, "kg/templates/company.md")
    assert r.status_code == 403 and not (ws / "kg/templates/company.md").exists()


# ── 4. History finds a removal by its subject ────────────────────────────────────────────────────

def test_a_long_path_keeps_the_verb_in_the_subject(tmp_path):
    """History's Restore finds a removal by `— removed`. Clipping the whole subject at 72 cut the
    verb off any long path, so the path is shortened in the middle instead."""
    ws = _init_ws(tmp_path, JANE)
    long = "kg/entities/organization/an-unusually-long-company-name-from-a-transcript.md"
    _page(ws, long, TEXT)
    c = _client(tmp_path)
    _remove(c, JANE, long)
    subject = _git(ws, "log", "-1", "--format=%s")
    assert len(subject) <= 72 and subject.endswith(" — removed") and "…" in subject
    assert subject.split("…", 1)[1].split(" — ")[0] in long
    _restore(c, JANE, long)
    assert _git(ws, "log", "-1", "--format=%s").endswith(" — restored")
