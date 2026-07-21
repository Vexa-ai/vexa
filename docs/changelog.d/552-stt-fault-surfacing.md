- **A meeting with no transcript now says why.** When the transcription backend refuses — an
  exhausted token, a rejected key, an unreachable service — the bot counts the failures and
  reports them once on its terminal `lifecycle.v1` event, and meeting-api persists them to
  `meeting.data.stt_fault` and onto the `meeting.status_change` webhook. Previously those faults
  were fully typed and attributed inside the bot and then died in a `console.error`, so a meeting
  whose STT was dead completed indistinguishable from a silent room — the empty-transcript reports
  that could never be diagnosed after the fact. The report carries the backend's own words
  (`payment_required`, HTTP 402, "Insufficient balance…") and one count per kind, so a storm of
  per-chunk failures becomes one honest summary rather than a flood.
