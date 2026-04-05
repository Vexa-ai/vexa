# Vexa Agent System

Runtime environment documentation for agents running inside Vexa containers. The workspace is at `/workspace` and persists across container restarts via cloud storage.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `VEXA_USER_ID` | Agent's user ID |
| `VEXA_AGENT_API` | Agent API endpoint (`http://agent-api:8100`) |
| `VEXA_RUNTIME_API` | Runtime API endpoint (`http://runtime-api:8090`) |
| `VEXA_BOT_API_TOKEN` | API token for authenticated requests (`X-API-Key` header) |

## vexa CLI

The `vexa` CLI is available in PATH for common operations.

### Workspace

```bash
vexa workspace save        # sync to persistent storage
vexa workspace status      # check sync state
```

### Containers

```bash
vexa container spawn --profile browser   # spawn Chromium + VNC + CDP
vexa container list                      # list running containers
vexa container stop {name}               # stop a container
vexa browser connect {name}              # get CDP URL for Playwright
```

### Scheduling

```bash
vexa schedule --at {iso8601} chat "message"    # reminder at exact time
vexa schedule --in {duration} chat "message"   # relative delay (5m, 2h, 1d)
vexa schedule list                             # pending jobs
vexa schedule cancel {job_id}                  # cancel job
```

### Meeting Bot Lifecycle

```bash
vexa meeting join --platform {teams|google_meet|zoom} --url {url}
vexa meeting list                                      # active bots (with elapsed time)
vexa meeting status --platform {p} --id {id}           # detailed status
vexa meeting participants --platform {p} --id {id}     # speakers seen
vexa meeting wait-active --platform {p} --id {id} [--timeout 60]
vexa meeting transcript {meeting_id}                   # fetch live transcript
vexa meeting transcribe --meeting-id {id}              # trigger post-meeting transcription
vexa meeting stop --platform {p} --id {id}             # remove bot
vexa meeting config --platform {p} --id {id} [--language en] [--task transcribe]
```

### Meeting Voice (TTS)

```bash
vexa meeting speak --platform {p} --id {id} --text "Hello" [--voice alloy]
vexa meeting speak-stop --platform {p} --id {id}
```

### Meeting Chat

```bash
vexa meeting chat --platform {p} --id {id} --text "message"
vexa meeting chat-read --platform {p} --id {id}
```

### Meeting Screen Share

```bash
vexa meeting screen --platform {p} --id {id} --type {image|video|url|html} [--url {url}] [--html {html}]
vexa meeting screen-stop --platform {p} --id {id}
```

### Meeting Avatar

```bash
vexa meeting avatar --platform {p} --id {id} --url {image_url}
vexa meeting avatar-reset --platform {p} --id {id}
```

### Meeting Diagnostics

```bash
vexa meeting events --platform {p} --id {id} [--limit 20]
```

### Recordings

```bash
vexa recording list [--meeting-id {id}]
vexa recording get {recording_id}
vexa recording download {recording_id} {media_file_id}
vexa recording delete {recording_id}
vexa recording config
vexa recording config --enabled true --capture-modes audio,video
```

## Direct API Access

For operations the CLI does not cover, call APIs directly with curl. Always include the auth header:

```bash
AUTH="-H 'X-API-Key: $VEXA_BOT_API_TOKEN'"
```

### Meeting API (meeting-api:8080)

Full bot control API: speak, chat, screen share, avatar, recordings.

