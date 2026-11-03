"""Git remotes on a self-hosted GitLab: nested group paths, token conventions, provider-aware create.

Three properties, each with its deny side:

* a repository path may be ``group/sub/…/repo`` on any host but ``github.com`` — and still never
  carries an empty segment, ``.``/``..``, GitLab's ``-`` separator or an odd character;
* a token is presented the way the host's provider expects (``oauth2:<token>`` for a GitLab host the
  operator registered, ``<user>:<token>`` when the token names its account, the bare token for GitHub
  and every unlisted host) and never appears in an error;
* publish creates a repository only on GitHub or a registered GitLab host, and refuses any other host
  by name before a request is made or a credential is read.

Offline: fakes, tmp dirs and local bare repos. The live proof against a real GitLab is in the PR.
"""
from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest
from pydantic import ValidationError

from control_plane import deploy_keys, repo_ref, workspace_attach, workspace_publish as pub
from shared import adapters, git_provider
from shared.config import load_settings
from shared.token_destination import embed_token

GL = "gitlab.example.com"
PAT = "glpat-" + "A1b2C3d4E5f6G7h8I9j0"


@pytest.fixture
def gitlab_host(monkeypatch):
    monkeypatch.setenv(git_provider.PROVIDERS_ENV, f"{GL}=gitlab,git.example.org=basic:svc-vexa")


# ── nested group paths ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (f"https://{GL}/group/sub/repo", f"https://{GL}/group/sub/repo.git"),
    (f"https://{GL}/group/sub/deeper/repo.git", f"https://{GL}/group/sub/deeper/repo.git"),
    (f"https://{GL}:8443/group/sub/repo/", f"https://{GL}:8443/group/sub/repo.git"),
    (f"git@{GL}:group/sub/repo.git", f"git@{GL}:group/sub/repo.git"),
    (f"ssh://git@{GL}:2222/group/sub/repo", f"ssh://git@{GL}:2222/group/sub/repo.git"),
    ("https://github.com/acme/kg", "https://github.com/acme/kg.git"),   # GitHub unchanged
])
def test_a_nested_group_path_normalizes(raw, expected):
    assert repo_ref.normalize(raw) == expected


def test_the_deepest_gitlab_path_is_accepted_and_one_deeper_is_not():
    deepest = "/".join(["g"] * (repo_ref.MAX_PATH_SEGMENTS - 1) + ["repo"])
    assert repo_ref.normalize(f"https://{GL}/{deepest}") == f"https://{GL}/{deepest}.git"
    with pytest.raises(repo_ref.RepoRefError) as e:
        repo_ref.normalize(f"https://{GL}/g/{deepest}")
    assert e.value.kind == "shape"


@pytest.mark.parametrize("bad", [
    f"https://{GL}/group/../repo",          # climbs
    f"https://{GL}/../group/repo",
    f"https://{GL}/../repo",                # two segments, one of them a climb
    f"git@{GL}:../repo.git",
    f"https://{GL}/group/./repo",
    f"https://{GL}/group//repo",            # empty segment
    f"https://{GL}/group/sub/.git",         # a name that is only the suffix
    f"https://{GL}/group/repo/-/tree/main",  # a GitLab page, not a repository
    f"https://{GL}/group/re po",
    f"https://{GL}/group/repo%2e%2e",
    f"https://{GL}/group/repo?ref=main",
    f"https://{GL}/group/repo#readme",
    f"git@{GL}:group/../repo.git",
    f"ssh://git@{GL}/group/../../repo",
    f"https://{GL}/repo",                   # one segment
    "https://github.com/a/b/c",             # github.com has exactly owner/repo
    "https://github.com/acme/",             # no repository name
])
def test_a_path_that_is_not_a_plain_nested_name_is_refused(bad):
    with pytest.raises(repo_ref.RepoRefError) as e:
        repo_ref.normalize(bad)
    assert e.value.kind == "shape"


