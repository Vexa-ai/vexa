# meeting-api tests: fixtures

Data files the meeting-api tests read.

- `outbound-url-vectors.json` — the table both outbound URL guards are held to: the Python `ssrf.py`
  (here, `src/meeting_api/webhooks/ssrf.py`, one of its vendored copies) and the TypeScript
  `url-guard.ts` in `@vexa/transcribe-whisper`. A byte-identical copy of the golden of
  `deploy/contracts/outbound-url.v1`, where the rule and its canonical `ssrf.py` live; another sits
  beside the whisper package's test. `scripts/parity.json` (fact `outbound-url-vectors`) fails the
  build when a copy differs. Read by `tests/test_outbound_url_vectors.py`.
