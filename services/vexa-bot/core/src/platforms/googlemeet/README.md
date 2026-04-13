# Google Meet Platform

Playwright-based bot integration for Google Meet. Handles the join flow, admission waiting, per-speaker audio capture, and speaker identity via DOM correlation.

## Join Flow

1. `join.ts`: Navigate to meeting URL, fill name input, click "Ask to join", enter waiting room, wait for admission. 5-second settle wait after navigation.
2. `admission.ts`: Polls for admission. Handles org-restricted blocks (unauthenticated guest).
3. `selectors.ts`: DOM selectors for name input, join button, mic/camera toggles. These are brittle -- Google changes their DOM periodically.

## Audio Capture

Per-speaker WebRTC tracks: each participant produces a separate audio stream via `<audio>`/`<video>` media elements in the DOM. This gives clean single-voice audio per speaker.

## Speaker Identity

Voting/locking system in `speaker-identity.ts`:
- DOM speaking indicators are correlated with audio tracks
- `LOCK_THRESHOLD=3` votes required, `LOCK_RATIO=0.7` (70%) to lock
- Once locked, a track is permanently assigned to a speaker name
- One-name-per-track and one-track-per-name enforced

## Known Issues

### audioTracks=0 — bot joins but captures no audio

**Status**: needs investigation, user-visible impact

The bot joins the meeting, is admitted, and finds media elements in the DOM — but those elements have `audioTracks=0`. After 10 retries (~30s) the bot enters "degraded monitoring mode" and produces 0 transcript segments for the entire session. A second bot sent to the same meeting typically works.

**Symptoms in logs:**
```
Found 1 media elements but none are active. Details:
  Element 1: paused=false, readyState=4, hasSrcObject=true, isMediaStream=true, audioTracks=0
```

**What we know:**
- Happens ~7.8% of joins (intermittent, not deterministic)
- The MediaStream exists (`hasSrcObject=true, isMediaStream=true`) but has no audio tracks
- `readyState=4` means the element is loaded — it's not a timing issue
- Retrying the same meeting with a new bot usually succeeds on the second attempt
- May be a race condition in how Google Meet attaches audio tracks to the WebRTC peer connection

**TODO:**
- [ ] Investigate whether this correlates with meeting size, admission delay, or browser state
- [ ] Check if waiting longer before polling (e.g. 10s post-admission vs current settle time) helps
- [ ] Consider auto-retry: if audioTracks=0 after all attempts, stop bot and create a new one
- [ ] Expose this as a clear status to users (e.g. `status: degraded_no_audio`) instead of silently producing empty transcripts

- Selectors break when Google updates their Meet DOM. Fix: inspect real Meet, update selectors.ts

## Development Notes

### Selector Maintenance

If selectors break, the fix is always: inspect a real Google Meet session, compare against `selectors.ts`, and update. The mock meeting page (if used for testing) must also match the real DOM structure.

### Key Files

| File | Purpose |
|------|---------|
| `join.ts` | Meeting join flow orchestration |
| `admission.ts` | Admission/waiting room handling |
| `selectors.ts` | DOM selectors (brittle, needs periodic updates) |
