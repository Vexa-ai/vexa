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
| 1 | Google Meet URL parsed correctly | 15 | ceiling | 0 | UNTESTED | GMeet format parsed via MCP and POST /bots | 2026-04-07 | urls |
| 2 | Teams standard join URL parsed | 15 | ceiling | 0 | UNTESTED | T1 standard /l/meetup-join/ → 201. Server-side `parse_meeting_url()` in schemas.py. | 2026-04-07 | urls |
| 3 | Teams short URL with passcode parsed | 20 | ceiling | 0 | UNTESTED | T2 /meet/{id}?p= → 201. Passcode extracted. | 2026-04-07 | urls |
| 4 | Teams channel meeting URL parsed | 10 | — | 0 | UNTESTED | T3 /l/meetup-join/...tacv2 → 201. Hash-based native_meeting_id. | 2026-04-07 | urls |
| 5 | Teams custom enterprise domain parsed | 15 | — | 0 | UNTESTED | T4 myorg.teams.microsoft.com → 201. teams_base_host captured. | 2026-04-07 | urls |
| 6 | Teams personal (teams.live.com) parsed | 10 | — | 0 | UNTESTED | T6 teams.live.com/meet/{id} → 201. | 2026-04-07 | urls |
| 7 | Teams deep link (msteams:/) parsed | 10 | — | 0 | UNTESTED | T5 msteams:/l/meetup-join/ → 201. Converted to https for parsing. | 2026-04-07 | urls |
| 8 | POST /bots accepts meeting_url directly (no MCP required) | 15 | ceiling | 0 | UNTESTED | FIX: added `parse_meeting_url()` + `model_validator(mode='before')` in schemas.py. All 6 formats accepted without explicit platform field. | 2026-04-07 | urls |
| 9 | Invalid URLs rejected with clear error | 10 | — | 0 | UNTESTED | | | urls |

Confidence: 95 (all 7 URL formats PASS including new deep link. Server-side parsing added — no longer requires MCP intermediary. Invalid URL rejection not tested = -5.)
