"""A delegated dispatch's workspace ceiling bounds the reads that walk a person-wide set.

Most routes name one workspace and resolve it, and the resolver holds it to the ceiling. These
read across EVERY workspace the person has — their memberships, their mounts, the workspaces a
page's links name — so they are bounded where the set is walked, by `ceiling.reads_within`:

* `GET /api/workspace/shared` (the `workspaces` tool) lists only memberships inside the ceiling;
* `POST /api/meeting/terms/scan` (`transcript_terms`) asks meeting-api with only those memberships,
  and answers `known` only from pages inside it;
* `POST /api/workspace/entity` (`entity_upsert`) resolves and rewrites `[[links]]` only into it;
* `POST /api/links/resolve` answers `not-yours` for a workspace outside it;
* `POST /api/workspace/invites/accept` joins only a workspace inside it.

In every case the person — no delegation — sees exactly what they saw before. The caller here is a
person (`126`) who owns two groups, A and B; the worker's ceiling grants A only.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.identity_token import DELEGATION_HEADERS
from control_plane.workspace_ids import ACCESS_NOT_YOURS, ACCESS_READABLE
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

ME = "126"
ROW = "147"
SEGMENTS = [
    {"start": 1.0, "absolute_start_time": "2026-10-09T10:00:01Z", "speaker": "Ana",
     "text": "Northwind Labs came back on the pricing."},
    {"start": 5.0, "absolute_start_time": "2026-10-09T10:00:05Z", "speaker": "Ben",
     "text": "Robin Vale is the one who signs it."},
]


def _me(subject: str = ME) -> dict:
    return {"X-User-Id": subject, "X-User-Email": f"{subject}@example.com"}


def _worker(*workspaces: str, regime: str = "autonomous") -> dict:
    return {**_me(), DELEGATION_HEADERS["regime"]: regime,
            DELEGATION_HEADERS["workspaces"]: ",".join(workspaces)}


@pytest.fixture()
def world(tmp_path):
    asked: list = []

    def _owner(user_id, meeting_id, workspaces=None):
        asked.append(list(workspaces or []))
        return {"id": meeting_id, "native_meeting_id": "abc-defg-hij"} if user_id == ME else None

    def _transcript(user_id, meeting_id, workspaces=None):
        asked.append(list(workspaces or []))
        return list(SEGMENTS) if user_id == ME else None

    c = TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(tmp_path)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path)), meeting_owner_lookup=_owner,
        meeting_transcript_lookup=_transcript))
    assert c.put("/api/workspace/file", headers=_me(),
                 json={"path": "notes.md", "content": "my desk"}).status_code == 200
    a = c.post("/api/workspace/shared/new", headers=_me(), json={"name": "Alpha"}).json()["workspace_id"]
    b = c.post("/api/workspace/shared/new", headers=_me(), json={"name": "Beta"}).json()["workspace_id"]
    for slug, kind, name in ((a, "company", "Northwind Labs"), (b, "person", "Robin Vale")):
        r = c.post("/api/workspace/entity", headers=_me(), json={
            "slug": slug, "kind": kind, "name": name, "facts": ["Named on the call."],
            "source": "the call"})
        assert r.status_code == 200, r.text
    asked.clear()
    return c, a, b, asked


def test_the_workspaces_listing_is_bounded_by_the_ceiling(world):
    c, a, b, _ = world
    mine = {m["workspace_id"] for m in c.get("/api/workspace/shared", headers=_me()).json()["memberships"]}
    assert {a, b} <= mine
    worker = c.get("/api/workspace/shared", headers=_worker(a)).json()["memberships"]
    assert {m["workspace_id"] for m in worker} == {a}


def test_transcript_terms_reads_nothing_from_outside_the_ceiling(world):
    c, a, b, asked = world
    r = c.post("/api/meeting/terms/scan", headers=_worker(a), json={"meeting_id": ROW})
    assert r.status_code == 200, r.text
    assert asked and all(b not in ws for ws in asked), asked
    assert all(a in ws for ws in asked), "the workspace inside the ceiling is still asked with"
    known = {t["term"]: t["known"] for t in r.json()["terms"]}
    assert known["Northwind Labs"] is not None
    assert known["Robin Vale"] is None

    asked.clear()
    r = c.post("/api/meeting/terms/scan", headers=_me(), json={"meeting_id": ROW})
    assert all({a, b} <= set(ws) for ws in asked), asked
    known = {t["term"]: t["known"] for t in r.json()["terms"]}
    assert known["Northwind Labs"] is not None and known["Robin Vale"] is not None


def test_entity_upsert_links_only_into_the_ceiling(world):
    c, a, b, _ = world
    reg = c.app.state.workspace_registry
    a_id, b_id = reg.by_slug(a)["id"], reg.by_slug(b)["id"]
    body = {"kind": "person", "name": "Ana Lima", "source": "the call",
            "facts": ["Met [[Northwind Labs]] about [[Robin Vale]]."]}
    r = c.post("/api/workspace/entity", headers=_worker(a), json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert "Robin Vale" in out["links_missing"]
    page = c.get("/api/workspace/file", headers=_me(), params={"path": out["path"]}).json()["content"]
    assert f"ws:{a_id}/" in page and f"ws:{b_id}/" not in page

    r = c.post("/api/workspace/entity", headers=_me(),
               json={**body, "name": "Ben Ode"})
    out = r.json()
    assert "Robin Vale" not in out["links_missing"]
    page = c.get("/api/workspace/file", headers=_me(), params={"path": out["path"]}).json()["content"]
    assert f"ws:{b_id}/" in page


def test_links_resolve_answers_not_yours_outside_the_ceiling(world):
    c, a, b, _ = world
    reg = c.app.state.workspace_registry
    a_ref, b_ref = f"ws:{reg.by_slug(a)['id']}/northwind-labs", f"ws:{reg.by_slug(b)['id']}/robin-vale"
    worker = {r["ref"]: r for r in c.post("/api/links/resolve", headers=_worker(a),
                                          json={"refs": [a_ref, b_ref]}).json()["results"]}
    assert worker[a_ref]["access"] == ACCESS_READABLE and worker[a_ref]["url"]
    assert worker[b_ref]["access"] == ACCESS_NOT_YOURS
    assert worker[b_ref]["url"] is None and "path" not in worker[b_ref]
    assert worker[b_ref]["title"] == "Robin Vale"     # derived from the ref, never read
    mine = {r["ref"]: r for r in c.post("/api/links/resolve", headers=_me(),
                                        json={"refs": [a_ref, b_ref]}).json()["results"]}
    assert mine[b_ref]["access"] == ACCESS_READABLE and mine[b_ref]["url"]


def test_an_invite_joins_only_a_workspace_inside_the_ceiling(world):
    c, a, _, _ = world
    other = c.post("/api/workspace/shared/new", headers=_me("127"), json={"name": "Gamma"}).json()["workspace_id"]
    token = c.post("/api/workspace/invites", headers=_me("127"),
                   json={"workspace_id": other, "role": "contributor"}).json()["token"]
    r = c.post("/api/workspace/invites/accept", headers=_worker(a, regime="human"), json={"token": token})
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "out_of_scope", r.text
    listed = {m["workspace_id"] for m in c.get("/api/workspace/shared", headers=_me()).json()["memberships"]}
    assert other not in listed
    assert c.post("/api/workspace/invites/accept", headers=_me(), json={"token": token}).status_code == 200
