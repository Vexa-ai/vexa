"""A new person's first chat, and "create a workspace and share it" said in one sentence.

Both failed on app.dev on 2026-10-13 (Vexa-ai/vexa#1783):

* a person created that day asked their agent to look around, and `workspace_tree(slug="personal")`
  answered `403 not authorized for this workspace` — `personal` is the word the MCP verbs use for
  the person's own workspace, and the tree resolver did not know it;
* a person asked for a workspace named "eugenia - dmitry" shared with one address as contributor.
  `workspace_new` answered slug `workspace-1` (the name was only a label), and `workspace_invite` /
  `workspace_members` on it answered `403 insufficient role` — a private workspace has no roster, so
  nobody, its creator included, owned it as far as sharing was concerned.

What has to be true: `personal` reads the person's own workspace; a named new workspace is
addressed by its name; inviting someone to one of your own private workspaces makes it shared with
you as owner and sends the invite, in that one call; and its roster answers its creator.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

ME = "47"
MY_EMAIL = "new.person@example.com"
THEM = "someone.else@example.com"


def _client(root: Path) -> TestClient:
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", redis_url="",
                             ui_url="https://app.example")
    return TestClient(create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                                 reader=WorkspaceReader(str(root))))


def _me(subject: str = ME, email: str = MY_EMAIL) -> dict:
    return {"X-User-Id": subject, "x-user-email": email}


import pytest


@pytest.fixture(autouse=True)
def _seed(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    (seed / "kg").mkdir(parents=True)
    (seed / "CLAUDE.md").write_text("root\n")
    (seed / "kg" / "INDEX.md").write_text("# Index\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))


def _fresh(tmp_path: Path) -> TestClient:
    c = _client(tmp_path / "ws")
    assert c.post("/api/workspace/init", headers=_me()).status_code == 201
    return c


# ── a new person's first look ────────────────────────────────────────────────────────────────────

def test_personal_reads_the_persons_own_workspace(tmp_path):
    c = _fresh(tmp_path)
    own = c.get("/api/workspace/tree", headers=_me())
    named = c.get("/api/workspace/tree", params={"slug": "personal"}, headers=_me())
    assert named.status_code == 200, named.text
    assert named.json() == own.json()


# ── create, by name ──────────────────────────────────────────────────────────────────────────────

def test_a_named_workspace_is_addressed_by_its_name(tmp_path):
    c = _fresh(tmp_path)
    first = c.post("/api/workspace/new", json={"name": "eugenia - dmitry"}, headers=_me())
    assert first.status_code == 201, first.text
    assert first.json()["slug"] == "eugenia-dmitry"
    again = c.post("/api/workspace/new", json={"name": "Eugenia - Dmitry"}, headers=_me())
    assert again.json()["slug"] == "eugenia-dmitry-2"


def test_an_unnamed_workspace_keeps_the_numbered_slug(tmp_path):
    c = _fresh(tmp_path)
    assert c.post("/api/workspace/new", json={}, headers=_me()).json()["slug"] == "workspace-1"


def test_a_name_that_is_a_reserved_word_does_not_take_that_slug(tmp_path):
    c = _fresh(tmp_path)
    slug = c.post("/api/workspace/new", json={"name": "Personal"}, headers=_me()).json()["slug"]
    assert slug != "personal"
    assert c.get("/api/workspace/tree", params={"slug": "personal"}, headers=_me()).json() == \
        c.get("/api/workspace/tree", headers=_me()).json()


# ── share it: the exact sequence from the chat ───────────────────────────────────────────────────

def test_create_then_invite_shares_it_with_the_creator_as_owner(tmp_path):
    c = _fresh(tmp_path)
    slug = c.post("/api/workspace/new", json={"name": "eugenia - dmitry"}, headers=_me()).json()["slug"]

    roster = c.get("/api/workspace/members", params={"workspace_id": slug}, headers=_me())
    assert roster.status_code == 200, roster.text
    assert [(m["subject"], m["role"]) for m in roster.json()["members"]] == [(ME, "owner")]

    r = c.post("/api/workspace/invite", json={"slug": slug, "email": THEM, "role": "contributor"},
               headers=_me())
    assert r.status_code == 200, r.text
    body = r.json()
    shared = body["workspace_id"]
    assert shared.startswith("eugenia-dmitry")
    assert body.get("shared_now") is True

    roster = c.get("/api/workspace/members", params={"workspace_id": shared}, headers=_me())
    assert roster.status_code == 200, roster.text
    assert (ME, "owner") in [(m["subject"], m["role"]) for m in roster.json()["members"]]
    listed = c.get("/api/workspace/shared", headers=_me()).json()["memberships"]
    assert shared in [m["workspace_id"] for m in listed]

    # a second invite to the now-shared workspace is an ordinary owner's invite
    again = c.post("/api/workspace/invite",
                   json={"slug": shared, "email": "third@example.com", "role": "reader"},
                   headers=_me())
    assert again.status_code == 200, again.text
    assert again.json().get("shared_now") in (None, False)


def test_an_existing_private_workspace_can_be_shared_by_inviting(tmp_path):
    c = _fresh(tmp_path)
    slug = c.post("/api/workspace/new", json={}, headers=_me()).json()["slug"]
    r = c.post("/api/workspace/invite", json={"slug": slug, "email": THEM, "role": "contributor"},
               headers=_me())
    assert r.status_code == 200, r.text


def test_nobody_else_can_share_my_private_workspace(tmp_path):
    c = _fresh(tmp_path)
    slug = c.post("/api/workspace/new", json={"name": "mine"}, headers=_me()).json()["slug"]
    r = c.post("/api/workspace/invite", json={"slug": slug, "email": THEM, "role": "owner"},
               headers=_me("99", "intruder@example.com"))
    assert r.status_code in (403, 404), r.text


def test_the_personal_workspace_is_not_shared_by_inviting(tmp_path):
    c = _fresh(tmp_path)
    for slug in ("personal", ME):
        r = c.post("/api/workspace/invite", json={"slug": slug, "email": THEM, "role": "reader"},
                   headers=_me())
        assert r.status_code in (403, 409), r.text


# ── the exact exchange of 2026-10-13: names, not slugs ─────────────────────────────────────────────
#
# The Files pane showed Personal, biz, "eugenia - dmitry" and "Eugenia - Dmitry". The person said
# "share this workspace with … — not biz! this one ▶ Eugenia - Dmitry"; the agent answered that no
# workspace was called that, listing `workspace-1` and `workspace-2` as unnamed.

def _the_founders_shape(c):
    """Two retries' worth of blank workspaces with near-identical names, and a named repo slot."""
    a = c.post("/api/workspace/new", json={}, headers=_me()).json()["slug"]
    b = c.post("/api/workspace/new", json={}, headers=_me()).json()["slug"]
    assert (a, b) == ("workspace-1", "workspace-2")
    for slug, name in ((a, "eugenia - dmitry"), (b, "Eugenia - Dmitry")):
        assert c.post("/api/workspace/rename", json={"slug": slug, "name": name},
                      headers=_me()).status_code == 200
    biz = c.post("/api/workspace/new", json={"name": "biz"}, headers=_me()).json()["slug"]
    return a, b, biz


