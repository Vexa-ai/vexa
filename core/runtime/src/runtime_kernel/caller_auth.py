"""caller_auth.py — who may drive the runtime's workload and schedule surfaces.

The runtime starts containers, Pods and processes with the environment and mount set a caller
names, and fires the HTTP requests a caller schedules. Those are control-plane verbs, so every
route except ``/health`` requires the runtime caller credential: ``RUNTIME_API_TOKEN``, presented as
``Authorization: Bearer <token>``. agent-api and meeting-api hold it; no spawned workload does (the
docker and k8s backends forward an explicit allow-list into workloads, and the process backend
builds each child's environment from scratch — see ``process_backend.child_environment``).

The token is required at boot. A runtime without one refuses to start rather than serving the
workload API open, and a value shorter than :data:`MIN_TOKEN_BYTES` or published in this repository
is refused the same way.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Callable, Mapping, Optional

from fastapi import HTTPException, Request

TOKEN_ENV = "RUNTIME_API_TOKEN"
#: A caller token shorter than this is refused at boot (32 hex characters = 128 bits).
MIN_TOKEN_BYTES = 32


def _published_tokens() -> frozenset[str]:
    """The literals that must never be the caller token: the ``forbidden_values`` the runtime's
    config.v1 declaration lists for it (the one list; the boot preflight checks the same)."""
    from .config_preflight import load_declaration

    entry = next(k for k in load_declaration()["keys"] if k["key"] == TOKEN_ENV)
    return frozenset(entry.get("forbidden_values") or ())


class CallerTokenError(RuntimeError):
    """The runtime caller token is absent or unusable — the runtime must not serve."""


def load_caller_token(env: Optional[Mapping[str, str]] = None) -> str:
    """The configured caller token, or :class:`CallerTokenError` naming what is wrong. Never echoes
    the value."""
    env = os.environ if env is None else env
    token = (env.get(TOKEN_ENV) or "").strip()
    if not token:
        raise CallerTokenError(
            f"{TOKEN_ENV} is not set — the runtime refuses to serve its workload API without a "
            "caller credential. Generate one (`openssl rand -hex 32`) and set the same value on "
            "the runtime, agent-api and meeting-api."
        )
    if token in _published_tokens():
        raise CallerTokenError(f"{TOKEN_ENV} holds a value published in the Vexa repository — generate a real one")
    if len(token.encode("utf-8")) < MIN_TOKEN_BYTES:
        raise CallerTokenError(f"{TOKEN_ENV} must be at least {MIN_TOKEN_BYTES} bytes")
    return token


#: The header a RuntimeEvent callback carries its signature in.
SIGNATURE_HEADER = "X-Runtime-Signature"
_CALLBACK_LABEL = b"vexa-runtime-callback.v1"


def _canonical(event: Mapping[str, object]) -> bytes:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_callback(token: str, event: Mapping[str, object]) -> str:
    """The signature a callback receiver checks: an HMAC over the event, keyed from the caller
    token (so the token itself never travels to a callback URL). The receiver holds the same token."""
    key = hmac.new(token.encode("utf-8"), _CALLBACK_LABEL, hashlib.sha256).digest()
    return "v1=" + hmac.new(key, _canonical(event), hashlib.sha256).hexdigest()


def bearer_guard(token: str) -> Callable[[Request], None]:
    """A FastAPI dependency admitting only requests that present ``token`` as a bearer credential.
    Compared in constant time; a missing or wrong credential is a 401 that says nothing else."""
    if not token:
        raise CallerTokenError("bearer_guard needs a non-empty caller token")
    expected = token.encode("utf-8")

    def guard(request: Request) -> None:
        header = request.headers.get("authorization") or ""
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(presented.strip().encode("utf-8"), expected):
            raise HTTPException(status_code=401, detail="runtime caller credential required",
                                headers={"WWW-Authenticate": "Bearer"})

    return guard
