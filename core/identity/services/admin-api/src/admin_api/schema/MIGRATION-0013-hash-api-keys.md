# MIGRATION-0013: API keys are held as their SHA-256

**Status:** applied **automatically** by `ensure_schema` (`schema/sync.py::_hash_api_tokens`) on
every admin-api boot, after the column sync and before the index sync. `schema.seal.json` is
re-sealed with the two new columns.

## What it fixes

`api_tokens.token` held every key's value. Anyone with the database or a backup held every
customer's key. Workspace invites never had this problem (`invites.token_hash`, MIGRATION-0009);
keys now follow the same pattern.

## What it adds

| Object | Shape | How it lands |
|---|---|---|
| `api_tokens.token_hash` | `VARCHAR(64)`, unique index: SHA-256 of the key, lowercase hex | additive column; filled by the pass below |
| `api_tokens.token_hint` | `VARCHAR(32)`: `vxa_<scope>_…` + the last four characters (`…abcd` for a key with no prefix) | additive column; filled by the pass below |
| `api_tokens.token` | loses `NOT NULL`; NULL on every converged row | `ALTER COLUMN token DROP NOT NULL` (catalog only) |

## The change

For every row whose `token` is set: `token_hash = sha256(token)`, `token_hint = hint(token)`,
`token = NULL`. The key itself does not change: a caller presents the same value, identity hashes
it, and the row matches.

- Idempotent: only rows with a `token` are read, so a second boot finds none.
- A writer that has not moved to hashes yet (vexa-platform `transcription-gateway`) may still insert
  a plaintext row. Lookups match `token_hash = sha256(presented) OR token = presented`
  (`key_store.token_matches`), so such a key works at once, and the next boot hashes it.
- From this release, every mint door (`/admin/users/{id}/tokens`, `/internal/sessions`) writes only
  the hash and the hint. The value is in the mint answer and nowhere else.
- Lists (`GET /admin/users/{id}/tokens`) answer `token_hint`; they never carried the value.

## How the backfill runs, and what `/internal/validate` sees while it does

- **Where.** In the boot convergence (`ensure_schema`), one transaction, after the column sync and
  before the index sync. The order is:
  1. `ADD COLUMN token_hash, token_hint`;
  2. `ALTER COLUMN token DROP NOT NULL`;
  3. one `UPDATE … SET token_hash, token_hint, token = NULL WHERE id = :id` per plaintext row (one
     executemany);
  4. the unique index on `token_hash`, built in-band.

  MIGRATION-0014's classify and sweep run in the same transaction, right after.
