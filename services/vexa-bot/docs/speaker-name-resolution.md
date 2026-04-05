# Speaker Name Resolution

**Overall confidence: 90%** — Fix implemented and compiles. Not yet tested against real Google Meet.

## WHY

Google Meet audio elements are **not inside participant tiles** in the DOM — they're in a separate part of the tree. Raw names from the DOM can be junk like `"GP\nGoogle Participant (spaces/vwKMOQeV5YsB/devices/145)"` for Spaces/Chat device participants. The bot must map audio track indices to human-readable speaker names for correct transcript attribution.

## WHAT

A layered, browser-side name resolution system that collects participant information and exposes it to Node.js via two injected functions:

### Browser-side (recording.ts)

**`__vexaGetAllParticipantNames()`** returns `{ names: Record<participantId, displayName>, speaking: string[] }`:
- Collects all participant tiles from DOM using `googleParticipantSelectors`
- Extracts names via priority-ordered selectors with fallbacks
- Tracks which participants are currently speaking

**`__vexaSpeakerEvents`** — chronological history of speaker state changes:
- Each event: `{ event_type, participant_name, participant_id, relative_timestamp_ms }`
- Fed by MutationObserver watching speaking indicator CSS classes

### Name extraction hierarchy (getGoogleParticipantName)

1. `span.notranslate` — most reliable Google Meet name element
2. Configured `googleNameSelectors` (`.zWGUib`, `.cS7aqe.N2K3jd`, etc.)
3. `data-self-name` attribute
4. `aria-label` on container or descendants
5. `data-tooltip` attribute
6. **Fallback**: `"Google Participant (${participantId})"` — the junk name

Each fallback validates length (1-50 chars) to filter UI text like "Let participants send messages".

### Node-side (speaker-identity.ts → resolveSpeakerName)

Four-tier resolution when audio arrives for a media element index:

1. **Direct index mapping** — element index → participant array position
2. **Currently speaking** — if exactly one participant is speaking, use their name
3. **Speaker events history** — mine event log for most recent unended SPEAKER_START
4. **Fallback** — `"Presentation"` (no participant tile = screen share)

### Caching

- `speakerNameCache: Map<string, string>` keyed by `"${platform}:${elementIndex}"`
- First audio from element triggers DOM query; subsequent uses cache
- `invalidateSpeakerName()` clears on participant leave

### Filtering

- Excludes bot's own name (case-insensitive)
- Excludes UI text: "Let participants send messages", "Turn on captions", etc.
- Validates name length bounds

## HOW — Data Flow

```
Google Meet Browser                          Node.js Process
────────────────────────────────────────────────────────────

1. User joins meeting
2. recording.ts initializes speaker detection
3. For each participant tile in DOM:
   • Extract participant ID (data-participant-id or generated)
   • Extract name (span.notranslate → data-self-name → aria-label → etc.)
   • Store in participants map
4. MutationObserver fires on speaking indicator class changes
   → Emit SPEAKER_START / SPEAKER_END to __vexaSpeakerEvents
5. Audio data arrives from media element
6. Browser calls __vexaPerSpeakerAudioData(speakerIndex, audioData)
                                           7. handlePerSpeakerAudioData() receives index
                                           8. resolveSpeakerName(page, index)
                                           9. Query __vexaGetAllParticipantNames()
                                          10. Apply 4-tier resolution
                                          11. Cache result
                                          12. Feed audio to SpeakerStreamManager
                                          13. Transcribe as "speaker: text"
```

## Key Files

| File | What |
|------|------|
| `core/src/services/speaker-identity.ts:90-176` | Node-side resolution logic |
| `core/src/platforms/googlemeet/recording.ts:311-354` | Browser-side name extraction |
| `core/src/platforms/googlemeet/selectors.ts:200-212` | CSS selectors (may be stale) |
| `core/src/index.ts:1125-1155` | handlePerSpeakerAudioData entry point |

## Known Bugs (2026-03-15 audit)

### Bug 1: Positional mapping is the only path that runs

In `speaker-identity.ts:123-125`, the code returns early on positional match:
```typescript
if (idx < filteredNames.length) {
  return filteredNames[idx];  // ← returns here, never checks speaking
}
```
The "currently speaking" check at lines 133-138 is **dead code** — it only runs when `idx >= filteredNames.length` (screen share case). The speaking-based resolution, which should be the most reliable strategy, never executes for normal participants.

