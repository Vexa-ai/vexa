"""git_credentials.py — a per-user, reusable GitHub token stored ONCE, server-side, and applied as the
fallback credential for every git op (attach · publish · push · pull) across ALL of the user's repos.

Security model (parity with the admin-api webhook secret — the level the owner chose):
  • **Server-side only** — the token is written under the workspaces store root at ``.secrets/<subject>.ghtoken``
    (a dot-dir the workspace scanners skip, and NOT inside any workspace's git tree, so it never lands in a
    commit). It is NEVER returned to the browser.
  • **Browser-isolated + masked** — a read for the UI returns only ``••••abcd`` (last-4), enough to confirm one
    is saved without disclosing it. The clear value never leaves the server after it is set.
  • **Log-redacted in use** — when applied to a git op the existing ``workspace_git_sync`` redaction (P15)
    scrubs it from URLs + error text; it is never written to ``.git/config``.
  • **Plaintext at rest** — like the webhook secret (no envelope encryption). Access is gated by the
    authenticated session; only the caller's own file is read/written. Use a MINIMALLY-scoped, rotatable
    fine-grained PAT — a stored PAT is password-equivalent and a full server compromise can use it.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_SECRETS_DIRNAME = ".secrets"          # dot-prefixed ⇒ skipped by every workspace scan; not a git tree
_SUBJECT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")   # the file name is subject-derived — keep it path-safe


def _token_path(root: str | Path, subject: str) -> Optional[Path]:
    """``<root>/.secrets/<subject>.ghtoken`` — or None for an unsafe/empty subject (never traverse)."""
    if not subject or not _SUBJECT_RE.match(subject):
        return None
    return Path(root) / _SECRETS_DIRNAME / f"{subject}.ghtoken"


def read_github_token(root: str | Path, subject: str) -> Optional[str]:
    """The caller's stored GitHub token, or ``None`` when unset/unreadable. On the git-op hot path — a
    missing file is simply "no saved token", never an error."""
    p = _token_path(root, subject)
    if p is None:
        return None
    try:
        tok = p.read_text(encoding="utf-8").strip()
        return tok or None
    except OSError:
        return None


def set_github_token(root: str | Path, subject: str, token: Optional[str]) -> bool:
    """Save (or, with a falsy ``token``, CLEAR) the caller's reusable GitHub token. Returns True when a
    token is now stored, False when cleared/absent. The file is created ``0600`` (owner-only)."""
    p = _token_path(root, subject)
    if p is None:
        raise ValueError("invalid subject")
    tok = (token or "").strip()
    if not tok:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    # Write then tighten perms to owner-only (the token is a bearer credential at rest).
    p.write_text(tok, encoding="utf-8")
    try:
        p.chmod(0o600)
        p.parent.chmod(0o700)
    except OSError:  # best-effort on filesystems that don't honor POSIX modes
        log.debug("could not chmod git-token store", exc_info=True)
    return True


def masked_github_token(root: str | Path, subject: str) -> Optional[str]:
    """A display-safe mask of the stored token (``••••`` + last-4), or ``None`` if none is saved. NEVER
    returns the clear value — this is the only shape the token may take on the way to the browser."""
    tok = read_github_token(root, subject)
    if not tok:
        return None
    return "••••" + (tok[-4:] if len(tok) >= 8 else "")
