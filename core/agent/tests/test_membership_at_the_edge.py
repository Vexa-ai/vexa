"""The membership verbs and the company-layer accept, served by the one MCP.

`behavior/asks/member-add.md`, `member-role.md` and `member-remove.md` are the three acts the
workspace front page queues on a chat (Vexa-ai/vexa#1632): they call `workspace_invite` and
`workspace_membership`, which agent-api has served as typed routes since they shipped — and nothing
put them in the agent manifest, so on a standard deployment the chat the button opened could read a
roster and change nothing. `behavior/asks/setup-global.md` ends on `mark_global_ready`, the
company-layer accept agent-api serves as `POST /api/global/ready`.

What has to be true:

* the manifest serves `workspace_invite`, `workspace_membership`, the roster they are answered
  against (`workspace_members`) and `mark_global_ready`, each bound to its agent-api route with the
  arguments the prompts pass;
* the accept takes a NAMED body, and with nothing in it the commit is authored by the person whose
  identity made the call — the agent never has to know, or be told, the admin's address.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import global_layer
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

AGENT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((AGENT / "mcp.tools.v1.json").read_text())
ADMIN = "u_admin"

VERBS = {
    "workspace_invite": ("POST", "/api/workspace/invite", ["slug", "email", "role"]),
    "workspace_membership": ("POST", "/api/workspace/membership", ["slug", "email", "role"]),
    "workspace_members": ("GET", "/api/workspace/members", ["workspace_id"]),
    "mark_global_ready": ("POST", "/api/global/ready", []),
}


def _client(root: Path) -> TestClient:
    settings = load_settings(workspaces_dir=str(root),
                             global_system_workspace_path=str(root / "_global"),
                             global_admin_subjects=ADMIN, internal_api_secret="s", redis_url="")
    return TestClient(create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                                 reader=WorkspaceReader(str(root))))


@pytest.mark.parametrize("name", sorted(VERBS))
def test_the_manifest_serves_the_verb_under_its_prompt_name(name):
    method, path, args = VERBS[name]
    tool = next((t for t in MANIFEST["tools"] if t["name"] == name), None)
    assert tool is not None, f"{name} is named by the behaviour prompts and not served"
    assert tool["route"] == {"method": method, "path": path}
    assert list(tool.get("arguments") or []) == args


def test_the_accept_takes_a_named_body(tmp_path):
    spec = _client(tmp_path).app.openapi()
    schema = spec["paths"]["/api/global/ready"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    body = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    assert {"author_email", "author_name"} <= set(body["properties"])


def test_the_accept_is_authored_by_the_person_who_called_it(tmp_path):
    g = tmp_path / "_global"
    g.mkdir()
    global_layer.ensure_repo(g)          # the repo exists before its first writer, as deployed
    for name in global_layer.LAYER_FILES:
        (g / name).write_text("# x\n\nsomething.\n")
    (g / "README.md").write_text("# Acme GmbH\n\nAcme GmbH sells widgets.\n")
    r = _client(tmp_path).post("/api/global/ready", json={},
                               headers={"X-User-Id": ADMIN, "x-user-email": "ada@acme.example"})
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] is True
    author = subprocess.run(["git", "-C", str(g), "log", "-1", "--format=%ae|%an"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert author == "ada@acme.example|ada"
