# MIGRATION-0006 — `ix_transcription_text_fts` (transcript full-text search) (F191)

**Status:** the index-build code (`ensure_fts_index`,
`meeting-api/.../collector/adapters.py::SqlAlchemyTranscriptStore`) existed and was never called
from anywhere — defined, dead, and silently absent on every deployment since it shipped. It is now
wired: `meeting-api`'s FastAPI `lifespan` (`meeting_api/__main__.py::_attach_background_loops`)
fires it as a **one-shot background task** (`_ensure_fts_index_once`, task name
`ensure-fts-index`) alongside the other control-plane loops, single-flighted across replicas the
same way `calendar-sync` and `signal-tape-janitor` are (`sweeps/single_flight.py`).

**No out-of-band ops step, on any DB, new or existing** — this is the difference from
MIGRATION-0002 and MIGRATION-0005. Unlike those, the index build now runs itself, automatically, on
the very next `meeting-api` boot after this change deploys, on every environment. `#1456`'s upgrade
notes need no manual-migration callout for this item.

## What the index is

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transcription_text_fts
ON transcriptions USING gin (to_tsvector('english', text));
```

Backs `transcript_search` (`collector/adapters.py`, the `ts_rank_cd`/`websearch_to_tsquery` query a
few hundred lines above `ensure_fts_index` in the same file): without it, every search is a
sequential scan of `transcriptions` — the highest-row-count table in the schema.

## Why this could not just ride `ensure_schema` (same three hazards as MIGRATION-0002, this table)

`admin-api`'s `ensure_schema` / `_sync_indexes` runs at **admin-api** boot, inside one transaction,
against the SSOT model. This index cannot go there:

1. **`CREATE INDEX CONCURRENTLY` cannot run inside a transaction block** — `ensure_schema` wraps
   convergence in one; a `CONCURRENTLY` build there is a hard Postgres error, not a slow one.
2. **A plain (non-concurrent) `CREATE INDEX` takes `ACCESS EXCLUSIVE`** on `transcriptions` for the
   build's duration — unacceptable on the busiest table in the schema, and it would stall
   `admin-api`'s startup (and therefore every dependent service's readiness) for as long as the
   build takes.
3. **It is owned by the wrong domain.** `transcriptions` and its FTS index are meeting-api's table;
   the build function already lives on meeting-api's own adapter (`SqlAlchemyTranscriptStore`), not
   in admin-api's schema SSOT.

## Why a one-shot background task, not an operator runbook

MIGRATION-0002's index is a **correctness backstop** — its absence changes dedup behavior under a
race. MIGRATION-0005's function is **load-bearing on every list query** — its absence makes list
requests fail outright. Both therefore needed a human-gated out-of-band build with a pre-flight
duplicate check before the deploy that depends on them.

This index has neither property, by the docstring on `ensure_fts_index` itself: *"Safe to call on
every boot BECAUSE SEARCH WORKS WITHOUT IT: a missing or half-built index means a slower query,
never a wrong answer and never a failed request."* There is nothing an operator needs to sequence,
no duplicate-row hazard to pre-check, and no failure mode search-side if the build has not finished
yet — a request that lands mid-build simply pays a sequential scan once more. That is exactly the
shape `ensure_fts_index`'s own INVALID-index self-heal (drop + rebuild on the next call if a prior
`CONCURRENTLY` build died mid-way) is built to run unattended, indefinitely, without an operator
ever being paged for it.

## Verifying it built

```sql
SELECT indexrelid::regclass, indisvalid
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'ix_transcription_text_fts';
```

`indisvalid = true` → done. No row yet, or `indisvalid = false` on a freshly booted meeting-api →
check the `ensure-fts-index` background-task log line (`transcript FTS index: {...}`); a `status`
of `created` or `present` is success, and an INVALID leftover is dropped and retried on the next
`meeting-api` restart with no operator action.

## Rollback

`DROP INDEX CONCURRENTLY IF EXISTS ix_transcription_text_fts;` — safe at any time; `transcript_search`
degrades to a sequential scan, nothing else changes. The background task will simply rebuild it on
the next boot unless the code wiring it (`_ensure_fts_index_once` in `meeting_api/__main__.py`) is
also reverted.
