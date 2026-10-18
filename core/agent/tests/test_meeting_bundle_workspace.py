"""A meeting's workspace and notes page travel with its bundle (meeting-bundle.v1, agent half).

Two deployments, each its own workspaces root (offline: fakes + tmp dirs, no meeting-api, no DB).

  A · the owner's `GET /api/meeting/bundle-parts` gives the bound workspace's tree (never its roster
      or policy) and the meeting's page; the bundle is then written with the contract's codec, the
      way meeting-api's export places the parts.
  B · after meeting-api imported that bundle as meeting 77, the importer's
      `POST /api/meeting/bundle-restore` lands the tree as a NEW private workspace and the page on
      their desk, re-bound to meeting 77.

Deny: a share recipient or workspace member (a `shared` row) and a stranger cannot read the parts;
nobody but the imported meeting's owner can restore; a bundle is restored once; a bundle that did
not create the meeting is refused; a tampered bundle is refused before anything is written.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import route_policy  # noqa: E402
from control_plane.api import create_app  # noqa: E402
from control_plane.dispatch import Dispatcher  # noqa: E402
from control_plane.workspace_attach import attached_workspaces, workspace_slot_dir  # noqa: E402
from control_plane.workspace_reader import WorkspaceReader  # noqa: E402
from shared import meeting_bundle_codec as codec  # noqa: E402
from shared.config import load_settings  # noqa: E402

OWNER, MEMBER, STRANGER = "301", "302", "303"
IMPORTER, OTHER = "902", "903"
WS, SRC_ID, NEW_ID = "team-notes", 5150, 77
PAGE = "kg/entities/meeting/2026-10-01-1400-release-sync.md"
PAGE_TEXT = (f"---\ntype: meeting\nmeeting: {SRC_ID}\ntitle: Release sync\n---\n\n# Release sync\n\n"
             f"<!-- vexa:transcript meeting={SRC_ID} -->\n\n<!-- meeting:decisions:start -->\n- ship it\n"
             "<!-- meeting:decisions:end -->\n")
BUNDLE_ID = "6f1c2b9e-3d4a-4c5b-8e7f-0a1b2c3d4e5f"


class _FakeRuntime:
    def launch(self, *a, **k): raise AssertionError("no runtime in these tests")


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _app(root: Path, lookup, recorded: list) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)), meeting_owner_lookup=lookup,
        meeting_note_recorder=lambda s, m, p: recorded.append((s, str(m), p)) or True))


@pytest.fixture()
def source(tmp_path):
    root = tmp_path / "a"
    for uid in (OWNER, MEMBER, STRANGER):
        (root / uid).mkdir(parents=True)
    (root / OWNER / PAGE).parent.mkdir(parents=True)
    (root / OWNER / PAGE).write_text(PAGE_TEXT)
    ws = root / WS
    (ws / "policy").mkdir(parents=True)
    (ws / "policy" / "members.json").write_text(json.dumps([
        {"subject": OWNER, "role": "owner", "email": "owner@example.test"},
        {"subject": MEMBER, "role": "viewer", "email": "member@example.test"}]))
    (ws / "notes").mkdir()
    (ws / "notes" / "agenda.md").write_text("# Agenda\n\n1. checklist\n")
    (ws / "kg" / "entities" / "person").mkdir(parents=True)
    (ws / "kg" / "entities" / "person" / "ada.md").write_text("# Ada\n")
    (ws / "notes" / "q&a@team.md").write_text("a name a bundle cannot carry\n")

    def lookup(user_id, meeting_id, workspaces=None):
        if str(meeting_id) != str(SRC_ID):
            return None
        row = {"id": SRC_ID, "native_meeting_id": "abc-defg-hij", "user_id": int(OWNER),
               "data": {"title": "Release sync", "workspace_id": WS, "metadata": {"note_path": PAGE}}}
        if str(user_id) == OWNER:
            return {**row, "shared": False}
        return {**row, "shared": True} if WS in (workspaces or []) else None

    return {"root": root, "client": _app(root, lookup, [])}


@pytest.fixture()
def target(tmp_path):
    root = tmp_path / "b"
    for uid in (IMPORTER, OTHER):
        (root / uid).mkdir(parents=True)
    rows = {}
    recorded: list = []

    def lookup(user_id, meeting_id, workspaces=None):
        row = rows.get(str(meeting_id))
        if row is None:
            return None
        return {**row, "shared": False} if str(user_id) == IMPORTER else None

    return {"root": root, "rows": rows, "recorded": recorded, "client": _app(root, lookup, recorded)}


def _h(uid):
    return {"X-User-Id": uid}


def _parts(source):
    r = source["client"].get(f"/api/meeting/bundle-parts?meeting_id={SRC_ID}", headers=_h(OWNER))
    assert r.status_code == 200, r.text
    return r


def _bundle(parts_bytes: bytes, bundle_id: str = BUNDLE_ID) -> bytes:
    workspace, notes = codec.read_parts(parts_bytes)
    meeting = {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "title": "Release sync",
               "status": "completed", "start_time": "2026-10-01T14:00:00Z", "end_time": "2026-10-01T14:30:00Z",
               "participants": ["Ada"], "media": []}
    return codec.write_bundle(
        meeting=meeting, transcript={"segments": [{"start": 0.0, "end": 1.0, "speaker": "Ada", "text": "Hi.", "language": "en"}]},
        annotations={"metadata": {}, "notes": None}, media=[], bundle_id=bundle_id,
        exported_at="2026-10-10T12:00:00Z", deployment_id="0" * 32, source_meeting_id=SRC_ID,
        workspace=workspace, notes_page=notes)


def _imported(target, bundle_id=BUNDLE_ID):
    target["rows"][str(NEW_ID)] = {"id": NEW_ID, "native_meeting_id": "abc-defg-hij", "user_id": int(IMPORTER),
                                   "data": {"title": "Release sync",
                                            "metadata": {"imported_bundle_id": bundle_id}}}


def _restore(target, bundle: bytes, uid=IMPORTER, meeting_id=NEW_ID):
    return target["client"].post(f"/api/meeting/bundle-restore?meeting_id={meeting_id}", content=bundle,
                                 headers={**_h(uid), "Content-Type": "application/zip"})


# ── export: the parts ─────────────────────────────────────────────────────────────────────────────
def test_the_owner_gets_the_workspace_tree_and_the_page_never_the_roster(source):
    r = _parts(source)
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = sorted(zf.namelist())
        assert zf.read("notes.md").decode() == PAGE_TEXT
    assert names == ["notes.md", "workspace/kg/entities/person/ada.md", "workspace/notes/agenda.md"]
    assert not any(n.startswith("workspace/policy") for n in names)
    # The file a bundle cannot name is counted, not silently lost.
    assert r.headers["X-Vexa-Skipped-Files"] == "1"
    assert r.headers["X-Vexa-Workspace-Files"] == "2" and r.headers["X-Vexa-Notes-Page"] == "1"


def test_a_workspace_member_cannot_take_the_parts(source):
    r = source["client"].get(f"/api/meeting/bundle-parts?meeting_id={SRC_ID}", headers=_h(MEMBER))
    assert r.status_code == 403


def test_a_stranger_cannot_take_the_parts(source):
    r = source["client"].get(f"/api/meeting/bundle-parts?meeting_id={SRC_ID}", headers=_h(STRANGER))
    assert r.status_code == 403


# ── import: the restore ───────────────────────────────────────────────────────────────────────────
def test_the_workspace_and_page_land_for_the_importer_bound_to_the_new_meeting(source, target):
    bundle = _bundle(_parts(source).content)
    _imported(target)
    r = _restore(target, bundle)
    assert r.status_code == 201, r.text
    body = r.json()
    slug = body["workspace"]["slug"]
    assert body["workspace"]["files"] == 2
    slot = workspace_slot_dir(target["root"], IMPORTER, slug)
    assert (slot / "notes" / "agenda.md").read_text() == "# Agenda\n\n1. checklist\n"
    assert (slot / "kg" / "entities" / "person" / "ada.md").is_file()
    assert slug in attached_workspaces(target["root"], IMPORTER).get("slots", {})
    page = (target["root"] / IMPORTER / body["notes_page"]["path"]).read_text()
    assert f"meeting: {NEW_ID}" in page and f"<!-- vexa:transcript meeting={NEW_ID} -->" in page
    assert f"meeting={SRC_ID}" not in page and "- ship it" in page
    assert target["recorded"] == [(IMPORTER, str(NEW_ID), body["notes_page"]["path"])]


def test_a_bundle_is_restored_once(source, target):
    bundle = _bundle(_parts(source).content)
    _imported(target)
    assert _restore(target, bundle).status_code == 201
    again = _restore(target, bundle)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "duplicate_import"


def test_only_the_imported_meetings_owner_can_restore(source, target):
    bundle = _bundle(_parts(source).content)
    _imported(target)
    assert _restore(target, bundle, uid=OTHER).status_code == 403
    assert not (target["root"] / OTHER / "kg").exists()


def test_a_bundle_that_did_not_create_this_meeting_is_refused(source, target):
    bundle = _bundle(_parts(source).content)
    _imported(target, bundle_id="00000000-0000-4000-8000-000000000000")
    r = _restore(target, bundle)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "manifest_mismatch"


def test_a_tampered_bundle_is_refused_before_anything_is_written(source, target):
    bundle = bytearray(_bundle(_parts(source).content))
    with zipfile.ZipFile(io.BytesIO(bytes(bundle))) as zf:
        entries = {n: zf.read(n) for n in zf.namelist()}
    entries["workspace/notes/agenda.md"] = b"# Agenda\n\n1. something else\n"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for n, b in entries.items():
            zf.writestr(n, b)
    _imported(target)
    r = _restore(target, out.getvalue())
    assert r.status_code == 422 and r.json()["detail"]["code"] == "hash_mismatch"
    assert attached_workspaces(target["root"], IMPORTER).get("slots", {}) in ({}, None) or \
        all("imported" not in (v.get("name") or "") for v in attached_workspaces(target["root"], IMPORTER)["slots"].values())


def test_the_restore_needs_a_person_in_the_loop():
    """A write into the person's workspaces: an unwatched delegated worker is refused before it runs."""
    rows = route_policy.load()
    assert any(r == ("POST", "/api/meeting/bundle-restore") for r in rows), rows
