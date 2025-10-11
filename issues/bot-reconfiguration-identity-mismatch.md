---
title: Bot Reconfiguration Identity Mismatch (Fix: Meeting-ID-Based Addressing)
type: Architecture Decision
status: Approved
priority: High
components: [bot-manager, vexa-bot, WhisperLive]
created: 2025-10-11
related: [meeting-token-and-meeting-id-auth.md]
owners: [dgrankin]
version: 1.0
---

# Bot Reconfiguration Identity Mismatch Bug

## Executive Summary

The bot reconfiguration system has an identity mismatch between the control plane (Redis commands) and data plane (WhisperLive sessions). Bot-manager addresses bots using ephemeral `connection_id` values, but the WebSocket layer generates fresh UUIDs on every reconnection, causing confusion and requiring unnecessary Redis mapping storage.

**Proposed Fix**: Use stable `meeting.id` (database primary key) as the addressing primitive for bot commands instead of ephemeral session UUIDs.

---

## Current Architecture (Problematic)

### How It Works Now

#### 1. Bot Launch
- Bot-manager generates `connection_id = uuid.uuid4()` 
- Stores mapping in Redis: `bm:meeting:{platform}:{native_meeting_id}:current_uid` → `connection_id`
- Bot subscribes to: `bot_commands:{connection_id}`
- Passes `connection_id` to bot via `BOT_CONFIG` environment variable

#### 2. Reconfiguration Request
```
Client → PUT /bots/{platform}/{native_meeting_id}/config
         Body: { "language": "en", "task": "transcribe" }

Bot-manager:
  1. Looks up meeting record by (user_id, platform, native_meeting_id)
  2. Resolves connection_id from:
     - Redis: bm:meeting:{platform}:{native_meeting_id}:current_uid
     - Fallback: Latest MeetingSession.session_uid from database
  3. Publishes to: bot_commands:{connection_id}
```

#### 3. Bot Receives Command
```typescript
// Node.js (index.ts)
handleRedisMessage(message, channel, page) {
  const command = JSON.parse(message);
  currentLanguage = command.language;
  currentTask = command.task;
  
  // Trigger browser-side reconfiguration
  page.evaluate(() => {
    window.triggerWebSocketReconfigure(lang, task);
  });
}
```

#### 4. WebSocket Reconnection
```typescript
// Browser (recording.ts)
window.triggerWebSocketReconfigure = async (lang, task) => {
  cfg.language = lang;
  cfg.task = task;
  
  // Close to force reconnect
  whisperLiveService.socket.close(1000, 'Reconfiguration requested');
};

// BrowserWhisperLiveService (browser.ts)
socket.onopen = (event) => {
  this.currentUid = generateBrowserUUID();  // ← NEW UUID GENERATED
  
  const configPayload = {
    uid: this.currentUid,                    // ← Fresh UID sent
    language: this.botConfigData.language,
    task: this.botConfigData.task,
    // ...
  };
  this.socket.send(JSON.stringify(configPayload));
};
```

---

## The Bug

### Identity Mismatch

```
Bot-manager level:    connectionId = "abc-123-def" (stable)
                            ↓
Redis channel:        bot_commands:abc-123-def
                            ↓
Bot Node.js:          connectionId = "abc-123-def" (stable)
                            ↓
Browser WebSocket:    currentUid = "xyz-789-ghi" (regenerates on each reconnection!)
                            ↓
WhisperLive server:   Sees NEW session every time
```

### Issues

1. ❌ **Session Confusion**: WhisperLive thinks it's a new bot session on every reconnection
2. ❌ **Unnecessary Storage**: Redis mapping `bm:meeting:*:current_uid` adds complexity
3. ❌ **Fragile Design**: Relies on ephemeral connection IDs that must be looked up
4. ❌ **Semantic Mismatch**: "Control plane ID" vs "data plane UID" serve different purposes
5. ❌ **Lookup Overhead**: Bot-manager must query Redis/database to find where to send commands

