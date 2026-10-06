# Native meeting process

The bot owns one native meeting session and injects its join port into `@vexa/join/node`. The join module owns admission, waiting, timeout and cancellation. The runtime owns process start, IPC validation and destruction. `leave()` waits for the native ended callback; `dispose()` destroys the process with a bounded forced-exit fallback. Leaving does not dispose the runtime. The composition root stops capture before leaving, then disposes the runtime.

`join-probe.mjs` is a joining-only composition root. It reads JoinConfig JSON from stdin, uses `ZOOM_SDK_DIR` and `ZOOM_SDK_ADDON`, reports observed admission and departure, and always disposes the runtime. It does not join audio or request recording. Production bot routing is unchanged. The existing capture modules and capture contract remain the media boundary; no SDK capture adapter is implemented by this probe.

Build `@vexa/join`, then run `node core/meetings/services/bot/runtime/native-meeting/join-probe.mjs` with private JSON stdin. Credentials never belong in arguments or logs. Missing external runtime fails with `runtime_missing`; install the SDK separately and build the Vexa-owned wrapper in `native/` using node-gyp and an external `zoom_sdk_dir`. Vendor files and built addons stay outside the repository and published images. The wrapper is not a separate service or an SDK module.

Tests: `node --test core/meetings/services/bot/runtime/native-meeting/test/*.test.mjs`. Fixtures run through a real child process. The optional SDK runtime has no network address and is shipped neither as a stock image nor a vendored dependency.
