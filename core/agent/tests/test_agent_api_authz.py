"""The agent-api authorization pass — dispatch 4 of the 2026-09-02 release backlog.

Eleven review rows, one question each, and two of them are questions about the SURFACE rather than
about a line: *does every route that takes a path apply the same rule*, and *does every route that
names a workspace ask whether this caller belongs to it*. Those two are enumerations on purpose —
a per-route test proves the route it was written for and says nothing about the next one somebody
adds, which is exactly how six disagreeing traversal guards came to exist (`shared/workspace_paths`).

Rows: R-A06 · R-A09/R-E09 · R-A10 · R-A15 · R-D15 · R-E04 · R-E05 · R-E11 · R-E13 · R-E14.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import link_resolver, repo_ref, scaffolds as scaffolds_mod, secret_store
from control_plane import workspace_ids as ids
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared import git_redaction
from shared.config import load_settings
from workspaces.shared.entities import upsert_entity
from workspaces.shared.workspace_paths import PathRefused, resolve_inside


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True)


def _client(root: Path, **kw) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)), **kw))


# ── the world every enumeration runs against ─────────────────────────────────────────────────────

@pytest.fixture()
def world(tmp_path):
    """One desk (`u_jane`), one group (`grp`, whose only member is `u_jane`), one outsider
    (`u_mallory`), and one file OUTSIDE the store for the traversal tests to fail to reach."""
    for slug in ("u_jane", "u_mallory"):
        (tmp_path / slug / "kg" / "entities").mkdir(parents=True)
        (tmp_path / slug / "README.md").write_text(f"# {slug}\n")
    grp = tmp_path / "grp"
    (grp / "policy").mkdir(parents=True)
    (grp / "policy" / "members.json").write_text('[{"subject":"u_jane","role":"owner"}]')
    (grp / "README.md").write_text("# grp\n")
    upsert_entity(grp, "person", "Cottalango Leon", ["Chairs the TSC."], "the meeting")
    for d in (tmp_path / "u_jane", tmp_path / "u_mallory", grp):
        _git(d, "init", "-q", "-b", "main")
        _git(d, "config", "user.email", "t@t")
        _git(d, "config", "user.name", "t")
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "init")
    secret = tmp_path.parent / "outside-the-store.txt"
    secret.write_text("root:x:0:0:the file no route may read\n")
    return tmp_path, secret


JANE = {"X-User-Id": "u_jane"}
MALLORY = {"X-User-Id": "u_mallory"}


# ── R-A06 · the link resolver's traversal guard counted `..` and nothing else ────────────────────

def test_ra06_an_absolute_target_is_an_escape(world):
    """`Path("/ws") / "/etc/passwd"` is `/etc/passwd` — the join DISCARDS the root, so counting
    `..` segments answers a question nobody asked."""
    assert link_resolver.escapes("/etc/passwd") is True
    assert link_resolver.escapes("../x") is True
    assert link_resolver.escapes("kg/INDEX.md") is False


def test_ra06_the_resolver_neither_reads_nor_echoes_an_absolute_path(world):
    root, secret = world
    reg = ids.WorkspaceRegistry()
    ids.migrate(root, reg)
    gid = reg.by_slug("grp")["id"]
    member = lambda r, slug, subject: "owner" if (slug, subject) == ("grp", "u_jane") else None  # noqa: E731

    out = link_resolver.resolve(f"ws:{gid}/{secret}", subject="u_jane", root=root,
                                registry=reg, is_member=member)
    # The client hands `path` and `url` straight to the file endpoint, where the string gets a
    # second chance — so echoing either one back is the whole exploit, not a cosmetic leak.
    assert out.get("path") is None
    assert (out.get("url") or "").find(str(secret)) == -1
    assert out.get("missing") is True


def test_ra06_a_symlink_out_of_the_workspace_is_an_escape(world):
    """The one shape no textual check can see: the string stays inside, the resolved path does not."""
    root, secret = world
    os.symlink(secret, root / "grp" / "escape.md")
    reg = ids.WorkspaceRegistry()
    ids.migrate(root, reg)
    gid = reg.by_slug("grp")["id"]
    member = lambda r, slug, subject: "owner" if (slug, subject) == ("grp", "u_jane") else None  # noqa: E731
    out = link_resolver.resolve(f"ws:{gid}/escape.md", subject="u_jane", root=root,
                                registry=reg, is_member=member)
    assert out.get("path") is None and out.get("missing") is True


# ── the common rule · every route that takes a path applies the SAME one ─────────────────────────

def _path_routes(client, path: str, *, sha: str = "HEAD"):
    """Every agent-api route that takes a caller-supplied PATH, as (name, callable)."""
    return [
        ("GET /api/workspace/file",
         lambda: client.get("/api/workspace/file", params={"path": path}, headers=JANE)),
        ("PUT /api/workspace/file",
         lambda: client.put("/api/workspace/file", json={"path": path, "content": "x"}, headers=JANE)),
        ("POST /api/desk/touch",
         lambda: client.post("/api/desk/touch", json={"workspace": "x" * 10, "path": path}, headers=JANE)),
        ("GET /api/workspace/git/show",
         lambda: client.get("/api/workspace/git/show", params={"sha": sha, "path": path}, headers=JANE)),
    ]


@pytest.mark.parametrize("bad", ["/etc/passwd", "../u_mallory/README.md", "kg/../../u_mallory/README.md"])
def test_every_path_route_refuses_the_same_shapes(world, bad):
    root, _ = world
    c = _client(root)
    for name, call in _path_routes(c, bad):
        r = call()
        assert r.status_code in (400, 403, 404), f"{name} admitted {bad!r} ({r.status_code})"
        assert "the file no route may read" not in r.text, f"{name} LEAKED {bad!r}"


def test_every_path_route_refuses_a_symlink_out_of_the_workspace(world):
    root, secret = world
    os.symlink(secret, root / "u_jane" / "escape.md")
    c = _client(root)
    for name, call in _path_routes(c, "escape.md"):
        r = call()
        assert "the file no route may read" not in r.text, f"{name} followed a symlink out"


def test_every_path_route_refuses_the_repository_and_identity_directories(world):
    """`.git` executes on the next commit; `.vexa` IS the workspace id — overwrite it and every
    `[[ws:<id>/…]]` link into this workspace resolves `gone` (PRD decision 26.1)."""
    root, _ = world
    c = _client(root)
    for reserved in (".git/config", ".vexa/workspace.json"):
        for name, call in _path_routes(c, reserved):
            r = call()
            assert r.status_code in (400, 403, 404), f"{name} admitted {reserved} ({r.status_code})"


# ── authorization · every route that names a workspace asks whether this caller belongs ──────────

def _workspace_routes(client, slug: str, headers: dict):
    return [
        ("GET /api/workspace/tree",
         lambda: client.get("/api/workspace/tree", params={"slug": slug}, headers=headers)),
        ("GET /api/workspace/file",
         lambda: client.get("/api/workspace/file", params={"slug": slug, "path": "README.md"}, headers=headers)),
        ("PUT /api/workspace/file",
         lambda: client.put("/api/workspace/file", json={"slug": slug, "path": "pwned.md", "content": "x"},
                            headers=headers)),
        ("POST /api/workspace/entity",
         lambda: client.post("/api/workspace/entity",
                             json={"slug": slug, "kind": "person", "name": "Mallory", "facts": ["f"],
                                   "source": "s"}, headers=headers)),
        ("GET /api/workspace/git",
         lambda: client.get("/api/workspace/git", params={"slug": slug}, headers=headers)),
        ("GET /api/workspace/git/show",
         lambda: client.get("/api/workspace/git/show", params={"slug": slug, "sha": "HEAD"}, headers=headers)),
        ("GET /api/workspace/{slug}/deploy-key",
         lambda: client.get(f"/api/workspace/{slug}/deploy-key", headers=headers)),
        ("POST /api/workspace/{slug}/deploy-key",
         lambda: client.post(f"/api/workspace/{slug}/deploy-key", json={}, headers=headers)),
        ("GET /api/workspace/purpose",
         lambda: client.get("/api/workspace/purpose", params={"slug": slug}, headers=headers)),
        ("GET /api/workspace/git-remote-status",
         lambda: client.get("/api/workspace/git-remote-status", params={"slug": slug}, headers=headers)),
    ]


def test_a_non_member_is_refused_by_every_route_that_names_a_group(world):
    """Decision 26.3: a GROUP is readable only by its members. `u_mallory` is in none."""
    root, _ = world
    c = _client(root)
    for name, call in _workspace_routes(c, "grp", MALLORY):
        r = call()
        assert r.status_code in (400, 403, 404), f"{name} admitted a non-member ({r.status_code})"
        assert "Cottalango" not in r.text, f"{name} leaked a group page to a non-member"
    assert not (root / "grp" / "pwned.md").exists()


def test_a_colleagues_desk_is_readable_and_never_writable(world):
    """The other half of the same ruling (decision 21 · 26.3): a desk is readable by any signed-in
    member of this instance and writable by its owner. A test that only proves the refusal would
    pass on a build that refused everything."""
    root, _ = world
    c = _client(root)
    assert c.get("/api/workspace/file", params={"slug": "u_jane", "path": "README.md"},
                 headers=MALLORY).status_code == 200
    assert c.put("/api/workspace/file", json={"slug": "u_jane", "path": "pwned.md", "content": "x"},
                 headers=MALLORY).status_code in (403, 404)
    assert not (root / "u_jane" / "pwned.md").exists()


# ── R-E04 · `POST /api/friction` let the BODY name the subject ───────────────────────────────────

def test_re04_an_anonymous_report_cannot_be_attributed_to_a_named_user(world):
    root, _ = world
    c = _client(root)
    r = c.post("/api/friction", json={"kind": "other", "happened": "x", "subject": "u_jane"})
    assert r.status_code == 201
    rows = c.get("/api/friction/dump", params={"format": "json"}, headers=JANE).json()["records"]
    assert [x for x in rows if x.get("subject") == "u_jane"] == [], "the body named someone else"


# ── R-E05 · the dump returned every user's reports to any signed-in caller ───────────────────────

def test_re05_the_dump_is_scoped_to_the_caller(world):
    root, _ = world
    c = _client(root)
    c.post("/api/friction", json={"kind": "other", "happened": "jane's private workspace path"},
           headers=JANE)
    c.post("/api/friction", json={"kind": "other", "happened": "mallory's own edge"}, headers=MALLORY)

    mine = c.get("/api/friction/dump", params={"format": "json"}, headers=MALLORY).json()
    assert "jane's private workspace path" not in str(mine)
    assert "mallory's own edge" in str(mine)


def test_re05_one_user_cannot_close_anothers_record(world):
    root, _ = world
    c = _client(root)
    rid = c.post("/api/friction", json={"kind": "other", "happened": "jane's edge"},
                 headers=JANE).json()["id"]
    assert c.post(f"/api/friction/{rid}/fix", json={"fix_ref": "nope"},
                  headers=MALLORY).status_code in (403, 404)


# ── R-E11 · raw exception text on a path that can carry a Redis password ─────────────────────────

def test_re11_the_admin_overview_redacts_the_error_it_reports(monkeypatch, world):
    """`overview["meetings_error"] = f"{type(e).__name__}: {e}"` — the exception comes off a redis
    client built from a URL that routinely carries `redis://:password@host`, and `redact()` is
    applied two routes away and not here. The invariant under test is the SHAPE: whatever the
    exception says, the response body has been through the scrubber."""
    root, _ = world
    from control_plane import admin_panel

    url = "redis://:hunter2thepasswordthatmustnotleak@redis-host:6379/0"
    monkeypatch.setattr(admin_panel, "pipeline_snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"AUTH failed for {url}")))
    monkeypatch.setattr(admin_panel, "fetch_workloads",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"connect {url}")))
    c = TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root), redis_url=url,
                                 internal_api_secret="s" * 32), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root))))
    body = c.get("/api/admin/overview", headers={**JANE, "X-Internal-Secret": "s" * 32})
    assert body.status_code == 200, body.text
    assert "hunter2thepasswordthatmustnotleak" not in body.text, body.text[:400]


# ── R-A09 / R-E09 · redaction-by-shape missed this project's own secret shapes ───────────────────

@pytest.mark.parametrize("secret,line", [
    ("f" * 64, "internal secret is {} in the environment"),               # openssl rand -hex 32
    ("a1b2c3d4" * 4, "admin token {} rejected"),                           # 32-char hex
    ("sk_live_51H8xQ2LkD9fPqRs7", "stripe key {} refused"),
    ("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "aws secret {} in the env"),
])
def test_ra09_the_shapes_this_project_actually_has_are_masked(secret, line):
    out = git_redaction.redact(line.format(secret))
    assert secret not in out, out


def test_ra09_a_git_object_id_in_a_git_message_still_survives():
    """The exemption is why the scrubber is usable at all — narrowed, not deleted."""
    oid = "9" * 40
    assert oid in git_redaction.redact(f"error: pathspec did not match; commit {oid} is unreachable")


def test_ra09_a_deploy_key_answer_is_still_readable():
    pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB" + "Q" * 20 + " vexa@workspace"
    fp = "SHA256:" + "Kj9" * 14 + "x"
    msg = f"Add this deploy key to your repository:\n{pub}\nfingerprint {fp}"
    out = git_redaction.redact(msg)
    assert pub in out and fp in out


@pytest.mark.parametrize("msg,keep", [
    ("fatal: repository 'https://gitlab.example.com/some-team/some-long-repository-name.git' not found",
     "some-long-repository-name"),
    ("error: pathspec 'feature/PR-1234-something' did not match any file(s) known to git",
     "feature/PR-1234-something"),
])
def test_ra09_a_git_error_still_says_what_it_is_about(msg, keep):
    """The widened rule pays for itself only if the message stays readable — a scrubber that eats
    its own diagnostics gets bypassed, which is worse than one that misses a shape."""
    assert keep in git_redaction.redact(msg)


# ── R-D15 · repo_ref whitelisted a URL's SHAPE but not its HOST (git-clone SSRF) ─────────────────

@pytest.mark.parametrize("url", [
    "http://169.254.169.254/a/b",          # the cloud metadata service
    "http://admin-api:8001/a/b",           # a compose service name — internal by construction
    "http://127.0.0.1:8001/a/b",
    "http://localhost/a/b",
    "https://10.1.2.3/a/b",
    "https://192.168.1.5/a/b",
])
def test_rd15_an_internal_host_is_refused_before_git_exists(url):
    with pytest.raises(repo_ref.RepoRefError):
        repo_ref.normalize(url)


@pytest.mark.parametrize("url", [
    "https://github.com/owner/repo",
    "https://gitlab.example.com/team/repo.git",
    "git@github.com:owner/repo.git",
    "owner/repo",
])
def test_rd15_a_real_repository_still_normalizes(url):
    assert repo_ref.normalize(url)


# ── R-E14 · user-controlled `ref` reached `git checkout` with no end-of-options guard ────────────

def test_re14_a_ref_that_is_a_git_option_is_refused(tmp_path):
    """`git checkout --quiet <ref>` with `ref = "--detach"` exits 0 and detaches HEAD — the value a
    caller supplied was consumed as a git OPTION. `--detach` is the harmless member of that family;
    the family is the finding."""
    from control_plane import workspace_attach
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    (origin / "README.md").write_text("v0\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "init")

    with pytest.raises(ValueError):
        workspace_attach._git_clone(str(origin), "--detach", tmp_path / "pwned")
    # …and an ordinary ref still checks out, which is the half a blanket refusal would break.
    workspace_attach._git_clone(str(origin), "main", tmp_path / "fine")
    assert (tmp_path / "fine" / "README.md").is_file()


def test_re14_and_rd15_reach_the_route_as_a_422_not_a_500(tmp_path, monkeypatch):
    """Both refusals are ``RepoRefError``, which the attach routes already render as a sentence a
    person can act on — the point of putting them in ``repo_ref`` rather than raising a bare
    ``ValueError`` that would surface as an unhandled 500."""
    from tests import gitserve
    c = _client(tmp_path)
    repo = gitserve.serve(tmp_path, gitserve.bare_repo(tmp_path, "mine"), monkeypatch, repo="mine")
    assert c.post("/api/workspace/swap", json={"repo": repo, "ref": "--detach"},
                  headers=JANE).status_code == 422
    assert c.post("/api/workspace/swap", json={"repo": "http://admin-api:8001/a/b", "ref": "main"},
                  headers=JANE).status_code == 422
    # …and the ordinary swap still works, so neither guard is a blanket refusal.
    assert c.post("/api/workspace/swap", json={"repo": repo, "ref": "main"},
                  headers=JANE).status_code == 200


# ── R-E13 · the secret store wrote on a read path, and a wrong key read as "no credential" ───────

def test_re13_a_read_does_not_create_the_master_key(tmp_path):
    """A sealed envelope exists (written under an operator-supplied key). Reading it with no key
    configured must ANSWER, not mint a fresh `.master.key` beside the ciphertext it cannot open."""
    secret_store.put(tmp_path, "u_jane/github", "ghp_thetoken", key_env="the-original-key")
    assert secret_store.get(tmp_path, "u_jane/github") is None
    assert not (secret_store.secrets_dir(tmp_path) / secret_store.MASTER_KEY_FILENAME).exists(), \
        "a pure read created server-side key state"


def test_re13_an_undecryptable_secret_is_not_reported_as_absent(tmp_path):
    secret_store.put(tmp_path, "u_jane/github", "ghp_thetoken", key_env="the-original-key")
    assert secret_store.get(tmp_path, "u_jane/github", key_env="the-original-key") == "ghp_thetoken"
    # The operator rotated (or lost) VEXA_SECRETS_KEY. "no credential" would have them re-paste the
    # PAT, silently overwriting an envelope that was fine.
    assert secret_store.state(tmp_path, "u_jane/github", key_env="a-different-key") == "unreadable"
    assert secret_store.state(tmp_path, "u_jane/nothing-here", key_env="a-different-key") == "absent"


# ── R-A10 · the scaffold id was a non-expiring capability with an unpruned index ─────────────────

def _fake_redis():
    import fakeredis
    return fakeredis.FakeStrictRedis(decode_responses=True)


def test_ra10_a_minted_scaffold_expires():
    r = _fake_redis()
    store = scaffolds_mod.ScaffoldStore(r)
    rec = store.mint({"who": "ben@bank.test", "kind": "first-visit", "opening": "hi"})
    assert r.ttl(f"agent:scaffold:{rec['id']}") > 0, "a scaffold link stays openable forever"


def test_ra10_a_redeemed_scaffold_leaves_the_pending_index():
    r = _fake_redis()
    store = scaffolds_mod.ScaffoldStore(r)
    rec = store.mint({"who": "ben@bank.test", "kind": "first-visit", "opening": "hi"})
    store.redeem(rec["id"], "u_ben")
    assert store.for_recipient("ben@bank.test") == []
    key = f"agent:scaffolds:by:{scaffolds_mod._recipient_key('ben@bank.test')}"
    assert rec["id"] not in (r.smembers(key) or set()), "the index never sheds a redeemed id"


# ── R-A15 · a mount that lost its write bit was silently promoted to a write target ──────────────

def test_ra15_a_mount_with_no_write_key_is_not_a_write_target(tmp_path):
    from worker.engine import writeback_candidates
    (tmp_path / "kg" / "entities").mkdir(parents=True)
    assert writeback_candidates(["Olga Avramenko spoke."], mounts=[{"path": str(tmp_path)}]) == []
    assert writeback_candidates(["Olga Avramenko spoke."],
                                mounts=[{"path": str(tmp_path), "write": True}]) != []


# ── the helper the whole dispatch is built on ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["/etc/passwd", "../x", "a/../../b", ".git/config", ".vexa/workspace.json", ""])
def test_resolve_inside_refuses_every_shape(tmp_path, bad):
    with pytest.raises(PathRefused):
        resolve_inside(tmp_path, bad)


def test_resolve_inside_admits_an_ordinary_document(tmp_path):
    assert resolve_inside(tmp_path, "kg/entities/person/olga.md") == \
        (tmp_path / "kg/entities/person/olga.md").resolve()
