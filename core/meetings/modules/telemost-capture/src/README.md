# telemost-capture/src

Front door [`index.ts`](index.ts). The browser pieces:
[`telemost-signal.ts`](telemost-signal.ts) (`installTelemostSignalTap` — the media-engine socket tap:
roster + per-slot server VAD, installed at document start) and
[`telemost-speakers.ts`](telemost-speakers.ts) (`createTelemostSpeakers` — speaking-tile watcher,
start/stop per participant with a short release window + a ~2 s heartbeat; OWNS the Telemost tile
selector arrays).

Zero external imports — pure browser code (ambient DOM), bundled standalone into the bot's page bundle.

[`telemost-capture.test.ts`](telemost-capture.test.ts) (`npm test`) is the L2 unit: it drives the
watcher against a fake document and pins the exported selector arrays — no browser.
