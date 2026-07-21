- **Transcribe a recording after the fact (#525).** `POST /meetings/{meeting_id}/transcribe` is
  served again: a completed, recorded meeting gains a transcript on demand, on the STT service's
  capacity-reserved deferred tier, with typed refusals (409 on a second run, provider errors
  surfaced with the provider's own code and message, never a silent empty transcript) and the
  detected language stored as ISO-639-1. The route was sealed in api.v1 but served by nothing on
  0.12 deployments; the conformance gate now holds it to the contract. See
  [Meetings API](/api/meetings#transcribe-a-recording).
