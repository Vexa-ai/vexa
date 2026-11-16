# join_screenshots — one join-failure PNG per bot attempt

The join layer writes checkpoint screenshots inside the bot container, and nothing uploaded them,
so they died with the pod. On 1 Nov 2026 the founder asked to see the screen a prod bot was refused
on and nobody could show it. A bot whose join fails (Meet refused it, the lobby timed out, the
meeting was not found, the join threw) now uploads ONE PNG here before its terminal report, and
names the stored key on that report's `join_evidence.screenshot_key`.

## Front door
- `build_router(repo, storage, token_secret=...)` — `POST /internal/join-evidence/screenshot`
  (internal, `include_in_schema=False`). Multipart `file` (PNG bytes) + `session_uid`. Auth is the
  recording upload's: the session's MeetingToken, or the internal tier. Answers
  `{"status": "stored", "key", "bytes"}`; 401 bad credential, 404 unknown session (or a token for
  another meeting), 413 over `JOIN_SCREENSHOT_MAX_BYTES` (2 MB), 415 not a PNG, 422 a session uid
  that is not a plain id.
- `screenshot_key(meeting_id=, session_uid=)` — `join-evidence/<meeting_id>/<session_uid>.png`.
  One bot session is one join attempt, so each attempt has its own picture.
- `delete_meeting_screenshots(storage, meeting_id=)` — what erasing a meeting removes (wired into
  the app's meeting-fixture deleter beside the signal tapes).

## Storage and retention
The same object store as recordings (`recordings.ports.Storage`), its own `join-evidence/`
keyspace, never folded into `meeting.data['recordings']`. Nothing in the application expires it:
the signal janitor sweeps only `signal/`, the recording reconciler only reads `recordings/`. The
prod bucket's lifecycle rules expire only incomplete multipart uploads (7 d) and noncurrent
versions (90 d). A screenshot therefore stays until its meeting is erased.
