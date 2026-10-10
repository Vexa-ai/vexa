"""meeting_token.py — the MeetingToken: the one credential a meeting bot holds.

An HS256 JWT signed with the MeetingToken key. That key is DERIVED from the deployment's admin secret
(``ADMIN_TOKEN``, admin-api's ``ADMIN_API_TOKEN``) as HMAC-SHA256(admin secret, ``KEY_LABEL``) — never
the admin secret itself, so the key that signs a bot's credential is not the key that mints API keys,
and no new secret has to be distributed: meeting-api is the only service that mints or verifies a
MeetingToken. ``bot_spawn`` mints one per bot session, bound to that session's connection id (claim
``session_uid``), and places it in the bot's invocation. The bot presents it as
``Authorization: Bearer <token>`` on the only two doors it calls:

  * the lifecycle callback (``lifecycle.mount``), where the session is the event's
    ``connection_id``;
  * the recording chunk and signal-tape upload (``recordings.router``), where the session is the
    request's ``session_uid``.

Both doors admit a token through :func:`admit_session`, one rule: a valid signature, an ``exp`` that
has not passed, the MeetingToken's own ``aud`` and ``scope``, and bound to exactly the session the
request names. A token bound to another session, or bound to
none, is refused. The internal tier (a trusted service, not a bot) is a separate credential each
door checks on its own.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


class InvalidMeetingToken(ValueError):
    """A MeetingToken that does not admit the request (bad signature, expired, malformed, or bound
    to another session). The message names the reason, never the token."""


#: The purpose the MeetingToken key is derived for (fact meeting-token-key-label). A new label is a
#: new key: every token minted under the old one is refused.
KEY_LABEL = b"vexa/meeting-token/v1"
#: The audience and scope every MeetingToken carries, and the only ones a door admits.
AUDIENCE = "transcription-collector"
SCOPE = "transcribe:write"


def signing_key(secret: str) -> bytes:
    """The MeetingToken key for the admin secret ``secret``: HMAC-SHA256(secret, KEY_LABEL)."""
    return hmac.new(secret.encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()


def sign(signing_input: bytes, secret: str) -> bytes:
    """The HS256 signature of ``signing_input`` (``header.payload``) under the MeetingToken key."""
    return hmac.new(signing_key(secret), signing_input, hashlib.sha256).digest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key(secret: Optional[str], action: str) -> str:
    key = secret if secret is not None else os.environ.get("ADMIN_TOKEN")
    if not key:
        error = InvalidMeetingToken if action == "verify" else ValueError
        raise error(f"ADMIN_TOKEN not configured; cannot {action} MeetingToken")
    return key


def mint_meeting_token(
    meeting_id: int,
    user_id: int,
    platform: str,
    native_meeting_id: str,
    *,
    session_uid: str,
    ttl_seconds: int = 7200,
    secret: Optional[str] = None,
) -> str:
    """Mint the MeetingToken for ONE bot session (``session_uid``, the spawn's connection id),
    signed with the key derived from ``ADMIN_TOKEN`` (or ``secret``). Stateless: no token table; the
    doors re-verify it."""
    if not session_uid:
        raise ValueError("a MeetingToken is bound to a session; session_uid is required")
    key = _key(secret, "mint")
    now = int(datetime.now(timezone.utc).timestamp())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "meeting_id": meeting_id,
        "user_id": user_id,
        "platform": platform,
        "native_meeting_id": native_meeting_id,
        "session_uid": session_uid,
        "scope": SCOPE,
        "iss": "meeting-api",
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid.uuid4()),
    }
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    return f"{header_b64}.{payload_b64}.{_b64url(sign(signing_input, key))}"


def verify_meeting_token(token: str, *, secret: Optional[str] = None) -> dict[str, Any]:
    """The claims of a MeetingToken signed with the MeetingToken key that carries an ``exp`` that has
    not passed, the MeetingToken audience and its scope. Raises :class:`InvalidMeetingToken`
    otherwise — a token without any one of the three is refused, not read as unlimited. Says nothing
    about which session it may act for — the doors use :func:`admit_session`."""
    key = _key(secret, "verify")
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        got = _b64url_decode(sig_b64)
    except (ValueError, TypeError, AttributeError):
        raise InvalidMeetingToken("malformed MeetingToken") from None
    if not hmac.compare_digest(sign(signing_input, key), got):
        raise InvalidMeetingToken("MeetingToken signature mismatch")
    try:
        claims = json.loads(_b64url_decode(payload_b64))
        if not isinstance(claims, dict):
            raise ValueError("claims are not an object")
        exp = claims.get("exp")
    except (ValueError, TypeError, AttributeError):
        raise InvalidMeetingToken("malformed MeetingToken") from None
    if exp is None or isinstance(exp, bool):
        raise InvalidMeetingToken("MeetingToken carries no exp")
    try:
        expired = int(datetime.now(timezone.utc).timestamp()) > int(exp)
    except (ValueError, TypeError, OverflowError):
        raise InvalidMeetingToken("malformed MeetingToken") from None
    if expired:
        raise InvalidMeetingToken("MeetingToken expired")
    if claims.get("aud") != AUDIENCE:
        raise InvalidMeetingToken("MeetingToken is not for this audience")
    if claims.get("scope") != SCOPE:
        raise InvalidMeetingToken("MeetingToken does not carry the MeetingToken scope")
    return claims


def admit_session(token: str, *, session_uid: Optional[str], secret: Optional[str] = None) -> dict[str, Any]:
    """The claims of a MeetingToken that may act for ``session_uid``: valid, unexpired and bound to
    exactly that session. Raises :class:`InvalidMeetingToken` for anything else, including a request
    that names no session and a token that is bound to none."""
    claims = verify_meeting_token(token, secret=secret)
    bound = claims.get("session_uid")
    if not session_uid:
        raise InvalidMeetingToken("the request names no session")
    if not bound:
        raise InvalidMeetingToken("MeetingToken is bound to no session")
    if not hmac.compare_digest(str(bound).encode(), str(session_uid).encode()):
        raise InvalidMeetingToken("MeetingToken is bound to another session")
    return claims