def test_a_nested_path_still_passes_the_host_gate():
    with pytest.raises(repo_ref.RepoRefError) as e:
        repo_ref.normalize("http://169.254.169.254/group/sub/repo")
    assert e.value.kind == "host"
    with pytest.raises(repo_ref.RepoRefError) as e:
        repo_ref.normalize("http://admin-api:8001/group/sub/repo")
    assert e.value.kind == "host"


def test_a_nested_path_names_its_workspace_after_the_repository():
    assert workspace_attach._slug(f"https://{GL}/group/sub/repo.git").startswith("repo-")


# ── the operator's provider map ───────────────────────────────────────────────────────────────────

def test_the_provider_map_parses():
    got = git_provider.parse_providers(f" {GL}=gitlab , git.example.org=basic:svc-vexa,ghe.example.net=github ")
    assert got == {GL: git_provider.Provider("gitlab"),
                   "git.example.org": git_provider.Provider("basic", "svc-vexa"),
                   "ghe.example.net": git_provider.Provider("github")}
    assert git_provider.parse_providers("") == {}


@pytest.mark.parametrize("bad", [
    "gitlab.example.com",                  # no provider
    "gitlab.example.com=bitbucket",        # unknown provider
    "gitlab=gitlab",                       # an undotted (deployment-internal) name
    "gitlab.example.com=basic",            # basic needs a username
    "gitlab.example.com=basic:a b",
    "gitlab.example.com=gitlab:someone",   # only basic takes a username
    "gitlab.example.com=gitlab,gitlab.example.com=github",
])
def test_a_malformed_provider_map_is_refused_by_name(bad):
    with pytest.raises(ValueError) as e:
        git_provider.parse_providers(bad)
    assert git_provider.PROVIDERS_ENV in str(e.value)


def test_a_malformed_provider_map_stops_the_boot():
    with pytest.raises(ValidationError):
        load_settings(git_providers="gitlab.example.com=bitbucket")
    assert load_settings(git_providers=f"{GL}=gitlab").git_providers == f"{GL}=gitlab"


# ── how a token is presented ──────────────────────────────────────────────────────────────────────

def test_a_gitlab_host_gets_oauth2_and_github_keeps_the_bare_token(gitlab_host):
    assert embed_token(f"https://{GL}/group/sub/repo.git", PAT) == f"https://oauth2:{PAT}@{GL}/group/sub/repo.git"
    assert embed_token("https://github.com/acme/kg.git", "ghp_x") == "https://ghp_x@github.com/acme/kg.git"
    assert embed_token("https://git.example.org/a/b.git", "tok") == "https://svc-vexa:tok@git.example.org/a/b.git"
    # an unlisted host behaves exactly as before the map existed
    assert embed_token("https://other.example.net/a/b.git", "tok") == "https://tok@other.example.net/a/b.git"


def test_a_token_that_names_its_account_is_used_as_written(gitlab_host):
    assert embed_token(f"https://{GL}/g/r.git", f"oauth2:{PAT}") == f"https://oauth2:{PAT}@{GL}/g/r.git"
    assert embed_token(f"https://{GL}/g/r.git", f"jane:{PAT}") == f"https://jane:{PAT}@{GL}/g/r.git"
    assert embed_token("https://other.example.net/g/r.git", "jane:tok") == "https://jane:tok@other.example.net/g/r.git"


def test_no_provider_puts_a_token_on_a_cleartext_or_ssh_url(gitlab_host):
    for url in (f"http://{GL}/g/r.git", f"git@{GL}:g/r.git", f"ssh://git@{GL}/g/r.git",
                f"https://someone@{GL}/g/r.git"):
        assert embed_token(url, PAT) == url


def test_a_clone_presents_oauth2_and_persists_no_credential(gitlab_host, monkeypatch, tmp_path):
    calls: list[tuple] = []

    def fake_run_git(cwd, *args, **kw):
        calls.append(args)

    monkeypatch.setattr(workspace_attach, "run_git", fake_run_git)
    url = f"https://{GL}/group/sub/repo.git"
    workspace_attach._git_clone(url, "main", tmp_path / "dest", token=PAT)
    clone = next(a for a in calls if a[0] == "clone")
    assert f"https://oauth2:{PAT}@{GL}/group/sub/repo.git" in clone
    set_url = next(a for a in calls if a[:2] == ("remote", "set-url"))
    assert set_url[-1] == url and PAT not in " ".join(set_url), "origin must be reset token-free"


