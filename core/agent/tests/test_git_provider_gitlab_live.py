"""Against a REAL self-hosted GitLab: the git provider port end to end, through agent-api's routes.

Skipped unless a GitLab is named — it is not part of the ordinary suite:

    VEXA_GITLAB_LIVE_BASE=https://gitlab.vexa.test:18443   the GitLab's external URL
    VEXA_GITLAB_LIVE_CA=/path/ca.crt                       the private CA its certificate is issued by
    VEXA_GITLAB_LIVE_TOKEN_FILE=/path/token                a token (api + write_repository) of a user
                                                           with Maintainer on VEXA_GITLAB_LIVE_GROUP
    VEXA_GITLAB_LIVE_GROUP=it/ai/vexa                      a nested group

What is real: GitLab (its API, its git over https, its certificate from a private CA), git, agent-api's
routes and the provider adapter. What is a double: the credential broker (an in-memory store behind
``git_secret_store``'s two calls) and the gateway (the routes are called directly).
"""
from __future__ import annotations

import json
import os
import ssl
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

BASE = os.environ.get("VEXA_GITLAB_LIVE_BASE", "")
CA = os.environ.get("VEXA_GITLAB_LIVE_CA", "")
TOKEN_FILE = os.environ.get("VEXA_GITLAB_LIVE_TOKEN_FILE", "")
GROUP = os.environ.get("VEXA_GITLAB_LIVE_GROUP", "it/ai/vexa")

pytestmark = pytest.mark.skipif(not (BASE and CA and TOKEN_FILE), reason="no live GitLab named")


@pytest.fixture
def live(monkeypatch, tmp_path):
    from control_plane import git_secret_store
    from shared import git_provider
    pem = Path(CA).read_text()
    monkeypatch.setenv(git_provider.PROVIDERS_ENV, json.dumps(
        [{"name": "corp", "kind": "gitlab", "baseUrl": BASE, "label": "Corp GitLab", "caBundle": pem}]))
    store: dict = {}
    monkeypatch.setattr(git_secret_store, "get", lambda root, name: store.get(name))

    def put(root, name, value):
        value = (value or "").strip() or None
        if value is None:
            store.pop(name, None)
        else:
            store[name] = value
        return value is not None
    monkeypatch.setattr(git_secret_store, "put", put)
    from tests.test_workspace_manage_routes import _client, _seed_primary
    ws = _seed_primary(tmp_path, "u_alpha")
    return {"client": _client(tmp_path), "ws": ws, "store": store, "token": Path(TOKEN_FILE).read_text().strip(),
            "h": {"X-User-Id": "u_alpha"}, "tmp": tmp_path}


def _api(path: str, token: str) -> dict:
    ctx = ssl.create_default_context(cafile=CA)
    req = urllib.request.Request(f"{BASE}/api/v4{path}", headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
        return json.loads(r.read())


def _git(*a, cwd=None, env=None) -> str:
    return subprocess.run(["git", *a], cwd=cwd, env={**os.environ, **(env or {})}, check=True,
                          capture_output=True, text=True).stdout.strip()


def test_connect_publish_push_pull_and_link_on_a_nested_group(live):
    c, h, tok = live["client"], live["h"], live["token"]
    # connect: the person saves a token for the GitLab provider (Connections → Git)
    r = c.post("/api/workspace/git-token", headers=h, json={"provider": "corp", "token": tok})
    assert r.status_code == 200 and r.json()["set"] and r.json()["provider"] == "corp"
    assert live["store"] == {"pat/u_alpha/corp": tok}, "one store, one name per provider"
    r = c.get("/api/workspace/git-providers", headers=h)
    corp = next(p for p in r.json()["providers"] if p["name"] == "corp")
    assert corp["token_saved"] and corp["nested_groups"] and tok not in json.dumps(r.json())

    # publish — what the workspace_publish tool sends (no token argument): create under the nested group
    name = f"ws-{int(time.time())}"
    r = c.post("/api/workspace/publish", headers=h,
               json={"repo_name": name, "provider": "corp", "org": GROUP, "private": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "corp" and body["created"]
    assert body["repo_url"] == f"{BASE}/{GROUP}/{name}"
    assert tok not in r.text

    # the web link is the project's own: GitLab says so
    proj = _api("/projects/" + urllib.request.quote(f"{GROUP}/{name}", safe=""), tok)
    assert proj["web_url"] == body["repo_url"] and proj["namespace"]["full_path"] == GROUP
    assert proj["visibility"] == "private"

    # pull back: a fresh clone over https with the private CA carries the published HEAD
    from shared.gitenv import transport_env
    from shared.token_destination import embed_token
    clone = live["tmp"] / "clone"
    url = body["repo_url"] + ".git"
    _git("clone", "-q", embed_token(url, tok), str(clone), env=transport_env(url))
    assert _git("rev-parse", "HEAD", cwd=clone) == body["head_sha"]
    assert (clone / "README.md").read_text() == "v0\n"

    # push a new commit through the workspace_push route (saved token for corp, chosen by the URL)
    ws = live["ws"]
    (ws / "note.md").write_text("from vexa\n")
    _git("add", "note.md", cwd=ws)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "note", cwd=ws)
    r = c.post("/api/workspace/push", headers=h, json={})
    assert r.status_code == 200, r.text
    _git("pull", "-q", "--ff-only", cwd=clone, env=transport_env(url))
    assert (clone / "note.md").read_text() == "from vexa\n"
    # the workspace's remote never keeps the token
    assert tok not in _git("config", "--get-regexp", "remote", cwd=ws)


def test_a_bad_token_is_a_typed_refusal_naming_the_provider(live):
    c, h = live["client"], live["h"]
    bad = "glpat-" + "x" * 20
    r = c.post("/api/workspace/publish", headers=h,
               json={"repo_name": "never", "provider": "corp", "org": GROUP, "token": bad})
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["code"] == "token_rejected" and d["provider"] == "corp"
    assert "Corp GitLab" in d["message"] and bad not in r.text


def test_an_unknown_group_is_a_clear_refusal(live):
    c, h, tok = live["client"], live["h"], live["token"]
    r = c.post("/api/workspace/publish", headers=h,
               json={"repo_name": "never", "provider": "corp", "org": "it/nope/none", "token": tok})
    assert r.status_code == 502 and "was not found" in r.json()["detail"] and tok not in r.text


def test_without_the_private_ca_the_host_is_not_trusted(live, monkeypatch):
    from shared import git_provider
    monkeypatch.setenv(git_provider.PROVIDERS_ENV, json.dumps(
        [{"name": "corp", "kind": "gitlab", "baseUrl": BASE, "label": "Corp GitLab"}]))
    c, h, tok = live["client"], live["h"], live["token"]
    r = c.post("/api/workspace/publish", headers=h,
               json={"repo_name": "never", "provider": "corp", "org": GROUP, "token": tok})
    assert r.status_code == 502 and "unreachable" in r.json()["detail"] and tok not in r.text
