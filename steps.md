# Vexa Bot Intelligent Meeting End Detection - Implementation Steps

## Overview

This document tracks the implementation of intelligent meeting end detection for the Vexa bot. The system uses speech activity patterns to determine when meetings have ended, replacing the simple "alone for 10 seconds" logic.

## Implementation Progress

- ✅ **Step 1**: Add Speech Activity Tracking Variables
- ✅ **Step 2**: Create Speech Activity Detection Function
- ✅ **Step 3**: Integrate Speech Detection with Transcription Handler
- ⏳ **Step 4**: Create Intelligent Meeting End Detection Function
- ⏳ **Step 5**: Replace Current Timeout Logic
- ⏳ **Step 6**: Add Participant Join Detection
- ⏳ **Step 7**: Add Debug Logging for Speech Activity

---

## Step 1: Add Speech Activity Tracking Variables ✅

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: After line 421 (after `let sessionAudioStartTimeMs: number | null = null;`)
- **Context**: Inside the `page.evaluate()` function, within the recording setup logic

### Why This Location?

- **Scope Access**: Variables need to be accessible throughout the entire meeting session
- **Initialization Timing**: Should be initialized when recording starts, not when WebSocket connects
- **Persistence**: Need to persist across WebSocket reconnections and participant changes
- **Existing Pattern**: Follows the same pattern as other session-level variables

### Code Added

```typescript
// Speech activity tracking variables for intelligent meeting end detection
let meetingHasHadSpeech = false; // Tracks if anyone has spoken during the meeting
let lastSpeechTime: number | null = null; // Timestamp of last speech activity
let participantSpeechHistory = new Map<string, boolean>(); // participantId -> has spoken during meeting
let silenceCountdown = 0; // Countdown timer in seconds (0 = not active)
let meetingJoinTime = Date.now(); // When bot joined the meeting
let isInSilenceCountdown = false; // Flag to prevent multiple countdown starts
```

### Variable Purposes

| Variable                   | Type                 | Purpose                                     | Updated When                    |
| -------------------------- | -------------------- | ------------------------------------------- | ------------------------------- |
| `meetingHasHadSpeech`      | boolean              | Prevents timeout logic until someone speaks | First transcription with text   |
| `lastSpeechTime`           | number \| null       | Tracks last speech for 2-minute threshold   | Any transcription with text     |
| `participantSpeechHistory` | Map<string, boolean> | Tracks which participants have spoken       | Transcription with speaker info |
| `silenceCountdown`         | number               | 3-minute grace period countdown             | Decremented every 5 seconds     |
| `meetingJoinTime`          | number               | Reference for dead meeting detection        | Set once at join                |
| `isInSilenceCountdown`     | boolean              | Prevents multiple countdown starts          | Countdown start/stop/reset      |

---

## Step 2: Create Speech Activity Detection Function ✅

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: After line 539 (after WebSocket message handler), before `socket.onerror`
- **Context**: Inside `setupWebSocket()` function, after message handler but before error handler

### Why This Location?

- **Scope Access**: Needs access to Step 1 variables
- **Reusability**: Can be called from multiple places
- **Organization**: Keeps helper functions separate from event handlers
- **Execution Context**: Inside `page.evaluate()` where variables are accessible

### Code Added

```typescript
// Helper function to process speech activity from transcription data
const processSpeechActivity = (transcriptionData: any) => {
  // Handle both single segment and array of segments
  const segments = Array.isArray(transcriptionData)
    ? transcriptionData
    : transcriptionData.segments || [transcriptionData];

  segments.forEach((segment: any) => {
    if (segment.text && segment.text.trim().length > 0) {
      // Mark that meeting has had speech
      if (!meetingHasHadSpeech) {
        meetingHasHadSpeech = true;
        (window as any).logBot(
          "🎤 First speech detected in meeting - speech tracking now active"
        );
      }

      // Update last speech time
      lastSpeechTime = Date.now();

      // Track speaker history (if speaker info available)
      if (segment.speaker) {
        const speakerId = segment.speaker.toString();
        if (!participantSpeechHistory.has(speakerId)) {
          participantSpeechHistory.set(speakerId, true);
          (window as any).logBot(`📝 New speaker detected: ${speakerId}`);
        }
      }

      // Reset silence countdown if we were in one
      if (isInSilenceCountdown) {
        isInSilenceCountdown = false;
        silenceCountdown = 0;
        (window as any).logBot(
          "🔄 Speech detected - resetting silence countdown"
        );
      }
    }
  });
};
```

### Key Features

- **Data Normalization**: Handles single segments, arrays, and nested segments
- **Speech Detection**: Only processes segments with actual text content
- **Speaker Tracking**: Records which participants have spoken
- **Countdown Reset**: Cancels countdown when speech is detected
- **Debug Logging**: Clear emoji-based logging for easy identification

