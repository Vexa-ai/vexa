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
