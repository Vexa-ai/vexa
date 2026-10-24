- **MCP · a demo meeting for a first run.** New MCP tool `start_demo_meeting` (`POST /demo-meetings`,
  scope `bot`): with no meeting of your own, ask for a demo and watch a live transcript within
  seconds. Vexa opens a call room on the deployment's LiveKit server, your own transcription bot joins
  it, and the same bot plays two scripted speakers (about a minute, bundled in the bot image as
  `demo/demo-v1/`). Nothing to paste, nobody to admit; the meeting ends by itself. Demo
  meetings carry `data.demo = true`, are not admitted through the service authority (not billed), and
  are limited to `DEMO_MEETINGS_PER_DAY` per account (default 3; 0 turns them off). meeting-api logs
  `demo_started` and `demo_completed`. invocation.v1 gains the optional `call.presenter` block
  (back-compatible).
