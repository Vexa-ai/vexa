---
services:
- meeting-api
- vexa-bot
---

**DoDs:** see [`./dods.yaml`](./dods.yaml) · Gate: **not enforced** (empty dods)

# Zoom (web) Transcription

Zoom web-client integration via Playwright — the browser-client
entry point at `https://zoom.us/wc/…`. Structurally analogous to
Google Meet + Teams: DOM-based admission, MediaRecorder on the
shared audio element, speaker inference via participant panel.

## Status

This feature folder was split from `realtime-transcription/zoom/`
in release `260422-zoom-sdk` (Pack F). That cycle scoped the
**zoom-sdk** body (native SDK recording + parity with gmeet);
zoom-web's story was explicitly deferred.

DoDs: to be authored in a future cycle alongside (or after) any
zoom-web bug-fix push. Until then, `dods: []` and gate `0` —
un-gated, same pattern gmeet used before its 260422 restoration.

## Code

- Handler entrypoint: `services/vexa-bot/core/src/platforms/zoom-web/index.ts`
- DOM selectors: `services/vexa-bot/core/src/platforms/zoom-web/selectors.ts`
- Strategies: `admission.ts`, `join.ts`, `leave.ts`, `prepare.ts`, `recording.ts`, `removal.ts`

## Peer

- `realtime-transcription/zoom-sdk` — native Meeting SDK track
  (proprietary C++ addon, external-meeting capable with Marketplace
  publishing). Was the other half of the old `zoom` feature folder.