---

## Step 3: Integrate Speech Detection with Transcription Handler ✅

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: Lines 530-537 (inside the `else` block of WebSocket message handler)
- **Context**: Inside `socket.onmessage` event handler, final `else` block processing transcription data

### Code Change

**Before:**

```typescript
} else {
// --- ADDED: Collect transcription segments for SRT ---
botCallbacks?.onTranscriptionSegmentsReceived(
data["segments"] || data
);

(window as any).logBot(
`Transcription: ${JSON.stringify(data)}`
);
}
```

**After:**

```typescript
} else {
// --- ADDED: Collect transcription segments for SRT ---
const transcriptionData = data["segments"] || data;
botCallbacks?.onTranscriptionSegmentsReceived(transcriptionData);

// Process speech activity for intelligent meeting end detection
processSpeechActivity(transcriptionData);

(window as any).logBot(
`Transcription: ${JSON.stringify(data)}`
);
}
```

### Key Changes

1. **Data Extraction**: Extract transcription data once, use multiple times
2. **SRT Integration**: Unchanged functionality, uses extracted data
3. **Speech Detection**: NEW - processes same data for speech tracking
4. **Logging**: Unchanged - still logs full WebSocket message

### Benefits

- **Real-Time Processing**: Speech activity detected immediately
- **Data Consistency**: Both SRT and speech detection use identical data
- **Performance**: Minimal overhead (one additional function call)
- **Maintainability**: Clear separation of concerns

---

## Step 4: Create Intelligent Meeting End Detection Function ⏳

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: After the `processSpeechActivity` function (before `socket.onerror`)
- **Context**: Inside `setupWebSocket()` function, after helper functions but before event handlers

### Purpose

Implement the core logic for determining when to leave the meeting

### Code to Add

```typescript
// Intelligent meeting end detection logic
const shouldLeaveMeeting = (
  participantCount: number
): { shouldLeave: boolean; reason: string } => {
  const now = Date.now();
  const timeSinceJoin = now - meetingJoinTime;

  // Case 1: Dead meeting detection (never had speech)
  if (!meetingHasHadSpeech) {
    const deadMeetingTimeoutMs = 5 * 60 * 1000; // 5 minutes
    if (timeSinceJoin > deadMeetingTimeoutMs) {
      return {
        shouldLeave: true,
        reason: "Dead meeting - no speech for 5 minutes after joining",
      };
    }
    return { shouldLeave: false, reason: "Waiting for first speech activity" };
  }

  // Case 2: Meeting had speech, now checking for end conditions
  if (!lastSpeechTime) {
    return { shouldLeave: false, reason: "No speech timing data available" };
  }

  const timeSinceLastSpeech = now - lastSpeechTime;
  const twoMinutesMs = 2 * 60 * 1000;

  // Case 3: Recent speech activity - meeting is active
  if (timeSinceLastSpeech < twoMinutesMs) {
    return {
      shouldLeave: false,
      reason: `Recent speech activity (${Math.round(
        timeSinceLastSpeech / 1000
      )}s ago)`,
    };
  }

  // Case 4: No speech for 2+ minutes, check remaining participants
  const remainingParticipantIds = Array.from(activeParticipants.keys());
  const silentParticipants = remainingParticipantIds.filter(
    (id) => !participantSpeechHistory.has(id)
  );

  // If all remaining participants have never spoken, start/continue countdown
  if (
    silentParticipants.length === remainingParticipantIds.length &&
    remainingParticipantIds.length > 0
  ) {
    if (!isInSilenceCountdown) {
      // Start countdown
      isInSilenceCountdown = true;
      silenceCountdown = 180; // 3 minutes in seconds
      (window as any).logBot(
        `🕐 Starting 3-minute countdown - all ${remainingParticipantIds.length} remaining participants are silent`
      );
    }

    // Continue countdown
    if (silenceCountdown > 0) {
      return {
        shouldLeave: false,
        reason: `Silence countdown: ${Math.round(silenceCountdown)}s remaining`,
      };
    } else {
      return {
        shouldLeave: true,
        reason: "Silence countdown completed - meeting appears ended",
      };
    }
  }

  // Case 5: Some remaining participants have spoken before - keep waiting
  return {
    shouldLeave: false,
    reason: `Some participants (${
      remainingParticipantIds.length - silentParticipants.length
    }/${remainingParticipantIds.length}) have spoken before`,
  };
};
```

---

## Step 5: Replace Current Timeout Logic ⏳

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: Replace lines 1320-1350 (the current `aloneTime` logic)
- **Context**: Inside the participant monitoring interval

### Purpose

