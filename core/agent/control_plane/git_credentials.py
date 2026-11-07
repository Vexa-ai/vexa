"""git_credentials.py — a per-user, reusable token PER GIT PROVIDER, stored ONCE, server-side, and
applied as the FALLBACK credential for every git op (attach · publish · push · pull) on that provider.

**One store, one name per provider.** The built-in GitHub provider keeps the name it always had,
``pat/<subject>``; any other configured provider (``shared.git_provider``) is ``pat/<subject>/<name>``.
A saved token is read only for a URL on its own provider (``shared.token_destination``).

**Fallback, not the primary.** The primary credential for loading an existing repo is now the
per-workspace DEPLOY KEY (:mod:`control_plane.deploy_keys`): the person adds OUR public key to THEIR
repository, and nothing of theirs travels to us. A PAT is broader than the job — one bearer credential
over every repo the account can reach — so it stays available for https remotes and for people who
prefer it, entered only in the terminal's token card and never in chat.

Security model:
  • **Kept in the credential broker** — :mod:`control_plane.git_secret_store` writes it to the broker
    (ADR-0040) as the ``git`` role, for the person the gateway signed for. agent-api holds no copy at
    rest. A token from agent-api's retired local store (sealed, or the older plaintext
    ``<root>/.secrets/<subject>.ghtoken``) moves into the broker on first use and is then removed.
  • **No broker, no saving** — without ``VEXA_GIT_STORE_BROKER_URL`` a save is a typed refusal
    (``GitStoreUnavailable``), never a token parked on agent-api's volume.
  • **Browser-isolated + masked** — a read for the UI returns only ``••••abcd`` (last-4), enough to
    confirm one is saved without disclosing it. The clear value never leaves the server after it is set.
  • **Log-redacted in use** — ``workspace_git_sync`` / ``workspace_attach`` scrub it from URLs and
    error text (P15); it is never written to ``.git/config``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from control_plane import git_secret_store as secret_store
from shared import git_provider

_SUBJECT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")   # the secret name is subject-derived — path-safe
_NAME = "pat/{subject}"


def secret_name(subject: str, provider: Optional[git_provider.GitProvider] = None) -> Optional[str]:
    """The broker name of ``subject``'s saved token for ``provider`` (the default provider when None)."""
    if not subject or not _SUBJECT_RE.match(subject):
        return None
    if provider is None or provider.name == git_provider.DEFAULT_NAME:
        return _NAME.format(subject=subject)
    return f"{_NAME.format(subject=subject)}/{provider.name}"


def _name(subject: str) -> Optional[str]:
    return secret_name(subject)


def _is_default(provider: Optional[git_provider.GitProvider]) -> bool:
    return provider is None or provider.name == git_provider.DEFAULT_NAME


def read_token(root: str | Path, subject: str, provider: Optional[git_provider.GitProvider] = None) -> Optional[str]:
    """The caller's saved token for ``provider`` (the default provider when None), or ``None`` when
    none is saved. Raises ``GitStoreUnavailable`` when the store cannot answer — a saved token that
    cannot be fetched is never read as "no token"."""
    if _is_default(provider):
        return read_github_token(root, subject)
    name = secret_name(subject, provider)
    return secret_store.get(root, name) if name else None


def set_token(root: str | Path, subject: str, token: Optional[str],
              provider: Optional[git_provider.GitProvider] = None) -> bool:
    """Save (or, with a falsy ``token``, CLEAR) the caller's token for ``provider``. True when one is
    now stored, False when cleared."""
    if _is_default(provider):
        return set_github_token(root, subject, token)
    name = secret_name(subject, provider)
    if name is None:
        raise ValueError("invalid subject")
    return secret_store.put(root, name, token)


def masked(token: Optional[str]) -> Optional[str]:
    """``••••`` + last-4 of ``token`` — the only shape a saved token takes on the way to the browser."""
    if not token:
        return None
    return "••••" + (token[-4:] if len(token) >= 8 else "")


def masked_token(root: str | Path, subject: str, provider: Optional[git_provider.GitProvider] = None) -> Optional[str]:
    """A display-safe mask of the saved token for ``provider``; NEVER the clear value."""
    return masked(read_token(root, subject, provider))


# The default (built-in GitHub) provider, under the names every caller before the port used. Its
# broker name is unchanged: ``pat/<subject>``.
def read_github_token(root: str | Path, subject: str) -> Optional[str]:
    """The caller's saved token for the default provider, or ``None`` when none is saved."""
    name = secret_name(subject)
    return secret_store.get(root, name) if name else None


def set_github_token(root: str | Path, subject: str, token: Optional[str]) -> bool:
    """Save (or CLEAR) the caller's token for the default provider."""
    name = secret_name(subject)
    if name is None:
        raise ValueError("invalid subject")
    return secret_store.put(root, name, token)


def masked_github_token(root: str | Path, subject: str) -> Optional[str]:
    return masked(read_github_token(root, subject))
