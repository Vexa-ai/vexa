"""MeetingToken minter — a faithful copy of meeting_api.bot_spawn.invocation.mint_meeting_token.

The recordings upload (`POST /internal/recordings/upload`) authenticates with a MeetingToken: an
HS256 JWS signed with the MeetingToken key meeting-api derives from its `token_secret` (== ADMIN_TOKEN
in the compose stack): HMAC-SHA256(ADMIN_TOKEN, KEY_LABEL). We mint one here so the always-on
recording proof can drive the bot's real upload path without spawning a bot. Kept in lock-step with
the shipped minter (same claims, same key derivation — fact meeting-token-key-label — same signing; a
`session_uid` binds the token to one bot session, and the shipped minter requires one).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid


#: meeting_api.meeting_token.KEY_LABEL — the purpose the MeetingToken key is derived for.
KEY_LABEL = b"vexa/meeting-token/v1"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_meeting_token(meeting_id: int, user_id: int, platform: str, native_meeting_id: str,
                       *, secret: str, session_uid: str, ttl_seconds: int = 7200) -> str:
    if not session_uid:
        raise ValueError("a MeetingToken is bound to a session; session_uid is required")
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "meeting_id": meeting_id,
        "user_id": user_id,
        "platform": platform,
        "native_meeting_id": native_meeting_id,
        "session_uid": session_uid,
        "scope": "transcribe:write",
        "iss": "meeting-api",
        "aud": "transcription-collector",
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid.uuid4()),
    }
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    key = hmac.new(secret.encode("utf-8"), KEY_LABEL, hashlib.sha256).digest()
    signature = hmac.new(key, signing_input, digestmod="sha256").digest()
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"
