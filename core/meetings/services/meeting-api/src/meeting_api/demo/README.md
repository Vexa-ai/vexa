# meeting_api/demo — a demo meeting for a first run

_meeting-api · module · MCP tool `start_demo_meeting`._

`POST /demo-meetings` gives a person with no meeting right now something to watch being transcribed:

1. **Limit.** `DEMO_MEETINGS_PER_DAY` per account in any 24 hours (default 3; 0 turns demos off),
   counted on the meeting rows (`MeetingRepo.count_demo_meetings`). Over it: `429 demo_limit_reached`.
2. **Room.** `calls.prepare_call` creates a call room, capped at `DEMO_MAX_DURATION_SEC` (default 300).
3. **Speakers.** One `calls.presenter_token` per voice of the bundled script (`DEMO_SPEAKERS`): this
   room only, publish audio, ten minutes, minted with the deployment's LiveKit key.
4. **Listener.** `bot_spawn.request_bot` spawns the person's own listener with the speakers in its
   invocation (`call.presenter`); the bot plays the script (`core/meetings/services/bot/demo/`). The
   service authority is not consulted (`authority=None`): a demo is not billable service. The row
   carries `data.demo = true`.
5. **Events.** `demo_started` (here) and `demo_completed` (`demo_ended`, from the lifecycle's
   call-ended hook) as `logevent.v1` lines; a hosted deployment maps them to its analytics.

The answer is the meeting (`platform: "vexa_call"`, `native_meeting_id`) plus a `demo` block and an
`instruction` for the agent: read it with `get_meeting_transcript`. Without calls (no `LIVEKIT_URL`)
the route answers `503 demo_not_available`.

## Verify

`uv run pytest tests/test_demo_meetings.py`
