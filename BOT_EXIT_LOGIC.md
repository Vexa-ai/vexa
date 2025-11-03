# Bot Exit Logic Documentation

## Overview

The bot uses an intelligent exit detection system that monitors speech activity and participant behavior to determine when to leave a meeting. The logic runs every 5 seconds and evaluates multiple conditions to decide whether the meeting is still active or should be ended.

## Configuration Variables

All exit logic thresholds can be configured via `.env` file. If not specified, default values are used.

| Variable                                | Default      | Description                                                                                      |
| --------------------------------------- | ------------ | ------------------------------------------------------------------------------------------------ |
| `SPEECH_ACTIVATION_THRESHOLD_SECONDS`   | 5            | Minimum accumulated speaking duration (in seconds) required to mark meeting as having had speech |
| `DEAD_MEETING_TIMEOUT_SECONDS`          | 300 (5 min)  | Timeout for dead meeting detection (no speech detected after bot joins)                          |
| `ABSOLUTE_SILENCE_TIMEOUT_SECONDS`      | 600 (10 min) | If no speech for this long, bot leaves regardless of participants                                |
| `RECENT_SPEECH_THRESHOLD_SECONDS`       | 120 (2 min)  | If speech occurred within this time, meeting is considered active                                |
| `SILENT_PARTICIPANTS_COUNTDOWN_SECONDS` | 180 (3 min)  | Case 4 countdown duration when all remaining participants are silent                             |

## Core Variables

### Speech Tracking Variables

- **`meetingHasHadSpeech`** (boolean): Tracks if anyone has spoken during the meeting. Set to `true` when any speaker accumulates `SPEECH_ACTIVATION_THRESHOLD_SECONDS` total duration.

- **`lastSpeechTime`** (number | null): Timestamp (milliseconds) of the most recent `SPEAKER_END` event. Updated on every speech completion.

- **`spokenSpeakers`** (Set<string>): Participant IDs who have spoken at least once during the meeting. Used to identify silent participants.

- **`speakerDurationCollector`** (Map<string, number>): Accumulates total speaking duration per participant ID (in seconds). Calculated from `SPEAKER_START`/`SPEAKER_END` events.

- **`speakerIdToNameMap`** (Map<string, string>): Persistent mapping of participant ID → name. Never cleared, so names are preserved even after participants leave.

### Countdown Variables

- **`silenceCountdown`** (number): Case 4 countdown timer in seconds (0 = not active). Decrements by 5 every interval.

- **`isInSilenceCountdown`** (boolean): Flag to prevent multiple countdown starts. Set to `true` when countdown begins.

- **`meetingJoinTime`** (number): Timestamp when bot joined the meeting. Used for dead meeting detection.

## Exit Cases (Evaluated Every 5 Seconds)

### Priority Order

The exit logic evaluates cases in the following priority order (checked first to last):

---

### Case 0: Alone in Meeting (Highest Priority)

**Trigger:** Participant count ≤ 1 (only the bot remains)

**Behavior:**

- Starts a 10-second countdown immediately
- Every 5 seconds, increments `aloneTime` counter
- After 10 seconds alone → **Bot leaves immediately**

**Why:** Fast response when bot is truly alone, regardless of other logic.

**Example:**

```
Time: 00:00 - 7 participants
Time: 00:05 - 6 participants
Time: 00:10 - 1 participant (bot alone) → Start 10s countdown
Time: 00:15 - Still alone → Leave meeting
```

---

### Case 1: Participant List Container Missing

**Trigger:** Participant count is 0 AND the participant list DOM element is missing

**Behavior:**

- **Bot leaves immediately**

**Why:** If the UI disappears, assume the meeting has ended.

---

### Case 2: Dead Meeting Detection

**Trigger:** `meetingHasHadSpeech = false` AND `timeSinceJoin > DEAD_MEETING_TIMEOUT_SECONDS`

**Condition:**

- No speech activity detected since bot joined
- Default: After 5 minutes (300 seconds)

**Behavior:**

- **Bot leaves**

**Why:** Avoid staying in meetings where no one speaks.

**Configuration:** `DEAD_MEETING_TIMEOUT_SECONDS` (default: 300 seconds)

**Example:**

```
00:00 - Bot joins, meetingHasHadSpeech = false
00:01 - Still false (no speech)
00:02 - Still false
...
05:00 - Still false, 5 minutes passed → Leave meeting
```

---

### Case 3: Recent Speech Activity Check

**Trigger:** Speech occurred within `RECENT_SPEECH_THRESHOLD_SECONDS`

**Condition:**

- `timeSinceLastSpeech < RECENT_SPEECH_THRESHOLD_SECONDS`
- Default: Speech within last 2 minutes (120 seconds)

**Behavior:**

- **Bot stays** - meeting is considered active

**Why:** Don't leave during active conversation periods.

