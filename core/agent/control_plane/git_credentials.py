"""git_credentials.py — a per-user, reusable GitHub token stored ONCE, server-side, and applied as the
FALLBACK credential for every git op (attach · publish · push · pull) across ALL of the user's repos.

**Fallback, not the primary.** The primary credential for loading an existing repo is now the
per-workspace DEPLOY KEY (:mod:`control_plane.deploy_keys`): the person adds OUR public key to THEIR
repository, and nothing of theirs travels to us. A PAT is broader than the job — one bearer credential
over every repo the account can reach — so it stays available for https remotes and for people who
prefer it, entered only in the terminal's token card and never in chat.

Security model:
  • **Encrypted at rest** — the value goes through :mod:`control_plane.secret_store` (one server-side
    key: ``VEXA_SECRETS_KEY``, or a ``0600`` key generated under the secrets root on first use). This
    module used to write the clear token to ``<root>/.secrets/<subject>.ghtoken``; a legacy plaintext
    file is still READ, and is re-sealed and unlinked on the next read (silent, one-way migration).
  • **Server-side only** — the store is a dot-dir the workspace scanners skip and NOT inside any
    workspace's git tree, so a token never lands in a commit. It is NEVER returned to the browser.
  • **Browser-isolated + masked** — a read for the UI returns only ``••••abcd`` (last-4), enough to
    confirm one is saved without disclosing it. The clear value never leaves the server after it is set.
  • **Log-redacted in use** — ``workspace_git_sync`` / ``workspace_attach`` scrub it from URLs and
    error text (P15); it is never written to ``.git/config``.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from control_plane import secret_store

log = logging.getLogger(__name__)

_SECRETS_DIRNAME = secret_store.SECRETS_DIRNAME   # kept as the legacy path's parent
_SUBJECT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")   # the secret name is subject-derived — path-safe
_NAME = "pat/{subject}"                                 # where the sealed token lives in the store


def _legacy_path(root: str | Path, subject: str) -> Optional[Path]:
    """``<root>/.secrets/<subject>.ghtoken`` — the PLAINTEXT file this module used to write. Read once
    more, then re-sealed and removed. Never written again."""
    if not subject or not _SUBJECT_RE.match(subject):
        return None
    return Path(root) / _SECRETS_DIRNAME / f"{subject}.ghtoken"


def _name(subject: str) -> Optional[str]:
    if not subject or not _SUBJECT_RE.match(subject):
        return None
    return _NAME.format(subject=subject)


def read_github_token(root: str | Path, subject: str) -> Optional[str]:
    """The caller's stored GitHub token, or ``None`` when unset/unreadable. On the git-op hot path — a
    missing secret is simply "no saved token", never an error.

    Reads the sealed secret first; falls back to a legacy plaintext file and MIGRATES it (seal, then
    unlink) so the clear value stops existing on disk the first time anyone touches the account."""
    name = _name(subject)
    if name is None:
        return None
    tok = secret_store.get(root, name)
    if tok:
        return tok
    legacy = _legacy_path(root, subject)
    if legacy is None:
        return None
    try:
        tok = legacy.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not tok:
        return None
    try:                                   # migrate: seal it, then drop the plaintext
        secret_store.put(root, name, tok)
        legacy.unlink()
        log.info("migrated a plaintext git token to the sealed store for subject=%s", subject)
    except (OSError, ValueError):
        log.debug("could not migrate the plaintext git token", exc_info=True)
    return tok


def set_github_token(root: str | Path, subject: str, token: Optional[str]) -> bool:
    """Save (or, with a falsy ``token``, CLEAR) the caller's reusable GitHub token. Returns True when a
    token is now stored, False when cleared/absent. Stored SEALED; any legacy plaintext file for the
    same subject is removed on the way through, clear or not."""
    name = _name(subject)
    if name is None:
        raise ValueError("invalid subject")
    legacy = _legacy_path(root, subject)
    if legacy is not None:
        try:
            legacy.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.debug("could not remove the legacy plaintext token file", exc_info=True)
    return secret_store.put(root, name, token)


def masked_github_token(root: str | Path, subject: str) -> Optional[str]:
    """A display-safe mask of the stored token (``••••`` + last-4), or ``None`` if none is saved. NEVER
    returns the clear value — this is the only shape the token may take on the way to the browser."""
    tok = read_github_token(root, subject)
    if not tok:
        return None
    return "••••" + (tok[-4:] if len(tok) >= 8 else "")
