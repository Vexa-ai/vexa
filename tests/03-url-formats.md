---
id: test/teams-url-formats
type: validation
requires: [test/infra-up, test/api-full]
produces: [TEAMS_URLS_OK]
validates: [meeting-urls]
docs: [features/meeting-urls/README.md, services/mcp/README.md]
mode: machine
---

# Teams URL Format Testing

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Verify meeting-api correctly parses all known Microsoft Teams meeting URL formats. Critical for OeNB (Austrian central bank) which uses the `/meet/{id}?p=` format.

## Why

Teams has multiple URL formats depending on how the meeting was created (scheduled, instant, channel, enterprise). Bot creation must extract the native meeting ID from all formats. A 400 error on a valid Teams URL means lost meetings.

## Inputs

| Name | From | Default | Description |
|------|------|---------|-------------|
| GATEWAY_URL | test/infra-up | — | API gateway URL |
| API_TOKEN | test/api-full | — | Token with bot scope |

## URL Formats

| # | Format | Pattern | Source |
|---|--------|---------|--------|
| T1 | Standard join | `https://teams.microsoft.com/l/meetup-join/19%3ameeting_{id}%40thread.v2/0?context=...` | Calendar-scheduled meetings |
| T2 | Meet shortlink | `https://teams.microsoft.com/meet/{id}?p={passcode}` | OeNB, instant meetings |
| T3 | Channel meeting | `https://teams.microsoft.com/l/meetup-join/19%3a{channel}%40thread.tacv2/...` | Channel-based meetings |
| T4 | Custom domain | `https://{org}.teams.microsoft.com/meet/{id}?p={passcode}` | Enterprise custom domains |
| T5 | Deep link | `msteams:/l/meetup-join/...` | Desktop app deep links |
| T6 | Short URL | `https://teams.live.com/meet/{id}` | Personal/consumer Teams |

## Procedure

### 1. Find URL parser

Locate where meeting-api parses Teams URLs:
- Search for URL parsing logic: regex, URL destructuring, or Teams-specific parsing
- Identify what `native_meeting_id` is extracted as
- Document the parser location for future reference

### 2. Test each format

For each URL format T1-T6:

```bash
# Send bot create request with the Teams URL
curl -s -X POST "$GATEWAY_URL/bots" \
  -H "X-API-Key: $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "teams",
    "native_meeting_id": "'$MEETING_ID'",
    "meeting_url": "'$TEAMS_URL'"
  }'
```

Expected responses:
- **PASS:** 201 (bot created) or bot reaches `joining` state — URL was parsed
- **PASS:** 200/202 with valid bot ID — URL accepted
- **FAIL:** 400 "invalid meeting URL" — parser doesn't recognize format
- **FAIL:** 422 "cannot extract meeting ID" — parser partial, regex too strict

Note: Bot will likely fail to actually join (no real meeting), but it must ACCEPT the URL and extract the meeting ID. The test is URL parsing, not meeting joining.

### 3. Test OeNB-specific format

The critical format for OeNB:

```
https://teams.microsoft.com/meet/{numeric_id}?p={alphanumeric_passcode}
```

Verify:
- `native_meeting_id` extracted correctly (the numeric part)
- Passcode `p=` parameter preserved and passed to bot
- Bot doesn't reject the URL

### 4. Record results

```
TEAMS URL FORMAT RESULTS — YYYY-MM-DD
T1 standard join:    {PASS|FAIL} — native_meeting_id={extracted}
T2 meet shortlink:   {PASS|FAIL} — native_meeting_id={extracted}
T3 channel meeting:  {PASS|FAIL} — native_meeting_id={extracted}
T4 custom domain:    {PASS|FAIL} — native_meeting_id={extracted}
T5 deep link:        {PASS|FAIL|SKIP} — native_meeting_id={extracted}
T6 short URL:        {PASS|FAIL|SKIP} — native_meeting_id={extracted}
```

## Outputs

| Name | Description |
|------|-------------|
| TEAMS_URLS_OK | true if T1-T4 all pass (T5, T6 optional) |
| FORMATS_TESTED | Count of formats tested |
| PARSER_LOCATION | File:line where URL parsing lives |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| 400 on /meet/{id}?p= URL | Parser only handles /l/meetup-join/ format | Add /meet/ pattern to URL parser regex | Teams has 6+ URL formats — parser must handle all |
| Passcode lost | Parser extracts meeting ID but drops ?p= query param | Pass full URL to bot, not just extracted ID | Some Teams meetings require passcode to join |
| Custom domain rejected | Parser hardcodes `teams.microsoft.com` | Use URL parsing that allows subdomains | Enterprise Teams uses custom domains |

## Docs ownership

After this test runs, verify and update:

- **features/meeting-urls/README.md**
  - DoD table: update Status, Evidence, Last checked for items #1 (Google Meet), #2 (Teams standard), #3 (Teams short/OeNB), #4 (Teams channel), #5 (Teams custom domain), #6 (Teams personal/teams.live.com), #7 (invalid URL rejection)
  - Supported formats table: verify each format pattern (Google Meet, Teams standard, Teams short, Teams channel, Teams custom domain, Teams personal, Teams deep link, Zoom) matches the actual regex/parser behavior observed during test — add or remove formats if the parser handles more or fewer than documented
  - Components table: verify URL parser location (`services/mcp/main.py`), validation (`services/meeting-api/meeting_api/schemas.py`), and bot creation (`services/meeting-api/meeting_api/meetings.py`) file paths match where the parsing logic actually lives (record PARSER_LOCATION output)
  - Confidence score: recalculate after updating statuses

- **services/mcp/README.md**
  - `parse_meeting_link` tool: verify it accepts all URL formats T1-T6 that passed the test and correctly returns `{platform, native_meeting_id, passcode}`
  - Teams Passcodes and URL Limitations section: verify the documented limitation ("Only Teams Free style links are supported: `teams.live.com/meet/...`") matches actual test results — if T1 (standard join `/l/meetup-join/`) now works, update the claim that "teams.microsoft.com/l/meetup-join/... links are not supported yet"
  - Passcode constraints: verify the documented "8-20 alphanumeric characters" constraint matches actual validation behavior observed during the test
  - Troubleshooting section: verify the Teams meeting troubleshooting advice reflects which URL formats actually work vs. fail
