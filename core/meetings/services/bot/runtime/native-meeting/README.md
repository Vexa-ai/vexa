# Native meeting process

The bot owns one native meeting session and injects its join port into `@vexa/join/node`. The join module owns admission, waiting, timeout and cancellation. The runtime owns process start, IPC validation and destruction. `leave()` waits for the native ended callback; `dispose()` destroys the process with a bounded forced-exit fallback. Leaving does not dispose the runtime. The composition root stops capture before leaving, then disposes the runtime.

`join-probe.mjs` is a joining-only composition root. It reads JoinConfig JSON from stdin, uses `ZOOM_SDK_DIR` and `ZOOM_SDK_ADDON`, reports observed admission and departure, and always disposes the runtime. It does not join audio or request recording. Production bot routing is unchanged. The separate @vexa/zoom-sdk-capture adapter owns conversion to the shared media contract; this joining-only probe does not start it.

Build `@vexa/join`, then run `node core/meetings/services/bot/runtime/native-meeting/join-probe.mjs` with private JSON stdin. Credentials never belong in arguments or logs. Missing external runtime fails with `runtime_missing`; install the SDK separately and build the Vexa-owned wrapper in `native/` using node-gyp and an external `zoom_sdk_dir`. Vendor files and built addons stay outside the repository and published images. The wrapper is not a separate service or an SDK module.

Tests: `node --test core/meetings/services/bot/runtime/native-meeting/test/*.test.mjs`. Fixtures run through a real child process. The optional SDK runtime has no network address and is shipped neither as a stock image nor a vendored dependency.

## Capture composition

`capture-probe.mjs` composes `@vexa/join/node` with `@vexa/zoom-sdk-capture` over the same runtime. It joins, requests raw-audio privilege, captures counters for 15 seconds, stops capture, leaves and disposes. It emits no PCM or participant names. The capture transport is `sdk-capture.v1`; joining remains `sdk-join.v1`. Capture joins VoIP and mutes the SDK participant; video remains off. PulseAudio must be available. This probe does not invoke STT, save recordings or alter production routing.

Only one capture session/topology should be active per runtime. IPC bounds in-flight audio messages; high-load native callback queuing and recording-permission revocation still need dedicated stress validation before production use. Native identity is meeting-scoped and is not an account identity.

## Audio pipeline

`audio-pipeline.mjs` composes per-participant SDK capture with the existing
`@vexa/gmeet-pipeline` channel engine and injected STT/transcript sink. Native signed
16-bit mono PCM is resampled to 16 kHz Float32; each SDK participant has an independent
buffer and LocalAgreement confirmation stream. Names come from native participant
metadata. Equal display names do not merge channels. `speaker_key` contains the native
meeting-scoped identity (`sdk-<userId>:<turn>`). Named SDK segments omit optional
`source`: the sealed transcript.v1 vocabulary has no SDK value, and `glow-bound`
would falsely claim browser attribution. Unknown names retain provisional attribution.
Browser defaults and the transcript schema are unchanged.

`transcription-probe.mjs` exercises join → capture → real Whisper → transcript counters
for 30 seconds, then flushes/stops capture, leaves, and disposes. It requires
`VEXA_TX_URL` and, when the service requires it, `VEXA_TX_KEY`. Logs contain counts
and failures, never participant names, speech text, or audio. This is an opt-in
probe composition; production bot dispatch is not switched to SDK by this change.

The optional `test/audio-pipeline.live.test.mjs` accepts `SDK_PCM_FIXTURE` (raw
32 kHz signed-16-bit little-endian mono), `SDK_EXPECT_WORDS` (comma-separated
ground-truth words), and the same STT environment. It tests real STT behind a
synthetic native input port; it does not establish live meeting capture.
