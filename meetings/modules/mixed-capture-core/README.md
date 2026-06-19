# @vexa/mixed-capture-core — the mixed-lane capture core (browser)

_meetings/ · module · meeting page → `mixed-capture.v1` audio (one mixed PCM stream, no names)._

Runs **inside the meeting page**. The platform-agnostic mixed-audio capture shared by every
mixed-lane platform (Zoom, Teams, arbitrary tab). Unlike Google Meet (per-participant `<audio>`
elements), these clients expose only a single MIXED audio stream, so this brick taps that one
stream into 16 kHz PCM — **no per-speaker channels, no names**. Who-is-talking comes from the
platform hint watchers in [`@vexa/zoom-capture`](../zoom-capture/) /
[`@vexa/teams-capture`](../teams-capture/), and names resolve downstream in
[`@vexa/mixed-pipeline`](../mixed-pipeline/) (time-windowed hints, no diarizer).

Two pieces: `createMixedAudioCapture` taps a mixed `MediaStream` (e.g. a `tabCapture` stream) to
PCM **and** re-plays it to the speakers (the `chromeMediaSource:'tab'` grab mutes the tab; capture
uses a `ScriptProcessorNode`, re-play a separate native-rate context on a cloned track — both
constraints learned the hard way in the extension's offscreen document). `installRemoteAudioHook`
patches `RTCPeerConnection` so each remote participant's audio track is mirrored into a hidden
`<audio data-vexa-injected>` element, exposing per-participant streams that Zoom/Teams hide in the DOM.

**Two hosts, one brick** — the [extension](../../../clients/)'s offscreen document and the
[bot](../../services/) container both consume this; same shape as the other capture bricks.
Recording (the mix → `recording.v1`) is a separate concern, not here.

## Surface
`createMixedAudioCapture` · `installRemoteAudioHook` (+ types `MixedAudioCapture`,
`MixedAudioOptions`). Front door: [`src/index.ts`](src/index.ts).

## Verify
`pnpm --filter @vexa/mixed-capture-core run build` — `tsc` clean. The DOM capture itself
(`ScriptProcessor`/`AudioContext`/`RTCPeerConnection` taps) is validated **live** in a real Zoom/Teams
(extension/bot) — consistent with how the lane has always been tested. `tsconfig` adds the `DOM` lib.
Covered by `gate:node`, `gate:isolation`, `gate:exports`, `gate:readme`.
