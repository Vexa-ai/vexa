# MIGRATION-0017 - drop `transcriptions.session_uid` (and its index)

**Status:** an **out-of-band, human-run ops step** (like MIGRATION-0001 and MIGRATION-0002). It
drops a column and an index, and `ensure_schema` never drops, so a deployment that already holds
the column keeps it until an operator runs the SQL below. A fresh install never creates it.

## What it removes

`transcriptions.session_uid` (`String`, nullable, indexed as `ix_transcriptions_session_uid`) and
its index. Nothing reads it: the transcript read path selects `transcriptions` rows without it
(`collector/adapters.py` `_persisted_segment`) and the segment serializer never emits it
(`_segment_to_api`). It is a different thing from `meeting_sessions.session_uid`, the bot's
runtime connection id, which stays.

What the column held:

- **Bot-captured rows:** `NULL`. The bot never sends a `session_uid` on a segment, so the collector
  wrote `NULL`.
- **Imported and seeded rows** (`POST /meetings/{id}/transcript-import`, the meeting bundle import,
  the rehearsal seed): `import-<source>-<meeting_id>`, derived from the row's own `source` and
  `meeting_id`, and already the prefix of every `segment_id` of that import. The value is
  recoverable from the row and is also kept in `meetings.data.transcript_import.session_uid`.

From this release the collector's INSERT does not name the column and the model does not define
it, so `transcript.v1`'s `SessionStart` and `Transcription` envelopes no longer require a
`session_uid` either.

## The change

```sql
DROP INDEX CONCURRENTLY IF EXISTS ix_transcriptions_session_uid;
ALTER TABLE transcriptions DROP COLUMN IF EXISTS session_uid;
```

Run the two statements separately (`DROP INDEX CONCURRENTLY` cannot run inside a transaction
block). `DROP COLUMN` takes a short `ACCESS EXCLUSIVE` lock on `transcriptions` and rewrites
nothing. Both are idempotent.

## Order

Run it **after** every meeting-api replica of the release before this one is gone. The previous
release names `session_uid` in its INSERT and reads full `Transcription` rows (`select(Transcription)`),
so both its transcript writes and its transcript reads fail once the column is dropped. A deployment
that never runs the SQL is correct as well: the column is nullable, so the new INSERT leaves it `NULL`.

## Pre-flight (read-only)

If something of yours reads the column, this shows whether it carries data:

```sql
SELECT count(*) AS rows_total, count(session_uid) AS rows_with_value FROM transcriptions;
```

## Rollback

```sql
ALTER TABLE transcriptions ADD COLUMN IF NOT EXISTS session_uid VARCHAR;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transcriptions_session_uid ON transcriptions (session_uid);
```

The values are not restored. Imported rows can be backfilled from `source` and `meeting_id` if a
reader ever needs them.
