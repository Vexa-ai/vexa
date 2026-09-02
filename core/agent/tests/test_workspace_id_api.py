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
    assert r.status_code == 200 and r.json() == {"id": "zzzzzzzzzz", "name": None,
                                                 "kind": None, "access": ids.ACCESS_GONE}


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

    assert by_ref[f"ws:{did127}/whoever"]["access"] == ids.ACCESS_NOT_YOURS
    assert by_ref[f"ws:{did127}/whoever"]["url"] is None
    assert by_ref["ws:zzzzzzzzzz/gone"]["access"] == ids.ACCESS_GONE

    mine = by_ref["Olga Avramenko"]
    assert mine["access"] == ids.ACCESS_READABLE and mine["title"] == "Olga Avramenko"
    assert mine["url"].endswith("/kg/entities/person/olga-avramenko.md")


def test_naming_somebody_elses_workspace_as_here_does_not_open_it(tmp_path):
    """The in-workspace form is resolved in the READER's workspace; a slug they may not read
    resolves to nothing rather than to that workspace's pages."""
    c = _world(tmp_path)
    out = c.post("/api/links/resolve", headers=_h("127"),
                 json={"refs": ["Olga Avramenko"], "slug": "126"}).json()["results"]
    assert out[0]["access"] == ids.ACCESS_GONE and out[0]["url"] is None


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
