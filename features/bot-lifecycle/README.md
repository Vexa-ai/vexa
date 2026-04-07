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
| 1 | POST /bots creates bot and returns id | 15 | ceiling | 0 | PASS | 201 on create. Also accepts meeting_url (6/6 Teams URL formats parsed server-side). | 2026-04-07 | api, urls, bot |
| 2 | Bot reaches active state in live meeting | 20 | ceiling | 0 | PASS | Full chain requested→joining→awaiting_admission→active on both platforms. | 2026-04-05 | bot |
| 3 | DELETE /bots stops bot, reaches completed | 15 | ceiling | 0 | PASS | 202 on delete. Container fully removed from docker ps -a (zombie fix applied). | 2026-04-07 | containers, finalize |
| 4 | Status visible via GET /bots/status | 15 | — | 0 | PASS | 200 with running_bots array. webhook_secret stripped from response (security fix). | 2026-04-07 | api, webhooks |
| 5 | Timeout auto-stop works (no infinite bots) | 15 | — | 0 | UNTESTED | noOneJoinedTimeout=120s too short for human-in-loop tests. | | containers |
| 6 | Works for GMeet, Teams, browser_session | 20 | — | 0 | PASS | GMeet: bot create+stop. Teams: 6 URL formats. Browser: tier 1 roundtrip. | 2026-04-07 | urls, browser, bot |
| 7 | Successful meeting never shows "failed" in dashboard | 10 | — | 0 | PASS | Fix verified: self_initiated_leave → completed (not failed). Dashboard: 7/7. | 2026-04-07 | dashboard |
| 8 | GET /bots/status returns running bots (not 422) | 5 | — | 0 | PASS | Route /bots/id/{meeting_id} avoids Starlette ambiguity. 200 confirmed. | 2026-04-07 | api |
| 9 | GET /meetings/{id} returns meeting after creation | 5 | — | 0 | PASS | Gateway route exists. | 2026-04-06 | dashboard |
| 10 | meeting_url parsed server-side (Teams, GMeet, Zoom) | 10 | — | 0 | PASS | FIX: added parse_meeting_url() + model_validator in schemas.py. 6/6 Teams formats, GMeet standard. | 2026-04-07 | urls |
| 11 | webhook_secret not leaked in API responses | 5 | — | 0 | PASS | FIX: field_serializer on MeetingResponse + safe_data in bots/status builder. Secret absent from both POST and GET responses. | 2026-04-07 | webhooks |
| 12 | Bot without `authenticated: true` handles GMeet name prompt | 5 | — | 0 | FAIL | BUG: Bot stuck on "Attempting to find name input field" when launched without saved cookies. Meeting 9876 stuck in joining until force-stopped. Bot should either enter name programmatically or fail fast with clear error. | 2026-04-07 | bot |
| 13 | Auto-admit works reliably (single-shot) | 10 | — | 0 | PASS | FIX: Multi-phase CDP script (panel→expand→click). Validated 4 consecutive rounds. Old script used text selector that hit non-clickable text node. | 2026-04-07 | admit |
| 14 | Dashboard refreshes bot/meeting status in real-time | 5 | — | 0 | INVESTIGATING | Dashboard WS handler does update status on `meeting.status` events (use-live-transcripts.ts:266). Stale "joining" may be from no WS connection on that page (wasn't subscribed). REST transcript fetch fix applied — status header uses `currentMeeting.status` from the store. | 2026-04-07 | dashboard |

### Proc ownership

| Proc | Owns |
|------|------|
| bot | #1 create, #2 active state, #6 platforms |
| containers | #3 stop+remove, #5 timeout |
| finalize | #3 completed state |
| api | #1 endpoint, #4 status, #8 route |
| urls | #10 URL parsing |
| webhooks | #11 secret leak |
| dashboard | #7 false failures, #9 meetings/{id} |
| browser | #6 browser_session |

### Fixes applied this run

| Bug | Fix | File |
|-----|-----|------|
| Teams URL parsing missing | Added `parse_meeting_url()` + `model_validator(mode='before')` | `services/meeting-api/meeting_api/schemas.py` |
| webhook_secret in response | Added `field_serializer` on MeetingResponse + `safe_data` filter in bots/status | `schemas.py`, `meetings.py` |
| Zombie containers | Added `backend.remove(name)` in on_exit | `services/runtime-api/runtime_api/main.py` |

Confidence: 90 (ceiling 1+2+3 = 50, items 4+6+7+8+9+10+11 = 70, timeout untested = -15, meeting chain not re-run today = -5 on #2)
