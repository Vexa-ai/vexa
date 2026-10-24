"""`workspace_search` — find pages in one workspace by the words in them (GET /api/workspace/search).

Reads exactly what `workspace_tree` lists, under the same read rules: the caller's own workspace, a
shared one they belong to, never a stranger's."""
from __future__ import annotations

from tests.test_r1_rooms import _h, world  # noqa: F401  (fixture: `team` owned by alice, bob contributor)


def _write(client, who, path, content, slug=None):
    body = {"path": path, "content": content, **({"slug": slug} if slug else {})}
    r = client.put("/api/workspace/file", headers=_h(who), json=body)
    assert r.status_code == 200, r.text


def test_every_word_must_occur_and_titles_rank_first(world):
    client, *_ = world
    _write(client, "alice", "kg/entities/meeting/acme-renewal.md",
           "---\ntitle: Acme renewal call\n---\nWe agreed on the renewal price.\n")
    _write(client, "alice", "notes/misc.md", "# Misc\nAcme came up once; renewal later. Acme again.\n")
    _write(client, "alice", "notes/other.md", "# Other\nNothing about that customer.\n")
    r = client.get("/api/workspace/search", headers=_h("alice"), params={"q": "ACME renewal"})
    assert r.status_code == 200, r.text
    body = r.json()
    paths = [h["path"] for h in body["results"]]
    assert paths[:2] == ["kg/entities/meeting/acme-renewal.md", "notes/misc.md"]
    assert "notes/other.md" not in paths
    top = body["results"][0]
    assert top["title"] == "Acme renewal call" and "renewal" in top["snippet"].lower()
    assert body["truncated"] is False


def test_folder_narrows_and_limit_caps(world):
    client, *_ = world
    _write(client, "alice", "kg/entities/meeting/a.md", "# A\nbudget\n")
    _write(client, "alice", "notes/b.md", "# B\nbudget\n")
    r = client.get("/api/workspace/search", headers=_h("alice"),
                   params={"q": "budget", "folder": "kg/entities/meeting"})
    assert [h["path"] for h in r.json()["results"]] == ["kg/entities/meeting/a.md"]
    r = client.get("/api/workspace/search", headers=_h("alice"), params={"q": "budget", "limit": 1})
    assert len(r.json()["results"]) == 1 and r.json()["truncated"] is True


def test_a_shared_workspace_is_searched_by_a_member_and_refused_to_a_stranger(world):
    client, *_ = world
    _write(client, "alice", "plan.md", "# Plan\nlaunch checklist\n", slug="team")
    r = client.get("/api/workspace/search", headers=_h("bob"), params={"q": "launch", "slug": "team"})
    assert r.status_code == 200, r.text
    assert [h["path"] for h in r.json()["results"]] == ["plan.md"]
    r = client.get("/api/workspace/search", headers=_h("mallory"), params={"q": "launch", "slug": "team"})
    assert r.status_code in (403, 404), r.text


def test_an_empty_query_is_refused(world):
    client, *_ = world
    assert client.get("/api/workspace/search", headers=_h("alice"), params={"q": ""}).status_code == 422
