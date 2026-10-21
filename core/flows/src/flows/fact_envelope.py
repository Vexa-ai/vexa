"""event.v2 — the SEALED FACT ENVELOPE: who a fact reaches, computed by its producer (PRD 0.13.4 D8).

CANONICAL COPY. This file lives in `core/flows/contracts/event.v2/` and is vendored VERBATIM into
every package that seals or verifies (`core/crm/src/crm/fact_envelope.py`,
`core/flows/src/flows/fact_envelope.py`); `scripts/parity.json` (`fact-envelope`) fails the build on
any byte difference. Standard library only, so every image can carry it.

WHAT IT IS. A fact posted to flows (`POST /events`) carries `refs` — references, never bodies (M5).
A person's flow is admitted when the fact's subject (`refs.uid`) is that person or when the person
is in the fact's audience. The producer is the only party that knows who else the fact concerns
(the next actor, the card's members, the workspace principal), so the PRODUCER computes the
audience and seals it, and flows admits by subject ∪ audience. A caller cannot claim an audience:
flows strips any `_audience`/`_owner`/`_visibility`/`_lifecycle` it is sent in `refs`, and sets them
only from an envelope this module verifies.

THE ENVELOPE. Enumerated keys only — a content field (a title, a summary, a body) is refused, so a
fat fact cannot be sealed or admitted (C8, M5):

    v           2
    producer    the producing domain; its key is derived from the seal secret and this name
    object      what the fact is about ("card", "record", ...)
    owner       the person id who owns the object
    visibility  "members" | "workspace" | "owner"
    audience    up to 200 short strings: person ids, and `ws:<slug>` for a workspace principal
    lifecycle   cards only, optional: kind, state, next_actor, next_check_at, handoff — the card's
                coordination fields and nothing else (C8)
    exp         unix seconds after which the seal is refused; set by `seal`, at most `SEAL_TTL_S`
                ahead (plus `CLOCK_SKEW_S`) when verified
    sig         hex HMAC-SHA256 over `canonical({"envelope": <envelope without sig>, "fact": <fact>})`,
                keyed per producer

THE FACT. The seal covers the whole fact the envelope rides on — `event_type`, `source_event_id`
and every `refs` key (the subject `uid`, the object id, the revision, the org) — so a sealed
audience cannot be lifted onto a different event, a different record or a different subject, and
`verify` answers only for the exact fact it was sealed over. `fact(event_type, source_event_id,
refs)` builds that binding; producer and verifier pass the refs exactly as posted.

THE KEY. `producer_key(secret, producer)` = HMAC-SHA256(secret, b"vexa-fact-envelope:" + producer).
One deployment secret yields a distinct key per producer, so an envelope sealed as `crm` does not
verify as `meetings`. The secret is the env `VEXA_FACT_SEAL_SECRET` and nothing else: it is its own
credential, never the internal tier's. Unset ⇒ nothing seals and nothing verifies.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time

VERSION = 2
#: The env the seal secret is read from. There is no fallback.
SEAL_SECRET_ENV = "VEXA_FACT_SEAL_SECRET"
#: Domain separation for the per-producer key.
KEY_LABEL = b"vexa-fact-envelope:"
#: How long a seal is good for. A producer seals at delivery, so this covers transit, not a queue.
SEAL_TTL_S = 300
#: The clock difference between a producer and flows that `verify` tolerates on `exp`.
CLOCK_SKEW_S = 60

VISIBILITIES = frozenset({"members", "workspace", "owner"})
#: The only card fields a fact may carry (PRD C8). Anything else in `lifecycle` is refused.
LIFECYCLE_KEYS = frozenset({"kind", "state", "next_actor", "next_check_at", "handoff"})
#: The only keys an envelope may have. A content field is not one of them (M5).
ENVELOPE_KEYS = frozenset({"v", "producer", "object", "owner", "visibility", "audience",
                           "lifecycle", "exp", "sig"})
REQUIRED_KEYS = frozenset({"v", "producer", "object", "owner", "visibility", "audience"})
#: The keys of the fact a seal is bound to.
FACT_KEYS = ("event_type", "source_event_id", "refs")
MAX_AUDIENCE = 200
MAX_STR = 200
#: The prefix of a workspace principal in `audience`. It never matches a person.
WORKSPACE_PREFIX = "ws:"


def secret_from_env(environ=None) -> str:
    """The seal secret: `VEXA_FACT_SEAL_SECRET`, else ""."""
    env = os.environ if environ is None else environ
    return str(env.get(SEAL_SECRET_ENV) or "")


def fact(event_type, source_event_id, refs) -> dict:
    """The fact a seal is bound to: the event's type, its producer-side id and its refs as posted."""
    return {"event_type": event_type, "source_event_id": source_event_id, "refs": refs}


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
    """Shape only — the seal and the expiry are checked separately."""
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
    if "exp" in env and (not isinstance(env["exp"], int) or isinstance(env["exp"], bool)):
        return False
    return True


