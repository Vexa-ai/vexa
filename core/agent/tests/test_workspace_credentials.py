"""The credential model for loading an existing repo — "how do we want to manage the secrets?"

The answer is that we do not take one, and these tests hold each half of that:

  * the ORDER — a workspace's deploy key first for an ssh remote, the saved PAT only as the https
    fallback, and "nothing" as a legitimate state rather than an error;
  * what an AGENT may be told — a capability line ("deploy key set"), never key material and never a
    token's last-4;
  * the REFUSAL — a credential smuggled into a chat-facing tool argument is rejected, and the rig's
    workspace tools are checked (by parsing the rig itself) to have no credential parameter to smuggle
    it into in the first place.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from control_plane import deploy_keys, git_credentials
from control_plane import workspace_credentials as wc

# The rig became `core/mcp`; its workspace verbs are one module now, and the refusal helper is
# `shaping.refuse_credentials`. Same two rules, same parse-the-shipped-code method.
MCP_WS = Path(__file__).resolve().parents[3] / "core/mcp/src/vexa_mcp/tools/workspaces.py"
MCP_SHAPING = Path(__file__).resolve().parents[3] / "core/mcp/src/vexa_mcp/shaping.py"


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


# ── the rig's own tools: nowhere to put a secret, and a refusal on the way in ──────────────────────

def _rig_functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(MCP_WS.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_the_rig_workspace_tools_have_no_credential_parameter():
    """`token` on a rig tool is the caller's VEXA session token (the identity convention every tool in
    that file uses) — there must be no git-credential parameter beside it for an agent to fill in."""
    banned = {"pat", "github_token", "repo_token", "password", "ssh_key", "private_key", "credential"}
    fns = _rig_functions()
    for name in ("workspace_attach", "workspace_push", "workspace_pull"):
        assert name in fns, f"{name} is missing from the MCP"
        params = {a.arg for a in fns[name].args.args}
        assert not (params & banned), f"{name} exposes a credential parameter: {params & banned}"


def test_the_rig_refuses_a_credential_smuggled_into_an_argument():
    """The rig's detector, executed as it actually ships (parsed out of the file, so this cannot drift
    from the deployed code) — a PAT typed into the repo URL is refused with a sentence that points at
    the key instead of asking again."""
    tree = ast.parse(MCP_SHAPING.read_text(encoding="utf-8"))
    wanted = ("CREDENTIAL_REFUSAL", "refuse_credentials")
    picked = [n for n in tree.body
              if (isinstance(n, ast.FunctionDef) and n.name in wanted)
              or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in wanted for t in n.targets))]
    assert len(picked) == 2, "the rig's refusal helper moved — this test must follow it"
    ns: dict = {}
    exec(compile(ast.Module(body=picked, type_ignores=[]), str(MCP_SHAPING), "exec"), ns)  # noqa: S102

    refuse = ns["refuse_credentials"]
    assert refuse("https://ghp_AAAAAAAAAAAAAAAAAAAA@github.com/acme/kg.git")
    assert refuse("git@github.com:acme/kg.git", "main", "", "ghp_BBBBBBBBBBBBBBBBBBBB")
    assert refuse("https://user:secret@github.com/acme/kg.git")
    assert refuse("git@github.com:acme/kg.git", "main", "grp-a1b2c3", "") == ""
    assert "will not take a token in chat" in ns["CREDENTIAL_REFUSAL"]


def test_the_rig_calls_that_refusal_before_it_does_anything():
    src = MCP_WS.read_text(encoding="utf-8")
    fns = _rig_functions()
    for name in ("workspace_attach", "workspace_push", "workspace_pull"):
        body = ast.get_source_segment(src, fns[name]) or ""
        assert "refuse_credentials(" in body, f"{name} does not screen its arguments"
