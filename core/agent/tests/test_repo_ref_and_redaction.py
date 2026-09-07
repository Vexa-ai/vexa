"""The 2026-09-02 incident, in tests: a PAT was pasted into the attach dialog's REPOSITORY field.

Two independent failures had to line up, so there are two independent fixes and both are held here:

  1. **Nothing validated the field** (``repo_ref``) — the string went straight to ``git clone``.
  2. **Nothing scrubbed git's answer** (``git_redaction``) — redaction was ``text.replace(token, …)``,
     which does nothing when the credential arrived as the ``repo`` argument rather than the ``token``
     one, so ``fatal: repository '<the token>' does not exist`` reached the card, the response body and
     the browser console.

Either fix alone would have stopped the leak. Both are here because the next credential will arrive
through a field nobody has classified yet, and only the second one is shape-based.
"""
from __future__ import annotations

import logging
import subprocess

import pytest
from fastapi.testclient import TestClient

from control_plane import repo_ref
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_attach import CloneError, _git_clone, swap_workspace
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from shared.git_redaction import MASK, looks_like_token, redact

PAT = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FINE = "github_pat_11ABCDE0aAbBcCdDeEfF_gGhHiIjJkKlLmMnNoOpPqQrRsStTuUvVwWxXyYzZ01"
GLPAT = "glpat-aBcDeFgHiJkLmNoPqRsT"


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _client(root):
    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
    ))


# ── F78 · what may be typed into "Repository" ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("https://github.com/acme/kg", "https://github.com/acme/kg.git"),
    ("https://github.com/acme/kg.git", "https://github.com/acme/kg.git"),
    ("https://github.com/acme/kg/", "https://github.com/acme/kg.git"),
    ("http://git.internal:8080/acme/kg.git", "http://git.internal:8080/acme/kg.git"),
    ("git@github.com:acme/kg.git", "git@github.com:acme/kg.git"),
    ("git@github.com:acme/kg", "git@github.com:acme/kg.git"),
    ("ssh://git@github.com/acme/kg.git", "ssh://git@github.com/acme/kg.git"),
    ("ssh://github.com/acme/kg", "ssh://github.com/acme/kg.git"),
    ("acme/kg", "https://github.com/acme/kg.git"),        # the bare shorthand expands to GitHub
    ("  acme/kg.git  ", "https://github.com/acme/kg.git"),
])
def test_a_repository_reference_normalizes(raw, expected):
    assert repo_ref.normalize(raw) == expected


def test_empty_means_swap_back_not_a_repository():
    assert repo_ref.normalize(None) is None
    assert repo_ref.normalize("   ") is None


@pytest.mark.parametrize("token", [PAT, FINE, GLPAT, "gho_aaaaaaaaaaaaaaaaaaaaaaaa",
                                   "ghs_bbbbbbbbbbbbbbbbbbbbbbbb", "ghu_cccccccccccccccccccccccc",
                                   "ghr_dddddddddddddddddddddddd"])
def test_a_token_in_the_repository_field_is_refused_by_name(token):
    with pytest.raises(repo_ref.RepoRefError) as e:
        repo_ref.normalize(token)
    assert e.value.kind == "token"
    assert e.value.sentence == (
        "That looks like a token, not a repository. Paste the repository URL here; "
        "a saved token goes in the token card.")
    assert token not in str(e.value), "the refusal must never carry the value back"


def test_a_url_carrying_a_credential_is_refused_too():
    with pytest.raises(repo_ref.RepoRefError) as e:
        repo_ref.normalize(f"https://{PAT}@github.com/acme/kg.git")
    assert e.value.kind == "token"


@pytest.mark.parametrize("bad", [
    "/workspaces/u_someone_else",          # another user's directory in the shared store
    "/tmp/anything",
    "file:///workspaces/u_someone_else",
    "not a url at all",
    "ftp://example.com/acme/kg.git",
    "https://github.com/acme",             # no repo
    "acme",                                # no owner
    "https://github.com/a/b/c",            # not owner/repo
])
def test_anything_that_is_not_a_repository_is_refused(bad):
    with pytest.raises(repo_ref.RepoRefError) as e:
        repo_ref.normalize(bad)
    assert e.value.kind == "shape"


