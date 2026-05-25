---
services:
- meeting-api
- vexa-bot
---

# Real-time transcription — Zoom SDK (native)

Native Zoom Meeting SDK track. C++ wrapper around `libmeetingsdk.so`
exposed to the bot via the `zoom_sdk_wrapper.node` Node addon. Joins
Zoom meetings without Playwright/web automation.

## Capability boundary

This subfeature **only** runs in the `vexa-bot:sdk` image variant. The
default `vexa-bot:web` image is license-clean (no Zoom SDK binaries,
no Marketplace credentials required) and does not provide this code
path. See `services/vexa-bot/Dockerfile.sdk` and the self-host
walkthrough at `services/vexa-bot/docs/SELFHOST.md`.

`POST /bots` with `platform=zoom_sdk` against a deployment running the
web image must return 4xx with an actionable error naming the missing
artifact or env var — never 201. This is the capability boundary the
pack restores.

## License firewall

The Zoom Meeting SDK binary (`libmeetingsdk.so`, `qt_libs/`, the built
`zoom_sdk_wrapper.node`) is **never** committed to this repo and
**never** baked into a publicly-pushed image. Self-hosters download
the SDK from Zoom directly under the Zoom Marketplace EULA via the
operator-facing script `scripts/build-zoom-sdk.sh`.

## DoDs

See `dods.yaml` for the three machine-readable definitions of done:

- `bot_joins_zoom_sdk` — native SDK join completes end-to-end.
- `recording_uploads_zoom_sdk` — raw-audio recording, upload, and
  transcribe all complete on a live meeting.
- `pre_flight_rejects_missing_artifacts` — capability boundary holds.

These DoDs are exercised only on the **live human Zoom meeting** gate.
There is no synthetic Zoom server.

## Status

Restored from `release/260422-zoom-sdk` orphan branch by pack
`0.10.6x-pack-zoom-sdk-restore-capability-boundary` (epic #370). The
native build is **deferred**: the pack delivers only the Dockerfile
split, capability boundary, and DoD registration. First end-to-end
live run lands on a follow-up pack that bundles a Marketplace app and
ground-truth multi-speaker meeting harness.
