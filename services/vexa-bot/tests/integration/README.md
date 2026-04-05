# Integration Tests — vexa-bot

## What this tests
- Audio capture -> transcription-service POST: audio sent, transcription received
- Segment publishing to Redis: transcribed segments written to transcription_segments stream
- Speaker identification: correct speaker labels attached to segments
- Meeting platform connection: bot joins and captures audio (platform-specific)
- Graceful shutdown: bot leaves meeting, flushes pending audio, publishes final segments
- Error recovery: reconnects on transient transcription-service failures

## Dependencies
- vexa-bot running (or testable in isolation)
- transcription-service reachable
- Redis running

## How to invoke
Start a testing agent in this directory. It reads this README and the parent service README to understand what to verify.