Replace simple participant count logic with intelligent speech-based logic while preserving existing "alone in meeting" behavior

### Code to Replace

**Replace the entire block:**

```typescript
// FIXED: Correct logic for tracking alone time
if (count <= 1) {
  // Bot is 1, so count <= 1 means bot is alone
  aloneTime += 5; // It's a 5-second interval
} else {
  // Someone else is here, so reset the timer.
  if (aloneTime > 0) {
    (window as any).logBot(
      "Another participant joined. Resetting alone timer."
    );
  }
  aloneTime = 0;
}

if (aloneTime >= 10) {
  // If bot has been alone for 10 seconds...
  (window as any).logBot(
    "Meeting ended or bot has been alone for 10 seconds. Stopping recorder..."
  );
  clearInterval(checkInterval);
  recorder.disconnect();
  (window as any).triggerNodeGracefulLeave();
  resolve();
} else if (aloneTime > 0) {
  // Log countdown if timer has started
  (window as any).logBot(
    `Bot has been alone for ${aloneTime} seconds. Will leave in ${
      10 - aloneTime
    } more seconds.`
  );
}
```

**With this code:**

```typescript
// Intelligent meeting end detection
const meetingEndDecision = shouldLeaveMeeting(count);

// CRITICAL: Handle "alone in meeting" case first (preserve existing behavior)
if (count <= 1) {
  // Bot is alone - use original logic for immediate response
  aloneTime += 5; // Keep existing aloneTime tracking

  if (aloneTime >= 10) {
    (window as any).logBot(
      "Meeting ended or bot has been alone for 10 seconds. Stopping recorder..."
    );
    clearInterval(checkInterval);
    recorder.disconnect();
    (window as any).triggerNodeGracefulLeave();
    resolve();
    return;
  }

  // Log countdown if timer has started
  if (aloneTime > 0) {
    (window as any).logBot(
      `Bot has been alone for ${aloneTime} seconds. Will leave in ${
        10 - aloneTime
      } more seconds.`
    );
  }
  return; // Skip new speech-based logic when alone
}

// NEW: Apply speech-based logic only when other participants are present
const meetingEndDecision = shouldLeaveMeeting(count);

// Update silence countdown if active
if (isInSilenceCountdown && silenceCountdown > 0) {
  silenceCountdown -= 5; // Subtract 5 seconds (interval duration)
}

// Handle new participants joining during countdown
if (count > activeParticipants.size) {
  (window as any).logBot(
    "New participant detected - updating participant tracking"
  );
  // The activeParticipants map will be updated by the observer logic
  // Reset countdown if we were in one (new participant might speak)
  if (isInSilenceCountdown) {
    isInSilenceCountdown = false;
    silenceCountdown = 0;
    (window as any).logBot(
      "🔄 New participant joined - resetting silence countdown"
    );
  }
}

// Log current status
(window as any).logBot(
  `Meeting Status: ${meetingEndDecision.reason} | Participants: ${count} | HasHadSpeech: ${meetingHasHadSpeech}`
);

// Decide whether to leave
if (meetingEndDecision.shouldLeave) {
  (window as any).logBot(`🚪 Leaving meeting: ${meetingEndDecision.reason}`);
  clearInterval(checkInterval);
  recorder.disconnect();
  (window as any).triggerNodeGracefulLeave();
  resolve();
}
```

---

## Step 6: Add Participant Join Detection ⏳

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: Find the participant observer logic (around line 800-900) where new participants are detected
- **Context**: Where participants are added to `activeParticipants`

### Purpose

Reset countdown when new participants join

### Implementation Notes

This step requires finding the exact location where participants are added to `activeParticipants`. We'll need to add a callback there to reset the countdown.

**Look for this pattern:**

```typescript
// NEW: Add participant to our central map
activeParticipants.set(participantId, {
  name: getParticipantName(participantElement),
  element: participantElement,
});
```

**Add countdown reset logic after this:**

```typescript
// Reset countdown if we were in one (new participant might speak)
if (isInSilenceCountdown) {
  isInSilenceCountdown = false;
  silenceCountdown = 0;
  (window as any).logBot(
    "🔄 New participant joined - resetting silence countdown"
  );
}
```

---

## Step 7: Add Debug Logging for Speech Activity ⏳

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: In the `checkInterval` function, after the meeting status log
- **Context**: Inside the participant monitoring interval

### Purpose

Provide detailed debugging information

### Code to Add

