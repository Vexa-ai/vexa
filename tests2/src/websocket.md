---
needs: [GATEWAY_URL, API_TOKEN]
gives: [WEBSOCKET_OK]
---

use: lib/http

# WebSocket

> **Why:** The dashboard shows live transcripts via WebSocket. If WS is broken, the dashboard looks dead even when REST works.
> **What:** Validate WS auth (accept with key, reject without), ping/pong, subscribe/unsubscribe, error handling (invalid JSON, unknown action).
> **How:** Connect via node ws module, send structured messages, assert responses, verify connection survives bad input.

## state

    TESTED = 0
    PASSED = 0

## steps

```
1. auth_ping
   TESTED += 1
   call: http.check_ws(URL="ws://localhost:8056/ws?api_key={API_TOKEN}", SEND='{"action":"ping"}', EXPECT_CONTAINS="pong")
   PASSED += 1; emit PASS "ws: auth + ping/pong"
   on_fail: stop

2. no_auth
   TESTED += 1
   do: |
       node -e "
       const ws=new(require('ws'))('ws://localhost:8056/ws');
       ws.on('message',d=>{console.log(d.toString());ws.close();process.exit(0)});
       ws.on('close',c=>{console.log('CLOSE:'+c);process.exit(0)});
       ws.on('error',e=>{console.log('ERROR:'+e.message);process.exit(0)});
       setTimeout(()=>process.exit(1),5000);
       "
   expect: stdout contains "missing_api_key" or "CLOSE:4401"
   PASSED += 1; emit PASS "ws: rejects unauth"
   on_fail: continue

3. subscribe
   TESTED += 1
   do: |
       node -e "
       const ws=new(require('ws'))('ws://localhost:8056/ws?api_key={API_TOKEN}');
       ws.on('open',()=>ws.send(JSON.stringify({action:'subscribe',meetings:[{platform:'{MEETING_PLATFORM}',native_id:'{NATIVE_MEETING_ID}'}]})));
       ws.on('message',d=>{const m=JSON.parse(d.toString());if(m.type==='subscribed'){console.log('SUBSCRIBED');ws.close();process.exit(0)}});
       setTimeout(()=>process.exit(1),5000);
       "
   expect: SUBSCRIBED
   PASSED += 1; emit PASS "ws: subscribe"
   on_fail: continue

4. invalid_json
   TESTED += 1
   do: |
       node -e "
       const ws=new(require('ws'))('ws://localhost:8056/ws?api_key={API_TOKEN}');
       ws.on('open',()=>ws.send('not json'));
       ws.on('message',d=>{const m=JSON.parse(d.toString());
       if(m.type==='error'){ws.send(JSON.stringify({action:'ping'}))}
       if(m.type==='pong'){console.log('SURVIVED');ws.close();process.exit(0)}});
       setTimeout(()=>process.exit(1),5000);
       "
   expect: SURVIVED
   PASSED += 1; emit PASS "ws: survives bad input"
   on_fail: continue

5. unknown_action
   TESTED += 1
   do: |
       node -e "
       const ws=new(require('ws'))('ws://localhost:8056/ws?api_key={API_TOKEN}');
       ws.on('open',()=>ws.send(JSON.stringify({action:'nonexistent'})));
       ws.on('message',d=>{const m=JSON.parse(d.toString());
       if(m.type==='error'&&m.error==='unknown_action'){console.log('HANDLED');ws.close();process.exit(0)}});
       setTimeout(()=>process.exit(1),5000);
       "
   expect: HANDLED
   PASSED += 1; emit PASS "ws: unknown action"
   on_fail: continue

6. summary
   => WEBSOCKET_OK = (PASSED == TESTED)
   emit FINDING "ws: {PASSED}/{TESTED}"
```
