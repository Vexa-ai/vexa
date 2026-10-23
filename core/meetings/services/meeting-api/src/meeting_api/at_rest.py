"""Secrets Vexa must present onward, sealed at rest (MIGRATION-0015).

A webhook signing secret cannot be hashed: meeting-api signs every outgoing webhook with it. So it
is stored sealed with AES-256-GCM under a key that lives in a file mounted apart from the database
(``VEXA_AT_REST_KEY_FILE``: 64 hex characters, ``openssl rand -hex 32``), the same custody as the
credential broker's store key. A database or backup without that file yields no secret.

  sealed form   ``vxe1:`` + base64url(nonce[12] || ciphertext+tag), AAD = ``vexa-at-rest.v1\\0<purpose>``

``seal`` and ``unseal`` are the only two doors. A value without the ``vxe1:`` prefix is a legacy
plaintext value and is answered as it is, so rows written before this keep working until the
convergence seals them. With no key configured nothing is sealed (and ``key_configured()`` says
so, for the boot warning); a sealed value read without the key is unreadable and answers None.

This file is vendored byte for byte into admin-api (``admin_api/at_rest.py``) and meeting-api
(``meeting_api/at_rest.py``); both read the same key file.
"""
from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path
from typing import Optional

KEY_FILE_ENV = "VEXA_AT_REST_KEY_FILE"
PREFIX = "vxe1:"
#: The purpose a per-user webhook signing secret is sealed under (users.data, meetings.data, the
#: webhook retry queue): a value sealed for one purpose does not open as another.
WEBHOOK_SECRET_PURPOSE = "webhook_secret"
_AAD = b"vexa-at-rest.v1\0"


def _load_key() -> Optional[bytes]:
    path = (os.environ.get(KEY_FILE_ENV) or "").strip()
    if not path:
        return None
    try:
        key = bytes.fromhex(Path(path).read_text().strip())
    except (OSError, ValueError):
        raise RuntimeError(f"{KEY_FILE_ENV} does not hold 64 hex characters") from None
    if len(key) != 32:
        raise RuntimeError(f"{KEY_FILE_ENV} does not hold 64 hex characters")
    return key


def key_configured() -> bool:
    return _load_key() is not None


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def seal(value: Optional[str], purpose: str) -> Optional[str]:
    """The stored form of a secret: sealed when a key is configured, as given otherwise."""
    if not value or is_sealed(value):
        return value
    key = _load_key()
    if key is None:
        return value
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    nonce = secrets.token_bytes(12)
    blob = nonce + AESGCM(key).encrypt(nonce, value.encode("utf-8"), _AAD + purpose.encode())
    return PREFIX + base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")


def unseal(value: Optional[str], purpose: str) -> Optional[str]:
    """The secret itself: a sealed value opened, a legacy plaintext value as it is, None when a
    sealed value cannot be opened (no key, another key, or tampered)."""
    if not value or not is_sealed(value):
        return value
    key = _load_key()
    if key is None:
        return None
    from cryptography.exceptions import InvalidTag  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    body = value[len(PREFIX):]
    try:
        blob = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        return AESGCM(key).decrypt(blob[:12], blob[12:], _AAD + purpose.encode()).decode("utf-8")
    except (InvalidTag, ValueError):
        return None
