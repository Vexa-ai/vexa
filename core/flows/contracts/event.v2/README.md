# event.v2 — the sealed fact envelope (D8) — UNSEALED (in development)

A fact a producing domain posts to flows (`POST /events`) may carry an **envelope**: who the fact
reaches, computed by the producer and signed by it. flows admits a person's flow when that person
is the fact's subject (`refs.uid`) **or** is named in the envelope's `audience`. Before this, a
fact reached its subject only, and the subject of a card decision is the person who decided — so
"Valeriia signs, your routine sees it" could not happen (PRD 0.13.4 §4.2 D8).

## Who seals

The producer, and only the producer: it is the one party that knows the next actor, the card's
members, the watchers and the workspace principal. The crm outbox seals today
(`core/crm/src/crm/outbox.py`); `meetings` and `agent` are on flows' allow-list for when they do.

- **Key.** `producer_key(secret, producer)` = HMAC-SHA256(secret, `b"vexa-fact-envelope:" + producer`).
  One deployment secret, a distinct key per producer: an envelope sealed as `crm` does not verify as
  `meetings`.
- **Secret.** env `VEXA_FACT_SEAL_SECRET`, its own credential with no fallback. Producer and
  flows-api must hold the same value. No secret: nothing seals, nothing verifies — the crm outbox
  refuses to start, and flows-api refuses every envelope.
- **Seal.** `sig` = hex HMAC-SHA256 over `canonical({"envelope": <envelope without sig>, "fact":
  {"event_type", "source_event_id", "refs"}})` — JSON with sorted keys, compact separators, UTF-8.
  The seal covers the whole fact: an envelope cannot be lifted onto another event, record, revision
  or subject. Null lifecycle values are dropped before sealing (absent and null are one answer).
- **Expiry.** `seal` sets `exp` = now + 300 s; `verify` refuses an envelope whose `exp` has passed or
  lies further ahead than a seal sets it (60 s clock skew allowed). The producer seals at delivery,
  so the window covers transit; a redelivery is sealed again. Replays inside the window are the
  same fact and dedup on `source_event_id`.

The algorithm is `fact_envelope.py` in this folder, the **canonical copy**, standard library only.
It is vendored byte-for-byte to `core/crm/src/crm/fact_envelope.py` and
`core/flows/src/flows/fact_envelope.py`; `scripts/parity.json` (`fact-envelope`) fails the build on
any difference.

## What admission does with it

1. flows drops `_audience`, `_owner`, `_visibility` and `_lifecycle` from the caller's `refs`,
   whoever the caller is. **A caller cannot claim an audience.** `admit()` does the same for every
   other path into it (the worker's re-emit, the mailbox, signals).
2. If the body carries `envelope`, flows verifies it against the posted `event_type`,
   `source_event_id` and `refs` (`fact_envelope.verify`, producers `crm`, `meetings`, `agent`). An envelope that does not verify is refused **403
   `{refused: "envelope_unverified"}`** — never admitted as if it were unsigned (P18).
3. A verified envelope sets `refs._audience`, `refs._owner`, `refs._visibility`, and for
   `object: "card"` `refs._lifecycle`.
4. A person's flow is admitted when `refs.uid == owner` or `owner ∈ refs._audience`. `ws:<slug>`
   entries name a workspace principal and never match a person.

A fact without an envelope (every row written before D8, and every producer that does not seal yet)
admits by subject only, as before. Where a rule needs the fact's owner it reads `refs._owner` when
sealed and falls back to `refs.uid` (the interim rule, BL-4).

## The envelope

| key | |
|---|---|
| `v` | `2` |
| `producer` | the producing domain |
| `object` | `card`, `record`, … |
| `owner` | the person id who owns the object |
| `visibility` | `members` · `workspace` · `owner` |
| `audience` | ≤ 200 short strings: person ids, and `ws:<slug>` |
| `lifecycle` | cards only, optional: `kind`, `state`, `next_actor`, `next_check_at`, `handoff` |
| `exp` | unix seconds; set by the seal, enforced on verify |
| `sig` | the seal |

**The keys are enumerated, and so are the lifecycle fields.** The card's coordination fields are
the only card fields a fact may carry (PRD C8). A title, a summary, a body or any other content
field is refused at sealing and at verification — and **refs never carry bodies either** (M5): a
fact says *what happened to which record*, and whoever is entitled to the content reads it from
the record's owner.

## Files

- `event.schema.json` — `#/$defs/Event` (the `POST /events` body), `Envelope`, `Lifecycle`. The
  seal is checked by `fact_envelope.py`, not by the schema.
- `fact_envelope.py` — canonical `fact`, `canonical`, `producer_key`, `seal`, `verify`, `secret_from_env`.
- `golden/` — sealed with the test secret `golden-seal-secret` at `now` = 1792310400 (exp 1792310700),
  each over its own `Event` fact; `<Shape>.reject-<case>.json` must
  fail to validate. `validate.mjs` (gate:schema) checks both directions, and
  `core/flows/tests/test_fact_envelope.py` re-verifies every golden seal in Python.

Registered for sealing in `contracts.seal.json` when the slice lands.
