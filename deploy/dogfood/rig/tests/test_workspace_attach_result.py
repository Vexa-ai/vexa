"""What `workspace_attach` answers is what agent-api's attach routes answer. A clone is used as it
is and agent-api no longer reports a `nested` field, so the rig must not invent one."""
from __future__ import annotations

import json

import pytest

from conftest import as_user, tool

LOADED = {"repo": "https://github.com/acme/kg", "ref": "main", "state": "cloned", "cloned": True,
          "parked": "personal-1"}


KEY = {"/admin/users/7/tokens": (200, {"token": "vxa_person_key"})}


@pytest.mark.parametrize("workspace,route", [("", "/workspace/swap"),
                                             ("acme", "/workspace/shared/acme/attach")])
def test_the_attach_result_carries_no_nested_field(monkeypatch, workspace, route):
    http = as_user(monkeypatch, "7", routes={**KEY, route: (200, dict(LOADED))})
    out = json.loads(tool("workspace_attach")(workspace=workspace, repo=LOADED["repo"]))
    assert http.urls(route), out
    assert out["state"] == "cloned" and out["parked"] == "personal-1"
    assert "nested" not in out


def test_the_tool_no_longer_promises_to_nest_a_repository():
    assert "nested" not in (tool("workspace_attach").__doc__ or "")


@pytest.mark.parametrize("verb,kwargs", [("workspace_attach", {"repo": LOADED["repo"]}),
                                          ("workspace_push", {"workspace": "acme"}),
                                          ("workspace_pull", {"workspace": "acme"})])
def test_the_workspace_git_verbs_run(monkeypatch, verb, kwargs):
    """Each one used to name an argument the tools no longer take and raised before doing anything."""
    as_user(monkeypatch, "7", routes={**KEY, "/workspace/": (200, dict(LOADED))})
    assert isinstance(json.loads(tool(verb)(**kwargs)), dict)
