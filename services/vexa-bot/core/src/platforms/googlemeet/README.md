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

- ~7.8% join failure rate ("No active media elements found" at join stage)
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
