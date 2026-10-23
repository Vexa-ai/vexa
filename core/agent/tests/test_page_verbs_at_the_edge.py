"""THE PAGE VERBS AT THE ONE MCP — `workspace_write`, `workspace_delete`, `workspace_move`,
`entity_upsert` and `fetch_asset` as agent-api routes the gateway's MCP binds (ADR-0037 §2).

The behaviour prompts name these five verbs on every turn that writes knowledge. Each one is a route
agent-api already serves; what a standard deployment lacked was the binding: three of them took a
bare `body: dict`, which publishes no properties for the assembler to derive arguments from, and the
other two were never put in the manifest. And an omitted `slug` on the rig meant "where this
conversation is working" while on agent-api it meant the person's desk — so the same prompt landed a
page in two different places depending on which server answered it.

What has to be true:

* every one of the five routes publishes a NAMED body, so `bind.py` can derive its arguments;
* the agent manifest serves each under the name the prompts use, bound to that route;
* an omitted `slug` on a write lands in the chat's target workspace, carried on the signed identity
  as `x-user-delegation-target`; `slug="personal"` (or `"desk"`) names the person's own desk; and a
  caller with no target — a person's own client, the terminal — writes where it always did.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared import asset_source as assets
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

AGENT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((AGENT / "mcp.tools.v1.json").read_text())
JANE = "u_jane"
SHARED = "bank-c1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

#: name -> (method, path, the arguments the prompts pass)
PAGE_VERBS = {
    "workspace_write": ("PUT", "/api/workspace/file", {"path", "content", "slug"}),
    "workspace_delete": ("POST", "/api/workspace/remove", {"path", "slug"}),
    "workspace_move": ("POST", "/api/workspace/move", {"path", "to", "slug", "to_slug"}),
    "entity_upsert": ("POST", "/api/workspace/entity",
                      {"kind", "name", "facts", "source", "slug", "dates", "summary", "fields",
                       "section", "connections", "open_questions"}),
    "fetch_asset": ("POST", "/api/workspace/asset", {"url", "path", "slug"}),
}


def _git(work: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _init_ws(root: Path, slug: str) -> Path:
    ws = root / slug
    ws.mkdir(parents=True, exist_ok=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    return ws


def _world(root: Path):
    """Jane's desk and a shared workspace she writes, and the app over both."""
    desk = _init_ws(root, JANE)
    idx = m.InMemoryMembershipIndex()
    shared = _init_ws(root, SHARED)
    m.ensure_owner(root, SHARED, JANE, index=idx)
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret="s", ui_url="https://app.example.test", redis_url="")
    client = TestClient(create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                                   reader=WorkspaceReader(str(root)), membership_index=idx))
    return client, desk, shared


def _as_worker(target: str = "") -> dict:
    """A worker's identity as agent-api receives it: the subject, and the chat's target."""
    h = {"X-User-Id": JANE}
    if target:
        h["x-user-delegation-target"] = target
    return h


# ── 1. every page verb publishes a named body ────────────────────────────────────────────────────

def _body_schema(spec: dict, method: str, path: str) -> dict:
    op = spec["paths"][path][method.lower()]
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    ref = schema.get("$ref", "")
    return spec["components"]["schemas"][ref.rsplit("/", 1)[-1]] if ref else schema


@pytest.mark.parametrize("name", sorted(PAGE_VERBS))
def test_every_page_verb_publishes_a_named_body(tmp_path, name):
    method, path, args = PAGE_VERBS[name]
    client, _, _ = _world(tmp_path)
    schema = _body_schema(client.app.openapi(), method, path)
    assert args <= set(schema.get("properties") or {}), (
        f"{method} {path} publishes {sorted(schema.get('properties') or {})}; the prompts pass "
        f"{sorted(args)}")
    assert schema.get("additionalProperties") is not True


# ── 2. the manifest serves them under the names the prompts use ──────────────────────────────────

@pytest.mark.parametrize("name", sorted(PAGE_VERBS))
def test_the_manifest_serves_the_page_verb_under_its_prompt_name(name):
    method, path, args = PAGE_VERBS[name]
    tool = next((t for t in MANIFEST["tools"] if t["name"] == name), None)
    assert tool is not None, f"{name} is named by the prompts and not served by the agent manifest"
    assert tool["route"] == {"method": method, "path": path}
    assert set(tool.get("arguments") or ()) == args
    assert tool["auth"] == "subject" and tool["identity"] == "user"


# ── 3. an omitted slug is the chat's target; `personal` is the desk ──────────────────────────────

def test_a_write_with_no_slug_lands_in_the_chats_target(tmp_path):
    client, desk, shared = _world(tmp_path)
    r = client.put("/api/workspace/file", headers=_as_worker(SHARED),
                   json={"path": "notes/plan.md", "content": "# Plan\n"})
    assert r.status_code == 200, r.text
    assert (shared / "notes/plan.md").read_text() == "# Plan\n"
    assert not (desk / "notes/plan.md").exists()


