# meeting-api tests: fixtures

Data files the meeting-api tests read.

- `outbound-url-vectors.json` — the table both outbound URL guards are held to: the Python `ssrf.py`
  (here, `src/meeting_api/webhooks/ssrf.py`, and its vendored copies) and the TypeScript `url-guard.ts`
  in `@vexa/transcribe-whisper`. A byte-identical copy sits beside that package's test
  (`core/meetings/modules/whisper/src/outbound-url-vectors.json`); `scripts/parity.json` (fact
  `outbound-url-vectors`) fails the build when the two differ. Read by `tests/test_outbound_url_vectors.py`.
