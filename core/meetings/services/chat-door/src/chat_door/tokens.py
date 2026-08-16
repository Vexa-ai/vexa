"""Magic-link + session tokens — signed, expiring, single-use where it matters.

Two token kinds share one wire shape and one key:

* ``kind="link"`` — the magic link mailed inside an artifact. **Single-use**: its ``jti`` is
  burned by the first successful verification, so a forwarded email cannot re-open the door.
* ``kind="session"`` — minted *by* the door after a link verifies, carried in a cookie.
  Reusable until it expires; carries the same subject/scope the link carried.

Wire shape (URL-safe, no dependency): ``<b64url(payload_json)>.<b64url(hmac_sha256)>``.
The MAC covers the exact payload bytes that travel, so a re-encoded payload cannot verify.

Payload fields — short names because this rides in a URL:

| Field | Meaning |
|---|---|
| ``k`` | kind (``link`` / ``session``) |
| ``sub`` | the email identity the token speaks for |
| ``mid`` | the meeting id it is scoped to |
| ``scp`` | scope — ``member`` or ``guest`` (see :mod:`chat_door.scope`) |
| ``exp`` | absolute expiry, unix seconds |
| ``jti`` | unique id — the single-use handle |

Verification is fail-closed and returns a *stable reason* on every rejection, so the door can
say which of "expired / already used / tampered / wrong kind" happened without guessing.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Iterable, Literal, Protocol

TokenKind = Literal["link", "session"]

#: Every reason a verification can fail. Stable strings — surfaced to callers and asserted in tests.
REASON_MALFORMED = "token_malformed"
REASON_BAD_SIGNATURE = "token_signature_invalid"
REASON_EXPIRED = "token_expired"
REASON_REPLAYED = "token_already_used"
REASON_WRONG_KIND = "token_wrong_kind"


class TokenError(Exception):
    """Verification failed. ``reason`` is one of the ``REASON_*`` constants."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TokenClaims:
    kind: TokenKind
    subject: str
    meeting_id: str
    scope: str
    expires_at: int
    jti: str


class UsedTokenStore(Protocol):
    """Where burned ``jti`` values live. ``burn`` returns False if already burned."""

    def burn(self, jti: str, *, expires_at: int) -> bool: ...


class InMemoryUsedTokenStore:
    """Process-local single-use ledger. Adequate for one dev process; not for a cluster."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def burn(self, jti: str, *, expires_at: int) -> bool:
        self._prune()
        if jti in self._seen:
            return False
        self._seen[jti] = expires_at
        return True

    def _prune(self) -> None:
        now = int(time.time())
        for jti in [j for j, exp in self._seen.items() if exp < now]:
            del self._seen[jti]

    def burned(self) -> Iterable[str]:
        return tuple(self._seen)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class TokenSigner:
    """Mints and verifies both token kinds against one HMAC key."""

    def __init__(self, key: bytes, *, used_store: UsedTokenStore | None = None) -> None:
        if not key:
            raise ValueError("signing key must be non-empty")
        self._key = key
        self._used = used_store if used_store is not None else InMemoryUsedTokenStore()

    # -- mint ---------------------------------------------------------------

    def issue(
        self,
        *,
        kind: TokenKind,
        subject: str,
        meeting_id: str,
        scope: str,
        ttl_seconds: int,
        now: int | None = None,
        jti: str | None = None,
    ) -> str:
        if not subject:
            raise ValueError("subject (email identity) is required")
        now = int(time.time()) if now is None else int(now)
        payload = {
            "k": kind,
            "sub": subject,
            "mid": str(meeting_id),
            "scp": scope,
            "exp": now + int(ttl_seconds),
            "jti": jti or secrets.token_urlsafe(12),
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return f"{_b64e(body)}.{_b64e(self._mac(body))}"

    def _mac(self, body: bytes) -> bytes:
        return hmac.new(self._key, body, hashlib.sha256).digest()

    # -- verify -------------------------------------------------------------

    def verify(
        self,
        token: str,
        *,
        expect_kind: TokenKind,
        now: int | None = None,
        consume: bool | None = None,
    ) -> TokenClaims:
        """Verify and return claims, or raise :class:`TokenError`.

        ``consume`` defaults to True for ``link`` tokens (single-use) and False for
        ``session`` tokens (reusable). Pass it explicitly to override.
        """
        now = int(time.time()) if now is None else int(now)
        if not token or token.count(".") != 1:
            raise TokenError(REASON_MALFORMED)
        body_b64, mac_b64 = token.split(".", 1)
        try:
            body = _b64d(body_b64)
            mac = _b64d(mac_b64)
        except Exception as exc:  # noqa: BLE001 - any decode failure is one class
            raise TokenError(REASON_MALFORMED) from exc
        if not hmac.compare_digest(mac, self._mac(body)):
            raise TokenError(REASON_BAD_SIGNATURE)
        try:
            payload = json.loads(body.decode("utf-8"))
            claims = TokenClaims(
                kind=payload["k"],
                subject=payload["sub"],
                meeting_id=str(payload["mid"]),
                scope=payload["scp"],
                expires_at=int(payload["exp"]),
                jti=payload["jti"],
            )
        except Exception as exc:  # noqa: BLE001
            raise TokenError(REASON_MALFORMED) from exc

        if claims.kind != expect_kind:
            raise TokenError(REASON_WRONG_KIND)
        if claims.expires_at <= now:
            raise TokenError(REASON_EXPIRED)

        should_consume = (claims.kind == "link") if consume is None else consume
        if should_consume and not self._used.burn(claims.jti, expires_at=claims.expires_at):
            raise TokenError(REASON_REPLAYED)
        return claims


def build_magic_link(base_url: str, token: str) -> str:
    """The one URL that appears in an artifact email."""
    return f"{base_url.rstrip('/')}/door/verify?t={token}"
