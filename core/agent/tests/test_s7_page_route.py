"""PRD 0.13.4 S7 over HTTP: `POST /api/desktop/page` (the README rendered per viewer, D17), shared
pins (`/api/pins`, 7ee) and the commit guard (deny test 23: git holds placeholders only).

L2: the desktop test app (real git workspaces, sqlite session store, fake flows and meetings)."""
from __future__ import annotations

import json
import subprocess

import pytest

from control_plane import page_grammar as g
from control_plane import page_render as pr
from control_plane.identity_token import DELEGATION_HEADERS
from tests.test_desktop_sections import H, _shared, env  # noqa: F401

PAGE = "/api/desktop/page"
DELEGATED = {DELEGATION_HEADERS["workspaces"]: "*", DELEGATION_HEADERS["regime"]: "human"}

README = """# Tinkerers

{{waiting-on-you}}

## Pinned
{{pinned}}

## People
{{people}}

## Partners
{{crm company="OeNB"}}

Notes live in [[file:notes.md|Writer label]].
Get going: {{new-chat prompt="Plan it"}} {{new-room name="speakers"}}
"""


@pytest.fixture(autouse=True)
def _on(env, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,home2,readme_page")


def _write(env, who, slug, path, content):
    r = env["c"].put("/api/workspace/file", headers=H(who), json={"path": path, "content": content, "slug": slug})
    return r


def _page(env, who, slug, **body):
    r = env["c"].post(PAGE, headers=H(who), json={"workspace": slug, **body})
    assert r.status_code == 200, r.text
    return r.json()


def _git_log(env, slug):
    ws = env["root"] / slug
    if not (ws / ".git").exists():
        ws = next(p for p in env["root"].rglob(slug) if (p / ".git").exists())
    return subprocess.run(["git", "-C", str(ws), "log", "--format=%H"], capture_output=True, text=True).stdout.split()


def test_switched_off_and_delegated_are_refused(env, monkeypatch):
    assert env["c"].post(PAGE, headers={**H("u1"), **DELEGATED}, json={}).status_code == 403
    monkeypatch.setenv("VEXA_FEATURES", "all,home2")
    r = env["c"].post(PAGE, headers=H("u1"), json={})
    assert r.status_code == 403 and r.json()["detail"]["feature"] == "readme_page"


def test_a_workspace_without_a_readme_renders_the_seeded_default(env):
    wid = _shared(env, "Fresh", "u1")
    out = _page(env, "u1", wid)
    assert out["nodes"][0]["t"] == "md" and out["nodes"][0]["text"].startswith("# Fresh")
    body = json.dumps(out)
    assert "{{" not in body.replace("}}}", "")  and "waiting-on-you" not in body


def test_two_viewers_one_readme(env):
    wid = _shared(env, "Tinkerers", "u1", u2="viewer")
    assert _write(env, "u1", wid, "README.md", README).status_code == 200
    assert _write(env, "u1", wid, "notes.md", "# Venue notes\n").status_code == 200
    crm_key = g.Block("crm", {"company": "OeNB"}).key
    owner = _page(env, "u1", wid, crm={crm_key: {"view": "company", "rows": [
        {"id": "co1", "title": "OeNB", "type": "Company",
         "fields": [{"name": "Name", "value": "OeNB", "shared": True}, {"name": "Stage", "value": "Negotiation", "shared": False}]}]}})
    reader = _page(env, "u2", wid, crm={crm_key: {"view": "company", "rows": [
        {"id": "co1", "title": "OeNB", "type": "Company", "fields": [{"name": "Name", "value": "OeNB", "shared": True}]}]}})
    assert owner["role"] == "owner" and reader["role"] == "reader"
    # the directory rule (founder 2026-10-17): a reader sees the people too — names and roles only
    assert any(n.get("name") == "people" for n in owner["nodes"])
    people = next(n for n in reader["nodes"] if n.get("name") == "people")
    titles = {r["title"] for r in people["rows"]}
    assert len(people["rows"]) == 2 and any("(you)" in t for t in titles)
    body = json.dumps(people)
    assert "@" not in body                          # no email addresses, profile basics only
    assert "invite" not in body.lower() and "request" not in body.lower()
    # the CRM block: only-you on his side only
    assert "only_you" in json.dumps(owner) and "only_you" not in json.dumps(reader)
    # actions: descriptors; a reader's new-room is disabled with the reason
    acts = {p["verb"]: p for n in reader["nodes"] if n["t"] == "line" for p in n["parts"] if p["t"] == "action"}
    assert acts["new-room"]["enabled"] is False and acts["new-chat"]["enabled"] is True
    # a file ref relative to the page resolves for both, by the server's title (not the writer's label)
    got = owner["refs"]["file:notes.md"]
    assert got["access"] == "readable" and got["label"] and got["label"] != "Writer label"
    assert "Writer label" not in json.dumps(owner) and "Writer label" not in json.dumps(reader)


def test_render_never_commits_and_a_resolved_render_cannot_be_committed(env):
    """Deny test 23: no commit on the platform commit path contains a resolved placeholder value."""
    wid = _shared(env, "Tinkerers", "u1")
    assert _write(env, "u1", wid, "README.md", README).status_code == 200
    before = _git_log(env, wid)
    tree = _page(env, "u1", wid)
    assert _git_log(env, wid) == before                       # rendering wrote nothing
    exported = pr.to_markdown(tree)
    r = _write(env, "u1", wid, "README.md", exported)
    assert r.status_code == 422 and "placeholder" in json.dumps(r.json()).lower()
    assert _git_log(env, wid) == before
    # the source (placeholders) is still what git holds
    assert _write(env, "u1", wid, "README.md", README + "\nMore.\n").status_code == 200


def test_pins_are_shared_and_resolved_per_viewer(env):
    wid = _shared(env, "Tinkerers", "u1", u2="viewer")
    assert _write(env, "u1", wid, "notes.md", "# Venue notes\n").status_code == 200
    secret = env["store"].create("u1", name="private-chat", title="Board prep merger 41")
    ok = env["c"].post("/api/pins", headers=H("u1"), json={"workspace": wid, "ref": f"[[chat:{secret['id']}]]"})
    assert ok.status_code == 200, ok.text
    # a reader cannot pin
    no = env["c"].post("/api/pins", headers=H("u2"), json={"workspace": wid, "ref": f"[[chat:{secret['id']}]]"})
    assert no.status_code == 403
    mine = env["c"].get("/api/pins", headers=H("u1"), params={"workspace": wid}).json()["pins"]
    theirs = env["c"].get("/api/pins", headers=H("u2"), params={"workspace": wid}).json()["pins"]
    assert mine[0]["access"] == "readable" and mine[0]["label"] == "Board prep merger 41"
    assert theirs[0]["access"] == "restricted" and "label" not in theirs[0] and theirs[0]["by"] == "u1"
    assert "Board prep" not in json.dumps(theirs)
    # {{pinned}} in the README: restricted row without a name for the reader
    assert _write(env, "u1", wid, "README.md", "# T\n\n## Pinned\n{{pinned}}\n").status_code == 200
    rp = _page(env, "u2", wid)
    block = next(n for n in rp["nodes"] if n.get("name") == "pinned")
    assert block["rows"][0].get("restricted") is True and "title" not in block["rows"][0]
    # unpin
    off = env["c"].post("/api/pins", headers=H("u1"), json={"workspace": wid, "ref": f"[[chat:{secret['id']}]]", "pinned": False})
    assert off.json()["pins"] == []


def test_a_bad_path_or_a_missing_page_is_a_calm_refusal(env):
    wid = _shared(env, "Tinkerers", "u1")
    assert env["c"].post(PAGE, headers=H("u1"), json={"workspace": wid, "path": "../x.md"}).status_code == 400
    assert env["c"].post(PAGE, headers=H("u1"), json={"workspace": wid, "path": "nope.md"}).status_code == 404
    assert env["c"].post(PAGE, headers=H("u3"), json={"workspace": wid}).status_code in (403, 404)
