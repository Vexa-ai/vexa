"""Envelope encryption for the secret VALUES a user entrusts to us, stored in ``users.data`` JSONB.

Third-party credentials — STT tokens, LLM keys, webhook signing secrets, calendar feed URLs, and
now OAuth refresh tokens — sit in that JSONB in the clear (Vexa-ai/vexa#876). A refresh token is a
worse thing to hold in the clear than any of the others: it is ongoing, silent read access to a
customer's whole calendar, and it survives until revoked.

**The shape is envelope encryption, two levels:**

    KEK  (32 bytes, operator-held, from VEXA_SECRETS_KEK — never in the database)
      wraps
    DEK  (32 bytes, one per user, random, stored WRAPPED in users.data["_secret_dek"])
      encrypts
    each secret value -> "enc:v1:<b64 nonce>:<b64 ciphertext>"

Rotating the KEK re-wraps N small DEKs instead of re-encrypting every secret, which is the whole
reason for the middle level. The JSONB structure is untouched — keys stay keys, only values change
shape — so nothing that queries this column has to learn anything.

**Two rules that are easy to get wrong and are enforced here:**

*Dual-read, never dual-write.* ``decrypt`` returns an unprefixed value unchanged, so a database
still holding plaintext keeps working during migration. ``encrypt`` has no matching passthrough:
once a cipher is configured, everything written is encrypted. Migration is therefore a read-then-
write sweep, not a mode.

*Fail loud, never silent plaintext (#876 A4).* If a value IS encrypted and no KEK is configured,
``decrypt`` raises. The failure mode this exists to prevent is a KEK that quietly goes missing in
one environment and a service that carries on serving — or worse, re-writing — the values in the
clear.

Every ciphertext is bound to the user and the field it belongs to via AES-GCM's additional
authenticated data. A ciphertext lifted from one user's row into another's, or from ``stt_token``
into ``webhook_secret``, fails to authenticate rather than decrypting into the wrong place.
"""
from __future__ import annotations

import base64
import os
from typing import Any, Mapping, Optional

VALUE_PREFIX = "enc:v1:"
DEK_PREFIX = "wrap:v1:"
DEK_FIELD = "_secret_dek"
KEK_ENV = "VEXA_SECRETS_KEK"

_KEY_BYTES = 32  # AES-256
_NONCE_BYTES = 12  # GCM standard; random per encryption, never reused under one key


class SecretCryptoError(RuntimeError):
    """Raised when an encrypted value cannot be read — a missing KEK, a wrong key, tampering.

    Never caught-and-ignored by design: every caller either has the key or must stop.
    """


def is_encrypted(value: Any) -> bool:
    """Does this stored value carry a ciphertext envelope? (Used for dual-read and for migration
    sweeps that need to know what is left to do.)"""
    return isinstance(value, str) and value.startswith(VALUE_PREFIX)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _load_kek(raw: Optional[str]) -> bytes:
    """The operator's master key: 32 bytes, base64 or hex. Anything else is a hard stop — a
    truncated or mistyped KEK must not silently become a weak key."""
    if not raw or not raw.strip():
        raise SecretCryptoError(f"{KEK_ENV} is empty")
    text = raw.strip()
    for decode in (_b64d, bytes.fromhex):
        try:
            key = decode(text)
        except Exception:
            continue
        if len(key) == _KEY_BYTES:
            return key
    raise SecretCryptoError(
        f"{KEK_ENV} must decode to exactly {_KEY_BYTES} bytes (base64 or hex); "
        "generate one with: python -c \"import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())\""
    )


class UserSecretBox:
    """One user's cipher — holds their unwrapped DEK for the life of a request, nothing longer."""

    __slots__ = ("_dek", "_user_id")

    def __init__(self, dek: bytes, user_id: int | str) -> None:
        self._dek = dek
        self._user_id = str(user_id)

    def _aad(self, field: str) -> bytes:
        """Binds the ciphertext to (user, field). Moving one elsewhere fails authentication."""
        return f"vexa:user:{self._user_id}:{field}".encode()

    def encrypt(self, field: str, plaintext: Optional[str]) -> Optional[str]:
        """A secret value → its envelope. ``None``/empty passes through — absence is not a secret."""
        if plaintext is None or plaintext == "":
            return plaintext
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(self._dek).encrypt(nonce, plaintext.encode(), self._aad(field))
        return f"{VALUE_PREFIX}{_b64e(nonce)}:{_b64e(ct)}"

    def decrypt(self, field: str, stored: Optional[str]) -> Optional[str]:
        """Envelope → the secret value. An UNPREFIXED value is pre-migration plaintext and is
        returned unchanged (dual-read); a prefixed one that will not authenticate raises."""
        if not is_encrypted(stored):
            return stored
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            _, _, rest = stored.partition(VALUE_PREFIX)
            nonce_b64, _, ct_b64 = rest.partition(":")
            plain = AESGCM(self._dek).decrypt(_b64d(nonce_b64), _b64d(ct_b64), self._aad(field))
        except Exception as exc:  # wrong key, tampered ciphertext, malformed envelope
            raise SecretCryptoError(
                f"could not decrypt {field!r} for user {self._user_id} — wrong key or tampered value"
            ) from exc
        return plain.decode()