```typescript
// Debug logging for speech activity (only when in countdown or no speech yet)
if (!meetingHasHadSpeech || isInSilenceCountdown) {
  const timeSinceJoin = Date.now() - meetingJoinTime;
  const lastSpeechAgo = lastSpeechTime ? Date.now() - lastSpeechTime : null;
  const speakersCount = participantSpeechHistory.size;

  (window as any).logBot(
    `🔍 Speech Debug: JoinTime: ${Math.round(
      timeSinceJoin / 1000
    )}s | LastSpeech: ${
      lastSpeechAgo ? Math.round(lastSpeechAgo / 1000) + "s ago" : "never"
    } | Speakers: ${speakersCount} | Countdown: ${
      isInSilenceCountdown ? silenceCountdown + "s" : "inactive"
    }`
  );
}
```

### Debug Information Provided

- **JoinTime**: How long since bot joined the meeting
- **LastSpeech**: How long since last speech (or "never")
- **Speakers**: Number of unique speakers detected
- **Countdown**: Current countdown status and remaining time

---

## Fixes

### Fix 1: Optimize participantSpeechHistory Data Structure

**Issue**: Using `Map<string, boolean>` where all values are always `true` is inefficient and unnecessary.

**Solution**: Change to `Set<string>` for better performance and cleaner code.

**Code Changes**:

**Step 1 - Variable Declaration:**

```typescript
// OLD:
let participantSpeechHistory = new Map<string, boolean>(); // participantId -> has spoken during meeting

// NEW:
let participantSpeechHistory = new Set<string>(); // Names of people who have spoken
```

**Step 2 - processSpeechActivity Function:**

```typescript
// OLD:
if (segment.speaker) {
  const speakerId = segment.speaker.toString();
  if (!participantSpeechHistory.has(speakerId)) {
    participantSpeechHistory.set(speakerId, true);
    (window as any).logBot(`📝 New speaker detected: ${speakerId}`);
  }
}

// NEW:
if (segment.speaker) {
  const speakerName = segment.speaker.toString();
  if (!participantSpeechHistory.has(speakerName)) {
    participantSpeechHistory.add(speakerName);
    (window as any).logBot(`📝 New speaker detected: ${speakerName}`);
  }
}
```

**Step 4 - shouldLeaveMeeting Function:**

```typescript
// OLD:
const silentParticipants = remainingParticipantIds.filter(
  (id) => !participantSpeechHistory.has(id)
);

// NEW:
const silentParticipants = remainingParticipantIds.filter(
  (id) => !participantSpeechHistory.has(id)
);
```

**Benefits**:

- **Performance**: `Set.has()` is O(1) vs `Map.has()` O(1) but cleaner
- **Memory**: Slightly more efficient (no boolean values stored)
- **Semantics**: "Set of speakers" is more intuitive than "Map with all true values"
- **Code**: Cleaner, no need to check existence before adding

### Fix 2: Rename participantSpeechHistory to spokenSpeakers

**Issue**: Variable name `participantSpeechHistory` is confusing and doesn't clearly indicate it contains speaker names.

**Solution**: Rename to `spokenSpeakers` for better clarity and consistency.

**Code Changes**:

**Step 1 - Variable Declaration:**

```typescript
// OLD:
let participantSpeechHistory = new Set<string>(); // Names of people who have spoken

// NEW:
let spokenSpeakers = new Set<string>(); // Names of people who have spoken
```

**Step 2 - processSpeechActivity Function:**

```typescript
// OLD:
if (segment.speaker) {
  const speakerName = segment.speaker.toString();
  if (!participantSpeechHistory.has(speakerName)) {
    participantSpeechHistory.add(speakerName);
    (window as any).logBot(`📝 New speaker detected: ${speakerName}`);
  }
}

// NEW:
if (segment.speaker) {
  const speakerName = segment.speaker.toString();
  if (!spokenSpeakers.has(speakerName)) {
    spokenSpeakers.add(speakerName);
    (window as any).logBot(`📝 New speaker detected: ${speakerName}`);
  }
}
```

**Step 4 - shouldLeaveMeeting Function:**

```typescript
// OLD:
const silentParticipants = remainingParticipantIds.filter(
  (id) => !participantSpeechHistory.has(id)
);

// NEW:
const silentParticipants = remainingParticipantIds.filter(
  (id) => !spokenSpeakers.has(id)
);
```

**Benefits**:

- **Clarity**: `spokenSpeakers` clearly indicates it contains names of people who spoke
- **Consistency**: Matches the semantic meaning of the data
- **Readability**: Easier to understand in code logic

### Fix 3: Fix Silent Participants Logic and Use Names Instead of IDs

**Issue**: The current logic compares participant IDs with speaker names, which will never match. Also, the condition logic is incorrect.

**Solution**: Use participant names instead of IDs and fix the comparison logic.

**Code Changes**:

**Step 4 - shouldLeaveMeeting Function:**

