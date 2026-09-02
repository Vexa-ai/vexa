"""deploy_keys.py — a per-workspace ed25519 DEPLOY KEY, so loading an existing repo never asks a human
to paste a secret into a chat.

The credential model, in one line: **the person adds our public key to their repo; nothing they own
ever travels to us.** A PAT is a bearer credential over *every* repo the user can reach, typed into a
box, and it lands in the same conversation transcript as everything else. A deploy key is scoped to one
repository, is added on GitHub's own settings page, and the half that leaves this server is the half
that is meant to be public.

  * ``ensure(root, workspace_key)`` generates the pair with ``ssh-keygen`` (no new Python dependency:
    the control-plane image installs ``openssh-client``, which git's ssh transport needs regardless),
    stores the PRIVATE half in the encrypted :mod:`secret_store`, and returns only the public half.
  * ``public_key`` / ``fingerprint`` are the only shapes a key takes on the way out. There is no read
    path for the private half — ``ssh_env`` materializes it into a ``0600`` file inside a ``0700``
    private directory for the duration of ONE git op and unlinks it on the way out.
  * ``ssh_env(root, workspace_key)`` yields the ``GIT_SSH_COMMAND`` overrides to merge into
    ``shared.gitenv.scrubbed_git_env`` — ``IdentitiesOnly=yes`` so the agent's ambient keys can never
    silently authenticate a workspace they were not granted, and ``BatchMode=yes`` so a missing or
    unaccepted key FAILS LOUD instead of hanging on a passphrase/host prompt.

Nothing here logs key material; the only thing written to the log is the workspace key's name.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator, Optional

from control_plane import secret_store

log = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
_COMMENT = "vexa-workspace"
# Where the private half lives inside the encrypted store (one secret per workspace).
_PRIV = "deploy/{key}.priv"
_PUB = "deploy/{key}.pub"


class DeployKeyError(RuntimeError):
    """Generation failed (no ``ssh-keygen`` in the image, or it errored). Never carries key material."""


def workspace_key(*, subject: str = "", workspace_id: str = "") -> str:
    """The store name for a workspace's deploy key. A SHARED workspace keys by its id (the key belongs
    to the workspace, so every member's attach/pull uses the same one); a person's own desk keys by
    subject. Path-safe by construction — an unsafe id raises rather than escaping the store."""
    raw = f"ws-{workspace_id}" if (workspace_id or "").strip() else f"user-{subject}"
    if not _KEY_RE.match(raw):
        raise ValueError("invalid workspace key")
    return raw


def public_key(root: str | Path, key: str, *, key_env: str = "") -> Optional[str]:
    """The stored public key (``ssh-ed25519 AAAA… vexa-workspace-<key>``), or None when none exists."""
    return secret_store.get(root, _PUB.format(key=key), key_env=key_env)


def exists(root: str | Path, key: str, *, key_env: str = "") -> bool:
    """Whether a USABLE deploy key is held (both halves readable)."""
    return (secret_store.get(root, _PRIV.format(key=key), key_env=key_env) is not None
            and public_key(root, key, key_env=key_env) is not None)


def fingerprint(pub: Optional[str]) -> Optional[str]:
    """``SHA256:…`` for a public key, computed locally. None when there is no key or no ssh-keygen."""
    if not pub:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "k.pub"
            f.write_text(pub if pub.endswith("\n") else pub + "\n", encoding="utf-8")
            out = subprocess.run(["ssh-keygen", "-lf", str(f)], capture_output=True, text=True)
        parts = out.stdout.split()
        return next((p for p in parts if p.startswith("SHA256:")), None)
    except (OSError, subprocess.SubprocessError):
        return None


def ensure(root: str | Path, key: str, *, key_env: str = "") -> dict:
    """Return this workspace's deploy key, generating the pair on first call. Idempotent — a second
    call returns the SAME public key, so the person is never asked to re-add one to their repo.

    ``{"public_key", "fingerprint", "created"}``. The private half is stored encrypted and is never
    part of the return value, any log line, or any API body."""
    if not _KEY_RE.match(key or ""):
        raise ValueError("invalid workspace key")
    if exists(root, key, key_env=key_env):
        pub = public_key(root, key, key_env=key_env)
        return {"public_key": pub, "fingerprint": fingerprint(pub), "created": False}
    if shutil.which("ssh-keygen") is None:
        raise DeployKeyError("ssh-keygen is not available on this server — a deploy key cannot be generated")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "id_ed25519"
        proc = subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{_COMMENT}-{key}", "-f", str(path)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not path.exists():
            raise DeployKeyError(f"could not generate a deploy key: {(proc.stderr or '').strip()[:200]}")
        priv = path.read_text(encoding="utf-8")
        pub = path.with_suffix(".pub").read_text(encoding="utf-8").strip()
    secret_store.put(root, _PRIV.format(key=key), priv, key_env=key_env)
    secret_store.put(root, _PUB.format(key=key), pub, key_env=key_env)
    log.info("deploy key generated for workspace-key=%s", key)  # name only (P15)
    return {"public_key": pub, "fingerprint": fingerprint(pub), "created": True}


def revoke(root: str | Path, key: str) -> bool:
    """Forget both halves. The public key stays on the user's repo until they remove it there — say so."""
    a = secret_store.delete(root, _PRIV.format(key=key))
    b = secret_store.delete(root, _PUB.format(key=key))
    return a or b


