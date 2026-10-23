"""How an API key is held at rest (MIGRATION-0013).

A key's value crosses the wire once, in the mint answer. The row keeps only:

  * ``token_hash`` — the SHA-256 of the value, hex. Every lookup hashes what the caller presented
    and matches this column, the way workspace invites match ``invites.token_hash``.
  * ``token_hint`` — what a list may show: the ``vxa_<scope>_`` prefix and the last four characters.

``token`` is the plaintext column rows had before this. Nothing in identity writes it; the schema
convergence moves every value it still holds into ``token_hash`` and clears it, on every boot.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import or_

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