```typescript
// OLD (BROKEN):
const remainingParticipantIds = Array.from(activeParticipants.keys());
const silentParticipants = remainingParticipantIds.filter(
  (id) => !spokenSpeakers.has(id)
);

// If all remaining participants have never spoken, start/continue countdown
if (
  silentParticipants.length === remainingParticipantIds.length &&
  remainingParticipantIds.length > 0
) {
  // ... countdown logic
}

// NEW (FIXED):
const remainingParticipants = new Set(
  Array.from(activeParticipants.values()).map((p) => p.name)
);
const silentParticipants = new Set(
  Array.from(remainingParticipants).filter((name) => !spokenSpeakers.has(name))
);

// If all remaining participants have never spoken, start/continue countdown
if (
  silentParticipants.size === remainingParticipants.size &&
  [...silentParticipants].every((name) => remainingParticipants.has(name)) &&
  remainingParticipants.size > 0
) {
  // ... countdown logic
}
```

**What This Fixes**:

- **ID vs Name Mismatch**: Now compares names with names instead of IDs with names
- **Correct Logic**: `silentParticipants` now contains actual participant names who haven't spoken
- **Proper Comparison**: Checks if all remaining participants are in the silent list

**Example**:

```typescript
// activeParticipants contains:
// { "participant-123" => {name: "Ilayda Ciftci", element: ...}, "user-456" => {name: "Youssef BEZZARGA", element: ...} }

// spokenSpeakers contains:
// Set(["Ilayda Ciftci"])

// remainingParticipants = ["Ilayda Ciftci", "Youssef BEZZARGA"]
// silentParticipants = ["Youssef BEZZARGA"] (only Youssef hasn't spoken)
// silentParticipants.length (1) !== remainingParticipants.length (2) → Don't start countdown
```

### Fix 4: Handle Empty Text with Duration-Based Speech Detection

**Issue**: When live transcription is deactivated, `segment.text` will always be empty, but we still need to detect speech activity.

**Solution**: Use segment duration (`end - start`) instead of text content to detect meaningful speech.

**Code Changes**:

**Step 2 - processSpeechActivity Function:**

```typescript
// OLD:
segments.forEach((segment: any) => {
  if (segment.text && segment.text.trim().length > 0) {
    // Mark that meeting has had speech
    if (!meetingHasHadSpeech) {
      meetingHasHadSpeech = true;
      (window as any).logBot(
        "🎤 First speech detected in meeting - speech tracking now active"
      );
    }

    // Update last speech time
    lastSpeechTime = Date.now();

    // Track speaker history (if speaker info available)
    if (segment.speaker) {
      const speakerName = segment.speaker.toString();
      if (!participantSpeechHistory.has(speakerName)) {
        participantSpeechHistory.add(speakerName);
        (window as any).logBot(`📝 New speaker detected: ${speakerName}`);
      }
    }

    // Reset silence countdown if we were in one
    if (isInSilenceCountdown) {
      isInSilenceCountdown = false;
      silenceCountdown = 0;
      (window as any).logBot(
        "🔄 Speech detected - resetting silence countdown"
      );
    }
  }
});

// NEW:
segments.forEach((segment: any) => {
  // Calculate segment duration in seconds
  const segmentDuration = segment.end - segment.start;
  const minimumDuration = 2.0; // 2 seconds minimum for meaningful speech

  // Check if segment has meaningful duration (instead of text content)
  if (segmentDuration >= minimumDuration) {
    // Mark that meeting has had speech
    if (!meetingHasHadSpeech) {
      meetingHasHadSpeech = true;
      (window as any).logBot(
        "🎤 First speech detected in meeting - speech tracking now active"
      );
    }

    // Update last speech time
    lastSpeechTime = Date.now();

    // Track speaker history (if speaker info available)
    if (segment.speaker) {
      const speakerName = segment.speaker.toString();
      if (!participantSpeechHistory.has(speakerName)) {
        participantSpeechHistory.add(speakerName);
        (window as any).logBot(
          `📝 New speaker detected: ${speakerName} (${segmentDuration.toFixed(
            1
          )}s)`
        );
      }
    }

    // Reset silence countdown if we were in one
    if (isInSilenceCountdown) {
      isInSilenceCountdown = false;
      silenceCountdown = 0;
      (window as any).logBot(
        "🔄 Speech detected - resetting silence countdown"
      );
    }
  } else if (segmentDuration > 0) {
    // Log short segments for debugging (optional)
    (window as any).logBot(
      `🔇 Short segment ignored: ${segmentDuration.toFixed(1)}s (speaker: ${
        segment.speaker || "unknown"
      })`
    );
  }
});
```

**Benefits**:

- **Works without text**: Detects speech based on duration instead of text content
- **Filters noise**: Ignores very short segments (< 2 seconds) that might be background noise
- **Maintains functionality**: All existing speech tracking logic remains the same
- **Better logging**: Shows segment duration in speaker detection logs
- **Debugging**: Optional logging of short segments for troubleshooting

