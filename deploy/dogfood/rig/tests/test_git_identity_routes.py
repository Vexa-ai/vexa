"""agent-api's git-backed workspace routes are called as the caller.

A broker-backed git store acts only for the person the gateway signed for, so a person's own session
reaches those routes THROUGH THE GATEWAY with their own key, at the path the agent manifest's
`forward` maps. A delegated worker keeps the internal tier, which carries its regime and ceiling: it
never borrows the person's key, which would sign it as the person.
"""
from __future__ import annotations

import json

import pytest

import vexa_control_mcp as rig
from conftest import as_user, tool

KEY = {"/admin/users/7/tokens": (200, {"token": "vxa_person_key"})}
HOME = {"has_home": True, "remote": "origin", "url": "git@github.com:acme/kg.git",
        "branch": "main"}
#: Each git verb, its arguments, and the agent-api routes it reaches.
VERBS = [
    ("workspace_attach", {"repo": "https://github.com/acme/kg"}, ["/workspace/swap"]),
    ("workspace_attach", {"workspace": "acme", "repo": "https://github.com/acme/kg"},
     ["/workspace/shared/acme/attach"]),
    ("workspace_push", {"workspace": "acme"}, ["/workspace/push"]),
    ("workspace_pull", {"workspace": "acme"}, ["/workspace/git-remote-status", "/workspace/pull"]),
    ("workspace_import", {"repo": "https://github.com/acme/kg"}, ["/workspace/import"]),
    ("workspace_import_status", {"operation_id": "op/1"}, ["/workspace/import/op%2F1/status"]),
]


@pytest.fixture(autouse=True)
def _fresh_keys():
    rig._USER_KEYS.clear()
    rig.rig_secrets.write(rig.USER_KEYS_STORE, {})
    yield
    rig.CALL_SCOPE.set(None)


def _routes():
    return {**KEY, "/workspace/git-remote-status": (200, HOME),
            "/workspace/": (200, {"state": "cloned", "branch": "main", "url": "x"})}


@pytest.mark.parametrize("verb,kwargs,frags", VERBS)
def test_a_person_s_git_verb_goes_through_the_gateway_as_them(monkeypatch, verb, kwargs, frags):
    http = as_user(monkeypatch, "7", routes=_routes())
    json.loads(tool(verb)(**kwargs))
    edge, _ = rig._agent_forward()
    for frag in frags:
        calls = [c for c in http.calls if c["url"].endswith(frag) or frag + "?" in c["url"]]
        assert calls, (verb, frag, http.urls())
        for c in calls:
            assert c["url"].startswith(f"{rig.GATEWAY}{edge}"), c["url"]
            assert c["headers"].get("X-API-Key") == "vxa_person_key"
            assert "X-User-Id" not in c["headers"]
    assert not http.urls(rig.AGENT_API), "a person's git verb reached agent-api past the gateway"


#: An autonomous worker is refused attach and import before any call (`HUMAN_ONLY_VERBS`).
WORKER_CASES = ([("human",) + v for v in VERBS]
                + [("autonomous",) + v for v in VERBS if v[0] not in rig.HUMAN_ONLY_VERBS])


@pytest.mark.parametrize("regime,verb,kwargs,frags", WORKER_CASES)
def test_a_worker_never_borrows_the_person_s_key(monkeypatch, regime, verb, kwargs, frags):
    http = as_user(monkeypatch, "7", routes=_routes())
    rig.CALL_SCOPE.set({"regime": regime, "workspaces": "*"})
    tool(verb)(**kwargs)
    assert not http.urls("/tokens"), "a person's key was minted for a worker"
    assert not http.urls(rig.GATEWAY)
    for frag in frags:
        calls = [c for c in http.calls if frag in c["url"]]
        assert calls and all(c["url"].startswith(f"{rig.AGENT_API}/api/") for c in calls), frags
        assert all(c["headers"].get("X-User-Id") == "7" for c in calls)


def test_a_revoked_key_is_reminted_once_and_a_refusal_is_not(monkeypatch):
    http = as_user(monkeypatch, "7", routes={**KEY, "/workspace/push": (401, {"detail": "x"})})
    tool("workspace_push")(workspace="acme")
    assert len(http.urls("/tokens")) == 2 and len(http.urls("/workspace/push")) == 2
    rig._USER_KEYS.clear()
    rig.rig_secrets.write(rig.USER_KEYS_STORE, {})
    http = as_user(monkeypatch, "7", routes={**KEY, "/workspace/push": (403, {"detail": "x"})})
    tool("workspace_push")(workspace="acme")
    assert len(http.urls("/tokens")) == 1 and len(http.urls("/workspace/push")) == 1


def test_the_edge_path_is_the_manifest_s_forward(monkeypatch):
    http = as_user(monkeypatch, "7", routes=_routes())
    monkeypatch.setattr(rig, "_AGENT_FORWARD", ["/edge-x/", "/api/"])
    tool("workspace_push")(workspace="acme")
    assert http.urls("/workspace/push")[-1] == f"{rig.GATEWAY}/edge-x/workspace/push"
