"""The identity + link-resolution ROUTES, over the real app.

PRD decision 26.1-26.3. Three reads and one write-seam, and the property under test in every case
is the same: **a link never errors.** `not-yours` and `gone` come back 200 with a title, because a
status code makes the client render an error where the design says render a greyed chip.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane import workspace_ids as ids
from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from shared.entities import upsert_entity
from shared.workspace_id import WORKSPACE_JSON, read_workspace_json


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _init_ws(root: Path, workspace_id: str) -> Path:
    ws = root / workspace_id
    (ws / "kg" / "entities").mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _client(root: Path, index=None):
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
        membership_index=index or m.InMemoryMembershipIndex(),
    ))


def _h(subject: str) -> dict:
    return {"X-User-Id": subject}


def _world(root: Path):
    """Desk 126, desk 127, and a group 126 owns. Built BEFORE the app, so the startup migration is
    what gives them their ids — which is the migration path the live instance takes."""
    _init_ws(root, "126")
    _init_ws(root, "127")
    _init_ws(root, "grp")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(root, "grp", "126", index=idx)
    upsert_entity(root / "grp", "person", "Cottalango Leon", ["Chairs the TSC."], "the meeting")
    upsert_entity(root / "126", "person", "Olga Avramenko", ["Attends."], "the meeting")
    return _client(root, idx)


# ── the migration runs at boot ───────────────────────────────────────────────────────────────────

def test_starting_the_app_gives_every_existing_workspace_an_id(tmp_path):
    c = _world(tmp_path)
    for slug in ("126", "127", "grp"):
        assert (tmp_path / slug / WORKSPACE_JSON).is_file()
        body = c.get(f"/api/workspaces/by-slug/{slug}", headers=_h("126")).json()
        assert body["id"] == read_workspace_json(tmp_path / slug)["id"]


def test_a_desk_is_named_never_numbered(tmp_path):
    """F49 — the chat header read `126`, which was the directory name showing through."""
    c = _world(tmp_path)
    assert c.get("/api/workspaces/by-slug/126", headers=_h("126")).json()["name"] == "Desk 126"


def test_init_names_the_desk_after_the_address_that_signed_in(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("governance root\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    c = _client(tmp_path / "root")
    r = c.post("/api/workspace/init", headers={**_h("126"), "X-User-Email": "olga@spi.com"})
    assert r.status_code == 201, r.text
    body = c.get("/api/workspaces/by-slug/126", headers=_h("126")).json()
    assert body["name"] == "olga@spi.com" and body["kind"] == "desk"
    assert body["access"] == ids.ACCESS_READABLE


def test_creating_a_group_keeps_the_human_name_the_slug_threw_away(tmp_path):
    c = _client(tmp_path)
    wid = c.post("/api/workspace/shared/new", headers=_h("126"),
                 json={"name": "ASWF DNA Project"}).json()["workspace_id"]
    assert wid.startswith("aswf-dna-project-")
    body = c.get(f"/api/workspaces/by-slug/{wid}", headers=_h("126")).json()
    assert body["name"] == "ASWF DNA Project" and body["kind"] == "group"
    assert body["access"] == ids.ACCESS_READABLE


# ── GET /api/workspaces/{id} ─────────────────────────────────────────────────────────────────────

def test_resolve_by_id_reports_access_per_reader(tmp_path):
    c = _world(tmp_path)
    gid = c.get("/api/workspaces/by-slug/grp", headers=_h("126")).json()["id"]
    assert c.get(f"/api/workspaces/{gid}", headers=_h("126")).json()["access"] == ids.ACCESS_READABLE
    other = c.get(f"/api/workspaces/{gid}", headers=_h("127"))
    assert other.status_code == 200                    # never a 403 — "not yours" is an ANSWER
    assert other.json()["access"] == ids.ACCESS_NOT_YOURS
    assert other.json()["name"] == "grp"               # the greyed chip still says WHAT it is


def test_an_unknown_id_is_gone_and_still_200(tmp_path):
    c = _world(tmp_path)
    r = c.get("/api/workspaces/zzzzzzzzzz", headers=_h("126"))
    assert r.status_code == 200 and r.json() == {"id": "zzzzzzzzzz", "name": None, "kind": None,
                                                 "access": ids.ACCESS_GONE, "writable": False}


# ── POST /api/links/resolve ──────────────────────────────────────────────────────────────────────

def test_resolve_a_page_of_refs_in_one_round_trip(tmp_path):
    c = _world(tmp_path)
    gid = c.get("/api/workspaces/by-slug/grp", headers=_h("126")).json()["id"]
    did127 = c.get("/api/workspaces/by-slug/127", headers=_h("127")).json()["id"]
    refs = [f"ws:{gid}/cottalango-leon", f"ws:{did127}/whoever", "ws:zzzzzzzzzz/gone",
            "Olga Avramenko"]
    out = c.post("/api/links/resolve", headers=_h("126"), json={"refs": refs}).json()["results"]
    by_ref = {r["ref"]: r for r in out}

    ok = by_ref[f"ws:{gid}/cottalango-leon"]
    assert ok["access"] == ids.ACCESS_READABLE and ok["title"] == "Cottalango Leon"
    assert ok["url"] == f"/w/{gid}/kg/entities/person/cottalango-leon.md"

    # A COLLEAGUE'S DESK IS READABLE (founder ruling 2026-09-02) and is never writable. It is a
    # ref to a page nobody has written, so it comes back `missing` — readable and not there, which
    # opens the panel's own empty state rather than refusing the click.
    other = by_ref[f"ws:{did127}/whoever"]
    assert other["access"] == ids.ACCESS_READABLE and other["writable"] is False
    assert by_ref["ws:zzzzzzzzzz/gone"]["access"] == ids.ACCESS_GONE

    mine = by_ref["Olga Avramenko"]
    assert mine["access"] == ids.ACCESS_READABLE and mine["title"] == "Olga Avramenko"
    assert mine["url"].endswith("/kg/entities/person/olga-avramenko.md")


def test_naming_a_group_you_are_not_in_as_here_does_not_open_it(tmp_path):
    """The in-workspace form resolves in the workspace the READER named, so that name is a claim
    and it is checked: a group they are not a member of resolves to nothing rather than to its
    pages. (A colleague's DESK is a different answer — readable, per the ruling — which is what the
    test below asserts, and the reason this guard is worth keeping distinct from it.)"""
    c = _world(tmp_path)
    out = c.post("/api/links/resolve", headers=_h("127"),
                 json={"refs": ["Cottalango Leon"], "slug": "grp"}).json()["results"]
    assert out[0]["access"] == ids.ACCESS_GONE and out[0]["url"] is None


def test_naming_a_colleagues_desk_as_here_resolves_and_stays_read_only(tmp_path):
    c = _world(tmp_path)
    out = c.post("/api/links/resolve", headers=_h("127"),
                 json={"refs": ["Olga Avramenko"], "slug": "126"}).json()["results"]
    assert out[0]["access"] == ids.ACCESS_READABLE and out[0]["writable"] is False


# ── the ruling, end to end: read follows the chip, write does not ───────────────────────────────

def test_a_colleague_may_READ_another_desk_through_the_file_api(tmp_path):
    """The chip and the endpoint must agree. Before the ruling the resolver would have said
    `not-yours`; after it, saying `readable` and then 403-ing the click would be worse than either
    answer alone."""
    c = _world(tmp_path)
    r = c.get("/api/workspace/file?path=kg/entities/person/olga-avramenko.md&slug=126",
              headers=_h("127"))
    assert r.status_code == 200 and "Olga Avramenko" in r.json()["content"]
    assert c.get("/api/workspace/tree?slug=126", headers=_h("127")).status_code == 200


def test_a_colleague_may_NOT_WRITE_another_desk(tmp_path):
    c = _world(tmp_path)
    assert c.put("/api/workspace/file", headers=_h("127"),
                 json={"path": "kg/x.md", "content": "no", "slug": "126"}).status_code == 403
    assert c.post("/api/workspace/entity", headers=_h("127"),
                  json={"kind": "person", "name": "Intruder", "facts": ["f"], "source": "s",
                        "slug": "126"}).status_code == 403


def test_the_read_widening_reaches_desks_only_never_a_group(tmp_path):
    """The registry is asked, not the directory layout, so this can only ever resolve something
    that IS a desk: a group the caller does not belong to still 403s, and `_system` has no registry
    row at all — by construction, precisely so nothing can reach it this way."""
    c = _world(tmp_path)
    assert c.get("/api/workspace/tree?slug=grp", headers=_h("127")).status_code == 403


# ── the usage signal (founder refinement 2026-09-02) ────────────────────────────────────────────

def test_a_touch_is_filed_under_the_callers_own_desk_and_mirrored_for_the_worker(tmp_path):
    """The DESK a touch belongs to is never a parameter — "whose desk is this" is not a question a
    client gets to answer."""
    from shared.workspace_id import TOUCHES_FILE, read_touches

    c = _world(tmp_path)
    gid = c.get("/api/workspaces/by-slug/grp", headers=_h("126")).json()["id"]
    r = c.post("/api/desk/touch", headers=_h("126"),
               json={"workspace": gid, "path": "kg/entities/person/cottalango-leon.md"})
    assert r.status_code == 202 and r.json() == {"recorded": True}

    rows = read_touches(tmp_path / "126")
    assert rows and rows[0]["workspace"] == gid
    assert rows[0]["path"] == "kg/entities/person/cottalango-leon.md"
    # excluded from git, so the turn's `git add -A` cannot commit a new version every turn
    assert f"/{TOUCHES_FILE}" in (tmp_path / "126" / ".git" / "info" / "exclude").read_text()


def test_a_touch_on_a_workspace_you_cannot_read_records_nothing_and_leaks_nothing(tmp_path):
    from shared.workspace_id import read_touches

    c = _world(tmp_path)
    gid = c.get("/api/workspaces/by-slug/grp", headers=_h("126")).json()["id"]
    r = c.post("/api/desk/touch", headers=_h("127"), json={"workspace": gid, "path": "README.md"})
    assert r.status_code == 202 and r.json() == {"recorded": False}   # never a 403 — nothing probed
    assert read_touches(tmp_path / "127") == []


def test_a_touch_refuses_a_traversal_and_an_empty_body(tmp_path):
    c = _world(tmp_path)
    gid = c.get("/api/workspaces/by-slug/grp", headers=_h("126")).json()["id"]
    assert c.post("/api/desk/touch", headers=_h("126"),
                  json={"workspace": gid, "path": "../126/secret.md"}).status_code == 400
    assert c.post("/api/desk/touch", headers=_h("126"), json={}).status_code == 400


def test_the_desk_readme_orders_by_what_was_touched(tmp_path):
    """End to end: the panel reports, the mirror lands, and the generator ranks by it."""
    from shared import desk_readme
    from shared.workspace_id import read_touches

    c = _world(tmp_path)
    gid = c.get("/api/workspaces/by-slug/grp", headers=_h("126")).json()["id"]
    did = c.get("/api/workspaces/by-slug/126", headers=_h("126")).json()["id"]
    upsert_entity(tmp_path / "grp", "person", "Aaa First", ["x"], "s")
    c.post("/api/desk/touch", headers=_h("126"),
           json={"workspace": gid, "path": "kg/entities/person/cottalango-leon.md"})

    desk_readme.update_readme(
        tmp_path / "126",
        mounts=[{"path": str(tmp_path / "126"), "id": did}, {"path": str(tmp_path / "grp"), "id": gid}],
        home_id=did, touches=read_touches(tmp_path / "126"))
    people = (tmp_path / "126" / "README.md").read_text().split("## People")[1]
    assert people.strip().splitlines()[0] == f"- [[ws:{gid}/cottalango-leon]]"


# ── rename (founder ruling 2026-09-02: the group's owner, and admins; audited) ───────────────────

def test_the_groups_owner_renames_it_and_every_link_survives(tmp_path):
    c = _world(tmp_path)
    gid = c.get("/api/workspaces/by-slug/grp", headers=_h("126")).json()["id"]
    ref = f"ws:{gid}/cottalango-leon"
    before = c.post("/api/links/resolve", headers=_h("126"), json={"refs": [ref]}).json()["results"][0]

    r = c.post(f"/api/workspaces/{gid}/rename", headers=_h("126"),
               json={"name": "Digital Naming Authority"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Digital Naming Authority" and r.json()["renamed_from"] == "grp"

    after = c.post("/api/links/resolve", headers=_h("126"), json={"refs": [ref]}).json()["results"][0]
    assert after["url"] == before["url"] and after["access"] == ids.ACCESS_READABLE
    assert after["workspace"] == "Digital Naming Authority"


def test_a_non_owner_may_not_rename_and_an_unknown_id_is_404(tmp_path):
    c = _world(tmp_path)
    gid = c.get("/api/workspaces/by-slug/grp", headers=_h("126")).json()["id"]
    assert c.post(f"/api/workspaces/{gid}/rename", headers=_h("127"),
                  json={"name": "Mine now"}).status_code == 403
    assert c.get(f"/api/workspaces/{gid}", headers=_h("126")).json()["name"] == "grp"
    assert c.post("/api/workspaces/zzzzzzzzzz/rename", headers=_h("126"),
                  json={"name": "X"}).status_code == 404
    assert c.post(f"/api/workspaces/{gid}/rename", headers=_h("126"),
                  json={"name": "  "}).status_code == 400


def test_a_desk_cannot_be_renamed_by_its_owner_through_the_route(tmp_path):
    c = _world(tmp_path)
    did = c.get("/api/workspaces/by-slug/126", headers=_h("126")).json()["id"]
    r = c.post(f"/api/workspaces/{did}/rename", headers=_h("126"), json={"name": "Olga"})
    assert r.status_code == 403 and "admin" in r.json()["detail"]


def test_the_identity_read_reports_writable_per_reader(tmp_path):
    c = _world(tmp_path)
    mine = c.get("/api/workspaces/by-slug/126", headers=_h("126")).json()
    theirs = c.get("/api/workspaces/by-slug/126", headers=_h("127")).json()
    assert mine["writable"] is True and mine["access"] == ids.ACCESS_READABLE
    assert theirs["writable"] is False and theirs["access"] == ids.ACCESS_READABLE


def test_refs_must_be_a_list(tmp_path):
    c = _world(tmp_path)
    assert c.post("/api/links/resolve", headers=_h("126"), json={"refs": "nope"}).status_code == 400


# ── the entity write, across mounts ──────────────────────────────────────────────────────────────

def test_the_entity_endpoint_writes_a_cross_workspace_link_in_id_form(tmp_path):
    c = _world(tmp_path)
    gid = c.get("/api/workspaces/by-slug/grp", headers=_h("126")).json()["id"]
    r = c.post("/api/workspace/entity", headers=_h("126"), json={
        "kind": "meeting", "name": "DNA TSC 2026-03-02",
        "facts": ["[[Cottalango Leon]] chaired it."], "source": "the transcript"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["links_rewritten"] == [["Cottalango Leon", f"[[ws:{gid}/cottalango-leon]]"]]
    page = (tmp_path / "126" / body["path"]).read_text()
    assert f"[[ws:{gid}/cottalango-leon]]" in page
    # and the written link resolves back, for this reader
    out = c.post("/api/links/resolve", headers=_h("126"),
                 json={"refs": [f"ws:{gid}/cottalango-leon"]}).json()["results"]
    assert out[0]["access"] == ids.ACCESS_READABLE


# ── the whole promise, end to end ────────────────────────────────────────────────────────────────

def test_a_link_written_before_a_rename_still_resolves_after_it(tmp_path):
    c = _world(tmp_path)
    gid = c.get("/api/workspaces/by-slug/grp", headers=_h("126")).json()["id"]
    ref = f"ws:{gid}/cottalango-leon"
    before = c.post("/api/links/resolve", headers=_h("126"), json={"refs": [ref]}).json()["results"][0]

    # rename the group in the registry — the act decision 26 exists to survive
    reg = c.app.state.workspace_registry if hasattr(c.app.state, "workspace_registry") else None
    assert reg is not None, "the registry must be reachable for an operator rename"
    ids.rename(reg, gid, "Digital Naming Authority")

    after = c.post("/api/links/resolve", headers=_h("126"), json={"refs": [ref]}).json()["results"][0]
    assert after["url"] == before["url"] and after["access"] == ids.ACCESS_READABLE
    assert c.get(f"/api/workspaces/{gid}", headers=_h("126")).json()["name"] == "Digital Naming Authority"
