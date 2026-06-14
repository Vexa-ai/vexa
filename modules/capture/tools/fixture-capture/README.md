# fixture-capture — the capture.v1 fixture extension

A dedicated Chrome extension that does **one thing**: capture `capture.v1` from a
Google Meet / Zoom / Teams tab and stream it to the recorder, so anyone can
extract fixtures from a real meeting **without the bot, an account, or a
transcription backend**.

It is *not* the product extension — no sidepanel, no dashboard, no API key, no
gateway. Just: open the meeting tab, hit Start, talk, Stop → a conformant fixture
lands in the store.

## No circular dependency

Self-contained. It imports only **leaf** dependencies — never the product
extension:

```
modules/capture/tools/fixture-capture
   └─ imports → @vexa/capture  (modules/capture/src/* — browser-pure brick)
   └─ imports → contracts/capture/v1/schema  (the wire codec)
```

Both extensions (this one and `services/vexa-extension`) independently own their
MV3 glue and import the shared brick + contract. No edge between them.

## Install

```bash
# 1. build the recorder (the WS sink) and this extension
cd modules/recorder && npm install && npm run build
cd modules/capture/tools/fixture-capture && npm install && npm run build   # → dist/

# 2. load the extension
#    chrome://extensions → Developer mode → Load unpacked → modules/capture/tools/fixture-capture/dist
```

## Use

```bash
# 1. run the recorder (writes to $VEXA_FIXTURE_CACHE, default ~/.vexa/fixtures)
cd modules/recorder && npm run capture          # listens ws://localhost:9099/ingest

# 2. open the meeting tab you're already in, click the extension icon
#    - Recorder WS URL: ws://localhost:9099/ingest  (default)
#    - Start → talk → Stop
```

The fixture is auto-named `platform-topology-meetingId` and written to
`$VEXA_FIXTURE_CACHE/capture/v1/<name>/` as `audio/*.wav` + `events.jsonl` +
`meta.json`. Validate it:

```bash
node contracts/capture/v1/validate.mjs ~/.vexa/fixtures/capture/v1/<name>
```

## What it captures (per platform)

- **gmeet** — per-participant audio (native `<audio>` elements) → `topology: per-participant`.
- **zoom / teams** — one mixed tab-audio track + DOM speaker hints → `topology: mixed`.

Same `capture.v1` wire codec as the bot/product extension, so a fixture captured
here is byte-identical in shape to one captured in production.
