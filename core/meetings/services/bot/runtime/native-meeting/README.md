# Native meeting process

The bot owns one native meeting session and injects its join port into `@vexa/join/node`. The join module owns admission, waiting, timeout and cancellation. The runtime owns process start, IPC validation and destruction. `leave()` waits for the native ended callback; `dispose()` destroys the process with a bounded forced-exit fallback. Leaving does not dispose the runtime. The composition root stops capture before leaving, then disposes the runtime.

`join-probe.mjs` is a joining-only composition root. It reads JoinConfig JSON from stdin, uses `ZOOM_SDK_DIR` and `ZOOM_SDK_ADDON`, reports observed admission and departure, and always disposes the runtime. It does not join audio or request recording. Production bot routing is unchanged. The separate @vexa/zoom-sdk-capture adapter owns conversion to the shared media contract; this joining-only probe does not start it.

Build `@vexa/join`, then run `node core/meetings/services/bot/runtime/native-meeting/join-probe.mjs` with private JSON stdin. Credentials never belong in arguments or logs. Missing external runtime fails with `runtime_missing`; install the SDK separately and build the Vexa-owned wrapper in `native/` using node-gyp and an external `zoom_sdk_dir`. Vendor files and built addons stay outside the repository and published images. The wrapper is not a separate service or an SDK module.

Tests: `node --test core/meetings/services/bot/runtime/native-meeting/test/*.test.mjs`. Fixtures run through a real child process. The optional SDK runtime has no network address and is shipped neither as a stock image nor a vendored dependency.

## Capture composition

`capture-probe.mjs` composes `@vexa/join/node` with `@vexa/zoom-sdk-capture` over the same runtime. It joins, requests raw-audio privilege, captures counters for 15 seconds, stops capture, leaves and disposes. It emits no PCM or participant names. The capture transport is `sdk-capture.v1`; joining remains `sdk-join.v1`. Capture joins VoIP and mutes the SDK participant; video remains off. PulseAudio must be available. This probe does not invoke STT, save recordings or alter production routing.

Only one capture session/topology should be active per runtime. IPC bounds in-flight audio messages; high-load native callback queuing and recording-permission revocation still need dedicated stress validation before production use. Native identity is meeting-scoped and is not an account identity.
