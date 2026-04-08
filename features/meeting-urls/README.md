---
services: [meeting-api, mcp]
tests3:
  targets: [contracts]
  checks: [URL_PARSER_EXISTS, GMEET_URL_PARSED, INVALID_URL_REJECTED, TEAMS_URL_STANDARD, TEAMS_URL_SHORTLINK, TEAMS_URL_CHANNEL, TEAMS_URL_ENTERPRISE, TEAMS_URL_PERSONAL]
---

# Meeting URLs

## Why

Users paste meeting URLs in various formats — scheduled links, instant meetings, channel meetings, custom enterprise domains, deep links. Every format must be parsed correctly to extract the platform, native meeting ID, and passcode. A 400 error on a valid URL means a lost meeting.

## What

```
User pastes URL → MCP /parse-meeting-link → {platform, native_meeting_id, passcode}
  → POST /bots with extracted fields → bot joins the correct meeting
```

### Supported formats

| Platform | Formats |
|----------|---------|
| **Google Meet** | `meet.google.com/{code}`, `meet.new` redirect |
| **Teams standard** | `/l/meetup-join/19%3ameeting_{id}%40thread.v2/...` |
| **Teams short** | `/meet/{numeric_id}?p={passcode}` (OeNB format) |
| **Teams channel** | `/l/meetup-join/19%3a{channel}%40thread.tacv2/...` |
| **Teams custom domain** | `{org}.teams.microsoft.com/meet/{id}?p={passcode}` |
| **Teams personal** | `teams.live.com/meet/{id}?p={passcode}` |
| **Teams deep link** | `msteams:/l/meetup-join/...` |
| **Zoom** | `zoom.us/j/{id}?pwd={password}` |

### Components

| Component | File | Role |
|-----------|------|------|
| URL parser | `services/mcp/main.py` | Parse URL → platform + native_meeting_id + passcode |
| Validation | `services/meeting-api/meeting_api/schemas.py` | Validate extracted fields |
| Bot creation | `services/meeting-api/meeting_api/meetings.py` | Construct meeting URL from parts |

## How

### 1. Parse a meeting URL via MCP

```bash
# Google Meet
curl -s -X POST http://localhost:8056/mcp/parse-meeting-link \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://meet.google.com/abc-defg-hij"}'
# {"platform": "gmeet", "native_meeting_id": "abc-defg-hij", "passcode": null}

# Teams standard
curl -s -X POST http://localhost:8056/mcp/parse-meeting-link \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc%40thread.v2/0?context=..."}'
# {"platform": "teams", "native_meeting_id": "19:meeting_abc@thread.v2", "passcode": null}

# Teams short link with passcode
curl -s -X POST http://localhost:8056/mcp/parse-meeting-link \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://teams.microsoft.com/meet/12345678?p=ABCDEF"}'
# {"platform": "teams", "native_meeting_id": "12345678", "passcode": "ABCDEF"}

# Teams custom enterprise domain
curl -s -X POST http://localhost:8056/mcp/parse-meeting-link \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://acme.teams.microsoft.com/meet/12345?p=XYZ"}'
# {"platform": "teams", "native_meeting_id": "12345", "passcode": "XYZ"}
```

### 2. Use parsed fields to create a bot

```bash
curl -s -X POST http://localhost:8056/bots \
  -H "X-API-Key: $VEXA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "meeting_url": "https://teams.microsoft.com/meet/12345678?p=ABCDEF",
    "bot_name": "Vexa Notetaker"
  }'
# meeting-api internally parses the URL and joins the correct meeting
# {"bot_id": 126, "status": "requested", "platform": "teams", ...}
```

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|-------|--------|----------|--------------|-------|
| 1 | Google Meet URL parsed correctly | 15 | ceiling | 0 | PASS | GMEET_URL_PARSED contract check: POST /bots with `meet.google.com/abc-defg-hij` → accepted | 2026-04-08 | GMEET_URL_PARSED |
| 2 | Teams standard join URL parsed | 15 | ceiling | 0 | PASS | TEAMS_URL_STANDARD contract check: POST /bots with `/l/meetup-join/` → accepted | 2026-04-08 | TEAMS_URL_STANDARD |
| 3 | Teams short URL with passcode parsed | 20 | ceiling | 0 | PASS | TEAMS_URL_SHORTLINK contract check: POST /bots with `meeting_url` only (no explicit platform) → accepted. Live e2e: `meeting-tts-teams.sh` — 3 bots joined, 4/4 TTS sent, 5 segments transcribed, 3/4 phrases matched | 2026-04-08 | TEAMS_URL_SHORTLINK, meeting-tts-teams.sh |
| 4 | Teams channel meeting URL parsed | 10 | — | 0 | PASS | TEAMS_URL_CHANNEL contract check: POST /bots with `/l/meetup-join/...tacv2` → accepted | 2026-04-08 | TEAMS_URL_CHANNEL |
| 5 | Teams custom enterprise domain parsed | 15 | — | 0 | PASS | TEAMS_URL_ENTERPRISE contract check: POST /bots with `myorg.teams.microsoft.com/meet/` (no explicit platform) → accepted | 2026-04-08 | TEAMS_URL_ENTERPRISE |
| 6 | Teams personal (teams.live.com) parsed | 10 | — | 0 | PASS | TEAMS_URL_PERSONAL contract check: POST /bots with `teams.live.com/meet/` (no explicit platform) → accepted | 2026-04-08 | TEAMS_URL_PERSONAL |
| 7 | Teams deep link (msteams:/) parsed | 10 | — | 0 | PASS | Unit test: `test_v2_deep_link` in `services/mcp/tests/test_parse_meeting_url.py`. Server-side `parse_meeting_url()` converts `msteams:` → `https://` | 2026-04-08 | unit test |
| 8 | POST /bots accepts meeting_url directly (no MCP required) | 15 | ceiling | 0 | PASS | All 3 Teams contract checks (SHORTLINK, ENTERPRISE, PERSONAL) send only `meeting_url` — no explicit `platform` or `native_meeting_id`. model_validator auto-parses. Live e2e confirmed with `meeting-tts-teams.sh` | 2026-04-08 | TEAMS_URL_SHORTLINK, meeting-tts-teams.sh |
| 9 | Invalid URLs rejected with clear error | 10 | — | 0 | PASS | INVALID_URL_REJECTED contract check: POST /bots with `not-a-url` → 422 | 2026-04-08 | INVALID_URL_REJECTED |

Confidence: 100 (all 9 checks PASS. Live Teams e2e validated with `meeting-tts-teams.sh`: URL parse → bot join → admission → TTS → transcription → scoring.)
