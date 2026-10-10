"""dispatch_sink.py — who may hand agent-api a dispatch to run.

`POST /invocations` and `POST /events` take a whole unit.v1 dispatch (or an event that becomes one),
and the dispatch names the person the turn runs as. Neither route reads an identity header, so the
CALLER is what has to be authenticated, and there are exactly two callers:

  * **the internal tier** — a service holding `INTERNAL_API_SECRET`, which may already act for any
    person at this service (`X-Internal-Secret`, compared in constant time);
  * **a dispatch agent-api itself composed** — a routine's job, which the runtime's scheduler holds
    and POSTs back when it is due. agent-api signs the job's body when it compiles the routine
    (`sign`) and the scheduler sends that signature back unchanged in `HEADER`; `verify` recomputes
    it over the body that arrived. The body names the person and the trigger, so a signature binds
    both: a stored job can be fired again, never re-pointed at somebody else or turned into a
    different kind of turn.

The signing key is DERIVED from the internal secret (HMAC with a fixed label), so a job stored in the
scheduler carries a MAC and never the secret itself, and there is no second secret to configure. No
secret configured means nothing verifies: the routes refuse every caller.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping

#: Where the scheduler carries a routine job's signature back to `POST /invocations`.
HEADER = "X-Vexa-Dispatch-Signature"
VERSION = "v1"
_LABEL = b"vexa-dispatch-sink.v1"


def _key(secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), _LABEL, hashlib.sha256).digest()


def canonical(body: Mapping[str, Any]) -> bytes:
    """One byte string per JSON value, whatever order or spacing it travelled in."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(secret: str, body: Mapping[str, Any]) -> str:
    """The signature for a dispatch body. Raises ``ValueError`` without a secret: an unsigned job
    would be refused on every fire, and saying so at compile time is the honest failure."""
    if not secret:
        raise ValueError("no internal secret configured: a routine's dispatch cannot be signed")
    mac = hmac.new(_key(secret), canonical(body), hashlib.sha256).hexdigest()
    return f"{VERSION}={mac}"


def verify(secret: str, body: Any, signature: str) -> bool:
    """Does ``signature`` sign exactly ``body``? False without a secret, a signature, or a mapping."""
    if not secret or not signature or not isinstance(body, Mapping):
        return False
    return hmac.compare_digest(sign(secret, body).encode("ascii"),
                               str(signature).strip().encode("utf-8"))


def internal_caller(secret: str, provided: str) -> bool:
    """Is ``provided`` the internal secret? False when none is configured."""
    return bool(secret) and bool(provided) and hmac.compare_digest(
        provided.encode("utf-8"), secret.encode("utf-8"))