### Bug 2: Positional mapping assumes track order = tile order

Audio element index N is mapped to participant tile index N in the DOM. But Google Meet doesn't guarantee any ordering relationship between audio tracks and participant tiles. Screen shares add extra audio elements that shift indices. Participants joining/leaving reorder tiles but not tracks.

### Bug 3: Cache never invalidates

`clearSpeakerNameCache()` and `invalidateSpeakerName()` are defined and exported but **never called** anywhere in the codebase. The cache is write-once — if participant B takes participant A's audio track slot, all subsequent audio is permanently attributed to A.

### Bug 4: Junk names pass validation

For Google Spaces/Chat devices, `aria-label` contains `"Google Participant (spaces/vwKMOQeV5YsB/devices/145)"` which is < 50 chars, passes the length check, and gets cached permanently as the speaker name.

### Bug 5: `__vexaSpeakerEvents` accumulates junk names

`sendGoogleSpeakerEvent()` in `recording.ts:392-407` calls `getGoogleParticipantName()` which can return junk names. These junk names are then accumulated in `__vexaSpeakerEvents` and used for post-meeting speaker mapping in `meeting.data.speaker_events`. The deferred transcription pipeline maps segments to these names, producing junk speaker labels.

## Fixes implemented (2026-03-15)

### Correlation-based track mapping (speaker-identity.ts) — replaces all previous approaches

**How it works:**
1. When audio arrives on track N and exactly one speaking indicator is active → record a **vote**: track N = that speaker
2. After 3 consistent votes (70%+ for same name) → **lock** the mapping
3. Locked mappings survive simultaneous speech — no more guessing during overlaps
4. If speaking name is already locked to a different track → don't assign (prevents name stealing)
5. Mappings expire after 60s to handle participant changes
6. Re-resolution every 10s picks up corrections

**Positional fallback removed entirely.** If we can't correlate a track, it returns "Presentation" — better wrong label than wrong speaker.

### Junk name filter (recording.ts)
`isJunkName()` rejects `"Google Participant (spaces/..."`, `spaces/`, `devices/` patterns.

### Test results (2-speaker real Google Meet)
- Track 0 → "info vexa" LOCKED (3/3 votes, 100%) — correct
- Track 1 → first assigned "info vexa" (stale speaking signal), self-corrected to "Dmitriy Grankin" at 10s re-resolve
- After the "already locked elsewhere" fix: track 1 won't steal track 0's locked name

## Scaling risk: large meetings (>9 participants)

**Confidence: 50% for 20+ speaker meetings**

The correlation algorithm scales fine (O(speakers × tracks), tiny constants). The risk is the **DOM** — Google Meet doesn't guarantee all participants are visible in DOM at once.

### DOM pagination
Google Meet shows ~9 video tiles at a time. Remaining participants are paginated or in a sidebar. `__vexaGetAllParticipantNames()` only finds tiles **currently rendered in DOM**. If a speaker's tile is off-screen:
- Their name won't appear in the `speaking` array
- No votes get recorded for their track
- Their track stays as "Presentation" until their tile scrolls into view

### Speaking indicators only fire for visible tiles
The MutationObserver watches CSS class changes on participant tiles. If the tile isn't rendered (paginated away), no mutation fires. The 500ms polling fallback also only checks tiles in DOM.

### Tile recycling
Google Meet may reuse DOM elements for different participants as the grid shifts. A tile that was "Alice" becomes "Bob" when someone else starts speaking. The `data-participant-id` changes but existing MutationObservers may be on stale elements.

### People panel vs video grid
The bot clicks "People" button at init to stabilize DOM. The People panel roster and the video grid are separate DOM trees. Names in the roster may not have speaking indicators attached.

### Mitigation ideas (not implemented)
- **Use People panel roster** as the name source instead of video tiles — it always lists all participants regardless of pagination
- **Check if People panel has speaking indicators** — if it does, it's a more reliable source than video tiles for large meetings
- **WebRTC `RTCPeerConnection.getStats()`** — exposes `trackIdentifier` per inbound stream, may link directly to participant without DOM. Not investigated.
- **Periodic "People" panel scroll** — programmatically scroll the People list to force all participant tiles into DOM, ensuring MutationObservers cover everyone