def test_the_route_refuses_before_any_git_process_exists(tmp_path, monkeypatch):
    """The ordering IS the fix. A validator that runs after ``git clone`` has already been told the
    secret has protected nothing — git has forked, logged, and put it in its own error text."""
    started: list = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: started.append(a) or (_ for _ in ()).throw(AssertionError("git ran")))
    c = _client(tmp_path)
    r = c.post("/api/workspace/swap", json={"repo": PAT}, headers={"X-User-Id": "u_jane"})
    assert r.status_code == 422
    assert r.json()["detail"] == repo_ref.TOKEN_SENTENCE
    assert PAT not in r.text, "the response body must not echo the value back"
    assert started == [], "no subprocess may start for a refused repository"


@pytest.mark.parametrize("path,body", [
    ("/api/workspace/swap", {"repo": PAT}),
    ("/api/workspace/activate", {"repo": PAT}),
])
def test_every_desk_route_that_clones_refuses_a_token(tmp_path, path, body):
    c = _client(tmp_path)
    r = c.post(path, json=body, headers={"X-User-Id": "u_jane"})
    assert r.status_code == 422 and r.json()["detail"] == repo_ref.TOKEN_SENTENCE
    assert PAT not in r.text


def test_the_refusal_log_line_names_the_kind_and_never_the_value(tmp_path, caplog):
    c = _client(tmp_path)
    with caplog.at_level(logging.WARNING):
        c.post("/api/workspace/swap", json={"repo": PAT}, headers={"X-User-Id": "u_jane"})
    assert any("repository field refused" in r.getMessage() for r in caplog.records)
    assert PAT not in caplog.text


def test_the_mechanic_refuses_a_credential_even_when_the_route_is_bypassed(tmp_path):
    """Duplicated on purpose: the MCP, a future route, or a test can reach ``workspace_attach``
    directly, and a secret must not reach ``git`` by any of them."""
    with pytest.raises(repo_ref.RepoRefError):
        swap_workspace(tmp_path, "u_jane", PAT)
    with pytest.raises(repo_ref.RepoRefError):
        _git_clone(PAT, "main", tmp_path / "dest")


# ── F79 · nothing that reaches a person or a log may carry a secret ────────────────────────────────

@pytest.mark.parametrize("secret", [PAT, FINE, GLPAT])
def test_a_token_is_scrubbed_out_of_any_text(secret):
    out = redact(f"fatal: repository '{secret}' does not exist")
    assert secret not in out and MASK in out


def test_the_leak_that_happened_is_scrubbed_even_though_nobody_passed_the_token():
    """The exact 2026-09-02 string, through the exact call the clone path now makes. The old redactor
    took the token as an argument; here there is no argument to give it, which is the whole point."""
    out = redact(f"fatal: repository '{PAT}' does not exist")
    assert PAT not in out


def test_a_url_credential_and_an_unknown_long_secret_are_scrubbed():
    assert "hunter2" not in redact("https://someone:hunter2@github.com/acme/kg.git")
    unknown = "zz" + "Q7rT9wL2mK4nP6vB8xC1dF3gH5jK7lM9nO0p"      # 38 chars, no known prefix
    assert unknown not in redact(f"remote: rejected {unknown}")


def test_a_git_object_id_survives_because_diagnostics_matter():
    sha = "a" * 40
    assert sha in redact(f"fatal: bad object {sha}")
    assert "b" * 64 in redact("bad object " + "b" * 64)


def test_a_deploy_key_is_not_a_secret_and_must_survive():
    """The answer to a missing credential IS an ssh public key — a long base64 run the generic rule
    would eat. It survives because the scrubber runs at the SOURCE of git's text, never over the
    composed message; this asserts the composed message keeps the key."""
    from control_plane import deploy_keys, workspace_credentials as wc
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        prompt = wc.deploy_key_prompt(root, key="user-u_jane", repo_url="git@github.com:acme/kg.git")
        assert prompt["public_key"] in wc.prompt_sentence(prompt)
        assert deploy_keys.public_key(root, "user-u_jane") == prompt["public_key"]


