# @vexa/zoom-sdk-join

Experimental native SDK joining controller. Independent from browser joining, capture, buffers and transcription; not wired into production routing.

## Surface

The root exports types only. `/node` exports `joinSdk(config, options)` and `JoinError`. Supply `meetingId`, `displayName`, SDK `jwt`, optional `password`, `onBehalfToken` and `zak`. Tokens are injected through private IPC, never arguments or event logs. `sdkDir` and `addonPath` must be absolute paths to an independently installed Linux SDK and locally built native addon.

`admitted` resolves only on native `in_meeting`. Host/waiting-room states remain pending. Authentication failure, native failure, crash, timeout and cancellation reject with a typed code. Admission timeout defaults to 120 seconds and stops after admission. `stop()` leaves, cleans up and resolves after process exit; after 3 seconds a stuck worker is killed. An AbortSignal cancels before or after admission. Observe `onState` and `closed` for post-admission termination. Observer exceptions cannot interrupt cleanup. There is no recording or audio subscription in this controller.

The parent/worker protocol is [sdk-join.v1](../../contracts/sdk-join.v1/). Native stdout/stderr are intentionally discarded; native SDK default logging is disabled. No secrets appear in errors. The separate [native source](../../services/zoom-sdk-native/) remains operator-built. No proprietary SDK files, binary addon or SDK download step are shipped.

## Verify

`pnpm --filter @vexa/zoom-sdk-join build`

`node --test core/meetings/modules/zoom-sdk/test/*.test.mjs`

Tests use disposable fake addons through the real child-process boundary. They prove lifecycle semantics, not live SDK admission.

## Live harness

On Linux with the external SDK and compiled addon installed, set `ZOOM_SDK_DIR` and `ZOOM_SDK_ADDON`, then pipe one JSON JoinConfig privately to `node core/meetings/modules/zoom-sdk/scripts/join.mjs`. The harness prints credential-free states and stays until meeting end or Ctrl-C. Use a currently authorized meeting; the fixture ID is not a live target.

The earlier native runtime compiled and initialized with SDK 6.7.2.7020. The saved development app returned authentication code 11; real admission remains unverified. Its Meeting SDK feature was observed disabled. No application settings are changed by this module. Operator terms, app configuration and any commercial review remain separate prerequisites.
