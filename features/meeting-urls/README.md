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

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|-------|--------|----------|--------------|-------|
| 1 | Google Meet URL parsed correctly | 15 | ceiling | 0 | PASS | GMeet format parsed | 2026-04-05T19:40Z | 03-url-formats, 06-create-meeting |
| 2 | Teams standard join URL parsed | 15 | ceiling | 0 | PASS | T1 standard format parsed | 2026-04-05T19:40Z | 03-url-formats, 06-create-meeting |
| 3 | Teams short URL with passcode parsed (OeNB) | 20 | ceiling | 0 | PASS | T2 shortlink/OeNB format parsed | 2026-04-05T19:40Z | 03-url-formats, 06-create-meeting |
| 4 | Teams channel meeting URL parsed | 10 | — | 0 | PASS | T3 channel format parsed | 2026-04-05T19:40Z | 03-url-formats |
| 5 | Teams custom enterprise domain parsed | 15 | — | 0 | PASS | T4 custom domain format parsed | 2026-04-05T19:40Z | 03-url-formats |
| 6 | Teams personal (teams.live.com) parsed | 10 | — | 0 | PASS | T6 teams.live.com format parsed | 2026-04-05T19:40Z | 03-url-formats, 06-create-meeting |
| 7 | Invalid URLs rejected with clear error | 15 | — | 0 | SKIP | Not explicitly tested this run | 2026-04-05T19:40Z | 03-url-formats, 02-api |

Confidence: 85 (ceiling items 1+2+3 pass = 50; items 4+5+6 = 35; invalid URL rejection not tested = 85/100)