```bash
BOT="http://meeting-api:8080"
TOKEN="$VEXA_BOT_API_TOKEN"

# Bot lifecycle
curl -X POST "$BOT/bots" -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"platform":"google_meet","native_meeting_id":"xxx-yyyy-zzz","transcribe_enabled":true}'
curl -X DELETE "$BOT/bots/{platform}/{native_meeting_id}" -H "X-API-Key: $TOKEN"
curl "$BOT/bots/status" -H "X-API-Key: $TOKEN"

# TTS
curl -X POST "$BOT/bots/{platform}/{id}/speak" -H "X-API-Key: $TOKEN" \
  -H "Content-Type: application/json" -d '{"text":"Hello everyone"}'
curl -X DELETE "$BOT/bots/{platform}/{id}/speak" -H "X-API-Key: $TOKEN"

# Chat
curl -X POST "$BOT/bots/{platform}/{id}/chat" -H "X-API-Key: $TOKEN" \
  -H "Content-Type: application/json" -d '{"message":"Meeting notes will be shared."}'
curl "$BOT/bots/{platform}/{id}/chat" -H "X-API-Key: $TOKEN"

# Screen sharing
curl -X POST "$BOT/bots/{platform}/{id}/screen" -H "X-API-Key: $TOKEN" \
  -H "Content-Type: application/json" -d '{"type":"url","content":"https://example.com/slides.html"}'
curl -X DELETE "$BOT/bots/{platform}/{id}/screen" -H "X-API-Key: $TOKEN"

# Avatar
curl -X PUT "$BOT/bots/{platform}/{id}/avatar" -H "X-API-Key: $TOKEN" \
  -H "Content-Type: application/json" -d '{"url":"https://example.com/avatar.png"}'

# Recordings
curl "$BOT/recordings" -H "X-API-Key: $TOKEN"
curl "$BOT/recordings/{recording_id}/media/{media_file_id}/download" -H "X-API-Key: $TOKEN"
```

### Runtime API (runtime-api:8090)

Container lifecycle management.

```bash
RT="http://runtime-api:8090"
TOKEN="$VEXA_BOT_API_TOKEN"

curl -X POST "$RT/containers" -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"user_id":"me","profile":"browser","config":{}}'
curl "$RT/containers" -H "X-API-Key: $TOKEN"
curl "$RT/containers/{name}/cdp" -H "X-API-Key: $TOKEN"
curl -X DELETE "$RT/containers/{name}" -H "X-API-Key: $TOKEN"
curl -X POST "$RT/containers/{name}/touch" -H "X-API-Key: $TOKEN"
```

### Transcription Collector (transcription-collector:8000)

```bash
TC="http://transcription-collector:8000"
TOKEN="$VEXA_BOT_API_TOKEN"

curl "$TC/internal/transcripts/{meeting_id}" -H "X-API-Key: $TOKEN"
curl "$TC/health"
```

### Agent API (agent-api:8100)

Job scheduling and workspace management.

```bash
AGENT="http://agent-api:8100"

curl -X POST "$AGENT/api/schedule" -H "Content-Type: application/json" \
  -d '{"user_id":"me","action":"chat","in":"5m","message":"Check meeting status"}'
curl "$AGENT/api/schedule?user_id=me"
curl -X DELETE "$AGENT/api/schedule/{job_id}"
curl -X POST "$AGENT/internal/workspace/save" -H "Content-Type: application/json" \
  -d '{"user_id":"me"}'
```

## Usage Patterns

### Join and Wait

After joining, always wait for the bot to become active before sending commands:

```bash
vexa meeting join --platform google_meet --url https://meet.google.com/xxx-yyy-zzz
vexa meeting wait-active --platform google_meet --id xxx-yyy-zzz
vexa meeting speak --platform google_meet --id xxx-yyy-zzz --text "Hello everyone"
```

### Browser Workflow

Spawn a browser container and connect via CDP:

```bash
vexa container spawn --profile browser
vexa browser connect {container-name}
# Returns CDP URL: http://vexa-browser-{user}-{id}:9223
# Connect with Playwright via connect_over_cdp()
```

### Meeting Events

Meeting start/end notifications arrive automatically via the agent-api meeting subscriber -- no polling needed. Act on these events to fetch transcripts, summarize, and extract action items.

## Rules

- Always `vexa workspace save` before expecting to be stopped
- For web browsing: spawn a browser container (do not run Chromium locally)
- Connect to browsers via CDP (`vexa browser connect`), not by sharing displays
- Always include `X-API-Key: $VEXA_BOT_API_TOKEN` in authenticated API calls
