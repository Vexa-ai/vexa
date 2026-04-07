---
needs: [GATEWAY_URL, API_TOKEN]
gives: [WEBSOCKET_OK]
---

use: lib/http

# WebSocket

> **Why:** The dashboard shows live transcripts via WebSocket. If WS is broken, the dashboard looks dead even when REST works.
> **What:** Two tiers. Protocol: auth, ping, error handling (no meeting needed). Content: subscribe to live meeting, verify transcript segments arrive with text (needs active meeting).
> **How:** Tier 1 via python3 websockets. Tier 2 subscribes to an active meeting and checks that `type=transcript` messages contain non-empty text.

## state

    TESTED = 0
    PASSED = 0

## steps

```
═══════════════════════════════════════════════════════════════
 TIER 1 — Protocol (no meeting needed)
═══════════════════════════════════════════════════════════════

1. auth_ping
   TESTED += 1
   call: http.check_ws(URL="ws://localhost:8056/ws?api_key={API_TOKEN}", SEND='{"action":"ping"}', EXPECT_CONTAINS="pong")
   PASSED += 1; emit PASS "ws: auth + ping/pong"
   on_fail: stop

2. no_auth
   TESTED += 1
   do: |
       python3 -c "
       import asyncio, websockets, json
       async def test():
           async with websockets.connect('ws://localhost:8056/ws') as ws:
               msg=json.loads(await ws.recv())
               print(msg.get('error',''))
       asyncio.run(test())
       "
   expect: stdout contains "missing_api_key"
   PASSED += 1; emit PASS "ws: rejects unauth"
   on_fail: continue

3. invalid_json
   TESTED += 1
   do: |
       python3 -c "
       import asyncio, websockets, json
       async def test():
           async with websockets.connect('ws://localhost:8056/ws?api_key={API_TOKEN}') as ws:
               await ws.send('not json')
               err=json.loads(await ws.recv())
               assert err['error']=='invalid_json'
               await ws.send(json.dumps({'action':'ping'}))
               pong=json.loads(await ws.recv())
               assert pong['type']=='pong'
               print('SURVIVED')
       asyncio.run(test())
       "
   expect: SURVIVED
   PASSED += 1; emit PASS "ws: survives bad input"
   on_fail: continue

4. unknown_action
   TESTED += 1
   do: |
       python3 -c "
       import asyncio, websockets, json
       async def test():
           async with websockets.connect('ws://localhost:8056/ws?api_key={API_TOKEN}') as ws:
               await ws.send(json.dumps({'action':'nonexistent'}))
               err=json.loads(await ws.recv())
               assert err['error']=='unknown_action'
               print('HANDLED')
       asyncio.run(test())
       "
   expect: HANDLED
   PASSED += 1; emit PASS "ws: unknown action"
   on_fail: continue

═══════════════════════════════════════════════════════════════
 TIER 2 — Content delivery (needs active meeting with speech)
 needs: MEETING_PLATFORM, NATIVE_MEETING_ID (from transcription proc)
═══════════════════════════════════════════════════════════════

5. subscribe_live
   TESTED += 1
   > Subscribe to the active meeting and wait for transcript messages.
   do: |
       python3 -c "
       import asyncio, websockets, json
       async def test():
           uri='ws://localhost:8056/ws?api_key={API_TOKEN}'
           async with websockets.connect(uri) as ws:
               await ws.send(json.dumps({'action':'subscribe','meetings':[{'platform':'{MEETING_PLATFORM}','native_id':'{NATIVE_MEETING_ID}'}]}))
               # First message should be subscription confirmation
               sub=json.loads(await asyncio.wait_for(ws.recv(),timeout=5))
               assert sub['type']=='subscribed', f'expected subscribed, got {sub}'
               print('SUBSCRIBED')
               # Wait for transcript messages (up to 30s)
               transcript_count=0
               empty_count=0
               for _ in range(20):
                   try:
                       msg=json.loads(await asyncio.wait_for(ws.recv(),timeout=5))
                       if msg.get('type')=='transcript' or 'text' in msg or 'segment' in str(msg.get('type','')):
                           text=msg.get('text','')
                           if text and len(text.strip())>0:
                               transcript_count+=1
                               print(f'SEGMENT:{text[:60]}')
                           else:
                               empty_count+=1
                               print(f'EMPTY_SEGMENT:keys={list(msg.keys())}')
                   except asyncio.TimeoutError:
                       break
               print(f'TRANSCRIPT_COUNT={transcript_count}')
               print(f'EMPTY_COUNT={empty_count}')
       asyncio.run(test())
       "
   if output contains "TRANSCRIPT_COUNT=0" and "EMPTY_COUNT" > 0:
       emit FAIL "BUG: WS delivers transcript messages but text field is empty"
       emit FINDING "Debug: check Redis channel tc:meeting:{MEETING_ID}:mutable — is text populated at publish time?"
   if output contains "TRANSCRIPT_COUNT=" and count > 0:
       PASSED += 1
       emit PASS "ws: live transcript segments with text"
   on_fail: continue

6. debug_redis_channel [optional]
   > If tier 2 fails, check the raw Redis messages to isolate the bug.
   > Is the publisher sending empty text, or is the relay stripping it?
   do: |
       python3 -c "
       import redis, json, time
       r=redis.Redis(host='localhost', port=6379, decode_responses=True)
       # Find the meeting_id for native_meeting_id from meeting-api DB
       # Subscribe to tc:meeting:*:mutable channels
       ps=r.pubsub()
       ps.psubscribe('tc:meeting:*:mutable')
       print('Listening on tc:meeting:*:mutable for 15s...')
       deadline=time.time()+15
       while time.time()<deadline:
           msg=ps.get_message(timeout=1)
           if msg and msg['type']=='pmessage':
               data=msg['data']
               try:
                   parsed=json.loads(data)
                   text=parsed.get('text','')
                   segment_type=parsed.get('type','')
                   print(f'REDIS: type={segment_type} text_len={len(text)} text={text[:60]}')
               except:
                   print(f'REDIS_RAW: {str(data)[:100]}')
       ps.close()
       "
   > If REDIS shows text_len=0: publisher bug (segment-publisher in vexa-bot)
   > If REDIS shows text_len>0: relay bug (api-gateway WS handler)
   on_fail: continue

7. debug_status_channel [optional]
   > Check if meeting status updates are published to Redis for the dashboard.
   do: |
       python3 -c "
       import redis, json, time
       r=redis.Redis(host='localhost', port=6379, decode_responses=True)
       ps=r.pubsub()
       ps.psubscribe('bm:meeting:*:status')
       print('Listening on bm:meeting:*:status for 15s...')
       deadline=time.time()+15
       while time.time()<deadline:
           msg=ps.get_message(timeout=1)
           if msg and msg['type']=='pmessage':
               print(f'STATUS: channel={msg[\"channel\"]} data={msg[\"data\"][:100]}')
       ps.close()
       "
   > If no messages: status publisher not wired to Redis
   > If messages arrive: dashboard not subscribing to bm:meeting:*:status
   on_fail: continue

8. summary
   => WEBSOCKET_OK = (PASSED == TESTED)
   emit FINDING "ws: {PASSED}/{TESTED}"
```

## Failure modes

| Symptom | Cause | Fix | Learned |
|---|---|---|---|
| WS protocol passes but live transcript text is empty | Segment publisher writes partial JSON to Redis (text not populated at publish time) OR gateway relay strips fields | Step 6 isolates: check Redis raw message. text_len=0 → publisher bug. text_len>0 → relay bug. | Protocol tests ≠ content tests. Always test both. |
| Dashboard shows stale bot status | Dashboard doesn't subscribe to `bm:meeting:{id}:status` Redis channel, or status publisher doesn't fire on state transitions | Step 7 checks Redis channel. No messages → publisher missing. Messages arrive → dashboard not subscribing. | Status polling must be tested separately from transcript delivery |
| Subscribe succeeds but no messages arrive | Wrong Redis channel name (meeting_id vs native_meeting_id mismatch) | Check that meeting_id used in channel matches the one from authorize-subscribe | Channel naming is a common source of silent failures |
