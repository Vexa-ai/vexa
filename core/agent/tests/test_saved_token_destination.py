"""A saved GitHub token goes only to GitHub, over HTTPS.

The token a person saves in the terminal's token card is a GitHub credential. It is attached to a git
operation only when the repository URL is `https://github.com/…`: never to another host, never over
`http://`, and never to a look-alike host or port. A token typed for one call in the terminal may go
to another host, but only over `https`.

Offline: fakes and tmp dirs, no network, no git server.
"""
from __future__ import annotations

import pytest

from control_plane import git_credentials, workspace_credentials as wcreds
from shared.token_destination import embed_token

SAVED = "ghp_" + "S" * 36
TYPED = "ghp_" + "T" * 36


@pytest.fixture
def saved_token(monkeypatch):
    monkeypatch.setattr(git_credentials, "read_github_token", lambda root, subject: SAVED)


def _token(tmp_path, url, explicit=None):
    with wcreds.for_workspace(tmp_path, key="k", repo_url=url, subject="u1",
                              explicit_token=explicit) as cred:
        return cred.token


@pytest.mark.parametrize("url", [
    "https://github.com/acme/kg",
    "https://github.com/acme/kg.git",
    "https://GitHub.com/acme/kg",
    "https://github.com:443/acme/kg",
])
def test_the_saved_token_goes_to_github_over_https(tmp_path, saved_token, url):
    assert _token(tmp_path, url) == SAVED


@pytest.mark.parametrize("url", [
    "http://github.com/acme/kg",                 # cleartext
    "https://gitlab.example.com/acme/kg",        # another host
    "https://github.com.example.net/acme/kg",    # a look-alike host
    "https://evil.example/github.com/acme/kg",   # github.com only in the path
    "https://github.com:8443/acme/kg",           # another port
    "https://github.com/",                       # no repository
    "",                                          # no URL at all
])
def test_the_saved_token_goes_nowhere_else(tmp_path, saved_token, url):
    assert _token(tmp_path, url) is None


def test_a_typed_token_is_used_for_its_call_and_the_saved_one_is_not(tmp_path, saved_token):
    assert _token(tmp_path, "https://gitlab.example.com/acme/kg", explicit=TYPED) == TYPED
    assert _token(tmp_path, "https://github.com/acme/kg", explicit=TYPED) == TYPED


def test_a_pull_carries_a_credential_only_over_https(monkeypatch, tmp_path):
    from control_plane import workspace_git_sync as sync

    seen: list[tuple] = []

    class _Proc:
        returncode, stdout, stderr = 1, "", "stop here"

    monkeypatch.setattr(sync, "home_remote", lambda wsp: ("origin", "http://git.example/acme/kg"))
    monkeypatch.setattr(sync, "_current_branch", lambda wsp: "main")
    monkeypatch.setattr(sync, "_git", lambda wsp, *args, **kw: seen.append(args) or _Proc())
    with pytest.raises(sync.RemoteSyncError):
        sync.pull_origin(tmp_path, token=TYPED)
    assert seen and all(TYPED not in " ".join(a) for a in seen)


def test_a_clone_url_carries_a_credential_only_over_https():
    assert embed_token("https://github.com/acme/kg", TYPED) == f"https://{TYPED}@github.com/acme/kg"
    assert embed_token("http://github.com/acme/kg", TYPED) == "http://github.com/acme/kg"
    assert embed_token("git@github.com:acme/kg.git", TYPED) == "git@github.com:acme/kg.git"


def test_the_capability_line_reports_a_saved_token_only_where_it_would_be_used(tmp_path, saved_token):
    assert wcreds.home_capability(tmp_path, key="k", remote="origin",
                                  url="https://github.com/acme/kg", subject="u1").endswith("saved token")
    assert wcreds.home_capability(tmp_path, key="k", remote="origin",
                                  url="https://gitlab.example.com/acme/kg",
                                  subject="u1").endswith("no credential yet")


def test_a_push_carries_a_credential_only_over_https(monkeypatch, tmp_path):
    from shared import adapters

    seen: list[tuple] = []
    monkeypatch.setattr(adapters, "_git", lambda work, *args, **kw: seen.append(args) or "sha")
    adapters.push_with_token(tmp_path, "http://git.example/acme/kg", "main", TYPED)
    assert all(TYPED not in " ".join(a) for a in seen)
    seen.clear()
    adapters.push_with_token(tmp_path, "https://github.com/acme/kg", "main", TYPED)
    assert any(f"https://{TYPED}@github.com/acme/kg" in a for a in seen)


def test_publish_sends_the_saved_token_only_to_github(tmp_path, monkeypatch, saved_token):
    from tests.test_workspace_manage_routes import _client, _seed_primary
    from control_plane.routers import workspaces as ws_router

    _seed_primary(tmp_path, "u_jane")
    used: list = []

    def fake_publish(root, subject, **kw):
        used.append((kw.get("remote_url"), kw.get("token")))
        raise ValueError("stop here")

    monkeypatch.setattr(ws_router, "publish_workspace", fake_publish)
    client = _client(tmp_path)
    h = {"X-User-Id": "u_jane"}

    r = client.post("/api/workspace/publish", headers=h,
                    json={"repo_name": "kg", "remote_url": "https://git.example.net/acme/kg"})
    assert r.status_code == 400 and not used, "the saved token must not reach another host"

    r = client.post("/api/workspace/publish", headers=h,
                    json={"repo_name": "kg", "remote_url": "http://github.com/acme/kg"})
    assert r.status_code == 400 and not used

    client.post("/api/workspace/publish", headers=h,
                json={"repo_name": "kg", "remote_url": "https://github.com/acme/kg"})
    assert used == [("https://github.com/acme/kg", SAVED)]
