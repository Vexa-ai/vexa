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
    producer    the producing domain; it names the public key the seal is verified under
    object      what the fact is about ("card", "record", ...)
    owner       the person id who owns the object
    visibility  "members" | "workspace" | "owner"
    audience    up to 200 short strings: person ids, and `ws:<slug>` for a workspace principal
    lifecycle   cards only, optional: kind, state, next_actor, next_check_at, handoff — the card's
                coordination fields and nothing else (C8)
    exp         unix seconds after which the seal is refused; set by `seal`, at most `SEAL_TTL_S`
                ahead (plus `CLOCK_SKEW_S`) when verified
    sig         hex Ed25519 signature (128 hex digits) over
                `canonical({"envelope": <envelope without sig>, "fact": <fact>})`, made with the
                producer's own private key

THE FACT. The seal covers the whole fact the envelope rides on — `event_type`, `source_event_id`
and every `refs` key (the subject `uid`, the object id, the revision, the org) — so a sealed
audience cannot be lifted onto a different event, a different record or a different subject, and
`verify` answers only for the exact fact it was sealed over. `fact(event_type, source_event_id,
refs)` builds that binding; producer and verifier pass the refs exactly as posted.

THE KEYS. Each producer holds its OWN Ed25519 private key and nothing else; flows-api holds only the
producers' PUBLIC keys. An envelope verifies under the public key of the producer it names, so a
service that leaks its key can forge its own facts and no other producer's, and the verifier holds
nothing that can seal at all. This module never touches a key: `seal` takes the producer's `sign`
(bytes -> 64-byte signature) and `verify` takes `{producer: check}` (check(message, signature) ->
bool), both injected, so the module stays standard library only. `fact_keys.py` beside it builds
both from PEM files: the producer's private key file, and flows' directory of public keys.
No sign: nothing seals. No check for a producer: nothing of that producer's verifies.
"""
from __future__ import annotations

import json
import re
import time

VERSION = 2
#: The signature scheme every seal uses. `fact_keys.py` holds the only implementation.
ALG = "ed25519"
#: An Ed25519 signature is 64 bytes: 128 lowercase hex digits.
_SIG_FORM = re.compile(r"[0-9a-f]{128}")
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


def fact(event_type, source_event_id, refs) -> dict:
    """The fact a seal is bound to: the event's type, its producer-side id and its refs as posted."""
    return {"event_type": event_type, "source_event_id": source_event_id, "refs": refs}


def canonical(env: dict) -> bytes:
    """The bytes a seal covers: sorted keys, compact separators, UTF-8."""
    return json.dumps(env, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def message(body: dict, the_fact: dict) -> bytes:
    """What the producer signs: the envelope without its seal, bound to the fact it rides on."""
    return canonical({"envelope": body, "fact": the_fact})


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


def _now(now) -> int:
    return int(time.time() if now is None else now)


def seal(env: dict, sign, the_fact: dict, *, now=None) -> dict:
    """The envelope with `exp` (now + `SEAL_TTL_S`) and `sig` added: `sign` is the producer's own
    signer (`fact_keys.signer_from_file`), applied to `message(envelope, the_fact)`. Raises ValueError
    on an envelope that would not verify (wrong version, an extra key, an unenumerated lifecycle
    field, an over-long audience, an `exp` of its own), on a malformed fact, or with no signer — a
    producer bug fails at the producer, loudly."""
    if not callable(sign):
        raise ValueError("fact envelope: no seal key (the producer has no signer)")
    if not _well_formed_fact(the_fact):
        raise ValueError("fact envelope: the fact must be event_type, source_event_id and refs")
    if isinstance(env, dict) and "exp" in env:
        raise ValueError("fact envelope: `exp` is set by the seal, never by the producer")
    body = {**_body(env), "exp": _now(now) + SEAL_TTL_S}
    if not _well_formed(body):
        raise ValueError("fact envelope: not an event.v2 envelope (enumerated keys and short strings only)")
    sig = sign(message(body, the_fact))
    if not isinstance(sig, (bytes, bytearray)) or len(sig) != 64:
        raise ValueError("fact envelope: the seal key is not an Ed25519 key")
    return {**body, "sig": bytes(sig).hex()}


def verify(env, checks, producers, the_fact, *, now=None) -> "dict | None":
    """The envelope without `sig` when it is well formed, carries an `exp` that has not passed (and
    is no further ahead than a seal sets it), its producer is one of `producers`, and its seal
    verifies under THAT producer's public key (`checks[producer]`, from `fact_keys.checks_from_dir`)
    over this envelope AND `the_fact` — the event type, the producer's event id and the refs exactly
    as posted. Otherwise None. Never raises on caller input."""
    try:
        if not isinstance(env, dict) or not isinstance(checks, dict) or not _well_formed_fact(the_fact):
            return None
        sig = env.get("sig")
        if not isinstance(sig, str) or not _SIG_FORM.fullmatch(sig):
            return None
        body = {k: v for k, v in env.items() if k != "sig"}
        if not _well_formed(body) or "exp" not in body or body["producer"] not in producers:
            return None
        check = checks.get(body["producer"])
        if not callable(check):
            return None
        t = _now(now)
        if not (t < body["exp"] <= t + SEAL_TTL_S + CLOCK_SKEW_S):
            return None
        if check(message(body, the_fact), bytes.fromhex(sig)) is not True:
            return None
        return body
    except Exception:  # noqa: BLE001 — a malformed envelope is unverified, never a 500
        return None
