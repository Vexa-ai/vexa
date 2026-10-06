# Native joining live receipt — 2026-10-06

Expected: the public join backend composed with the bot-owned native runtime authenticates, waits for native admission, leaves, observes departure and releases its process. No capture or recording request.

Actual: the `join-probe.mjs` composition root ran against an owned dedicated test meeting with external Linux x86_64 SDK 6.7.2.7020 and the locally built Vexa wrapper. The saved development app credentials were injected privately as a freshly signed SDK JWT. No credentials, meeting IDs or passcodes are recorded here.

```jsonl
{"version":1,"kind":"state","state":"initializing"}
{"version":1,"kind":"state","state":"authenticating"}
{"version":1,"kind":"state","state":"connecting"}
{"version":1,"kind":"state","state":"connecting"}
{"version":1,"kind":"state","state":"in_meeting"}
{"receipt":"native_admission_observed"}
{"receipt":"native_departure_observed"}
{"receipt":"runtime_closed","code":0,"signal":null,"forced":false}
```

Connecting is emitted only after successful native authentication. Admission is the native in-meeting callback. Departure is resolved only by the native ended callback. SSH/harness exit was 0; a subsequent process check found no probe/worker process. The extra browser participant used to verify the meeting was also left; the existing host was not ended.

Verdict: joining, admission, leave and cleanup passed live. Audio/video were off; recording permission, media capture and production routing were not tested or activated. Deterministic tests separately cover cancellation, timeout, auth failure, crashes, forced cleanup and preservation of the runtime between leave and host disposal.

## Backend relocation and capture — 2026-10-06

After moving native join to `join/src/zoom-sdk/` (beside web `join/src/zoom/`), the joining-only probe was rerun against the owned dedicated meeting. Native authentication, admission, departure and unforced exit 0 passed again.

The separate capture probe used the same admitted runtime with per-participant capture. Native audio callbacks copy SDK PCM before returning, carry callback epoch time across IPC, and identify participants by SDK user ID. The capture module emits the existing 16 kHz mono Float32 contract.

First capture run:
```jsonl
{"capture":"permission_pending"}
{"capture":"subscribed"}
{"capture":"receiving_audio"}
{"receipt":"capture_counters","frames":871,"samples":139360,"channels":1,"sampleRate":16000,"peak":0.4280286729335785,"nonSilent":true}
{"receipt":"runtime_closed","code":0,"signal":null,"forced":false}
```

A second probe added name/input-rate counters but received zero audio frames during its 15-second observation window, despite successful subscription. It reported `frames:0`, `samples:0`, `channels:0`, `namedFrames:0`, `inputRates:[]`, `nonSilent:false`, exited 1 and still cleaned the runtime with unforced exit 0. No retry was labelled a pass. Silence versus absence of delivered audio was not independently established in that window.

Verdict: real non-silent per-participant capture and clean stop/leave/disposal were witnessed in the first run. SDK-provided name binding, concurrent speakers, long-running capture/revocation and end-to-end STT remain unproven live. No PCM or participant names were saved by either probe. Shared capture-wire round-trip, 32/48→16 kHz anti-aliasing, channel interleaving, timestamp continuity/gaps and cleanup were tested with deterministic fixtures.

Reuse: per-user frames fit the existing Google Meet channel-routed pipeline; its current transcript source label `glow-bound` needs an SDK provenance alternative before production wiring. Mixed audio can use the mixed pipeline; SDK active-speaker name hints are not yet wired. Browser DOM/WebRTC extraction is not part of native capture.


## Channel pipeline composition — 2026-10-06

Expected: the user-started test meeting supplies participant audio; SDK capture
feeds the existing channel engine and real Whisper service; named transcript
segments are emitted and teardown completes without killing the worker.

Actual: the first composition run lacked the STT token and surfaced unauthorized
errors (13 calls, zero segments). The runtime still exited 0 without forced
termination. After supplying the existing development STT credential, the live
run observed native admission, subscription and receiving_audio, then:

```jsonl
{"receipt":"transcription_counters","calls":12,"segments":3,"named":3,"speakers":1,"failed":false}
{"receipt":"runtime_closed","code":0,"signal":null,"forced":false}
```

Verdict: SDK per-participant audio → 16 kHz capture → channel buffers/LocalAgreement
→ real Whisper → named transcript segments passed live for one participant.
No speech text, participant names or PCM were persisted. Transcript accuracy was
not scored against ground truth. Concurrent live speakers, sustained capture,
permission revocation and native callback queue bounds remain unvalidated.
Production dispatch remains unchanged; this proof uses the opt-in bot composition.

Offline: 14 native composition/runtime tests passed, including overlapping tracks
with identical display names, missing names, transcript schema conformance, STT
failure reporting, and exactly-once capture stop/finalization. The existing channel
pipeline suite and capture conversion suite passed. Optional fixture-based real
STT replay was skipped because its fixture environment was not supplied; the live
meeting probe above supplied the actual STT evidence.

SDK identity is carried in `speaker_key` (`sdk-<userId>:<turn>`). Named SDK
segments omit optional `source`; no `glow-bound` claim is emitted for native
metadata, and no new enum value is forced into the sealed transcript.v1 contract.
