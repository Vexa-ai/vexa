"""runtime_signature.py — is this RuntimeEvent callback the runtime's?

The runtime signs every callback it delivers (``X-Runtime-Signature``) with an HMAC over the event,
keyed from the runtime caller credential (``RUNTIME_API_TOKEN``), which meeting-api also holds. The
token itself never travels to a callback URL. Mirrors ``runtime_kernel.caller_auth.sign_callback``
byte for byte; ``tests/test_runtime_callback_signature.py`` pins both against one vector.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping

HEADER = "X-Runtime-Signature"
_LABEL = b"vexa-runtime-callback.v1"


def sign(token: str, event: Mapping[str, Any]) -> str:
    key = hmac.new(token.encode("utf-8"), _LABEL, hashlib.sha256).digest()
    body = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "v1=" + hmac.new(key, body, hashlib.sha256).hexdigest()


def verify(token: str, event: Any, signature: str) -> bool:
    """False without a token, a signature or a mapping body."""
    if not token or not signature or not isinstance(event, Mapping):
        return False
    return hmac.compare_digest(sign(token, event).encode("ascii"), str(signature).strip().encode("utf-8"))
