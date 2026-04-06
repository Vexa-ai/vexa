---
needs: [GATEWAY_URL, API_TOKEN, MEETING_PLATFORM, NATIVE_MEETING_ID]
gives: [FINALIZATION_OK]
---

use: lib/http

# Finalize

> **Why:** Bots that don't shut down cleanly leak containers, lose recordings, and leave ghost entries in the database.
> **What:** Stop all bots for a meeting and verify each reaches `completed` with `completion_reason=stopped` and `end_time` set.
> **How:** DELETE /bots for each token, wait, verify status=completed and transition chain ends with active -> stopping -> completed.

## state

    TOKENS          = []
    STOPPED         = 0
    FINALIZATION_OK = false

## steps

```
1. collect_tokens
   > All tokens that have bots in this meeting.
   => TOKENS = [API_TOKEN, SPEAKER_TOKEN]
   on_fail: stop

2. stop
   for TOKEN in TOKENS:
       call: http.delete(URL="{GATEWAY_URL}/bots/{MEETING_PLATFORM}/{NATIVE_MEETING_ID}", TOKEN={TOKEN})
       emit PASS "stop sent"
       on_fail: continue

3. wait
   do: sleep 15

4. verify
   for TOKEN in TOKENS:
       call: http.get_json(URL="{GATEWAY_URL}/bots/status", TOKEN={TOKEN})
       > Find bot, check status.
       if status == "completed":
           STOPPED += 1
           expect: completion_reason == "stopped"
           expect: end_time is set
           emit PASS "bot completed cleanly"
       else:
           emit FAIL "bot in '{status}' after stop"
       on_fail: continue

5. transitions
   for TOKEN in TOKENS:
       call: http.get_json(URL="{GATEWAY_URL}/bots/status", TOKEN={TOKEN})
       expect: transitions end with active -> stopping -> completed
       emit FINDING "transitions: {transitions}"
       on_fail: continue

6. summary
   => FINALIZATION_OK = (STOPPED == len(TOKENS))
   if FINALIZATION_OK: emit PASS "all bots completed"
   else: emit FAIL "{len(TOKENS) - STOPPED} bots did not complete"
```
