---
needs: [GATEWAY_URL, API_TOKEN, MEETING_URL, MEETING_PLATFORM, NATIVE_MEETING_ID]
gives: [RECORDER_ID, BOT_STATUS]
---

use: lib/http

# Bot Lifecycle

> **Why:** The bot state machine (requested -> joining -> awaiting_admission -> active) is the core contract. Any deviation is a bug.
> **What:** Launch a recorder bot, poll status transitions, get human to admit it, verify it reaches active.
> **How:** POST /bots, poll GET /bots/status every 10s logging each transition, human admits from meeting UI, verify final state is active.

## state

    RECORDER_ID = ""
    BOT_STATUS  = ""
    TRANSITIONS = []

## steps

```
1. launch
   call: http.post_json(
       URL="{GATEWAY_URL}/bots",
       DATA='{"platform":"{MEETING_PLATFORM}","native_meeting_id":"{NATIVE_MEETING_ID}","bot_name":"Recorder","transcribe_enabled":true,"automatic_leave":{"no_one_joined_timeout":300000}}',
       TOKEN={API_TOKEN}
   )
   expect: STATUS_CODE in [200, 201]
   => RECORDER_ID = BODY.id
   on_fail: stop

2. poll
   > Poll every 10s. Log every transition. Stop on awaiting_admission, active, or terminal.
   do: |
       PREV=""
       for i in $(seq 1 30); do
           STATUS=$(curl -sf -H "X-API-Key: {API_TOKEN}" "{GATEWAY_URL}/bots/status" | \
               python3 -c "import sys,json; bots=json.load(sys.stdin).get('running_bots',[]); [print(b['status']) for b in bots if b.get('native_meeting_id')=='{NATIVE_MEETING_ID}']" 2>/dev/null | head -1)
           [ "$STATUS" != "$PREV" ] && echo "$(date +%H:%M:%S) $PREV -> $STATUS" && PREV="$STATUS"
           case "$STATUS" in
               awaiting_admission) echo "AWAITING"; exit 0;;
               active) echo "ACTIVE"; exit 0;;
               failed|error|ended) echo "TERMINAL:$STATUS"; exit 1;;
           esac
           sleep 10
       done
       echo "TIMEOUT"; exit 1
   => BOT_STATUS
   if BOT_STATUS == "awaiting_admission": emit PASS "bot in waiting room"
   if BOT_STATUS == "active": emit PASS "bot active (no waiting room)"
   if BOT_STATUS in [failed, error, ended]: emit FAIL "bot terminal: {BOT_STATUS}"
   on_fail: stop

3. admit [human]
   if BOT_STATUS == "awaiting_admission":
       ask: "Bot is waiting in meeting. Admit it, then type 'done'."
   on_fail: stop

4. verify_active
   call: http.poll_until(URL="{GATEWAY_URL}/bots/status", TOKEN={API_TOKEN}, FIELD="status", VALUE="active", MAX=6, INTERVAL=5)
   => BOT_STATUS = "active"
   emit PASS "bot active"
   on_fail: stop

5. verify_transitions
   call: http.get_json(URL="{GATEWAY_URL}/bots/status", TOKEN={API_TOKEN})
   expect: transitions contain requested -> joining -> awaiting_admission -> active
   => TRANSITIONS
   emit FINDING "lifecycle: {TRANSITIONS}"
   on_fail: continue
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| Bot reaches "failed" after successful meeting | self_initiated_leave exits with code 1, meeting-api treats non-zero as failure | callbacks.py: added branch for self_initiated_leave during stopping → completed | 2026-04-06: 30 meetings in DB with "failed" status but transcripts. Exit code semantics matter — graceful leave is not a crash |
| /bots/status returns 422 | Starlette matches /bots/{meeting_id} instead of /bots/status | Renamed to /bots/id/{meeting_id} in meetings.py and main.py | 2026-04-06: static and parameterized routes on same prefix are ambiguous |
| Bot timeout in waiting room | no_one_joined_timeout default 120s too short | Set automatic_leave.no_one_joined_timeout: 300000 in POST /bots | Default is for production, tests need 5min for human context-switching |
