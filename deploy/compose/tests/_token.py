"""MeetingToken minter — a faithful copy of meeting_api.bot_spawn.invocation.mint_meeting_token.

The recordings upload (`POST /internal/recordings/upload`) authenticates with a MeetingToken: an
HS256 JWS signed with the meeting-api's `token_secret` (== INTERNAL_API_SECRET in the compose stack).
We mint one here so the always-on recording proof can drive the bot's real upload path without
spawning a bot. Kept in lock-step with the shipped minter (same claims, same signing).
"""
from __future__ import annotations

import base64
import hmac
import json
import time
import uuid


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def mint_meeting_token(meeting_id: int, user_id: int, platform: str, native_meeting_id: str,
                       *, secret: str, ttl_seconds: int = 7200) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "meeting_id": meeting_id,
        "user_id": user_id,
        "platform": platform,
        "native_meeting_id": native_meeting_id,
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
    signature = hmac.new(secret.encode(), signing_input, digestmod="sha256").digest()
    return f"{header_b64}.{payload_b64}.{_b64url(signature)}"
