"""How an API key is held at rest (MIGRATION-0013).

A key's value crosses the wire once, in the mint answer. The row keeps only:

  * ``token_hash`` — the SHA-256 of the value, hex. Every lookup hashes what the caller presented
    and matches this column, the way workspace invites match ``invites.token_hash``.
  * ``token_hint`` — what a list may show: the ``vxa_<scope>_`` prefix and the last four characters.

``token`` is the plaintext column rows had before this. Nothing in identity writes it; the schema
convergence moves every value it still holds into ``token_hash`` and clears it, on every boot.

Which keys a person sees (MIGRATION-0014). The credential class (``api_tokens.kind``) says whom a
key is for:

  * ``key``     — a person's key: made on their key page, by the connect door when they press
    connect, or by an operator for them. Listed to the person, and theirs to revoke.
  * ``session`` — the terminal's signed-in browser (``POST /internal/sessions``).
  * ``system``  — a credential the product mints for itself, such as the key a sign-in cookie
    carries. It always has an end.

A person's key list shows ``key`` rows only. ``session`` and ``system`` rows are deleted once they
expire: nothing can authenticate with them, and nobody is shown them.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, or_

from .token_scope import TOKEN_PATTERN


def hash_token(value: str) -> str:
    """The stored form of a key: SHA-256 of its UTF-8 bytes, lowercase hex (64 characters)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def token_hint(value: str) -> str:
    """What a list shows for a key: ``vxa_bot_…AbCd``, or ``…AbCd`` for a key with no prefix."""
    m = TOKEN_PATTERN.match(value)
    prefix = f"vxa_{m.group(1)}_" if m else ""
    return f"{prefix}…{value[-4:]}"


def stored_fields(value: str) -> dict:
    """The columns a freshly minted key is written with: its hash and hint, never its value."""
    return {"token": None, "token_hash": hash_token(value), "token_hint": token_hint(value)}


def token_matches(model, value: str):
    """The WHERE clause that finds a presented key's row. A row another writer stored in plaintext
    since the last convergence still matches on ``token``; the next boot hashes it."""
    return or_(model.token_hash == hash_token(value), model.token == value)


KIND_KEY = "key"
KIND_SESSION = "session"
KIND_SYSTEM = "system"
#: The classes a person never sees, deleted once they expire.
HIDDEN_KINDS = (KIND_SESSION, KIND_SYSTEM)

#: The names the product's own sign-in surfaces give the key a browser cookie carries: the terminal
#: without sessions, the hosted webapp, and the old dashboard. A key minted under one of them is a
#: system key whatever the caller asked for, so a client that predates the class still mints a
#: hidden credential that ends.
SIGN_IN_KEY_NAMES = frozenset({"terminal-login", "webapp-login", "dashboard-login"})
#: The longest a system key lives, and its lifetime when the caller names none: the 30 days of the
#: cookies that carry sign-in keys.
SYSTEM_KEY_LIFETIME_SECONDS = 60 * 60 * 24 * 30


def class_for_mint(requested: Optional[str], name: Optional[str]) -> str:
    """The class an admin-door mint gets: what the caller asked for, and always ``system`` for a
    sign-in name. The admin door never mints a session."""
    if (name or "").strip().lower() in SIGN_IN_KEY_NAMES:
        return KIND_SYSTEM
    return KIND_SYSTEM if requested == KIND_SYSTEM else KIND_KEY


def system_expiry(requested_seconds: Optional[int], now: datetime) -> datetime:
    """A system key's end: the requested lifetime, never past SYSTEM_KEY_LIFETIME_SECONDS."""
    life = min(requested_seconds or SYSTEM_KEY_LIFETIME_SECONDS, SYSTEM_KEY_LIFETIME_SECONDS)
    return now + timedelta(seconds=life)


def expired_hidden(model, now: datetime):
    """The DELETE of every expired ``session`` and ``system`` row."""
    return delete(model).where(model.kind.in_(HIDDEN_KINDS), model.expires_at.isnot(None),
                               model.expires_at < now)
