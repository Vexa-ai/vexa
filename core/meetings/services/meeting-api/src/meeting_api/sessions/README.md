# sessions — the `MeetingSession` model + the shared SQLAlchemy mirror

A `MeetingSession` is one bot **connection** to a meeting — N per meeting, keyed by `session_uid`
(the `connectionId` the bot is constructed with). `bot_spawn` eager-creates a row on spawn;
`recordings` looks it up by `session_uid` when the bot uploads a chunk, so the upload resolves its
meeting even before the bot reports `active`.

This sub-package also owns the meeting-api's **single SQLAlchemy mirror** (`Meeting` /
`Transcription` / `MeetingSession`) — the source-of-truth `Base` every other module (`collector`,
`recordings`, `bot_spawn`) binds, so there is ONE `declarative_base()` in the monolith.

## Front door
- `Base` / `Meeting` / `Transcription` / `MeetingSession` — the SQLAlchemy models (lazily exposed via
  PEP 562 so importing `sessions` never pulls in SQLAlchemy).
- `new_session(meeting_id, session_uid)` — build an un-persisted `MeetingSession` for the eager
  create on spawn.

## Shared-models decision (P2)
These models are a **self-contained per-service mirror** of the backing-stack tables (the SSOT is
`identity/services/admin-api/.../schema/models.py`) — the same pattern as `obs.py`. `gate:isolation-py`
PRE-ALLOWS a `meeting_api → admin_api` edge for the models, but meeting-api does **not** take it: the
mirror keeps the monolith import-free of the identity domain (no real cross-package edge is created),
exactly as the folded-in collector already did. Recordings + notes live in `meetings.data` JSONB
(there is no separate recordings table).
