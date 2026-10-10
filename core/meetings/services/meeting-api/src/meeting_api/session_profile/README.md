# meeting_api/session_profile — an authenticated bot's session write-back

_meeting-api · module · the only path by which a bot's rotated browser session reaches the store._

In authenticated-bot mode (`BOT_AUTHENTICATED=true`) every bot restores the deployment's stored
browser session from `BOT_USERDATA_S3_PATH` with the `BOT_S3_*` key pair, which is **read-only**
(Compose's storage-init grants it Get + List on that prefix and nothing else). On clean teardown the
bot hands its rotated session back here, and this module stores it with meeting-api's own storage
credentials (`S3_*`, else `MINIO_*`) at `BOT_S3_ENDPOINT` / `BOT_S3_BUCKET`.

## Route

`PUT /internal/browser-session/{session_uid}` — `include_in_schema=False`; bots only, the gateway
routes nothing here. Body: `{"files": [{"path": "<profile path>", "data": "<base64>"}]}`.

| Check | Refusal |
|---|---|
| `Authorization: Bearer <MeetingToken>` admitted for exactly `session_uid` (`meeting_token.admit_session`) | 401 |
| `BOT_AUTHENTICATED` on, store complete, bots' pair not a storage root key or secret (`bot_spawn.auth_session_config`) | 409 off · 503 misconfigured |
| the session is the newest one spawned on the identity (`MeetingRepo.latest_auth_session`), of the token's meeting, and the meeting is live or ended < `WRITEBACK_GRACE_S` (600 s) ago | 403 |
| body ≤ `MAX_BODY_BYTES` | 413 |
| every entry exactly `{path, data}`; every path a SESSION_PROFILE path named once (no traversal, absolute, backslash or control characters); each file ≤ `maxFileBytes`; all ≤ `maxFiles` / `maxTotalBytes` — one bad entry refuses the whole body | 422 |

A store failure is a 502 naming nothing secret. Every refusal past the token is a `logevent.v1`
`session_writeback_refused` line with the reason; a stored write-back is `session_writeback_stored`.

## The profile

[`session-profile.v1.json`](session-profile.v1.json) is a **byte-identical copy** of
[`@vexa/remote-browser`'s](../../../../../modules/remote-browser/src/session-profile.v1.json), the
file the bot restores and collects with; [`tests/test_session_profile.py`](../../../tests/test_session_profile.py)
fails on any difference. Change both together.

## Files

- [`profile.py`](profile.py) — `SESSION_PROFILE`, `profile_path_refusal`, `parse_profile_upload`.
- [`router.py`](router.py) — the route and its four checks.
- [`writer.py`](writer.py) — `S3SessionWriter` (boto3, lazy) and the `SessionWriter` port.
