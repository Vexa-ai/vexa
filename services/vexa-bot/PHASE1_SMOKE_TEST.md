# Phase 1 Smoke Test: Vexa Bot Speaker Detection & WebSocket Communication

## Overview
This smoke test validates that the Vexa Bot can detect speaker changes in Google Meet and transmit speaker events via WebSocket to WhisperLive.

## Prerequisites
- Docker environment with Vexa services running
- Google Meet meeting with multiple participants
- Browser developer tools access

## Test Procedure

### Step 1: Start Vexa Bot in a Meeting
1. Start the Vexa bot using the API:
```bash
curl -X POST "http://localhost:18056/meetings" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij",
    "bot_name": "Vexa Test Bot"
  }'
```

### Step 2: Monitor WebSocket Traffic
**Option A: Browser Developer Tools**
1. Once the bot joins the meeting, open Chrome/Edge Developer Tools
2. Go to Network tab → Filter by "WS" (WebSocket)
3. Find the WhisperLive WebSocket connection
4. Click on it to view messages

**Option B: Service Logs**
```bash
# Monitor WhisperLive logs for incoming messages
docker logs -f vexa_dev-whisperlive-1

# Monitor Vexa Bot logs for speaker detection
docker logs -f VEXA_BOT_CONTAINER_ID
```

### Step 3: Generate Speaker Activity
1. Have participants in the meeting speak one at a time
2. Have multiple participants speak simultaneously 
3. Have participants leave the meeting while speaking
4. End the meeting (bot should leave automatically)

## Expected Results

### 1. Speaker Detection Logs (in Browser Console)
You should see logs like:
```
👁️ Observing: John Doe (ID: 12345), Initial state: silent
🎤 SPEAKER_START: John Doe (ID: 12345)
Speaker event sent: SPEAKER_START for John Doe (12345) at 2023-12-07T10:30:15.123Z
🔇 SPEAKER_END: John Doe (ID: 12345)
Speaker event sent: SPEAKER_END for John Doe (12345) at 2023-12-07T10:30:20.456Z
```

### 2. WebSocket Messages (in Network Tab)
**Initial Configuration:**
```json
{
  "uid": "550e8400-e29b-41d4-a716-446655440000",
  "language": null,
  "task": "transcribe",
  "model": "medium",
  "use_vad": true,
  "platform": "google_meet",
  "token": "your-api-token",
  "meeting_id": "abc-defg-hij",
  "meeting_url": "https://meet.google.com/abc-defg-hij"
}
```

**Speaker Activity Messages:**
```json
{
  "type": "speaker_activity",
  "payload": {
    "event_type": "SPEAKER_START",
    "participant_name": "John Doe",
    "participant_id_meet": "12345",
    "client_timestamp_ms": 1701942615123,
    "uid": "550e8400-e29b-41d4-a716-446655440000",
    "token": "your-api-token",
    "platform": "google_meet",
    "meeting_id": "abc-defg-hij",
    "meeting_url": "https://meet.google.com/abc-defg-hij"
  }
}
```

**Session Control Message (when leaving):**
```json
{
  "type": "session_control",
  "payload": {
    "event": "LEAVING_MEETING",
    "uid": "550e8400-e29b-41d4-a716-446655440000",
    "client_timestamp_ms": 1701942715456,
    "token": "your-api-token",
    "platform": "google_meet",
    "meeting_id": "abc-defg-hij"
  }
}
```

### 3. WhisperLive Service Logs
You should see:
```
INFO - Received raw message from client: {"type":"speaker_activity","payload":{...}}
INFO - Received raw message from client: {"type":"session_control","payload":{...}}
```

## Validation Criteria

✅ **PASS Criteria:**
- [ ] Bot joins Google Meet successfully 
- [ ] Speaker detection logs appear in browser console
- [ ] `SPEAKER_START` events are sent when participants begin speaking
- [ ] `SPEAKER_END` events are sent when participants stop speaking
- [ ] Participant names are correctly identified (not just IDs)
- [ ] WebSocket messages have correct JSON structure
- [ ] `session_control` message with `LEAVING_MEETING` is sent before bot leaves
- [ ] Multiple simultaneous speakers are detected
- [ ] Participants removed while speaking trigger synthetic `SPEAKER_END`

❌ **FAIL Criteria:**
- [ ] No speaker detection logs appear
- [ ] WebSocket messages are malformed JSON
- [ ] Speaker events are not transmitted via WebSocket
- [ ] Bot crashes or fails to join meeting
- [ ] Participant names show as "Participant (vexa-id-...)" for all users

## Troubleshooting

### Issue: No Speaker Detection Logs
**Check:** Ensure the participant selector `.IisKdb` is still valid for current Google Meet UI
**Action:** Update selectors in `google.ts` if Google Meet UI has changed

### Issue: WebSocket Messages Not Sent
**Check:** Verify WebSocket connection is established before speaker events
**Action:** Look for "WebSocket connection opened" log before speaker activity

### Issue: Malformed Participant Names
**Check:** The name selector classes may have changed
**Action:** Update `nameSelectors` array in the speaker detection code

### Issue: WhisperLive Not Receiving Messages
**Check:** WhisperLive service logs for connection errors
**Action:** Verify REDIS_STREAM_URL environment variable is set correctly

## Phase 1 Success Criteria
- [x] Speaker detection logic integrated into Vexa Bot
- [x] WebSocket communication for speaker events implemented  
- [x] LEAVING_MEETING signal implemented
- [x] JSON message format follows plan specification
- [x] Smoke test documentation provided

## Next Phase
Once Phase 1 smoke test passes, proceed to **Phase 2**: WhisperLive Server-Side Event Reception & Redis Forwarding. 