# transcribe: deferred transcription from the recording (#525)

Serves the sealed `POST /meetings/{meeting_id}/transcribe`: a completed meeting with a master
recording gains transcript rows on demand. Composes the recordings seam (finalize-on-read →
master bytes) and the collector's durable-write seam (`upsert_segments`); the STT hop speaks the
OpenAI-compatible `/v1/audio/transcriptions` contract on the deferred tier (503 + Retry-After
looped, bounded). Language is normalized to ISO-639-1 at storage (#355 defect 3); every refusal
is a typed `TranscribeFault` the router maps to an HTTP status: never a silent `[]`.

## Front door
- `build_router(store, stt, resolve_master)`: the mountable route (the unified app mounts it).
- `transcribe_meeting(...)`: the flow core (callable directly in tests): owner-only,
  terminal-status gate, Q2 conflict on a second run, per-process in-flight guard.
- `TranscribeFault`: the typed refusal (`kind` + optional provider status/code).
- `normalize_language`: provider language value → ISO-639-1 code (or None).
- `adapters.HttpSttTranscriber`: the STT client (`from_env()`: `TRANSCRIPTION_SERVICE_URL` /
  `TRANSCRIPTION_SERVICE_TOKEN` / #522's `TRANSCRIPTION_MODEL`, default `whisper-1`).
- `adapters.master_audio_resolver(repo, storage)`: recordings seam → `MasterResolver` port.
- `fakes.FakeSttTranscriber`: offline driver (app factory / conformance).

## Tests
`tests/test_transcribe_deferred.py`: the #355 regression floor (D-A2 fixtures verbatim) plus
the ruled edges. Red-first: the file predates the module and is its contract.
