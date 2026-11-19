"""A worker's tool-access token is never readable by another person through a file route.

A worker's working directory is usually a desk, a shared workspace or `_global`. It used to write its
vexa MCP attachment — the delegation token in an Authorization header — at `.claude/mcp.json` inside
that directory, and agent-api's file routes refused only `.git` and `.vexa`, so every member of a
shared workspace could read another member's live token.

Two people share `team`; person A's worker runs in it. Person B — a contributor, with every right a
member has — tries every file route: the token is in no file B can reach, and a path into `.claude`
is refused on reads, writes, moves and uploads alike (for A as well: `.claude` is plumbing, not a
page). A file an older worker left there stays unreadable.
"""
from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import workspace_membership as m
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared import delegation
from shared.config import load_settings

SECRET = "worker-token-test-secret"


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _git(work: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def world(tmp_path, monkeypatch):
    ws = tmp_path / "team"
    ws.mkdir()
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("team notes\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    idx = m.InMemoryMembershipIndex()
    m.ensure_owner(tmp_path, "team", "person_a", index=idx)
    m.grant_membership(tmp_path, "team", "person_b", "contributor", added_by="person_a", index=idx)
    client = TestClient(create_app(Dispatcher(load_settings(), _Runtime(), _Identity()),
                                   reader=WorkspaceReader(str(tmp_path)), membership_index=idx))
    # PERSON A'S WORKER, running in the shared workspace, attaches its toolbelt
    token = delegation.mint_delegation(SECRET, subject="person_a")
    monkeypatch.setenv("VEXA_MCP_URL", "https://gateway.example/mcp")
    monkeypatch.setenv("VEXA_MCP_DELEGATION_TOKEN", token)
    monkeypatch.delenv("VEXA_MOUNTS", raising=False)
    from worker.engine import mcp_delegation_config
    attached, _ = mcp_delegation_config(ws)
    assert attached and token in Path(attached).read_text()   # the worker has its toolbelt
    return client, ws, token


def _as(person: str) -> dict:
    return {"X-User-Id": person}


def test_the_token_is_in_no_file_another_member_can_read(world):
    client, ws, token = world
    # B can read the workspace at all — the routes below are refusing a path, not B
    assert client.get("/api/workspace/file", params={"slug": "team", "path": "README.md"},
                      headers=_as("person_b")).status_code == 200
    for hidden in (False, True):
        tree = client.get("/api/workspace/tree", params={"slug": "team", "hidden": hidden},
                          headers=_as("person_b"))
        assert tree.status_code == 200
        files = tree.json()["files"]
        assert not any(f.split("/")[0] == ".claude" for f in files), files
        for f in files:
            r = client.get("/api/workspace/file", params={"slug": "team", "path": f},
                           headers=_as("person_b"))
            assert token not in r.text, f
    r = client.get("/api/workspace/file", params={"slug": "team", "path": ".claude/mcp.json"},
                   headers=_as("person_b"))
    assert r.status_code == 400 and token not in r.text


@pytest.mark.parametrize("person", ["person_b", "person_a"])
def test_a_path_into_claude_is_refused_on_every_file_route(world, person):
    client, ws, token = world
    # what an older worker left behind is still never served
    (ws / ".claude").mkdir(exist_ok=True)
    (ws / ".claude" / "mcp.json").write_text(json.dumps({"Authorization": f"Bearer {token}"}))
    for path in (".claude/mcp.json", "./.claude/mcp.json", "notes/../.claude/mcp.json", ".claude"):
        r = client.get("/api/workspace/file", params={"slug": "team", "path": path}, headers=_as(person))
        assert r.status_code in (400, 404) and token not in r.text, (path, r.status_code)
    r = client.put("/api/workspace/file", headers=_as(person),
                   json={"slug": "team", "path": ".claude/mcp.json", "content": "{}"})
    assert r.status_code == 400, r.text
    r = client.post("/api/workspace/move", headers=_as(person),
                    json={"slug": "team", "path": ".claude/mcp.json", "to": "stolen.md"})
    assert r.status_code == 400, r.text
    assert not (ws / "stolen.md").exists()
    r = client.post("/api/workspace/move", headers=_as(person),
                    json={"slug": "team", "path": "README.md", "to": ".claude/README.md"})
    assert r.status_code == 400, r.text
    r = client.put("/api/workspace/asset", headers=_as(person),
                   data={"slug": "team", "path": ".claude/x.png"},
                   files={"file": ("x.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 16), "image/png")})
    assert r.status_code == 400, r.text
    assert json.loads((ws / ".claude" / "mcp.json").read_text())   # untouched by any of them
