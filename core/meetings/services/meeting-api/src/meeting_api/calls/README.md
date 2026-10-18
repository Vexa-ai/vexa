# meeting_api/calls — a call's room and its listener

_meeting-api · module · ADR-0048 decision 6 · #1783._

A call is a meeting on the deployment's own LiveKit server (`platform: "vexa_call"`).

When a call starts (`POST /bots {"platform": "vexa_call", "session_id": "s-…"}`), `bot_spawn` asks
this module for the call:

1. **Room.** `prepare_call` creates the room. Its name is `call-<hex>`, which is also the meeting's
   `native_meeting_id`. Its metadata holds:
   - the transcription notice (`transcribed`, `transcription_switchable`);
   - the owner;
   - the session.
2. **Listener.** `prepare_call` also returns the listener's `invocation.v1` `call` block. The block
   carries a hidden, subscribe-only token that expires with the call's cap.
3. **Meeting row.** Once the row exists, `record_meeting` adds its id to the room's metadata.

agent-api reads that metadata back when it binds the call to its session, and it mints participants'
tokens. This module mints only the listener's token and the room service's tokens.

| Piece | What it does |
|---|---|
| `livekit.py` | `LiveKitConfig` (`LIVEKIT_URL`, `LIVEKIT_INTERNAL_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`), HS256 access tokens, the Twirp room service |
| `spawn.py` | `Calls` (the server and its rooms, or None), `CallLimits` (`CALL_*`), `prepare_call`, `record_meeting`, `InMemoryCallRooms` for tests |

Without `LIVEKIT_URL`, `Calls.from_env()` is None and a `vexa_call` spawn answers
`503 calls_not_deployed`.

A caller cannot turn transcription off unless `CALL_TRANSCRIPTION_SWITCHABLE` is on.

## Verify

`uv run pytest tests/test_call_livekit_token.py tests/test_call_spawn.py`