class SecretCipher:
    """The KEK holder. Wraps and unwraps per-user DEKs; the DEKs do the actual work."""

    __slots__ = ("_kek",)

    def __init__(self, kek: bytes) -> None:
        if len(kek) != _KEY_BYTES:
            raise SecretCryptoError(f"KEK must be {_KEY_BYTES} bytes, got {len(kek)}")
        self._kek = kek

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> Optional["SecretCipher"]:
        """``SecretCipher`` when a KEK is configured, ``None`` when the operator has not set one.

        ``None`` is a legitimate deployment (self-hosters who have not turned this on) and means
        "write plaintext, as before". It does NOT mean "read encrypted values as plaintext" — that
        is what ``require_readable`` refuses.
        """
        source = env if env is not None else os.environ
        raw = source.get(KEK_ENV)
        if raw is None or not raw.strip():
            return None
        # Prove the backend is present HERE, at configuration time, rather than at the first
        # encrypt — an operator who sets a KEK into an image without the wheel should learn at
        # boot, not when the first user connects a calendar.
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except ImportError as exc:  # pragma: no cover - exercised by the packaging gate
            raise SecretCryptoError(
                f"{KEK_ENV} is set but the 'cryptography' package is not installed in this image"
            ) from exc
        return cls(_load_kek(raw))

    def wrap_dek(self, dek: bytes, user_id: int | str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(_NONCE_BYTES)
        ct = AESGCM(self._kek).encrypt(nonce, dek, f"vexa:dek:{user_id}".encode())
        return f"{DEK_PREFIX}{_b64e(nonce)}:{_b64e(ct)}"

    def unwrap_dek(self, wrapped: str, user_id: int | str) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            _, _, rest = wrapped.partition(DEK_PREFIX)
            nonce_b64, _, ct_b64 = rest.partition(":")
            return AESGCM(self._kek).decrypt(
                _b64d(nonce_b64), _b64d(ct_b64), f"vexa:dek:{user_id}".encode()
            )
        except Exception as exc:
            raise SecretCryptoError(
                f"could not unwrap the data key for user {user_id} — wrong KEK or tampered value"
            ) from exc

    def for_user(self, user_id: int | str, data: dict) -> UserSecretBox:
        """This user's box, minting and storing a wrapped DEK in ``data`` on first use.

        ``data`` IS the user's JSONB dict and is mutated in place — the caller is about to persist
        it anyway, and a DEK that is generated but not saved would orphan everything encrypted
        under it. Callers MUST save ``data`` after encrypting with the returned box.
        """
        wrapped = data.get(DEK_FIELD)
        if isinstance(wrapped, str) and wrapped.startswith(DEK_PREFIX):
            return UserSecretBox(self.unwrap_dek(wrapped, user_id), user_id)
        dek = os.urandom(_KEY_BYTES)
        data[DEK_FIELD] = self.wrap_dek(dek, user_id)
        return UserSecretBox(dek, user_id)


def require_readable(cipher: Optional[SecretCipher], data: Mapping[str, Any],
                     fields: tuple[str, ...]) -> None:
    """Stop if this row holds ciphertext we have no key for (#876 A4).

    The failure this prevents: a KEK missing in one environment, a service that shrugs, and secrets
    served — or rewritten — in the clear. Silence here is the bug, so it raises.
    """
    if cipher is not None:
        return
    encrypted = [f for f in fields if is_encrypted(data.get(f))]
    if encrypted:
        raise SecretCryptoError(
            f"{KEK_ENV} is not set but these stored values are encrypted: {', '.join(encrypted)}. "
            "Refusing to continue — set the key that wrote them, or the data is unreadable."
        )
