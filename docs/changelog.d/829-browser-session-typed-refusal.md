### Fixed

- **Requesting a `browser_session` bot answers a typed 422 instead of a 500 that poisons the
  retry.** api.v1 seals more platforms than the meeting-bot invocation contract carries; with a
  `meeting_url` attached, such a request wrote its meeting row and then died inside schema
  validation — a 500 plus an orphaned active row that made the user's retry 409. The refusal now
  happens before any write, names the supported platforms, and points at the tracked
  restoration ([#816](https://github.com/Vexa-ai/vexa/issues/816)).
- **The join layer refuses an unknown platform instead of silently running the Google Meet
  flow.** The dispatch's fallback branch WAS the Google Meet branch, so an unrecognized platform
  drove Meet selectors against an arbitrary URL and failed minutes later with misattributed
  selector errors. ([#816](https://github.com/Vexa-ai/vexa/issues/816))
