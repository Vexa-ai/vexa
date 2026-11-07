"""The compose proof's MeetingToken minter derives the key the way meeting-api does (offline).

meeting-api signs a MeetingToken with HMAC-SHA256(ADMIN_TOKEN, "vexa/meeting-token/v1"), never with
ADMIN_TOKEN itself; ``_token.py`` re-implements the minter for the live recording proof. The vector
below is the one meeting-api's tests pin (tests/test_meeting_token.py), so the copy cannot drift from
the minter without one of the two failing.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from _token import KEY_LABEL, mint_meeting_token

VECTOR_KEY_FOR_K = "6ef1212461daafc429a9d3fecb29853c60ea8e46cc2f4ae7e5ba4f2cedbe4662"


def _decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_the_copy_signs_with_the_derived_key_not_the_admin_secret():
    assert KEY_LABEL == b"vexa/meeting-token/v1"
    token = mint_meeting_token(1, 1, "google_meet", "abc", secret="k", session_uid="conn-1")
    head, body, sig = token.split(".")
    signing = f"{head}.{body}".encode()
    assert _decode(sig) == hmac.new(bytes.fromhex(VECTOR_KEY_FOR_K), signing, hashlib.sha256).digest()
    assert _decode(sig) != hmac.new(b"k", signing, hashlib.sha256).digest()
    claims = json.loads(_decode(body))
    assert {"exp", "aud", "scope", "session_uid"} <= set(claims)