---

## Root Cause Analysis

The fundamental issue is using **session-oriented addressing** for what should be **meeting-oriented commands**:

- A **meeting** is the stable business entity (one bot per meeting)
- A **session** is an ephemeral connection (can reconnect multiple times)
- Commands operate at the meeting level ("reconfigure this meeting's bot")
- But addressing operates at the session level ("send command to session xyz")

This architectural mismatch creates:
- Extra indirection (meeting → session lookup)
- Storage overhead (maintaining the mapping)
- Semantic confusion (what happens if session restarts?)

---

## Proposed Fix: Meeting-ID-Based Addressing

### Why `meeting.id` Is The Right Identifier

The database `Meeting` table structure:
```sql
Meeting:
  - id (PRIMARY KEY)              -- Auto-incrementing integer
  - user_id (FOREIGN KEY)
  - platform (VARCHAR)
  - platform_specific_id (VARCHAR) -- Native meeting ID from provider
  - UNIQUE (user_id, platform, platform_specific_id)
```

**Key insight**: `meeting.id` uniquely identifies one bot instance and is:
- ✅ Stable (doesn't change for meeting lifetime)
- ✅ Already available (bot receives it in config)
- ✅ Simple (single integer, not composite key)
- ✅ Globally unique (primary key)
- ✅ Natural addressing primitive (commands target meetings, not sessions)

### New Architecture

**Channel Format:**
```
bot_commands:meeting:{meeting_id}
```

**Example:**
```
bot_commands:meeting:12345
```

**Flow:**
```
Client Request → Bot-manager has meeting.id → Publish directly (no lookup!)
                                                      ↓
                                            bot_commands:meeting:12345
                                                      ↓
                                            Bot receives (validates meeting_id)
                                                      ↓
                                            Bot carries MeetingToken (HS256) in data plane; collector verifies
                                                      ↓
                                            WebSocket reconnects (UID irrelevant)
```

---

## Implementation Changes

### 1. Bot Subscription (`vexa-bot/core/src/index.ts`)

**Current:**
```typescript
const commandChannel = `bot_commands:${currentConnectionId}`;
await redisSubscriber.subscribe(commandChannel, handleRedisMessage);
```

**New:**
```typescript
const meetingId = botConfig.meeting_id;
if (!meetingId) {
  log("ERROR: meeting_id not provided in botConfig. Cannot subscribe to commands.");
  // Handle error - maybe exit?
}
const commandChannel = `bot_commands:meeting:${meetingId}`;
await redisSubscriber.subscribe(commandChannel, handleRedisMessage);
log(`Subscribed to meeting-specific channel: ${commandChannel}`);
```

### 1.1 Fail-Fast Requirements (Mandatory)

- Bot MUST exit on startup if `meeting_id` is missing in `BOT_CONFIG`.
- Bot MUST ignore any command where `command.meeting_id !== botConfig.meeting_id`.
- Bot-manager MUST publish ONLY to `bot_commands:meeting:{meeting.id}` and include `meeting_id` in every payload.
- WhisperLive MUST reject the initial WS config if any of `uid`, `platform`, `meeting_url`, `token`, or `meeting_id` is missing; respond with ERROR and close the socket.
- No session-UID addressing anywhere; no dual-publish/subscribe. Meeting channel is the only control-plane channel.

### 2. Bot-Manager Publishing (`bot-manager/app/main.py:659-681`)

**Current:**
```python
# Resolve current session_uid (connectionId) for this meeting
original_session_uid: Optional[str] = None
if redis_client:
    mapping_key = f"bm:meeting:{platform.value}:{native_meeting_id}:current_uid"
    cached_uid = await redis_client.get(mapping_key)
    if isinstance(cached_uid, str) and cached_uid:
        original_session_uid = cached_uid

if not original_session_uid:
    latest_session_stmt = select(MeetingSession.session_uid).where(...)
    original_session_uid = session_result.scalars().first()

# Construct and Publish command
command_payload = {
    "action": "reconfigure",
    "uid": original_session_uid,
    "language": req.language,
    "task": req.task
}
channel = f"bot_commands:{original_session_uid}"
await redis_client.publish(channel, payload_str)
```

**New:**
```python
# Direct publishing - no Redis lookup needed!
internal_meeting_id = active_meeting.id

channel = f"bot_commands:meeting:{internal_meeting_id}"
command_payload = {
    "action": "reconfigure",
    "meeting_id": internal_meeting_id,  # For validation
    "language": req.language,
    "task": req.task
}

try:
    payload_str = json.dumps(command_payload)
    logger.info(f"Publishing command to channel '{channel}': {payload_str}")
    await redis_client.publish(channel, payload_str)
    logger.info(f"Successfully published reconfigure command for meeting {internal_meeting_id}.")
except Exception as e:
    logger.error(f"Failed to publish reconfigure command to Redis channel {channel}: {e}", exc_info=True)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to send reconfiguration command to the bot."
    )
```

### 3. Bot Message Validation (`vexa-bot/core/src/index.ts`)

**Add validation:**
```typescript
const handleRedisMessage = async (message: string, channel: string, page: Page | null) => {
  log(`[DEBUG] handleRedisMessage entered for channel ${channel}.`);
  log(`Received command on ${channel}: ${message}`);
  
  try {
    const command = JSON.parse(message);
    
    // Validate this command is for us
    if (command.meeting_id && command.meeting_id !== botConfig.meeting_id) {
      log(`⚠️ Ignoring command for different meeting: ${command.meeting_id} (ours: ${botConfig.meeting_id})`);
      return;
    }
    
    if (command.action === 'reconfigure') {
      log(`Processing reconfigure command: Lang=${command.language}, Task=${command.task}`);
      currentLanguage = command.language;
      currentTask = command.task;
      // ... rest of implementation
    } else if (command.action === 'leave') {
      stopSignalReceived = true;
      log("Received leave command");
      if (!isShuttingDown && page && !page.isClosed()) {
        await performGracefulLeave(page, 0, "self_initiated_leave");
      }
    }
  } catch (e: any) {
    log(`Error processing Redis message: ${e.message}`);
  }
};
```

### 4. Remove Redis Mapping Storage (`bot-manager/app/main.py:511-518`)

**Delete this code block:**
```python
# Persist (platform, native_meeting_id) -> current connectionId mapping in Redis for command routing
try:
    if redis_client and connection_id:
        mapping_key = f"bm:meeting:{req.platform.value}:{native_meeting_id}:current_uid"
        await redis_client.set(mapping_key, connection_id, ex=24*60*60)
        logger.info(f"[DEBUG] Stored current_uid mapping in Redis: {mapping_key} -> {connection_id}")
except Exception as e:
    logger.warning(f"[DEBUG] Failed to store current_uid mapping in Redis: {e}")
```

### 5. Stop Command (`bot-manager/app/main.py:684-828`)

**Current:**
```python
# Find the earliest session UID for this meeting
session_stmt = select(MeetingSession.session_uid).where(
    MeetingSession.meeting_id == meeting.id
).order_by(MeetingSession.session_start_time.asc())

session_result = await db.execute(session_stmt)
earliest_session_uid = session_result.scalars().first()

if not earliest_session_uid:
    logger.warning(f"Stop request: No session UID for meeting {meeting.id}")

# Publish 'leave' command via Redis Pub/Sub
command_channel = f"bot_commands:{earliest_session_uid}"
payload = json.dumps({"action": "leave"})
await redis_client.publish(command_channel, payload)
```

**New:**
```python
# Direct meeting-based channel (no session lookup needed!)
command_channel = f"bot_commands:meeting:{meeting.id}"
payload = json.dumps({
    "action": "leave",
    "meeting_id": meeting.id
})

try:
    logger.info(f"Publishing leave command to Redis channel '{command_channel}': {payload}")
    await redis_client.publish(command_channel, payload)
    logger.info(f"Successfully published leave command for meeting {meeting.id}.")
except Exception as e:
    logger.error(f"Failed to publish leave command to Redis channel {command_channel}: {e}", exc_info=True)
    # Log error but continue with delayed stop
```

### 6. Ensure `meeting_id` in BotConfig (updated for MeetingToken)

```python
bot_config_data = {
    "meeting_id": meeting_id,
    "platform": platform,
    "meetingUrl": meeting_url,
    "botName": bot_name,
    "meeting_token": "<HS256 JWT>",
    "nativeMeetingId": native_meeting_id,
    "language": language,
    "task": task,
    "redisUrl": REDIS_URL,
    "container_name": container_name,
    "automaticLeave": {
        "waitingRoomTimeout": 300000,
        "noOneJoinedTimeout": 120000,
        "everyoneLeftTimeout": 60000
    },
    "botManagerCallbackUrl": "http://bot-manager:8080/bots/internal/callback/exited"
}
```

---

## Benefits of This Fix

### ✅ Architectural Clarity
- **Meeting** = Control Identity (stable, business entity)
- **WebSocket UID** = Data Session (ephemeral, implementation detail)
- Clear separation of concerns between control and data planes

### ✅ Simplified Operations
```
Before: API → DB lookup → Redis lookup → Publish
After:  API → DB lookup → Publish (direct)
```

### ✅ No Redis Mapping Storage
- Removes `bm:meeting:{platform}:{native_meeting_id}:current_uid` keys
- One less failure point
- Cleaner Redis namespace
- Reduced memory usage

### ✅ Reconnection-Friendly
- WebSocket can reconnect with different UIDs freely
- Control plane (Redis commands) unaffected by data plane reconnections
- Stubborn mode works naturally without coordination

### ✅ Consistent with Domain Model
- Meeting is the business entity
- Natural addressing: "send command to meeting 12345"
- Not: "send command to session xyz-789-ghi" (what if it reconnected?)

### ✅ Simpler Bot Logic
- Bot knows its `meeting_id` at launch (already in config)
- No need to track or resolve session UIDs
- Single subscription for entire bot lifetime
- No confusion about which UID to use

### ✅ Better Error Messages
```
Before: "Could not find session xyz-789-ghi"
After:  "Could not find meeting 12345"
```

### ✅ Debugging Improvements
- `meeting_id` is visible in logs, database, and Redis
- Easy to correlate across all components
- No need to chase UUID mappings

---

## What Doesn't Change

✅ The WS session UID is client-provided and may change on reconnection (ephemeral, data-plane only). The server does not auto-generate UIDs when missing; missing UID causes an immediate connection error.  
✅ Core reconfiguration logic remains the same (close → stubborn reconnect → new config)  
✅ Meeting uniqueness constraint still enforced at database level

---

## Migration Path

### Phase 1: Verify Prerequisites ✅
- [x] `meeting_id` already in `BotConfig` type definition
- [x] `meeting_id` already passed to bot containers
- [x] Bot already has access to `botConfig.meeting_id`

### Phase 2: Update Bot Subscription
- [ ] Modify `vexa-bot/core/src/index.ts` to subscribe to `bot_commands:meeting:{meeting_id}`
- [ ] Add validation in `handleRedisMessage` to check `command.meeting_id`
- [ ] Add error handling if `meeting_id` not provided in config

### Phase 3: Update Bot-Manager Commands
- [ ] Modify reconfigure endpoint to publish to `bot_commands:meeting:{meeting.id}`
- [ ] Modify stop endpoint to publish to `bot_commands:meeting:{meeting.id}`
- [ ] Remove session UID lookup logic
- [ ] Update command payloads to include `meeting_id` for validation

### Phase 4: Clean Up Redis Mapping
- [ ] Remove `current_uid` mapping write in bot launch
- [ ] Remove `current_uid` mapping read in reconfigure endpoint
- [ ] Delete any lingering `bm:meeting:*:current_uid` keys (manual cleanup)

### Phase 5: Testing
- [ ] Test reconfiguration with meeting-based channels
- [ ] Test stop command with meeting-based channels
- [ ] Test WebSocket reconnection scenarios
- [ ] Verify no regression in stubborn mode behavior
- [ ] Load test to ensure no performance impact

### Phase 6: Documentation
- [ ] Update API documentation
- [ ] Update architecture diagrams
- [ ] Update deployment notes
- [ ] Document the addressing scheme change

**Compatibility**: No backward compatibility. Session-based channels and Redis mappings are removed; meeting-only addressing is mandatory.

---

## Alternative Approaches Considered

### Alternative 1: Composite Key `{user_id}:{platform}:{native_meeting_id}`
**Channel:** `bot_commands:meeting:{user_id}:{platform}:{native_meeting_id}`

**Pros:**
- Human-readable
- No DB lookup needed to publish
- Matches natural business key

**Cons:**
- Longer channel name (more Redis memory)
- Bot needs to know its `user_id` (currently only has token)
- More complex validation logic
- Not how system is architected (meeting.id is the primary key)

**Verdict:** Rejected in favor of simpler `meeting.id` approach

### Alternative 2: Keep Session-Based, Fix UID Reuse
**Approach:** Make browser reuse the same UID on reconnection

**Cons:**
- More complex browser state management
- WhisperLive server needs to handle UID reuse (stale session cleanup)
- Doesn't solve the fundamental addressing problem
- Still requires Redis mapping storage

**Verdict:** Rejected - doesn't address root cause

### Alternative 3: Platform-Specific Channels
**Channel:** `bot_commands:{platform}:{native_meeting_id}`

**Cons:**
- Bot-manager doesn't know user_id when publishing (needs it for uniqueness)
- Could have collisions if same native_meeting_id across users
- Less aligned with database schema

**Verdict:** Rejected - uniqueness issues

---

## Risk Assessment

### Low Risk ✅
- Changes are isolated to bot-manager and vexa-bot
- No database schema changes required
- No changes to WhisperLive or transcription-collector
- Can be tested thoroughly in staging
- Rollback is straightforward (revert code)

### Testing Requirements
1. **Unit Tests**: Message validation, channel name generation
2. **Integration Tests**: End-to-end reconfigure flow
3. **Reconnection Tests**: Verify WebSocket UID changes don't break commands
4. **Load Tests**: Ensure Redis publish performance unchanged
5. **Failure Tests**: Bot restart, Redis reconnection, network issues

---

## Success Criteria

- [x] Bot subscribes using `meeting_id` instead of `connectionId`
- [x] Bot-manager publishes commands without Redis/DB lookup
- [x] Reconfiguration works end-to-end
- [x] Stop/leave commands work end-to-end
- [x] WebSocket reconnection doesn't affect command delivery
- [x] No `bm:meeting:*:current_uid` keys in Redis
- [x] All tests pass
- [x] No performance degradation

---

## Related Issues

- Bot reconnection handling (complementary)
- Webhook delivery reliability (separate concern)
- Session tracking improvements (future optimization)

---

## References

**Code Locations:**
- Bot subscription: `vexa/services/vexa-bot/core/src/index.ts:342-367`
- Bot message handler: `vexa/services/vexa-bot/core/src/index.ts:131-203`
- Bot-manager reconfigure: `vexa/services/bot-manager/app/main.py:572-682`
- Bot-manager stop: `vexa/services/bot-manager/app/main.py:684-828`
- WebSocket reconnection: `vexa/services/vexa-bot/core/src/utils/browser.ts:316-345`
- BotConfig type: `vexa/services/vexa-bot/core/src/types.ts:1-20`

**Related Documentation:**
- vexa/services/vexa-bot/README.md - Bot architecture overview
- vexa/services/bot-manager/README.md - Bot-manager API documentation

