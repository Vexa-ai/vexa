# MIGRATION-0001 — drop the dead `recordings` + `media_files` tables

**Status:** applied in the v0.12 schema definition (`schema/models.py`). NOT executed against
any live/prod DB — this is a schema-model decision + eval, per Group-1 scope.

## Verdict: the `recordings` table is DEAD

The parent defines `Recording` + `MediaFile` ORM models
(`services/meeting-api/meeting_api/models.py`) but **never writes to them**. Recordings are
stored in `meetings.data['recordings'][]` JSONB.

### Evidence (read from the parent source-of-truth)

1. **The only write path is JSONB.** `internal_upload_recording`
   (`services/meeting-api/meeting_api/recordings.py`) folds every uploaded chunk into
   `meeting.data['recordings'][]` via `apply_chunk_to_recording`
   (`recording_jsonb.py` — its module docstring literally says *"the meeting.data.recordings[]
   writer"*). It does `meeting.data = ...; flag_modified(meeting, "data"); db.commit()` — no
   `Recording(...)` row is ever constructed or `db.add`-ed anywhere in the tree.

2. **All `/recordings/*` reads target JSONB.** `_list_meeting_data_recordings`,
   `_find_meeting_data_recording` (JSONB `@>` containment), `delete_recording` — all operate on
   `meeting.data['recordings']`. The list/get/master/raw/delete endpoints never touch the table.

3. **The two ORM reads that exist are explicit LEGACY/defensive fallbacks:**
   - `meetings.py:~2147` (deferred-transcribe): *"Find recording — check recordings table
     first, then meeting.data (legacy)"* — the JSONB block is the one that actually resolves
     `storage_path` in production.
   - `collector/endpoints.py:~74` (`_purge_recordings_for_meeting`): guarded by
     `SELECT to_regclass('public.recordings') IS NOT NULL` and, when absent, logs *"recordings
     table unavailable in this environment; skipping model recording cleanup"* and proceeds.
     I.e. the code is written to run correctly **when the table does not exist** — exactly the
     v0.12 state.

Because nothing writes the table and the only readers are guarded legacy fallbacks that no-op
when it is absent, omitting `recordings` + `media_files` from the v0.12 schema is safe.

### Change

- `schema/models.py` defines `User, APIToken, Meeting, Transcription, MeetingSession` only.
- `Recording`, `MediaFile`, and the `Meeting.recordings` relationship are **removed**.
- O-STACK-1 (`tests/test_stack_postgres.py::test_recordings_table_is_dead`) asserts the tables
  are NOT created and `to_regclass('public.recordings')` is NULL, and
  `test_recordings_live_in_meeting_data_jsonb` proves the real JSONB path round-trips +
  is `@>`-queryable.

### If ever applied to a real DB (out of Group-1 scope)

`DROP TABLE IF EXISTS media_files; DROP TABLE IF EXISTS recordings;` — but only after confirming
the deployed parent revision matches the source read here (the guards above make the drop a
no-op risk-wise, but a real drop is a separate, human-reviewed ops change).
