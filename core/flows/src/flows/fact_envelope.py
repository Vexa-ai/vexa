"""event.v2 — the SEALED FACT ENVELOPE: who a fact reaches, computed by its producer (PRD 0.13.4 D8).

CANONICAL COPY. This file lives in `core/flows/contracts/event.v2/` and is vendored VERBATIM into
every package that seals or verifies (`core/crm/src/crm/fact_envelope.py`,
`core/flows/src/flows/fact_envelope.py`); `scripts/parity.json` (`fact-envelope`) fails the build on
any byte difference. Standard library only, so every image can carry it.

WHAT IT IS. A fact posted to flows (`POST /events`) carries `refs` — references, never bodies (M5).
Until D8 a person's flow was admitted only when the fact's subject (`refs.uid`) was that person, so
"Valeriia signs, your routine sees it" could not happen: the subject of a card decision is the
person who decided. The producer is the only party that knows who else the fact concerns (the next
actor, the card's members, the workspace principal), so the PRODUCER computes the audience and
seals it, and flows admits by subject ∪ audience. A caller cannot claim an audience: flows strips
any `_audience`/`_owner`/`_visibility`/`_lifecycle` it is sent in `refs`, and sets them only from an
envelope this module verifies.

THE ENVELOPE. Enumerated keys only — a content field (a title, a summary, a body) is refused, so a
fat fact cannot be sealed or admitted (C8, M5):

    v           2
    producer    the producing domain; its key is derived from the shared secret and this name
    object      what the fact is about ("card", "record", ...)
    owner       the person id who owns the object
    visibility  "members" | "workspace" | "owner"
    audience    up to 200 short strings: person ids, and `ws:<slug>` for a workspace principal
    lifecycle   cards only, optional: kind, state, next_actor, next_check_at, handoff — the card's
                coordination fields and nothing else (C8)
    sig         hex HMAC-SHA256 over `canonical(envelope without sig)`, keyed per producer

THE KEY. `producer_key(secret, producer)` = HMAC-SHA256(secret, b"vexa-fact-envelope:" + producer).
One deployment secret yields a distinct key per producer, so an envelope sealed as `crm` does not
verify as `meetings`. The secret is the env `VEXA_FACT_SEAL_SECRET`, falling back to
`INTERNAL_API_SECRET` (the internal tier's one shared secret) when unset; producer and verifier must
resolve the same value. No secret ⇒ nothing seals and nothing verifies.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os

VERSION = 2
#: The env the seal secret is read from, and the one it falls back to when that is unset or empty.
SEAL_SECRET_ENV = "VEXA_FACT_SEAL_SECRET"
FALLBACK_SECRET_ENV = "INTERNAL_API_SECRET"
#: Domain separation for the per-producer key.
KEY_LABEL = b"vexa-fact-envelope:"

VISIBILITIES = frozenset({"members", "workspace", "owner"})
#: The only card fields a fact may carry (PRD C8). Anything else in `lifecycle` is refused.
LIFECYCLE_KEYS = frozenset({"kind", "state", "next_actor", "next_check_at", "handoff"})
#: The only keys an envelope may have. A content field is not one of them (M5).
ENVELOPE_KEYS = frozenset({"v", "producer", "object", "owner", "visibility", "audience",
                           "lifecycle", "sig"})
REQUIRED_KEYS = frozenset({"v", "producer", "object", "owner", "visibility", "audience"})
MAX_AUDIENCE = 200
MAX_STR = 200
#: The prefix of a workspace principal in `audience`. It never matches a person.
WORKSPACE_PREFIX = "ws:"


def secret_from_env(environ=None) -> str:
    """The seal secret: `VEXA_FACT_SEAL_SECRET`, else `INTERNAL_API_SECRET`, else ""."""
    env = os.environ if environ is None else environ
    return str(env.get(SEAL_SECRET_ENV) or env.get(FALLBACK_SECRET_ENV) or "")


def canonical(env: dict) -> bytes:
    """The bytes a seal covers: sorted keys, compact separators, UTF-8."""
    return json.dumps(env, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _bytes(secret) -> bytes:
    return secret if isinstance(secret, bytes) else str(secret or "").encode("utf-8")


def producer_key(secret, producer: str) -> bytes:
    """HMAC-SHA256(secret, b"vexa-fact-envelope:" + producer)."""
    return hmac.new(_bytes(secret), KEY_LABEL + str(producer).encode("utf-8"), hashlib.sha256).digest()


def _short(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_STR


def _well_formed(env) -> bool:
    """Shape only — the seal is checked separately."""
    if not isinstance(env, dict) or not REQUIRED_KEYS <= set(env) or not set(env) <= ENVELOPE_KEYS:
        return False
    if env["v"] != VERSION or isinstance(env["v"], bool):
        return False
    if not (_short(env["producer"]) and _short(env["object"]) and _short(env["owner"])):
        return False
    if env["visibility"] not in VISIBILITIES:
        return False
    audience = env["audience"]
    if not isinstance(audience, list) or len(audience) > MAX_AUDIENCE:
        return False
    if not all(_short(a) for a in audience):
        return False
    if "lifecycle" in env:
        life = env["lifecycle"]
        if not isinstance(life, dict) or not set(life) <= LIFECYCLE_KEYS:
            return False
        if not all(_short(v) for v in life.values()):
            return False
    return True


def _body(env: dict) -> dict:
    """The envelope without its seal, with null lifecycle values dropped: absent and null are one
    answer (a card with no next check has none), and a seal must not depend on which was sent."""
    body = {k: v for k, v in env.items() if k != "sig"}
    if isinstance(body.get("lifecycle"), dict):
        body["lifecycle"] = {k: v for k, v in body["lifecycle"].items() if v is not None}
    return body


def _sig(body: dict, secret) -> str:
    return hmac.new(producer_key(secret, body["producer"]), canonical(body), hashlib.sha256).hexdigest()


def seal(env: dict, secret) -> dict:
    """The envelope with `sig` added, keyed by `env["producer"]`. Raises ValueError on an envelope
    that would not verify (wrong version, an extra key, an unenumerated lifecycle field, an
    over-long audience) or with no secret — a producer bug fails at the producer, loudly."""
    if not _bytes(secret):
        raise ValueError("fact envelope: no seal secret (VEXA_FACT_SEAL_SECRET / INTERNAL_API_SECRET)")
    body = _body(env)
    if not _well_formed(body):
        raise ValueError("fact envelope: not an event.v2 envelope (enumerated keys and short strings only)")
    return {**body, "sig": _sig(body, secret)}


def verify(env, secret, producers) -> "dict | None":
    """The envelope without `sig` when it is well formed, its producer is one of `producers`, and its
    seal is valid under that producer's key; otherwise None. Never raises on caller input."""
    try:
        if not isinstance(env, dict) or not _bytes(secret):
            return None
        sig = env.get("sig")
        if not isinstance(sig, str):
            return None
        body = {k: v for k, v in env.items() if k != "sig"}
        if not _well_formed(body) or body["producer"] not in producers:
            return None
        if not hmac.compare_digest(_sig(body, secret), sig):
            return None
        return body
    except Exception:  # noqa: BLE001 — a malformed envelope is unverified, never a 500
        return None