**Example Segment Processing**:

```typescript
// This segment will be processed (2.0s duration):
{
  "start": 10.0,
  "end": 12.0,
  "text": "", // Empty but duration is 2 seconds
  "speaker": "Ilayda Ciftci"
}

// This segment will be ignored (0.5s duration):
{
  "start": 15.0,
  "end": 15.5,
  "text": "",
  "speaker": "John Smith"
}
```

---

## Known Issues to Address

1. **Speaker ID Mismatch**: `segment.speaker` contains participant names (e.g., "Ilayda Ciftci") but `activeParticipants.keys()` contains participant IDs - need to map these correctly
2. **Multiple Bots Scenario**: When 2 bots + 1 human remain, logic might incorrectly start countdown
3. **Name Formatting**: Participant names might be formatted differently between transcription and UI

---

## Technical Notes

### Data Flow

```
Transcription Data → processSpeechActivity() → Update Variables → shouldLeaveMeeting() → Decision
```

### Key Variables

- `activeParticipants`: Map of current participants (from Google Meet UI)
- `participantSpeechHistory`: Map of who has spoken (from transcription data)
- `meetingHasHadSpeech`: Boolean flag for first speech detection
- `lastSpeechTime`: Timestamp of most recent speech
- `silenceCountdown`: 3-minute countdown timer

### Decision Logic

The system uses a 5-case decision tree:

1. **Dead meeting** (no speech for 5 minutes)
2. **Data validation** (missing timing data)
3. **Recent activity** (speech within 2 minutes)
4. **Silent participants** (all remaining never spoke)
5. **Mixed participants** (some have spoken before)

This approach provides intelligent meeting end detection while preventing premature exits during active conversations.

---

# UPDATED STEPS WITH FIXES APPLIED

## Overview

This section contains the corrected implementation steps with all fixes applied. The original steps above contain the fixes as separate sections, but this provides the clean, corrected steps ready for implementation.

## Implementation Progress

- ✅ **Step 1**: Add Speech Activity Tracking Variables (with fixes)
- ✅ **Step 2**: Create Speech Activity Detection Function (with fixes)
- ✅ **Step 3**: Integrate Speech Detection with Transcription Handler
- ⏳ **Step 4**: Create Intelligent Meeting End Detection Function (with fixes)
- ⏳ **Step 5**: Replace Current Timeout Logic
- ⏳ **Step 6**: Add Participant Join Detection
- ⏳ **Step 7**: Add Debug Logging for Speech Activity

---

## Step 1: Add Speech Activity Tracking Variables ✅

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: After line 421 (after `let sessionAudioStartTimeMs: number | null = null;`)
- **Context**: Inside the `page.evaluate()` function, within the recording setup logic

### Code to Add

```typescript
// Speech activity tracking variables for intelligent meeting end detection
let meetingHasHadSpeech = false; // Tracks if anyone has spoken during the meeting
let lastSpeechTime: number | null = null; // Timestamp of last speech activity
let spokenSpeakers = new Set<string>(); // Names of people who have spoken
let silenceCountdown = 0; // Countdown timer in seconds (0 = not active)
let meetingJoinTime = Date.now(); // When bot joined the meeting
let isInSilenceCountdown = false; // Flag to prevent multiple countdown starts
```

---

## Step 2: Create Speech Activity Detection Function ✅

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: After line 539 (after WebSocket message handler), before `socket.onerror`
- **Context**: Inside `setupWebSocket()` function, after message handler but before error handler

### Code to Add

```typescript
// Helper function to process speech activity from transcription data
const processSpeechActivity = (transcriptionData: any) => {
  // Handle both single segment and array of segments
  const segments = Array.isArray(transcriptionData)
    ? transcriptionData
    : transcriptionData.segments || [transcriptionData];

  segments.forEach((segment: any) => {
    // Calculate segment duration in seconds
    const segmentDuration = segment.end - segment.start;
    const minimumDuration = 2.0; // 2 seconds minimum for meaningful speech

    // Check if segment has meaningful duration (instead of text content)
    if (segmentDuration >= minimumDuration) {
      // Mark that meeting has had speech
      if (!meetingHasHadSpeech) {
        meetingHasHadSpeech = true;
        (window as any).logBot(
          "🎤 First speech detected in meeting - speech tracking now active"
        );
      }

      // Update last speech time
      lastSpeechTime = Date.now();

      // Track speaker history (if speaker info available)
      if (segment.speaker) {
        const speakerName = segment.speaker.toString();
        if (!spokenSpeakers.has(speakerName)) {
          spokenSpeakers.add(speakerName);
          (window as any).logBot(
            `📝 New speaker detected: ${speakerName} (${segmentDuration.toFixed(
              1
            )}s)`
          );
        }
      }

      // Reset silence countdown if we were in one
      if (isInSilenceCountdown) {
        isInSilenceCountdown = false;
        silenceCountdown = 0;
        (window as any).logBot(
          "🔄 Speech detected - resetting silence countdown"
        );
      }
    } else if (segmentDuration > 0) {
      // Log short segments for debugging (optional)
      (window as any).logBot(
        `🔇 Short segment ignored: ${segmentDuration.toFixed(1)}s (speaker: ${
          segment.speaker || "unknown"
        })`
      );
    }
  });
};
```

