"""deploy_keys.py — a per-workspace ed25519 DEPLOY KEY, so loading an existing repo never asks a human
to paste a secret into a chat.

The credential model, in one line: **the person adds our public key to their repo; nothing they own
ever travels to us.** A PAT is a bearer credential over *every* repo the user can reach, typed into a
box, and it lands in the same conversation transcript as everything else. A deploy key is scoped to one
repository, is added on GitHub's own settings page, and the half that leaves this server is the half
that is meant to be public.

  * ``ensure(root, workspace_key)`` generates the pair with ``ssh-keygen`` (no new Python dependency:
    the control-plane image installs ``openssh-client``, which git's ssh transport needs regardless),
    stores both halves in the credential broker (:mod:`git_secret_store`, ADR-0040), and returns only
    the public half. Without a configured broker it refuses before generating anything.
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

from control_plane import git_secret_store as secret_store
from shared import git_provider
from workspaces.shared import workspace_paths as wpaths

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


def public_key(root: str | Path, key: str) -> Optional[str]:
    """The stored public key (``ssh-ed25519 AAAA… vexa-workspace-<key>``), or None when none exists."""
    return secret_store.get(root, _PUB.format(key=key))


def exists(root: str | Path, key: str) -> bool:
    """Whether a USABLE deploy key is held (both halves readable)."""
    return (secret_store.get(root, _PRIV.format(key=key)) is not None
            and public_key(root, key) is not None)


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


#: WHO STORED EACH DEPLOY KEY (Vexa-ai/vexa#1783). The broker keeps the key and not its author, and a
#: key outlives the workspace that minted it — so the one fact a deleted source can no longer answer
#: ("whose credential is this?") is written here, beside the store, the moment the key is made. Names
#: and subject ids only, never key material. First writer wins.
OWNERS_DIRNAME = ".deploy-key-owners"


def _owner_rel(key: str) -> str:
    if not _KEY_RE.match(key or ""):
        raise ValueError("invalid workspace key")
    return f"{OWNERS_DIRNAME}/{key}.json"


def stored_by(root: str | Path, key: str) -> Optional[str]:
    """The subject who stored ``key``, or None when nobody recorded it (a key made before this record).
    Read through ``workspace_paths`` (no link followed at any step), like every path under the store."""
    import json
    try:
        raw = wpaths.read_text_inside(root, _owner_rel(key), max_bytes=4096)
        data = json.loads(raw) if raw else {}
    except (OSError, ValueError, wpaths.PathRefused):
        return None
    who = str((data or {}).get("subject") or "").strip() if isinstance(data, dict) else ""
    return who or None


def record_storer(root: str | Path, key: str, subject: str) -> bool:
    """Record ``subject`` as the one who stored ``key`` unless somebody already is (first writer
    wins: ``write_new_inside`` creates only when nothing is at the name). True when written."""
    import json
    if not str(subject or "").strip():
        return False
    try:
        return bool(wpaths.write_new_inside(root, _owner_rel(key),
                                            json.dumps({"key": key, "subject": str(subject)}), mode=0o600))
    except (OSError, wpaths.PathRefused):
        return False


def ensure(root: str | Path, key: str, *, subject: Optional[str] = None) -> dict:
    """Return this workspace's deploy key, generating the pair on first call. Idempotent — a second
    call returns the SAME public key, so the person is never asked to re-add one to their repo.

    ``{"public_key", "fingerprint", "created"}``. The private half is kept in the credential broker
    and is never part of the return value, any log line, or any API body."""
    if not _KEY_RE.match(key or ""):
        raise ValueError("invalid workspace key")
    if not secret_store.enabled():
        raise secret_store.GitStoreUnavailable(secret_store.UNCONFIGURED)
    if exists(root, key):
        pub = public_key(root, key)
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
    secret_store.put(root, _PRIV.format(key=key), priv)
    secret_store.put(root, _PUB.format(key=key), pub)
    log.info("deploy key generated for workspace-key=%s", key)  # name only (P15)
    if subject:
        record_storer(root, key, subject)
    return {"public_key": pub, "fingerprint": fingerprint(pub), "created": True}


def revoke(root: str | Path, key: str) -> bool:
    """Forget both halves. The public key stays on the user's repo until they remove it there — say so."""
    a = secret_store.delete(root, _PRIV.format(key=key))
    b = secret_store.delete(root, _PUB.format(key=key))
    return a or b


@contextlib.contextmanager
def ssh_env(root: str | Path, key: str) -> Iterator[Optional[dict]]:
    """Yield the env overrides that make ONE git op authenticate as this workspace's deploy key, or
    ``None`` when no key is stored (the caller then falls back to a PAT / anonymous).

    The private half is written to a ``0600`` file inside a ``0700`` temp dir and removed on exit, so
    it exists on disk only for the length of the op. ``IdentitiesOnly=yes`` prevents the host's own
    agent/keys leaking in; ``BatchMode=yes`` keeps a bad key a loud failure rather than a hung prompt.
    ``StrictHostKeyChecking=accept-new`` trusts a host's key on first sight and pins it thereafter —
    the same posture as a fresh developer machine, and required because the store has no known_hosts."""
    priv = secret_store.get(root, _PRIV.format(key=key))
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
        wpaths.remove_tree(td, ignore_errors=True)


def is_ssh_url(url: str) -> bool:
    """``git@host:org/repo`` and ``ssh://…`` are deploy-key territory; ``https://…`` is PAT territory."""
    u = (url or "").strip()
    return u.startswith("ssh://") or bool(re.match(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:", u))


def deploy_keys_url(repo_url: str) -> Optional[str]:
    """The settings page where a public key is added as a deploy key, derived from the repo URL by the
    configured provider it is on (``shared.git_provider``): GitHub's ``/settings/keys``, GitLab's
    repository settings. None for any other host — we say the state, we never guess a link."""
    u = (repo_url or "").strip().rstrip("/")
    provider = git_provider.for_url(u)
    if provider is None:
        return None
    path = provider.repo_path(u)
    return provider.deploy_keys_url(path) if path else None