**Configuration:** `RECENT_SPEECH_THRESHOLD_SECONDS` (default: 120 seconds)

**Example:**

```
16:30:00 - Last speech detected (lastSpeechTime updated)
16:30:05 - 5 seconds since last speech → Stay (within 2 min window)
16:31:00 - 60 seconds since last speech → Stay (within 2 min window)
16:32:01 - 121 seconds since last speech → Proceed to Case 3.5 or 4
```

---

### Case 3.5: Absolute Silence Timeout

**Trigger:** `timeSinceLastSpeech >= ABSOLUTE_SILENCE_TIMEOUT_SECONDS`

**Condition:**

- Meeting previously had speech (`meetingHasHadSpeech = true`)
- No speech for 10+ minutes (default: 600 seconds)

**Behavior:**

- **Bot leaves immediately** - regardless of participant status

**Why:** Even if participants are present, if no one has spoken for 10 minutes, the meeting has effectively ended.

**Configuration:** `ABSOLUTE_SILENCE_TIMEOUT_SECONDS` (default: 600 seconds)

**Example:**

```
16:20:00 - Last speech detected
16:22:00 - 2 minutes silence → Continue to Case 4
16:25:00 - 5 minutes silence → Continue to Case 4
16:30:01 - 10+ minutes silence → Leave immediately (absolute timeout)
```

---

### Case 4: Silent Participants Analysis

**Trigger:** No speech for 2+ minutes AND checking remaining participants

**Conditions:**

1. `timeSinceLastSpeech >= RECENT_SPEECH_THRESHOLD_SECONDS` (2+ minutes)
2. `timeSinceLastSpeech < ABSOLUTE_SILENCE_TIMEOUT_SECONDS` (less than 10 minutes)
3. All remaining participants are silent (have never spoken)

**Behavior:**

- If all remaining participants have never spoken → Start 3-minute countdown
- Countdown decrements by 5 seconds every interval
- When countdown reaches 0 → **Bot leaves**

**Countdown Management:**

- Starts at `SILENT_PARTICIPANTS_COUNTDOWN_SECONDS` (180 seconds)
- Resets if:
  - New speech detected (any `SPEAKER_END` event)
  - New participant joins
- If some remaining participants have spoken before → **Bot stays** (wait for them)

**Configuration:**

- `RECENT_SPEECH_THRESHOLD_SECONDS` (default: 120 seconds) - threshold to enter this case
- `SILENT_PARTICIPANTS_COUNTDOWN_SECONDS` (default: 180 seconds) - countdown duration

**Example:**

```
Scenario A: All participants are silent
16:32:00 - Last speech 2+ minutes ago
16:32:00 - Remaining: [Alice (never spoke), Bob (never spoke), Bot]
16:32:00 - All remaining are silent → Start 3-min countdown (180s)
16:32:05 - Countdown: 175s remaining
16:33:00 - Countdown: 120s remaining
16:34:00 - Countdown: 60s remaining
16:35:00 - Countdown: 0s → Leave meeting

Scenario B: Some participants have spoken before
16:32:00 - Last speech 2+ minutes ago
16:32:00 - Remaining: [Alice (spoke before), Bob (never spoke), Bot]
16:32:00 - Alice has spoken before → Stay (wait for Alice to speak again)
```

---

### Case 5: Some Participants Have Spoken Before

**Trigger:** Case 4 conditions met BUT some remaining participants have spoken before

**Condition:**

- Not all remaining participants are silent
- At least one participant in the meeting has spoken before

**Behavior:**

- **Bot stays** - continues waiting for previously speaking participants to speak again

**Why:** Participants who have spoken before might speak again, so we keep waiting.

**Example:**

```
16:32:00 - Last speech 2+ minutes ago
16:32:00 - Remaining participants: [Youssef (spoke before), Amine (spoke before), Bot]
16:32:00 - Decision: "Some participants (2/3) have spoken before" → Stay
```

---

## Speech Detection Mechanism

### Duration Calculation

**Source:** `SPEAKER_START` and `SPEAKER_END` DOM events from Google Meet UI

**Process:**

1. On `SPEAKER_START`: Store timestamp in `activeSpeakerStarts` map
2. On `SPEAKER_END`:
   - Find matching `SPEAKER_START` timestamp
   - Calculate duration: `(END - START) / 1000` seconds
   - Accumulate to `speakerDurationCollector`
   - Update `lastSpeechTime = Date.now()`
   - Add participant ID to `spokenSpeakers` (first time only)
   - Store name in `speakerIdToNameMap` (persistent)

**Characteristics:**

- Real-time: Calculated immediately on each `SPEAKER_END`
- Cumulative: Durations accumulate across multiple speaking periods
- ID-based: Uses participant ID (not name) to handle name variations
- Handles overlapping speakers: Each participant tracked independently

