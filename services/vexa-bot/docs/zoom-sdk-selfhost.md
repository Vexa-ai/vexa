# Zoom Meeting SDK — self-host walkthrough

This walkthrough is for self-hosted Vexa operators who want to
transcribe Zoom meetings via the **native Meeting SDK** path rather
than the browser-automation `zoom_web` path.

Origin: pack `0.10.6x-pack-zoom-sdk-restore-capability-boundary`
(epic [#370](https://github.com/Vexa-ai/vexa/issues/370)). This pack
restores the SDK code paths, splits the bot image, and adds an API
capability boundary; it does **not** redistribute the Zoom SDK binary.

## SDK vs Web — which one do you want?

| Question | Choose `zoom_web` | Choose `zoom_sdk` |
|---|---|---|
| Do you have a Zoom Marketplace app + Client ID/Secret? | not required | **required** |
| Do you have rights to download the Zoom Meeting SDK Linux archive under Zoom's EULA? | not required | **required** |
| Need the bot to join meetings hosted by accounts other than yours? | yes | publishing-gated |
| Need per-user raw audio for clean speaker attribution? | timing-correlation only | **yes (planned)** |
| Need a license-clean Docker image suitable for public OSS distribution? | yes | no |

If you answered "no" to either of the first two rows, use `zoom_web`.
The default `vexa-bot:web` image serves it out-of-the-box.

## Prerequisites for `zoom_sdk`

1. **Marketplace app.** Register a "General App" (Server-to-Server
   OAuth) at https://marketplace.zoom.us/develop/create. Note the
   Client ID and Client Secret.

2. **SDK download.** From the Marketplace dashboard, download the
   Linux Meeting SDK archive (current target: 5.x). You accept Zoom's
   SDK EULA when you download it.

3. **Linux build host** with: `build-essential`, `cmake`, `python3`,
   `libssl-dev`, `qtbase5-dev`, `node` (20+), `docker`.

## Build steps

```bash
# 1. From the repo root.
cd vexa

# 2. Drop the SDK archive contents into the staging directory.
#    Final layout (binaries are .gitignored, will not be committed):
#      services/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk/
#        ├── h/                  (headers — already in-repo)
#        ├── libmeetingsdk.so    (you supply)
#        ├── qt_libs/            (you supply)
#        └── ...

# 3. Validate the layout. This step is also exercised by the synthetic
#    gate against a stubbed tree, so it doesn't require the binaries
#    to be present.
scripts/build-zoom-sdk.sh --validate-only

# 4. Build the SDK image. This invokes node-gyp and docker build
#    against services/vexa-bot/Dockerfile.sdk.
scripts/build-zoom-sdk.sh --build
```

The resulting image is **never** pushed to a public registry by OSS
CI. It exists only on your build host.

## Deploy

In your compose/.env or Helm values, set:

```bash
BOT_IMAGE_NAME=vexaai/vexa-bot:sdk
ZOOM_CLIENT_ID=<your-marketplace-client-id>
ZOOM_CLIENT_SECRET=<your-marketplace-client-secret>
```

Then send a Zoom meeting request:

```bash
curl -X POST https://your-vexa/bots \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"platform":"zoom_sdk","native_meeting_id":"123456789"}'
```

If `BOT_IMAGE_NAME` is **not** the `:sdk` variant, or either of
`ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` is unset, the request returns
**422** with `error: zoom_sdk_unavailable` and a `missing:` list
naming the unsatisfied precondition. This is the capability boundary
the pack restores: no more silent 201s that lead to a container that
crashes on undefined symbols.

For one-time credentials (the user supplies their own meeting access
keys per request), set `zoom_obf_token` and/or `zoom_zak_token` on the
request body.

## Troubleshooting

- **`undefined symbol _ZNSt28__atomic_futex_unsigned_base...Qt_5`**
  The native addon is loading the wrong Qt. Rebuild after
  `apt install qtbase5-dev` and verify `LD_LIBRARY_PATH` includes the
  SDK's `qt_libs/`.

- **`Cannot find module './build/Release/zoom_sdk_wrapper'`**
  `npm run build:native` did not run. Re-run
  `scripts/build-zoom-sdk.sh --build` and check the output for
  node-gyp errors.

- **`Marketplace error code 63`**
  Your Marketplace app is not published. Same-account meetings work
  during development; publishing is a separate business action.

## Out of scope for this pack

- Bundling the Zoom SDK binary in any Vexa-published image or artifact.
- Publishing the Marketplace app on behalf of operators.
- Per-user raw-audio forwarding (`onOneWayAudioRawDataReceived`):
  ships in a follow-up pack. v1 uses mixed audio plus
  `onActiveSpeakerChange` for speaker correlation.