def test_a_push_error_never_carries_any_spelling_of_the_token(gitlab_host, monkeypatch, tmp_path):
    def failing_git(work, *args, **kw):
        if args[0] == "push":
            raise RuntimeError(f"git push failed: fatal: unable to access 'https://jane:{PAT}@{GL}/g/r.git/'")
        return "sha"

    monkeypatch.setattr(adapters, "_git", failing_git)
    for token in (PAT, f"jane:{PAT}", f"oauth2:{PAT}"):
        with pytest.raises(adapters.GitPushError) as e:
            adapters.push_with_token(tmp_path, f"https://{GL}/g/r.git", "main", token)
        assert PAT not in str(e.value)


def test_the_deploy_key_link_points_at_a_registered_gitlab(gitlab_host):
    assert deploy_keys.deploy_keys_url(f"git@{GL}:group/sub/repo.git") == (
        f"https://{GL}/group/sub/repo/-/settings/repository#js-deploy-keys-settings")
    assert deploy_keys.deploy_keys_url("git@other.example.net:group/repo.git") is None
    assert deploy_keys.deploy_keys_url("git@github.com:acme/kg.git") == "https://github.com/acme/kg/settings/keys"


# ── provider-aware create ─────────────────────────────────────────────────────────────────────────

def test_create_goes_to_github_by_default_and_to_a_registered_gitlab(gitlab_host):
    assert pub.creation_host(None) == "github.com"
    assert pub.creation_host("GitHub.com") == "github.com"
    assert pub.creation_host(GL) == GL


def test_create_on_an_unregistered_host_is_refused_by_name(gitlab_host):
    with pytest.raises(pub.UnsupportedHostError) as e:
        pub.creation_host("other.example.net")
    assert "other.example.net" in str(e.value) and "remote_url" in str(e.value)


@pytest.mark.parametrize("host", ["169.254.169.254", "admin-api", "localhost", "a/b", "x y"])
def test_create_never_aims_at_a_deployment_internal_or_malformed_host(gitlab_host, host):
    with pytest.raises(ValueError):
        pub.creation_host(host)