def test_personal_names_the_desk_whatever_the_target(tmp_path):
    client, desk, shared = _world(tmp_path)
    for word in ("personal", "desk"):
        r = client.put("/api/workspace/file", headers=_as_worker(SHARED),
                       json={"path": f"notes/{word}.md", "content": "mine\n", "slug": word})
        assert r.status_code == 200, r.text
        assert (desk / f"notes/{word}.md").is_file()
        assert not (shared / f"notes/{word}.md").exists()


def test_a_caller_with_no_target_writes_where_it_always_did(tmp_path):
    client, desk, shared = _world(tmp_path)
    r = client.put("/api/workspace/file", headers=_as_worker(),
                   json={"path": "notes/plan.md", "content": "# Plan\n"})
    assert r.status_code == 200, r.text
    assert (desk / "notes/plan.md").is_file()
    assert not (shared / "notes/plan.md").exists()


def test_an_entity_with_no_slug_is_filed_in_the_chats_target(tmp_path):
    client, desk, shared = _world(tmp_path)
    r = client.post("/api/workspace/entity", headers=_as_worker(SHARED), json={
        "kind": "company", "name": "Acme", "facts": ["Acme builds rockets."],
        "source": "the 2026-10-09 call"})
    assert r.status_code == 200, r.text
    assert (shared / "kg/entities/company/acme.md").is_file()
    assert not (desk / "kg/entities/company/acme.md").exists()


def test_a_single_fact_given_as_a_string_is_still_one_fact(tmp_path):
    client, _, shared = _world(tmp_path)
    r = client.post("/api/workspace/entity", headers=_as_worker(SHARED), json={
        "kind": "person", "name": "Ada Lovelace", "facts": "Ada chairs the TSC.",
        "source": "the 2026-10-09 call"})
    assert r.status_code == 200, r.text
    assert "Ada chairs the TSC." in (shared / "kg/entities/person/ada-lovelace.md").read_text()


def test_a_removal_with_no_slug_removes_from_the_chats_target(tmp_path):
    client, desk, shared = _world(tmp_path)
    for ws in (desk, shared):
        (ws / "notes").mkdir(exist_ok=True)
        (ws / "notes/plan.md").write_text("x\n")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-q", "-m", "plan")
    r = client.post("/api/workspace/remove", headers=_as_worker(SHARED),
                    json={"path": "notes/plan.md"})
    assert r.status_code == 200, r.text
    assert not (shared / "notes/plan.md").exists()
    assert (desk / "notes/plan.md").is_file()


def test_a_move_names_its_source_path_like_every_other_page_verb(tmp_path):
    client, _, shared = _world(tmp_path)
    (shared / "notes").mkdir()
    (shared / "notes/plan.md").write_text("x\n")
    _git(shared, "add", "-A")
    _git(shared, "commit", "-q", "-m", "plan")
    r = client.post("/api/workspace/move", headers=_as_worker(SHARED),
                    json={"path": "notes/plan.md", "to": "notes/2026-plan.md"})
    assert r.status_code == 200, r.text
    assert (shared / "notes/2026-plan.md").is_file()


def test_a_move_to_personal_takes_the_page_to_the_desk(tmp_path):
    client, desk, shared = _world(tmp_path)
    (shared / "notes").mkdir()
    (shared / "notes/plan.md").write_text("x\n")
    _git(shared, "add", "-A")
    _git(shared, "commit", "-q", "-m", "plan")
    r = client.post("/api/workspace/move", headers=_as_worker(SHARED),
                    json={"path": "notes/plan.md", "to": "notes/plan.md", "to_slug": "personal"})
    assert r.status_code == 200, r.text
    assert (desk / "notes/plan.md").is_file()
    assert not (shared / "notes/plan.md").exists()


def test_a_fetched_picture_lands_beside_the_pages_of_the_chats_target(tmp_path, monkeypatch):
    """A page references its picture RELATIVELY, so the picture has to be in the workspace the page
    is written to — and with no slug that is now the chat's target."""
    monkeypatch.setattr(assets, "fetch_asset",
                        lambda url, **_kw: (PNG, "image/png", url))
    client, desk, shared = _world(tmp_path)
    r = client.post("/api/workspace/asset", headers=_as_worker(SHARED),
                    json={"url": "https://x.example/logo.png"})
    assert r.status_code == 200, r.text
    assert (shared / r.json()["path"]).read_bytes() == PNG
    assert not (desk / "assets").exists()


def test_entity_upsert_states_the_cards_sections_and_the_connection_shape(tmp_path):
    """The agent reads the shape off the argument descriptions, so they are the WRITER's own words —
    generated from `entities.py` — never a second copy that can name a section the card lacks."""
    from workspaces.shared import entities as entities_mod

    client, _, _ = _world(tmp_path)
    props = _body_schema(client.app.openapi(), "POST", "/api/workspace/entity")["properties"]
    assert entities_mod.tool_sections_text() in props["fields"]["description"]
    assert entities_mod.tool_connection_text() in props["connections"]["description"]

