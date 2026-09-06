"""workspace_credentials.py — the ONE place that answers "what does this workspace authenticate with?".

Three things use git credentials (attach · push · pull) across two lanes (a person's desk · a shared
group workspace) and two front doors (the HTTP routes · the control MCP). Before this module each of
those resolved its own credential, and the only shape any of them knew was "a PAT the caller passed in"
— which is why the answer to *"how do I load an existing repo?"* was *"paste a token"*.

The order here is the security posture, in code:

1. **The workspace's DEPLOY KEY**, for an ``ssh://`` / ``git@host:`` remote. Scoped to one repository,
   added by the person on GitHub's own settings page, and the only half that ever leaves this server is
   the public one. Nothing is embedded in a URL, so nothing can be persisted into ``.git/config``.
2. **The caller's saved PAT** (``git_credentials``), for an ``https://`` remote. Broader than the job
   and therefore the fallback, entered once in the terminal's token card.
3. **Nothing** — and that is a legitimate answer for a public repo, so it is not an error here. It
   becomes one only when git actually refuses, and then :func:`is_auth_failure` recognises the refusal
   and :func:`deploy_key_prompt` turns it into a STATE the person can act on ("add this key, say done")
   rather than a box asking for a secret.

A credential is never returned to a caller, never logged, and never accepted from chat text — see
:func:`credential_in_text`, which is what stops an agent from helpfully pasting one into a tool call.
"""
from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from control_plane import deploy_keys, git_credentials, secret_store

log = logging.getLogger(__name__)

# Token shapes we refuse to accept as ordinary text (GitHub PATs, and any URL carrying userinfo).
_TOKEN_SHAPES = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b")
_URL_CREDENTIAL = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
# git's ways of saying "you are not authorised", across https and ssh.
_AUTH_FAILURE = re.compile(
    r"authentication failed|could not read username|could not read password|terminal prompts disabled"
    r"|permission denied|publickey|access denied|403|repository not found|please make sure you have"
    r" the correct access rights",
    re.I,
)


@dataclass(frozen=True)
class Credential:
    """What one git op will authenticate with. ``token`` and ``ssh_env`` are for the op only — neither
    is ever put in a response body, a log line, or the workspace's git config."""

    kind: str                      # 'deploy-key' | 'token' | 'none'
    token: Optional[str] = None
    ssh_env: Optional[dict] = None

    @property
    def described(self) -> str:
        """The CAPABILITY-shaped description an agent may see: what kind of credential exists, never
        which one it is. 'deploy key set' is a fact about the workspace; a PAT's last-4 is not."""
        return {"deploy-key": "deploy key set", "token": "saved token",
                "none": "no credential"}[self.kind]


def credential_in_text(*values: Optional[str]) -> bool:
    """True when any argument LOOKS like a credential — a GitHub token, or a URL with userinfo in it.

    The tools this guards take no credential parameter at all, so a token can only arrive by being typed
    into a repo URL or a branch name. That is exactly the path this refuses: a secret pasted into a chat
    is in the transcript forever, in the model's context, and in whatever the transcript syncs to."""
    for v in values:
        s = (v or "").strip()
        if not s:
            continue
        if _TOKEN_SHAPES.search(s) or _URL_CREDENTIAL.search(s):
            return True
    return False


def is_auth_failure(message: str) -> bool:
    """Whether a (already token-redacted) git error is git refusing us, rather than a bad ref/URL."""
    return bool(_AUTH_FAILURE.search(message or ""))


@contextlib.contextmanager
def for_workspace(root: str | Path, *, key: str, repo_url: str = "", subject: str = "",
                  explicit_token: Optional[str] = None) -> Iterator[Credential]:
    """Resolve the credential for ONE git op on the workspace whose deploy-key name is ``key``.

    Deploy key for an ssh remote (the private half exists on disk only inside this ``with``), else the
    caller's saved PAT / an explicitly-passed one, else nothing. ``explicit_token`` is the terminal's
    per-call token field — the MCP path never supplies it."""
    token = (explicit_token or "").strip() or (git_credentials.read_github_token(root, subject) if subject else None)
    if deploy_keys.is_ssh_url(repo_url) or not repo_url:
        with deploy_keys.ssh_env(root, key) as env:
            if env is not None:
                yield Credential("deploy-key", None, env)
                return
            yield Credential("token", token, None) if token else Credential("none")
            return
    yield Credential("token", token, None) if token else Credential("none")


def home_capability(root: str | Path, *, key: str, remote: Optional[str], url: Optional[str],
                    subject: str = "") -> str:
    """One line an AGENT may be told about a workspace's git home: whether it has one, and what kind of
    credential is available for it. Never key material, never a token's last-4 — a capability, not a
    secret. ``"no git home"`` when the workspace was never attached or published."""
    if not url:
        return "no git home"
    bits = [f"origin {url}" if (remote or "origin") == "origin" else f"{remote} {url}"]
    if deploy_keys.exists(root, key):
        bits.append("deploy key set")
    elif subject and git_credentials.read_github_token(root, subject):
        bits.append("saved token")
    else:
        bits.append("no credential yet")
    return ", ".join(bits)


def deploy_key_prompt(root: str | Path, *, key: str, repo_url: str = "") -> dict:
    """Generate (or reuse) this workspace's deploy key and describe the ONE action that unblocks it.

    This is the answer to "how do we manage the secrets?": we do not take one. The person adds our
    public key to their repository as a **write** deploy key, and comes back and says so."""
    made = deploy_keys.ensure(root, key)
    add_at = deploy_keys.deploy_keys_url(repo_url)
    return {
        "public_key": made["public_key"],
        "fingerprint": made["fingerprint"],
        "add_at": add_at,
        "add_as": "a deploy key with WRITE access",
        "then": "say `done` when added",
    }


def prompt_sentence(prompt: dict) -> str:
    """The prompt as ONE block of plain text — what a tool result and an API error body both say."""
    where = f" at {prompt['add_at']}" if prompt.get("add_at") else " in the repository's Settings → Deploy keys"
    return (
        "This workspace has no credential for that repository yet. "
        f"Add this public key{where} as {prompt['add_as']}, then say `done` when added:\n"
        f"{prompt['public_key']}"
    )


def secrets_key_env(settings) -> str:
    """The operator's ``VEXA_SECRETS_KEY`` off the boot settings, or "" — kept here so no caller has to
    know that ``secret_store`` also reads the env var directly."""
    try:
        return settings.secrets_key.get_secret_value() if settings is not None else ""
    except AttributeError:
        return secret_store.configured_key()