def test_redact_is_idempotent_and_survives_odd_input():
    once = redact(f"a {PAT} b")
    assert redact(once) == once
    assert redact(None) == ""
    assert redact(CloneError(f"boom {PAT}")) .find(PAT) == -1


def test_a_clone_failure_carrying_a_token_reaches_the_route_masked(tmp_path, monkeypatch):
    """End to end through the route: git fails with the secret in its own stderr (the shape of the
    incident), and neither the response body nor the log carries it."""
    def _boom(cmd, *a, **k):
        raise subprocess.CalledProcessError(128, cmd, stderr=f"fatal: could not read Username for 'https://{PAT}@github.com'")
    monkeypatch.setattr(subprocess, "run", _boom)
    c = _client(tmp_path)
    r = c.post("/api/workspace/swap", json={"repo": "https://github.com/acme/private.git"},
               headers={"X-User-Id": "u_jane"})
    assert r.status_code == 502
    assert PAT not in r.text
    assert MASK in r.json()["detail"]


def test_the_friction_ledger_never_stores_a_credential():
    """A friction record is durable BY DESIGN ("nothing here expires"), so a secret pasted into one
    outlives the session, the fix and the rotation."""
    from shared import friction as fr
    rec = fr.normalize({"what_i_was_doing": f"attaching {PAT}",
                        "what_went_wrong": f"fatal: repository '{PAT}' does not exist",
                        "tool": "workspace_attach"})
    blob = repr(rec)
    assert PAT not in blob
    assert MASK in blob


def test_looks_like_token_is_narrow_enough_to_be_useful():
    assert looks_like_token(PAT) and looks_like_token(GLPAT)
    for ordinary in ("git@github.com:acme/kg.git", "https://github.com/acme/kg", "main", "acme/kg", ""):
        assert not looks_like_token(ordinary)


# ── the answer must survive the scrubbing (else the fix breaks the feature it protects) ───────────

#: A fingerprint whose base64 body contains NEITHER `+` NOR `/`. Those two characters fall outside the
#: generic rule's character class, so they split a long run — which means a fingerprint that happens to
#: contain one survives by accident. Roughly one in four does not. This is the one that does not.
PLAIN_FP = "SHA256:" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5nP7rT9v"


def test_a_deploy_key_card_renders_the_whole_public_key_and_its_fingerprint():
    """The card a person acts on carries an ssh public key and a SHA256 fingerprint — both long,
    opaque, base64-ish runs that the generic secret rule would happily eat. Both are PUBLIC by
    definition; masking them would leave a message reading "add this: «redacted»"."""
    key = ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHqZ0mQfZ5xW1kTn2vJ8sQpYbR3cL7dM4eF6gH9iJ0kL "
           "vexa-workspace-ws-acme")
    card = (f"This workspace has no credential for that repository yet ({PLAIN_FP}). "
            f"Add this public key as a deploy key with WRITE access, then say `done` when added:\n{key}")
    out = redact(card)
    assert key in out, "the public key must survive — it is the answer, not a secret"
    assert PLAIN_FP in out, "the fingerprint must survive, and not merely by luck"
    assert "say `done` when added" in out


def test_a_pat_in_the_same_message_is_still_masked():
    """The allow-list must not become a hole: a real credential sitting beside the key still goes."""
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHqZ0mQfZ5xW1kTn2vJ8sQpYbR3cL7dM4eF6gH9iJ0kL vexa"
    out = redact(f"could not read Username for 'https://{PAT}@github.com'\n{key}\n{PLAIN_FP}")
    assert PAT not in out and MASK in out
    assert key in out and PLAIN_FP in out


def test_an_md5_fingerprint_survives_too():
    md5 = "MD5:" + ":".join(["ab"] * 16)
    assert md5 in redact(f"key fingerprint {md5}")