---

## Step 3: Integrate Speech Detection with Transcription Handler ✅

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: Lines 530-537 (inside the `else` block of WebSocket message handler)
- **Context**: Inside `socket.onmessage` event handler, final `else` block processing transcription data

### Code Change

**Replace this:**

```typescript
} else {
// --- ADDED: Collect transcription segments for SRT ---
botCallbacks?.onTranscriptionSegmentsReceived(
data["segments"] || data
);

(window as any).logBot(
`Transcription: ${JSON.stringify(data)}`
);
}
```

**With this:**

```typescript
} else {
// --- ADDED: Collect transcription segments for SRT ---
const transcriptionData = data["segments"] || data;
botCallbacks?.onTranscriptionSegmentsReceived(transcriptionData);

// Process speech activity for intelligent meeting end detection
processSpeechActivity(transcriptionData);

(window as any).logBot(
`Transcription: ${JSON.stringify(data)}`
);
}
```

---

## Step 4: Create Intelligent Meeting End Detection Function ⏳

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: After `processSpeechActivity` function, before `socket.onerror`
- **Context**: Inside `setupWebSocket()` function, after helper functions but before event handlers

### Code to Add

```typescript
// Intelligent meeting end detection logic
const shouldLeaveMeeting = (
  participantCount: number
): { shouldLeave: boolean; reason: string } => {
  const now = Date.now();
  const timeSinceJoin = now - meetingJoinTime;

  // Case 1: Dead meeting detection (never had speech)
  if (!meetingHasHadSpeech) {
    const deadMeetingTimeoutMs = 5 * 60 * 1000; // 5 minutes
    if (timeSinceJoin > deadMeetingTimeoutMs) {
      return {
        shouldLeave: true,
        reason: "Dead meeting - no speech for 5 minutes after joining",
      };
    }
    return { shouldLeave: false, reason: "Waiting for first speech activity" };
  }

  // Case 2: Meeting had speech, now checking for end conditions
  if (!lastSpeechTime) {
    return { shouldLeave: false, reason: "No speech timing data available" };
  }

  const timeSinceLastSpeech = now - lastSpeechTime;
  const twoMinutesMs = 2 * 60 * 1000;

  // Case 3: Recent speech activity - meeting is active
  if (timeSinceLastSpeech < twoMinutesMs) {
    return {
      shouldLeave: false,
      reason: `Recent speech activity (${Math.round(
        timeSinceLastSpeech / 1000
      )}s ago)`,
    };
  }

  // Case 4: No speech for 2+ minutes, check remaining participants
  const remainingParticipants = new Set(
    Array.from(activeParticipants.values()).map((p) => p.name)
  );
  const silentParticipants = new Set(
    Array.from(remainingParticipants).filter(
      (name) => !spokenSpeakers.has(name)
    )
  );

  function setsEqual<T>(a: Set<T>, b: Set<T>): boolean {
    if (a.size !== b.size) return false;
    for (const x of a) if (!b.has(x)) return false;
    return true;
  }

  // If all remaining participants have never spoken, start/continue countdown
  if (
    setsEqual(silentParticipants, remainingParticipants) &&
    remainingParticipants.size > 0
  ) {
    if (!isInSilenceCountdown) {
      // Start countdown
      isInSilenceCountdown = true;
      silenceCountdown = 180; // 3 minutes in seconds
      (window as any).logBot(
        `🕐 Starting 3-minute countdown - all ${remainingParticipants.size} remaining participants are silent`
      );
    }

    // Continue countdown
    if (silenceCountdown > 0) {
      return {
        shouldLeave: false,
        reason: `Silence countdown: ${Math.round(silenceCountdown)}s remaining`,
      };
    } else {
      return {
        shouldLeave: true,
        reason: "Silence countdown completed - meeting appears ended",
      };
    }
  }

  // Case 5: Some remaining participants have spoken before - keep waiting
  return {
    shouldLeave: false,
    reason: `Some participants (${
      remainingParticipants.size - silentParticipants.size
    }/${remainingParticipants.size}) have spoken before`,
  };
};
```

---

