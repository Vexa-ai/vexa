"""`open_page` and `workspace_target` are agent-api routes (Vexa-ai/vexa#1783).

Both verbs were served only by the dogfood control rig (`deploy/dogfood/rig/vexa_control_mcp.py`),
which answered them by reaching agent-api's internal tier and the gateway on the person's behalf.
They are the agent domain's: the harness turns their RESULTS into the chat's `open` and `focus`
events (`llm/tool_events.py`), so the route only has to answer truthfully — `opened`/`targeted`
when the thing is there and theirs, a refusal otherwise. Neither route writes the chat's target:
`_binding_watch` stays its one writer (`routers/chats.py`).

L2: a real FastAPI app over fakes, no redis, no runtime, no meeting-api.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_attach import create_shared_workspace_dir
from control_plane.workspace_membership import InMemoryMembershipIndex, ensure_owner
from control_plane.workspace_reader import WorkspaceReader
from llm.tool_events import _open_event, _workspace_focus
from shared.config import load_settings

USER = "175"
H = {"X-User-Id": USER}
AGENT = pathlib.Path(__file__).resolve().parents[1]


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


MEETINGS = {"42": {"id": 42, "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
                   "start_time": "2026-10-12T10:00:00Z", "data": {}}}
WORDS = {"42": [{"text": "hello", "speaker": "Ada"}], "43": []}


def _owner_lookup(user_id, meeting_id, workspaces=None):
    if user_id != USER:
        return None
    if str(meeting_id) == "43":
        return {"id": 43, "platform": "google_meet", "native_meeting_id": "q", "data": {}}
    return MEETINGS.get(str(meeting_id))


def _words_lookup(user_id, meeting_id, workspaces=None):
    return WORDS.get(str(meeting_id)) if user_id == USER else None


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("root\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    app = create_app(Dispatcher(settings, _Runtime(), _Identity()),
                     reader=WorkspaceReader(str(root)), sessions=_Sessions(),
                     meeting_owner_lookup=_owner_lookup, meeting_transcript_lookup=_words_lookup)
    client = TestClient(app)
    # the caller's own desk exists once anything touches it
    assert client.post("/api/workspace/init", headers=H).status_code in (200, 201)
    return {"root": root, "client": client}


def _desk_write(client, path, content="# hi\n"):
    r = client.put("/api/workspace/file", json={"path": path, "content": content}, headers=H)
    assert r.status_code == 200, r.text


# ── workspace_target ─────────────────────────────────────────────────────────────────────────────

def test_an_empty_slug_targets_the_persons_own_desk(stack):
    r = stack["client"].post("/api/workspace/target", json={}, headers=H)
    assert r.status_code == 200
    assert r.json()["targeted"] == ""
    assert _workspace_focus(json.dumps(r.json())) is None, "the desk is the default, not a focus"


def test_a_workspace_the_person_owns_is_targeted_and_earns_a_focus_event(stack):
    wid = create_shared_workspace_dir(stack["root"], "Example Bank")
    ensure_owner(stack["root"], wid, USER, index=InMemoryMembershipIndex())
    r = stack["client"].post("/api/workspace/target", json={"slug": wid}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["targeted"] == wid
    assert _workspace_focus(json.dumps(r.json())) == {"type": "focus", "workspace": wid,
                                                      **({"name": r.json()["name"]} if r.json().get("name") else {})}


def test_a_workspace_that_is_not_theirs_is_refused(stack):
    r = stack["client"].post("/api/workspace/target", json={"slug": "somebody-elses"}, headers=H)
    assert r.status_code in (403, 404)


def test_a_malformed_slug_is_a_400(stack):
    r = stack["client"].post("/api/workspace/target", json={"slug": "grp/../_global"}, headers=H)
    assert r.status_code == 400


def test_the_route_does_not_write_the_chats_target(stack):
    """One writer: `_binding_watch`, from the focus event the harness derives from this answer."""
    wid = create_shared_workspace_dir(stack["root"], "Copperline")
    ensure_owner(stack["root"], wid, USER, index=InMemoryMembershipIndex())
    stack["client"].post("/api/workspace/target", json={"slug": wid}, headers=H)
    rows = stack["client"].get("/api/sessions", headers=H).json()["sessions"]
    assert all(row.get("target") in (None, "") for row in rows)


# ── open_page ────────────────────────────────────────────────────────────────────────────────────

def test_a_page_on_the_desk_opens(stack):
    _desk_write(stack["client"], "kg/entities/person/ada.md")
    r = stack["client"].post("/api/page/open", json={"target": "kg/entities/person/ada.md"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["opened"] is True and body["path"] == "kg/entities/person/ada.md"
    assert _open_event(json.dumps(body)) == {"type": "open", "target": "kg/entities/person/ada.md",
                                             "workspace": "", "path": "kg/entities/person/ada.md"}


def test_a_page_that_is_not_there_is_not_opened(stack):
    r = stack["client"].post("/api/page/open", json={"target": "kg/nope.md"}, headers=H)
    assert r.status_code == 200
    assert r.json()["opened"] is False and r.json()["reason"]
    assert _open_event(json.dumps(r.json())) is None


def test_a_path_escaping_the_workspace_is_refused(stack):
    r = stack["client"].post("/api/page/open", json={"target": "../../etc/passwd"}, headers=H)
    assert r.status_code == 200 and r.json()["opened"] is False


def test_a_shared_workspace_page_opens_under_its_slug(stack):
    wid = create_shared_workspace_dir(stack["root"], "Example Bank")
    ensure_owner(stack["root"], wid, USER, index=InMemoryMembershipIndex())
    w = stack["client"].put("/api/workspace/file", json={"path": "notes.md", "content": "x", "slug": wid},
                            headers=H)
    assert w.status_code == 200, w.text
    r = stack["client"].post("/api/page/open", json={"target": f"{wid}/notes.md"}, headers=H)
    assert r.json()["opened"] is True and r.json()["workspace"] == wid and r.json()["path"] == "notes.md"


def test_the_transcript_opens_on_the_meeting_row_never_the_native_id(stack):
    r = stack["client"].post("/api/page/open", json={"target": "meeting:transcript", "meeting": "42"},
                             headers=H)
    assert r.json()["opened"] is True and r.json()["path"] == "meeting:42"


def test_a_meeting_with_no_words_does_not_open_an_empty_canvas(stack):
    r = stack["client"].post("/api/page/open", json={"target": "meeting:transcript", "meeting": "43"},
                             headers=H)
    assert r.json()["opened"] is False


def test_somebody_elses_meeting_is_refused(stack):
    r = stack["client"].post("/api/page/open", json={"target": "meeting:transcript", "meeting": "99"},
                             headers=H)
    assert r.status_code == 403


def test_a_meeting_target_needs_the_meeting(stack):
    r = stack["client"].post("/api/page/open", json={"target": "meeting:note"}, headers=H)
    assert r.json()["opened"] is False and "meeting" in r.json()["reason"]


# ── declared at the edge ─────────────────────────────────────────────────────────────────────────

def test_both_verbs_are_manifest_rows_on_these_routes():
    rows = {t["name"]: t["route"] for t in json.loads((AGENT / "mcp.tools.v1.json").read_text())["tools"]}
    assert rows["open_page"] == {"method": "POST", "path": "/api/page/open"}
    assert rows["workspace_target"] == {"method": "POST", "path": "/api/workspace/target"}
