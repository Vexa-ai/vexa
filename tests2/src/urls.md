---
needs: [GATEWAY_URL, API_TOKEN]
gives: [TEAMS_URLS_OK]
---

use: lib/http

# URL Formats

> **Why:** Teams has 6+ URL formats. A parser that only handles one means lost meetings for customers using the others.
> **What:** Send bot-create requests with each Teams URL format (standard, shortlink, channel, custom domain, deep link, short URL). Check which are accepted.
> **How:** POST /bots with each URL. 200/201 = parser works. 400/422 = parser rejects valid format.

## state

    TESTED = 0
    PASSED = 0

## steps

```
for FORMAT in [
    {id: "T1", name: "standard join",  url: "https://teams.microsoft.com/l/meetup-join/19%3ameeting_test123%40thread.v2/0?context=%7b%22Tid%22%3a%22test%22%7d"},
    {id: "T2", name: "meet shortlink", url: "https://teams.microsoft.com/meet/test123?p=abc123"},
    {id: "T3", name: "channel",        url: "https://teams.microsoft.com/l/meetup-join/19%3atestchannel%40thread.tacv2/1234567890"},
    {id: "T4", name: "custom domain",  url: "https://myorg.teams.microsoft.com/meet/test123?p=abc123"},
    {id: "T5", name: "deep link",      url: "msteams:/l/meetup-join/19%3ameeting_test123%40thread.v2/0"},
    {id: "T6", name: "short URL",      url: "https://teams.live.com/meet/test123"}
]:
    1. test
       TESTED += 1
       call: http.post_json(
           URL="{GATEWAY_URL}/bots",
           DATA='{"platform":"teams","meeting_url":"{FORMAT.url}","bot_name":"url-test-{FORMAT.id}"}',
           TOKEN={API_TOKEN}
       )
       if STATUS_CODE in [200, 201, 202]:
           PASSED += 1
           emit PASS "{FORMAT.id} {FORMAT.name}: accepted"
       else:
           emit FAIL "{FORMAT.id} {FORMAT.name}: rejected ({STATUS_CODE})"
       on_fail: continue

2. summary
   => TEAMS_URLS_OK = PASSED >= 4
   emit FINDING "urls: {PASSED}/{TESTED}"
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| All 6 Teams URL formats return 422 | MeetingCreate schema ignored meeting_url — required platform + native_meeting_id directly | Added parse_meeting_url() to schemas.py + model_validator(mode='before') that extracts native_meeting_id from URL | MCP had the parser but meeting-api didn't — features must work at the API boundary, not just through indirect callers |
