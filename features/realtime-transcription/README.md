---
services:
- meeting-api
- vexa-bot
- tts-service
---

# Real-time transcription

Live audio → speaker-attributed transcript segments while a meeting is in
progress. Covers Google Meet, Microsoft Teams, and Zoom Web (Playwright-
based browser bot, not the native SDK track).

## Shared pipeline

All three platforms feed a common downstream pipeline:

```
audio chunk → per-speaker buffer → Whisper submission → confirmed segment
                       (SpeakerStreamManager — services/vexa-bot/core/src/services/speaker-streams.ts)
```

One `SpeakerStreamManager` instance with N internal buffers keyed by
speaker. Each buffer:

- Collects audio chunks for one speaker.
- Submits unconfirmed audio to Whisper every ~2 s (configurable
  `submitInterval`).
- Confirms text on word-prefix match across consecutive submissions
  (`speaker-streams.ts` confirm logic).
- Emits a confirmed segment via `onSegmentConfirmed(speakerId,
  speakerName, text, ...)` with the speaker name attached at emit time.

Whisper backend (`TranscriptionClient`) is wired via
`TRANSCRIPTION_SERVICE_URL` — same backend for all three platforms.

## Where the platforms diverge

The architectural choice is upstream of the manager: **how does the bot
acquire audio and bind it to speaker names?**

|  | **Google Meet** | **Microsoft Teams** | **Zoom Web** |
|--|-----------------|---------------------|--------------|
| Audio source | Per-participant `<audio>` DOM elements, individually subscribed via `MediaStreamAudioSourceNode` | Combined / mixed audio stream (single downlink) | **Same as gmeet** — per-participant `<audio>` DOM elements, individually subscribed |
| Streams open at any time | One per visible participant | One mixed | **3 SFU slots** — recycled across N speakers |
| Name source | DOM text adjacent to participant tile | Caption-event payload (`(speaker, text, ts)` tuples) | DOM active-speaker badge (`.speaker-active-container__video-frame`) |
| Name binding | **Stable** — track 0 = Alice for the entire meeting (vote-locked via `speaker-identity.ts`) | Bound per caption event; no track concept | **Volatile** — `updateSpeakerName()` called on every chunk to remap |
| Speaker detection trigger | `MutationObserver` on speaking-indicator class mutations + 500ms polling fallback | Caption event arrival from Teams DOM | DOM polling every 250ms |
| Track identity matters? | Yes (locked, vote-driven) | N/A (no per-speaker tracks) | **No** (recycled by Zoom's SFU) |
| Code home | `services/vexa-bot/core/src/platforms/googlemeet/` | `services/vexa-bot/core/src/platforms/msteams/` | `services/vexa-bot/core/src/platforms/zoom/web/` |

## Architecture families

- **gmeet family**: stable per-track audio acquisition + state-locked
  speaker-track binding. Track 0 is Alice forever.
- **msteams family**: mixed audio + caption-driven labeling. No per-track
  concept. Speaker identity arrives as event data, not via track lookup.
- **Zoom Web (third pattern)**: gmeet-style audio acquisition + msteams-
  style reactive labeling. Borrows gmeet's `MediaStreamAudioSourceNode`
  plumbing but inserts a `updateSpeakerName()` re-mapping step on every
  chunk because Zoom's SFU recycles its small set of audio track slots
  across all active speakers — track identity carries no meaning.

The gmeet and msteams patterns each have a natural state-lock for
correctness: vote-locked tracks (gmeet) and event-keyed names (msteams).
Zoom Web has neither — its DOM-polling overlay is reactive only, with
the polling interval as the entire correctness budget. See
`zoom/README.md` for the implications.

## Subfeatures

- [`gmeet/`](./gmeet/) — Google Meet (Playwright)
- [`msteams/`](./msteams/) — Microsoft Teams (Playwright)
- [`zoom/`](./zoom/) — Zoom Web (Playwright; canonical platform value
  `zoom` once the Wave 3 platform-enum upgrade ships)

# Intentionally un-gated: legacy feature carries no machine-readable

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **confidence ≥ 90%**
# DoDs yet. Populate `dods:` before this feature's next release or
# its expected behavior changes.
gate:
  confidence_min: 0    # not enforced until dods: is populated
dods: []   # intentionally un-gated, reason: DoDs not yet authored
