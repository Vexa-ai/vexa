# Meeting Chat

## Why

Read and write meeting chat messages programmatically. Enables chatbots, command-driven meeting control, and chat transcript capture alongside audio transcription.

## What

```
Read:  GET /bots/{platform}/{id}/chat → chat messages from meeting
Write: POST /bots/{platform}/{id}/chat {text} → Redis PUBLISH → bot types in meeting chat
```

### Components

| Component | File | Role |
|-----------|------|------|
| chat endpoints | `services/meeting-api/meeting_api/voice_agent.py` | REST for read/write |
| chat handler | `services/vexa-bot/core/src/browser-session.ts` | Playwright types into chat DOM |

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | POST /chat sends message visible to participants | 30 | ceiling | 0 | PASS | GMeet: POST /chat → 200. Teams: POST /chat → 202. Messages sent during active meeting. | 2026-04-05T21:41Z | 09-verify-transcription |
| 2 | GET /chat returns messages from meeting | 30 | ceiling | 0 | PARTIAL | GMeet: GET /chat → 2 messages (send+receive). Teams: GET /chat fails after meeting completes — BUG #29 (_find_active_meeting blocks read). Redis has chat data but API won't serve it. | 2026-04-05T21:41Z | 09-verify-transcription |
| 3 | Works on GMeet and Teams | 20 | — | 0 | PARTIAL | GMeet chat read+write works. Teams write works, read fails after meeting ends (BUG #29). | 2026-04-05T21:41Z | 09-verify-transcription |
| 4 | Chat messages persisted after meeting ends | 20 | — | 0 | FAIL | Teams chat in Redis (meeting:126:chat_messages, 2 entries) but inaccessible via API. BUG #29. | 2026-04-05T21:41Z | 10-verify-post-meeting |

Confidence: 30 (item 1 PASS = 30; item 2 PARTIAL — GMeet works, Teams blocked by BUG #29; items 3-4 partial/fail)

## Known Issues

### GET /chat fails for completed meetings (bug #29)

`GET /bots/{platform}/{id}/chat` returns an error after the meeting completes because `_find_active_meeting()` blocks read access to meetings that are no longer active. Chat messages are stored in Redis during the meeting but become inaccessible after the bot leaves.

**Root cause:** `voice_agent.py` uses `_find_active_meeting()` which filters by active status. After bot completion, the meeting is no longer "active" and the function returns None, causing a 404 or error response.

**Impact:** Dashboard can't show chat history for completed meetings. Chat data is orphaned in Redis.

**Fix needed:** Use a read-path function that doesn't require active status, or persist chat messages to Postgres before meeting completion.
