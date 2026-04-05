---
id: test/bot-lifecycle
type: validation
requires: [test/create-live-meeting]
produces: [BOT_ID, BOT_TOKEN]
validates: [bot-lifecycle, realtime-transcription, speaking-bot, auth-and-limits]
docs: [features/bot-lifecycle/README.md, features/realtime-transcription/README.md, features/speaking-bot/README.md, features/auth-and-limits/README.md, services/meeting-api/README.md, services/runtime-api/README.md, services/vexa-bot/README.md]
mode: machine
skill: /test-bot-lifecycle
---

# Bot Lifecycle

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Launch a bot to a real meeting. Assert it follows the correct lifecycle: `requested → joining → awaiting_admission`. Any other state transition is a lifecycle failure.

## Platform differences

The state machine is the same on all platforms, but the bot's internal browser behavior differs:

| Platform | Join flow | Waiting room | Notes |
|----------|-----------|--------------|-------|
| google_meet | Navigate to URL, auto-joins | "Admit" in host UI | Most reliable, fully automated |
| teams | Navigate to URL, click "Join now" | "Admit" in lobby | Teams anti-bot detection more aggressive |
| zoom | Navigate to URL, enter passcode | "Admit" in waiting room | Needs passcode from meeting creator |

## Inputs

| Name | From | Default | Description |
|------|------|---------|-------------|
| MEETING_URL | test/create-live-meeting | — | Full meeting URL |
| MEETING_PLATFORM | test/create-live-meeting | google_meet | Platform identifier |
| API_TOKEN | test/api-full | — | API token with bot scope |
| GATEWAY_URL | test/infra-up | — | API gateway URL |

## Timeout guidance

Set `no_one_joined_timeout` to **300000** (5 min) in bot creation payloads. The default
120s is too short — humans need time to switch to the meeting UI and click "Admit".
Without this, bots silently time out and the test reports a false failure.

## Script

```bash
eval $(./testing/bot-lifecycle.sh GATEWAY_URL API_TOKEN MEETING_PLATFORM NATIVE_MEETING_ID [MEETING_URL])
```

See [bot-lifecycle.sh](bot-lifecycle.sh) for implementation.

## Steps

### Join phase
1. Extract meeting ID from URL
2. Launch bot via POST /bots. Set `no_one_joined_timeout` to 300000 (5 min) — the default
   120s is too short when humans need to context-switch to admit bots:
   ```bash
   curl -s -X POST "$GATEWAY_URL/bots" \
     -H "X-API-Key: $API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "platform": "'$PLATFORM'",
       "native_meeting_id": "'$NATIVE_ID'",
       "bot_name": "Recorder",
       "no_one_joined_timeout": 300000
     }'
   ```
3. **Poll lifecycle actively** — poll `GET /bots/status` every 10s, log each status
   change to test-log.md. When the bot is in `awaiting_admission`, log clearly:
   `"Bot {id} awaiting admission — human needs to admit in meeting"`.
   FAIL on `failed`, `error`, or `ended` (before admission).
   ```bash
   PREV_STATUS=""
   for i in $(seq 1 30); do   # 30 × 10s = 5 min max
       STATUS=$(curl -s -H "X-API-Key: $API_TOKEN" "$GATEWAY_URL/bots/status" | \
           python3 -c "import sys,json; bots=json.load(sys.stdin).get('running_bots',[]); \
           [print(b['status']) for b in bots if b.get('native_meeting_id')=='$NATIVE_ID']" 2>/dev/null | head -1)
       if [ "$STATUS" != "$PREV_STATUS" ]; then
           echo "$(date '+%H:%M:%S') Bot status: $PREV_STATUS → $STATUS"
           # Log to test-log.md
           PREV_STATUS="$STATUS"
       fi
       case "$STATUS" in
           awaiting_admission)
               echo ">>> Bot awaiting admission — human needs to admit in meeting"
               break ;;
           active)
               echo ">>> Bot is active (no waiting room?)"
               break ;;
           failed|error|ended)
               echo ">>> FAIL: Bot reached terminal state '$STATUS' before admission"
               break ;;
       esac
       sleep 10
   done
   ```
4. External observation — verify bot visible in meeting UI

> assert: bot reaches `awaiting_admission` without passing through unexpected states

### Active phase (after admit)
5. Verify status is `active`
6. Verify `data.status_transition` contains exact sequence: `requested→joining→awaiting_admission→active`
7. All transitions sourced from `bot_callback` (not `user` or `system`)

### Finalization (run last, after all transcription tests)

> run: test/verify-finalization (separate script, called at end of pipeline)

Stops all bots and validates clean shutdown:
- `DELETE /bots/{platform}/{native_id}` for each token
- Status: `completed` (not `failed`)
- Transitions end: `active→stopping→completed`
- `completion_reason` = `stopped`
- `end_time` is set

> assert: full lifecycle is `requested→joining→awaiting_admission→active→stopping→completed`
> on-fail: if `active→failed`, check runtime-api idle_timeout (must be 0 for meeting profile)

## Outputs