## Step 5: Replace Current Timeout Logic ⏳

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: Replace lines 1320-1350 (the current `aloneTime` logic)
- **Context**: Inside the participant monitoring interval

### Code to Replace

**Replace the entire `aloneTime` logic block with:**

```typescript
// Intelligent meeting end detection
const meetingEndDecision = shouldLeaveMeeting(count);

// CRITICAL: Handle "alone in meeting" case first (preserve existing behavior)
if (count <= 1) {
  // Bot is alone - use original logic for immediate response
  aloneTime += 5; // Keep existing aloneTime tracking

  if (aloneTime >= 10) {
    (window as any).logBot(
      "Meeting ended or bot has been alone for 10 seconds. Stopping recorder..."
    );
    clearInterval(checkInterval);
    recorder.disconnect();
    (window as any).triggerNodeGracefulLeave();
    resolve();
    return;
  }

  // Log countdown if timer has started
  if (aloneTime > 0) {
    (window as any).logBot(
      `Bot has been alone for ${aloneTime} seconds. Will leave in ${
        10 - aloneTime
      } more seconds.`
    );
  }
  return; // Skip new speech-based logic when alone
}

// NEW: Apply speech-based logic only when other participants are present
const meetingEndDecision = shouldLeaveMeeting(count);

// Update silence countdown if active
if (isInSilenceCountdown && silenceCountdown > 0) {
  silenceCountdown -= 5; // Subtract 5 seconds (interval duration)
}

// Handle new participants joining during countdown
if (count > activeParticipants.size) {
  (window as any).logBot(
    "New participant detected - updating participant tracking"
  );
  // The activeParticipants map will be updated by the observer logic
  // Reset countdown if we were in one (new participant might speak)
  if (isInSilenceCountdown) {
    isInSilenceCountdown = false;
    silenceCountdown = 0;
    (window as any).logBot(
      "🔄 New participant joined - resetting silence countdown"
    );
  }
}

// Log current status
(window as any).logBot(
  `Meeting Status: ${meetingEndDecision.reason} | Participants: ${count} | HasHadSpeech: ${meetingHasHadSpeech}`
);

// Decide whether to leave
if (meetingEndDecision.shouldLeave) {
  (window as any).logBot(`🚪 Leaving meeting: ${meetingEndDecision.reason}`);
  clearInterval(checkInterval);
  recorder.disconnect();
  (window as any).triggerNodeGracefulLeave();
  resolve();
}
```

---

## Step 6: Add Participant Join Detection ⏳

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: Find the participant observer logic (around line 800-900) where new participants are detected
- **Context**: Where participants are added to `activeParticipants`

### Code to Add

**Find this pattern:**

```typescript
// NEW: Add participant to our central map
activeParticipants.set(participantId, {
  name: getParticipantName(participantElement),
  element: participantElement,
});
```

**Add countdown reset logic after this:**

```typescript
// Reset countdown if we were in one (new participant might speak)
if (isInSilenceCountdown) {
  isInSilenceCountdown = false;
  silenceCountdown = 0;
  (window as any).logBot(
    "🔄 New participant joined - resetting silence countdown"
  );
}
```

---

## Step 7: Add Debug Logging for Speech Activity ⏳

### Location Details

- **File**: `services/vexa-bot/core/src/platforms/google.ts`
- **Position**: In the `checkInterval` function, after the meeting status log
- **Context**: Inside the participant monitoring interval

### Code to Add

```typescript
// Debug logging for speech activity (only when in countdown or no speech yet)
if (!meetingHasHadSpeech || isInSilenceCountdown) {
  const timeSinceJoin = Date.now() - meetingJoinTime;
  const lastSpeechAgo = lastSpeechTime ? Date.now() - lastSpeechTime : null;
  const speakersCount = spokenSpeakers.size;

  (window as any).logBot(
    `🔍 Speech Debug: JoinTime: ${Math.round(
      timeSinceJoin / 1000
    )}s | LastSpeech: ${
      lastSpeechAgo ? Math.round(lastSpeechAgo / 1000) + "s ago" : "never"
    } | Speakers: ${speakersCount} | Countdown: ${
      isInSilenceCountdown ? silenceCountdown + "s" : "inactive"
    }`
  );
}
```

---

## Key Fixes Applied

1. **Set instead of Map**: `spokenSpeakers` is now a `Set<string>` instead of `Map<string, boolean>`
2. **Duration-based detection**: Uses segment duration instead of text content for speech detection
3. **Name comparison**: Compares participant names with speaker names instead of IDs
4. **Set comparison**: Uses proper set equality check for silent participants
5. **Better variable names**: `spokenSpeakers` instead of `participantSpeechHistory`

This implementation provides intelligent meeting end detection while preserving existing "alone in meeting" behavior.