### Speech Activation

**Trigger:** When any speaker's total accumulated duration reaches `SPEECH_ACTIVATION_THRESHOLD_SECONDS`

**Result:** `meetingHasHadSpeech = true` (permanently set)

**Default:** 5 seconds of total speaking time

**Example:**

```
Speaker A: 0.8s + 1.2s + 2.1s = 4.1s total → hasHadSpeech still false
Speaker A: + 0.9s = 5.0s total → hasHadSpeech = true ✅
```

---

## Complete Flow Example

### Scenario: Normal Meeting with Conversation

```
00:00 - Bot joins, meetingHasHadSpeech = false
00:05 - Alice speaks for 3s → Total: 3s (hasHadSpeech still false)
00:10 - Bob speaks for 4s → Total: 4s (hasHadSpeech still false)
00:15 - Alice speaks for 2.5s → Total: 5.5s → hasHadSpeech = true ✅
00:20 - Conversation continues (lastSpeechTime updated)
00:22 - Recent speech (< 2 min) → Case 3: Stay
00:25 - Recent speech (< 2 min) → Case 3: Stay
...
02:30 - Last speech 2+ minutes ago
02:30 - Remaining: [Alice (spoke), Bob (spoke), Bot]
02:30 - Some participants have spoken → Case 5: Stay
02:35 - Still waiting...
02:40 - Alice speaks again → lastSpeechTime updated → Back to Case 3
```

### Scenario: All Participants Leave Except Silent Ones

```
00:00 - Bot joins, 5 participants
00:10 - 3 participants speak → hasHadSpeech = true
00:15 - Active conversation
...
10:00 - Last speech 2+ minutes ago
10:00 - Remaining: [SilentPerson1, SilentPerson2, Bot] (speakers left)
10:00 - All remaining are silent → Case 4: Start 3-min countdown
10:03 - Countdown complete → Leave meeting
```

### Scenario: Meeting Becomes Completely Inactive

```
00:00 - Bot joins, active meeting
00:15 - Active conversation, hasHadSpeech = true
...
15:00 - Last speech 10+ minutes ago
15:00 - Case 3.5: Absolute silence timeout → Leave immediately
```

---

## Edge Cases Handled

### 1. Participant Name Capitalization Variations

- **Issue:** Same person can appear as "Youssef Bezzarga" and "Youssef BEZZARGA"
- **Solution:** Uses participant IDs for tracking, not names
- **Implementation:** `spokenSpeakers` stores IDs, not names

### 2. Participant Leaves Mid-Speech

- **Issue:** Participant removed from DOM while `SPEAKER_START` is active
- **Solution:** Synthetic `SPEAKER_END` sent, duration calculated, cleanup performed

### 3. Overlapping Speakers

- **Issue:** Multiple participants speaking simultaneously
- **Solution:** Each participant tracked independently via ID-based maps
- **Implementation:** `activeSpeakerStarts` Map keyed by participant ID

### 4. Name Preservation After Participant Leaves

- **Issue:** `activeParticipants` map cleared when participant leaves, losing name info
- **Solution:** `speakerIdToNameMap` persists names even after participants leave
- **Implementation:** Names stored on every `SPEAKER_END`, never cleared

---

## Debug Logging

The system provides comprehensive debug logs every 5 seconds:

```json
{
  "participants": 3,
  "hasHadSpeech": true,
  "lastSpeechTime": 123,
  "spokenSpeakers": ["Youssef Bezzarga (spaces/.../devices/70)", ...],
  "speakerDurations": {
    "Youssef Bezzarga": 245.7,
    "Amine Bezzarga": 192.9
  },
  "silenceCountdown": 0,
  "isInCountdown": false,
  "decision": "Recent speech activity (123s ago)"
}
```

**Key fields:**

- `participants`: Current participant count
- `hasHadSpeech`: Whether meeting has had meaningful speech
- `lastSpeechTime`: Seconds since last speech (or "never")
- `spokenSpeakers`: Array of participants who have spoken (with IDs)
- `speakerDurations`: Total speaking time per speaker (in seconds)
- `silenceCountdown`: Case 4 countdown timer (0 if inactive)
- `isInCountdown`: Whether Case 4 countdown is active
- `decision`: Current exit decision reason

---

## Summary

The bot exit logic is a multi-layered system that:

1. **Responds immediately** to being alone (Case 0)
2. **Detects dead meetings** that never had speech (Case 1, 2)
3. **Tracks active conversations** via recent speech (Case 3)
4. **Handles absolute silence** in previously active meetings (Case 3.5)
5. **Analyzes participant behavior** to detect when only silent participants remain (Case 4)
6. **Waits intelligently** for previously speaking participants (Case 5)

All thresholds are configurable via `.env` file, allowing customization for different use cases and meeting patterns.
