# meeting_api/voice_notes — a voice note's audio door

_meeting-api · module · the one route a voice note has that a meeting does not._

A voice note is a meeting with no platform to join (`platform: "voice_note"`). `POST /bots` makes its
row and spawns its bot (`bot_spawn`: no meeting URL, no recording, an invocation.v1 `voiceNote` block
naming the audio stream and the limits). The person's browser then streams its microphone here, and
the bot reads it from Redis and runs it through the same lane, STT and sinks a meeting's audio takes.
Everything after that — the meeting page, the transcript, notes, search, share, export — is a
meeting's.

## Routes

`POST /voice-notes/recorder` — the MCP tool `record_voice_note` (`recorder.py`). The agent has no
microphone, so the tool only answers `{"status":"awaiting_user","ui_action":"open_voice_note",
"instruction":…}`, which the person's open Minutes tab acts on by starting the recorder; it creates no
row. A worker with nobody present is refused (`regime.require_person`, `403 human_session_required`).
Verify: `uv run pytest tests/test_voice_note_recorder.py tests/test_regime.py`.


`WS /voice-notes/{native_meeting_id}/stream` — reached through the gateway, which authorizes the
upgrade against the meetings row `GET /voice-notes/{native_meeting_id}/stream` (scopes `bot` · `tx`) and
signs the caller's identity onto the hop. A plain `GET` on the path answers 426.

| Frame | Meaning |
|---|---|
| server `{"type":"ready","max_duration_sec":N,"received_sec":s}` | accepted; `s` survives a reconnect |
| client binary | one capture.v1 no-name audio frame: Int32 track, Float64 capture epoch ms, ≤ 1 s of mono 16 kHz Float32 PCM |
| server `{"type":"progress","received_sec":s}` | about once a second of audio |
| client `{"type":"stop"}` → server `{"type":"stopped","received_sec":s}`, close 1000 | the end marker is appended; the bot finalizes the note |
| client `{"type":"ping"}` → server `{"type":"pong"}` | keeps a paused note alive |

| Check | Refusal |
|---|---|
| the caller (`x-user-id`, signed) has a live `voice_note` meeting by that id | `not_found`, close 4404 |
| each binary frame is a capture.v1 audio frame of at most a second | `bad_frame`, close 4400 |
| the note's total audio stays within `VOICE_NOTE_MAX_DURATION_SEC` | `max_duration`, close 4413 — the end marker is appended first |

Frames go to `voice_note:meeting:{id}:audio` (field `f`; the end marker is field `end`), the sample
count to `voice_note:meeting:{id}:audio:samples`; both expire an hour after the cap. The bot's own
Redis user may `XREAD` that one stream and nothing else (`bot_spawn.workload_redis`). Writers:
`RedisAudioStreamWriter` (production) and `InMemoryAudioStreamWriter` (tests, the in-process app).

## Verify

`uv run pytest tests/test_voice_note.py tests/test_bot_redis_users.py`
