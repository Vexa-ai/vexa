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

- **Keys.** Each producer signs with its **own Ed25519 private key** (`VEXA_FACT_SEAL_KEY_FILE`, a
  PKCS#8 PEM only that producer mounts). flows-api holds only the producers' **public** keys, one
  `<producer>.pem` per producer in `VEXA_FACT_VERIFY_KEYS_DIR`, and verifies an envelope under the
  public key of the producer it names. A leaked or compromised producer can forge its own facts and
  no other producer's, and nothing flows holds can seal. No private key: the producer refuses to
  start (the crm outbox) and nothing seals. No public key for a producer: flows-api refuses every
  envelope of that producer. There is no shared secret and no fallback to the internal tier's.
- **Seal.** `sig` = hex Ed25519 signature (128 hex digits) over `canonical({"envelope": <envelope
  without sig>, "fact": {"event_type", "source_event_id", "refs"}})` — JSON with sorted keys, compact
  separators, UTF-8.
  The seal covers the whole fact: an envelope cannot be lifted onto another event, record, revision
  or subject. Null lifecycle values are dropped before sealing (absent and null are one answer).
- **Expiry.** `seal` sets `exp` = now + 300 s; `verify` refuses an envelope whose `exp` has passed or
  lies further ahead than a seal sets it (60 s clock skew allowed). The producer seals at delivery,
  so the window covers transit; a redelivery is sealed again. Replays inside the window are the
  same fact and dedup on `source_event_id`.

The algorithm is `fact_envelope.py` in this folder, the **canonical copy**, standard library only:
it takes the producer's signer and the verifier's per-producer checks as injected callables.
`fact_keys.py` beside it is the only code that touches key material (PEM loading and the `keygen`
one-shot; each service reads its own env) and needs `cryptography`. Both are vendored byte-for-byte —
`fact_envelope.py` to `core/crm/src/crm/` and `core/flows/src/flows/`, `fact_keys.py` to
`core/crm/src/crm/` and `core/flows/src/flows_integrations/` — and `scripts/parity.json`
(`fact-envelope`, `fact-keys`) fails the build on any difference.

**Rotating or adopting keys.** A producer's keypair is minted by the deploy surface (compose's
`fact-seal-keys` one-shot, the chart's `fact-seal-keys.yaml`); `python -m crm.fact_keys keygen
<private.pem> <public.pem>` keeps an existing private key and rewrites its public half. Rows are
sealed at delivery and a refused row stays pending, so while the two sides disagree about a key
(an upgrade, a rotation) a card fact is refused once and delivered, freshly sealed, once they agree.

## What admission does with it

1. flows drops `_audience`, `_owner`, `_visibility` and `_lifecycle` from the caller's `refs`,
   whoever the caller is. **A caller cannot claim an audience.** `admit()` does the same for every
   other path into it (the worker's re-emit, the mailbox, signals).
2. If the body carries `envelope`, flows verifies it against the posted `event_type`,
   `source_event_id` and `refs` under the named producer's public key (`fact_envelope.verify`,
   producers `crm`, `meetings`, `agent`). An envelope that does not verify is refused **403
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
- `fact_envelope.py` — canonical `fact`, `canonical`, `message`, `seal`, `verify` (stdlib only).
- `fact_keys.py` — canonical `load_signer`, `load_check`, `signer_from_file`, `checks_from_dir`,
  `public_pem`, `keygen`.
- `golden/` — sealed with a published TEST key (public half `golden/golden-crm-verify-key.pem`) at
  `now` = 1792310400 (exp 1792310700),
  each over its own `Event` fact; `<Shape>.reject-<case>.json` must
  fail to validate. `validate.mjs` (gate:schema) checks both directions, and
  `core/flows/tests/test_fact_envelope.py` re-verifies every golden seal in Python.

Registered for sealing in `contracts.seal.json` when the slice lands.
