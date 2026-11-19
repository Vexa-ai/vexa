"""`workspace_forget`, end to end — the app.dev case of 2026-10-13 (Vexa-ai/vexa#1783).

A person dropped a power of attorney (two passport numbers) into a chat. The drawer file
`uploads/7ac36fe6f2952885-docx` was swept into the desk's history by the turn's commit (d4218da,
the commit's only path), while the agent said it had saved nothing. They asked: "delete information
about that — it's unnecessary footprint in this knowledge". The values below are made up."""
from __future__ import annotations

import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests._session_fakes import MemBlobs
from tests.test_api import _FakeIdentity, _FakeRuntime

ME = "2"
H = {"X-User-Id": ME, "x-user-email": "owner@example.test"}
UPLOAD = "uploads/7ac36fe6f2952885-docx"
NUMS = ("AB1234567", "CD7654321")


def _docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml",
                   "<w:document><w:t>Power of attorney.</w:t><w:t>Passport no. AB1234567</w:t>"
                   "<w:t>Passport no. CD7654321</w:t></w:document>")
    return buf.getvalue()


def _git(ws, *a):
    return subprocess.run(["git", "-C", str(ws), *a], capture_output=True, text=True, check=True).stdout


@pytest.fixture()
def world(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    (seed / "kg").mkdir(parents=True)
    (seed / "CLAUDE.md").write_text("root\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    root = tmp_path / "ws"
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", redis_url="")
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    c = TestClient(create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                              reader=WorkspaceReader(str(root)), session_store=store))
    assert c.post("/api/workspace/init", headers=H).status_code == 201
    desk = root / ME
    # the drop, and the turn commit that swept it into history before the drawer was excluded
    r = c.post("/api/workspace/upload", headers=H,
               files={"files": ("poa.docx", _docx(), "application/octet-stream")})
    up = r.json()["files"][0]["path"]
    (desk / up).rename(desk / UPLOAD)
    _git(desk, "-c", "user.name=o", "-c", "user.email=o@example.test", "add", "-f", UPLOAD)
    _git(desk, "-c", "user.name=o", "-c", "user.email=o@example.test", "commit", "-q", "-m",
         f"{ME}: {UPLOAD} — added")
    # the chat that read it
    sess = store.create(ME, title="POA check")
    turn = json.dumps({"type": "user", "message": {"content": f"@file:{UPLOAD}"}}) + "\n" + \
        json.dumps({"type": "tool_result", "content": "Passport no. AB1234567; Passport no. CD7654321"}) + "\n"
    store.save_bundle(sess["id"], 0, {".claude/projects/x/t.jsonl": turn.encode()})
    store.add_note(sess["id"], ME, "is CD7654321 the second one?")
    return c, desk, store, sess["id"]


def test_the_first_call_changes_nothing_and_asks(world):
    c, desk, _, _ = world
    head = _git(desk, "rev-parse", "HEAD")
    r = c.post("/api/workspace/forget", headers=H, json={"paths": [UPLOAD]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is False and UPLOAD in body["would_forget"]["files"]
    assert body["would_forget"]["history_commits_touching_them"] == 1
    assert body["ask"].startswith("Forget ")
    assert _git(desk, "rev-parse", "HEAD") == head and (desk / UPLOAD).exists()
    assert not any(n in r.text for n in NUMS)


def test_confirmed_it_is_gone_from_tree_history_transcripts_and_notes(world):
    c, desk, store, sid = world
    r = c.post("/api/workspace/forget", headers=H, json={"paths": [UPLOAD], "confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert not any(n in r.text for n in NUMS), "a removed value is never answered"
    assert not (desk / UPLOAD).exists()
    assert UPLOAD not in _git(desk, "log", "--all", "--name-only", "--format=")
    assert body["removed"]["workspace"]["files_removed"] == 1
    assert body["removed"]["workspace"]["commits_rewritten"] == 1
    assert body["removed"]["transcripts"]["spans"] >= 3
    text = store.load_bundle(sid)["files"][".claude/projects/x/t.jsonl"].decode()
    assert not any(n in text for n in NUMS) and text.count("\n") == 2, "the turns are kept"
    assert all(not any(n in x["text"] for n in NUMS) for x in store.notes(sid))
    assert any("email" in s for s in body["not_removed"])
    audit = (Path(str(desk).replace(f"/{ME}", "")) / ".system" / ME / "audit" / "forget.jsonl")
    line = audit.read_text()
    assert '"files": 1' in line and not any(n in line for n in NUMS) and UPLOAD not in line


def test_another_person_cannot_forget_in_my_desk(world):
    c, _, _, _ = world
    r = c.post("/api/workspace/forget", headers={"X-User-Id": "99"},
               json={"slug": ME, "paths": [UPLOAD], "confirm": True})
    assert r.status_code in (403, 404)


def test_nothing_named_is_refused(world):
    c, _, _, _ = world
    assert c.post("/api/workspace/forget", headers=H, json={}).status_code == 400


def test_with_the_session_store_off_the_desk_transcripts_are_redacted(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    (seed / "kg").mkdir(parents=True)
    (seed / "CLAUDE.md").write_text("root\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    root = tmp_path / "ws"
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", redis_url="")
    c = TestClient(create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                              reader=WorkspaceReader(str(root))))
    c.post("/api/workspace/init", headers=H)
    desk = root / ME
    (desk / "uploads").mkdir()
    (desk / UPLOAD).write_bytes(_docx())
    (desk / ".claude" / "projects" / "x").mkdir(parents=True)
    (desk / ".claude" / "projects" / "x" / "t.jsonl").write_text('{"c": "AB1234567 and CD7654321"}\n')
    r = c.post("/api/workspace/forget", headers=H, json={"paths": [UPLOAD], "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["removed"]["transcripts"]["spans"] == 2
    assert "AB1234567" not in (desk / ".claude" / "projects" / "x" / "t.jsonl").read_text()


def test_forget_reaches_old_chats_left_in_system_and_the_retired_archive(world):
    """session-store (0.13.3): a chat copy in the old `_system` layout, or one `session_migrate
    --retire` moved into the archive, is a transcript too — a forget redacts it like the store's."""
    from control_plane import session_migrate
    c, desk, _store, _ = world
    root = desk.parent
    old = root / ".system" / ME / ".claude" / "projects" / "x"
    old.mkdir(parents=True)
    (old / "t.jsonl").write_text('{"c": "AB1234567"}\n')
    arch = root / session_migrate.ARCHIVE / "20261014T000000Z" / ".system" / ME / ".claude" / "projects" / "x"
    arch.mkdir(parents=True)
    (arch / "t.jsonl").write_text('{"c": "CD7654321"}\n')
    other = root / session_migrate.ARCHIVE / "20261014T000000Z" / ".system" / "9" / ".claude"
    other.mkdir(parents=True)
    (other / "t.jsonl").write_text('{"c": "AB1234567"}\n')       # someone else's: never touched
    r = c.post("/api/workspace/forget", headers=H, json={"paths": [UPLOAD], "confirm": True})
    assert r.status_code == 200, r.text
    assert "AB1234567" not in (old / "t.jsonl").read_text()
    assert "CD7654321" not in (arch / "t.jsonl").read_text()
    assert "AB1234567" in (other / "t.jsonl").read_text()


# ── a named file that is no longer on disk (friction 2026-10-13 21:46) ───────────────────────────
# On app.dev the upload was removed one minute after it landed (`workspace_delete`, a committed
# removal), so it was in history and the chat's transcript but not on disk. A forget must still
# reach every copy, and say which named file was already gone instead of failing on it.

def _transcript_text(store, sid):
    return store.load_bundle(sid)["files"][".claude/projects/x/t.jsonl"].decode()


def test_a_file_already_removed_is_forgotten_from_history_and_said_to_be_gone(world):
    c, desk, store, sid = world
    assert c.post("/api/workspace/remove", headers=H, json={"path": UPLOAD}).status_code == 200
    assert not (desk / UPLOAD).exists()
    pv = c.post("/api/workspace/forget", headers=H, json={"paths": [UPLOAD]}).json()
    assert pv["would_forget"]["already_gone_from_disk"] == [UPLOAD]
    assert UPLOAD in pv["would_forget"]["files"]
    r = c.post("/api/workspace/forget", headers=H, json={"paths": [UPLOAD], "confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed"]["workspace"]["already_gone_from_disk"] == [UPLOAD]
    assert UPLOAD not in _git(desk, "log", "--all", "--name-only", "--format=")
    assert not any(n in _transcript_text(store, sid) for n in NUMS)


def test_a_file_deleted_on_disk_but_still_committed_is_read_from_its_last_commit(world):
    """The identifiers come from the file's own last version — which is the commit that ADDED it
    when nothing committed its removal (the `^` of that commit has no such file)."""
    c, desk, store, sid = world
    (desk / UPLOAD).unlink()
    r = c.post("/api/workspace/forget", headers=H, json={"paths": [UPLOAD], "confirm": True})
    assert r.status_code == 200, r.text
    assert not any(n in _transcript_text(store, sid) for n in NUMS)
    assert all(not any(n in x["text"] for n in NUMS) for x in store.notes(sid))


def test_a_file_that_is_nowhere_is_reported_gone_not_an_error(world):
    c, desk, _, _ = world
    ghost = "uploads/0000000000000000-nothing.pdf"
    pv = c.post("/api/workspace/forget", headers=H, json={"paths": [ghost]})
    assert pv.status_code == 200, pv.text
    assert pv.json()["would_forget"]["already_gone"] == [ghost]
    assert "already gone" in pv.json()["ask"]
    r = c.post("/api/workspace/forget", headers=H, json={"paths": [ghost], "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["removed"]["workspace"]["already_gone"] == [ghost]


def test_a_person_never_forgets_the_company_layer_or_the_forget_record(world):
    """`_global` is written by the admin account alone; a person's forget there is refused before
    anything is removed or rewritten. `_system` keeps the audit of forgets and is no target."""
    c, desk, _, _ = world
    glob = desk.parent / "_global"
    glob.mkdir(exist_ok=True)
    _git(glob, "init", "-q", "-b", "main")
    (glob / "POLICIES.md").write_text("# Policies\n")
    _git(glob, "-c", "user.name=a", "-c", "user.email=a@example.test", "add", "-A")
    _git(glob, "-c", "user.name=a", "-c", "user.email=a@example.test", "commit", "-q", "-m", "policies")
    head = _git(glob, "rev-parse", "HEAD")
    r = c.post("/api/workspace/forget", headers=H,
               json={"slug": "_global", "paths": ["POLICIES.md"], "confirm": True})
    assert r.status_code == 403, r.text
    assert (glob / "POLICIES.md").read_text() == "# Policies\n" and _git(glob, "rev-parse", "HEAD") == head
    r = c.post("/api/workspace/forget", headers=H,
               json={"slug": "_system", "paths": ["audit/forget.jsonl"], "confirm": True})
    assert r.status_code == 403, r.text
