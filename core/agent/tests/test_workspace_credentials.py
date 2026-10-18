"""The credential model for loading an existing repo — "how do we want to manage the secrets?"

The answer is that we do not take one, and these tests hold each half of that:

  * the ORDER — a workspace's deploy key first for an ssh remote, the saved PAT only as the https
    fallback, and "nothing" as a legitimate state rather than an error;
  * what an AGENT may be told — a capability line ("deploy key set"), never key material and never a
    token's last-4;
  * the REFUSAL — a credential smuggled into a chat-facing tool argument is rejected.

The three assertions that PARSE THE RIG live beside it, in `deploy/dogfood/rig/tests/`: a test under
`core/` that reads a file under `deploy/` makes the domains and the lane inseparable.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from control_plane import deploy_keys, git_credentials
from control_plane import workspace_credentials as wc



@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    from control_plane import secret_store
    monkeypatch.delenv(secret_store.ENV_KEY_NAME, raising=False)


# ── which credential, and in what order ────────────────────────────────────────────────────────────

def test_an_ssh_repo_uses_the_workspace_deploy_key_even_when_a_token_is_saved(tmp_path):
    git_credentials.set_github_token(tmp_path, "u_jane", "ghp_saved_fallback_token")
    key = deploy_keys.workspace_key(workspace_id="grp-1")
    deploy_keys.ensure(tmp_path, key)
    with wc.for_workspace(tmp_path, key=key, repo_url="git@github.com:acme/kg.git", subject="u_jane") as cred:
        assert cred.kind == "deploy-key"
        assert cred.token is None, "an ssh remote must never carry a PAT"
        assert "GIT_SSH_COMMAND" in cred.ssh_env


def test_an_https_repo_falls_back_to_the_saved_token(tmp_path):
    git_credentials.set_github_token(tmp_path, "u_jane", "ghp_saved_fallback_token")
    key = deploy_keys.workspace_key(workspace_id="grp-2")
    deploy_keys.ensure(tmp_path, key)     # a key exists and is still not used — the URL decides
    with wc.for_workspace(tmp_path, key=key, repo_url="https://github.com/acme/kg.git", subject="u_jane") as cred:
        assert cred.kind == "token" and cred.token == "ghp_saved_fallback_token"
        assert cred.ssh_env is None


def test_no_credential_is_a_state_not_an_error(tmp_path):
    key = deploy_keys.workspace_key(subject="u_new")
    with wc.for_workspace(tmp_path, key=key, repo_url="https://github.com/acme/public.git", subject="u_new") as cred:
        assert cred.kind == "none" and cred.token is None and cred.ssh_env is None


def test_an_explicit_token_beats_the_saved_one_and_is_not_persisted(tmp_path):
    git_credentials.set_github_token(tmp_path, "u_jane", "ghp_saved_fallback_token")
    key = deploy_keys.workspace_key(subject="u_jane")
    with wc.for_workspace(tmp_path, key=key, repo_url="https://github.com/a/b.git",
                          subject="u_jane", explicit_token="ghp_one_off_from_the_form") as cred:
        assert cred.token == "ghp_one_off_from_the_form"
    assert git_credentials.read_github_token(tmp_path, "u_jane") == "ghp_saved_fallback_token"


# ── what an agent may be told ──────────────────────────────────────────────────────────────────────

def test_the_capability_line_says_the_kind_and_never_the_value(tmp_path):
    key = deploy_keys.workspace_key(workspace_id="grp-3")
    assert wc.home_capability(tmp_path, key=key, remote=None, url=None) == "no git home"

    line = wc.home_capability(tmp_path, key=key, remote="origin", url="https://github.com/acme/kg")
    assert line == "origin https://github.com/acme/kg, no credential yet"

    git_credentials.set_github_token(tmp_path, "u_jane", "ghp_ABCDEFGH1234wxyz")
    line = wc.home_capability(tmp_path, key=key, remote="origin", url="https://github.com/acme/kg",
                              subject="u_jane")
    assert line.endswith("saved token")
    assert "wxyz" not in line, "not even the last-4 — a capability, not a hint at the secret"

    deploy_keys.ensure(tmp_path, key)
    line = wc.home_capability(tmp_path, key=key, remote="origin", url="https://github.com/acme/kg",
                              subject="u_jane")
    assert line.endswith("deploy key set")
    assert "ssh-ed25519" not in line


def test_the_prompt_is_an_action_not_a_request_for_a_secret(tmp_path):
    key = deploy_keys.workspace_key(workspace_id="grp-4")
    prompt = wc.deploy_key_prompt(tmp_path, key=key, repo_url="git@github.com:acme/kg.git")
    assert prompt["public_key"].startswith("ssh-ed25519 ")
    assert prompt["add_at"] == "https://github.com/acme/kg/settings/keys"
    assert prompt["then"] == "say `done` when added"
    sentence = wc.prompt_sentence(prompt)
    assert "add this public key" in sentence.lower()
    assert "token" not in sentence.lower(), "the sentence must never ask for a token"


def test_an_unmappable_host_states_the_place_without_guessing_a_url(tmp_path):
    key = deploy_keys.workspace_key(workspace_id="grp-5")
    prompt = wc.deploy_key_prompt(tmp_path, key=key, repo_url="https://git.internal.acme/kg.git")
    assert prompt["add_at"] is None
    assert "Settings → Deploy keys" in wc.prompt_sentence(prompt)


# ── the refusal ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "ghp_16CharactersOrMoreAAAA",
    "github_pat_11ABCDEFG0aaaaaaaaaaaaa_bbbb",
    "https://someone:hunter2@github.com/acme/kg.git",
    "https://ghs_aaaaaaaaaaaaaaaaaaaa@github.com/acme/kg.git",
])
def test_a_credential_in_free_text_is_recognised(text):
    assert wc.credential_in_text(text) is True


@pytest.mark.parametrize("text", [
    "", None, "git@github.com:acme/kg.git", "https://github.com/acme/kg.git", "main", "grp-a1b2c3",
])
def test_ordinary_arguments_are_not_mistaken_for_credentials(text):
    assert wc.credential_in_text(text) is False


@pytest.mark.parametrize("message,auth", [
    ("fatal: Authentication failed for 'https://github.com/acme/kg.git/'", True),
    ("git@github.com: Permission denied (publickey).", True),
    ("could not read Username for 'https://github.com': terminal prompts disabled", True),
    ("remote: Repository not found.", True),
    ("fatal: couldn't find remote ref nosuchbranch", False),
    ("cannot fast-forward — this workspace has local commits the remote doesn't have.", False),
])
def test_only_an_authorisation_refusal_triggers_the_deploy_key_answer(message, auth):
    assert wc.is_auth_failure(message) is auth


# The three assertions that read the RIG's source moved to `deploy/dogfood/rig/tests/` — beside
# the file they parse. A test under `core/` that reads a file under `deploy/` makes the domains
# and the lane inseparable: merging `core/` would drag the rig with it.
