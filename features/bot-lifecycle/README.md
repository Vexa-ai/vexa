# Bot Lifecycle

## Why

Bots must progress through a predictable lifecycle: requested → joining → awaiting_admission → active → stopping → completed. Each state transition must be observable via API and trigger appropriate side effects.

## What

```
POST /bots → requested → runtime-api creates container → joining
  → platform-specific join → awaiting_admission → host admits → active
  → DELETE /bots or timeout → stopping → recording upload → completed
```

### States

| State | Meaning | Transitions to |
|-------|---------|---------------|
| requested | API accepted, container creating | joining |
| joining | Container running, navigating to meeting | awaiting_admission |
| awaiting_admission | In lobby, waiting for host | active |
| active | In meeting, capturing audio | stopping |
| stopping | Leave requested, uploading recording | completed |
| completed | Done, container stopped | — |
| failed | Error at any point | — |

### Components

| Component | File | Role |
|-----------|------|------|
| bot creation | `services/meeting-api/meeting_api/meetings.py` | Create meeting record, spawn container |
| status callbacks | `services/meeting-api/meeting_api/meetings.py` | Bot container reports status changes |
| runtime container | `services/runtime-api/` | Container lifecycle management |
| bot core | `services/vexa-bot/core/src/index.ts` | Meeting join, state machine |

## How

### 1. Create a bot (join a meeting)

```bash
curl -s -X POST http://localhost:8056/bots \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "meeting_url": "https://meet.google.com/abc-defg-hij",
    "bot_name": "Vexa Notetaker"
  }'
# {"bot_id": 135, "status": "requested", "platform": "gmeet", ...}
```

### 2. Poll bot status

```bash
curl -s -H "X-API-Key: $VEXA_API_KEY" \
  http://localhost:8056/bots/gmeet/135
# {"bot_id": 135, "status": "active", "platform": "gmeet", ...}
```

State transitions: `requested` -> `joining` -> `awaiting_admission` -> `active` -> `stopping` -> `completed`.

### 3. List all bots

```bash
curl -s -H "X-API-Key: $VEXA_API_KEY" \
  http://localhost:8056/bots
# [{"bot_id": 135, "status": "active", ...}, ...]
```

### 4. Stop a bot (leave the meeting)

```bash
curl -s -X DELETE -H "X-API-Key: $VEXA_API_KEY" \
  http://localhost:8056/bots/gmeet/135
# 200 {"status": "stopping"}
```

The bot uploads its recording, then transitions to `completed`.

### 5. Teams example

```bash
curl -s -X POST http://localhost:8056/bots \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "meeting_url": "https://teams.microsoft.com/l/meetup-join/...",
    "bot_name": "Vexa Notetaker"
  }'
# {"bot_id": 125, "status": "requested", "platform": "teams", ...}
```

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|-------|--------|----------|--------------|-------|
| 1 | POST /bots creates bot and returns id | 15 | ceiling | 0 | PASS | Bot created on both platforms (GMeet 135, Teams 125/126, browser 131) | 2026-04-05T21:50Z | 07-bot-lifecycle, 02-api |
| 2 | Bot reaches active state in live meeting | 20 | ceiling | 0 | PASS | Full chain requested→joining→awaiting_admission→active on both platforms. All transitions from bot_callback. | 2026-04-05T21:50Z | 07-bot-lifecycle |
| 3 | DELETE /bots stops bot, reaches completed | 15 | ceiling | 0 | PASS | 4/4 current-run bots: completed, reason=stopped, end_time set. Chains: active→stopping→completed (GMeet 135, Teams 125, 126), stopping→completed (browser 131). 2 stale bots (116, 117) stuck in stopping from previous run — process gone, state not reconciled. | 2026-04-05T21:50Z | 11-finalization |
| 4 | Status visible via GET /bots | 15 | — | 0 | PASS | All state transitions observable via API | 2026-04-05T21:50Z | 07-bot-lifecycle, 04-dashboard |
| 5 | Timeout auto-stop works (no infinite bots) | 15 | — | 0 | SKIP | Not tested this run. noOneJoinedTimeout=120s too short for human-in-loop tests. | 2026-04-05T21:50Z | 14-container-lifecycle |
| 6 | Works for GMeet, Teams, browser_session | 20 | — | 0 | PASS | GMeet, Teams, and browser_session all verified. Browser session cleanup confirmed in finalization. | 2026-04-05T21:50Z | 07-bot-lifecycle, 05-browser-session |

07 owns `requested → joining → awaiting_admission → active`.
11-finalization owns `active → stopping → completed` (runs after transcription tests).
14-container-lifecycle owns orphan sweep after everything stops.

Confidence: 75 (ceiling items 1+2+3 pass = 50; items 4+6 = 35; timeout auto-stop not tested; 2 stale bots from previous run stuck in stopping = deduction for state reconciliation gap)
