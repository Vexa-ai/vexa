"""Connect/disconnect workspace docs to a meeting — ``meeting.data['docs']`` (the connection store).

Drives the collector ``create_app`` over the in-memory fake, OFFLINE (TestClient, no docker/DB):
  * POST /meetings/{platform}/{native_meeting_id}/docs → appends a {workspace, path, ...} ref;
  * re-connecting the same path → no duplicate (deduped/updated by path);
  * DELETE …/docs → the ref is gone;
  * the connected docs surface on GET /meetings (and GET /meetings/{id});
  * owner-scoped (another user → 404) + fail-closed (no x-user-id → 401).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
H = {"x-user-id": str(USER)}
PLAT, NID = "google_meet", "abc-defg-hij"


def _client():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NID)
    return TestClient(create_app(store, redis=None)), mid


def test_connect_doc_appears_in_meeting_docs():
    client, mid = _client()
    r = client.post(
        f"/meetings/{PLAT}/{NID}/docs",
        json={"path": "notes/intro.md", "workspace": "u_live", "title": "Intro", "kind": "note"},
        headers=H,
    )
    assert r.status_code == 200, r.text
    docs = r.json()["docs"]
    assert docs == [{"workspace": "u_live", "path": "notes/intro.md", "title": "Intro", "kind": "note"}]

    # surfaces on GET /meetings
    rl = client.get("/meetings", headers=H)
    assert rl.status_code == 200
    meeting = rl.json()["meetings"][0]
    assert meeting["data"]["docs"] == docs

    # and on GET /meetings/{id}
    rd = client.get(f"/meetings/{mid}", headers=H)
    assert rd.status_code == 200
    assert rd.json()["data"]["docs"] == docs


def test_connect_same_path_twice_no_dup():
    client, _ = _client()
    client.post(f"/meetings/{PLAT}/{NID}/docs",
                json={"path": "a.md", "workspace": "first"}, headers=H)
    r = client.post(f"/meetings/{PLAT}/{NID}/docs",
                    json={"path": "a.md", "workspace": "second"}, headers=H)
    docs = r.json()["docs"]
    assert len(docs) == 1
    assert docs[0] == {"workspace": "second", "path": "a.md"}  # updated in place


def test_multiple_docs_then_delete_one():
    client, _ = _client()
    client.post(f"/meetings/{PLAT}/{NID}/docs", json={"path": "a.md", "workspace": "a"}, headers=H)
    client.post(f"/meetings/{PLAT}/{NID}/docs", json={"path": "b.md", "workspace": "b"}, headers=H)

    # delete via body
    r = client.request(
        "DELETE", f"/meetings/{PLAT}/{NID}/docs", json={"path": "a.md"}, headers=H
    )
    assert r.status_code == 200, r.text
    assert [d["path"] for d in r.json()["docs"]] == ["b.md"]

    # delete via query param
    r2 = client.delete(f"/meetings/{PLAT}/{NID}/docs?path=b.md", headers=H)
    assert r2.status_code == 200
    assert r2.json()["docs"] == []


def test_meeting_docs_absent_treated_as_empty():
    client, _ = _client()
    rl = client.get("/meetings", headers=H)
    assert rl.json()["meetings"][0]["data"].get("docs", []) == []


def test_connect_doc_owner_scoped_404():
    client, _ = _client()
    r = client.post(
        f"/meetings/{PLAT}/{NID}/docs",
        json={"path": "a.md", "workspace": "a"},
        headers={"x-user-id": "999"},
    )
    assert r.status_code == 404


def test_connect_doc_requires_user_identity():
    client, _ = _client()
    r = client.post(f"/meetings/{PLAT}/{NID}/docs", json={"path": "a.md", "workspace": "a"})
    assert r.status_code == 401


def test_connect_doc_requires_path_and_workspace():
    client, _ = _client()
    assert client.post(f"/meetings/{PLAT}/{NID}/docs",
                       json={"workspace": "x"}, headers=H).status_code == 422
    assert client.post(f"/meetings/{PLAT}/{NID}/docs",
                       json={"path": "x"}, headers=H).status_code == 422
