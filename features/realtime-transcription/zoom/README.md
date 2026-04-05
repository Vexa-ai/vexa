# Zoom Transcription

**Not shipped.** MVP1 blocked (speaker identity broken by SFU track remapping).

## Why

Third platform. Same shared pipeline (SpeakerStreamManager), different audio entry. Zoom uses per-speaker `<audio>` elements like GMeet, but SFU limits to 3 streams and remaps tracks dynamically.

## What

Same as GMeet pattern: N `<audio>` elements → N ScriptProcessors → shared pipeline. But:

- SFU delivers max 3 audio streams regardless of participant count
- Tracks remap dynamically (track 0 = Charlie, then later track 0 = Alice)
- Permanent track locking is incompatible — need DOM-based per-segment attribution
- Recorder bot occupies 1 of 3 tracks

### MVP ladder

| MVP | Gate | Status |
|-----|------|--------|
| MVP0 | Audio flows (amplitude > 0.005) | DONE (score 80) |
| MVP1 | Speaker identity correct | BLOCKED — SFU remapping breaks track locking |
| MVP2 | E2E transcription + first dataset | Blocked by MVP1 |
| MVP3 | Automated collection (TTS bots) | Blocked by MVP2 + reCAPTCHA |
| MVP4 | Quality parity with GMeet/Teams | Blocked by MVP3 |
| MVP5 | Production hardening | Blocked by MVP4 |

### Key files

| File | Role | Status |
|------|------|--------|
| `platforms/zoom/web/join.ts` | URL builder, enter meeting | Working |
| `platforms/zoom/web/prepare.ts` | Audio channel join, dismiss dialogs | Working |
| `platforms/zoom/web/admission.ts` | Waiting room detection | Working |
| `platforms/zoom/web/recording.ts` | Speaker polling (250ms) | Working |
| `platforms/zoom/web/removal.ts` | Ejection detection | Working |
| `platforms/zoom/web/selectors.ts` | CSS selectors | Verified |
| `scripts/zoom-auto-admit.js` | Admit from waiting room | Built, untested (reCAPTCHA) |

### Dead ends

- PulseAudio capture: Chrome doesn't route Zoom WebRTC through PulseAudio (records silence)
- Permanent track locking: SFU remaps tracks, locks assign wrong names
- Zoom Web SDK raw audio: SDK doesn't expose it, `mediaCapture` only triggers consent popup
- Rapid bot joins: reCAPTCHA after 3-4 joins from same IP

## How

```bash
# Same API — platform auto-detected from URL
POST /bots {"meeting_url": "https://zoom.us/j/123456789?pwd=..."}
```

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Test |
|---|-------|--------|---------|-------|--------|----------|--------------|------|
| 1 | Bot joins and captures per-speaker audio | 25 | ceiling | 0 | — | — | — | tests/rt-collection.md |
| 2 | Each GT line: correct speaker despite SFU remapping | 30 | ceiling | 0 | — | — | — | tests/rt-replay.md |
| 3 | Each GT line: content matches (≥ 70% similarity) | 20 | — | 0 | — | — | — | tests/rt-replay.md |
| 4 | No missed GT lines | 10 | — | 0 | — | — | — | tests/rt-replay.md |
| 5 | Automated TTS collection (no reCAPTCHA block) | 15 | — | 0 | — | — | — | tests/rt-collection.md |

Confidence: —
