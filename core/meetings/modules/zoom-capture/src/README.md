# zoom-capture/src

Front door [`index.ts`](index.ts). The browser pieces:
[`zoom-speakers.ts`](zoom-speakers.ts) (`createZoomSpeakers` — polls Zoom's active-speaker DOM,
emits a name on each transition + a ~2 s heartbeat; selectors mirror the bot's Zoom `selectors.ts`,
with `getState()` forensics for live selector tuning) and
[`zoom-chat.ts`](zoom-chat.ts) (`createZoomChat` — defensive chat-panel reader → `{ sender, text }`).

[`track-name-resolver.ts`](track-name-resolver.ts) (`createTrackNameResolver`) is the per-track lane's
channel↔name correlator — **no DOM and no audio**, so it is golden-testable offline exactly like
`GmeetChannelBinder` and `TrackNamer`, the two namers it is the Zoom sibling of. It takes only two
inputs (a track's energy, a lit name) and owns: vote/argmax binding, margin hysteresis, 1:1 by
identity with a purity co-hold for genuinely duplicate names, idle release on rejoin, the sticky
self-exclusion backstop, and the `Speaker A/B/C` labels for channels that never earn a name.

Zero external imports — pure DOM (and, for the resolver, pure logic). The DOM scraping is
live-validated in a real Zoom.

`npm test` chains two L2 units, no browser:
[`zoom-capture.test.ts`](zoom-capture.test.ts) drives the real chat extraction (sender/body,
group-header climb, aria + timestamp handling) and the active-speaker flicker-confirmation against an
in-memory DOM shim; [`track-name-resolver.golden.test.ts`](track-name-resolver.golden.test.ts) pins
the resolver's failure modes — stray-sample self-correction, contamination, duplicate names, rejoin,
self-leak, and the letter fallback.