| Name | Description |
|------|-------------|
| BOT_ID | Bot/meeting numeric ID |
| BOT_TOKEN | Bot session token (if available) |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| Bot stays in `requested` forever | Runtime-api failed to spawn process/container | Check runtime-api logs, verify meeting profile exists in profiles.yaml | |
| Bot goes directly to `failed` | Playwright crash on startup | Check shm_size (needs 2GB), check DISPLAY=:99 | |
| Bot goes to `active` (skips admission) | Meeting has no waiting room enabled | Enable waiting room in Google Meet settings | |
| Bot goes to `ended` immediately | Meeting URL invalid or expired | Verify MEETING_URL is current and meeting is still active | |
| Bot `active→failed` instead of `completed` | runtime-api idle_timeout kills container after 600s | Set meeting profile `idle_timeout: 0` — meeting-api manages lifecycle | Runtime-api doesn't know bot is active in a meeting — always set 0 for meeting profile |
| `completion_reason` empty | Bot container crashed (OOM, SIGKILL) instead of clean exit | Check container exit code, shm_size, memory limits | Clean exit = `stopped`, crash = empty reason + `failed` status |
| Bot timed out in waiting room | Human didn't admit fast enough, `no_one_joined_timeout` (default 120s) expired | Set `no_one_joined_timeout: 300000` (5 min) in bot creation payload. Poll status actively and log `awaiting_admission` so humans know to act. | Default 120s is too short for test flows where humans context-switch between terminal and meeting UI. Always poll and log status changes — silent sleeps waste time and hide the problem. |

## Docs ownership

After this test runs, verify and update:

- **features/bot-lifecycle/README.md**
  - DoD table: update Status, Evidence, Last checked for items #1 (POST /bots creates bot), #2 (bot reaches active state), #4 (status visible via GET /bots), #6 (works for GMeet/Teams/browser_session)
  - States table: verify the documented state machine (`requested -> joining -> awaiting_admission -> active -> stopping -> completed`) matches the actual `data.status_transition` sequence observed by the test — if any states are skipped or new states appear, update the table
  - Components table: verify `services/meeting-api/meeting_api/meetings.py` (bot creation, status callbacks), `services/runtime-api/` (container lifecycle), and `services/vexa-bot/core/src/index.ts` (state machine) file paths are still correct
  - Confidence score: recalculate after updating statuses

- **features/realtime-transcription/README.md**
  - DoD table: this test creates a bot that will later produce transcription — note the bot ID and platform for cross-reference with test 09
  - Platform architectures table: verify the Platform differences (Google Meet N separate streams vs Teams 1 mixed stream) match what the bot actually does during the join phase

- **features/speaking-bot/README.md**
  - DoD table: update Status, Evidence, Last checked for items #1 (POST /speak returns 202) and #4 (interrupt DELETE /speak stops playback) — if the bot-lifecycle test exercises the speak endpoint
  - Components table: verify `services/meeting-api/meeting_api/voice_agent.py` (speak endpoint) and `services/vexa-bot/core/src/services/tts-playback.ts` (TTS playback) paths exist

- **features/auth-and-limits/README.md**
  - DoD table: update Status, Evidence, Last checked for item #3 (concurrent bot limit enforced) — if the test verifies that a second bot creation for the same user is rejected or allowed per limits

- **services/meeting-api/README.md**
  - API Endpoints: verify POST `/bots` request body fields (`platform`, `native_meeting_id`, `bot_name`, `meeting_url`, `voice_agent_enabled`) and response shape (`id`, `status`, `session_token`) match what the test sent and received
  - Internal Callbacks: verify the callback endpoints (`/bots/internal/callback/started`, `/joining`, `/awaiting_admission`, `/status_change`) are called in the order documented — check meeting-api logs for callback sequence
  - Environment variables: verify `BOT_IMAGE_NAME`, `RUNTIME_API_URL`, `MEETING_API_URL` match actual container config

- **services/runtime-api/README.md**
  - Container lifecycle: verify POST `/containers` with meeting profile creates a container and returns `{name, status: "running", ports}` as documented
  - Profiles section: verify the `meeting` profile exists in `profiles.yaml` and has `idle_timeout: 0` (so runtime-api doesn't kill the bot mid-meeting)
  - Backends section: verify the active backend (`docker`/`process`/`kubernetes`) matches what `ORCHESTRATOR_BACKEND` is set to

- **services/vexa-bot/README.md**
  - Platform table: verify the platform used (Google Meet or Teams) matches the documented browser, audio capture, and speaker identity methods
  - Bot-Enforced Timeouts table: verify `waitingRoomTimeout`, `everyoneLeftTimeout`, `noOneJoinedTimeout` defaults match what the bot container actually uses (from BOT_CONFIG JSON)
  - Runtime Control section: verify Redis `bot_commands:meeting:<meeting_id>` channel commands (`leave`, `reconfigure`) match actual behavior
  - VNC Browser View section: verify the bot container has Xvfb, fluxbox, x11vnc, and websockify running (check `ps aux` inside the container)