def _well_formed_fact(f) -> bool:
    return (isinstance(f, dict) and set(f) == set(FACT_KEYS) and _short(f["event_type"])
            and _short(f["source_event_id"]) and isinstance(f["refs"], dict))


def _body(env: dict) -> dict:
    """The envelope without its seal, with null lifecycle values dropped: absent and null are one
    answer (a card with no next check has none), and a seal must not depend on which was sent."""
    body = {k: v for k, v in env.items() if k != "sig"}
    if isinstance(body.get("lifecycle"), dict):
        body["lifecycle"] = {k: v for k, v in body["lifecycle"].items() if v is not None}
    return body


def _sig(body: dict, the_fact: dict, secret) -> str:
    return hmac.new(producer_key(secret, body["producer"]),
                    canonical({"envelope": body, "fact": the_fact}), hashlib.sha256).hexdigest()


def _now(now) -> int:
    return int(time.time() if now is None else now)


def seal(env: dict, secret, the_fact: dict, *, now=None) -> dict:
    """The envelope with `exp` (now + `SEAL_TTL_S`) and `sig` added, keyed by `env["producer"]` and
    bound to `the_fact` (`fact(...)`). Raises ValueError on an envelope that would not verify (wrong
    version, an extra key, an unenumerated lifecycle field, an over-long audience, an `exp` of its
    own), on a malformed fact, or with no secret — a producer bug fails at the producer, loudly."""
    if not _bytes(secret):
        raise ValueError(f"fact envelope: no seal secret ({SEAL_SECRET_ENV} is unset)")
    if not _well_formed_fact(the_fact):
        raise ValueError("fact envelope: the fact must be event_type, source_event_id and refs")
    if isinstance(env, dict) and "exp" in env:
        raise ValueError("fact envelope: `exp` is set by the seal, never by the producer")
    body = {**_body(env), "exp": _now(now) + SEAL_TTL_S}
    if not _well_formed(body):
        raise ValueError("fact envelope: not an event.v2 envelope (enumerated keys and short strings only)")
    return {**body, "sig": _sig(body, the_fact, secret)}


def verify(env, secret, producers, the_fact, *, now=None) -> "dict | None":
    """The envelope without `sig` when it is well formed, carries an `exp` that has not passed (and
    is no further ahead than a seal sets it), its producer is one of `producers`, and its seal is
    valid under that producer's key over this envelope AND `the_fact` — the event type, the
    producer's event id and the refs exactly as posted. Otherwise None. Never raises on caller
    input."""
    try:
        if not isinstance(env, dict) or not _bytes(secret) or not _well_formed_fact(the_fact):
            return None
        sig = env.get("sig")
        if not isinstance(sig, str):
            return None
        body = {k: v for k, v in env.items() if k != "sig"}
        if not _well_formed(body) or "exp" not in body or body["producer"] not in producers:
            return None
        t = _now(now)
        if not (t < body["exp"] <= t + SEAL_TTL_S + CLOCK_SKEW_S):
            return None
        if not hmac.compare_digest(_sig(body, the_fact, secret), sig):
            return None
        return body
    except Exception:  # noqa: BLE001 — a malformed envelope is unverified, never a 500
        return None
