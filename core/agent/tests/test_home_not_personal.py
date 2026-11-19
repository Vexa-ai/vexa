"""NO WRITE LANDS IN A HIDDEN PERSONAL, AND NONE IN A WORKSPACE PICKED BY ORDER (#1783).

A write that names no workspace goes to Personal (the desk) while it is on. Once Personal is hidden
(switched off, deleted) and nothing names a workspace, it is REFUSED with a clear message — never sent
to "the first active workspace", which can be a shared one a customer reads. Every writer that names
no workspace — uploads, proposals, claims, meeting terms, a restored meeting note, personal limits,
saved templates, the panel's touches, management ops with no slug — follows that one rule.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane import meeting_note, meeting_terms
from control_plane import workspace_membership as m
from control_plane.workspace_attach import NoHome, home_dir
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

JANE = "u_jane"
SLOT = "biz-b1c87f"
_IDX = m.InMemoryMembershipIndex()


def _git_init(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    for a in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(p), *a], check=True, capture_output=True)
    (p / "README.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(p), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(p), "commit", "-qm", "seed"], check=True, capture_output=True)
    return p


SHARED = "oenb-x-vexa"


def _root(tmp_path: Path, *, personal_on: bool) -> tuple[Path, Path, Path]:
    root = tmp_path / "workspaces"
    desk = _git_init(root / JANE)
    store = root / ".attached" / JANE
    biz = _git_init(store / SLOT)
    shared = _git_init(root / SHARED)                         # a customer reads this one
    m.ensure_owner(root, SHARED, JANE, index=_IDX)
    state = {"_flat_v1": True, "active": None, "hidden": [] if personal_on else ["seed"],
             "active_set": (["seed"] if personal_on else []) + [SLOT],
             "slots": {"seed": {"repo": None, "ref": None}, SLOT: {"repo": None, "ref": "main", "name": "biz"}}}
    (store / "state.json").write_text(json.dumps(state))
    (root / "_global").mkdir(exist_ok=True)
    return root, desk, biz


def _client(root: Path) -> TestClient:
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    return TestClient(create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                                 reader=WorkspaceReader(str(root)), membership_index=_IDX))


def test_the_home_is_personal_and_a_hidden_personal_leaves_none(tmp_path):
    root, desk, biz = _root(tmp_path, personal_on=True)
    assert home_dir(root, JANE) == desk
    root2, desk2, biz2 = _root(tmp_path / "off", personal_on=False)
    with pytest.raises(NoHome):
        home_dir(root2, JANE)


def _written(d: Path) -> list[str]:
    return sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file() and ".git" not in p.parts
                  and p.name != "README.md" and not str(p.relative_to(d)).startswith((".attached", "policy/"))
                  and str(p.relative_to(d)) != ".vexa/workspace.json")


def test_with_personal_hidden_an_upload_naming_no_workspace_is_refused_and_nothing_is_written(tmp_path):
    """The founder's case: Personal hidden, a SHARED workspace first in the list. The upload is
    refused with a clear message; nothing lands in the shared one, nor anywhere else."""
    root, desk, biz = _root(tmp_path, personal_on=False)
    shared = root / SHARED
    c = _client(root)
    before = {d: _written(d) for d in (desk, biz, shared)}
    r = c.post("/api/workspace/upload", headers={"X-User-Id": JANE},
                           files=[("files", ("note.txt", b"hello", "text/plain"))])
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["refused"] == "no workspace to write to"
    assert "Writes to" in r.json()["detail"]["said"]
    assert {d: _written(d) for d in (desk, biz, shared)} == before


def test_with_personal_on_an_upload_lands_in_personal(tmp_path):
    root, desk, biz = _root(tmp_path, personal_on=True)
    r = _client(root).post("/api/workspace/upload", headers={"X-User-Id": JANE},
                           files=[("files", ("note.txt", b"hello", "text/plain"))])
    assert r.status_code in (200, 201), r.text
    assert any(p.startswith(("uploads/", "assets/")) for p in _written(desk))
    assert not _written(root / SHARED)


def test_a_proposal_is_refused_with_personal_hidden_and_filed_in_personal_while_on(tmp_path):
    body = {"title": "Try X", "body": "why", "source": "chat", "act": "send the weekly digest"}
    root, desk, biz = _root(tmp_path, personal_on=False)
    r = _client(root).post("/api/proposals", headers={"X-User-Id": JANE}, json=body)
    assert r.status_code == 409, r.text
    assert not (biz / ".vexa" / "proposals.json").exists() and not (root / SHARED / ".vexa" / "proposals.json").exists()
    root, desk, biz = _root(tmp_path / "on", personal_on=True)
    r = _client(root).post("/api/proposals", headers={"X-User-Id": JANE}, json=body)
    assert r.status_code == 201, r.text
    assert (desk / ".vexa" / "proposals.json").is_file()


def test_meeting_terms_are_kept_in_personal_and_not_at_all_when_it_is_hidden(tmp_path):
    root, desk, biz = _root(tmp_path, personal_on=False)
    assert meeting_terms._locate(root, JANE, "120") is None
    root, desk, biz = _root(tmp_path / "on", personal_on=True)
    assert meeting_terms._locate(root, JANE, "120")[0] == desk


def test_a_meeting_note_is_found_for_the_readers_wherever_it_is(tmp_path):
    root, desk, biz = _root(tmp_path, personal_on=False)
    rel = "kg/entities/meeting/2026-10-15-0900-google-meet-meeting-jmk-qxpo-yzg.md"
    (biz / rel).parent.mkdir(parents=True)
    (biz / rel).write_text("---\ntype: meeting\nmeeting: 120\n---\n\nhello\n")
    row = {"id": 120, "native_meeting_id": "jmk-qxpo-yzg", "data": {"metadata": {"note_path": rel}}}
    assert meeting_note.find(root, JANE, row) == (rel, biz)


@pytest.mark.parametrize("personal_on", [True, False])
def test_a_management_op_with_no_slug_is_personal_or_refused(tmp_path, personal_on):
    root, desk, biz = _root(tmp_path, personal_on=personal_on)
    r = _client(root).post("/api/workspace/purpose", headers={"X-User-Id": JANE}, json={"purpose": "Work for the meetup"})
    if not personal_on:
        assert r.status_code == 409, r.text
        assert all("meetup" not in (d / f).read_text(errors="ignore") for d in (biz, root / SHARED) for f in _written(d))
        return
    assert r.status_code in (200, 201), r.text
    assert "meetup" in "".join((desk / f).read_text(errors="ignore") for f in _written(desk))