---
services:
- meeting-api
- mcp
---

# Meeting URLs

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 100%**

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


<!-- BEGIN AUTO-DOD -->
<!-- Auto-written by tests3/lib/aggregate.py from release tag `0.10.0-260430-1701`. Do not edit by hand — edit the sidecar `dods.yaml` + re-run `make -C tests3 report --write-features`. -->

**Confidence: 10%** (gate: 100%, status: ❌ below gate)

| # | Behavior | Weight | Status | Evidence (modes) |
|---|----------|-------:|:------:|------------------|
| url-parser-exists | meeting-api has a URL parser module (url_parser.py) that handles platform detection | 10 | ✅ pass | `lite`: smoke-static/URL_PARSER_EXISTS: MeetingCreate schema has parse_meeting_url — accepts meeting_url field directly; `compose`: smoke-static/URL_PARSER_EXISTS: MeetingCreate schema has parse_meeting_url — accepts meeting_url field directly; `helm`: smoke-static/URL_PARSER_EXISTS: MeetingCreat… |
| gmeet-parsed | Google Meet URL (meet.google.com/xxx-xxxx-xxx) parses correctly | 15 | ❌ fail | `lite`: smoke-contract/GMEET_URL_PARSED: Google Meet URL accepted by POST /bots — parser handles GMeet format; `compose`: smoke-contract/GMEET_URL_PARSED: Google Meet URL accepted by POST /bots — parser handles GMeet format; `helm`: smoke-contract/GMEET_URL_PARSED: HTTP 401 (expected one of [200,… |
| invalid-rejected | Invalid meeting URL returns 400 (not 500) | 10 | ❌ fail | `lite`: smoke-contract/INVALID_URL_REJECTED: garbage URLs rejected with 400/422 — input validation works; `compose`: smoke-contract/INVALID_URL_REJECTED: garbage URLs rejected with 400/422 — input validation works; `helm`: smoke-contract/INVALID_URL_REJECTED: HTTP 401 (expected one of [400, 422]) |
| teams-standard | Teams standard link (teams.microsoft.com/l/meetup-join/...) parses | 15 | ❌ fail | `lite`: smoke-contract/TEAMS_URL_STANDARD: Teams standard join URL accepted by POST /bots; `compose`: smoke-contract/TEAMS_URL_STANDARD: Teams standard join URL accepted by POST /bots; `helm`: smoke-contract/TEAMS_URL_STANDARD: HTTP 401 (expected one of [200, 201, 202, 403, 409, 500]) |
| teams-shortlink | Teams shortlink (teams.live.com, teams.microsoft.com/meet) parses | 10 | ❌ fail | `lite`: smoke-contract/TEAMS_URL_SHORTLINK: Teams /meet/ shortlink URL parsed and accepted by POST /bots (no explicit platform needed); `compose`: smoke-contract/TEAMS_URL_SHORTLINK: Teams /meet/ shortlink URL parsed and accepted by POST /bots (no explicit platform needed); `helm`: smoke-contract… |
| teams-channel | Teams channel meeting URL parses | 10 | ❌ fail | `lite`: smoke-contract/TEAMS_URL_CHANNEL: Teams channel meeting URL accepted or known gap; `compose`: smoke-contract/TEAMS_URL_CHANNEL: Teams channel meeting URL accepted or known gap; `helm`: smoke-contract/TEAMS_URL_CHANNEL: HTTP 401 (expected one of [200, 201, 202, 403, 409, 422, 500]) |
| teams-enterprise | Teams enterprise-tenant URL parses (custom domain) | 15 | ❌ fail | `lite`: smoke-contract/TEAMS_URL_ENTERPRISE: Teams enterprise domain URL parsed and accepted by POST /bots (no explicit platform needed); `compose`: smoke-contract/TEAMS_URL_ENTERPRISE: Teams enterprise domain URL parsed and accepted by POST /bots (no explicit platform needed); `helm`: smoke-cont… |
| teams-personal | Teams personal-account URL parses | 15 | ❌ fail | `lite`: smoke-contract/TEAMS_URL_PERSONAL: Teams personal (teams.live.com) URL parsed and accepted by POST /bots (no explicit platform needed); `compose`: smoke-contract/TEAMS_URL_PERSONAL: Teams personal (teams.live.com) URL parsed and accepted by POST /bots (no explicit platform needed); `helm`… |

<!-- END AUTO-DOD -->

