"""Secrets Vexa must present onward, sealed at rest (MIGRATION-0015).

A webhook signing secret cannot be hashed: meeting-api signs every outgoing webhook with it. So it
is stored sealed with AES-256-GCM under a key that lives in a file mounted apart from the database
(``VEXA_AT_REST_KEY_FILE``: 64 hex characters, ``openssl rand -hex 32``), the same custody as the
credential broker's store key. A database or backup without that file yields no secret.

  sealed form   ``vxe1:`` + base64url(nonce[12] || ciphertext+tag), AAD = ``vexa-at-rest.v1\\0<purpose>``

``seal`` and ``unseal`` are the only two doors, and both need the key:

  * ``load_key()`` reads and checks the file once; the service calls it at boot and refuses to
    start without a usable key (``AtRestKeyUnavailable``). Later calls reuse the key read.
  * ``seal`` never writes a secret unsealed: with no key it raises.
  * ``unseal`` answers a legacy plaintext value (no ``vxe1:`` prefix) as it is, so rows written
    before the convergence sealed them keep working, and raises ``SealedSecretUnreadable`` for a
    sealed value it cannot open (no key, another key, tampered). The caller fails the delivery
    visibly; it never signs with nothing.

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

#: The key read from the file named by KEY_FILE_ENV, keyed by that path: read once, re-read only if
#: the path itself changes.
_loaded: dict = {}


class AtRestKeyUnavailable(RuntimeError):
    """No usable at-rest key: the variable is unset, the file is unreadable, or it is not 64 hex."""


class SealedSecretUnreadable(RuntimeError):
    """A sealed value that does not open under this service's key."""


def load_key() -> bytes:
    """The at-rest key, read once. Raises AtRestKeyUnavailable when there is none to read."""
    path = (os.environ.get(KEY_FILE_ENV) or "").strip()
    if not path:
        raise AtRestKeyUnavailable(f"{KEY_FILE_ENV} is not set")
    if path in _loaded:
        return _loaded[path]
    try:
        key = bytes.fromhex(Path(path).read_text().strip())
    except (OSError, ValueError):
        raise AtRestKeyUnavailable(f"{KEY_FILE_ENV} does not name a file of 64 hex characters") from None
    if len(key) != 32:
        raise AtRestKeyUnavailable(f"{KEY_FILE_ENV} does not name a file of 64 hex characters")
    _loaded.clear()
    _loaded[path] = key
    return key


def key_configured() -> bool:
    try:
        load_key()
        return True
    except AtRestKeyUnavailable:
        return False


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def seal(value: Optional[str], purpose: str) -> Optional[str]:
    """The stored form of a secret. Raises AtRestKeyUnavailable rather than store it unsealed."""
    if not value or is_sealed(value):
        return value
    key = load_key()
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    nonce = secrets.token_bytes(12)
    blob = nonce + AESGCM(key).encrypt(nonce, value.encode("utf-8"), _AAD + purpose.encode())
    return PREFIX + base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")


def unseal(value: Optional[str], purpose: str) -> Optional[str]:
    """The secret itself: a sealed value opened, a legacy plaintext value as it is. Raises
    SealedSecretUnreadable for a sealed value that cannot be opened."""
    if not value or not is_sealed(value):
        return value
    try:
        key = load_key()
    except AtRestKeyUnavailable as e:
        raise SealedSecretUnreadable(str(e)) from None
    from cryptography.exceptions import InvalidTag  # noqa: PLC0415
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

    body = value[len(PREFIX):]
    try:
        blob = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        return AESGCM(key).decrypt(blob[:12], blob[12:], _AAD + purpose.encode()).decode("utf-8")
    except (InvalidTag, ValueError):
        raise SealedSecretUnreadable("a sealed value does not open under this key") from None
