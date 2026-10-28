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
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, or_, text

from .token_scope import TOKEN_PATTERN


def hash_token(value: str) -> str:
    """The stored form of a key: SHA-256 of its UTF-8 bytes, lowercase hex (64 characters)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_TRANSIT_PATTERN = re.compile(r"^(vxt_[a-z][a-z0-9-]*_)")


def token_hint(value: str) -> str:
    """What a list shows for a key: ``vxa_bot_…AbCd``, ``vxt_transcription_…AbCd`` for a transit
    key, or ``…AbCd`` for a key with no prefix."""
    m = TOKEN_PATTERN.match(value)
    t = _TRANSIT_PATTERN.match(value)
    prefix = f"vxa_{m.group(1)}_" if m else (t.group(1) if t else "")
    return f"{prefix}…{value[-4:]}"


def stored_fields(value: str) -> dict:
    """The columns a freshly minted key is written with: its hash and hint, never its value."""
    return {"token": None, "token_hash": hash_token(value), "token_hint": token_hint(value)}


def token_matches(model, value: str):
    """The WHERE clause that finds a presented key's row. A row another writer stored in plaintext
    still matches on ``token``; the lookup that finds it hashes it (``hash_in_place``)."""
    return or_(model.token_hash == hash_token(value), model.token == value)


_HASH_IN_PLACE = text(
    "UPDATE api_tokens SET token_hash = :h, token_hint = :hint, token = NULL "
    "WHERE id = (SELECT id FROM api_tokens WHERE id = :id AND token IS NOT NULL FOR UPDATE SKIP LOCKED)")
_HASH_BOUNDS = ("SET LOCAL lock_timeout = '100ms'", "SET LOCAL synchronous_commit = off")


async def hash_in_place(db, row, value: str) -> None:
    """A row found by its plaintext ``token`` (a writer that does not hash yet) is hashed and cleared
    by the lookup that found it, not left until the next boot. It never waits for a lock and never
    fails the answer: a row it cannot take now is taken by the next lookup."""
    if getattr(row, "token", None) is None:
        return
    try:
        postgres = db.get_bind().dialect.name == "postgresql"
    except Exception:  # noqa: BLE001 — a session with no bind (a test fake) is not Postgres
        postgres = False
    try:
        if postgres:
            for stmt in _HASH_BOUNDS:
                await db.execute(text(stmt))
            await db.execute(_HASH_IN_PLACE, {"h": hash_token(value), "hint": token_hint(value), "id": row.id})
        else:
            row.token, row.token_hash, row.token_hint = None, hash_token(value), token_hint(value)
        await db.commit()
    except Exception:  # noqa: BLE001 — advisory: the key already answered
        rollback = getattr(db, "rollback", None)
        if rollback is not None:
            await rollback()


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


#: The scopes flows minted its unnamed 15-minute keys with (flows_steps/common.py `user_api_key`,
#: before 0.13.4 removed it), in the order it sent them: MIGRATION-0014 recognises its leftovers by
#: this exact array.
FLOWS_MINTED_SCOPES = ("bot", "browser", "tx")


class ReservedKeyName(ValueError):
    """A person's key may not carry a sign-in surface's name."""


def is_reserved_name(name: Optional[str]) -> bool:
    return (name or "").strip().lower() in SIGN_IN_KEY_NAMES


def class_for_mint(requested: Optional[str], name: Optional[str]) -> str:
    """The class an admin-door mint gets. A sign-in name is always ``system``: a caller that names
    no class (a sign-in surface from before the class existed) gets that, and one that asks for a
    person's ``key`` under a sign-in name is refused (ReservedKeyName) rather than silently hidden.
    The admin door never mints a session."""
    if is_reserved_name(name):
        if requested == KIND_KEY:
            raise ReservedKeyName(f"the key name {name!r} is reserved for sign-in")
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