- **Locks.** Steps 1 and 2 take `ACCESS EXCLUSIVE` on `api_tokens` and hold it until the convergence
  commits. Each lock is asked for with a 500 ms `lock_timeout`, retried with backoff (`sync.py`
  `LOCK_TIMEOUT_MS`), so the convergence never queues behind live reads. Once it holds the lock,
  every `/internal/validate` read of `api_tokens` waits. Validate bounds its own read at 1 s
  (`READ_BOUNDS`), so a key presented in that window gets the gateway's typed `503 Authentication
  temporarily unavailable`; the client retries.
- **Measured.** bbb, 28 Oct 2026; Postgres 16 in testcontainers, host load average 35–40.
  - **Setup:** 12,000 synthetic plaintext keys in the pre-0013 column shape. Prod held about 11,000
    on 23 Oct (identity PRD, § Prod counts).
  - **The convergence:** about 1.9 s for the whole of it, MIGRATION-0013 and 0014 included. Every
    row hashed, none left in plaintext.
  - **A concurrent read** using validate's bounds, 67 reads: one hit the 1 s lock timeout, the
    slowest took 1.00 s, and 2 took over 200 ms.
  - **Expected in prod:** about 1–2 s of 503s on key validation, once, at the upgrade's boot.
  - The probe was `test_zz_lock_timing_pg.py`, a throwaway kept on bbb at `~/ci/ik135-timing`; it is
    not in the repo.
- **Clearing the plaintext is the same step as writing the hash, not a second one.** That has a
  rolling-deploy cost, below.

## Open decision: how the upgrade rolls out (finding 1 of the #1900 review)

An admin-api older than this release looks keys up by `token`. As soon as the first new pod (or the
migrations Job) commits this pass, every old pod still serving answers **401 to every existing key**
until it is replaced. That was observed directly: an old pod answering 200 for a key answered 401
for it right after the pass.

Measured on bbb, 28 Oct 2026, unless marked as an estimate:
- **Setup:** kind, one node, 4 CPU / 4 GB. The admin-api Deployment came from the chart
  (`helm template`), with its probes and strategy. 12,000 legacy keys over 1,000 users. A prober hit
  the Service every 100 ms.
- **Process start:** Docker on bbb at host load 21–37. The chart limit is 0.5 CPU / 512 Mi.

| | Today (one release, RollingUpdate `maxSurge 1, maxUnavailable 0`) | A: two releases | B: one release, admin-api Recreate (or 1 replica) for this upgrade |
|---|---|---|---|
| Mechanics | the first new pod (or the Job) hashes and clears `token`; old pods keep serving and answer 401 | 0.13.5 writes and reads the hash, and keeps `token` (new mints too); 0.13.6 clears it | old pods stop, then new pods start and run the pass |
| Window (measured) | 2 replicas: **28.0 s and 29.4 s** of 401s (about half the requests in the window: 168/418 and 166/433). With the migrations Job enabled: **46.8 s** (340/572). 1 replica (the hosted shape): **15.0 s** in which every request got 401 (142/305) | **none**: old pods keep validating with `token`, and 0.13.6's old pods already read by hash | **no admin-api endpoint for 12.9 s and 12.0 s** (connection refused, zero 401s); first 200 at +14.7 s and +13.6 s |
| Process ready | — | — | 5.7–6.2 s at the chart's limits with the pass pending; 4.9–5.7 s for a plain restart. The pass adds about 0.7–1.0 s; the 10 s readiness `initialDelaySeconds` dominates |
| Real cluster (estimate) | as measured, plus image pull and scheduling | — | 13–25 s; worst case 30 s grace + 200 s startup budget |
| Plaintext at rest | none after the pass | **every key readable in the database and in any backup for one release cycle** | none after the pass |
| Rollback | to a backup only | to the pre-0013 admin-api during 0.13.5 | to a backup only |
| Self-hosters must | accept the 401 window | install 0.13.5, then 0.13.6 | set the chart's one-upgrade flag, or accept the window |
| Hosted must | ship transcription-gateway's hash lookup first; accept about 15 s of total 401 | ship that lookup before 0.13.6 | the same gateway prerequisite; about 13–25 s with no admin-api |

**Not measured:** what the gateway answers a client while admin-api has no endpoints; a real LKE
cluster (image pull, scheduling, live load); the hosted subchart's actual strategy (vexa-platform
pins `adminApi.replicaCount: 1`, `values-base.yaml:195`).

**One outlier:** one of five migrate-pod runs took 45 s instead of 5.5–8.9 s. Its log wasn't kept.
It may have been the lock-retry backoff, which sums to about 30.5 s.

**Not implemented on this branch:** neither A nor B. The founder decides.

## Operator-visible

Nothing for key holders: every key keeps working. A key's value can no longer be read back from the
database, so an operator who needs a customer's key mints a new one.

## Rollback

**One-way.** A value cannot be recovered from its hash. An admin-api older than this release looks
keys up by `token`, which is NULL after the pass, so on rollback **no existing key validates** until
the database is restored from a backup taken before the upgrade. Take one before upgrading.

## Deploy order (hosted)

vexa-platform `transcription-gateway` reads `api_tokens` itself. It must ship its hash lookup
(`token_hash = sha256(key)`) **before or with** this admin-api: an older gateway looks keys up by
`token` and finds none after the pass.

## Why it complies

| Rule | How | Enforced by |
|---|---|---|
| no secret at rest | only a hash and a four-character hint are stored | `tests/test_key_hashing.py`, `tests/test_key_hashing_pg.py` |
| no key breaks | the pass hashes in place; lookups hash the presented key | `tests/test_key_hashing_pg.py::test_old_plaintext_keys_still_authenticate_after_hashing` |
| no unreviewed DB change | `schema.seal.json` re-sealed with `token_hash`, `token_hint`, nullable `token` | `gate:db-schema` |