@contextlib.contextmanager
def ssh_env(root: str | Path, key: str, *, key_env: str = "") -> Iterator[Optional[dict]]:
    """Yield the env overrides that make ONE git op authenticate as this workspace's deploy key, or
    ``None`` when no key is stored (the caller then falls back to a PAT / anonymous).

    The private half is written to a ``0600`` file inside a ``0700`` temp dir and removed on exit, so
    it exists on disk only for the length of the op. ``IdentitiesOnly=yes`` prevents the host's own
    agent/keys leaking in; ``BatchMode=yes`` keeps a bad key a loud failure rather than a hung prompt.
    ``StrictHostKeyChecking=accept-new`` trusts a host's key on first sight and pins it thereafter —
    the same posture as a fresh developer machine, and required because the store has no known_hosts."""
    priv = secret_store.get(root, _PRIV.format(key=key), key_env=key_env)
    if not priv:
        yield None
        return
    td = tempfile.mkdtemp(prefix="vexa-dk-")
    try:
        os.chmod(td, 0o700)
        kf = Path(td) / "id_ed25519"
        kf.write_text(priv if priv.endswith("\n") else priv + "\n", encoding="utf-8")
        os.chmod(kf, 0o600)
        known = Path(td) / "known_hosts"
        known.touch()
        os.chmod(known, 0o600)
        cmd = (
            f"ssh -i {kf} -o IdentitiesOnly=yes -o BatchMode=yes "
            f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={known}"
        )
        yield {"GIT_SSH_COMMAND": cmd}
    finally:
        shutil.rmtree(td, ignore_errors=True)


def is_ssh_url(url: str) -> bool:
    """``git@host:org/repo`` and ``ssh://…`` are deploy-key territory; ``https://…`` is PAT territory."""
    u = (url or "").strip()
    return u.startswith("ssh://") or bool(re.match(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:", u))


def deploy_keys_url(repo_url: str) -> Optional[str]:
    """The GitHub settings page where a public key is added as a deploy key, derived from the repo URL.
    None for a host we cannot map — we say the state, we never guess a link."""
    u = (repo_url or "").strip().rstrip("/")
    u = re.sub(r"\.git$", "", u)
    m = re.match(r"^(?:https?://|ssh://)?(?:[^@/]+@)?github\.com[:/]+([^/]+)/([^/]+)$", u)
    if not m:
        return None
    return f"https://github.com/{m.group(1)}/{m.group(2)}/settings/keys"
