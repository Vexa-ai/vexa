# mixed/capture/ — mixed-lane capture (browser)

The mixed lane captures ONE mixed audio stream + per-platform "who's lit" hints.
Split so the audio tap is shared and each platform owns only its DOM watcher:

- `core/`  `@vexa/mixed-capture-core` — the mixed-audio tap + WebRTC hook (shared)
- `zoom/`  `@vexa/zoom-capture` — Zoom active-speaker DOM + chat
- `teams/` `@vexa/teams-capture` — Teams voice-outline + chat

All emit `mixed-capture.v1` (audio frame via `@vexa/capture-codec`; hints as
`active-speaker` events). Names resolve downstream in `@vexa/mixed-pipeline`.
