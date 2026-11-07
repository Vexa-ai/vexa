"""dispatch_sink.py — who may hand agent-api a dispatch to run.

`POST /invocations` and `POST /events` take a whole unit.v1 dispatch (or an event that becomes one),
and the dispatch names the person the turn runs as. Neither route reads an identity header, so the
CALLER is what has to be authenticated, and there is exactly one caller: **the internal tier** — a
service holding `INTERNAL_API_SECRET`, which may already act for any person at this service
(`X-Internal-Secret`, compared in constant time).

Routines used to be a second caller: a job agent-api signed and the runtime's scheduler POSTed back.
Routines are flows now (ADR-0046) and agent-api arms no scheduler job, so no signature is accepted;
a request that carries one is refused like any other caller without the internal secret. No secret
configured means nothing opens: the routes refuse every caller.
"""
from __future__ import annotations

import hmac


def internal_caller(secret: str, provided: str) -> bool:
    """Is ``provided`` the internal secret? False when none is configured."""
    return bool(secret) and bool(provided) and hmac.compare_digest(
        provided.encode("utf-8"), secret.encode("utf-8"))
