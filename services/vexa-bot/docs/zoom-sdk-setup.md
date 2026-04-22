# Zoom SDK (Native) — Self-Hosted Platform Setup

> Authoritative guide for `platform=zoom_sdk` self-hosters. Derived from
> issue #150's reporter-validated walk-through; shipped as part of release
> 260422-zoom-sdk Pack C so the knowledge lives in-repo instead of in an
> issue body.

This page covers the `zoom-sdk` platform (native Zoom Meeting SDK, C++
addon, external-meeting capable with Marketplace publishing). For the
Playwright web-client track, see the `zoom-web` platform — it needs no
Marketplace app and no native SDK download; it shares the deploy path of
Google Meet and Microsoft Teams.

## 1. Create a Meeting SDK app on Zoom Marketplace

1. Go to <https://marketplace.zoom.us/user/build> (sign in with the Zoom
   account that will host meetings for the bot).
2. Create an app — **App type: Meeting SDK** (not "General App", not
   "OAuth"). Meeting SDK is the only type that lets the bot receive raw
   audio.
3. On the app's **App Credentials** page:
   - Copy **SDK Key**  → set as `ZOOM_CLIENT_ID` in `.env`
   - Copy **SDK Secret** → set as `ZOOM_CLIENT_SECRET` in `.env`
4. Leave the app in **Development** mode while you test internal-account
   meetings (same Zoom account host + bot). See §6 *Limitations* for
   external-meeting publishing.

## 2. Download the SDK binaries

Zoom Meeting SDK binaries are **proprietary and not redistributable** —
we can't ship them in the repo.

1. On your Marketplace app page: **Download** → **Meeting SDK** →
   **Linux x86_64**.
2. Extract the archive and place these files under
   `services/vexa-bot/core/src/platforms/zoom-sdk/native/zoom_meeting_sdk/`:

   ```
   libmeetingsdk.so                 (and symlink libmeetingsdk.so.1 -> libmeetingsdk.so)
   libcml.so
   libmpg123.so
   qt_libs/Qt/lib/                  (directory; bundled Qt libraries)
   ```

   The `qt_libs/Qt/lib/` path is **nested** — not `qt_libs/` directly.
   That's the path `entrypoint.sh` prepends to `LD_LIBRARY_PATH`, so Qt
   resolves before any system Qt that might be installed.

3. The `h/` include directory is already committed (public SDK headers);
   don't overwrite it.

## 3. Build the native addon

From the repo root:

```bash
bash scripts/build-zoom-sdk.sh
```

The script:

1. Verifies the SDK files are present at the expected paths.
2. Installs system dependencies via `apt-get` (`qtbase5-dev`,
   `libxcb-xtest0`) — needs root / `sudo`.
3. Runs `npm install --ignore-scripts` in `services/vexa-bot/` (workspace
   deps without the native-postinstall hook).
4. Runs `npx node-gyp rebuild` against `services/vexa-bot/binding.gyp`.
5. Smoke-loads the built addon via `node -e "require('./build/Release/zoom_sdk_wrapper')"` —
   catches symbol-resolution failures immediately.

On success you will see `Addon loads cleanly`. The addon lands at
`services/vexa-bot/build/Release/zoom_sdk_wrapper.node`.

> Manual equivalent (if the script does not fit your environment):
>
> ```bash
> sudo apt-get install -y qtbase5-dev libxcb-xtest0
> cd services/vexa-bot
> npm install --ignore-scripts
> npx node-gyp rebuild
> ```

## 4. Runtime configuration

Set in `.env` (or your deployment's env source):

```
ZOOM_CLIENT_ID=<your SDK Key>
ZOOM_CLIENT_SECRET=<your SDK Secret>
```

The bot container's entrypoint (`services/vexa-bot/core/entrypoint.sh`)
handles `LD_LIBRARY_PATH` automatically — it prepends
`$SDK_DIR/qt_libs/Qt/lib` before system paths so bundled Qt wins.

The Docker image also declares `ENV LD_LIBRARY_PATH=...` in the runtime
stage, so non-entrypoint invocations (`docker run --entrypoint
/bin/bash`) still work.

If you run the bot outside Docker, export `LD_LIBRARY_PATH` yourself:

```bash
SDK_DIR=services/vexa-bot/core/src/platforms/zoom-sdk/native/zoom_meeting_sdk
export LD_LIBRARY_PATH="$SDK_DIR/qt_libs/Qt/lib:$SDK_DIR:$LD_LIBRARY_PATH"
```

The SDK writes logs to `~/.zoomsdk/logs/` by default; ensure the
process can create that directory.

## 5. Zoom account settings (required for raw audio recording)

The bot receives raw audio by requesting **Local Recording** privilege
from the meeting host. Without the right account settings, every
recording start fails with `SDKERR_NO_PERMISSION` (code 12) regardless
of SDK credentials.

On the Zoom account that hosts the meetings:

1. **Settings -> Recording -> "Record to computer files"** -> **ON**.
2. **Settings -> Recording -> "Auto approve permission requests"** — enable
   for **BOTH** internal and external participants.
   - Without auto-approve, every `RequestLocalRecordingPrivilege` call
     requires the host to click "Allow" inside the meeting UI.
   - The bot's retry loop waits up to 10 s, so a quick manual click also
     works for small-scale testing — but do not ship that to production.

After the settings are enabled:

- The bot's first `startRecording` call returns `NO_PERMISSION` and
  triggers `RequestLocalRecordingPrivilege`.
- Zoom auto-approves within ~1 second.
- The retry loop (in `sdk-manager.ts::startRecording`) calls
  `startRecording` again, succeeds, and raw audio starts flowing to
  both `onMixedAudioData` (mixed PCM) and `onOneWayAudioData` (per-user
  PCM with `user_id`).

## 6. Limitations

- **Unpublished apps = same-account only.** While the Marketplace SDK
  app is in Development mode, it can only join meetings hosted by the
  same Zoom account that owns the app. Attempting to join an external
  meeting returns SDK error code 63. To join arbitrary external
  meetings, you must publish the app on Marketplace (review process
  applies).
- **Proprietary binaries.** `libmeetingsdk.so` + `qt_libs/` are licensed
  under the Zoom SDK Terms of Service. Do not redistribute them — each
  operator must download their own copy.
- **Platform coverage.** This guide covers Linux x86_64 only. ARM and
  macOS bot hosts require different SDK variants and are not wired into
  this build path today.
- **Per-user audio depends on the `onOneWayAudioRawDataReceived`
  callback.** If you fork the native wrapper and reintroduce the
  no-op override, you lose per-speaker attribution and the
  `zoom-sdk-per-speaker-raw-audio-forwarded` DoD fails.
- **Not verified in this cycle**: external meetings (blocked by the §6
  first bullet), headphone-quality audio levels, behavior with >10
  concurrent speakers. Release 260422-zoom-sdk validated the SDK track
  against same-account meetings up to 3 speakers per the reporter's setup.

## References

- Issue #150 — original P0/P1/P2 walkthrough (reporter-validated on SDK
  6.7.2.7020).
- `services/vexa-bot/core/src/platforms/zoom-sdk/native/src/zoom_wrapper.cpp` —
  the C++ wrapper; StartRecording flow follows the SDK's recording-
  controller contract.
- `services/vexa-bot/core/src/platforms/zoom-sdk/sdk-manager.ts` —
  TypeScript wrapper + diagnoseLoadFailure remediation table.
- `scripts/build-zoom-sdk.sh` — one-command build.
- `deploy/env-example` — where to set credentials.
- Zoom Marketplace docs — <https://marketplace.zoom.us/docs/sdk/native-sdks/linux/>
