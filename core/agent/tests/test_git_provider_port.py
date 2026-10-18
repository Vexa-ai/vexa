"""The git provider port: one ``GitProvider`` per configured host, GitHub and GitLab adapters.

What this file holds:

* **selection** — a URL, a host or a name picks exactly one configured provider; GitHub is always there;
* **nested groups** — a GitLab provider (with a port and a path prefix) reads, links and creates under
  ``group/sub/…``; GitHub stays ``owner/repo``;
* **no hard-coded host** — ``github.com`` appears only in the GitHub adapter and its defaults (static);
* **GitHub byte-compatibility** — the create request is the one recorded before the port existed;
* **the GitLab create → push flow** — a fake GitLab answers the API, a local bare repo takes the push;
* **saved tokens per provider** — one broker store, a name per provider, each sent only to its host;
* **typed faults** — a rejected token is ``token_rejected``, naming the provider, never the token.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import urllib.error
from pathlib import Path

import pytest

from shared import git_provider as gp
from shared.git_provider_github import GitHubProvider
from shared.git_provider_gitlab import GitLabProvider

REPO = Path(__file__).resolve().parents[3]
AGENT = Path(__file__).resolve().parents[1]
CORP = {"name": "corp", "kind": "gitlab", "baseUrl": "https://git.corp.example:8443/gitlab", "label": "Corp GitLab"}
PEM = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"


@pytest.fixture
def corp(monkeypatch):
    monkeypatch.setenv(gp.PROVIDERS_ENV, json.dumps([CORP]))
    return gp.registry()


# ── selection ─────────────────────────────────────────────────────────────────────────────────────

def test_github_is_always_configured_and_is_the_default(monkeypatch):
    monkeypatch.delenv(gp.PROVIDERS_ENV, raising=False)
    reg = gp.registry()
    assert [p.name for p in reg.all()] == ["github"]
    assert isinstance(reg.default(), GitHubProvider)
    assert reg.for_url("https://github.com/acme/kg.git").name == "github"
    assert reg.for_url("git@github.com:acme/kg.git").name == "github"
    assert reg.for_url("https://gitlab.com/acme/kg.git") is None


def test_a_url_selects_its_provider_by_host_port_and_prefix(corp):
    assert corp.for_url("https://git.corp.example:8443/gitlab/it/ai/vexa/notes.git").name == "corp"
    assert corp.for_url("git@git.corp.example:it/ai/vexa/notes.git").name == "corp"
    assert corp.for_url("https://git.corp.example/gitlab/it/notes.git") is None          # other port
    assert corp.for_url("https://git.corp.example:8443/other/it/notes.git") is None      # outside the prefix
    assert corp.by_name("CORP") is corp.for_url("https://git.corp.example:8443/gitlab/a/b")
    assert corp.by_name("nope") is None


def test_the_comma_list_still_configures_hosts(monkeypatch):
    monkeypatch.setenv(gp.PROVIDERS_ENV, "gitlab.example.com=gitlab,git.example.org=basic:svc")
    reg = gp.registry()
    assert reg.for_url("https://gitlab.example.com/g/s/r.git").kind == "gitlab"
    assert reg.for_url("https://git.example.org/a/b.git").kind == "basic"
    assert reg.by_name("git.example.org").can_create is False


@pytest.mark.parametrize("bad", [
    '[{"name":"x","kind":"gitlab","baseUrl":"http://git.corp.example"}]',          # cleartext
    '[{"name":"x","kind":"bitbucket","baseUrl":"https://git.corp.example"}]',      # unknown kind
    '[{"name":"X Y","kind":"gitlab","baseUrl":"https://git.corp.example"}]',       # bad name
    '[{"name":"x","kind":"gitlab","baseUrl":"https://u:p@git.corp.example"}]',     # credential in URL
    '[{"name":"x","kind":"gitlab","baseUrl":"https://a.example"},{"name":"x","kind":"gitlab","baseUrl":"https://b.example"}]',
    '[{"name":"x","kind":"gitlab","baseUrl":"https://a.example","token":"t"}]',    # unknown key
    '[{"name":"x","kind":"gitlab","baseUrl":"https://a.example","caBundle":"not a pem"}]',
    '{"name":"x"}',
])
def test_a_malformed_provider_list_is_refused_by_name(bad):
    with pytest.raises(ValueError) as e:
        gp.parse_registry(bad)
    assert gp.PROVIDERS_ENV in str(e.value)


def test_a_malformed_provider_list_stops_the_boot():
    from pydantic import ValidationError
    from shared.config import load_settings
    with pytest.raises(ValidationError):
        load_settings(git_providers='[{"name":"x","kind":"gitlab","baseUrl":"http://a.example"}]')
    assert load_settings(git_providers=json.dumps([CORP])).git_providers


def test_a_github_enterprise_provider_uses_its_own_api():
    ghe = gp.parse_registry('[{"name":"ghe","kind":"github","baseUrl":"https://ghe.corp.example"}]').by_name("ghe")
    assert ghe.api_url == "https://ghe.corp.example/api/v3"
    assert gp.parse_registry("").default().api_url == "https://api.github.com"


# ── nested groups ─────────────────────────────────────────────────────────────────────────────────

def test_gitlab_reads_links_and_clones_nested_group_paths(corp):
    p = corp.by_name("corp")
    url = "https://git.corp.example:8443/gitlab/it/ai/vexa/notes.git"
    assert p.repo_path(url) == "it/ai/vexa/notes"
    assert p.web_url("it/ai/vexa/notes") == "https://git.corp.example:8443/gitlab/it/ai/vexa/notes"
    assert p.clone_url("it/ai/vexa/notes") == url
    assert p.namespace("it/ai/vexa") == "it/ai/vexa"
    assert p.deploy_keys_url("it/ai/vexa/notes") == \
        "https://git.corp.example:8443/gitlab/it/ai/vexa/notes/-/settings/repository#js-deploy-keys-settings"
    with pytest.raises(ValueError):
        p.namespace("it/../x")


def test_github_stays_owner_slash_repo(corp):
    gh = corp.default()
    assert gh.repo_path("https://github.com/acme/kg.git") == "acme/kg"
    assert gh.repo_path("https://github.com/a/b/c.git") is None
    assert gh.namespace("acme") == "acme"
    with pytest.raises(ValueError):
        gh.namespace("acme/sub")
    assert gh.deploy_keys_url("acme/kg") == "https://github.com/acme/kg/settings/keys"


def test_a_provider_describes_itself_without_a_credential(corp):
    d = corp.by_name("corp").describe()
    assert d == {"name": "corp", "kind": "gitlab", "label": "Corp GitLab",
                 "base_url": "https://git.corp.example:8443/gitlab", "host": "git.corp.example:8443",
                 "nested_groups": True, "can_create": True}


def test_gitlab_token_is_presented_as_oauth2_on_its_host_only(corp):
    from shared.token_destination import embed_token
    assert embed_token("https://git.corp.example:8443/gitlab/it/ai/r.git", "glpat-abc") == \
        "https://oauth2:glpat-abc@git.corp.example:8443/gitlab/it/ai/r.git"
    assert embed_token("https://github.com/acme/kg.git", "ghp_x") == "https://ghp_x@github.com/acme/kg.git"


def test_a_private_ca_reaches_git_only_for_its_provider(monkeypatch):
    monkeypatch.setenv(gp.PROVIDERS_ENV, json.dumps([{**CORP, "caBundle": PEM}]))
    from shared.gitenv import transport_env
    env = transport_env("https://git.corp.example:8443/gitlab/it/r.git")
    assert Path(env["GIT_SSL_CAINFO"]).read_text() == PEM
    assert "GIT_SSL_CAINFO" not in transport_env("https://github.com/acme/kg.git")


# ── no hard-coded host ────────────────────────────────────────────────────────────────────────────

#: The only files that may name github.com in the code that talks to git hosts: the GitHub adapter
#: (Python) and the terminal's provider defaults. Each other entry says why it is not git hosting.
ALLOWED = {
    "core/agent/shared/git_provider_github.py": "the GitHub adapter",
    "clients/terminal/src/data/gitProviders.ts": "the terminal's built-in GitHub default",
    "clients/terminal/src/minutes/connectionHosts.ts": "custom-secret API host suggestions, not git hosting",
}
SCANNED = ("core/agent/control_plane", "core/agent/shared", "core/agent/worker", "clients/terminal/src")


def _production_files():
    for top in SCANNED:
        for f in (REPO / top).rglob("*"):
            rel = f.relative_to(REPO).as_posix()
            if f.suffix not in (".py", ".ts", ".tsx", ".json") or "/tests/" in rel or "__tests__" in rel \
                    or re.search(r"\.test\.tsx?$", rel) or "node_modules" in rel:
                continue
            yield rel, f


def test_no_git_host_is_hard_coded_outside_its_adapter():
    hits = []
    for rel, f in _production_files():
        if rel in ALLOWED:
            continue
        for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            if re.search(r"github\.com", line, re.I) and "github.com/Vexa-ai/" not in line:
                hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    assert not hits, "github.com outside the GitHub adapter:\n" + "\n".join(hits)


# ── GitHub byte-compatibility (recorded before the port) ──────────────────────────────────────────

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Recorder:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        code, body = self.answers.pop(0)
        if code >= 300:
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(json.dumps(body).encode()))
        return _Resp(json.dumps(body).encode())


def test_github_create_sends_exactly_what_it_sent_before_the_port():
    recorded = json.loads((AGENT / "tests/fixtures/github_create_recorded.json").read_text())
    for case in recorded:
        rec = _Recorder((201, {"clone_url": case["returned"]}))
        gh = GitHubProvider("github", "https://github.com", opener=rec)
        a = case["args"]
        got = gh.create_repo("recorded-test-token", a["name"], a["private"], a["org"])
        req = rec.requests[0]
        assert got == case["returned"]
        assert {"method": req.get_method(), "url": req.full_url,
                "headers": dict(sorted((k.lower(), v) for k, v in req.header_items())),
                "body": json.loads(req.data.decode())} == case["request"]


def test_the_publish_shim_still_creates_on_github_by_default(monkeypatch):
    from control_plane import workspace_publish as wp
    assert wp.creation_host(None) == "github.com"
    assert wp.creation_host("github.com") == "github.com"


# ── the GitLab create → push flow (fake API, real git) ────────────────────────────────────────────

def _git(*a, cwd=None):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def test_gitlab_create_resolves_the_nested_group_then_creates_with_the_token_in_a_header(corp):
    rec = _Recorder((200, {"id": 42, "full_path": "it/ai/vexa"}),
                    (201, {"http_url_to_repo": "https://git.corp.example:8443/gitlab/it/ai/vexa/notes.git"}))
    p = GitLabProvider("corp", "https://git.corp.example:8443/gitlab", opener=rec)
    url = p.create_repo("oauth2:glpat-SECRET", "notes", True, "it/ai/vexa")
    assert url == "https://git.corp.example:8443/gitlab/it/ai/vexa/notes.git"
    ns, create = rec.requests
    assert ns.full_url == "https://git.corp.example:8443/gitlab/api/v4/namespaces/it%2Fai%2Fvexa"
    assert create.full_url == "https://git.corp.example:8443/gitlab/api/v4/projects"
    assert json.loads(create.data) == {"name": "notes", "path": "notes", "visibility": "private", "namespace_id": 42}
    for r in rec.requests:
        assert r.get_header("Private-token") == "glpat-SECRET"
        assert "SECRET" not in r.full_url


def test_publish_to_a_named_gitlab_provider_creates_in_the_group_and_pushes(corp, tmp_path, monkeypatch):
    from control_plane import workspace_publish as wp
    root = tmp_path / "root"
    ws = root / "u1"
    ws.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=ws)
    (ws / "a.md").write_text("hello\n")
    _git("add", "a.md", cwd=ws)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one", cwd=ws)
    bare = tmp_path / "remote.git"
    _git("init", "-q", "--bare", str(bare))
    seen = {}

    def create(provider, token, name, private, owner):
        seen.update(provider=provider.name, name=name, owner=owner, private=private)
        return str(bare)

    res = wp.publish_workspace(root, "u1", token="glpat-x", repo_name="notes", provider="corp",
                               org="it/ai/vexa", ws_dir=ws, create_with=create)
    assert seen == {"provider": "corp", "name": "notes", "owner": "it/ai/vexa", "private": True}
    assert res.created and res.pushed_ref == "main"
    assert subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "main"], capture_output=True,
                          text=True).stdout.strip() == res.head_sha


def test_publish_refuses_an_unknown_provider_before_touching_anything(corp, tmp_path):
    from control_plane import workspace_publish as wp
    with pytest.raises(wp.UnsupportedHostError) as e:
        wp.publish_workspace(tmp_path, "u1", token="t", repo_name="n", provider="elsewhere", ws_dir=tmp_path)
    assert "corp" in str(e.value) and "github" in str(e.value)


# ── typed faults ──────────────────────────────────────────────────────────────────────────────────

def test_a_rejected_token_is_a_typed_fault_naming_the_provider_never_the_token(corp):
    p = GitLabProvider("corp", "https://git.corp.example:8443/gitlab", label="Corp GitLab",
                       opener=_Recorder((401, {"message": "401 Unauthorized glpat-SECRET"})))
    with pytest.raises(gp.GitProviderError) as e:
        p.create_repo("glpat-SECRET", "notes", True, None)
    assert e.value.fault == "token_rejected" and e.value.provider == "corp"
    assert "SECRET" not in str(e.value) and "Corp GitLab" in str(e.value)
    gh = GitHubProvider("github", "https://github.com", opener=_Recorder((401, {"message": "Bad credentials"})))
    with pytest.raises(gp.GitProviderError) as e:
        gh.verify_token("ghp_x")
    assert e.value.fault == "token_rejected"


def test_verify_token_names_the_account(corp):
    p = GitLabProvider("corp", "https://git.corp.example:8443/gitlab", opener=_Recorder((200, {"username": "svc-vexa"})))
    assert p.verify_token("glpat-x") == "svc-vexa"
    gh = GitHubProvider("github", "https://github.com", opener=_Recorder((200, {"login": "octo"})))
    assert gh.verify_token("ghp_x") == "octo"


# ── saved tokens: one store, one name per provider, each sent only to its own host ────────────────

def test_saved_token_names_are_per_provider_and_github_keeps_its_name(corp):
    from control_plane import git_credentials as gc
    assert gc.secret_name("u1", corp.default()) == "pat/u1"
    assert gc.secret_name("u1", corp.by_name("corp")) == "pat/u1/corp"


def test_a_saved_token_reaches_only_its_own_provider(corp, monkeypatch, tmp_path):
    from control_plane import git_credentials as gc, workspace_credentials as wc
    store = {"pat/u1": "ghp_SAVED", "pat/u1/corp": "glpat-SAVED"}
    monkeypatch.setattr(gc.secret_store, "get", lambda root, name: store.get(name))
    assert wc._token_for(tmp_path, "https://github.com/acme/kg.git", "u1", None) == "ghp_SAVED"
    assert wc._token_for(tmp_path, "https://git.corp.example:8443/gitlab/it/r.git", "u1", None) == "glpat-SAVED"
    assert wc._token_for(tmp_path, "https://gitlab.com/acme/kg.git", "u1", None) is None
    assert wc._token_for(tmp_path, "http://git.corp.example:8443/gitlab/it/r.git", "u1", None) is None


def test_the_broker_accepts_a_per_provider_name_only_for_its_owner():
    import sys
    sys.path.insert(0, str(AGENT / "services/credential-broker/src"))
    from credential_broker.models import GitSecretBody
    from credential_broker.routes_git import owned_by
    GitSecretBody(name="pat/u1/corp", action="get")
    assert owned_by("pat/u1/corp", "u1", frozenset())
    assert not owned_by("pat/u1/corp", "u2", frozenset())
    assert not owned_by("pat/u1/../u2", "u1", frozenset())