def test_the_listing_names_every_workspace_as_the_pane_does(tmp_path):
    c = _fresh(tmp_path)
    a, b, biz = _the_founders_shape(c)
    rows = c.get("/api/workspace/shared", headers=_me()).json()["all"]
    by_slug = {r["slug"]: r for r in rows}
    assert by_slug["personal"]["name"] == "Personal" and by_slug["personal"]["kind"] == "personal"
    assert by_slug[a]["name"] == "eugenia - dmitry" and by_slug[a]["kind"] == "private"
    assert by_slug[b]["name"] == "Eugenia - Dmitry"
    assert by_slug[biz]["name"] == "biz"
    assert by_slug[a]["same_name_as"] == [b] and by_slug[b]["same_name_as"] == [a]
    assert by_slug[a]["empty"] is True and by_slug[b]["empty"] is True


def test_share_this_one_by_its_name_defaults_to_contributor(tmp_path):
    c = _fresh(tmp_path)
    a, b, _ = _the_founders_shape(c)
    r = c.post("/api/workspace/invite", json={"slug": "Eugenia - Dmitry", "email": THEM},
               headers=_me())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shared_now"] is True and body["role"] == "contributor"
    assert "contributor" in body["said"]
    rows = c.get("/api/workspace/shared", headers=_me()).json()["all"]
    shared = next(x for x in rows if x["slug"] == body["workspace_id"])
    assert shared["kind"] == "shared" and shared["name"] == "Eugenia - Dmitry"
    assert a in {x["slug"] for x in rows}, "the other one is untouched"


def test_two_workspaces_answering_one_name_are_asked_about(tmp_path):
    c = _fresh(tmp_path)
    _the_founders_shape(c)
    r = c.post("/api/workspace/invite", json={"slug": "EUGENIA - DMITRY", "email": THEM},
               headers=_me())
    assert r.status_code == 409, r.text
    assert {m["slug"] for m in r.json()["matches"]} == {"workspace-1", "workspace-2"}


def test_a_name_reads_and_removes_too(tmp_path):
    c = _fresh(tmp_path)
    a, _, biz = _the_founders_shape(c)
    assert c.get("/api/workspace/tree", params={"slug": "biz"}, headers=_me()).status_code == 200
    r = c.delete("/api/workspace/eugenia - dmitry", headers=_me())
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == a
    rows = c.get("/api/workspace/shared", headers=_me()).json()["all"]
    assert a not in {x["slug"] for x in rows}


# ── a workspace made in a chat is where that chat writes (founder, 2026-10-13) ─────────────────────
#
# The agent made "DNA check-in" in a chat; the chat kept writing to Personal and the Writes-to menu
# could not offer it. `workspace_new` answered no `created`, so the harness's `focus` event — the
# one that mounts it and makes it the chat's target — never fired; and nothing registered it, so the
# header's by-slug check could not name it.

def test_a_new_workspace_answers_what_the_focus_event_reads_and_is_addressable(tmp_path):
    from llm import tool_events
    c = _fresh(tmp_path)
    r = c.post("/api/workspace/new", json={"name": "DNA check-in"}, headers=_me())
    body = r.json()
    assert body["created"] == body["slug"] == "dna-check-in" and body["name"] == "DNA check-in"
    ev = tool_events._workspace_focus(__import__("json").dumps(body))
    assert ev == {"type": "focus", "workspace": "dna-check-in", "name": "DNA check-in"}
    rec = c.get("/api/workspaces/by-slug/dna-check-in", headers=_me()).json()
    assert rec["access"] == "readable" and rec["writable"] is True and rec["name"] == "DNA check-in"
