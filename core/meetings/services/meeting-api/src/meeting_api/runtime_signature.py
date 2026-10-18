"""runtime_signature.py — is this RuntimeEvent callback the runtime's, sent now, to this door?

The runtime signs every callback it delivers (``X-Runtime-Signature: t=<unix seconds>,v2=<hex>``)
with an HMAC over the signing time, the URL it delivers to and the event, keyed from the runtime
caller credential (``RUNTIME_API_TOKEN``), which meeting-api also holds. The token itself never
travels to a callback URL. A callback is accepted only when the signature is valid for the URL it
was received on, its time is within :data:`SKEW_SEC` of this clock, and it has not been accepted
before (:class:`ReplayGuard`). Mirrors ``runtime_kernel.caller_auth.sign_callback`` byte for byte;
``tests/test_runtime_callback_signature.py`` pins both against one vector.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any, Mapping, Optional

HEADER = "X-Runtime-Signature"
_LABEL = b"vexa-runtime-callback.v2"
#: How far a callback's signing time may be from this clock, either way (seconds).
SKEW_SEC = 300
_FORM = re.compile(r"^t=([0-9]{1,12}),v2=([0-9a-f]{64})$")


def _message(timestamp: int, url: str, event: Mapping[str, Any]) -> bytes:
    body = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"{int(timestamp)}\n{url}\n".encode("utf-8") + body


def sign(token: str, event: Mapping[str, Any], url: str, timestamp: int) -> str:
    key = hmac.new(token.encode("utf-8"), _LABEL, hashlib.sha256).digest()
    return f"t={int(timestamp)},v2=" + hmac.new(key, _message(timestamp, url, event), hashlib.sha256).hexdigest()


def check(token: str, event: Any, signature: str, url: str, now: Optional[float] = None) -> Optional[str]:
    """None when the signature is valid for ``url`` and fresh; otherwise why not ("unsigned",
    "stale"). Replays are the caller's to refuse (:class:`ReplayGuard`), keyed by the signature."""
    if not token or not signature or not isinstance(event, Mapping):
        return "unsigned"
    m = _FORM.match(str(signature).strip())
    if not m:
        return "unsigned"
    timestamp = int(m.group(1))
    if not hmac.compare_digest(sign(token, event, url, timestamp).encode("ascii"), m.group(0).encode("ascii")):
        return "unsigned"
    if abs((time.time() if now is None else now) - timestamp) > SKEW_SEC:
        return "stale"
    return None


def verify(token: str, event: Any, signature: str, url: str, now: Optional[float] = None) -> bool:
    return check(token, event, signature, url, now) is None


class ReplayGuard:
    """Accepts each signature once within the skew window: in Redis when the app has one (shared by
    every meeting-api replica, ``SET NX`` with the window as its expiry), else in this process."""

    PREFIX = "runtime:callback:seen:"

    def __init__(self, redis: Any = None, window: int = 2 * SKEW_SEC) -> None:
        self._redis = redis if redis is not None and hasattr(redis, "set") else None
        self._window = window
        self._seen: dict[str, float] = {}

    async def first(self, signature: str) -> bool:
        """True the first time this signature is seen within the window."""
        key = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        if self._redis is not None:
            return bool(await self._redis.set(self.PREFIX + key, "1", nx=True, ex=self._window))
        now = time.time()
        self._seen = {k: t for k, t in self._seen.items() if now - t < self._window}
        if key in self._seen:
            return False
        self._seen[key] = now
        return True