def _run(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _workspace(root: Path, subject: str) -> Path:
    ws = root / subject
    ws.mkdir(parents=True)
    _run(ws, "init", "-q", "-b", "main")
    _run(ws, "config", "user.email", "t@t")
    _run(ws, "config", "user.name", "t")
    (ws / "CLAUDE.md").write_text("root\n")
    _run(ws, "add", "-A")
    _run(ws, "commit", "-q", "-m", "c0")
    return ws


def test_publish_creates_in_a_nested_gitlab_group_and_pushes(gitlab_host, monkeypatch, tmp_path):
    monkeypatch.setenv("VEXA_ALLOW_LOCAL_REPO_ROOT", str(tmp_path))
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1")
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _run(bare, "init", "-q", "--bare", "-b", "main")
    calls: list[tuple] = []

    def fake_gitlab(host, name, private, token, namespace):
        calls.append((host, name, private, token, namespace))
        return str(bare)

    def never_github(*a):  # pragma: no cover - must not run
        raise AssertionError("a GitLab publish must not call GitHub")

    res = pub.publish_workspace(root, "u1", token=PAT, repo_name="notes", org="group/sub",
                                host=GL, create_repo=never_github, create_gitlab_repo=fake_gitlab)
    assert calls == [(GL, "notes", True, PAT, "group/sub")]
    assert res.created and _run(bare, "rev-parse", "main") == _run(ws, "rev-parse", "HEAD")


def test_publish_to_an_unsupported_host_touches_nothing(gitlab_host, tmp_path):
    root = tmp_path / "workspaces"
    _workspace(root, "u1")

    def never(*a):  # pragma: no cover - must not run
        raise AssertionError("no creator may run for an unsupported host")

    with pytest.raises(pub.UnsupportedHostError):
        pub.publish_workspace(root, "u1", token=PAT, repo_name="notes", host="other.example.net",
                              create_repo=never, create_gitlab_repo=never)
    assert _run(root / "u1", "remote") == "", "no remote may be added"


def test_a_github_org_is_one_name_and_a_gitlab_group_may_nest():
    assert pub._valid_namespace("acme", nested=False) == "acme"
    assert pub._valid_namespace("group/sub/team", nested=True) == "group/sub/team"
    for bad, nested in (("acme/sub", False), ("group/../x", True), ("group//x", True), ("a b", True)):
        with pytest.raises(ValueError):
            pub._valid_namespace(bad, nested=nested)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """Records every GitLab request and answers from a script of (status, body) pairs."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        status, body = self.answers.pop(0)
        if status >= 300:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(json.dumps(body).encode()))
        return _Resp(json.dumps(body).encode())


def test_gitlab_create_resolves_the_nested_group_and_sends_the_token_only_in_a_header(monkeypatch):
    opener = _FakeOpener((200, {"id": 42}), (201, {"http_url_to_repo": f"https://{GL}/group/sub/notes.git"}))
    monkeypatch.setattr(pub, "_GITLAB_OPENER", opener)
    url = pub._gitlab_create_repo(GL, "notes", True, f"oauth2:{PAT}", "group/sub")
    assert url == f"https://{GL}/group/sub/notes.git"
    ns, create = opener.requests
    assert ns.full_url == f"https://{GL}/api/v4/namespaces/group%2Fsub"
    assert create.full_url == f"https://{GL}/api/v4/projects"
    assert json.loads(create.data) == {"name": "notes", "path": "notes", "visibility": "private", "namespace_id": 42}
    for r in (ns, create):
        assert r.get_header("Private-token") == PAT, "the password half authenticates the API"
        assert PAT not in r.full_url


def test_gitlab_create_reports_a_taken_name_and_a_rejected_token_without_the_token(monkeypatch):
    monkeypatch.setattr(pub, "_GITLAB_OPENER", _FakeOpener((400, {"message": {"name": ["has already been taken"]}})))
    with pytest.raises(pub.RepoExistsError):
        pub._gitlab_create_repo(GL, "notes", True, PAT, None)
    monkeypatch.setattr(pub, "_GITLAB_OPENER", _FakeOpener((401, {"message": f"401 Unauthorized {PAT}"})))
    with pytest.raises(pub.PublishError) as e:
        pub._gitlab_create_repo(GL, "notes", True, PAT, None)
    assert PAT not in str(e.value) and "HTTP 401" in str(e.value)


def test_gitlab_create_does_not_follow_a_redirect():
    handler = pub._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/") is None


# ── the route ─────────────────────────────────────────────────────────────────────────────────────

def test_the_publish_route_refuses_an_unsupported_host_before_reading_a_credential(gitlab_host, tmp_path, monkeypatch):
    from tests.test_workspace_manage_routes import _client, _seed_primary
    from control_plane import git_credentials
    from control_plane.routers import workspaces as ws_router

    _seed_primary(tmp_path, "u_jane")
    reads: list = []
    published: list = []
    monkeypatch.setattr(git_credentials, "read_github_token", lambda root, subject: reads.append(1) or "ghp_" + "S" * 36)
    monkeypatch.setattr(ws_router, "publish_workspace", lambda *a, **kw: published.append(kw) or (_ for _ in ()).throw(ValueError("stop")))
    client = _client(tmp_path)
    h = {"X-User-Id": "u_jane"}

    r = client.post("/api/workspace/publish", headers=h, json={"repo_name": "kg", "host": "other.example.net"})
    assert r.status_code == 422 and "other.example.net" in r.json()["detail"]
    assert not reads and not published

    # a registered GitLab host never receives the saved GitHub token
    r = client.post("/api/workspace/publish", headers=h, json={"repo_name": "kg", "host": GL})
    assert r.status_code == 400 and not reads and not published

    client.post("/api/workspace/publish", headers=h, json={"repo_name": "kg", "host": GL, "token": PAT, "org": "group/sub"})
    assert published and published[-1]["host"] == GL and published[-1]["token"] == PAT and not reads
